# -*- coding: utf-8 -*-
"""接口入参 / 出参模型。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

CrawlerType = Literal["search", "detail", "creator", "network"]


class JobCreate(BaseModel):
    crawler_type: CrawlerType = "search"
    keywords: list[str] = Field(default_factory=list, description="搜索关键词列表")
    aweme_ids: list[str] = Field(default_factory=list, description="作品 ID 或作品链接")
    creator_ids: list[str] = Field(default_factory=list, description="创作者主页链接或 sec_user_id")
    max_notes: int = Field(default=20, ge=10, le=1000, description="单个关键词最多采集的作品数")
    max_comments: int = Field(default=20, ge=0, le=5000, description="单个作品最多采集的一级评论数")
    start_page: int = Field(default=1, ge=1, description="搜索起始页")
    enable_comments: bool = True
    enable_sub_comments: bool = False
    enable_media: bool = False
    login_type: Literal["qrcode", "cookie"] = "qrcode"
    cookie: str = ""
    headless: bool = False
    sleep_sec: float = Field(default=2.0, ge=0, le=60)

    # ---- 社交网络扩散采集（crawler_type=network）----
    expand_depth: int = Field(default=2, ge=0, le=3, description="扩散层级，0 表示只采种子用户")
    max_users: int = Field(default=50, ge=2, le=500, description="本次最多发现的用户数")
    max_awemes_per_user: int = Field(default=10, ge=0, le=50, description="每个用户最多采集的作品数")
    include_following: bool = Field(
        default=False, description="实验性：尝试采集关注列表（接口受限时自动跳过）"
    )

    @model_validator(mode="after")
    def _validate(self) -> "JobCreate":
        if self.crawler_type == "search" and not self.keywords:
            raise ValueError("关键词搜索至少需要一个关键词")
        if self.crawler_type == "network" and not (self.keywords or self.aweme_ids or self.creator_ids):
            raise ValueError("扩散采集至少需要一个种子：关键词、作品或创作者")
        if self.crawler_type == "detail" and not self.aweme_ids:
            raise ValueError("作品采集至少需要一个作品链接或 ID")
        if self.crawler_type == "creator" and not self.creator_ids:
            raise ValueError("创作者采集至少需要一个创作者链接")
        if self.login_type == "qrcode":
            # 二维码登录必须看到浏览器窗口才能扫码
            self.headless = False
        if self.crawler_type != "search":
            self.keywords = self.keywords or []
        return self


class SettingsUpdate(BaseModel):
    cookie: str = ""


class JobOut(BaseModel):
    id: str
    crawler_type: str
    keywords: str
    params: dict
    status: str
    created_at: int
    started_at: int | None = None
    finished_at: int | None = None
    exit_code: int | None = None
    error: str | None = None
    video_count: int = 0
    comment_count: int = 0
    has_qrcode: bool = False
