# -*- coding: utf-8 -*-
"""dyer 运行时配置：路径、端口、默认参数。"""

from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENGINE_DIR = BASE_DIR / "engine"
WWW_DIR = Path(__file__).resolve().parent / "www"


def _load_dotenv() -> None:
    """加载项目根目录 .env（不引入额外依赖，解析 KEY=VALUE 即可）。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()

DATA_DIR = Path(os.environ.get("DYER_DATA_DIR", BASE_DIR / "data")).expanduser().resolve()
JOBS_DIR = DATA_DIR / "jobs"
DB_PATH = DATA_DIR / "dyer.db"
SETTINGS_PATH = DATA_DIR / "settings.json"

HOST = os.environ.get("DYER_HOST", "127.0.0.1")
PORT = int(os.environ.get("DYER_PORT", "8000"))

# 同一时刻允许运行的采集任务数。抖音登录态与浏览器上下文都较重，默认 1。
MAX_CONCURRENT_JOBS = int(os.environ.get("DYER_MAX_CONCURRENT_JOBS", "1"))
# 单个任务日志在内存中保留的最大行数
LOG_BUFFER_SIZE = int(os.environ.get("DYER_LOG_BUFFER_SIZE", "2000"))

DEFAULT_JOB_OPTIONS = {
    "max_notes": 20,
    "max_comments": 20,
    "start_page": 1,
    "enable_comments": True,
    "enable_sub_comments": False,
    "enable_media": False,
    "login_type": "qrcode",
    "headless": True,
    "sleep_sec": 2.0,
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
