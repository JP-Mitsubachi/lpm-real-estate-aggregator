"""Tests for services/fallback_reasons.py — v2.1 3-line text (立地・融資・収益).

PRD §5.3.2 spec: 3 lines, location → loan → yield.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Property  # noqa: E402
from services.fallback_reasons import generate_fallback_reasons  # noqa: E402


def _hakata(**overrides) -> Property:
    base = dict(
        id="x",
        name="n",
        prefecture="福岡県",
        city="福岡市博多区",
        propertyType="区分マンション",
        yieldGross=8.9,
        price=25_000_000,
        area=42.5,
        age=18,
        structure="RC",
        layout="2LDK",
        nearestStation="ＪＲ鹿児島本線「博多」徒歩7分",
        dealScore=75,
        dealRank="A",
        # v2.1 fields
        walkMinutes=7,
        locationGrade="S",
        lineRank="A",
        remainingDurableYears=29,
        benchmarkCapRate=6.0,
    )
    base.update(overrides)
    return Property(**base)


# --- shape ---------------------------------------------------------------

def test_returns_exactly_three_lines():
    out = generate_fallback_reasons(_hakata())
    assert isinstance(out, list)
    assert len(out) == 3
    for line in out:
        assert isinstance(line, str)
        assert line.strip()


# --- 立地行 ---------------------------------------------------------------

def test_location_line_includes_city_and_walk_and_grade():
    out = generate_fallback_reasons(_hakata())
    assert "福岡市博多区" in out[0]
    assert "徒歩7分" in out[0]
    assert "S" in out[0]


def test_location_line_with_no_walk():
    out = generate_fallback_reasons(_hakata(walkMinutes=None))
    assert "福岡市博多区" in out[0]
    assert "徒歩" not in out[0]


def test_location_line_with_unknown_grade():
    out = generate_fallback_reasons(_hakata(locationGrade=None))
    assert "−" in out[0] or "ランク" in out[0]


# --- 融資行 ---------------------------------------------------------------

def test_loan_line_long_remaining():
    out = generate_fallback_reasons(_hakata(age=10, remainingDurableYears=37))
    assert "残存37年" in out[1]
    assert "長期融資" in out[1]


def test_loan_line_medium_remaining():
    out = generate_fallback_reasons(_hakata(age=30, remainingDurableYears=17))
    assert "残存17年" in out[1]
    assert "地銀" in out[1] or "信金" in out[1]


def test_loan_line_short_remaining():
    out = generate_fallback_reasons(_hakata(age=40, remainingDurableYears=7))
    assert "残存7年" in out[1]
    assert "自己資金" in out[1]


def test_loan_line_over_lifespan():
    out = generate_fallback_reasons(_hakata(age=50, remainingDurableYears=-3))
    assert "法定耐用年数超え" in out[1]


def test_loan_line_handles_missing_age():
    out = generate_fallback_reasons(_hakata(age=None, remainingDurableYears=None))
    assert "築年不明" in out[1]


def test_loan_line_marks_estimated_structure():
    out = generate_fallback_reasons(
        _hakata(structureEstimated=True, structure="RC")
    )
    assert "推定" in out[1]


# --- 収益行 ---------------------------------------------------------------

def test_yield_line_includes_benchmark_comparison():
    out = generate_fallback_reasons(_hakata(yieldGross=8.9, benchmarkCapRate=6.0))
    assert "8.9" in out[2]
    assert "6.0" in out[2]
    # 乖離+48%
    assert "+" in out[2]


def test_yield_line_when_yield_missing():
    out = generate_fallback_reasons(_hakata(yieldGross=None))
    assert "未掲載" in out[2]


def test_yield_line_when_below_benchmark():
    out = generate_fallback_reasons(_hakata(yieldGross=5.0, benchmarkCapRate=6.0))
    assert "5.0" in out[2]
    assert "下回る" in out[2]


# --- robustness ----------------------------------------------------------

def test_handles_no_benchmark_via_medians_fallback():
    p = _hakata(yieldGross=8.0, benchmarkCapRate=None)
    out = generate_fallback_reasons(p, medians={"yield_median": 6.5})
    assert "6.5" in out[2]


def test_handles_no_benchmark_at_all():
    p = _hakata(yieldGross=None, benchmarkCapRate=None)
    out = generate_fallback_reasons(p)
    assert len(out) == 3
    assert out[2]


# --- 長さ予算（合計300字以内・新軸で多少緩めに） ----------------------

def test_total_length_under_400_chars():
    out = generate_fallback_reasons(_hakata())
    total = sum(len(line) for line in out)
    assert total < 400, f"too long: {total} chars"
