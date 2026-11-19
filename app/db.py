# -*- coding: utf-8 -*-
"""dyer 数据层：任务 / 作品 / 评论 三张表，SQLite + async SQLAlchemy。"""

from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
    text,
)
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
    user_count: Mapped[int] = mapped_column(Integer, default=0)
    relation_count: Mapped[int] = mapped_column(Integer, default=0)
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
    user_sec_uid: Mapped[str] = mapped_column(String(128), default="", index=True)
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
    user_sec_uid: Mapped[str] = mapped_column(String(128), default="", index=True)
    job_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    source_keyword: Mapped[str] = mapped_column(Text, default="")
    add_ts: Mapped[int] = mapped_column(BigInteger, default=0)


class DyUser(Base):
    """抖音用户（账号）——以 sec_uid 为唯一身份，字段来自公开主页。"""

    __tablename__ = "dy_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sec_uid: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    uid: Mapped[str] = mapped_column(String(64), default="", index=True)
    nickname: Mapped[str] = mapped_column(Text, default="")
    unique_id: Mapped[str] = mapped_column(String(128), default="", comment="抖音号")
    signature: Mapped[str] = mapped_column(Text, default="")
    avatar_url: Mapped[str] = mapped_column(Text, default="")
    gender: Mapped[int] = mapped_column(Integer, default=0)
    ip_location: Mapped[str] = mapped_column(Text, default="")
    follower_count: Mapped[int] = mapped_column(Integer, default=0)
    following_count: Mapped[int] = mapped_column(Integer, default=0)
    aweme_count: Mapped[int] = mapped_column(Integer, default=0)
    total_favorited: Mapped[int] = mapped_column(Integer, default=0)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    home_url: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), default="comment", comment="发现来源")
    depth: Mapped[int] = mapped_column(Integer, default=0, comment="扩散层级，0 为种子")
    profile_complete: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已抓取完整主页")
    first_seen_ts: Mapped[int] = mapped_column(BigInteger, default=0)
    last_seen_ts: Mapped[int] = mapped_column(BigInteger, default=0)
    last_job_id: Mapped[str] = mapped_column(String(32), default="", index=True)


class UserRelation(Base):
    """用户之间的关系边（有向、带权重、可追溯证据）。"""

    __tablename__ = "user_relations"
    __table_args__ = (
        UniqueConstraint("src_sec_uid", "dst_sec_uid", "rel_type", name="uq_relation_edge"),
        Index("ix_relation_src", "src_sec_uid"),
        Index("ix_relation_dst", "dst_sec_uid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    src_sec_uid: Mapped[str] = mapped_column(String(128))
    dst_sec_uid: Mapped[str] = mapped_column(String(128))
    rel_type: Mapped[str] = mapped_column(
        String(32), default="commented_on", comment="commented_on/replied_to/following/co_author"
    )
    weight: Mapped[int] = mapped_column(Integer, default=1, comment="互动次数")
    evidence: Mapped[str] = mapped_column(Text, default="", comment="最近一次证据（作品/评论 ID）")
    first_seen_ts: Mapped[int] = mapped_column(BigInteger, default=0)
    last_seen_ts: Mapped[int] = mapped_column(BigInteger, default=0)
    last_job_id: Mapped[str] = mapped_column(String(32), default="")


class RawItem(Base):
    """采集到的原始响应（去重后逐条保存，可回溯）。"""

    __tablename__ = "raw_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    kind: Mapped[str] = mapped_column(String(32), default="", index=True)
    external_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[str] = mapped_column(Text, default="")
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    fetched_at: Mapped[int] = mapped_column(BigInteger, default=0, index=True)


class UserMetric(Base):
    """一次网络分析中单个用户的图指标。"""

    __tablename__ = "user_metrics"

    sec_uid: Mapped[str] = mapped_column(String(128), primary_key=True)
    run_id: Mapped[int] = mapped_column(Integer, default=0, index=True)
    pagerank: Mapped[float] = mapped_column(Float, default=0.0)
    degree_in: Mapped[int] = mapped_column(Integer, default=0)
    degree_out: Mapped[int] = mapped_column(Integer, default=0)
    degree_total: Mapped[int] = mapped_column(Integer, default=0)
    betweenness: Mapped[float] = mapped_column(Float, default=0.0)
    community: Mapped[int] = mapped_column(Integer, default=-1, index=True)
    component: Mapped[int] = mapped_column(Integer, default=-1)
    # 用户特征画像（由 app/social/graph.py 的规则算法给出）
    role: Mapped[str] = mapped_column(String(32), default="peripheral", index=True)
    influence_score: Mapped[float] = mapped_column(Float, default=0.0)
    cross_community_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    computed_at: Mapped[int] = mapped_column(BigInteger, default=0)


class NetworkRun(Base):
    """一次社交网络分析的汇总结果。"""

    __tablename__ = "network_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[int] = mapped_column(BigInteger, default=0, index=True)
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    edge_count: Mapped[int] = mapped_column(Integer, default=0)
    density: Mapped[float] = mapped_column(Float, default=0.0)
    avg_degree: Mapped[float] = mapped_column(Float, default=0.0)
    component_count: Mapped[int] = mapped_column(Integer, default=0)
    community_count: Mapped[int] = mapped_column(Integer, default=0)
    modularity: Mapped[float] = mapped_column(Float, default=0.0)
    params: Mapped[str] = mapped_column(Text, default="{}")
    summary: Mapped[str] = mapped_column(Text, default="{}")


Index("ix_videos_job_aweme", Video.job_id, Video.aweme_id)
Index("ix_comments_aweme_time", Comment.aweme_id, Comment.create_time)

DATA_DIR.mkdir(parents=True, exist_ok=True)
engine = create_async_engine(f"sqlite+aiosqlite:///{DB_PATH}", echo=False, future=True)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_apply_sqlite_migrations)


# 旧库升级：create_all 不会给已存在的表补列，这里做最小化迁移。
_MIGRATIONS = {
    "videos": {"user_sec_uid": "VARCHAR(128) DEFAULT ''"},
    "comments": {"user_sec_uid": "VARCHAR(128) DEFAULT ''"},
    "jobs": {
        "user_count": "INTEGER DEFAULT 0",
        "relation_count": "INTEGER DEFAULT 0",
    },
    "user_metrics": {
        "role": "VARCHAR(32) DEFAULT 'peripheral'",
        "influence_score": "FLOAT DEFAULT 0",
        "cross_community_ratio": "FLOAT DEFAULT 0",
    },
}


def _apply_sqlite_migrations(conn) -> None:
    for table, columns in _MIGRATIONS.items():
        existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
        for name, ddl in columns.items():
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


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
