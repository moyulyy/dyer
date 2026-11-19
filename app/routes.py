# -*- coding: utf-8 -*-
"""HTTP / WebSocket 路由。"""

from __future__ import annotations

import csv
import io
import json
import sys
from pathlib import Path
from typing import Annotated, Any, Dict, Literal

from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import func, or_, select

from app import config
from app.db import Comment, Job, SessionLocal, Video
from app.jobs import job_manager, job_to_dict
from app.schemas import JobCreate, SettingsUpdate

router = APIRouter()


# --------------------------------------------------------------------- 基础信息
@router.get("/api/health")
async def health() -> Dict[str, Any]:
    return {"status": "ok", "time": __import__("time").time()}


@router.get("/api/meta")
async def meta() -> Dict[str, Any]:
    engine_entry = config.ENGINE_DIR / "job_runner.py"
    playwright_installed = False
    browsers_installed = False
    try:
        import playwright  # noqa: F401

        playwright_installed = True
        browsers_installed = (Path.home() / ".cache" / "ms-playwright").exists()
    except ImportError:
        pass
    settings = job_manager.load_settings()
    return {
        "engine": {
            "path": str(config.ENGINE_DIR),
            "ready": engine_entry.exists(),
            "playwright": playwright_installed,
            "browsers": browsers_installed,
            "python": sys.version.split()[0],
        },
        "data_dir": str(config.DATA_DIR),
        "defaults": config.DEFAULT_JOB_OPTIONS,
        "has_cookie": bool(settings.get("cookie")),
        "max_concurrent_jobs": config.MAX_CONCURRENT_JOBS,
    }


# --------------------------------------------------------------------- 设置
@router.get("/api/settings")
async def get_settings() -> Dict[str, Any]:
    settings = job_manager.load_settings()
    return {"has_cookie": bool(settings.get("cookie")), "cookie_masked": _mask(settings.get("cookie", ""))}


@router.put("/api/settings")
async def update_settings(payload: SettingsUpdate) -> Dict[str, Any]:
    saved = job_manager.save_settings({"cookie": payload.cookie.strip()})
    return {"has_cookie": bool(saved.get("cookie")), "cookie_masked": _mask(saved.get("cookie", ""))}


def _mask(cookie: str) -> str:
    if not cookie:
        return ""
    if len(cookie) <= 24:
        return cookie[:4] + "***"
    return f"{cookie[:12]}...{cookie[-8:]}"


# --------------------------------------------------------------------- 任务
@router.post("/api/jobs")
async def create_job(payload: JobCreate) -> Dict[str, Any]:
    if payload.login_type == "cookie" and not payload.cookie:
        cookie = job_manager.load_settings().get("cookie", "")
        if not cookie:
            raise HTTPException(status_code=400, detail="请先在设置里保存 Cookie，或直接填写 Cookie")
        payload.cookie = cookie
    return await job_manager.create_job(payload)


@router.get("/api/jobs")
async def list_jobs(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> Dict[str, Any]:
    async with SessionLocal() as session:
        result = await session.execute(select(Job).order_by(Job.created_at.desc()).limit(limit))
        jobs = result.scalars().all()
    return {"items": [job_to_dict(job) for job in jobs]}


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> Dict[str, Any]:
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        return job_to_dict(job)


@router.post("/api/jobs/{job_id}/stop")
async def stop_job(job_id: str) -> Dict[str, Any]:
    stopped = await job_manager.stop_job(job_id)
    if not stopped:
        raise HTTPException(status_code=400, detail="任务不在运行中")
    return {"ok": True}


@router.post("/api/jobs/{job_id}/retry")
async def retry_job(job_id: str) -> Dict[str, Any]:
    job = await job_manager.retry_job(job_id)
    if not job:
        raise HTTPException(status_code=400, detail="该任务无法重试")
    return job


@router.get("/api/jobs/{job_id}/logs")
async def job_logs(job_id: str, after: Annotated[int, Query(ge=0)] = 0) -> Dict[str, Any]:
    logs = job_manager.get_logs(job_id, after)
    if not logs:
        logs = await job_manager.load_logs_from_file(job_id)
        logs = [entry for entry in logs if entry["seq"] > after]
    return {"items": logs}


@router.get("/api/jobs/{job_id}/qrcode")
async def job_qrcode(job_id: str) -> Response:
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    qr_path = Path(job.qrcode_path)
    if not qr_path.exists():
        raise HTTPException(status_code=404, detail="暂未生成二维码")
    return FileResponse(qr_path, media_type="image/png", headers={"Cache-Control": "no-store"})


@router.delete("/api/jobs/{job_id}")
async def delete_job(job_id: str) -> Dict[str, Any]:
    async with SessionLocal() as session:
        job = await session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail="任务不存在")
        if job.status == "running":
            raise HTTPException(status_code=400, detail="请先停止任务")
        data_dir = Path(job.data_dir)
        await session.delete(job)
        await session.commit()
    job_manager.delete_job_files(data_dir)
    return {"ok": True}


@router.websocket("/ws/jobs/{job_id}")
async def job_ws(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    queue = job_manager.subscribe(job_id)
    try:
        for entry in await job_manager.load_logs_from_file(job_id):
            await websocket.send_json({"type": "log", **entry})
        await websocket.send_json({"type": "ready"})
        while True:
            payload = await queue.get()
            await websocket.send_json(payload)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        job_manager.unsubscribe(job_id, queue)


# --------------------------------------------------------------------- 数据
SORT_FIELDS = {
    "create_time": Video.create_time,
    "liked_count": Video.liked_count,
    "comment_count": Video.comment_count,
    "share_count": Video.share_count,
    "collected_count": Video.collected_count,
    "add_ts": Video.add_ts,
}


@router.get("/api/videos")
async def list_videos(
    q: str = "",
    keyword: str = "",
    job_id: str = "",
    sort: str = "create_time",
    order: Literal["asc", "desc"] = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> Dict[str, Any]:
    stmt = select(Video)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(Video.title.like(like), Video.desc.like(like), Video.nickname.like(like),
                Video.aweme_id.like(like))
        )
    if keyword:
        stmt = stmt.where(Video.source_keyword.like(f"%{keyword}%"))
    if job_id:
        stmt = stmt.where(Video.job_id == job_id)

    total = await _count(Video, stmt)
    column = SORT_FIELDS.get(sort, Video.create_time)
    stmt = stmt.order_by(column.asc() if order == "asc" else column.desc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)

    async with SessionLocal() as session:
        result = await session.execute(stmt)
        rows = result.scalars().all()
    return {
        "items": [_video_dict(row) for row in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/api/videos/{aweme_id}")
async def video_detail(aweme_id: str) -> Dict[str, Any]:
    async with SessionLocal() as session:
        result = await session.execute(select(Video).where(Video.aweme_id == aweme_id))
        video = result.scalar_one_or_none()
        if not video:
            raise HTTPException(status_code=404, detail="作品不存在")
        comment_total = await session.scalar(
            select(func.count()).select_from(Comment).where(Comment.aweme_id == aweme_id)
        )
        data = _video_dict(video)
        data["comment_total"] = int(comment_total or 0)
        return data


@router.get("/api/videos/{aweme_id}/comments")
async def video_comments(
    aweme_id: str,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    order: Literal["asc", "desc"] = "desc",
) -> Dict[str, Any]:
    stmt = select(Comment).where(Comment.aweme_id == aweme_id)
    total = await _count(Comment, stmt)
    stmt = stmt.order_by(
        Comment.like_count.desc() if order == "desc" else Comment.create_time.asc()
    ).offset((page - 1) * page_size).limit(page_size)
    async with SessionLocal() as session:
        result = await session.execute(stmt)
        rows = result.scalars().all()
    return {
        "items": [
            {
                "comment_id": row.comment_id,
                "content": row.content,
                "nickname": row.nickname,
                "like_count": row.like_count,
                "sub_comment_count": row.sub_comment_count,
                "create_time": row.create_time,
                "parent_comment_id": row.parent_comment_id,
            }
            for row in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.delete("/api/videos/{aweme_id}")
async def delete_video(aweme_id: str) -> Dict[str, Any]:
    async with SessionLocal() as session:
        result = await session.execute(select(Video).where(Video.aweme_id == aweme_id))
        video = result.scalar_one_or_none()
        if not video:
            raise HTTPException(status_code=404, detail="作品不存在")
        comments = await session.execute(select(Comment).where(Comment.aweme_id == aweme_id))
        for comment in comments.scalars().all():
            await session.delete(comment)
        await session.delete(video)
        await session.commit()
    return {"ok": True}


@router.get("/api/stats")
async def stats() -> Dict[str, Any]:
    async with SessionLocal() as session:
        video_total = await session.scalar(select(func.count()).select_from(Video)) or 0
        comment_total = await session.scalar(select(func.count()).select_from(Comment)) or 0
        total_liked = await session.scalar(select(func.sum(Video.liked_count))) or 0
        job_total = await session.scalar(select(func.count()).select_from(Job)) or 0

        top_result = await session.execute(
            select(Video.source_keyword, func.count(Video.id).label("cnt"))
            .where(Video.source_keyword != "")
            .group_by(Video.source_keyword)
            .order_by(func.count(Video.id).desc())
            .limit(10)
        )
        top_keywords = [
            {"keyword": keyword, "count": count} for keyword, count in top_result.all()
        ]

        hot_result = await session.execute(
            select(Video).order_by(Video.liked_count.desc()).limit(8)
        )
        hot_videos = [_video_dict(video) for video in hot_result.scalars().all()]

        day_result = await session.execute(
            select(
                func.strftime("%Y-%m-%d", Video.create_time, "unixepoch", "localtime").label("day"),
                func.count(Video.id),
            )
            .where(Video.create_time > 0)
            .group_by("day")
            .order_by("day")
            .limit(14)
        )
        daily = [{"day": day, "count": count} for day, count in day_result.all()]

    return {
        "videos": int(video_total),
        "comments": int(comment_total),
        "liked": int(total_liked),
        "jobs": int(job_total),
        "top_keywords": top_keywords,
        "hot_videos": hot_videos,
        "daily": daily,
    }


@router.get("/api/export")
async def export_data(
    kind: Literal["videos", "comments"] = "videos",
    format: Literal["csv", "json"] = "csv",
    keyword: str = "",
    job_id: str = "",
) -> StreamingResponse:
    if kind == "videos":
        stmt = select(Video).order_by(Video.create_time.desc())
        if keyword:
            stmt = stmt.where(Video.source_keyword.like(f"%{keyword}%"))
        if job_id:
            stmt = stmt.where(Video.job_id == job_id)
        fields = [
            "aweme_id", "title", "desc", "nickname", "create_time", "liked_count",
            "comment_count", "share_count", "collected_count", "aweme_url", "cover_url",
            "video_download_url", "source_keyword", "add_ts",
        ]
    else:
        stmt = select(Comment).order_by(Comment.create_time.desc())
        if keyword:
            stmt = stmt.where(Comment.source_keyword.like(f"%{keyword}%"))
        if job_id:
            stmt = stmt.where(Comment.job_id == job_id)
        fields = [
            "comment_id", "aweme_id", "content", "nickname", "create_time",
            "like_count", "sub_comment_count", "parent_comment_id",
        ]

    async with SessionLocal() as session:
        result = await session.execute(stmt)
        rows = result.scalars().all()
        records = [{field: getattr(row, field, "") for field in fields} for row in rows]

    filename = f"dyer_{kind}_{__import__('time').strftime('%Y%m%d_%H%M%S')}"
    if format == "json":
        body = json.dumps(records, ensure_ascii=False, indent=2).encode("utf-8")
        return StreamingResponse(
            io.BytesIO(body),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}.json"'},
        )

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(records)
    body = ("\ufeff" + buffer.getvalue()).encode("utf-8")
    return StreamingResponse(
        io.BytesIO(body),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}.csv"'},
    )


# --------------------------------------------------------------------- 工具
async def _count(model, stmt) -> int:
    subquery = stmt.order_by(None).subquery()
    async with SessionLocal() as session:
        value = await session.scalar(select(func.count()).select_from(subquery))
    return int(value or 0)


def _video_dict(video: Video) -> Dict[str, Any]:
    return {
        "aweme_id": video.aweme_id,
        "aweme_type": video.aweme_type,
        "title": video.title,
        "desc": video.desc,
        "nickname": video.nickname,
        "creator_hash": video.creator_hash,
        "create_time": video.create_time,
        "liked_count": video.liked_count,
        "comment_count": video.comment_count,
        "share_count": video.share_count,
        "collected_count": video.collected_count,
        "aweme_url": video.aweme_url,
        "cover_url": video.cover_url,
        "video_download_url": video.video_download_url,
        "source_keyword": video.source_keyword,
        "job_id": video.job_id,
        "add_ts": video.add_ts,
    }
