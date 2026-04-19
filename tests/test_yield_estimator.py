"""Tests for services/yield_estimator.py — SUUMO/ふれんず利回り推計 (B案).

本番 properties.json (3,183件) で調査した結果:
- SUUMO 0/1369 (0%) が yieldGross 未掲載
- ふれんず 0/1318 (0%) も同様
- HOME'S 442/496 (89%) のみ yieldGross あり

これにより S/A ランクが HOME'S に偏重する問題を、
中央値 + Cap Rate ベンチマークによる推計でカバーする。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Property  # noqa: E402
from services.medians import compute_medians  # noqa: E402
from services.yield_estimator import estimate_yield_for_property  # noqa: E402


# --- helpers --------------------------------------------------------------

def _make_population(
    n: int = 20,
    *,
    city: str = "福岡市博多区",
    propertyType: str = "区分マンション",
    yield_base: float = 6.0,
    yield_step: float = 0.05,
) -> list[Property]:
    """yield 付き母集団を n 件生成（compute_medians が有効になる規模）."""
    return [
        Property(
            id=f"pop-{i}",
            name=f"{city}物件{i}",
            prefecture="福岡県",
            city=city,
            propertyType=propertyType,
            layout="2LDK",
            structure="RC",
            age=10,
            yieldGross=yield_base + (i * yield_step),
            price=20_000_000 + i * 100_000,
            area=40.0,
            nearestStation="ＪＲ鹿児島本線「博多」徒歩10分",
        )
        for i in range(n)
    ]


# --- test_estimate_yield_returns_actual_when_yieldgross_present ------------

def test_estimate_yield_returns_actual_when_yieldgross_present():
    """yieldGross が掲載されている物件（HOME'S 等）は actual を返す."""
    population = _make_population(n=20)
    medians = compute_medians(population)

    target = Property(
        id="t1",
        name="実利回り掲載物件",
        prefecture="福岡県",
        city="福岡市博多区",
        propertyType="区分マンション",
        layout="2LDK",
        structure="RC",
        age=10,
        yieldGross=7.2,  # 実利回り掲載あり
        price=20_000_000,
        area=40.0,
    )

    est, conf = estimate_yield_for_property(target, medians)
    assert est == 7.2
    assert conf == "actual"


# --- test_estimate_yield_returns_median_fallback_when_no_yield -------------

def test_estimate_yield_returns_median_fallback_when_no_yield():
    """yieldGross が None で yieldMedianInArea がある場合、median 由来を返す."""
    population = _make_population(n=20)
    medians = compute_medians(population)

    target = Property(
        id="t2",
        name="SUUMO物件",
        sourceName="SUUMO",
        prefecture="福岡県",
        city="福岡市博多区",
        propertyType="区分マンション",
        layout="2LDK",
        structure="RC",
        age=10,
        yieldGross=None,
        price=20_000_000,
        area=40.0,
    )
    # scoring.py 側で書き戻される前提: 事前に median を設定
    target.yieldMedianInArea = 6.475

    est, conf = estimate_yield_for_property(target, medians)
    assert est == 6.475
    assert conf == "median"


# --- test_estimate_yield_returns_benchmark_when_no_median ------------------

def test_estimate_yield_returns_benchmark_when_no_median():
    """yieldGross も yieldMedianInArea も無い場合、Cap Rate ベンチマークで fallback."""
    medians: dict = {}  # 母集団 0 件 → medians 空

    target = Property(
        id="t3",
        name="ふれんず物件",
        sourceName="ふれんず",
        prefecture="福岡県",
        city="福岡市中央区",
        propertyType="区分マンション",
        layout="1K",  # 単身 → fukuoka_city_single (5.25%)
        structure="RC",
        age=10,
        yieldGross=None,
        price=15_000_000,
        area=25.0,
    )
    target.yieldMedianInArea = None  # 明示

    est, conf = estimate_yield_for_property(target, medians)
    assert est == 5.25
    assert conf == "fallback"


# --- test_estimate_yield_returns_none_when_no_data -------------------------

def test_estimate_yield_returns_none_when_no_data():
    """yieldGross/median/benchmark すべて無効 → (None, 'none').

    benchmark が 0 以下 になるケースは現状 config で存在しないが、
    明示的に benchmark_or_population_median が 0 を返した場合に none とする。
    city 空 & prefecture 空 & propertyType 空 → benchmark は default=6.0 になるため、
    ここでは benchmark_cap_rate が 0 を返すよう monkeypatch で強制。
    """
    target = Property(
        id="t4",
        name="データなし物件",
        prefecture="",
        city="",
        propertyType="",
        yieldGross=None,
    )
    target.yieldMedianInArea = None

    # benchmark を 0 にパッチして "none" ケースを作る
    import services.yield_estimator as ye

    original = ye.get_benchmark_cap_rate
    try:
        ye.get_benchmark_cap_rate = lambda p: 0.0  # type: ignore[assignment]
        est, conf = estimate_yield_for_property(target, {})
    finally:
        ye.get_benchmark_cap_rate = original  # type: ignore[assignment]

    assert est is None
    assert conf == "none"


# --- 追加: ふれんず/SUUMO 典型シナリオの統合チェック -----------------------

def test_estimate_yield_suumo_full_flow():
    """SUUMO 物件（yieldGross=None）を medians + yieldMedianInArea 同時に使って推計."""
    population = _make_population(n=30, city="福岡市博多区")
    medians = compute_medians(population)

    target = Property(
        id="suumo-1",
        name="SUUMO博多マンション",
        sourceName="SUUMO",
        prefecture="福岡県",
        city="福岡市博多区",
        propertyType="区分マンション",
        layout="2LDK",
        structure="RC",
        age=10,
        yieldGross=None,
        price=20_000_000,
        area=40.0,
    )
    # 母集団由来の中央値を設定
    target.yieldMedianInArea = 6.5

    est, conf = estimate_yield_for_property(target, medians)
    assert conf == "median"
    assert est == 6.5


def test_estimate_yield_benchmark_kitakyushu_high_rate():
    """北九州の Cap Rate ベンチマークは 10.0%（fallback ケース）."""
    target = Property(
        id="furenzu-1",
        name="ふれんず北九州物件",
        sourceName="ふれんず",
        prefecture="福岡県",
        city="北九州市小倉北区",
        propertyType="区分マンション",
        layout="2LDK",
        structure="RC",
        age=10,
        yieldGross=None,
        price=6_000_000,
        area=35.0,
    )
    target.yieldMedianInArea = None

    est, conf = estimate_yield_for_property(target, {})
    assert est == 10.0
    assert conf == "fallback"
