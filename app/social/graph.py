# -*- coding: utf-8 -*-
"""社交网络拓扑分析。

以「用户」为节点、「互动关系」为边构建有向图，计算社交网络研究中常用的指标：

- **PageRank**（Brin & Page, 1998）：有向图上的影响力/被引用程度；
- **度中心性**（Freeman, 1978）：入度=被互动，出度=主动互动；
- **介数中心性**（Freeman, 1977）：经过该节点的最短路径比例，衡量"桥接/结构洞"位置；
- **Louvain 社区发现**（Blondel et al., 2008）：识别抱团社区及其模块度；
- **连通分量 / 密度 / 平均度**：整体拓扑形态。

计算与持久化分离：`compute_metrics` 是纯函数，`analyze_network` 负责读库、落库。
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List, Optional

import networkx as nx
from sqlalchemy import delete, select

from app.db import DyUser, NetworkRun, SessionLocal, UserMetric, UserRelation

# 不同关系类型对"连接强度"的贡献：关注关系最稳定，回复比一级评论更强
REL_TYPE_WEIGHT = {
    "commented_on": 1.0,
    "replied_to": 1.5,
    "following": 2.0,
    "co_author": 1.0,
}
BETWEENNESS_EXACT_LIMIT = 1500
PAGERANK_ALPHA = 0.85


def build_graphs(
    users: Iterable[Dict[str, Any]], edges: Iterable[Dict[str, Any]]
) -> tuple[nx.DiGraph, nx.Graph]:
    """构建有向图（影响力）与无向图（社区/桥接）。"""
    digraph = nx.DiGraph()
    for user in users:
        sec_uid = (user.get("sec_uid") or "").strip()
        if not sec_uid:
            continue
        digraph.add_node(
            sec_uid,
            nickname=user.get("nickname") or "",
            follower_count=int(user.get("follower_count") or 0),
            depth=int(user.get("depth") or 0),
            profile_complete=bool(user.get("profile_complete")),
        )

    for edge in edges:
        src = (edge.get("src_sec_uid") or "").strip()
        dst = (edge.get("dst_sec_uid") or "").strip()
        if not src or not dst or src == dst:
            continue
        rel_type = edge.get("rel_type") or "commented_on"
        weight = float(edge.get("weight") or 1) * REL_TYPE_WEIGHT.get(rel_type, 1.0)
        for node in (src, dst):
            if node not in digraph:
                digraph.add_node(node, nickname="", follower_count=0, depth=0, profile_complete=False)
        if digraph.has_edge(src, dst):
            digraph[src][dst]["weight"] += weight
            digraph[src][dst]["types"].add(rel_type)
        else:
            digraph.add_edge(src, dst, weight=weight, types={rel_type})

    undirected = nx.Graph()
    undirected.add_nodes_from(digraph.nodes)
    for src, dst, data in digraph.edges(data=True):
        if undirected.has_edge(src, dst):
            undirected[src][dst]["weight"] += data["weight"]
        else:
            undirected.add_edge(src, dst, weight=data["weight"])
    return digraph, undirected


def _pagerank(
    graph: nx.DiGraph, alpha: float = PAGERANK_ALPHA, max_iter: int = 100, tol: float = 1e-9
) -> Dict[str, float]:
    """纯 Python 的加权 PageRank（幂迭代）。

    不依赖 numpy/scipy，保证最小安装即可运行；规模在万级节点内足够快。
    """
    nodes = list(graph.nodes)
    count = len(nodes)
    if count == 0:
        return {}
    rank = {node: 1.0 / count for node in nodes}
    out_weight = {
        node: sum(float(data.get("weight", 1.0)) for _, _, data in graph.out_edges(node, data=True))
        for node in nodes
    }
    for _ in range(max_iter):
        updated = {node: (1.0 - alpha) / count for node in nodes}
        dangling = sum(rank[node] for node in nodes if out_weight[node] == 0)
        for node in nodes:
            total = out_weight[node]
            if not total:
                continue
            share = alpha * rank[node] / total
            for _, target, data in graph.out_edges(node, data=True):
                updated[target] += share * float(data.get("weight", 1.0))
        if dangling:
            bonus = alpha * dangling / count
            for node in nodes:
                updated[node] += bonus
        diff = sum(abs(updated[node] - rank[node]) for node in nodes)
        rank = updated
        if diff < tol * count:
            break
    return rank


def _label(graph: nx.Graph, node: str) -> str:
    nickname = graph.nodes[node].get("nickname") if node in graph else ""
    return nickname or node


def compute_metrics(
    users: Iterable[Dict[str, Any]], edges: Iterable[Dict[str, Any]]
) -> Dict[str, Any]:
    """计算全部图指标（不访问数据库，便于单测）。"""
    users = list(users)
    edges = list(edges)
    digraph, undirected = build_graphs(users, edges)
    node_count = digraph.number_of_nodes()
    edge_count = digraph.number_of_edges()

    metrics: Dict[str, Dict[str, Any]] = {}
    summary: Dict[str, Any] = {
        "top_pagerank": [],
        "top_betweenness": [],
        "top_degree": [],
        "communities": [],
        "relation_types": {},
        "isolated_nodes": 0,
    }
    if node_count == 0:
        return {
            "node_count": 0,
            "edge_count": 0,
            "density": 0.0,
            "avg_degree": 0.0,
            "component_count": 0,
            "community_count": 0,
            "modularity": 0.0,
            "metrics": metrics,
            "summary": summary,
        }

    pagerank = _pagerank(digraph) if edge_count else {node: 0.0 for node in digraph}
    in_degree = dict(digraph.in_degree())
    out_degree = dict(digraph.out_degree())
    degree = dict(undirected.degree())

    undirected_edges = undirected.number_of_edges()
    if undirected_edges == 0:
        betweenness = {node: 0.0 for node in undirected}
        communities = [{node} for node in sorted(undirected)]
    else:
        if node_count <= BETWEENNESS_EXACT_LIMIT:
            betweenness = nx.betweenness_centrality(undirected, normalized=True)
        else:
            # 大图用 k 抽样近似，避免 O(n*m) 爆炸
            betweenness = nx.betweenness_centrality(
                undirected, k=min(500, node_count), normalized=True, seed=42
            )
        communities = list(
            nx.community.louvain_communities(undirected, weight="weight", seed=42)
        )
        communities.sort(key=len, reverse=True)

    community_of: Dict[str, int] = {}
    for index, members in enumerate(communities):
        for node in members:
            community_of[node] = index

    components = sorted(nx.connected_components(undirected), key=len, reverse=True)
    component_of: Dict[str, int] = {}
    for index, members in enumerate(components):
        for node in members:
            component_of[node] = index

    if undirected_edges and len(communities) > 1:
        modularity = float(
            nx.community.modularity(undirected, communities, weight="weight")
        )
    else:
        modularity = 0.0

    isolated = 0
    for node in digraph.nodes:
        if degree.get(node, 0) == 0:
            isolated += 1
        metrics[node] = {
            "pagerank": float(pagerank.get(node, 0.0)),
            "degree_in": int(in_degree.get(node, 0)),
            "degree_out": int(out_degree.get(node, 0)),
            "degree_total": int(degree.get(node, 0)),
            "betweenness": float(betweenness.get(node, 0.0)),
            "community": int(community_of.get(node, -1)),
            "component": int(component_of.get(node, -1)),
            "nickname": _label(digraph, node),
            "follower_count": int(digraph.nodes[node].get("follower_count") or 0),
            "depth": int(digraph.nodes[node].get("depth") or 0),
        }

    relation_types: Dict[str, int] = {}
    for edge in edges:
        rel = edge.get("rel_type") or "commented_on"
        relation_types[rel] = relation_types.get(rel, 0) + 1

    def top(key: str, limit: int = 10) -> List[Dict[str, Any]]:
        ranked = sorted(metrics.items(), key=lambda pair: pair[1][key], reverse=True)
        return [
            {
                "sec_uid": sec_uid,
                "nickname": data["nickname"],
                "follower_count": data["follower_count"],
                "value": round(float(data[key]), 6),
            }
            for sec_uid, data in ranked[:limit]
            if data[key] > 0
        ]

    summary.update(
        {
            "top_pagerank": top("pagerank"),
            "top_betweenness": top("betweenness"),
            "top_degree": top("degree_total"),
            "communities": [
                {
                    "id": index,
                    "size": len(members),
                    "leaders": [
                        {
                            "sec_uid": node,
                            "nickname": metrics[node]["nickname"],
                            "pagerank": round(metrics[node]["pagerank"], 6),
                        }
                        for node in sorted(
                            members, key=lambda n: metrics[n]["pagerank"], reverse=True
                        )[:5]
                    ],
                }
                for index, members in enumerate(communities[:20])
            ],
            "relation_types": relation_types,
            "isolated_nodes": isolated,
        }
    )

    return {
        "node_count": node_count,
        "edge_count": edge_count,
        "density": float(nx.density(digraph)),
        "avg_degree": round(sum(degree.values()) / node_count, 4),
        "component_count": len(components),
        "community_count": len(communities),
        "modularity": round(modularity, 6),
        "metrics": metrics,
        "summary": summary,
    }


async def analyze_network(job_id: str = "") -> int:
    """读取当前库中的用户与关系，计算指标并落库，返回分析批次 ID。"""
    async with SessionLocal() as session:
        user_rows = (await session.execute(select(DyUser))).scalars().all()
        relation_rows = (await session.execute(select(UserRelation))).scalars().all()

    users = [
        {
            "sec_uid": user.sec_uid,
            "nickname": user.nickname,
            "follower_count": user.follower_count,
            "depth": user.depth,
            "profile_complete": user.profile_complete,
        }
        for user in user_rows
    ]
    edges = [
        {
            "src_sec_uid": relation.src_sec_uid,
            "dst_sec_uid": relation.dst_sec_uid,
            "rel_type": relation.rel_type,
            "weight": relation.weight,
        }
        for relation in relation_rows
    ]

    result = compute_metrics(users, edges)
    computed_at = int(time.time())

    async with SessionLocal() as session:
        run = NetworkRun(
            created_at=computed_at,
            node_count=result["node_count"],
            edge_count=result["edge_count"],
            density=result["density"],
            avg_degree=result["avg_degree"],
            component_count=result["component_count"],
            community_count=result["community_count"],
            modularity=result["modularity"],
            params=json.dumps({"job_id": job_id}, ensure_ascii=False),
            summary=json.dumps(result["summary"], ensure_ascii=False),
        )
        session.add(run)
        await session.flush()
        await session.execute(delete(UserMetric))
        for sec_uid, values in result["metrics"].items():
            session.add(
                UserMetric(
                    sec_uid=sec_uid,
                    run_id=run.id,
                    pagerank=values["pagerank"],
                    degree_in=values["degree_in"],
                    degree_out=values["degree_out"],
                    degree_total=values["degree_total"],
                    betweenness=values["betweenness"],
                    community=values["community"],
                    component=values["component"],
                    computed_at=computed_at,
                )
            )
        await session.commit()
        return int(run.id)


async def latest_analysis() -> Optional[Dict[str, Any]]:
    """返回最近一次网络分析的汇总（供 API 使用）。"""
    async with SessionLocal() as session:
        run = (
            await session.execute(select(NetworkRun).order_by(NetworkRun.id.desc()).limit(1))
        ).scalar_one_or_none()
        if run is None:
            return None
        try:
            summary = json.loads(run.summary or "{}")
        except json.JSONDecodeError:
            summary = {}
        return {
            "run_id": run.id,
            "created_at": run.created_at,
            "node_count": run.node_count,
            "edge_count": run.edge_count,
            "density": run.density,
            "avg_degree": run.avg_degree,
            "component_count": run.component_count,
            "community_count": run.community_count,
            "modularity": run.modularity,
            "summary": summary,
        }
