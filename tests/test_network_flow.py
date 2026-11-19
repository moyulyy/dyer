# -*- coding: utf-8 -*-
"""network 任务端到端：假引擎产出原始响应 → 入库 → 自动网络分析 → 接口可查。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config as app_config
from app.main import app

FAKE_NETWORK_ENGINE = '''# -*- coding: utf-8 -*-
import json
import os
import sys

job_path = sys.argv[sys.argv.index("--job") + 1]
job = json.load(open(job_path, encoding="utf-8"))
target = os.path.join(job["data_dir"], "network")
os.makedirs(target, exist_ok=True)


def user(sec, nick, followers=0):
    return {"sec_uid": sec, "uid": sec[-4:], "nickname": nick, "follower_count": followers}


entries = [
    {
        "kind": "search_list",
        "external_id": "扩散测试",
        "depth": 0,
        "source": "keyword:扩散测试",
        "fetched_at": 1,
        "payload": {"data": [{"aweme_info": {
            "aweme_id": "net-aw-1",
            "desc": "扩散采样的作品",
            "create_time": 1700000000,
            "author": user("SEC_NET_AUTHOR", "网络作者", 800),
            "statistics": {"digg_count": 12, "comment_count": 1},
        }}]},
    },
    {
        "kind": "comment_list",
        "external_id": "net-aw-1",
        "depth": 1,
        "source": "aweme:net-aw-1",
        "fetched_at": 2,
        "meta": {"author_sec_uid": "SEC_NET_AUTHOR"},
        "payload": {"comments": [{
            "cid": "net-c1",
            "aweme_id": "net-aw-1",
            "text": "扩散评论",
            "create_time": 1700000100,
            "digg_count": 2,
            "reply_id": "0",
            "user": user("SEC_NET_COMMENTER", "评论者网络"),
        }]},
    },
    {
        "kind": "user_profile",
        "external_id": "SEC_NET_AUTHOR",
        "depth": 0,
        "source": "profile",
        "fetched_at": 3,
        "payload": {"user": {**user("SEC_NET_AUTHOR", "网络作者", 800), "signature": "扩散采集"}},
    },
]
with open(os.path.join(target, "raw.jsonl"), "w", encoding="utf-8") as f:
    for entry in entries:
        f.write(json.dumps(entry, ensure_ascii=False) + "\\n")
print("[fake] network engine done", flush=True)
'''


@pytest.fixture
def network_client(tmp_path: Path, monkeypatch):
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    (engine_dir / "job_runner.py").write_text(FAKE_NETWORK_ENGINE, encoding="utf-8")
    monkeypatch.setattr(app_config, "ENGINE_DIR", engine_dir)
    with TestClient(app) as client:
        yield client


def _wait_finished(client: TestClient, job_id: str, timeout: float = 25.0) -> dict:
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("success", "failed", "stopped"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"任务未在超时时间内结束: {job}")


def test_network_job_end_to_end(network_client: TestClient):
    response = network_client.post(
        "/api/jobs",
        json={
            "crawler_type": "network",
            "keywords": ["扩散测试"],
            "expand_depth": 2,
            "max_users": 10,
            "max_awemes_per_user": 2,
            "login_type": "qrcode",
        },
    )
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]

    job = _wait_finished(network_client, job_id)
    assert job["status"] == "success", job
    assert job["user_count"] == 2
    assert job["relation_count"] == 1

    users = network_client.get("/api/users").json()
    assert users["total"] == 2
    user = next(item for item in users["items"] if item["sec_uid"] == "SEC_NET_AUTHOR")
    assert user["nickname"] == "网络作者"
    assert user["follower_count"] == 800
    assert user["pagerank"] > 0
    assert user["degree_in"] == 1

    detail = network_client.get("/api/users/SEC_NET_COMMENTER").json()
    assert detail["relations"]["out"][0]["sec_uid"] == "SEC_NET_AUTHOR"
    assert detail["relations"]["out"][0]["rel_type"] == "commented_on"

    overview = network_client.get("/api/network/overview").json()
    assert overview["counts"]["users"] == 2
    assert overview["counts"]["raw_items"] == 3
    assert overview["analysis"] is not None
    assert overview["analysis"]["node_count"] == 2

    logs = network_client.get(f"/api/jobs/{job_id}/logs").json()["items"]
    assert any("已入库 2 个用户" in entry["text"] for entry in logs)
    assert any("社交网络分析完成" in entry["text"] for entry in logs)


def test_network_job_requires_seed(network_client: TestClient):
    response = network_client.post(
        "/api/jobs", json={"crawler_type": "network", "keywords": []}
    )
    assert response.status_code == 422
