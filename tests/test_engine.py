# -*- coding: utf-8 -*-
"""引擎侧测试：裁剪后的引擎必须能正常导入，任务参数映射正确。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"


def test_engine_imports_without_optional_heavy_deps():
    """只安装 requirements.txt 的依赖时，抖音引擎必须可以导入。"""
    code = (
        "import media_platform.douyin, store.douyin, media_downloader, database.db_session;"
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ENGINE),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_job_runner_dry_run_maps_config(tmp_path: Path):
    job = {
        "crawler_type": "search",
        "keywords": ["露营, 户外"],
        "max_notes": 30,
        "max_comments": 50,
        "enable_comments": True,
        "login_type": "cookie",
        "cookie": "a=b",
        "headless": True,
        "data_dir": str(tmp_path / "data"),
    }
    job_file = tmp_path / "job.json"
    job_file.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ENGINE / "job_runner.py"), "--job", str(job_file), "--dry-run"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    snapshot = json.loads(result.stdout)
    assert snapshot["crawler_type"] == "search"
    assert snapshot["keywords"] == "露营,户外"
    assert snapshot["max_notes"] == 30
    assert snapshot["max_comments"] == 50
    assert snapshot["login_type"] == "cookie"
    assert snapshot["headless"] is True
    assert snapshot["save_option"] == "jsonl"
    assert snapshot["data_dir"] == str(tmp_path / "data")
