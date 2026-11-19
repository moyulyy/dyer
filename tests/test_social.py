# -*- coding: utf-8 -*-
"""用户维度测试：清洗、幂等入库、图指标、分析落库与接口。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import DyUser, SessionLocal, UserMetric, UserRelation, init_db
from app.main import app
from app.social.cleaning import (
    clean_text,
    merge_relations,
    merge_user,
    normalize_comment,
    normalize_sec_uid,
    normalize_user,
    to_int,
)
import networkx as nx

from app.social.graph import (
    ROLE_LABELS,
    analyze_network,
    classify_roles,
    compute_metrics,
    latest_analysis,
)
from app.social.importer import import_network_data

SEC_A = "SEC_ALPHA_0001"
SEC_B = "SEC_BETA_0002"
SEC_C = "SEC_GAMMA_0003"
SEC_D = "SEC_DELTA_0004"
SEC_E = "SEC_EPSILON_0005"


# --------------------------------------------------------------------- 数据构造
def _user(sec: str, nickname: str, uid: str = "", followers: int = 0) -> dict:
    return {
        "sec_uid": sec,
        "uid": uid or sec[-4:],
        "nickname": nickname,
        "signature": "",
        "follower_count": followers,
    }


def _aweme(aweme_id: str, author: dict, desc: str = "") -> dict:
    return {
        "aweme_id": aweme_id,
        "desc": desc or f"作品 {aweme_id}",
        "create_time": 1700000000,
        "author": author,
        "statistics": {"digg_count": 10, "comment_count": 2, "share_count": 1, "collect_count": 3},
    }


def _entries() -> list[dict]:
    a = _user(SEC_A, "作者A", "1001", 900)
    b = _user(SEC_B, "作者B", "1002", 500)
    c = _user(SEC_C, "用户C", "1003", 120)
    d = _user(SEC_D, "用户D", "1004", 80)
    e = _user(SEC_E, "用户E", "1005", 30)
    comments = [
        {"cid": "c1", "aweme_id": "aw1", "text": "评论一", "create_time": 1700000100, "digg_count": 3, "reply_id": "0", "user": c},
        {"cid": "c2", "aweme_id": "aw1", "text": "评论二", "create_time": 1700000200, "digg_count": 1, "reply_id": "0", "user": d},
        {"cid": "c3", "aweme_id": "aw1", "text": "回复 C", "create_time": 1700000300, "digg_count": 0, "reply_id": "c1", "reply_to_userid": "1003", "user": e},
    ]
    return [
        {
            "kind": "search_list",
            "external_id": "露营",
            "depth": 0,
            "source": "keyword:露营",
            "fetched_at": 1700000000,
            "payload": {"data": [{"aweme_info": _aweme("aw1", a)}, {"aweme_info": _aweme("aw2", b)}]},
        },
        {
            "kind": "comment_list",
            "external_id": "aw1",
            "depth": 1,
            "source": "aweme:aw1",
            "fetched_at": 1700000400,
            "meta": {"author_sec_uid": SEC_A},
            "payload": {"comments": comments},
        },
        {
            "kind": "user_profile",
            "external_id": SEC_C,
            "depth": 1,
            "source": "profile",
            "fetched_at": 1700000500,
            "payload": {"user": {**c, "signature": "露营爱好者", "ip_location": "广东", "following_count": 33}},
        },
        {
            "kind": "following_list",
            "external_id": SEC_C,
            "depth": 2,
            "source": "following",
            "fetched_at": 1700000600,
            "payload": {"followings": [a, e]},
        },
        # 同一批评论重复出现：验证幂等
        {
            "kind": "comment_list",
            "external_id": "aw1",
            "depth": 1,
            "source": "aweme:aw1",
            "fetched_at": 1700000700,
            "meta": {"author_sec_uid": SEC_A},
            "payload": {"comments": comments[:2]},
        },
    ]


def _write_raw(tmp_path: Path, entries: list[dict]) -> Path:
    data_dir = tmp_path / "data"
    raw = data_dir / "network" / "raw.jsonl"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n",
        encoding="utf-8",
    )
    return data_dir


# --------------------------------------------------------------------- 清洗
def test_clean_text_and_numbers():
    assert clean_text("  a\x00b\n c  ") == "ab c"
    assert clean_text("x" * 10, limit=5) == "xxxx…"
    assert to_int("123") == 123
    assert to_int("1.9") == 1
    assert to_int("abc") == 0
    assert to_int(None) == 0


def test_normalize_user_requires_sec_uid():
    assert normalize_sec_uid("") == ""
    assert normalize_sec_uid("ab") == ""
    assert normalize_sec_uid(" SEC_123 ") == "SEC_123"
    assert normalize_user({"nickname": "无 sec_uid"}) is None
    user = normalize_user({"sec_uid": SEC_A, "nickname": " 作者 A ", "follower_count": "1200"}, source="comment", depth=2)
    assert user["nickname"] == "作者 A"
    assert user["follower_count"] == 1200
    assert user["home_url"].endswith(SEC_A)


def test_merge_user_prefers_complete_profile():
    base = normalize_user({"sec_uid": SEC_A, "nickname": "旧昵称", "follower_count": 100}, depth=3)
    rich = normalize_user(
        {"sec_uid": SEC_A, "nickname": "新昵称", "follower_count": 80, "signature": "简介"},
        depth=1,
        complete=True,
    )
    merged = merge_user(base, rich)
    assert merged["nickname"] == "新昵称"
    assert merged["follower_count"] == 100  # 计数取最大值，不回退
    assert merged["signature"] == "简介"
    assert merged["depth"] == 1
    assert merged["profile_complete"] is True


def test_normalize_comment_and_relation_dedup():
    comment = normalize_comment(
        {"cid": "c9", "text": "hi", "user": {"sec_uid": SEC_C, "nickname": "C"}}, aweme_id="aw1"
    )
    assert comment["comment_id"] == "c9"
    assert comment["user_sec_uid"] == SEC_C
    assert comment["_user"]["sec_uid"] == SEC_C

    merged = merge_relations(
        [
            {"src_sec_uid": SEC_C, "dst_sec_uid": SEC_A, "rel_type": "commented_on", "evidence": "c1", "ts": 1},
            {"src_sec_uid": SEC_C, "dst_sec_uid": SEC_A, "rel_type": "commented_on", "evidence": "c2", "ts": 2},
            {"src_sec_uid": SEC_C, "dst_sec_uid": SEC_C, "rel_type": "commented_on", "ts": 3},  # 自环丢弃
        ]
    )
    assert len(merged) == 1
    edge = next(iter(merged.values()))
    assert edge["weight"] == 2
    assert edge["evidence"] == "c2"


# --------------------------------------------------------------------- 入库
@pytest.mark.asyncio
async def test_import_network_is_idempotent(tmp_path: Path):
    data_dir = _write_raw(tmp_path, _entries())
    first = await import_network_data("job-1", data_dir)
    second = await import_network_data("job-1", data_dir)

    assert first == second
    assert first["users"] == 5
    assert first["videos"] == 2
    assert first["comments"] == 3
    assert first["relations"] == 6  # C/D/E 评论 A、E 回复 C、C 关注 A/E

    async with SessionLocal() as session:
        users = (await session.execute(select(DyUser))).scalars().all()
        assert len(users) == 5
        c = next(user for user in users if user.sec_uid == SEC_C)
        assert c.follower_count == 120
        assert c.signature == "露营爱好者"
        assert c.profile_complete is True
        assert c.depth == 1

        relations = (await session.execute(select(UserRelation))).scalars().all()
        by_key = {(rel.src_sec_uid, rel.dst_sec_uid, rel.rel_type): rel for rel in relations}
        assert by_key[(SEC_C, SEC_A, "commented_on")].weight == 2  # 重复导入不累加
        assert by_key[(SEC_C, SEC_A, "following")].weight == 1
        assert by_key[(SEC_E, SEC_C, "replied_to")].weight == 1


# --------------------------------------------------------------------- 图指标
def test_compute_metrics_on_star_graph():
    users = [
        {"sec_uid": sec, "nickname": nick, "follower_count": 0, "depth": 0, "profile_complete": True}
        for sec, nick in [("SEC_A", "中心"), ("SEC_B", "B"), ("SEC_C", "C"), ("SEC_D", "D")]
    ]
    edges = [
        {"src_sec_uid": src, "dst_sec_uid": "SEC_A", "rel_type": "commented_on", "weight": 1}
        for src in ("SEC_B", "SEC_C", "SEC_D")
    ]
    result = compute_metrics(users, edges)
    assert result["node_count"] == 4
    assert result["edge_count"] == 3
    assert result["component_count"] == 1
    assert result["community_count"] == 1
    assert result["metrics"]["SEC_A"]["degree_in"] == 3
    assert result["metrics"]["SEC_A"]["pagerank"] == max(
        m["pagerank"] for m in result["metrics"].values()
    )
    assert result["summary"]["top_pagerank"][0]["sec_uid"] == "SEC_A"
    assert result["summary"]["relation_types"] == {"commented_on": 3}


def test_compute_metrics_empty():
    result = compute_metrics([], [])
    assert result["node_count"] == 0
    assert result["metrics"] == {}
    assert result["summary"]["top_pagerank"] == []


@pytest.mark.asyncio
async def test_analyze_network_persists_metrics(tmp_path: Path):
    data_dir = _write_raw(tmp_path, _entries())
    await import_network_data("job-1", data_dir)
    run_id = await analyze_network(job_id="job-1")

    async with SessionLocal() as session:
        count = await session.scalar(select(func.count()).select_from(UserMetric))
    assert count == 5

    analysis = await latest_analysis()
    assert analysis is not None
    assert analysis["run_id"] == run_id
    assert analysis["node_count"] == 5
    # 图里有向边按「节点对」合并（C→A 同时有评论与关注），因此是 5 条
    assert analysis["edge_count"] == 5
    assert analysis["summary"]["top_pagerank"]

    second = await analyze_network(job_id="job-1")
    assert second > run_id
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(UserMetric)) == 5


# --------------------------------------------------------------------- 接口
@pytest.fixture
def seeded(tmp_path: Path):
    data_dir = _write_raw(tmp_path, _entries())

    async def _seed():
        await init_db()
        await import_network_data("job-social", data_dir)
        await analyze_network(job_id="job-social")

    async def _run():
        await _seed()

    asyncio.run(_run())
    return data_dir


def test_users_api(seeded):
    with TestClient(app) as client:
        listing = client.get("/api/users?page=1&page_size=10&sort=pagerank").json()
        assert listing["total"] == 5
        assert listing["items"][0]["pagerank"] >= listing["items"][-1]["pagerank"]

        detail = client.get(f"/api/users/{SEC_A}").json()
        assert detail["user"]["nickname"] == "作者A"
        assert detail["relations"]["in"], "作者A 应有入边"
        assert detail["videos"][0]["aweme_id"] == "aw1"

        assert client.get("/api/users/SEC_NOT_EXIST").status_code == 404


def test_network_api(seeded):
    with TestClient(app) as client:
        overview = client.get("/api/network/overview").json()
        assert overview["counts"]["users"] == 5
        assert overview["counts"]["relations"] == 6
        assert overview["analysis"]["node_count"] == 5
        assert overview["relation_types"][0]["type"] in ("commented_on", "replied_to", "following")

        graph = client.get("/api/network/graph?limit=50").json()
        assert len(graph["nodes"]) == 5
        assert graph["edges"]

        raw = client.get("/api/raw?page_size=5").json()
        assert raw["total"] == len(_entries())
        assert raw["items"][0]["payload"]

        rerun = client.post("/api/network/analyze").json()
        assert rerun["run_id"] >= 1


# --------------------------------------------------------------------- 用户角色画像
def test_classify_roles_covers_all_types():
    graph = nx.Graph()
    graph.add_edges_from(
        [("A", "B"), ("A", "C"), ("A", "D"), ("B", "C"), ("B", "E"), ("B", "F"), ("D", "E"), ("D", "F")]
    )
    roles = classify_roles(
        nodes=["A", "B", "C", "D", "E", "F", "G"],
        undirected=graph,
        degree={"A": 3, "B": 4, "C": 2, "D": 3, "E": 2, "F": 2, "G": 0},
        in_degree={"A": 1, "B": 4, "C": 2, "D": 0, "E": 1, "F": 0, "G": 0},
        out_degree={"A": 3, "B": 0, "C": 0, "D": 3, "E": 0, "F": 1, "G": 0},
        pagerank={"A": 0.20, "B": 0.35, "C": 0.30, "D": 0.05, "E": 0.05, "F": 0.03, "G": 0.02},
        betweenness={"A": 0.6, "B": 0.1, "C": 0.2, "D": 0.02, "E": 0.02, "F": 0.01, "G": 0.0},
        community_of={"A": 2, "B": 0, "C": 1, "D": 1, "E": 1, "F": 1, "G": 1},
        followers={"A": 10, "B": 100, "C": 50, "D": 5, "E": 5, "F": 5, "G": 0},
        aweme_counts={node: 0 for node in "ABCDEFG"},
    )

    assert roles["A"]["role"] == "bridge"
    assert roles["B"]["role"] == "hub"
    assert roles["C"]["role"] == "community_leader"
    assert roles["D"]["role"] == "active_commenter"
    assert roles["E"]["role"] == "creator"
    assert roles["F"]["role"] == "peripheral"
    assert roles["G"]["role"] == "isolated"

    assert roles["A"]["cross_community_ratio"] == 1.0
    assert 0 <= roles["B"]["influence_score"] <= 100
    assert roles["B"]["influence_score"] == max(item["influence_score"] for item in roles.values())
    assert set(ROLE_LABELS) >= {item["role"] for item in roles.values()}


def test_compute_metrics_emits_roles():
    users = [
        {"sec_uid": f"SEC_{name}", "nickname": name, "follower_count": 0, "aweme_count": 0, "depth": 0, "profile_complete": True}
        for name in ("A", "B", "C", "D")
    ]
    edges = [
        {"src_sec_uid": src, "dst_sec_uid": "SEC_A", "rel_type": "commented_on", "weight": 1}
        for src in ("SEC_B", "SEC_C", "SEC_D")
    ]
    result = compute_metrics(users, edges)
    assert result["metrics"]["SEC_A"]["role"] == "hub"
    assert result["summary"]["roles"]
    assert sum(item["count"] for item in result["summary"]["roles"]) == 4
    assert result["summary"]["top_influence"]


def test_users_roles_and_map_api(seeded):
    with TestClient(app) as client:
        roles = client.get("/api/users/roles").json()
        assert roles["total"] == 5
        assert sum(item["count"] for item in roles["items"]) == 5
        assert all(item["label"] for item in roles["items"])

        # 路由顺序：/users/map 不能被 /users/{sec_uid} 吞掉
        mapping = client.get("/api/users/map").json()
        assert mapping["totals"]["total"] == 5
        gd = next((item for item in mapping["known"] if item["region"] == "广东省"), None)
        assert gd is not None and gd["total"] >= 1

        filtered = client.get("/api/users?role=hub").json()
        assert filtered["total"] >= 1
        assert all(item["role"] == "hub" for item in filtered["items"])

        male = client.get("/api/users?gender=1").json()
        assert male["total"] == 0  # 测试数据没有性别 1 的账号
        unknown = client.get("/api/users?gender=0").json()
        assert unknown["total"] == 5
