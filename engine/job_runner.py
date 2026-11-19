# -*- coding: utf-8 -*-
"""dyer 抖音采集引擎入口。

本项目把 https://github.com/NanmiCoder/MediaCrawler 裁剪为「抖音专用引擎」，
放在 ``engine/`` 目录下，并由 Web 服务以「一个任务一个子进程」的方式调用：

    python engine/job_runner.py --job /path/to/job.json

任务 JSON 由 Web 服务生成，字段见 ``docs/engine-integration.md``。
之所以用子进程而不是线程，是因为 MediaCrawler 的配置是模块级全局变量，
子进程可以天然隔离每个任务的配置、浏览器上下文和事件循环。

注意：本文件为 dyer 新增，不属于上游 MediaCrawler，许可见 engine/LICENSE
（NON-COMMERCIAL LEARNING LICENSE 1.1，仅限学习研究使用）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict

ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))

# 引擎内部有若干相对路径（libs/stealth.min.js、browser_data 登录态目录），
# 因此统一把工作目录切到 engine/，保证以任意路径启动都能正常工作。
if os.path.abspath(os.getcwd()) != ENGINE_DIR:
    os.chdir(ENGINE_DIR)
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)

if sys.stdout and hasattr(sys.stdout, "buffer") and (sys.stdout.encoding or "").lower() != "utf-8":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")


def _as_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    # 列表里的每一项也可能带逗号/中文逗号，统一按分隔符拆开
    result = []
    for item in value:
        for part in str(item).replace("，", ",").split(","):
            part = part.strip()
            if part:
                result.append(part)
    return result


def apply_job_config(job: Dict[str, Any]) -> Dict[str, Any]:
    """把任务参数写入引擎的模块级配置，返回实际生效的配置快照。"""
    import config

    crawler_type = job.get("crawler_type", "search")
    if crawler_type not in ("search", "detail", "creator", "network"):
        raise ValueError(f"不支持的采集类型: {crawler_type}")

    config.PLATFORM = "dy"
    config.CRAWLER_TYPE = crawler_type
    config.LOGIN_TYPE = job.get("login_type", "qrcode")
    if config.LOGIN_TYPE not in ("qrcode", "cookie", "phone"):
        raise ValueError(f"不支持的登录方式: {config.LOGIN_TYPE}")
    config.COOKIES = job.get("cookie", "") or ""
    config.KEYWORDS = ",".join(_as_list(job.get("keywords")))
    config.DY_SPECIFIED_ID_LIST = _as_list(job.get("aweme_ids"))
    config.DY_CREATOR_ID_LIST = _as_list(job.get("creator_ids"))

    config.START_PAGE = max(1, int(job.get("start_page", 1) or 1))
    config.CRAWLER_MAX_NOTES_COUNT = max(1, int(job.get("max_notes", 20) or 20))
    config.CRAWLER_MAX_SLEEP_SEC = max(0, float(job.get("sleep_sec", 2) or 0))
    config.MAX_CONCURRENCY_NUM = max(1, int(job.get("concurrency", 1) or 1))

    config.ENABLE_GET_COMMENTS = bool(job.get("enable_comments", True))
    config.ENABLE_GET_SUB_COMMENTS = bool(job.get("enable_sub_comments", False))
    config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES = max(
        0, int(job.get("max_comments", 20) or 0)
    )
    config.ENABLE_GET_MEDIA = bool(job.get("enable_media", False))

    config.HEADLESS = bool(job.get("headless", True))
    # auto | chrome | chromium；chrome 需要系统安装 Google Chrome，失败会自动回退
    config.BROWSER_CHANNEL = str(job.get("browser_channel", "auto") or "auto")
    config.ENABLE_CDP_MODE = bool(job.get("enable_cdp", False))
    config.CDP_HEADLESS = config.HEADLESS
    config.SAVE_LOGIN_STATE = bool(job.get("save_login_state", True))

    config.SAVE_DATA_OPTION = job.get("save_option", "jsonl")
    config.SAVE_DATA_PATH = job["data_dir"]
    config.QRCODE_SAVE_PATH = job.get("qrcode_path", "") or ""

    # 引擎级开关：代理池需要额外账号体系，dyer 默认关闭。
    config.ENABLE_IP_PROXY = bool(job.get("enable_ip_proxy", False))
    config.DISABLE_SSL_VERIFY = bool(job.get("disable_ssl_verify", False))

    return {
        "platform": config.PLATFORM,
        "crawler_type": config.CRAWLER_TYPE,
        "login_type": config.LOGIN_TYPE,
        "keywords": config.KEYWORDS,
        "aweme_ids": config.DY_SPECIFIED_ID_LIST,
        "creator_ids": config.DY_CREATOR_ID_LIST,
        "start_page": config.START_PAGE,
        "max_notes": config.CRAWLER_MAX_NOTES_COUNT,
        "enable_comments": config.ENABLE_GET_COMMENTS,
        "enable_sub_comments": config.ENABLE_GET_SUB_COMMENTS,
        "max_comments": config.CRAWLER_MAX_COMMENTS_COUNT_SINGLENOTES,
        "expand_depth": int(job.get("expand_depth", 2) or 0),
        "max_users": int(job.get("max_users", 50) or 50),
        "max_awemes_per_user": int(job.get("max_awemes_per_user", 10) or 10),
        "include_following": bool(job.get("include_following", False)),
        "enable_media": config.ENABLE_GET_MEDIA,
        "headless": config.HEADLESS,
        "browser_channel": getattr(config, "BROWSER_CHANNEL", "auto"),
        "enable_cdp": config.ENABLE_CDP_MODE,
        "save_option": config.SAVE_DATA_OPTION,
        "data_dir": config.SAVE_DATA_PATH,
        "qrcode_path": config.QRCODE_SAVE_PATH,
    }


async def run_crawler(job: Dict[str, Any]) -> None:
    snapshot = apply_job_config(job)
    print(f"[dyer] 任务配置: {json.dumps(snapshot, ensure_ascii=False)}", flush=True)

    if job.get("crawler_type") == "network":
        # 社交网络扩散采集：以用户为中心，输出 network/raw.jsonl
        from playwright.async_api import async_playwright

        from dyer_network import run_network

        async with async_playwright() as playwright:
            summary = await run_network(job, playwright)
        print(f"[dyer] 扩散采集统计: {json.dumps(summary, ensure_ascii=False)}", flush=True)
        return

    from media_platform.douyin import DouYinCrawler

    crawler = DouYinCrawler()
    try:
        await crawler.start()
    finally:
        browser_context = getattr(crawler, "browser_context", None)
        cdp_manager = getattr(crawler, "cdp_manager", None)
        try:
            if cdp_manager:
                await cdp_manager.cleanup(force=True)
            elif browser_context:
                await browser_context.close()
        except Exception as exc:  # 关闭浏览器失败不应影响任务结果
            print(f"[dyer] 关闭浏览器失败: {exc}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="dyer 抖音采集引擎")
    parser.add_argument("--job", required=True, help="任务 JSON 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只打印解析后的配置，不启动浏览器")
    args = parser.parse_args()

    with open(args.job, "r", encoding="utf-8") as f:
        job = json.load(f)
    job.setdefault("data_dir", os.path.join(os.path.dirname(os.path.abspath(args.job)), "data"))

    if args.dry_run:
        snapshot = apply_job_config(job)
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return 0

    os.makedirs(job["data_dir"], exist_ok=True)
    print(f"[dyer] 开始采集，任务数据目录: {job['data_dir']}", flush=True)
    asyncio.run(run_crawler(job))
    print("[dyer] 采集流程结束", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
