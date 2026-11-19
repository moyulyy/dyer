# -*- coding: utf-8 -*-
"""把引擎的 `network/raw.jsonl` 清洗后写入用户维度数据库。

设计要点：
1. 先解析全部原始行，在内存里按主键去重（用户 sec_uid、作品 aweme_id、
   评论 comment_id、关系 src+dst+type），再统一入库，避免逐条查库。
2. 原始响应本身也全部入库（raw_items，sha256 去重），以满足"保留所有原始数据"。
3. 导入是幂等的：重复导入同一批数据不会产生重复用户/关系，权重也不会翻倍。
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import select

from app.db import (
    Comment,
    DyUser,
    RawItem,
    SessionLocal,
    UserRelation,
    Video,
)
from app.social.cleaning import (
    merge_relations,
    merge_user,
    normalize_aweme,
    normalize_comment,
    normalize_sec_uid,
    normalize_user,
    to_int,
)

USER_FIELDS = (
    "sec_uid",
    "uid",
    "nickname",
    "unique_id",
    "signature",
    "avatar_url",
    "gender",
    "ip_location",
    "follower_count",
    "following_count",
    "aweme_count",
    "total_favorited",
    "is_verified",
    "home_url",
    "source",
    "depth",
    "profile_complete",
    "first_seen_ts",
    "last_seen_ts",
    "last_job_id",
)

VIDEO_FIELDS = (
    "aweme_id",
    "aweme_type",
    "title",
    "desc",
    "nickname",
    "creator_hash",
    "create_time",
    "liked_count",
    "comment_count",
    "share_count",
    "collected_count",
    "aweme_url",
    "cover_url",
    "video_download_url",
    "music_download_url",
    "note_download_url",
    "source_keyword",
    "user_sec_uid",
    "add_ts",
    "last_modify_ts",
)

COMMENT_FIELDS = (
    "comment_id",
    "aweme_id",
    "content",
    "nickname",
    "creator_hash",
    "create_time",
    "like_count",
    "sub_comment_count",
    "parent_comment_id",
    "pictures",
    "user_sec_uid",
    "add_ts",
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_raw_entries(path: Path) -> List[Dict[str, Any]]:
    """读取 raw.jsonl；坏行直接跳过（引擎中途被杀时最后一行可能不完整）。"""
    entries: List[Dict[str, Any]] = []
    if not path.exists():
        return entries
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict) and entry.get("kind"):
                entries.append(entry)
    return entries


def _as_dict(payload: Any) -> Dict[str, Any]:
    return payload if isinstance(payload, dict) else {}


def _list_from(payload: Any, *keys: str) -> List[Any]:
    if isinstance(payload, list):
        return payload
    data = _as_dict(payload)
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


class _Aggregator:
    """内存聚合器：去重 + 字段合并，最后一次性写库。"""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        self.users: Dict[str, Dict[str, Any]] = {}
        self.videos: Dict[str, Dict[str, Any]] = {}
        self.comments: Dict[str, Dict[str, Any]] = {}
        self.relations: List[Dict[str, Any]] = []
        self.raw_rows: List[Dict[str, Any]] = []
        self._comment_context: List[Dict[str, Any]] = []

    # ---------------------------------------------------------------- 用户
    def add_user(self, user: Optional[Dict[str, Any]]) -> None:
        if not user:
            return
        sec_uid = user["sec_uid"]
        if sec_uid in self.users:
            self.users[sec_uid] = merge_user(self.users[sec_uid], user)
        else:
            self.users[sec_uid] = user

    # ---------------------------------------------------------------- 原始数据
    def add_raw(self, entry: Dict[str, Any], ts: int) -> None:
        payload = entry.get("payload")
        try:
            text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return
        self.raw_rows.append(
            {
                "job_id": self.job_id,
                "kind": str(entry.get("kind"))[:32],
                "external_id": str(entry.get("external_id") or "")[:128],
                "depth": to_int(entry.get("depth")),
                "source": str(entry.get("source") or "")[:500],
                "payload": text,
                "sha256": _sha256(text),
                "fetched_at": to_int(entry.get("fetched_at")) or ts,
            }
        )

    # ---------------------------------------------------------------- 解析
    def consume(self, entry: Dict[str, Any], ts: int, keyword: str) -> None:
        kind = str(entry.get("kind") or "")
        payload = entry.get("payload")
        meta = _as_dict(entry.get("meta"))
        depth = to_int(entry.get("depth"))
        source = str(entry.get("source") or kind)[:32]
        self.add_raw(entry, ts)

        if kind == "user_profile":
            user_raw = _as_dict(payload).get("user") or payload
            self.add_user(
                normalize_user(
                    user_raw,
                    source="profile",
                    depth=depth,
                    ts=ts,
                    job_id=self.job_id,
                    complete=True,
                )
            )
            return

        if kind in ("aweme_list", "search_list", "detail_list"):
            for item in _list_from(payload, "aweme_list", "data", "items"):
                self._consume_aweme(item, ts, keyword, depth, source)
            return

        if kind == "comment_list":
            aweme_id = str(entry.get("external_id") or "")
            author_sec_uid = normalize_sec_uid(meta.get("author_sec_uid"))
            self._consume_comments(payload, aweme_id, author_sec_uid, ts, depth, keyword)
            return

        if kind == "following_list":
            src_sec_uid = normalize_sec_uid(entry.get("external_id"))
            for item in _list_from(payload, "followings", "data"):
                user = normalize_user(
                    item, source="following", depth=depth, ts=ts, job_id=self.job_id
                )
                if not user:
                    continue
                self.add_user(user)
                if src_sec_uid:
                    self.relations.append(
                        {
                            "src_sec_uid": src_sec_uid,
                            "dst_sec_uid": user["sec_uid"],
                            "rel_type": "following",
                            "evidence": "",
                            "ts": ts,
                            "job_id": self.job_id,
                        }
                    )

    def _consume_aweme(
        self, item: Any, ts: int, keyword: str, depth: int, source: str
    ) -> None:
        # 搜索结果是 [{"aweme_info": {...}}] 结构
        if isinstance(item, dict) and "aweme_info" in item:
            item = item.get("aweme_info")
        if isinstance(item, dict) and item.get("aweme_mix_info"):
            mix_items = item["aweme_mix_info"].get("mix_items") or []
            if mix_items:
                item = mix_items[0]
        aweme = normalize_aweme(item, ts=ts, keyword=keyword)
        if not aweme:
            return
        author = aweme.pop("_author", None)
        aweme_id = aweme["aweme_id"]
        current = self.videos.get(aweme_id)
        self.videos[aweme_id] = {**current, **aweme} if current else aweme
        if author:
            user = normalize_user(
                author, source="author", depth=depth, ts=ts, job_id=self.job_id
            )
            self.add_user(user)

    def _consume_comments(
        self,
        payload: Any,
        aweme_id: str,
        author_sec_uid: str,
        ts: int,
        depth: int,
        keyword: str,
    ) -> None:
        for raw in _list_from(payload, "comments", "data"):
            comment = normalize_comment(raw, aweme_id=aweme_id, ts=ts)
            if not comment:
                continue
            user_raw = comment.pop("_user", None)
            reply_to_uid = comment.pop("_reply_to_uid", "")
            self.comments[comment["comment_id"]] = comment
            if user_raw:
                self.add_user(
                    normalize_user(
                        user_raw,
                        source="comment",
                        depth=depth,
                        ts=ts,
                        job_id=self.job_id,
                    )
                )
            if user_raw and author_sec_uid:
                self._comment_context.append(
                    {
                        "commenter": comment["user_sec_uid"],
                        "author": author_sec_uid,
                        "comment_id": comment["comment_id"],
                        "parent": comment["parent_comment_id"],
                        "reply_to_uid": reply_to_uid,
                        "ts": ts,
                    }
                )

    # ---------------------------------------------------------------- 关系
    def build_relations(self) -> None:
        """评论互动关系：评论者 -> 作品作者；回复关系：回复者 -> 被回复评论作者。"""
        uid_map = {u["uid"]: u["sec_uid"] for u in self.users.values() if u.get("uid")}
        comment_authors = {
            c["comment_id"]: c["user_sec_uid"] for c in self.comments.values() if c["user_sec_uid"]
        }
        for ctx in self._comment_context:
            commenter = ctx["commenter"]
            if not commenter:
                continue
            if ctx["author"] and ctx["author"] != commenter:
                self.relations.append(
                    {
                        "src_sec_uid": commenter,
                        "dst_sec_uid": ctx["author"],
                        "rel_type": "commented_on",
                        "evidence": ctx["comment_id"],
                        "ts": ctx["ts"],
                        "job_id": self.job_id,
                    }
                )
            parent = ctx["parent"]
            target = ""
            if parent and parent != "0":
                target = comment_authors.get(parent, "")
            if not target and ctx["reply_to_uid"]:
                target = uid_map.get(ctx["reply_to_uid"], "")
            if target and target != commenter:
                self.relations.append(
                    {
                        "src_sec_uid": commenter,
                        "dst_sec_uid": target,
                        "rel_type": "replied_to",
                        "evidence": ctx["comment_id"],
                        "ts": ctx["ts"],
                        "job_id": self.job_id,
                    }
                )


async def import_network_entries(
    job_id: str, entries: Iterable[Dict[str, Any]], *, keyword: str = ""
) -> Dict[str, int]:
    """把一批原始响应导入数据库，返回本次涉及的各类记录数。"""
    ts_now = int(time.time())
    agg = _Aggregator(job_id)
    for entry in entries:
        agg.consume(entry, ts_now, keyword)
    agg.build_relations()

    relations = merge_relations(agg.relations)

    async with SessionLocal() as session:
        await _save_raw(session, agg.raw_rows)
        await _save_users(session, agg.users)
        await _save_videos(session, agg.videos)
        await _save_comments(session, agg.comments)
        await _save_relations(session, relations)
        await session.commit()

    return {
        "users": len(agg.users),
        "videos": len(agg.videos),
        "comments": len(agg.comments),
        "relations": len(relations),
        "raw": len(agg.raw_rows),
    }


async def import_network_data(job_id: str, data_dir: str | Path, keyword: str = "") -> Dict[str, int]:
    """从任务目录读取 network/raw.jsonl 并导入。"""
    path = Path(data_dir) / "network" / "raw.jsonl"
    entries = read_raw_entries(path)
    if not entries:
        return {"users": 0, "videos": 0, "comments": 0, "relations": 0, "raw": 0}
    return await import_network_entries(job_id, entries, keyword=keyword)


# ------------------------------------------------------------------ 入库细节
async def _save_raw(session, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        return
    # 先按 sha256 去重（批内 + 库内）
    unique: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        unique.setdefault(row["sha256"], row)
    existing: set[str] = set()
    digests = list(unique.keys())
    for i in range(0, len(digests), 500):
        chunk = digests[i : i + 500]
        result = await session.execute(select(RawItem.sha256).where(RawItem.sha256.in_(chunk)))
        existing.update(result.scalars().all())
    for digest, row in unique.items():
        if digest in existing:
            continue
        session.add(RawItem(**row))


async def _save_users(session, users: Dict[str, Dict[str, Any]]) -> None:
    if not users:
        return
    result = await session.execute(
        select(DyUser).where(DyUser.sec_uid.in_(list(users.keys())))
    )
    existing = {user.sec_uid: user for user in result.scalars().all()}
    now = int(time.time())
    for sec_uid, values in users.items():
        user = existing.get(sec_uid)
        if user is None:
            payload = {k: values.get(k, "") for k in USER_FIELDS}
            user = DyUser(**payload)
            if not user.first_seen_ts:
                user.first_seen_ts = now
            user.last_seen_ts = user.last_seen_ts or now
            session.add(user)
            continue
        current = {field: getattr(user, field) for field in USER_FIELDS}
        merged = merge_user(current, values)
        for field in USER_FIELDS:
            setattr(user, field, merged.get(field, current.get(field)))


async def _save_videos(session, videos: Dict[str, Dict[str, Any]]) -> None:
    if not videos:
        return
    result = await session.execute(
        select(Video).where(Video.aweme_id.in_(list(videos.keys())))
    )
    existing = {video.aweme_id: video for video in result.scalars().all()}
    for aweme_id, values in videos.items():
        video = existing.get(aweme_id)
        if video is None:
            video = Video(aweme_id=aweme_id)
            session.add(video)
        for field in VIDEO_FIELDS:
            if field in values and values[field] not in ("", None):
                setattr(video, field, values[field])


async def _save_comments(session, comments: Dict[str, Dict[str, Any]]) -> None:
    if not comments:
        return
    result = await session.execute(
        select(Comment).where(Comment.comment_id.in_(list(comments.keys())))
    )
    existing = {comment.comment_id: comment for comment in result.scalars().all()}
    for comment_id, values in comments.items():
        comment = existing.get(comment_id)
        if comment is None:
            comment = Comment(comment_id=comment_id)
            session.add(comment)
        for field in COMMENT_FIELDS:
            if field in values and values[field] not in ("", None):
                setattr(comment, field, values[field])


async def _save_relations(session, relations: Dict[tuple, Dict[str, Any]]) -> None:
    if not relations:
        return
    src_list = {key[0] for key in relations}
    existing: Dict[tuple, UserRelation] = {}
    result = await session.execute(
        select(UserRelation).where(UserRelation.src_sec_uid.in_(list(src_list)))
    )
    for relation in result.scalars().all():
        existing[(relation.src_sec_uid, relation.dst_sec_uid, relation.rel_type)] = relation

    for key, values in relations.items():
        relation = existing.get(key)
        if relation is None:
            session.add(UserRelation(**values))
            continue
        # 幂等：同一批数据重复导入不应让权重翻倍，取较大值
        relation.weight = max(relation.weight, values["weight"])
        relation.evidence = values.get("evidence") or relation.evidence
        relation.last_seen_ts = max(relation.last_seen_ts, values.get("last_seen_ts", 0))
        relation.last_job_id = values.get("last_job_id") or relation.last_job_id
