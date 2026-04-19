"""Tests for services/medians.py — area median computation with fallback (E-2).

PRD §5.2.3 spec:
- group key: (prefecture, city, propertyType)
- ≥15 items: exact median
- 5-14 items: fall back to (prefecture, propertyType)
- <5 items: no median → caller treats as N/A
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Property  # noqa: E402
from services.medians import (  # noqa: E402
    benchmark_or_population_median,
    compute_medians,
    get_yield_benchmark,
    group_key,
    lookup_medians,
)


# --- helpers -------------------------------------------------------------

def make_prop(
    *,
    prefecture: str = "福岡県",
    city: str = "福岡市博多区",
    property_type: str = "区分マンション",
    yield_gross: float | None = 6.5,
    price: int | None = 25_000_000,
    area: float | None = 40.0,
    pid: str = "p-x",
) -> Property:
    return Property(
        id=pid,
        name="t",
        price=price,
        yieldGross=yield_gross,
        prefecture=prefecture,
        city=city,
        propertyType=property_type,
        area=area,
    )


def make_props(n: int, **kwargs) -> list[Property]:
    return [make_prop(pid=f"p-{i}", **kwargs) for i in range(n)]


# --- group_key -----------------------------------------------------------

def test_group_key_returns_three_tuple():
    p = make_prop()
    assert group_key(p) == ("福岡県", "福岡市博多区", "区分マンション")


# --- exact median (>=15) -------------------------------------------------

def test_exact_median_when_group_has_15_or_more():
    props = make_props(15, yield_gross=6.0, price=24_000_000, area=40.0)
    out = compute_medians(props)
    key = ("福岡県", "福岡市博多区", "区分マンション")
    assert key in out
    assert out[key]["source"] == "exact"
    assert out[key]["sample_size"] == 15
    assert out[key]["yield_median"] == 6.0
    assert out[key]["price_per_sqm_median"] == 24_000_000 / 40.0


def test_exact_median_correct_with_mixed_values():
    yields = [4.0, 5.0, 6.0, 7.0, 8.0] * 3  # 15 items, median = 6.0
    props = [
        make_prop(pid=f"p-{i}", yield_gross=y, price=20_000_000, area=40.0)
        for i, y in enumerate(yields)
    ]
    out = compute_medians(props)
    key = ("福岡県", "福岡市博多区", "区分マンション")
    assert out[key]["yield_median"] == 6.0


def test_yield_none_excluded_from_median():
    """Properties with yieldGross=None must not poison the median."""
    props = make_props(14, yield_gross=6.0, price=25_000_000, area=40.0)
    props.append(make_prop(pid="p-noyield", yield_gross=None, price=25_000_000, area=40.0))
    out = compute_medians(props)
    key = ("福岡県", "福岡市博多区", "区分マンション")
    assert out[key]["sample_size"] == 15
    assert out[key]["yield_median"] == 6.0


# --- fallback (5-14) -----------------------------------------------------

def test_fallback_when_group_has_5_to_14():
    """Single city has 8 props → fallback to (prefecture, propertyType)."""
    props = (
        make_props(8, city="福岡市博多区", yield_gross=6.0)
        + make_props(8, city="福岡市中央区", yield_gross=8.0)
    )
    out = compute_medians(props)

    key_exact = ("福岡県", "福岡市博多区", "区分マンション")
    key_fallback = ("福岡県", "区分マンション")
    assert out[key_exact]["source"] == "fallback"
    # fallback merges all 16 props → median yields 7.0
    assert out[key_exact]["yield_median"] == 7.0
    assert out[key_exact]["sample_size"] == 16
    # fallback key itself also recorded
    assert out[key_fallback]["source"] == "fallback_aggregate"


def test_no_median_when_group_under_5_and_fallback_also_short():
    """4 props in city, only 4 in prefecture/type → no median anywhere."""
    props = make_props(4, city="福岡市博多区")
    out = compute_medians(props)
    key = ("福岡県", "福岡市博多区", "区分マンション")
    assert key not in out


# --- lookup_medians ------------------------------------------------------

def test_lookup_returns_exact_when_present():
    props = make_props(15)
    medians = compute_medians(props)
    p = props[0]
    assert lookup_medians(p, medians) is not None
    assert lookup_medians(p, medians)["source"] == "exact"


def test_lookup_returns_none_when_no_data():
    props = make_props(2)
    medians = compute_medians(props)
    p = props[0]
    assert lookup_medians(p, medians) is None


def test_lookup_falls_back_via_two_tuple_key():
    """Sparse city group (8) → exact entry is fallback-flavored."""
    props = (
        make_props(8, city="福岡市博多区", yield_gross=6.0)
        + make_props(8, city="福岡市中央区", yield_gross=8.0)
    )
    medians = compute_medians(props)
    p = props[0]
    assert lookup_medians(p, medians)["source"] == "fallback"


# --- price_per_sqm safety ------------------------------------------------

def test_price_per_sqm_skips_zero_area():
    props = make_props(15, price=25_000_000, area=40.0)
    # one outlier with area=0 (would division-by-zero)
    props.append(make_prop(pid="bad", price=25_000_000, area=0.0))
    out = compute_medians(props)
    key = ("福岡県", "福岡市博多区", "区分マンション")
    # 16 items go in but area=0 is skipped from price_per_sqm
    assert out[key]["price_per_sqm_median"] == 625_000  # 25M / 40


# --- Cap Rate ベンチマーク（v2.1 主指標） -------------------------------

def test_benchmark_fukuoka_single():
    p = Property(
        id="x", name="n",
        prefecture="福岡県", city="福岡市中央区",
        propertyType="区分マンション",
    )
    # layoutを直接設定
    p.layout = "1K"
    assert get_yield_benchmark(p) == 5.25


def test_benchmark_fukuoka_family():
    p = make_prop(city="福岡市博多区")
    p.layout = "2LDK"
    assert get_yield_benchmark(p) == 6.0


def test_benchmark_kitakyushu():
    p = make_prop(city="北九州市小倉北区")
    assert get_yield_benchmark(p) == 10.0


def test_benchmark_kurume():
    p = make_prop(city="久留米市")
    assert get_yield_benchmark(p) == 10.0


def test_benchmark_default_fallback():
    p = make_prop(city="春日市")
    assert get_yield_benchmark(p) == 6.0


def test_benchmark_or_population_uses_benchmark_first():
    p = make_prop(city="福岡市博多区")
    val, src = benchmark_or_population_median(p, {})
    assert val == 6.0
    assert src == "benchmark"
