# -*- coding: utf-8 -*-
"""IP 属地归一化与地区聚合测试。"""

from app.social.regions import OTHER, UNKNOWN, aggregate_regions, normalize_region


def test_normalize_region_aliases():
    assert normalize_region("广东") == "广东省"
    assert normalize_region("广东省") == "广东省"
    assert normalize_region("北京") == "北京市"
    assert normalize_region("中国香港") == "香港特别行政区"
    assert normalize_region("内蒙古") == "内蒙古自治区"
    assert normalize_region("新疆") == "新疆维吾尔自治区"
    assert normalize_region("浙江杭州") == "浙江省"
    assert normalize_region("  四川  ") == "四川省"
    assert normalize_region("") == UNKNOWN
    assert normalize_region(None) == UNKNOWN
    assert normalize_region("海外") == OTHER


def test_aggregate_regions_gender_split():
    result = aggregate_regions(
        [
            ("广东", 1, 3),
            ("广东", 2, 2),
            ("广东", 0, 1),
            ("北京", 1, 4),
            ("海外", 0, 2),
        ]
    )
    assert result["totals"] == {"total": 12, "male": 7, "female": 2, "unknown": 3}

    gd = next(item for item in result["known"] if item["region"] == "广东省")
    assert (gd["total"], gd["male"], gd["female"], gd["unknown"]) == (6, 3, 2, 1)
    assert result["regions"][0]["region"] == "广东省"  # 按总数降序
    assert [item["region"] for item in result["others"]] == [OTHER]
    assert all(item["region"] != OTHER for item in result["known"])


def test_aggregate_empty():
    result = aggregate_regions([])
    assert result["regions"] == []
    assert result["totals"] == {"total": 0, "male": 0, "female": 0, "unknown": 0}
