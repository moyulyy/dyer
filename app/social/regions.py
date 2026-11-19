# -*- coding: utf-8 -*-
"""把抖音 IP 属地文本归一化为省级行政区名称。

抖音返回的 `ip_location` 通常是「广东」「浙江」「北京」这类简称，偶尔是
「中国香港」或「海外」。前端地图使用 GeoJSON 的省名（如「广东省」），
因此这里统一转换，并把无法识别的地点归入「其他」。
"""

from __future__ import annotations

import re
from typing import Iterable

UNKNOWN = "未知"
OTHER = "其他"

# 简称 / 别名 -> GeoJSON 中的省级行政区全称
REGION_ALIASES = {
    "北京": "北京市",
    "天津": "天津市",
    "上海": "上海市",
    "重庆": "重庆市",
    "河北": "河北省",
    "山西": "山西省",
    "辽宁": "辽宁省",
    "吉林": "吉林省",
    "黑龙江": "黑龙江省",
    "江苏": "江苏省",
    "浙江": "浙江省",
    "安徽": "安徽省",
    "福建": "福建省",
    "江西": "江西省",
    "山东": "山东省",
    "河南": "河南省",
    "湖北": "湖北省",
    "湖南": "湖南省",
    "广东": "广东省",
    "海南": "海南省",
    "四川": "四川省",
    "贵州": "贵州省",
    "云南": "云南省",
    "陕西": "陕西省",
    "甘肃": "甘肃省",
    "青海": "青海省",
    "台湾": "台湾省",
    "内蒙古": "内蒙古自治区",
    "广西": "广西壮族自治区",
    "西藏": "西藏自治区",
    "宁夏": "宁夏回族自治区",
    "新疆": "新疆维吾尔自治区",
    "香港": "香港特别行政区",
    "澳门": "澳门特别行政区",
}

PROVINCES: list[str] = list(dict.fromkeys(REGION_ALIASES.values()))

_PREFIX = re.compile(r"^中国")


def normalize_region(value: str | None) -> str:
    """返回省级全称；空值返回「未知」，无法识别返回「其他」。"""
    text = (value or "").strip()
    if not text:
        return UNKNOWN
    text = _PREFIX.sub("", text).strip()
    if not text:
        return UNKNOWN
    if text in REGION_ALIASES:
        return REGION_ALIASES[text]
    if text in PROVINCES:
        return text
    # 「广东省深圳市」「浙江杭州」等，取省级前缀
    for short, full in REGION_ALIASES.items():
        if text.startswith(short):
            return full
    return OTHER


def is_known_region(value: str) -> bool:
    return value in PROVINCES


def aggregate_regions(rows: Iterable[tuple[str, int, int]]) -> dict:
    """把 (ip_location, gender, count) 聚合成地图所需的地区统计。

    gender: 1=男，2=女，其他=未知。
    """
    buckets: dict[str, dict[str, int]] = {}
    for location, gender, count in rows:
        region = normalize_region(location)
        bucket = buckets.setdefault(
            region, {"region": region, "total": 0, "male": 0, "female": 0, "unknown": 0}
        )
        bucket["total"] += count
        if gender == 1:
            bucket["male"] += count
        elif gender == 2:
            bucket["female"] += count
        else:
            bucket["unknown"] += count

    regions = sorted(buckets.values(), key=lambda item: (-item["total"], item["region"]))
    totals = {
        "total": sum(item["total"] for item in regions),
        "male": sum(item["male"] for item in regions),
        "female": sum(item["female"] for item in regions),
        "unknown": sum(item["unknown"] for item in regions),
    }
    return {
        "regions": regions,
        "known": [item for item in regions if is_known_region(item["region"])],
        "others": [item for item in regions if not is_known_region(item["region"])],
        "totals": totals,
    }
