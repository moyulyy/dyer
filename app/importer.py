# -*- coding: utf-8 -*-
"""把引擎写出的 JSONL 采集结果导入 dyer 的 SQLite。

引擎（engine/job_runner.py）以 SAVE_DATA_OPTION=jsonl 运行，数据落在
``<job_dir>/data/dy/jsonl/{crawler_type}_{item_type}_{date}.jsonl``。
这里按 aweme_id / comment_id 做幂等 upsert，重复导入不会产生重复数据。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable

from sqlalchemy import select

from app.db import Comment, SessionLocal, Video

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
)

INT_FIELDS = {
    "create_time",
    "liked_count",
    "comment_count",
    "share_count",
    "collected_count",
    "like_count",
    "sub_comment_count",
}


def _to_int(value: Any) -> int:
    try:
        if value is None or value == "":
            return 0
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def _read_jsonl(paths: Iterable[Path]) -> tuple[Dict[str, Dict], Dict[str, Dict]]:
    """返回 (videos, comments)，按主键去重，后出现的覆盖先出现的。"""
    videos: Dict[str, Dict] = {}
    comments: Dict[str, Dict] = {}
    for path in paths:
        name = path.stem
        if "contents" in name:
            bucket = videos
            key = "aweme_id"
        elif "comments" in name:
            bucket = comments
            key = "comment_id"
        else:
            continue
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                item_id = item.get(key)
                if item_id:
                    bucket[str(item_id)] = item
    return videos, comments


def _normalize(item: Dict, fields: Iterable[str]) -> Dict:
    out = {}
    for field in fields:
        value = item.get(field)
        if field in INT_FIELDS:
            out[field] = _to_int(value)
        elif value is None:
            out[field] = ""
        else:
            out[field] = str(value)
    return out


async def import_job_data(job_id: str, data_dir: str | Path) -> Dict[str, int]:
    """导入一个任务目录下的全部 JSONL，返回 {videos, comments} 数量。"""
    data_path = Path(data_dir)
    if not data_path.exists():
        return {"videos": 0, "comments": 0}

    raw_videos, raw_comments = _read_jsonl(sorted(data_path.rglob("*.jsonl")))
    if not raw_videos and not raw_comments:
        return {"videos": 0, "comments": 0}

    async with SessionLocal() as session:
        if raw_videos:
            existing_result = await session.execute(
                select(Video).where(Video.aweme_id.in_(list(raw_videos.keys())))
            )
            existing = {video.aweme_id: video for video in existing_result.scalars().all()}
            for aweme_id, item in raw_videos.items():
                values = _normalize(item, VIDEO_FIELDS)
                video = existing.get(aweme_id)
                if video is None:
                    video = Video(aweme_id=aweme_id)
                    session.add(video)
                for field, value in values.items():
                    setattr(video, field, value)
                if not video.add_ts:
                    video.add_ts = _to_int(item.get("add_ts"))
                video.last_modify_ts = _to_int(item.get("last_modify_ts"))
                video.job_id = job_id

        if raw_comments:
            existing_result = await session.execute(
                select(Comment).where(Comment.comment_id.in_(list(raw_comments.keys())))
            )
            existing_c = {
                comment.comment_id: comment for comment in existing_result.scalars().all()
            }
            for comment_id, item in raw_comments.items():
                values = _normalize(item, COMMENT_FIELDS)
                comment = existing_c.get(comment_id)
                if comment is None:
                    comment = Comment(comment_id=comment_id)
                    session.add(comment)
                for field, value in values.items():
                    setattr(comment, field, value)
                if not comment.add_ts:
                    comment.add_ts = _to_int(item.get("last_modify_ts"))
                comment.job_id = job_id
                comment.source_keyword = str(item.get("source_keyword") or "")

        await session.commit()

    return {"videos": len(raw_videos), "comments": len(raw_comments)}
