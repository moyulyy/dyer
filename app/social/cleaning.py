# -*- coding: utf-8 -*-
"""抖音原始数据的清洗与归一化。

引擎只负责把原始响应写进 `network/raw.jsonl`，本模块负责把它变成
可以入库的规范字段：去控制字符、数字转 int、缺失字段兜底、跨来源合并。
所有函数都是纯函数，便于单测。
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
WHITESPACE = re.compile(r"\s+")


def clean_text(value: Any, limit: int = 2000) -> str:
    """去掉控制字符、折叠空白、截断超长文本。"""
    if value is None:
        return ""
    text = CONTROL_CHARS.sub("", str(value))
    text = WHITESPACE.sub(" ", text).strip()
    if limit and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def to_int(value: Any) -> int:
    """把 `"1.2e4"`、`"123"`、None 等统一转成 int，失败返回 0。"""
    if value is None or value == "":
        return 0
    try:
        if isinstance(value, bool):
            return int(value)
        return int(float(str(value)))
    except (TypeError, ValueError):
        return 0


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def normalize_sec_uid(value: Any) -> str:
    """校验 sec_uid：抖音的 sec_uid 为 Base64 风格长串，去掉空白与非法字符。"""
    text = clean_text(value, 128)
    if len(text) < 3:
        return ""
    return text


def _first_url(obj: Any) -> str:
    """从 {"url_list": [...]} 结构里取第一个可用 URL。"""
    if not isinstance(obj, dict):
        return ""
    urls = obj.get("url_list") or []
    if isinstance(urls, list):
        for url in urls:
            if url:
                return clean_text(url, 500)
    return ""


def _avatar(user: Dict[str, Any]) -> str:
    for key in ("avatar_larger", "avatar_medium", "avatar_thumb", "avatar_168x168"):
        url = _first_url(user.get(key))
        if url:
            return url
    return ""


def normalize_user(
    raw: Dict[str, Any],
    *,
    source: str = "comment",
    depth: int = 0,
    ts: int = 0,
    job_id: str = "",
    complete: bool = False,
) -> Optional[Dict[str, Any]]:
    """把主页/评论/作品里的 user 对象归一化成 DyUser 字段。

    返回 None 表示该记录无法识别用户（缺少 sec_uid），调用方应跳过。
    """
    if not isinstance(raw, dict):
        return None
    sec_uid = normalize_sec_uid(raw.get("sec_uid") or raw.get("sec_user_id"))
    if not sec_uid:
        return None

    unique_id = clean_text(raw.get("unique_id") or raw.get("short_id"), 128)
    signature = clean_text(raw.get("signature"), 500)
    nickname = clean_text(raw.get("nickname"), 64)

    return {
        "sec_uid": sec_uid,
        "uid": clean_text(raw.get("uid"), 64),
        "nickname": nickname,
        "unique_id": unique_id,
        "signature": signature,
        "avatar_url": _avatar(raw),
        "gender": to_int(raw.get("gender")),
        "ip_location": clean_text(raw.get("ip_location"), 64),
        "follower_count": to_int(raw.get("follower_count")),
        "following_count": to_int(raw.get("following_count")),
        "aweme_count": to_int(raw.get("aweme_count")),
        "total_favorited": to_int(raw.get("total_favorited")),
        "is_verified": to_bool(raw.get("is_verified")) or bool(clean_text(raw.get("custom_verify"), 64)),
        "home_url": f"https://www.douyin.com/user/{sec_uid}",
        "source": source,
        "depth": max(0, int(depth or 0)),
        "profile_complete": bool(complete),
        "first_seen_ts": ts,
        "last_seen_ts": ts,
        "last_job_id": job_id,
    }


def merge_user(existing: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """同一用户多次出现时的合并规则（用于跨响应/跨任务去重）。

    - 文本字段：新的非空值优先（主页资料通常比评论里的摘要更完整）
    - 计数字段：取最大值，避免爬取顺序造成数据回退
    - depth 取最小值，first_seen 取最早，profile_complete 有则保留
    """
    merged = dict(existing)
    for field, value in new.items():
        if field in ("follower_count", "following_count", "aweme_count", "total_favorited"):
            merged[field] = max(to_int(existing.get(field)), to_int(value))
        elif field in ("first_seen_ts",):
            current = to_int(existing.get(field))
            merged[field] = min(current, to_int(value)) if current else to_int(value)
        elif field == "last_seen_ts":
            merged[field] = max(to_int(existing.get(field)), to_int(value))
        elif field == "depth":
            current = existing.get(field)
            merged[field] = min(int(current), int(value)) if current is not None else int(value)
        elif field == "profile_complete":
            merged[field] = bool(existing.get(field)) or bool(value)
        elif field in ("is_verified",):
            merged[field] = bool(existing.get(field)) or bool(value)
        else:
            if value not in ("", None, 0, False):
                merged[field] = value
            elif field not in merged:
                merged[field] = value
    return merged


def _author_of(raw: Dict[str, Any]) -> Dict[str, Any]:
    author = raw.get("author")
    if isinstance(author, dict):
        return author
    user = raw.get("user")
    return user if isinstance(user, dict) else {}


def normalize_aweme(raw: Dict[str, Any], *, ts: int = 0, keyword: str = "") -> Optional[Dict[str, Any]]:
    """归一化作品，并带上作者 sec_uid，便于与用户维度关联。"""
    if not isinstance(raw, dict):
        return None
    aweme_id = clean_text(raw.get("aweme_id"), 64)
    if not aweme_id:
        return None
    author = _author_of(raw)
    sec_uid = normalize_sec_uid(author.get("sec_uid"))
    stats = raw.get("statistics") or {}
    return {
        "aweme_id": aweme_id,
        "aweme_type": clean_text(raw.get("aweme_type"), 32),
        "title": clean_text(raw.get("desc"), 500),
        "desc": clean_text(raw.get("desc"), 4000),
        "nickname": clean_text(author.get("nickname"), 64),
        "creator_hash": "",
        "create_time": to_int(raw.get("create_time")),
        "liked_count": to_int(stats.get("digg_count")),
        "comment_count": to_int(stats.get("comment_count")),
        "share_count": to_int(stats.get("share_count")),
        "collected_count": to_int(stats.get("collect_count")),
        "aweme_url": f"https://www.douyin.com/video/{aweme_id}",
        "cover_url": _first_url((raw.get("video") or {}).get("cover"))
        or _first_url((raw.get("video") or {}).get("origin_cover")),
        "video_download_url": "",
        "music_download_url": "",
        "note_download_url": "",
        "source_keyword": clean_text(keyword, 128),
        "user_sec_uid": sec_uid,
        "add_ts": ts,
        "last_modify_ts": ts,
        "_author": author if sec_uid else None,
    }


def normalize_comment(
    raw: Dict[str, Any], *, aweme_id: str, ts: int = 0
) -> Optional[Dict[str, Any]]:
    """归一化评论，并返回评论者信息（用于用户维度）。"""
    if not isinstance(raw, dict):
        return None
    comment_id = clean_text(raw.get("cid"), 64)
    if not comment_id:
        return None
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    sec_uid = normalize_sec_uid(user.get("sec_uid"))
    return {
        "comment_id": comment_id,
        "aweme_id": aweme_id,
        "content": clean_text(raw.get("text"), 2000),
        "nickname": clean_text(user.get("nickname"), 64),
        "creator_hash": "",
        "create_time": to_int(raw.get("create_time")),
        "like_count": to_int(raw.get("digg_count")),
        "sub_comment_count": to_int(raw.get("reply_comment_total")),
        "parent_comment_id": clean_text(raw.get("reply_id") or "0", 64),
        "pictures": "",
        "user_sec_uid": sec_uid,
        "add_ts": ts,
        "_user": user if sec_uid else None,
        "_reply_to_uid": clean_text(raw.get("reply_to_userid"), 64),
    }


def relation_key(src: str, dst: str, rel_type: str) -> tuple[str, str, str]:
    return (src, dst, rel_type)


def merge_relations(items: Iterable[Dict[str, Any]]) -> Dict[tuple[str, str, str], Dict[str, Any]]:
    """把同一对用户的多条互动证据合并成一条边（weight 累加）。"""
    merged: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    for item in items:
        src, dst, rel_type = item["src_sec_uid"], item["dst_sec_uid"], item["rel_type"]
        if not src or not dst or src == dst:
            continue
        key = relation_key(src, dst, rel_type)
        current = merged.get(key)
        if current is None:
            merged[key] = {
                "src_sec_uid": src,
                "dst_sec_uid": dst,
                "rel_type": rel_type,
                "weight": 1,
                "evidence": item.get("evidence", ""),
                "first_seen_ts": item.get("ts", 0),
                "last_seen_ts": item.get("ts", 0),
                "last_job_id": item.get("job_id", ""),
            }
        else:
            current["weight"] += 1
            current["last_seen_ts"] = max(current["last_seen_ts"], item.get("ts", 0))
            if item.get("evidence"):
                current["evidence"] = item["evidence"]
    return merged
