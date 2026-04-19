"""Tests for models.py — both legacy fields and new M1 scoring fields.

TDD note: this file is written BEFORE the M1 model changes land. The
`test_new_*` tests should fail until E-1 is implemented.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import MIN_INVESTMENT_PRICE_YEN, Property  # noqa: E402


# --- Legacy guarantees (do not regress) ----------------------------------

def test_minimal_property_constructs():
    p = Property(id="x1", name="物件1")
    assert p.id == "x1"
    assert p.price is None
    # scoring defaults
    assert p.dealScore is None
    assert p.dealRank is None
    assert p.dealReasons == []
    assert p.dealModelVersion == "v2.5"
    assert p.isAutoFallback is False
    # v2.1 new field defaults
    assert p.walkMinutes is None
    assert p.locationGrade is None
    assert p.lineRank is None
    assert p.remainingDurableYears is None
    assert p.locationScore is None
    assert p.yieldBenchmarkScore is None
    assert p.loanScore is None
    assert p.stagnationScore == 0
    assert p.riskScore is None
    assert p.inRedevelopmentZone is False
    assert p.hazardFlag is None
    assert p.structureEstimated is False
    assert p.benchmarkCapRate is None


def test_price_below_threshold_rejected():
    with pytest.raises(ValidationError):
        Property(id="x", name="rental", price=MIN_INVESTMENT_PRICE_YEN - 1)


def test_price_at_threshold_accepted():
    p = Property(id="x", name="ok", price=MIN_INVESTMENT_PRICE_YEN)
    assert p.price == MIN_INVESTMENT_PRICE_YEN


# --- New M1 fields: dealScore --------------------------------------------

@pytest.mark.parametrize("score", [0, 1, 50, 99, 100])
def test_deal_score_in_range_accepted(score):
    p = Property(id="x", name="n", dealScore=score)
    assert p.dealScore == score


@pytest.mark.parametrize("score", [-1, 101, 9999])
def test_deal_score_out_of_range_rejected(score):
    with pytest.raises(ValidationError):
        Property(id="x", name="n", dealScore=score)


def test_deal_score_none_allowed():
    p = Property(id="x", name="n", dealScore=None)
    assert p.dealScore is None


# --- New M1 fields: dealRank ---------------------------------------------

@pytest.mark.parametrize("rank", ["S", "A", "B", "C", "D", "N/A"])
def test_deal_rank_valid_values_accepted(rank):
    p = Property(id="x", name="n", dealRank=rank)
    assert p.dealRank == rank


@pytest.mark.parametrize("rank", ["E", "s", "a", "F", "", "NA"])
def test_deal_rank_invalid_values_rejected(rank):
    with pytest.raises(ValidationError):
        Property(id="x", name="n", dealRank=rank)


def test_deal_rank_none_allowed():
    p = Property(id="x", name="n", dealRank=None)
    assert p.dealRank is None


# --- New M1 fields: dealReasons ------------------------------------------

def test_deal_reasons_default_empty_list():
    p = Property(id="x", name="n")
    assert p.dealReasons == []


def test_deal_reasons_accepts_three_strings():
    p = Property(id="x", name="n", dealReasons=["a", "b", "c"])
    assert p.dealReasons == ["a", "b", "c"]


def test_deal_reasons_non_string_element_rejected():
    with pytest.raises(ValidationError):
        Property(id="x", name="n", dealReasons=["a", 123, "c"])


# --- New M1 fields: median / deviation -----------------------------------

def test_yield_median_and_pricepersqm_optional():
    p = Property(id="x", name="n", yieldMedianInArea=6.8, pricePerSqmMedian=420000)
    assert p.yieldMedianInArea == 6.8
    assert p.pricePerSqmMedian == 420000


def test_yield_deviation_can_be_negative():
    p = Property(id="x", name="n", yieldDeviation=-12.5)
    assert p.yieldDeviation == -12.5


# --- New M1 fields: model version + fallback flag ------------------------

def test_deal_model_version_overridable():
    p = Property(id="x", name="n", dealModelVersion="v2.5")
    assert p.dealModelVersion == "v2.5"


def test_deal_model_version_default_v23():
    p = Property(id="x", name="n")
    assert p.dealModelVersion == "v2.5"


def test_is_auto_fallback_flag():
    p = Property(id="x", name="n", isAutoFallback=True)
    assert p.isAutoFallback is True


# --- Composite construction (Sランク物件想定) ----------------------------

def test_full_scored_property_construction():
    p = Property(
        id="suumo-12345",
        name="博多駅徒歩8分・築18年RC",
        price=28_000_000,
        yieldGross=8.9,
        prefecture="福岡県",
        city="福岡市博多区",
        propertyType="区分マンション",
        builtYear=2008,
        age=18,
        structure="RC",
        area=42.5,
        dealScore=88,
        dealRank="S",
        dealReasons=[
            "福岡市博多区・路線A・徒歩8分の立地はSランクです。",
            "RC築18年で残存29年、長期融資が見込めます。",
            "表面8.9%はベンチマーク6.0%に対し+48%、市場との乖離を狙える水準です。",
        ],
        yieldMedianInArea=6.8,
        pricePerSqmMedian=803000,
        yieldDeviation=30.9,
        # v2.1 fields
        walkMinutes=8,
        locationGrade="S",
        lineRank="A",
        remainingDurableYears=29,
        locationScore=30,
        yieldBenchmarkScore=30,
        loanScore=20,
        stagnationScore=0,
        riskScore=10,
        inRedevelopmentZone=True,
        hazardFlag="medium",
        structureEstimated=False,
        benchmarkCapRate=6.0,
    )
    assert p.dealRank == "S"
    assert p.dealScore == 88
    assert len(p.dealReasons) == 3
    assert p.dealModelVersion == "v2.5"
    assert p.isAutoFallback is False
    assert p.locationScore == 30
    assert p.loanScore == 20


# --- v2.1 新フィールドの境界バリデーション --------------------------------

@pytest.mark.parametrize("loc", [0, 15, 30])
def test_location_score_in_range(loc):
    p = Property(id="x", name="n", locationScore=loc)
    assert p.locationScore == loc


@pytest.mark.parametrize("loc", [-1, 31])
def test_location_score_out_of_range_rejected(loc):
    with pytest.raises(ValidationError):
        Property(id="x", name="n", locationScore=loc)


@pytest.mark.parametrize("loan", [0, 10, 20])
def test_loan_score_in_range(loan):
    p = Property(id="x", name="n", loanScore=loan)
    assert p.loanScore == loan


@pytest.mark.parametrize("loan", [-1, 21])
def test_loan_score_out_of_range_rejected(loan):
    with pytest.raises(ValidationError):
        Property(id="x", name="n", loanScore=loan)


@pytest.mark.parametrize("y", [0, 15, 30])
def test_yield_benchmark_score_in_range(y):
    p = Property(id="x", name="n", yieldBenchmarkScore=y)
    assert p.yieldBenchmarkScore == y


@pytest.mark.parametrize("y", [-1, 31])
def test_yield_benchmark_score_out_of_range_rejected(y):
    with pytest.raises(ValidationError):
        Property(id="x", name="n", yieldBenchmarkScore=y)


@pytest.mark.parametrize("r", [0, 5, 10])
def test_risk_score_in_range(r):
    p = Property(id="x", name="n", riskScore=r)
    assert p.riskScore == r


@pytest.mark.parametrize("r", [-1, 11])
def test_risk_score_out_of_range_rejected(r):
    with pytest.raises(ValidationError):
        Property(id="x", name="n", riskScore=r)


@pytest.mark.parametrize("grade", ["S", "A", "B", "C", "D"])
def test_location_grade_valid(grade):
    p = Property(id="x", name="n", locationGrade=grade)
    assert p.locationGrade == grade


@pytest.mark.parametrize("bad", ["E", "X", ""])
def test_location_grade_invalid_rejected(bad):
    with pytest.raises(ValidationError):
        Property(id="x", name="n", locationGrade=bad)


@pytest.mark.parametrize("flag", ["high", "medium", "low"])
def test_hazard_flag_valid(flag):
    p = Property(id="x", name="n", hazardFlag=flag)
    assert p.hazardFlag == flag


@pytest.mark.parametrize("bad", ["HIGH", "warning", ""])
def test_hazard_flag_invalid_rejected(bad):
    with pytest.raises(ValidationError):
        Property(id="x", name="n", hazardFlag=bad)


def test_remaining_durable_years_can_be_negative():
    p = Property(id="x", name="n", remainingDurableYears=-5)
    assert p.remainingDurableYears == -5


def test_in_redevelopment_zone_default_false():
    p = Property(id="x", name="n")
    assert p.inRedevelopmentZone is False
    p2 = Property(id="x", name="n", inRedevelopmentZone=True)
    assert p2.inRedevelopmentZone is True


def test_structure_estimated_default_false():
    p = Property(id="x", name="n")
    assert p.structureEstimated is False
    p2 = Property(id="x", name="n", structureEstimated=True)
    assert p2.structureEstimated is True


# --- v2.2: compositeRankValue (タイブレーカー用) -------------------------

def test_composite_rank_value_default_none():
    p = Property(id="x", name="n")
    assert p.compositeRankValue is None


@pytest.mark.parametrize("v", [0.0, 50.0, 85.0323, 99.999, 101.0])
def test_composite_rank_value_in_range(v):
    p = Property(id="x", name="n", compositeRankValue=v)
    assert p.compositeRankValue == v


@pytest.mark.parametrize("v", [-0.001, 101.001, 200.0])
def test_composite_rank_value_out_of_range_rejected(v):
    with pytest.raises(ValidationError):
        Property(id="x", name="n", compositeRankValue=v)


def test_v2_2_default_model_version():
    """v2.2 で DEAL_MODEL_VERSION_DEFAULT が bump."""
    p = Property(id="x", name="n")
    assert p.dealModelVersion == "v2.5"
