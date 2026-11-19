# -*- coding: utf-8 -*-
"""扩散采集器测试：用假客户端验证 BFS 调度、去重、层级限制与降级。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from dyer_network import ExplorationPlanner, NetworkExplorer, RawSink  # noqa: E402


def _user(uid: str, sec: str, nick: str) -> dict:
    return {"uid": uid, "sec_uid": sec, "nickname": nick}


def _aweme(aweme_id: str, author: dict) -> dict:
    return {
        "aweme_id": aweme_id,
        "desc": f"作品{aweme_id}",
        "create_time": 1700000000,
        "author": author,
        "statistics": {"digg_count": 10, "comment_count": 2},
    }


class FakeClient:
    """最小可用的抖音客户端替身，记录调用次数。"""

    def __init__(self) -> None:
        self.calls = 0
        self.following_raises = True
        self.users = {
            "SEC_A": _user("1", "SEC_A", "作者A"),
            "SEC_B": _user("2", "SEC_B", "作者B"),
            "SEC_C": _user("3", "SEC_C", "用户C"),
        }

    async def search_info_by_keyword(self, **kwargs):
        self.calls += 1
        return {
            "data": [{"aweme_info": _aweme("a1", self.users["SEC_A"])}],
            "extra": {"logid": "log-1"},
        }

    async def get_video_by_id(self, aweme_id):
        self.calls += 1
        return _aweme(aweme_id, self.users["SEC_A"])

    async def get_user_info(self, sec_uid):
        self.calls += 1
        return {"user": {**self.users.get(sec_uid, _user("9", sec_uid, "未知")), "follower_count": 100}}

    async def get_user_aweme_posts(self, sec_uid, max_cursor=""):
        self.calls += 1
        author = self.users.get(sec_uid, _user("9", sec_uid, "未知"))
        return {"aweme_list": [_aweme(f"{sec_uid}-1", author)], "has_more": 0}

    async def get_aweme_comments(self, aweme_id, cursor=0):
        self.calls += 1
        return {
            "comments": [
                {"cid": f"{aweme_id}-c1", "aweme_id": aweme_id, "text": "评论", "user": self.users["SEC_C"], "reply_id": "0"},
                # 同一评论者重复出现，不应重复入队
                {"cid": f"{aweme_id}-c2", "aweme_id": aweme_id, "text": "再评", "user": self.users["SEC_C"], "reply_id": "0"},
            ],
            "has_more": 0,
        }

    async def get_sub_comments(self, aweme_id, comment_id, cursor=0):
        self.calls += 1
        return {"comments": [], "has_more": 0}

    async def get(self, uri, params=None, headers=None):
        self.calls += 1
        if self.following_raises:
            raise RuntimeError("接口受限")
        return {"followings": [self.users["SEC_B"]]}


@pytest.mark.asyncio
async def test_planner_dedup_and_depth_limit():
    planner = ExplorationPlanner(max_users=3, max_depth=1)
    assert planner.add_seed("SEC_A") is True
    assert planner.add_seed("SEC_A") is False  # 去重
    assert planner.add_candidate("SEC_B", 1, "comment") is True
    assert planner.add_candidate("SEC_C", 2, "comment") is False  # 超出层级
    assert planner.add_candidate("SEC_D", 1, "comment") is True  # 第 3 个用户，仍在预算内
    assert planner.add_candidate("SEC_E", 1, "comment") is False  # 超出人数预算
    assert planner.discovered == 3
    assert planner.pop()[0] == "SEC_A"


@pytest.mark.asyncio
async def test_explorer_writes_raw_and_degrades(tmp_path: Path):
    sink = RawSink(str(tmp_path / "network" / "raw.jsonl"))
    client = FakeClient()
    explorer = NetworkExplorer(
        client,
        sink,
        {
            "keywords": ["露营"],
            "expand_depth": 2,
            "max_users": 5,
            "max_awemes_per_user": 2,
            "max_comments_per_aweme": 2,
            "include_following": True,  # 关注接口会抛错，应被降级
            "sleep_sec": 0,
        },
    )
    summary = await explorer.run()
    sink.close()

    assert summary["users_discovered"] >= 2
    assert summary["users_expanded"] >= 1
    assert summary["raw_records"] > 0

    records = [json.loads(line) for line in (tmp_path / "network" / "raw.jsonl").read_text(encoding="utf-8").splitlines()]
    kinds = {record["kind"] for record in records}
    assert {"search_list", "user_profile", "aweme_list", "comment_list"} <= kinds

    # 评论者只被发现一次
    comment_records = [r for r in records if r["kind"] == "comment_list"]
    assert comment_records
    assert comment_records[0]["meta"]["author_sec_uid"] == "SEC_A"
    assert comment_records[0]["depth"] == 1  # 评论层级 = 作者层级 + 1

    # 关注接口失败不影响主流程，且不会产生 following_list
    assert "following_list" not in kinds
