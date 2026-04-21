"""Tests for services/persona_matcher.py — 投資家ペルソナ × 物件マッチング (v2.6).

Source: company/research/topics/2026-04-19-real-estate-investor-personas-brief.md §5
Persona IDs: income / loan_strategy / capital_gain / location / renovation
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Property  # noqa: E402
from services.persona_matcher import match_personas  # noqa: E402


# ===== ヘルパー: ペルソナ MUST を満たす物件のテンプレ ====================

def _make_income_property(**overrides) -> Property:
    """インカム型 MUST 全クリア物件のテンプレ（actual yield + locationGrade B）."""
    base = dict(
        id="inc-1",
        name="インカム型MUSTクリア物件",
        prefecture="福岡県",
        city="福岡市南区",
        propertyType="区分マンション",
        layout="2LDK",
        structure="RC",
        age=15,
        builtYear=2011,
        yieldGross=7.0,
        yieldEstimated=7.0,
        yieldSourceConfidence="actual",
        yieldMedianInArea=6.0,  # MUST: yield(7) >= median(6)
        price=15_000_000,
        area=40.0,
        nearestStation="福岡市地下鉄空港線「博多」徒歩9分",
        # score_property 由来フィールドを直書き（unit test のため）
        locationGrade="A",
        lineRank="S",
        walkMinutes=9,
        remainingDurableYears=32,  # RC47 - 15
        hazardFlag="low",
        inRedevelopmentZone=False,
        benchmarkCapRate=6.0,
        yieldDeviation=16.7,  # (7-6)/6 *100
    )
    base.update(overrides)
    return Property(**base)


def _make_loan_strategy_property(**overrides) -> Property:
    base = dict(
        id="loan-1",
        name="融資戦略型MUST物件",
        prefecture="福岡県",
        city="福岡市博多区",
        propertyType="一棟売りマンション",
        layout="2LDK",
        structure="RC",   # MUST: RC/SRC/S系
        age=10,
        builtYear=2016,    # MUST: 1982以降（修正4）
        yieldGross=6.5,
        yieldEstimated=6.5,
        yieldSourceConfidence="actual",
        price=50_000_000,  # MUST: 3000万以上
        area=200.0,
        nearestStation="JR鹿児島本線「博多」徒歩7分",
        locationGrade="S",
        lineRank="A",
        walkMinutes=7,
        remainingDurableYears=37,  # MUST: 15以上
        hazardFlag="low",
        inRedevelopmentZone=True,
        benchmarkCapRate=6.0,
    )
    base.update(overrides)
    return Property(**base)


def _make_capital_gain_property(**overrides) -> Property:
    base = dict(
        id="cap-1",
        name="キャピタル型MUST物件",
        prefecture="福岡県",
        city="福岡市中央区",
        propertyType="区分マンション",
        layout="2LDK",
        structure="RC",
        age=10,
        builtYear=2016,
        yieldGross=5.5,
        yieldEstimated=5.5,
        yieldSourceConfidence="actual",
        price=27_000_000,  # 30,000,000 * 0.9 (中央値より-10%) on 50m²×600,000
        area=50.0,
        pricePerSqmMedian=600_000.0,  # 50 * 600k = 30M; price 27M = -10%
        nearestStation="福岡市地下鉄空港線「天神」徒歩5分",
        locationGrade="S",
        lineRank="S",
        walkMinutes=5,
        remainingDurableYears=37,
        hazardFlag="low",
        inRedevelopmentZone=True,
        benchmarkCapRate=5.25,
    )
    base.update(overrides)
    return Property(**base)


def _make_location_property(**overrides) -> Property:
    base = dict(
        id="loc-1",
        name="立地特化型MUST物件",
        prefecture="福岡県",
        city="福岡市中央区",
        propertyType="区分マンション",
        layout="1K",
        structure="RC",
        age=12,
        builtYear=2014,
        yieldGross=4.5,  # MUST: 4.0以上
        yieldEstimated=4.5,
        yieldSourceConfidence="actual",
        price=18_000_000,
        area=25.0,
        nearestStation="福岡市地下鉄空港線「天神」徒歩4分",
        locationGrade="S",   # MUST: A以上
        lineRank="S",        # MUST: A以上
        walkMinutes=4,       # MUST: 10以内
        remainingDurableYears=35,
        hazardFlag="low",
        inRedevelopmentZone=True,
        benchmarkCapRate=5.25,
    )
    base.update(overrides)
    return Property(**base)


def _make_renovation_property(**overrides) -> Property:
    base = dict(
        id="ren-1",
        name="築古再生型MUST物件",
        prefecture="福岡県",
        city="福岡市西区",      # MUST: 福岡市内
        propertyType="戸建賃貸",  # MUST: 戸建系
        layout="3LDK",
        structure="木造",
        age=30,
        builtYear=1996,        # MUST: 1982以降（修正4）
        price=8_000_000,       # MUST: 1000万以下
        area=70.0,
        yieldGross=None,
        yieldEstimated=None,
        yieldSourceConfidence="none",
        nearestStation="JR筑肥線「下山門」徒歩12分",
        locationGrade="B",
        lineRank="C",
        walkMinutes=12,
        remainingDurableYears=-8,
        hazardFlag="medium",
        inRedevelopmentZone=False,
        benchmarkCapRate=6.0,
    )
    base.update(overrides)
    return Property(**base)


# ===== 1. 各ペルソナ MUST 全クリア → True ===============================

def test_income_must_all_pass_returns_match():
    """インカム型: MUST 全クリアで matches に含まれる."""
    p = _make_income_property()
    matches, stars = match_personas(p)
    assert "income" in matches
    assert stars["income"] >= 3


def test_loan_strategy_must_all_pass_returns_match():
    """融資戦略型: MUST 全クリアで matches に含まれる."""
    p = _make_loan_strategy_property()
    matches, stars = match_personas(p)
    assert "loan_strategy" in matches
    assert stars["loan_strategy"] >= 3


def test_capital_gain_must_all_pass_returns_match():
    """キャピタル型: MUST 全クリアで matches に含まれる."""
    p = _make_capital_gain_property()
    matches, stars = match_personas(p)
    assert "capital_gain" in matches
    assert stars["capital_gain"] >= 3


def test_location_must_all_pass_returns_match():
    """立地特化型: MUST 全クリアで matches に含まれる."""
    p = _make_location_property()
    matches, stars = match_personas(p)
    assert "location" in matches
    assert stars["location"] >= 3


def test_renovation_must_all_pass_returns_match():
    """築古再生型: MUST 全クリアで matches に含まれる."""
    p = _make_renovation_property()
    matches, stars = match_personas(p)
    assert "renovation" in matches
    assert stars["renovation"] >= 3


# ===== 2. 各ペルソナの NEVER 該当 → False ===============================

def test_income_never_yield_below_threshold_excluded():
    """インカム型: yield < 5.0% (MUST 違反) は不一致."""
    p = _make_income_property(yieldGross=4.5, yieldEstimated=4.5,
                              yieldDeviation=-25.0)
    matches, _ = match_personas(p)
    assert "income" not in matches


def test_loan_strategy_never_old_pre_1982_excluded():
    """融資戦略型: 旧耐震 (1982 未満) は NEVER で不一致.

    修正4: 1981/1-5月築の旧耐震を確実に排除するため min_built_year_hard=1982 に bump.
    """
    p = _make_loan_strategy_property(builtYear=1980, age=46,
                                     remainingDurableYears=1)
    matches, _ = match_personas(p)
    assert "loan_strategy" not in matches


def test_loan_strategy_never_built_1981_excluded_after_bump():
    """修正4: builtYear=1981 は 1982 へのbumpにより新たに NEVER 該当となる."""
    p = _make_loan_strategy_property(builtYear=1981, age=45,
                                     remainingDurableYears=2)
    matches, _ = match_personas(p)
    assert "loan_strategy" not in matches


def test_loan_strategy_must_built_1982_passes():
    """修正4: builtYear=1982 (新耐震境界) は MUST 通過."""
    p = _make_loan_strategy_property(builtYear=1982, age=44,
                                     remainingDurableYears=15)
    matches, _ = match_personas(p)
    assert "loan_strategy" in matches


def test_capital_gain_never_kitakyushu_excluded():
    """キャピタル型: 北九州市は forbidden_cities_substring で NEVER."""
    p = _make_capital_gain_property(city="北九州市小倉北区",
                                    locationGrade="C")
    matches, _ = match_personas(p)
    assert "capital_gain" not in matches


def test_location_never_walk_over_15min_excluded():
    """立地特化型: 駅徒歩 16分は NEVER (walk_minutes_hard_max=15)."""
    p = _make_location_property(walkMinutes=16,
                                nearestStation="福岡市地下鉄空港線「天神」徒歩16分")
    matches, _ = match_personas(p)
    assert "location" not in matches


def test_renovation_never_pre_1982_excluded():
    """築古再生型: 旧耐震は出口詰まりで NEVER (修正4: 1982 未満)."""
    p = _make_renovation_property(builtYear=1975, age=51)
    matches, _ = match_personas(p)
    assert "renovation" not in matches


def test_renovation_never_built_1981_excluded_after_bump():
    """修正4: 築古再生型でも 1981 は新たに NEVER。"""
    p = _make_renovation_property(builtYear=1981, age=45)
    matches, _ = match_personas(p)
    assert "renovation" not in matches


# ===== 3. PREFER で stars が 3→4 に上がる =================================

def test_income_prefer_increases_stars():
    """インカム型: PREFER 1個追加で ★3→★4."""
    # ベース（PREFER ヒットを強制ゼロにする条件）
    base = _make_income_property(
        layout="3LDK",         # 単身向けではない（PREFER外）
        age=30,                # 20年超（PREFER外）
        locationGrade="C",     # B未満（PREFER外）
        yieldDeviation=0.0,    # 偏差0（PREFER外）
    )
    _, base_stars = match_personas(base)
    assert "income" in base_stars
    base_score = base_stars["income"]

    # PREFER 1: 単身レイアウトを足す → +1
    bumped = _make_income_property(
        layout="1K",
        age=30,
        locationGrade="C",
        yieldDeviation=0.0,
    )
    _, bumped_stars = match_personas(bumped)
    assert bumped_stars["income"] == base_score + 1


def test_loan_strategy_prefer_increases_stars():
    """融資戦略型: 一棟マンション + 立地S + 残存25年 + 再開発 + 利回り6%以上 で ★5."""
    # ベース: PREFER 全外し
    base = _make_loan_strategy_property(
        propertyType="区分マンション",  # 一棟ではない
        locationGrade="C",
        remainingDurableYears=15,       # 25未満
        inRedevelopmentZone=False,
        yieldGross=5.0, yieldEstimated=5.0,  # 6%未満
    )
    _, base_stars = match_personas(base)
    base_score = base_stars["loan_strategy"]

    # 一棟物件を足す → PREFER 1個 → +1
    bumped = _make_loan_strategy_property(
        propertyType="一棟売りアパート",
        locationGrade="C",
        remainingDurableYears=15,
        inRedevelopmentZone=False,
        yieldGross=5.0, yieldEstimated=5.0,
    )
    _, bumped_stars = match_personas(bumped)
    assert bumped_stars["loan_strategy"] == base_score + 1


def test_capital_gain_prefer_increases_stars():
    """キャピタル型: 駅徒歩10分以内 PREFER で +1."""
    # ベース: PREFER 全外し
    base = _make_capital_gain_property(
        inRedevelopmentZone=False,
        walkMinutes=12,
        lineRank="C",
        firstSeenAt=None,
    )
    _, base_stars = match_personas(base)
    base_score = base_stars["capital_gain"]

    bumped = _make_capital_gain_property(
        inRedevelopmentZone=False,
        walkMinutes=8,         # PREFER: 10分以内 → +1
        lineRank="C",
        firstSeenAt=None,
    )
    _, bumped_stars = match_personas(bumped)
    assert bumped_stars["capital_gain"] == base_score + 1


def test_location_prefer_increases_stars():
    """立地特化型: locationGrade S かつ駅徒歩5分以内で PREFER 加点."""
    # ベース: locationGrade A（PREFER 外）, 徒歩 8分
    base = _make_location_property(
        locationGrade="A",
        walkMinutes=8,
        inRedevelopmentZone=False,
        layout="2LDK",
        hazardFlag="medium",
    )
    _, base_stars = match_personas(base)
    base_score = base_stars["location"]

    # S立地に変える → PREFER 1個追加 → +1
    bumped = _make_location_property(
        locationGrade="S",
        walkMinutes=8,
        inRedevelopmentZone=False,
        layout="2LDK",
        hazardFlag="medium",
    )
    _, bumped_stars = match_personas(bumped)
    assert bumped_stars["location"] == base_score + 1


def test_renovation_prefer_increases_stars():
    """築古再生型: 築 25〜40年 PREFER で +1."""
    # ベース: 築20年（PREFER外）+ 50m²（PREFER外）
    base = _make_renovation_property(
        age=20,
        area=50.0,
        firstSeenAt=None,
    )
    _, base_stars = match_personas(base)
    base_score = base_stars["renovation"]

    # 築 30年 → PREFER 加点
    bumped = _make_renovation_property(
        age=30,
        area=50.0,
        firstSeenAt=None,
    )
    _, bumped_stars = match_personas(bumped)
    assert bumped_stars["renovation"] == base_score + 1


# ===== 4. estimated 物件の信頼度補正 ====================================

def test_estimated_yield_reduces_stars_by_one():
    """estimated 利回り (yieldSourceConfidence != 'actual') 使用時は -1."""
    # actual ベース（PREFER 全部入りで最大になりすぎないよう調整）
    actual = _make_income_property(
        yieldGross=7.0,
        yieldEstimated=7.0,
        yieldSourceConfidence="actual",
        layout="2LDK", age=30, locationGrade="C", yieldDeviation=0.0,  # PREFER 全外し
    )
    _, actual_stars = match_personas(actual)

    # estimated（同 PREFER 構成）
    estimated = _make_income_property(
        yieldGross=None,
        yieldEstimated=7.0,
        yieldSourceConfidence="median",
        layout="2LDK", age=30, locationGrade="C", yieldDeviation=0.0,
    )
    _, est_stars = match_personas(estimated)
    assert est_stars["income"] == actual_stars["income"] - 1
    # 最低 1 を下回らない
    assert est_stars["income"] >= 1


# ===== 5. 複数ペルソナマッチ物件 ======================================

def test_multi_persona_premium_property():
    """中央区RC築10年徒歩5分の優良物件は 3ペルソナ以上にマッチ.

    インカム型 + 立地特化型 + キャピタル型 を同時に満たす条件を作る.
    """
    p = Property(
        id="premium-1",
        name="プレミアム物件",
        prefecture="福岡県",
        city="福岡市中央区",
        propertyType="区分マンション",
        layout="1K",
        structure="RC",
        age=8,
        builtYear=2018,
        yieldGross=6.5,
        yieldEstimated=6.5,
        yieldSourceConfidence="actual",
        yieldMedianInArea=5.25,        # インカム MUST: yield(6.5) >= median(5.25)
        yieldDeviation=23.8,
        price=18_000_000,
        area=30.0,
        pricePerSqmMedian=700_000.0,    # 30 * 700k = 21M; price 18M = -14% → MUST OK
        nearestStation="福岡市地下鉄空港線「赤坂」徒歩4分",
        locationGrade="S",
        lineRank="S",
        walkMinutes=4,
        remainingDurableYears=39,
        hazardFlag="low",
        inRedevelopmentZone=True,
        benchmarkCapRate=5.25,
    )
    matches, stars = match_personas(p)
    # インカム型・立地特化型・キャピタル型のいずれも MUST クリア
    assert "income" in matches
    assert "location" in matches
    assert "capital_gain" in matches
    # プレミアム物件は 3 ペルソナ以上にマッチ
    assert len(matches) >= 3


# ===== 6. 全ペルソナ外れ物件 ============================================

def test_all_personas_unmatch():
    """利回り無し・郊外・築古・小型の物件は全ペルソナ外れ."""
    p = Property(
        id="unmatched-1",
        name="全ペルソナ外れ物件",
        prefecture="福岡県",
        city="北九州市八幡西区",
        propertyType="区分マンション",
        layout="3DK",
        structure="木造",
        age=42,
        builtYear=1984,
        yieldGross=None,
        yieldEstimated=None,
        yieldSourceConfidence="none",
        price=2_500_000,
        area=45.0,
        nearestStation="JR鹿児島本線「黒崎」徒歩18分",
        locationGrade="C",
        lineRank="A",
        walkMinutes=18,
        remainingDurableYears=-20,
        hazardFlag="low",
    )
    matches, stars = match_personas(p)
    assert matches == []
    assert stars == {}


# ===== 7. 修正2: location ペルソナ MUST から yield 撤廃 =====================

def test_location_must_passes_without_yield():
    """修正2: 立地特化型は yield 値が無くても MUST 通過する.

    ブリーフ §5.4「利回りより立地。利回り妥協」と整合させるため、
    旧 MUST「yield ≥ 4%」を撤廃した（PREFER 加点に格下げ）.
    """
    p = _make_location_property(
        yieldGross=None,
        yieldEstimated=None,
        yieldSourceConfidence="none",
    )
    matches, stars = match_personas(p)
    assert "location" in matches
    assert stars["location"] >= 3


def test_location_must_passes_with_low_yield():
    """修正2: 立地特化型は yield < 4% でも MUST 通過する."""
    p = _make_location_property(
        yieldGross=2.5,
        yieldEstimated=2.5,
        yieldSourceConfidence="actual",
    )
    matches, _ = match_personas(p)
    assert "location" in matches


def test_location_prefer_yield_bonus_actual_increases_stars():
    """修正2: yield ≥ 4% (actual) で PREFER bonus_yield_min が +1 加点される."""
    base = _make_location_property(
        locationGrade="A",       # PREFER S 外し
        walkMinutes=8,           # PREFER 5分外し
        inRedevelopmentZone=False,
        layout="2LDK",           # 単身外し
        hazardFlag="medium",     # ハザード low 外し
        yieldGross=2.0, yieldEstimated=2.0, yieldSourceConfidence="actual",
    )
    _, base_stars = match_personas(base)
    base_score = base_stars["location"]

    bumped = _make_location_property(
        locationGrade="A", walkMinutes=8, inRedevelopmentZone=False,
        layout="2LDK", hazardFlag="medium",
        yieldGross=4.5, yieldEstimated=4.5, yieldSourceConfidence="actual",
    )
    _, bumped_stars = match_personas(bumped)
    assert bumped_stars["location"] == base_score + 1


def test_location_prefer_yield_bonus_estimated_net_zero():
    """修正2: estimated 値で yield bonus が発火しても、estimated 補正 -1 で net 0."""
    # base: yield 2% actual (bonus 不発火)
    base = _make_location_property(
        locationGrade="A", walkMinutes=8, inRedevelopmentZone=False,
        layout="2LDK", hazardFlag="medium",
        yieldGross=2.0, yieldEstimated=2.0, yieldSourceConfidence="actual",
    )
    _, base_stars = match_personas(base)
    base_score = base_stars["location"]

    # bumped: yield 4.5% (estimated, median由来) → +1 bonus, -1 estimated penalty = 差0
    bumped = _make_location_property(
        locationGrade="A", walkMinutes=8, inRedevelopmentZone=False,
        layout="2LDK", hazardFlag="medium",
        yieldGross=None, yieldEstimated=4.5, yieldSourceConfidence="median",
    )
    _, bumped_stars = match_personas(bumped)
    assert bumped_stars["location"] == base_score


# ===== 8. 修正3: structureEstimated 補正 (loan_strategy のみ) ===============

def test_loan_strategy_estimated_structure_reduces_stars_by_one():
    """修正3: structureEstimated=True なら loan_strategy の stars が -1.

    yieldEstimated 補正 (既存) と同じパターンで、構造推定物件は
    銀行融資の基礎資料として弱いため信頼度を下げる。
    """
    base = _make_loan_strategy_property(
        # PREFER 1個ヒット (一棟物件のまま) で base ★4 を確保（★1 床に達しないように）
        propertyType="一棟売りマンション",
        locationGrade="C",
        remainingDurableYears=15,
        inRedevelopmentZone=False,
        yieldGross=5.0, yieldEstimated=5.0,
        structureEstimated=False,
    )
    _, base_stars = match_personas(base)
    assert "loan_strategy" in base_stars
    base_score = base_stars["loan_strategy"]

    bumped = _make_loan_strategy_property(
        propertyType="一棟売りマンション",
        locationGrade="C",
        remainingDurableYears=15,
        inRedevelopmentZone=False,
        yieldGross=5.0, yieldEstimated=5.0,
        structureEstimated=True,   # 構造推定 → -1
    )
    _, bumped_stars = match_personas(bumped)
    assert bumped_stars["loan_strategy"] == base_score - 1
    assert bumped_stars["loan_strategy"] >= 1  # 最低 1


def test_estimated_structure_does_not_affect_renovation():
    """修正B (v2.6.2): structureEstimated 補正は renovation 以外に適用。

    renovation は structure を判定に使わないため補正対象外。
    income/capital_gain/location/loan_strategy は対象で -1 が入る。
    """
    base = _make_renovation_property(
        age=30, area=50.0, firstSeenAt=None,
        structureEstimated=False,
    )
    _, base_stars = match_personas(base)

    bumped = _make_renovation_property(
        age=30, area=50.0, firstSeenAt=None,
        structureEstimated=True,
    )
    _, bumped_stars = match_personas(bumped)
    assert bumped_stars["renovation"] == base_stars["renovation"]


def test_income_estimated_structure_reduces_stars_by_one():
    """修正B (v2.6.2): income で structureEstimated=True なら -1."""
    base = _make_income_property(
        # PREFER 全外しで base ★3、structureEstimated=True なら ★2
        layout="3LDK", age=30, locationGrade="C", yieldDeviation=0.0,
        structureEstimated=False,
    )
    _, base_stars = match_personas(base)

    bumped = _make_income_property(
        layout="3LDK", age=30, locationGrade="C", yieldDeviation=0.0,
        structureEstimated=True,
    )
    _, bumped_stars = match_personas(bumped)
    assert bumped_stars["income"] == base_stars["income"] - 1
    assert bumped_stars["income"] >= 1


def test_capital_gain_estimated_structure_reduces_stars_by_one():
    """修正B (v2.6.2): capital_gain で structureEstimated=True なら -1."""
    base = _make_capital_gain_property(
        # PREFER 1個だけ（walkMinutes=5）で base ★4、structureEstimated=True で ★3
        inRedevelopmentZone=False, lineRank="C", firstSeenAt=None,
        walkMinutes=5,
        structureEstimated=False,
    )
    _, base_stars = match_personas(base)

    bumped = _make_capital_gain_property(
        inRedevelopmentZone=False, lineRank="C", firstSeenAt=None,
        walkMinutes=5,
        structureEstimated=True,
    )
    _, bumped_stars = match_personas(bumped)
    assert bumped_stars["capital_gain"] == base_stars["capital_gain"] - 1
    assert bumped_stars["capital_gain"] >= 1


def test_location_estimated_structure_reduces_stars_by_one():
    """修正B (v2.6.2): location で structureEstimated=True なら -1."""
    base = _make_location_property(
        # PREFER 全外しで base ★3、structureEstimated=True で ★2
        locationGrade="A", walkMinutes=8, inRedevelopmentZone=False,
        layout="2LDK", hazardFlag="medium",
        yieldGross=None, yieldEstimated=None, yieldSourceConfidence="none",
        structureEstimated=False,
    )
    _, base_stars = match_personas(base)

    bumped = _make_location_property(
        locationGrade="A", walkMinutes=8, inRedevelopmentZone=False,
        layout="2LDK", hazardFlag="medium",
        yieldGross=None, yieldEstimated=None, yieldSourceConfidence="none",
        structureEstimated=True,
    )
    _, bumped_stars = match_personas(bumped)
    assert bumped_stars["location"] == base_stars["location"] - 1
    assert bumped_stars["location"] >= 1


def test_renovation_not_in_structure_penalty_whitelist():
    """修正B (v2.6.2): YAML ホワイトリストに renovation が含まれていないことを確認."""
    from services.persona_matcher import get_persona_config, reset_persona_cache
    reset_persona_cache()
    cfg = get_persona_config()
    whitelist = cfg["common"].get("estimated_structure_penalty_personas") or []
    assert "renovation" not in whitelist
    # 他の4ペルソナは含まれる
    for pid in ("loan_strategy", "income", "capital_gain", "location"):
        assert pid in whitelist


def test_loan_strategy_estimated_structure_floor_at_one():
    """修正3: structureEstimated 補正でも min_stars=1 を下回らない."""
    # PREFER 全外しで base ★3 になる構成、estimated 利回りで -1 ★2、structure -1 で ★1 床
    p = _make_loan_strategy_property(
        propertyType="区分マンション",      # 一棟外し
        locationGrade="C",                  # B未満
        remainingDurableYears=15,           # 25未満
        inRedevelopmentZone=False,
        # PREFER min_yield_for_prefer (6.0) を下回り、かつ estimated
        yieldGross=None,
        yieldEstimated=5.0,
        yieldSourceConfidence="median",
        structureEstimated=True,
    )
    _, stars = match_personas(p)
    assert "loan_strategy" in stars
    assert stars["loan_strategy"] >= 1
