# -*- coding: utf-8 -*-
"""用户网络相关接口：用户列表/详情、网络概览、图谱数据、原始数据、重新分析。"""

from __future__ import annotations

import json
from typing import Annotated, Any, Dict, List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from app.db import (
    Comment,
    DyUser,
    RawItem,
    SessionLocal,
    UserMetric,
    UserRelation,
    Video,
)
from app.social.graph import ROLE_LABELS, analyze_network, latest_analysis
from app.social.regions import aggregate_regions

router = APIRouter(prefix="/api", tags=["network"])

SORT_FIELDS = {
    "pagerank": UserMetric.pagerank,
    "influence": UserMetric.influence_score,
    "degree": UserMetric.degree_total,
    "betweenness": UserMetric.betweenness,
    "followers": DyUser.follower_count,
    "depth": DyUser.depth,
    "nickname": DyUser.nickname,
}


def _user_row(user: DyUser, metric: Optional[UserMetric]) -> Dict[str, Any]:
    return {
        "sec_uid": user.sec_uid,
        "uid": user.uid,
        "nickname": user.nickname,
        "unique_id": user.unique_id,
        "signature": user.signature,
        "avatar_url": user.avatar_url,
        "ip_location": user.ip_location,
        "follower_count": user.follower_count,
        "following_count": user.following_count,
        "aweme_count": user.aweme_count,
        "total_favorited": user.total_favorited,
        "is_verified": user.is_verified,
        "home_url": user.home_url,
        "source": user.source,
        "depth": user.depth,
        "profile_complete": user.profile_complete,
        "first_seen_ts": user.first_seen_ts,
        "last_seen_ts": user.last_seen_ts,
        "gender": user.gender,
        "pagerank": round(metric.pagerank, 6) if metric else 0.0,
        "role": (metric.role if metric else "") or "",
        "role_label": ROLE_LABELS.get(metric.role, metric.role) if metric else "",
        "influence_score": round(metric.influence_score, 2) if metric else 0.0,
        "cross_community_ratio": round(metric.cross_community_ratio, 4) if metric else 0.0,
        "degree_in": metric.degree_in if metric else 0,
        "degree_out": metric.degree_out if metric else 0,
        "degree_total": metric.degree_total if metric else 0,
        "betweenness": round(metric.betweenness, 6) if metric else 0.0,
        "community": metric.community if metric else -1,
        "component": metric.component if metric else -1,
    }


@router.get("/users")
async def list_users(
    q: str = "",
    sort: Literal[
        "pagerank", "influence", "degree", "betweenness", "followers", "depth", "nickname"
    ] = "pagerank",
    order: Literal["asc", "desc"] = "desc",
    depth: Optional[int] = None,
    community: Optional[int] = None,
    role: str = "",
    gender: Optional[int] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> Dict[str, Any]:
    base = select(DyUser).outerjoin(UserMetric, DyUser.sec_uid == UserMetric.sec_uid)
    if q:
        like = f"%{q}%"
        base = base.where(
            or_(
                DyUser.nickname.like(like),
                DyUser.unique_id.like(like),
                DyUser.sec_uid.like(like),
                DyUser.signature.like(like),
            )
        )
    if depth is not None:
        base = base.where(DyUser.depth == depth)
    if community is not None:
        base = base.where(UserMetric.community == community)
    if role:
        base = base.where(UserMetric.role == role)
    if gender is not None:
        base = base.where(DyUser.gender == gender)

    async with SessionLocal() as session:
        total = int(
            await session.scalar(select(func.count()).select_from(base.subquery())) or 0
        )
        column = SORT_FIELDS.get(sort, UserMetric.pagerank)
        stmt = (
            select(DyUser, UserMetric)
            .outerjoin(UserMetric, DyUser.sec_uid == UserMetric.sec_uid)
            .order_by(column.asc() if order == "asc" else column.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        if q:
            like = f"%{q}%"
            stmt = stmt.where(
                or_(
                    DyUser.nickname.like(like),
                    DyUser.unique_id.like(like),
                    DyUser.sec_uid.like(like),
                    DyUser.signature.like(like),
                )
            )
        if depth is not None:
            stmt = stmt.where(DyUser.depth == depth)
        if community is not None:
            stmt = stmt.where(UserMetric.community == community)
        if role:
            stmt = stmt.where(UserMetric.role == role)
        if gender is not None:
            stmt = stmt.where(DyUser.gender == gender)
        rows = (await session.execute(stmt)).all()

    return {
        "items": [_user_row(user, metric) for user, metric in rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/users/roles")
async def user_roles() -> Dict[str, Any]:
    """用户角色分布（由社交网络分析给出）。"""
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(UserMetric.role, func.count(UserMetric.sec_uid)).group_by(UserMetric.role)
            )
        ).all()
    total = sum(count for _, count in rows)
    items = [
        {
            "role": role or "peripheral",
            "label": ROLE_LABELS.get(role, role),
            "count": count,
            "ratio": round(count / total, 4) if total else 0,
        }
        for role, count in sorted(rows, key=lambda row: -row[1])
    ]
    return {"total": total, "items": items}


@router.get("/users/map")
async def user_map() -> Dict[str, Any]:
    """按 IP 属地聚合用户数量，并区分性别。"""
    async with SessionLocal() as session:
        rows = (
            await session.execute(
                select(DyUser.ip_location, DyUser.gender, func.count(DyUser.id)).group_by(
                    DyUser.ip_location, DyUser.gender
                )
            )
        ).all()
    return aggregate_regions(rows)


@router.get("/users/{sec_uid}")
async def user_detail(sec_uid: str) -> Dict[str, Any]:
    async with SessionLocal() as session:
        user = (
            await session.execute(select(DyUser).where(DyUser.sec_uid == sec_uid))
        ).scalar_one_or_none()
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        metric = await session.get(UserMetric, sec_uid)

        out_rows = (
            await session.execute(
                select(UserRelation)
                .where(UserRelation.src_sec_uid == sec_uid)
                .order_by(UserRelation.weight.desc())
                .limit(50)
            )
        ).scalars().all()
        in_rows = (
            await session.execute(
                select(UserRelation)
                .where(UserRelation.dst_sec_uid == sec_uid)
                .order_by(UserRelation.weight.desc())
                .limit(50)
            )
        ).scalars().all()

        peer_ids = {row.dst_sec_uid for row in out_rows} | {row.src_sec_uid for row in in_rows}
        peers: Dict[str, DyUser] = {}
        if peer_ids:
            peer_result = await session.execute(
                select(DyUser).where(DyUser.sec_uid.in_(list(peer_ids)))
            )
            peers = {peer.sec_uid: peer for peer in peer_result.scalars().all()}

        videos = (
            await session.execute(
                select(Video)
                .where(Video.user_sec_uid == sec_uid)
                .order_by(Video.create_time.desc())
                .limit(20)
            )
        ).scalars().all()

        comment_total = int(
            await session.scalar(
                select(func.count())
                .select_from(Comment)
                .where(Comment.user_sec_uid == sec_uid)
            )
            or 0
        )

    def relation_dict(row: UserRelation, peer_sec_uid: str) -> Dict[str, Any]:
        peer = peers.get(peer_sec_uid)
        return {
            "sec_uid": peer_sec_uid,
            "nickname": peer.nickname if peer else "",
            "follower_count": peer.follower_count if peer else 0,
            "rel_type": row.rel_type,
            "weight": row.weight,
            "evidence": row.evidence,
            "last_seen_ts": row.last_seen_ts,
        }

    return {
        "user": _user_row(user, metric),
        "relations": {
            "out": [relation_dict(row, row.dst_sec_uid) for row in out_rows],
            "in": [relation_dict(row, row.src_sec_uid) for row in in_rows],
        },
        "videos": [
            {
                "aweme_id": video.aweme_id,
                "title": video.title,
                "liked_count": video.liked_count,
                "comment_count": video.comment_count,
                "create_time": video.create_time,
                "aweme_url": video.aweme_url,
                "cover_url": video.cover_url,
            }
            for video in videos
        ],
        "comment_total": comment_total,
    }


@router.get("/network/overview")
async def network_overview() -> Dict[str, Any]:
    async with SessionLocal() as session:
        user_total = int(await session.scalar(select(func.count()).select_from(DyUser)) or 0)
        relation_total = int(
            await session.scalar(select(func.count()).select_from(UserRelation)) or 0
        )
        raw_total = int(await session.scalar(select(func.count()).select_from(RawItem)) or 0)
        video_total = int(await session.scalar(select(func.count()).select_from(Video)) or 0)
        comment_total = int(
            await session.scalar(select(func.count()).select_from(Comment)) or 0
        )
        profile_total = int(
            await session.scalar(
                select(func.count()).select_from(DyUser).where(DyUser.profile_complete.is_(True))
            )
            or 0
        )

        rel_rows = (
            await session.execute(
                select(UserRelation.rel_type, func.count(UserRelation.id), func.sum(UserRelation.weight))
                .group_by(UserRelation.rel_type)
            )
        ).all()
        depth_rows = (
            await session.execute(
                select(DyUser.depth, func.count(DyUser.id)).group_by(DyUser.depth).order_by(DyUser.depth)
            )
        ).all()

    analysis = await latest_analysis()
    return {
        "counts": {
            "users": user_total,
            "profiles": profile_total,
            "relations": relation_total,
            "raw_items": raw_total,
            "videos": video_total,
            "comments": comment_total,
        },
        "relation_types": [
            {"type": rel_type, "count": count, "weight": int(weight or 0)}
            for rel_type, count, weight in rel_rows
        ],
        "depth_distribution": [{"depth": depth, "count": count} for depth, count in depth_rows],
        "analysis": analysis,
    }


@router.post("/network/analyze")
async def run_analysis() -> Dict[str, Any]:
    run_id = await analyze_network()
    analysis = await latest_analysis()
    return {"run_id": run_id, "analysis": analysis}


@router.get("/network/graph")
async def network_graph(
    limit: Annotated[int, Query(ge=10, le=1000)] = 150,
    community: Optional[int] = None,
) -> Dict[str, Any]:
    """返回前端力导向图所需的节点与边（按度数截断）。"""
    async with SessionLocal() as session:
        stmt = (
            select(DyUser, UserMetric)
            .join(UserMetric, DyUser.sec_uid == UserMetric.sec_uid)
            .order_by(UserMetric.degree_total.desc(), UserMetric.pagerank.desc())
            .limit(limit)
        )
        if community is not None:
            stmt = stmt.where(UserMetric.community == community)
        rows = (await session.execute(stmt)).all()
        nodes = [
            {
                "sec_uid": user.sec_uid,
                "nickname": user.nickname or user.sec_uid[:8],
                "follower_count": user.follower_count,
                "depth": user.depth,
                "pagerank": round(metric.pagerank, 6),
                "degree": metric.degree_total,
                "community": metric.community,
            }
            for user, metric in rows
        ]
        node_ids = [node["sec_uid"] for node in nodes]
        edges: List[Dict[str, Any]] = []
        if node_ids:
            relation_rows = (
                await session.execute(
                    select(UserRelation)
                    .where(UserRelation.src_sec_uid.in_(node_ids))
                    .where(UserRelation.dst_sec_uid.in_(node_ids))
                )
            ).scalars().all()
            edges = [
                {
                    "source": row.src_sec_uid,
                    "target": row.dst_sec_uid,
                    "type": row.rel_type,
                    "weight": row.weight,
                }
                for row in relation_rows
            ]

    return {
        "nodes": nodes,
        "edges": edges,
        "truncated": len(rows) >= limit,
    }


@router.get("/raw")
async def list_raw(
    kind: str = "",
    job_id: str = "",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Dict[str, Any]:
    """浏览已保存的原始响应（payload 截断展示）。"""
    stmt = select(RawItem)
    if kind:
        stmt = stmt.where(RawItem.kind == kind)
    if job_id:
        stmt = stmt.where(RawItem.job_id == job_id)

    async with SessionLocal() as session:
        total = int(
            await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        )
        rows = (
            await session.execute(
                stmt.order_by(RawItem.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).scalars().all()

    items = []
    for row in rows:
        payload = row.payload
        if len(payload) > 5000:
            payload = payload[:5000] + "…"
        items.append(
            {
                "id": row.id,
                "job_id": row.job_id,
                "kind": row.kind,
                "external_id": row.external_id,
                "depth": row.depth,
                "source": row.source,
                "sha256": row.sha256,
                "fetched_at": row.fetched_at,
                "payload": payload,
            }
        )
    return {"items": items, "total": total, "page": page, "page_size": page_size}
