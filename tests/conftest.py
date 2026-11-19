# -*- coding: utf-8 -*-
"""测试环境：独立的临时数据目录，避免污染项目 data/。"""

import asyncio
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TEST_DATA_DIR = pathlib.Path(tempfile.mkdtemp(prefix="dyer-test-"))
os.environ["DYER_DATA_DIR"] = str(TEST_DATA_DIR)
os.environ["DYER_MAX_CONCURRENT_JOBS"] = "2"

import pytest  # noqa: E402

from app.db import Base, SessionLocal, engine  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    async def _reset():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_reset())
    yield


@pytest.fixture
def session_factory():
    return SessionLocal
