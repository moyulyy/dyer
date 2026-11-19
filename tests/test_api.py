# -*- coding: utf-8 -*-
"""接口测试：使用假引擎（不启动浏览器）验证任务全链路。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config as app_config
from app.main import app

FAKE_ENGINE = '''# -*- coding: utf-8 -*-
import json
import os
import sys

job_path = sys.argv[sys.argv.index("--job") + 1]
job = json.load(open(job_path, encoding="utf-8"))
data_dir = job["data_dir"]
target = os.path.join(data_dir, "dy", "jsonl")
os.makedirs(target, exist_ok=True)
print("[fake] engine started", flush=True)
with open(os.path.join(target, "search_contents_2026-01-01.jsonl"), "w", encoding="utf-8") as f:
    f.write(json.dumps({
        "aweme_id": "9001",
        "aweme_type": "0",
        "title": "假引擎作品",
        "desc": "假引擎作品",
        "nickname": "测试用户",
        "creator_hash": "hash-test",
        "create_time": 1700000000,
        "liked_count": "88",
        "comment_count": "2",
        "share_count": "1",
        "collected_count": "3",
        "aweme_url": "https://www.douyin.com/video/9001",
        "cover_url": "",
        "source_keyword": "测试",
        "last_modify_ts": 1700000100,
    }, ensure_ascii=False) + "\\n")
with open(os.path.join(target, "search_comments_2026-01-01.jsonl"), "w", encoding="utf-8") as f:
    f.write(json.dumps({
        "comment_id": "fc-1",
        "aweme_id": "9001",
        "content": "假引擎评论",
        "nickname": "评论者",
        "creator_hash": "hash-c",
        "create_time": 1700000200,
        "like_count": 7,
        "parent_comment_id": "0",
        "last_modify_ts": 1700000300,
    }, ensure_ascii=False) + "\\n")
print("[fake] engine done", flush=True)
'''


@pytest.fixture
def fake_engine(tmp_path: Path, monkeypatch):
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    (engine_dir / "job_runner.py").write_text(FAKE_ENGINE, encoding="utf-8")
    monkeypatch.setattr(app_config, "ENGINE_DIR", engine_dir)
    return engine_dir


@pytest.fixture
def client(fake_engine):
    with TestClient(app) as test_client:
        yield test_client


def _wait_finished(client: TestClient, job_id: str, timeout: float = 20.0) -> dict:
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("success", "failed", "stopped"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"任务未在超时时间内结束: {job}")


def test_job_runs_engine_and_imports_data(client: TestClient):
    resp = client.post(
        "/api/jobs",
        json={
            "crawler_type": "search",
            "keywords": ["露营", "测试"],
            "max_notes": 10,
            "login_type": "qrcode",
        },
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["id"]

    job = _wait_finished(client, job_id)
    assert job["status"] == "success", job
    assert job["video_count"] == 1
    assert job["comment_count"] == 1

    videos = client.get("/api/videos").json()
    assert videos["total"] == 1
    item = videos["items"][0]
    assert item["aweme_id"] == "9001"
    assert item["liked_count"] == 88
    assert item["source_keyword"] == "测试"

    comments = client.get("/api/videos/9001/comments").json()
    assert comments["total"] == 1
    assert comments["items"][0]["content"] == "假引擎评论"

    logs = client.get(f"/api/jobs/{job_id}/logs").json()["items"]
    assert any("engine started" in entry["text"] for entry in logs)
    assert any("已导入 1 个作品" in entry["text"] for entry in logs)

    stats = client.get("/api/stats").json()
    assert stats["videos"] == 1
    assert stats["comments"] == 1
    assert stats["top_keywords"] == [{"keyword": "测试", "count": 1}]


def test_validation_rejects_empty_keywords(client: TestClient):
    resp = client.post("/api/jobs", json={"crawler_type": "search", "keywords": []})
    assert resp.status_code == 422


def test_cookie_login_requires_cookie(client: TestClient):
    resp = client.post(
        "/api/jobs",
        json={"crawler_type": "search", "keywords": ["x"], "login_type": "cookie"},
    )
    assert resp.status_code == 400
    assert "Cookie" in resp.json()["detail"]


def test_settings_save_and_use_cookie(client: TestClient):
    resp = client.put("/api/settings", json={"cookie": "sessionid=demo-cookie-value"})
    assert resp.status_code == 200
    assert resp.json()["has_cookie"] is True

    resp = client.post(
        "/api/jobs",
        json={"crawler_type": "search", "keywords": ["x"], "login_type": "cookie"},
    )
    assert resp.status_code == 200
    job = _wait_finished(client, resp.json()["id"])
    assert job["status"] == "success"


def test_qrcode_endpoint(client: TestClient):
    resp = client.post(
        "/api/jobs",
        json={"crawler_type": "search", "keywords": ["qr"], "login_type": "qrcode"},
    )
    job_id = resp.json()["id"]
    job = _wait_finished(client, job_id)
    assert client.get(f"/api/jobs/{job_id}/qrcode").status_code == 404

    # 引擎扫码登录时会把二维码写到任务目录，接口应能直接返回图片
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
        b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    from app import config as app_config

    qr_path = app_config.JOBS_DIR / job_id / "qrcode.png"
    qr_path.write_bytes(png)
    resp = client.get(f"/api/jobs/{job_id}/qrcode")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/png")
    assert client.get(f"/api/jobs/{job_id}").json()["has_qrcode"] is True


def test_export_csv_and_json(client: TestClient):
    resp = client.post("/api/jobs", json={"crawler_type": "search", "keywords": ["导出"]})
    _wait_finished(client, resp.json()["id"])

    csv_resp = client.get("/api/export?kind=videos&format=csv")
    assert csv_resp.status_code == 200
    assert "9001" in csv_resp.text
    assert "attachment" in csv_resp.headers["content-disposition"]

    json_resp = client.get("/api/export?kind=comments&format=json")
    assert json_resp.status_code == 200
    assert json_resp.json()[0]["comment_id"] == "fc-1"


def test_retry_and_delete_job(client: TestClient):
    resp = client.post("/api/jobs", json={"crawler_type": "search", "keywords": ["重试"]})
    job_id = resp.json()["id"]
    _wait_finished(client, job_id)

    retry = client.post(f"/api/jobs/{job_id}/retry")
    assert retry.status_code == 200
    new_id = retry.json()["id"]
    assert new_id != job_id
    _wait_finished(client, new_id)

    assert client.delete(f"/api/jobs/{new_id}").status_code == 200
    assert client.get(f"/api/jobs/{new_id}").status_code == 404


def test_websocket_streams_logs_and_status(client: TestClient):
    resp = client.post("/api/jobs", json={"crawler_type": "search", "keywords": ["ws"]})
    job_id = resp.json()["id"]

    texts, statuses = [], []
    with client.websocket_connect(f"/ws/jobs/{job_id}") as ws:
        for _ in range(500):
            message = ws.receive_json()
            if message["type"] == "log":
                texts.append(message["text"])
            elif message["type"] == "status":
                statuses.append(message["status"])
                if message["status"] in ("success", "failed", "stopped"):
                    break
            if statuses and texts and statuses[-1] == "success":
                break

    assert any("engine started" in text for text in texts)
    assert "success" in statuses
