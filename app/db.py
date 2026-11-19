# -*- coding: utf-8 -*-
"""dyer 数据层：任务 / 作品 / 评论 三张表，SQLite + async SQLAlchemy。"""

from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy import BigInteger, Index, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.config import DATA_DIR, DB_PATH


class Base(DeclarativeBase):
    pass


class Job(Base):
    """一次采集任务。"""

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    crawler_type: Mapped[str] = mapped_column(String(16), default="search")
    keywords: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    finished_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_path: Mapped[str] = mapped_column(Text, default="")
    data_dir: Mapped[str] = mapped_column(Text, default="")
    qrcode_path: Mapped[str] = mapped_column(Text, default="")
    video_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Video(Base):
    """抖音作品（字段对齐引擎 store.douyin 的输出）。"""

    __tablename__ = "videos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    aweme_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    aweme_type: Mapped[str] = mapped_column(String(32), default="")
    title: Mapped[str] = mapped_column(Text, default="")
    desc: Mapped[str] = mapped_column(Text, default="")
    nickname: Mapped[str] = mapped_column(Text, default="")
    creator_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    create_time: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    liked_count: Mapped[int] = mapped_column(Integer, default=0)
    comment_count: Mapped[int] = mapped_column(Integer, default=0)
    share_count: Mapped[int] = mapped_column(Integer, default=0)
    collected_count: Mapped[int] = mapped_column(Integer, default=0)
    aweme_url: Mapped[str] = mapped_column(Text, default="")
    cover_url: Mapped[str] = mapped_column(Text, default="")
    video_download_url: Mapped[str] = mapped_column(Text, default="")
    music_download_url: Mapped[str] = mapped_column(Text, default="")
    note_download_url: Mapped[str] = mapped_column(Text, default="")
    source_keyword: Mapped[str] = mapped_column(Text, default="", index=True)
    job_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    add_ts: Mapped[int] = mapped_column(BigInteger, default=0)
    last_modify_ts: Mapped[int] = mapped_column(BigInteger, default=0)


class Comment(Base):
    """抖音作品的一级/二级评论。"""

    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comment_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    aweme_id: Mapped[str] = mapped_column(String(64), index=True)
    content: Mapped[str] = mapped_column(Text, default="")
    nickname: Mapped[str] = mapped_column(Text, default="")
    creator_hash: Mapped[str] = mapped_column(String(64), default="", index=True)
    create_time: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    sub_comment_count: Mapped[int] = mapped_column(Integer, default=0)
    parent_comment_id: Mapped[str] = mapped_column(String(64), default="")
    pictures: Mapped[str] = mapped_column(Text, default="")
    job_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    source_keyword: Mapped[str] = mapped_column(Text, default="")
    add_ts: Mapped[int] = mapped_column(BigInteger, default=0)


Index("ix_videos_job_aweme", Video.job_id, Video.aweme_id)
Index("ix_comments_aweme_time", Comment.aweme_id, Comment.create_time)

DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def mark_stale_jobs_failed() -> int:
    """服务重启后，之前处于运行/排队中的任务已经不存在了，统一标记为失败。"""
    import time

    async with SessionLocal() as session:
        result = await session.execute(
            select(Job).where(Job.status.in_(("running", "queued")))
        )
        jobs = result.scalars().all()
        for job in jobs:
            job.status = "failed"
            job.error = "服务重启，任务已中断"
            job.finished_at = int(time.time())
            job.pid = None
        await session.commit()
        return len(jobs)
