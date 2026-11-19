# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from sqlalchemy import select

from app.db import Comment, SessionLocal, Video
from app.importer import import_job_data


def _write_jsonl(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


@pytest.mark.asyncio
async def test_import_jsonl_upsert(tmp_path: Path):
    data_dir = tmp_path / "data"
    _write_jsonl(
        data_dir / "dy" / "jsonl" / "search_contents_2026-01-01.jsonl",
        [
            {
                "aweme_id": "1001",
                "aweme_type": "0",
                "title": "第一条作品",
                "desc": "第一条作品",
                "nickname": "用户A",
                "creator_hash": "hash-a",
                "create_time": 1700000000,
                "liked_count": "1234",
                "comment_count": "12",
                "share_count": "3",
                "collected_count": "45",
                "aweme_url": "https://www.douyin.com/video/1001",
                "cover_url": "https://example.com/a.jpg",
                "source_keyword": "露营",
                "last_modify_ts": 1700000100,
            },
            # 同一个作品出现两次，应被去重且以最后一条为准
            {
                "aweme_id": "1001",
                "title": "第一条作品（更新）",
                "desc": "第一条作品（更新）",
                "nickname": "用户A",
                "creator_hash": "hash-a",
                "create_time": 1700000000,
                "liked_count": "1300",
                "comment_count": "12",
                "share_count": "3",
                "collected_count": "45",
                "aweme_url": "https://www.douyin.com/video/1001",
                "source_keyword": "露营",
                "last_modify_ts": 1700000200,
            },
        ],
    )
    _write_jsonl(
        data_dir / "dy" / "jsonl" / "search_comments_2026-01-01.jsonl",
        [
            {
                "comment_id": "c-1",
                "aweme_id": "1001",
                "content": "拍得真好",
                "nickname": "路人甲",
                "creator_hash": "hash-b",
                "create_time": 1700000300,
                "like_count": 5,
                "sub_comment_count": "2",
                "parent_comment_id": "0",
                "pictures": "",
                "last_modify_ts": 1700000400,
            },
            {
                "comment_id": "c-2",
                "aweme_id": "1001",
                "content": "求装备清单",
                "nickname": "路人乙",
                "creator_hash": "hash-c",
                "create_time": 1700000500,
                "like_count": "9",
                "parent_comment_id": "0",
                "last_modify_ts": 1700000600,
            },
        ],
    )

    result = await import_job_data("job-test", data_dir)
    assert result == {"videos": 1, "comments": 2}

    async with SessionLocal() as session:
        video = (await session.execute(select(Video))).scalar_one()
        assert video.title == "第一条作品（更新）"
        assert video.liked_count == 1300
        assert video.source_keyword == "露营"
        assert video.job_id == "job-test"
        comments = (await session.execute(select(Comment))).scalars().all()
        assert {c.comment_id for c in comments} == {"c-1", "c-2"}
        assert {c.like_count for c in comments} == {5, 9}

    # 再次导入应保持幂等
    again = await import_job_data("job-test-2", data_dir)
    assert again == {"videos": 1, "comments": 2}
    async with SessionLocal() as session:
        assert len((await session.execute(select(Video))).scalars().all()) == 1
        assert len((await session.execute(select(Comment))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_import_missing_dir(tmp_path: Path):
    result = await import_job_data("job-x", tmp_path / "nope")
    assert result == {"videos": 0, "comments": 0}


@pytest.mark.asyncio
async def test_import_ignores_malformed_lines(tmp_path: Path):
    data_dir = tmp_path / "data"
    path = data_dir / "dy" / "jsonl" / "detail_contents_2026-01-01.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json}\n" + json.dumps({"aweme_id": "2001", "title": "ok"}) + "\n", encoding="utf-8")

    result = await import_job_data("job-y", data_dir)
    assert result["videos"] == 1
