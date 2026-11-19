# -*- coding: utf-8 -*-
"""dyer 社交网络扩散采集器。

与上游 MediaCrawler 的三种采集模式不同，这个采集器以**用户（账号）**为中心：

1. 种子：关键词搜索的作品作者、指定作品作者、指定创作者；
2. 对每个用户抓取公开主页、作品、作品评论，把评论者作为下一层节点；
3. 按 BFS 逐层扩散，受 `max_users` / `expand_depth` / 请求预算约束；
4. 每个响应都原样写入 `network/raw.jsonl`，由 Web 服务清洗入库。

"关注列表"在抖音 Web 端属于受限接口，这里作为**实验性**能力：
调用失败只记日志并跳过，不影响主流程。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, Optional, Set, Tuple

import config
from media_platform.douyin import DouYinCrawler
from media_platform.douyin.client import DouYinClient
from media_platform.douyin.field import PublishTimeType
from media_platform.douyin.login import DouYinLogin
from tools import utils
from var import request_keyword_var


def log(message: str) -> None:
    """同时写日志与 stdout，父进程据此实时展示并入库。"""
    print(f"[network] {message}", flush=True)
    utils.logger.info(f"[dyer.network] {message}")


# --------------------------------------------------------------------------- 调度
class ExplorationPlanner:
    """BFS 调度器：负责用户去重、层级限制与总量预算（纯逻辑，可单测）。"""

    def __init__(self, max_users: int = 50, max_depth: int = 2) -> None:
        self.max_users = max(1, int(max_users))
        self.max_depth = max(0, int(max_depth))
        self._queue: Deque[Tuple[str, int, str]] = deque()
        self._seen: Set[str] = set()
        self.expanded = 0
        self.skipped = 0

    @property
    def queued(self) -> int:
        return len(self._queue)

    @property
    def discovered(self) -> int:
        return len(self._seen)

    def _enqueue(self, sec_uid: str, depth: int, source: str) -> bool:
        sec_uid = (sec_uid or "").strip()
        if not sec_uid or depth > self.max_depth:
            self.skipped += 1
            return False
        if sec_uid in self._seen:
            return False
        if len(self._seen) >= self.max_users:
            self.skipped += 1
            return False
        self._seen.add(sec_uid)
        self._queue.append((sec_uid, depth, source))
        return True

    def add_seed(self, sec_uid: str, source: str = "seed") -> bool:
        return self._enqueue(sec_uid, 0, source)

    def add_candidate(self, sec_uid: str, depth: int, source: str) -> bool:
        return self._enqueue(sec_uid, depth, source)

    def has_next(self) -> bool:
        return bool(self._queue)

    def pop(self) -> Optional[Tuple[str, int, str]]:
        if not self._queue:
            return None
        self.expanded += 1
        return self._queue.popleft()


# --------------------------------------------------------------------------- 落盘
class RawSink:
    """把每一次原始响应按行写入 raw.jsonl（父进程可增量读取）。"""

    def __init__(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self._file = open(path, "a", encoding="utf-8")
        self.count = 0

    def write(
        self,
        kind: str,
        external_id: str,
        payload: Any,
        depth: int,
        source: str,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        record = {
            "kind": kind,
            "external_id": external_id,
            "payload": payload,
            "depth": depth,
            "source": source,
            "meta": meta or {},
            "fetched_at": int(time.time()),
        }
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()
        self.count += 1

    def close(self) -> None:
        try:
            self._file.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- 采集
class NetworkExplorer:
    """在已登录的抖音客户端上执行 BFS 扩散采集。"""

    def __init__(
        self,
        client: DouYinClient,
        sink: RawSink,
        options: Dict[str, Any],
    ) -> None:
        self.client = client
        self.sink = sink
        self.keywords: List[str] = [str(k) for k in options.get("keywords") or [] if str(k).strip()]
        self.aweme_ids: List[str] = [str(a) for a in options.get("aweme_ids") or [] if str(a).strip()]
        self.creator_ids: List[str] = [str(c) for c in options.get("creator_ids") or [] if str(c).strip()]
        self.expand_depth = max(0, int(options.get("expand_depth", 2)))
        self.max_awemes_per_user = max(0, int(options.get("max_awemes_per_user", 10)))
        self.max_comments_per_aweme = max(0, int(options.get("max_comments_per_aweme", 20)))
        self.max_search_notes = max(1, int(options.get("max_search_notes", 20)))
        self.include_following = bool(options.get("include_following", False))
        self.enable_sub_comments = bool(options.get("enable_sub_comments", False))
        self.sleep_sec = max(0.0, float(options.get("sleep_sec", 2)))
        self.max_requests = max(10, int(options.get("max_requests", 500)))

        self.planner = ExplorationPlanner(
            max_users=int(options.get("max_users", 50)), max_depth=self.expand_depth
        )
        self.requests = 0
        self.level_stats: Dict[int, int] = {}

    # ---------------------------------------------------------------- 入口
    async def run(self) -> Dict[str, Any]:
        started = time.time()
        log(f"开始扩散采集：关键词={self.keywords} 作品={len(self.aweme_ids)} 创作者={len(self.creator_ids)}")
        await self._seed_keywords()
        await self._seed_awemes()
        self._seed_creators()

        while self.planner.has_next() and self.requests < self.max_requests:
            node = self.planner.pop()
            if node is None:
                break
            sec_uid, depth, source = node
            try:
                await self._explore_user(sec_uid, depth, source)
            except Exception as exc:  # 单个节点失败不中断整体扩散
                log(f"用户 {sec_uid} 采集失败，已跳过：{exc}")
            await self._sleep()

        summary = {
            "users_discovered": self.planner.discovered,
            "users_expanded": self.planner.expanded,
            "skipped": self.planner.skipped,
            "requests": self.requests,
            "raw_records": self.sink.count,
            "elapsed_sec": round(time.time() - started, 1),
            "level_stats": self.level_stats,
        }
        log(f"扩散采集结束：{json.dumps(summary, ensure_ascii=False)}")
        return summary

    # ---------------------------------------------------------------- 种子
    async def _seed_keywords(self) -> None:
        for keyword in self.keywords:
            request_keyword_var.set(keyword)
            search_id = ""
            offset = 0
            collected = 0
            while collected < self.max_search_notes and self.requests < self.max_requests:
                response = await self._call(
                    self.client.search_info_by_keyword(
                        keyword=keyword,
                        offset=offset,
                        publish_time=PublishTimeType.UNLIMITED,
                        search_id=search_id,
                    )
                )
                items = (response or {}).get("data") or []
                if not items:
                    break
                self.sink.write(
                    "search_list", keyword, response, 0, f"keyword:{keyword}", {"keyword": keyword}
                )
                search_id = (response or {}).get("extra", {}).get("logid", "") or search_id
                for item in items:
                    aweme = self._pick_aweme(item)
                    if not aweme:
                        continue
                    sec_uid = self._author_sec_uid(aweme)
                    if sec_uid:
                        self.planner.add_seed(sec_uid, f"keyword:{keyword}")
                        self._bump_level(0)
                    collected += 1
                offset += len(items)
                await self._sleep()

    async def _seed_awemes(self) -> None:
        for aweme_id in self.aweme_ids:
            try:
                detail = await self._call(self.client.get_video_by_id(aweme_id))
            except Exception as exc:
                log(f"作品 {aweme_id} 详情获取失败：{exc}")
                continue
            if not detail:
                continue
            self.sink.write("detail_list", aweme_id, {"aweme_list": [detail]}, 0, f"seed:aweme:{aweme_id}")
            sec_uid = self._author_sec_uid(detail)
            if sec_uid:
                self.planner.add_seed(sec_uid, f"seed:aweme:{aweme_id}")
                self._bump_level(0)
            await self._sleep()

    def _seed_creators(self) -> None:
        for creator in self.creator_ids:
            sec_uid = self._parse_creator(creator)
            if sec_uid:
                self.planner.add_seed(sec_uid, "seed:creator")
                self._bump_level(0)
            else:
                log(f"无法解析创作者：{creator}")

    # ---------------------------------------------------------------- 单节点
    async def _explore_user(self, sec_uid: str, depth: int, source: str) -> None:
        log(f"展开用户 {sec_uid}（层级 {depth}，来源 {source}）")
        profile = await self._call(self.client.get_user_info(sec_uid))
        if profile:
            self.sink.write("user_profile", sec_uid, profile, depth, source)

        awemes = await self._fetch_awemes(sec_uid)
        if awemes:
            self.sink.write(
                "aweme_list",
                sec_uid,
                {"aweme_list": awemes, "author_sec_uid": sec_uid},
                depth,
                source,
            )
        for aweme in awemes:
            aweme_id = str(aweme.get("aweme_id") or "")
            if not aweme_id:
                continue
            await self._fetch_comments(aweme_id, sec_uid, depth)

        if self.include_following:
            await self._fetch_followings(sec_uid, depth)

    async def _fetch_awemes(self, sec_uid: str) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        cursor = ""
        while len(result) < self.max_awemes_per_user and self.requests < self.max_requests:
            response = await self._call(self.client.get_user_aweme_posts(sec_uid, cursor))
            items = (response or {}).get("aweme_list") or []
            if not items:
                break
            result.extend(items)
            if not (response or {}).get("has_more"):
                break
            next_cursor = str((response or {}).get("max_cursor") or "")
            if not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
            await self._sleep()
        return result[: self.max_awemes_per_user] if self.max_awemes_per_user else []

    async def _fetch_comments(self, aweme_id: str, author_sec_uid: str, depth: int) -> None:
        if self.max_comments_per_aweme <= 0:
            return
        comments: List[Dict[str, Any]] = []
        cursor = 0
        while len(comments) < self.max_comments_per_aweme and self.requests < self.max_requests:
            response = await self._call(self.client.get_aweme_comments(aweme_id, cursor))
            batch = (response or {}).get("comments") or []
            if not batch:
                break
            comments.extend(batch)
            if self.enable_sub_comments:
                comments.extend(await self._fetch_sub_comments(batch, len(comments)))
            if not (response or {}).get("has_more"):
                break
            next_cursor = int((response or {}).get("cursor") or 0)
            if next_cursor == cursor:
                break
            cursor = next_cursor
            await self._sleep()

        comments = comments[: self.max_comments_per_aweme]
        if not comments:
            return
        self.sink.write(
            "comment_list",
            aweme_id,
            {"comments": comments},
            depth + 1,
            f"aweme:{aweme_id}",
            {"author_sec_uid": author_sec_uid},
        )
        for comment in comments:
            user = comment.get("user") if isinstance(comment.get("user"), dict) else {}
            commenter = str(user.get("sec_uid") or "")
            if commenter and commenter != author_sec_uid:
                if self.planner.add_candidate(commenter, depth + 1, "comment"):
                    self._bump_level(depth + 1)

    async def _fetch_sub_comments(
        self, parent_comments: Iterable[Dict[str, Any]], already: int
    ) -> List[Dict[str, Any]]:
        collected: List[Dict[str, Any]] = []
        for parent in parent_comments:
            if not parent.get("reply_comment_total"):
                continue
            if already + len(collected) >= self.max_comments_per_aweme:
                break
            cursor = 0
            while True:
                response = await self._call(
                    self.client.get_sub_comments(
                        str(parent.get("aweme_id") or ""), str(parent.get("cid") or ""), cursor
                    )
                )
                batch = (response or {}).get("comments") or []
                if not batch:
                    break
                collected.extend(batch)
                if not (response or {}).get("has_more"):
                    break
                cursor = int((response or {}).get("cursor") or 0)
                await self._sleep()
        return collected

    async def _fetch_followings(self, sec_uid: str, depth: int) -> None:
        """实验性：抖音 Web 关注列表接口受限，失败时降级跳过。"""
        try:
            response = await self._call(
                self.client.get(
                    "/aweme/v1/web/relation/followings/",
                    {"sec_user_id": sec_uid, "count": 20, "offset": 0, "source_type": 3},
                )
            )
            followings = (response or {}).get("followings") or (response or {}).get("data") or []
            if not followings:
                log(f"用户 {sec_uid} 关注列表为空或接口受限")
                return
            self.sink.write(
                "following_list", sec_uid, {"followings": followings}, depth + 1, "following"
            )
            for item in followings:
                target = str((item or {}).get("sec_uid") or "")
                if target and self.planner.add_candidate(target, depth + 1, "following"):
                    self._bump_level(depth + 1)
        except Exception as exc:
            log(f"关注列表接口不可用（已跳过）：{exc}")

    # ---------------------------------------------------------------- 工具
    async def _call(self, coro: Any) -> Any:
        self.requests += 1
        return await coro

    async def _sleep(self) -> None:
        if self.sleep_sec:
            await asyncio.sleep(self.sleep_sec)

    def _bump_level(self, depth: int) -> None:
        self.level_stats[depth] = self.level_stats.get(depth, 0) + 1

    @staticmethod
    def _pick_aweme(item: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        aweme = item.get("aweme_info")
        if isinstance(aweme, dict):
            return aweme
        if item.get("aweme_mix_info"):
            mix_items = item["aweme_mix_info"].get("mix_items") or []
            if mix_items:
                return mix_items[0]
        return item if item.get("aweme_id") else None

    @staticmethod
    def _author_sec_uid(aweme: Dict[str, Any]) -> str:
        author = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
        return str(author.get("sec_uid") or "")

    @staticmethod
    def _parse_creator(value: str) -> str:
        """支持主页链接或裸 sec_user_id。"""
        from media_platform.douyin.help import parse_creator_info_from_url

        value = (value or "").strip()
        if not value:
            return ""
        if "://" in value:
            try:
                return parse_creator_info_from_url(value).sec_user_id
            except ValueError:
                return ""
        return value


# --------------------------------------------------------------------------- 会话
async def open_douyin_session(crawler: DouYinCrawler, playwright) -> DouYinClient:
    """启动浏览器、打开抖音、必要时登录，返回可用的客户端。"""
    if config.ENABLE_CDP_MODE:
        log("扩散采集不支持 CDP 模式，已回退到标准 Playwright 模式")
        config.ENABLE_CDP_MODE = False

    browser_context = await crawler.launch_browser(
        playwright.chromium, None, None, headless=config.HEADLESS
    )
    crawler.browser_context = browser_context
    await browser_context.add_init_script(
        path=str(Path(__file__).resolve().parent / "libs" / "stealth.min.js")
    )

    page = await browser_context.new_page()
    await page.goto(crawler.index_url)
    crawler.context_page = page

    client = await crawler.create_douyin_client(None)
    if not await client.pong(browser_context):
        log(f"未检测到登录态，使用 {config.LOGIN_TYPE} 方式登录")
        login = DouYinLogin(
            login_type=config.LOGIN_TYPE,
            login_phone="",
            browser_context=browser_context,
            context_page=page,
            cookie_str=config.COOKIES,
        )
        await login.begin()
        await client.update_cookies(browser_context, urls=crawler.cookie_urls)
    return client


async def run_network(job: Dict[str, Any], playwright) -> Dict[str, Any]:
    """network 任务入口：被 job_runner 调用。"""
    data_dir = job["data_dir"]
    raw_path = os.path.join(data_dir, "network", "raw.jsonl")
    sink = RawSink(raw_path)
    crawler = DouYinCrawler()
    try:
        client = await open_douyin_session(crawler, playwright)
        explorer = NetworkExplorer(client, sink, job)
        return await explorer.run()
    finally:
        sink.close()
        try:
            if crawler.browser_context:
                await crawler.browser_context.close()
        except Exception:
            pass
