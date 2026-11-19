# -*- coding: utf-8 -*-
"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import config
from app.db import init_db, mark_stale_jobs_failed
from app.jobs import job_manager
from app.routes import router
from app.social.routes import router as social_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
logger = logging.getLogger("dyer")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    await init_db()
    stale = await mark_stale_jobs_failed()
    if stale:
        logger.info("已将 %s 个残留任务标记为中断", stale)
    await job_manager.start()
    logger.info("dyer 已启动，数据目录: %s", config.DATA_DIR)
    try:
        yield
    finally:
        await job_manager.shutdown()
        logger.info("dyer 已停止")


app = FastAPI(
    title="dyer · 抖音数据采集",
    description="基于 MediaCrawler 抖音引擎的数据采集 Web 服务，仅供学习研究使用。",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(router)
app.include_router(social_router)
app.mount("/static", StaticFiles(directory=str(config.WWW_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(config.WWW_DIR / "index.html")


@app.get("/favicon.svg", include_in_schema=False)
async def favicon() -> FileResponse:
    return FileResponse(config.WWW_DIR / "favicon.svg", media_type="image/svg+xml")
