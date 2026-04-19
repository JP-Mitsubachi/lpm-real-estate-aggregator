"""Tests for services/scoring.py — v2.1 5軸スコアリング (PRD §5).

新軸:
- locationScore (0-30) ← location_score.py
- yieldBenchmarkScore (0-30) ← Cap Rateベンチマーク乖離
- loanScore (0-20) ← 残存耐用年数 + 構造推定
- stagnationScore (0 固定)
- riskScore (0-10) ← 再開発+ハザード

Sランク複合条件・残存5年未満強制降格を含む。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Property  # noqa: E402
from services.scoring import (  # noqa: E402
    assign_rank,
    apply_s_rank_guard,
    calc_yield_benchmark_score,
    calc_loan_score,
    calc_risk_score,
    calc_stagnation_score,
    estimate_structure,
    get_benchmark_cap_rate,
    get_remaining_durable_years,
    get_structure_lifespan,
    score_property,
)


# --- Cap Rate ベンチマーク ------------------------------------------------

def test_benchmark_fukuoka_single_room():
    p = Property(id="x", name="n", city="福岡市中央区", layout="1K")
    assert get_benchmark_cap_rate(p) == 5.25


def test_benchmark_fukuoka_family():
    p = Property(id="x", name="n", city="福岡市博多区", layout="2LDK")
    assert get_benchmark_cap_rate(p) == 6.0


def test_benchmark_kitakyushu():
    p = Property(id="x", name="n", city="北九州市小倉北区", layout="2LDK")
    assert get_benchmark_cap_rate(p) == 10.0


def test_benchmark_kurume():
    p = Property(id="x", name="n", city="久留米市", layout="3LDK")
    assert get_benchmark_cap_rate(p) == 10.0


def test_benchmark_default_fallback():
    p = Property(id="x", name="n", city="春日市", layout="2LDK")
    assert get_benchmark_cap_rate(p) == 6.0  # 福岡市ファミリー値で仮置き


# --- 収益スコア (yieldBenchmarkScore) ------------------------------------

def test_yield_score_at_benchmark_is_zero():
    """ベンチマーク同等は0点."""
    p = Property(id="x", name="n", city="福岡市博多区", layout="2LDK", yieldGross=6.0)
    assert calc_yield_benchmark_score(p) == 0


def test_yield_score_below_benchmark_is_zero():
    p = Property(id="x", name="n", city="福岡市博多区", layout="2LDK", yieldGross=5.0)
    assert calc_yield_benchmark_score(p) == 0


def test_yield_score_above_benchmark_scaled():
    """+20%乖離で30点満点."""
    # bench 6.0 の +20% = 7.2 → 30点
    p = Property(id="x", name="n", city="福岡市博多区", layout="2LDK", yieldGross=7.2)
    assert calc_yield_benchmark_score(p) == 30


def test_yield_score_capped_at_30():
    p = Property(id="x", name="n", city="福岡市博多区", layout="2LDK", yieldGross=20.0)
    assert calc_yield_benchmark_score(p) == 30


def test_yield_score_none_when_yield_missing():
    p = Property(id="x", name="n", city="福岡市博多区", layout="2LDK", yieldGross=None)
    assert calc_yield_benchmark_score(p) is None


def test_yield_score_kitakyushu_higher_benchmark():
    """北九州 bench 10.0 → 利回り12は +20%乖離で 30点."""
    p = Property(id="x", name="n", city="北九州市小倉北区", layout="2LDK", yieldGross=12.0)
    assert calc_yield_benchmark_score(p) == 30


# --- 構造推定 -------------------------------------------------------------

def test_estimate_structure_explicit_rc():
    p = Property(id="x", name="n", structure="RC造", propertyType="区分マンション")
    s, est = estimate_structure(p)
    assert s == "RC"
    assert est is False


def test_estimate_structure_alias_normalized():
    p = Property(id="x", name="n", structure="鉄筋コンクリート", propertyType="区分マンション")
    s, est = estimate_structure(p)
    assert s == "RC"
    assert est is False


def test_estimate_structure_blank_with_mansion():
    p = Property(id="x", name="n", structure="", propertyType="区分マンション")
    s, est = estimate_structure(p)
    assert s == "RC"
    assert est is True


def test_estimate_structure_blank_with_apart():
    p = Property(id="x", name="n", structure="", propertyType="一棟売りアパート")
    s, est = estimate_structure(p)
    assert s == "S造"
    assert est is True


def test_estimate_structure_blank_with_house():
    p = Property(id="x", name="n", structure="", propertyType="戸建賃貸")
    s, est = estimate_structure(p)
    assert s == "木造"
    assert est is True


def test_estimate_structure_blank_with_land():
    p = Property(id="x", name="n", structure="", propertyType="投資用土地・事業用土地")
    s, est = estimate_structure(p)
    assert s is None
    assert est is True


def test_estimate_structure_blank_default():
    p = Property(id="x", name="n", structure="", propertyType="売り店舗・事務所")
    s, est = estimate_structure(p)
    assert s == "RC"  # デフォルトでRC
    assert est is True


# --- 残存耐用年数 ----------------------------------------------------------

def test_get_lifespan_rc():
    assert get_structure_lifespan("RC") == 47


def test_get_lifespan_wood():
    assert get_structure_lifespan("木造") == 22


def test_get_lifespan_steel():
    assert get_structure_lifespan("S造") == 34


def test_remaining_years_calculation():
    p = Property(id="x", name="n", structure="RC造", propertyType="マンション", age=15)
    assert get_remaining_durable_years(p) == 32


def test_remaining_years_negative_when_over_lifespan():
    p = Property(id="x", name="n", structure="木造", propertyType="戸建賃貸", age=30)
    # 22 - 30 = -8
    assert get_remaining_durable_years(p) == -8


def test_remaining_years_none_when_age_missing():
    p = Property(id="x", name="n", structure="RC", propertyType="マンション", age=None)
    assert get_remaining_durable_years(p) is None


# --- 融資スコア (loanScore) ------------------------------------------------

def test_loan_score_brand_new_rc_full():
    p = Property(id="x", name="n", structure="RC", propertyType="マンション", age=0)
    assert calc_loan_score(p) == 20


def test_loan_score_15_year_rc():
    """残存32年 → 20点."""
    p = Property(id="x", name="n", structure="RC", propertyType="マンション", age=15)
    assert calc_loan_score(p) == 20


def test_loan_score_30_year_rc_remaining_17():
    """残存17年 → 15点."""
    p = Property(id="x", name="n", structure="RC", propertyType="マンション", age=30)
    assert calc_loan_score(p) == 15


def test_loan_score_40_year_rc_remaining_7():
    """残存7年 → 10点."""
    p = Property(id="x", name="n", structure="RC", propertyType="マンション", age=40)
    assert calc_loan_score(p) == 10


def test_loan_score_45_year_rc_remaining_2():
    """残存2年 → 5点."""
    p = Property(id="x", name="n", structure="RC", propertyType="マンション", age=45)
    assert calc_loan_score(p) == 5


def test_loan_score_over_lifespan_rc():
    """築50年RC（法定47超）→ 0点."""
    p = Property(id="x", name="n", structure="RC", propertyType="マンション", age=50)
    assert calc_loan_score(p) == 0


def test_loan_score_wood_22_at_threshold():
    """木造22年（残存0）→ 0点."""
    p = Property(id="x", name="n", structure="木造", propertyType="戸建賃貸", age=22)
    assert calc_loan_score(p) == 0


def test_loan_score_wood_15_remaining_7():
    """木造15年残存7年 → 10点."""
    p = Property(id="x", name="n", structure="木造", propertyType="戸建賃貸", age=15)
    assert calc_loan_score(p) == 10


def test_loan_score_land_only_midpoint():
    """土地は中位点(10)."""
    p = Property(id="x", name="n", structure="", propertyType="投資用土地・事業用土地", age=None)
    assert calc_loan_score(p) == 10


def test_loan_score_age_none_returns_none():
    p = Property(id="x", name="n", structure="RC", propertyType="マンション", age=None)
    assert calc_loan_score(p) is None


# --- リスクスコア ----------------------------------------------------------

def test_risk_score_chuo_with_redevelopment():
    """中央区 + 天神駅 → 5(safety) + 5(redevelopment) = 10."""
    p = Property(
        id="x", name="n",
        city="福岡市中央区",
        nearestStation="地下鉄空港線「天神」徒歩3分",
    )
    assert calc_risk_score(p) == 10


def test_risk_score_hakata_medium_hazard_with_redevelopment():
    """博多区 medium ハザード(safety 5は維持) + 再開発5 = 10."""
    p = Property(
        id="x", name="n",
        city="福岡市博多区",
        nearestStation="ＪＲ鹿児島本線「博多」徒歩5分",
    )
    # medium は減点しない設計（high のみ -5）
    assert calc_risk_score(p) == 10


def test_risk_score_kitakyushu_no_redev():
    """北九州 → ハザード安全だが再開発なし = 5."""
    p = Property(
        id="x", name="n",
        city="北九州市小倉北区",
        nearestStation="モノレール「旦過」徒歩5分",
    )
    assert calc_risk_score(p) == 5


def test_risk_score_no_city():
    p = Property(id="x", name="n", city="", nearestStation="")
    assert calc_risk_score(p) == 5  # 安全側にデフォルト


# --- 滞留スコア（v2.1は0固定） ----------------------------------------------

def test_stagnation_score_always_zero():
    p = Property(id="x", name="n")
    assert calc_stagnation_score(p) == 0


# --- assign_rank ----------------------------------------------------------

@pytest.mark.parametrize("score,rank", [
    (100, "S"), (80, "S"),
    (79, "A"), (65, "A"),
    (64, "B"), (50, "B"),
    (49, "C"), (35, "C"),
    (34, "D"), (0, "D"),
])
def test_assign_rank_thresholds(score, rank):
    assert assign_rank(score) == rank


def test_assign_rank_none_returns_na():
    assert assign_rank(None) == "N/A"


# --- Sランク複合条件ガード ------------------------------------------------

def test_s_guard_passes_when_all_conditions_met():
    rank = apply_s_rank_guard(
        rank="S", score=82,
        location_score=30, loan_score=20, yield_score=22,
        remaining_years=32,
    )
    assert rank == "S"


def test_s_guard_demotes_when_location_too_low():
    rank = apply_s_rank_guard(
        rank="S", score=82,
        location_score=10,  # < 15
        loan_score=20, yield_score=22, remaining_years=32,
    )
    assert rank == "A"


def test_s_guard_demotes_when_loan_too_low():
    rank = apply_s_rank_guard(
        rank="S", score=82,
        location_score=20, loan_score=5,  # < 10
        yield_score=22, remaining_years=32,
    )
    assert rank == "A"


def test_s_guard_demotes_when_yield_too_low():
    rank = apply_s_rank_guard(
        rank="S", score=82,
        location_score=20, loan_score=20,
        yield_score=10,  # < 15
        remaining_years=32,
    )
    assert rank == "A"


def test_s_guard_demotes_when_remaining_under_10():
    rank = apply_s_rank_guard(
        rank="S", score=82,
        location_score=30, loan_score=10, yield_score=20,
        remaining_years=8,  # < 10
    )
    assert rank == "A"


def test_s_guard_passthrough_for_non_s_ranks_when_remaining_safe():
    """A以下のランクで残存5年以上 → ガードは影響しない."""
    rank = apply_s_rank_guard(
        rank="A", score=70,
        location_score=10, loan_score=5, yield_score=10,
        remaining_years=15,
    )
    assert rank == "A"


def test_s_guard_force_demote_remaining_under_5():
    """残存5年未満は S/A/B/C どれでも強制 D."""
    rank = apply_s_rank_guard(
        rank="A", score=70,
        location_score=15, loan_score=5, yield_score=15,
        remaining_years=3,
    )
    assert rank == "D"


def test_s_guard_remaining_none_does_not_force_demote():
    """残存が None（土地など） → 強制降格しない."""
    rank = apply_s_rank_guard(
        rank="A", score=70,
        location_score=15, loan_score=10, yield_score=15,
        remaining_years=None,
    )
    assert rank == "A"


# --- score_property 統合テスト --------------------------------------------

def test_score_property_full_s_example():
    """中央区RC築8年 利回り7.5% 徒歩4分 空港線."""
    p = Property(
        id="x", name="物件",
        prefecture="福岡県",
        city="福岡市中央区",
        propertyType="区分マンション",
        layout="2LDK",
        structure="RC造",
        age=8,
        yieldGross=7.5,
        price=28_000_000,
        area=45.0,
        nearestStation="地下鉄空港線「赤坂」徒歩4分",
    )
    scored = score_property(p)
    assert scored.dealRank == "S"
    assert scored.dealScore is not None and scored.dealScore >= 80
    assert scored.locationScore == 30
    assert scored.loanScore == 20
    assert scored.yieldBenchmarkScore is not None and scored.yieldBenchmarkScore >= 15
    assert scored.dealModelVersion == "v2.5"
    assert scored.locationGrade == "S"
    assert scored.lineRank == "S"


def test_score_property_kitakyushu_high_yield_not_s():
    """北九州木造築22年12% → 立地C+融資0で複合条件不合格 → S 不可."""
    p = Property(
        id="x", name="物件",
        prefecture="福岡県",
        city="北九州市小倉北区",
        propertyType="戸建賃貸",
        layout="3LDK",
        structure="木造",
        age=22,
        yieldGross=12.0,
        price=5_000_000,
        area=70.0,
        nearestStation="モノレール「旦過」徒歩18分",
    )
    scored = score_property(p)
    assert scored.dealRank != "S"
    # 残存0で強制D降格
    assert scored.dealRank == "D"


def test_score_property_n_a_when_city_missing():
    """city 空文字 → N/A."""
    p = Property(
        id="x", name="物件",
        city="",
        propertyType="区分マンション",
        age=10,
        structure="RC",
    )
    scored = score_property(p)
    assert scored.dealRank == "N/A"
    assert scored.dealScore is None


def test_score_property_n_a_when_age_missing_and_not_land():
    p = Property(
        id="x", name="物件",
        city="福岡市博多区",
        propertyType="区分マンション",
        age=None,
    )
    scored = score_property(p)
    assert scored.dealRank == "N/A"


def test_score_property_land_can_score():
    """土地は age なしでも N/A にしない."""
    p = Property(
        id="x", name="物件",
        city="福岡市中央区",
        propertyType="投資用土地・事業用土地",
        age=None,
        nearestStation="地下鉄空港線「赤坂」徒歩5分",
        price=20_000_000,
    )
    scored = score_property(p)
    # 立地30 + 融資10(土地中位) + 滞留0 + リスク10 = 50, 収益None
    # 収益None時は0として加算（部分点回避でNone扱いにはしない）
    assert scored.dealRank in ("S", "A", "B", "C", "D")


def test_score_property_partial_yield_only_still_scores():
    """yieldGross欠損でも他軸で点数つく."""
    p = Property(
        id="x", name="物件",
        city="福岡市博多区",
        propertyType="区分マンション",
        layout="2LDK",
        structure="RC",
        age=10,
        yieldGross=None,
        nearestStation="ＪＲ鹿児島本線「博多」徒歩5分",
    )
    scored = score_property(p)
    assert scored.dealRank in ("S", "A", "B", "C", "D")
    assert scored.locationScore is not None
    assert scored.loanScore == 20
    assert scored.yieldBenchmarkScore is None
    # Sランク条件 yield>=15 で必ず弾かれるので S にはならない
    assert scored.dealRank != "S"


def test_score_property_structure_estimated_flag_set():
    """structure 欠損 → 推定フラグが立ち、reasonsに注記."""
    p = Property(
        id="x", name="物件",
        city="福岡市博多区",
        propertyType="区分マンション",  # → RC推定
        structure="",
        layout="2LDK",
        age=10,
        yieldGross=6.5,
        nearestStation="地下鉄空港線「赤坂」徒歩7分",
    )
    scored = score_property(p)
    assert scored.structureEstimated is True
    assert any("構造推定" in r for r in scored.dealReasons)


def test_score_property_includes_reasons_three_lines():
    p = Property(
        id="x", name="物件",
        city="福岡市中央区",
        propertyType="区分マンション",
        layout="1K",
        structure="RC",
        age=10,
        yieldGross=6.0,
        nearestStation="地下鉄空港線「赤坂」徒歩7分",
    )
    scored = score_property(p)
    # reasons は location_score / scoring の責務分担で生成
    assert len(scored.dealReasons) >= 3


# ===== v2.2: Sランク price 上限ガード =======================================

def _s_rank_candidate(*, pid_override: str = "s-cand", **overrides) -> Property:
    """Sランク条件を満たす中央区RC築8年の雛形."""
    base = dict(
        id=pid_override,
        name="物件",
        prefecture="福岡県",
        city="福岡市中央区",
        propertyType="区分マンション",
        layout="2LDK",
        structure="RC造",
        age=8,
        yieldGross=7.5,
        price=28_000_000,
        area=45.0,
        nearestStation="地下鉄空港線「赤坂」徒歩4分",
    )
    base.update(overrides)
    return Property(**base)


def test_s_rank_kept_when_price_under_ceiling():
    """3000万円以下のSランク候補は維持される."""
    p = _s_rank_candidate(price=25_000_000)
    scored = score_property(p)
    assert scored.dealRank == "S"


def test_s_rank_demoted_when_price_exceeds_ceiling():
    """3000万円超のSランク候補は A 降格."""
    p = _s_rank_candidate(price=31_000_000)
    scored = score_property(p)
    assert scored.dealRank == "A"


def test_s_rank_demoted_when_price_far_exceeds_ceiling():
    """5,900万円のような実際にRound1で混入したケース."""
    p = _s_rank_candidate(price=59_000_000)
    scored = score_property(p)
    assert scored.dealRank == "A"


def test_s_rank_at_exact_ceiling_kept():
    """ちょうど3000万は維持（境界は <=）."""
    p = _s_rank_candidate(price=30_000_000)
    scored = score_property(p)
    assert scored.dealRank == "S"


def test_apply_s_rank_guard_signature_includes_price():
    """guard が price 引数を受け取れる。None もOK."""
    rank = apply_s_rank_guard(
        rank="S", score=85,
        location_score=30, loan_score=20, yield_score=20,
        remaining_years=32,
        price=20_000_000,
    )
    assert rank == "S"


def test_apply_s_rank_guard_demotes_on_high_price():
    rank = apply_s_rank_guard(
        rank="S", score=85,
        location_score=30, loan_score=20, yield_score=20,
        remaining_years=32,
        price=50_000_000,
    )
    assert rank == "A"


def test_apply_s_rank_guard_price_none_does_not_demote():
    rank = apply_s_rank_guard(
        rank="S", score=85,
        location_score=30, loan_score=20, yield_score=20,
        remaining_years=32,
        price=None,
    )
    assert rank == "S"


def test_apply_s_rank_guard_price_check_does_not_apply_to_a_rank():
    """A以下では price ガードは効かない（Sランク専用）."""
    rank = apply_s_rank_guard(
        rank="A", score=70,
        location_score=15, loan_score=10, yield_score=15,
        remaining_years=15,
        price=80_000_000,
    )
    assert rank == "A"


# ===== v2.2: compositeRankValue タイブレーカー ==============================

def test_composite_rank_value_assigned():
    p = _s_rank_candidate()
    scored = score_property(p)
    assert scored.compositeRankValue is not None
    assert scored.compositeRankValue >= scored.dealScore


def test_composite_rank_value_sub_one_addition():
    """サブスコアの加算は <1.0（ランク境界を絶対跨がない）."""
    p = _s_rank_candidate()
    scored = score_property(p)
    addition = scored.compositeRankValue - scored.dealScore
    assert 0 <= addition < 1.0


def test_composite_rank_value_breaks_ties():
    """dealScore同じ・locationScore違いで順位確定."""
    p_high = _s_rank_candidate(
        pid_override="p-high",
        nearestStation="地下鉄空港線「赤坂」徒歩4分",  # location 高め
    )
    p_low = Property(
        id="p-low", name="物件",
        prefecture="福岡県",
        city="福岡市博多区",  # 同じSグレード
        propertyType="区分マンション",
        layout="2LDK",
        structure="RC造",
        age=8,
        yieldGross=7.5,
        price=28_000_000,
        area=45.0,
        nearestStation="ＪＲ鹿児島本線「博多」徒歩12分",  # 駅徒歩で立地差を作る
    )
    s_high = score_property(p_high)
    s_low = score_property(p_low)
    # 同じdealScoreでも、locationScoreが大きい方が compositeRankValue でリード
    if s_high.dealScore == s_low.dealScore:
        assert s_high.compositeRankValue > s_low.compositeRankValue


def test_composite_rank_value_max_addition_bounded():
    """最大加算 = 30*0.001 + 20*0.0001 + 30*0.00001 + 100*0.000001 = 0.03251."""
    max_add = 30 * 0.001 + 20 * 0.0001 + 30 * 0.00001 + 100 * 0.000001
    assert max_add < 0.04
    # よって100点満点の物件でも 100.04 未満 → Property の上限101に余裕で収まる


def test_composite_rank_value_none_when_na():
    """N/A 物件は compositeRankValue も None."""
    p = Property(
        id="x", name="物件",
        city="",
        propertyType="区分マンション",
        age=10,
    )
    scored = score_property(p)
    assert scored.dealRank == "N/A"
    assert scored.compositeRankValue is None


# ===== v2.3: structureEstimated Sランクガード ===============================

def test_apply_s_rank_guard_demotes_when_structure_estimated():
    """構造推定（structureEstimated=True）のSランク候補は A 降格."""
    rank = apply_s_rank_guard(
        rank="S", score=85,
        location_score=30, loan_score=20, yield_score=20,
        remaining_years=32, price=20_000_000,
        structure_estimated=True,
        property_type="区分マンション",
    )
    assert rank == "A"


def test_apply_s_rank_guard_keeps_when_structure_confirmed():
    """構造確定済み（structureEstimated=False）はSランク維持."""
    rank = apply_s_rank_guard(
        rank="S", score=85,
        location_score=30, loan_score=20, yield_score=20,
        remaining_years=32, price=20_000_000,
        structure_estimated=False,
        property_type="区分マンション",
    )
    assert rank == "S"


def test_score_property_structureEstimated_demoted_in_full_pipeline():
    """structure="" + マンション → 推定True → S候補でも A 降格."""
    p = Property(
        id="x", name="物件",
        prefecture="福岡県",
        city="福岡市中央区",
        propertyType="区分マンション",
        layout="2LDK",
        structure="",  # 構造欠損 → 推定 RC
        age=8,
        yieldGross=7.5,
        price=28_000_000,
        area=45.0,
        nearestStation="地下鉄空港線「赤坂」徒歩4分",
    )
    scored = score_property(p)
    assert scored.structureEstimated is True
    # 立地・融資・収益・残存・priceすべて条件クリアでも構造推定で A 降格
    assert scored.dealRank == "A"


# ===== v2.3: propertyType-aware modifiers ===================================

def test_apply_s_rank_guard_apartment_strict_yield_threshold():
    """一棟売りアパートは yield_score < 20 で A 降格（区分は15で通る）."""
    rank = apply_s_rank_guard(
        rank="S", score=85,
        location_score=30, loan_score=20,
        yield_score=18,  # 区分なら通るが一棟は厳しめ
        remaining_years=32, price=20_000_000,
        structure_estimated=False,
        property_type="一棟売りアパート",
    )
    assert rank == "A"


def test_apply_s_rank_guard_apartment_passes_at_high_yield():
    """一棟アパートでも yield 20以上ならS維持."""
    rank = apply_s_rank_guard(
        rank="S", score=85,
        location_score=30, loan_score=15,
        yield_score=20,
        remaining_years=32, price=20_000_000,
        structure_estimated=False,
        property_type="一棟売りアパート",
    )
    assert rank == "S"


def test_apply_s_rank_guard_mansion_default_threshold():
    """区分マンションは yield_score >= 15 で通る（デフォルト）."""
    rank = apply_s_rank_guard(
        rank="S", score=85,
        location_score=30, loan_score=20,
        yield_score=15,  # default min
        remaining_years=32, price=20_000_000,
        structure_estimated=False,
        property_type="区分マンション",
    )
    assert rank == "S"


def test_apply_s_rank_guard_one_block_mansion_strict_loan():
    """一棟売りマンションは loan_score < 15 で A 降格."""
    rank = apply_s_rank_guard(
        rank="S", score=85,
        location_score=30, loan_score=10,  # 区分なら通るが一棟は15必要
        yield_score=20,
        remaining_years=32, price=20_000_000,
        structure_estimated=False,
        property_type="一棟売りマンション",
    )
    assert rank == "A"


def test_apply_s_rank_guard_default_property_type_uses_thresholds():
    """propertyType=None または未登録は default thresholds を使う."""
    rank = apply_s_rank_guard(
        rank="S", score=85,
        location_score=30, loan_score=10, yield_score=15,
        remaining_years=32, price=20_000_000,
        structure_estimated=False,
        property_type=None,
    )
    assert rank == "S"


def test_apply_s_rank_guard_unknown_property_type_uses_thresholds():
    """未登録 propertyType は default."""
    rank = apply_s_rank_guard(
        rank="S", score=85,
        location_score=30, loan_score=10, yield_score=15,
        remaining_years=32, price=20_000_000,
        structure_estimated=False,
        property_type="売り店舗・事務所",
    )
    assert rank == "S"


# ===== v2.3: dealModelVersion bump =========================================

def test_score_property_sets_v25_model_version():
    p = _s_rank_candidate()
    scored = score_property(p)
    assert scored.dealModelVersion == "v2.5"


# ===== v2.5: medians 書き戻し（BUG FIX） ===================================

def test_yieldMedianInArea_populated_after_scoring():
    """score_property に medians 辞書を渡すと yieldMedianInArea / pricePerSqmMedian / yieldDeviation が書き戻される."""
    from services.medians import compute_medians

    # 福岡市博多区・区分マンション の母集団を fallback_min(=8相当) を超える件数作る
    # configを尊重しつつ、十分に大きい数で安全にテスト
    population = []
    for i in range(20):
        population.append(
            Property(
                id=f"pop-{i}",
                name=f"博多区物件{i}",
                prefecture="福岡県",
                city="福岡市博多区",
                propertyType="区分マンション",
                layout="2LDK",
                structure="RC",
                age=10,
                yieldGross=6.0 + (i * 0.05),  # 6.0 - 6.95 → median ~ 6.475
                price=20_000_000 + i * 100_000,
                area=40.0,
                nearestStation="ＪＲ鹿児島本線「博多」徒歩10分",
            )
        )
    medians = compute_medians(population)
    target = population[0]
    target.yieldGross = 7.5  # 中央値より高い → yieldDeviation > 0

    score_property(target, medians=medians)

    assert target.yieldMedianInArea is not None
    assert target.pricePerSqmMedian is not None
    # yieldGross 7.5 が中央値より高ければ deviation > 0
    assert target.yieldDeviation is not None
    assert target.yieldDeviation > 0


def test_yieldMedianInArea_none_when_no_medians_dict():
    """medians 引数なしの後方互換: フィールドはNoneのまま."""
    p = _s_rank_candidate()
    score_property(p)  # medians 引数なし
    assert p.yieldMedianInArea is None
    assert p.pricePerSqmMedian is None
    assert p.yieldDeviation is None


def test_yieldMedianInArea_falls_back_to_benchmark_when_population_missing():
    """v2.5: 対象物件のグループキーに medians がない場合、benchmarkCapRate でフォールバック."""
    from services.medians import compute_medians

    # 母集団は別エリア・別タイプ → 北九州区分マンションは medians に含まれない
    population = [
        Property(
            id=f"pop-{i}", name="x",
            prefecture="福岡県", city="福岡市中央区",
            propertyType="一棟売りアパート", layout="1K",
            structure="木造", age=10, yieldGross=5.5,
            price=20_000_000, area=30.0,
        )
        for i in range(20)
    ]
    medians = compute_medians(population)
    # 北九州物件は medians に含まれないが Cap Rate ベンチマーク (10.0%) でフォールバック
    target = Property(
        id="t1", name="x",
        prefecture="福岡県", city="北九州市八幡西区",
        propertyType="区分マンション", layout="1K",
        structure="RC", age=10, yieldGross=8.0,
        price=10_000_000, area=30.0,
    )
    score_property(target, medians=medians)
    # フォールバックで benchmark = 10.0 が入る（北九州 Cap Rate）
    assert target.yieldMedianInArea == 10.0
    assert target.yieldDeviation is not None
    assert target.yieldDeviation < 0  # 8.0 < 10.0


# ===== v2.5 B案: yieldEstimated 対応 =======================================

def test_calc_yield_score_uses_estimated_when_actual_missing():
    """yieldGross=None でも yieldEstimated があればその値で score を計算する."""
    p = Property(
        id="x", name="n",
        city="福岡市博多区", layout="2LDK",
        yieldGross=None,
    )
    # ベンチマーク 6.0 に対し 7.2 → +20% 乖離 → 通常上限 30 だが estimated で 15 にクリップ
    p.yieldEstimated = 7.2
    p.yieldSourceConfidence = "median"
    score = calc_yield_benchmark_score(p)
    assert score is not None
    # estimated の最大 = cap/2 = 15
    assert score == 15


def test_calc_yield_score_clips_to_half_for_estimated():
    """estimated 扱いでは cap 30 → 15 にクリップされ、actual との差分が保たれる."""
    # 同じ利回り値でも actual と estimated で点数が異なることを確認
    p_actual = Property(
        id="x1", name="n",
        city="福岡市博多区", layout="2LDK",
        yieldGross=20.0,  # 爆発的な高利回り → actual なら 30点満点
    )
    score_actual = calc_yield_benchmark_score(p_actual)
    assert score_actual == 30

    p_est = Property(
        id="x2", name="n",
        city="福岡市博多区", layout="2LDK",
        yieldGross=None,
    )
    p_est.yieldEstimated = 20.0
    p_est.yieldSourceConfidence = "median"
    score_est = calc_yield_benchmark_score(p_est)
    # estimated は半分にクリップ
    assert score_est == 15


def test_calc_yield_score_estimated_fallback_confidence_also_clipped():
    """信頼度 'fallback' でも同様に半分クリップ."""
    p = Property(
        id="x", name="n",
        city="福岡市博多区", layout="2LDK",
        yieldGross=None,
    )
    p.yieldEstimated = 7.2
    p.yieldSourceConfidence = "fallback"
    score = calc_yield_benchmark_score(p)
    assert score == 15


def test_calc_yield_score_still_none_when_both_missing():
    """yieldGross / yieldEstimated 両方 None なら従来どおり None."""
    p = Property(
        id="x", name="n",
        city="福岡市博多区", layout="2LDK",
        yieldGross=None,
    )
    assert calc_yield_benchmark_score(p) is None


def test_yieldDeviation_negative_when_below_median():
    """yieldGross が中央値を下回るとき yieldDeviation < 0."""
    from services.medians import compute_medians

    population = []
    for i in range(20):
        population.append(
            Property(
                id=f"pop-{i}", name="x",
                prefecture="福岡県", city="福岡市博多区",
                propertyType="区分マンション", layout="2LDK",
                structure="RC", age=10,
                yieldGross=8.0,  # 全件 8.0 → median 8.0
                price=20_000_000, area=40.0,
            )
        )
    medians = compute_medians(population)
    target = population[0]
    target.yieldGross = 6.0  # 中央値より低い

    score_property(target, medians=medians)
    assert target.yieldMedianInArea == 8.0
    assert target.yieldDeviation is not None
    assert target.yieldDeviation < 0


