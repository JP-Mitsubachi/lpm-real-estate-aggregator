"""Investor persona matching (v2.6.2, PGE Round 3 修正反映).

Source: company/research/topics/2026-04-19-real-estate-investor-personas-brief.md §5
Config: config/personas.yaml

5 ペルソナ:
    income          インカムゲイン重視型
    loan_strategy   融資戦略型
    capital_gain    キャピタルゲイン型
    location        立地特化型
    renovation      築古再生型

ブリーフ §5.6（節税・団信型）はドメインエキスパート判断により除外。
理由: 「カモ化」批判が業界共通で、てるさんの「営業利益率1%向上」哲学と倫理整合しない。

公開API:
    match_personas(p) -> (matches, stars)
        matches: list[str]   ペルソナID（MUST 全クリアした順）
        stars:   dict[str,int]  ペルソナID → 1〜5

仕様:
    - MUST 全クリア → 基本 ★3
    - PREFER 加点 1個ごとに +1（最大 ★5）
    - estimated 利回り使用時 (yieldSourceConfidence != "actual") は -1（最低 ★1）
    - structureEstimated=True 物件は common.estimated_structure_penalty_personas に
      含まれるペルソナのみ追加 -1（v2.6.2 で income/capital_gain/location へ展開済み、
      最低 ★1）
    - NEVER 該当があれば一律マッチ無し

Round 2 修正:
    - 修正2: location MUST から min_yield 撤廃。PREFER bonus_yield_min に格下げ。
    - 修正3: loan_strategy で structureEstimated=True なら personaStars -1。
    - 修正4: min_built_year 1981→1982（YAML 側）。コード側は YAML の値をそのまま使用。

Round 3 (v2.6.2) 修正:
    - 修正A: renovation.must.allowed_property_types に命名揺れ追加（YAML 側のみ）。
    - 修正B: estimated_structure_penalty_personas を income/capital_gain/location
              へ展開（YAML 側のみ、コード側はホワイトリスト参照で動的追従）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml

from models import Property


# ===== Config loader =========================================================

DEFAULT_PERSONA_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "personas.yaml"
)


class PersonaConfigError(Exception):
    """Raised when personas.yaml is missing or malformed."""


@lru_cache(maxsize=1)
def get_persona_config() -> dict:
    """Load personas.yaml once (cached)."""
    if not DEFAULT_PERSONA_PATH.exists():
        raise PersonaConfigError(
            f"personas config not found: {DEFAULT_PERSONA_PATH}"
        )
    with DEFAULT_PERSONA_PATH.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if not isinstance(cfg, dict):
        raise PersonaConfigError("personas.yaml must be a mapping")
    for key in ("income", "loan_strategy", "capital_gain", "location",
                "renovation", "common"):
        if key not in cfg:
            raise PersonaConfigError(f"personas.yaml missing section: {key}")
    return cfg


def reset_persona_cache() -> None:
    """Reset cached config (for tests)."""
    get_persona_config.cache_clear()


# ===== Helpers ===============================================================

_GRADE_RANK = {"S": 4, "A": 3, "B": 2, "C": 1, "D": 0}
_LINE_RANK_ORDER = {"S": 4, "A": 3, "B": 2, "C": 1}


def _grade_at_least(grade: Optional[str], minimum: str) -> bool:
    """locationGrade が minimum 以上か。grade=None は False。"""
    if grade is None:
        return False
    return _GRADE_RANK.get(grade, -1) >= _GRADE_RANK.get(minimum, -1)


def _line_rank_at_least(rank: Optional[str], minimum: str) -> bool:
    if rank is None:
        return False
    return _LINE_RANK_ORDER.get(rank, -1) >= _LINE_RANK_ORDER.get(minimum, -1)


def _city_matches_substring(city: str, substrings: list[str]) -> bool:
    if not city:
        return False
    return any(s in city for s in substrings)


def _is_layout_single(layout: str, keywords: list[str]) -> bool:
    if not layout:
        return False
    return any(kw in layout for kw in keywords)


def _resolve_yield(p: Property) -> tuple[Optional[float], bool]:
    """利回り値と is_actual を返す。

    Returns:
        (value, is_actual):
            value: 利用可能な利回り (% 値)。actual を優先、無ければ estimated。
            is_actual: True なら actual 値（信頼度補正なし）
    """
    if p.yieldGross is not None:
        return p.yieldGross, True
    if p.yieldEstimated is not None:
        is_actual = (p.yieldSourceConfidence == "actual")
        return p.yieldEstimated, is_actual
    return None, False


def _has_price_drop(p: Property) -> bool:
    """priceHistory から値下げが1回以上発生しているか。"""
    history = p.priceHistory or []
    if len(history) < 2:
        return False
    prices = [h.get("price") for h in history if h.get("price") is not None]
    if len(prices) < 2:
        return False
    # いずれかのステップで前回より安くなっていれば True
    for i in range(1, len(prices)):
        if prices[i] < prices[i - 1]:
            return True
    return False


def _days_since_first_seen(p: Property) -> Optional[int]:
    """firstSeenAt からの経過日数。パース不能 / 未設定は None."""
    if not p.firstSeenAt:
        return None
    try:
        # ISO8601。末尾 "Z" 対応
        ts = p.firstSeenAt.rstrip("Z")
        first = datetime.fromisoformat(ts)
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - first).days
    except (ValueError, TypeError):
        return None


def _price_below_median(p: Property, threshold_pct: float) -> bool:
    """price <= pricePerSqmMedian * area * (1 - threshold/100) か。

    pricePerSqmMedian / area / price のいずれかが None なら False。
    """
    if p.pricePerSqmMedian is None or not p.area or not p.price:
        return False
    expected = p.pricePerSqmMedian * p.area
    if expected <= 0:
        return False
    threshold = expected * (1 - threshold_pct / 100.0)
    return p.price <= threshold


# ===== ペルソナ判定: 1. インカムゲイン重視型 ============================

def _match_income(p: Property, cfg: dict) -> tuple[bool, int, bool]:
    """Returns (matched, prefer_count, used_estimated)."""
    spec = cfg["income"]
    must = spec["must"]
    never = spec["never"]

    # NEVER: 利回り評価不能
    yld, is_actual = _resolve_yield(p)
    if must.get("require_yield_above_area_median"):
        pass  # MUST 側で判定
    if never.get("require_yield_value") and yld is None:
        return False, 0, False
    # NEVER: 立地D
    if p.locationGrade in (never.get("forbidden_location_grade") or []):
        return False, 0, False
    # NEVER: ハザード high
    if p.hazardFlag in (never.get("forbidden_hazard_flag") or []):
        return False, 0, False

    # MUST 1: 利回り基準（actual or estimated >= min_yield）
    if yld is None or yld < float(must["min_yield"]):
        return False, 0, False
    # MUST 2: エリア中央値以上
    if must.get("require_yield_above_area_median"):
        if p.yieldMedianInArea is None:
            return False, 0, False
        if yld < p.yieldMedianInArea:
            return False, 0, False
    # MUST 3: 残存耐用年数
    if (p.remainingDurableYears is None
            or p.remainingDurableYears < int(must["min_remaining_years"])):
        return False, 0, False
    # MUST 4: 物件種別
    if p.propertyType not in must["allowed_property_types"]:
        return False, 0, False

    # ----- PREFER -----
    prefer_count = 0
    pref = spec["prefer"]
    # PREFER 1: yieldDeviation >= +1%
    if (p.yieldDeviation is not None
            and p.yieldDeviation >= float(pref["yield_deviation_min_pct"])):
        prefer_count += 1
    # PREFER 2: 立地グレード B以上
    if _grade_at_least(p.locationGrade, str(pref["min_location_grade"])):
        prefer_count += 1
    # PREFER 3: 単身向け間取り
    if _is_layout_single(p.layout or "", pref["single_layouts"]):
        prefer_count += 1
    # PREFER 4: 築20年以内
    if p.age is not None and p.age <= int(pref["max_age"]):
        prefer_count += 1

    used_estimated = not is_actual  # estimated 利回り使用フラグ
    return True, prefer_count, used_estimated


# ===== ペルソナ判定: 2. 融資戦略型 ====================================

def _match_loan_strategy(p: Property, cfg: dict) -> tuple[bool, int, bool]:
    spec = cfg["loan_strategy"]
    must = spec["must"]
    never = spec["never"]

    # NEVER: 残存5年未満
    if (p.remainingDurableYears is not None
            and p.remainingDurableYears < int(never["min_remaining_years_hard"])):
        return False, 0, False
    # NEVER: 旧耐震
    if (p.builtYear is not None
            and p.builtYear < int(never["min_built_year_hard"])):
        return False, 0, False
    # NEVER: 木造×築15年超
    fow = never.get("forbidden_old_wood") or {}
    if (fow and p.structure in (fow.get("structures") or [])
            and p.age is not None and p.age > int(fow.get("min_age", 0))):
        return False, 0, False

    # MUST 1: 構造（推定でも可。structureEstimated は信頼度低）
    # ※ estimate_structure は scoring.py で正規化済みだが、Property.structure は
    #   生文字列のまま入っているので alias 経由で判定するため、
    #   scoring.estimate_structure を再利用する
    from services.scoring import estimate_structure as _est_structure
    structure_key, _struct_est = _est_structure(p)
    if structure_key not in must["allowed_structures"]:
        return False, 0, False
    # MUST 2: 残存耐用年数
    if (p.remainingDurableYears is None
            or p.remainingDurableYears < int(must["min_remaining_years"])):
        return False, 0, False
    # MUST 3: 政令市内
    city = p.city or ""
    if not _city_matches_substring(city, must["required_cities"]):
        return False, 0, False
    # MUST 4: 新耐震
    if p.builtYear is None or p.builtYear < int(must["min_built_year"]):
        return False, 0, False
    # MUST 5: 価格下限
    if p.price is None or p.price < int(must["min_price"]):
        return False, 0, False

    # ----- PREFER -----
    prefer_count = 0
    pref = spec["prefer"]
    # PREFER 1: 一棟物件
    if p.propertyType in (pref.get("preferred_property_types") or []):
        prefer_count += 1
    # PREFER 2: 立地B以上
    if _grade_at_least(p.locationGrade, str(pref["min_location_grade"])):
        prefer_count += 1
    # PREFER 3: 残存25年以上
    if (p.remainingDurableYears is not None
            and p.remainingDurableYears >= int(pref["long_remaining_years"])):
        prefer_count += 1
    # PREFER 4: 再開発エリア
    if pref.get("require_redevelopment") and p.inRedevelopmentZone:
        prefer_count += 1
    # PREFER 5: 利回り 6%以上
    yld, is_actual = _resolve_yield(p)
    if yld is not None and yld >= float(pref["min_yield_for_prefer"]):
        prefer_count += 1

    # used_estimated: PREFER 5 で estimated 利回り使ったかどうかで決める
    used_estimated = (yld is not None and not is_actual
                      and yld >= float(pref["min_yield_for_prefer"]))
    return True, prefer_count, used_estimated


# ===== ペルソナ判定: 3. キャピタルゲイン型 ===========================

def _match_capital_gain(p: Property, cfg: dict) -> tuple[bool, int, bool]:
    spec = cfg["capital_gain"]
    must = spec["must"]
    never = spec["never"]

    # NEVER: 流動性低エリア（city 部分一致）
    if _city_matches_substring(p.city or "",
                               never.get("forbidden_cities_substring") or []):
        return False, 0, False
    # NEVER: 築30年超 × 木造
    fow = never.get("forbidden_old_wood") or {}
    if (fow and p.structure in (fow.get("structures") or [])
            and p.age is not None and p.age > int(fow.get("min_age", 0))):
        return False, 0, False

    # MUST 1: 流動性最上位エリア
    if (p.city or "") not in must["required_cities_exact"]:
        return False, 0, False
    # MUST 2: 物件種別
    if p.propertyType not in must["allowed_property_types"]:
        return False, 0, False
    # MUST 3: 中央値より -10% 以上安い
    if not _price_below_median(p, float(must["price_below_median_pct"])):
        return False, 0, False
    # MUST 4: 新耐震
    if p.builtYear is None or p.builtYear < int(must["min_built_year"]):
        return False, 0, False

    # ----- PREFER -----
    prefer_count = 0
    pref = spec["prefer"]
    # PREFER 1: 再開発
    if pref.get("require_redevelopment") and p.inRedevelopmentZone:
        prefer_count += 1
    # PREFER 2: 駅徒歩10分以内
    if (p.walkMinutes is not None
            and p.walkMinutes <= int(pref["max_walk_minutes"])):
        prefer_count += 1
    # PREFER 3: 路線S
    if _line_rank_at_least(p.lineRank, str(pref["min_line_rank"])):
        prefer_count += 1
    # PREFER 4: 値下げ履歴
    if pref.get("require_price_drop") and _has_price_drop(p):
        prefer_count += 1
    # PREFER 5: 滞留 90日以上
    days = _days_since_first_seen(p)
    if days is not None and days >= int(pref["min_days_listed"]):
        prefer_count += 1

    # キャピタルゲイン型は MUST に利回りを使わないため estimated 補正不要
    return True, prefer_count, False


# ===== ペルソナ判定: 4. 立地特化型 ====================================

def _match_location(p: Property, cfg: dict) -> tuple[bool, int, bool]:
    spec = cfg["location"]
    must = spec["must"]
    never = spec["never"]

    # NEVER: 駅徒歩15分超
    if (p.walkMinutes is not None
            and p.walkMinutes > int(never["walk_minutes_hard_max"])):
        return False, 0, False
    # NEVER: 福岡市外
    if _city_matches_substring(p.city or "",
                               never.get("forbidden_cities_substring") or []):
        return False, 0, False
    # NEVER: 駅情報不在
    if never.get("require_nearest_station") and not p.nearestStation:
        return False, 0, False

    # MUST 1: 立地A以上
    if not _grade_at_least(p.locationGrade, str(must["min_location_grade"])):
        return False, 0, False
    # MUST 2: 駅徒歩10分以内
    if (p.walkMinutes is None
            or p.walkMinutes > int(must["max_walk_minutes"])):
        return False, 0, False
    # MUST 3: 路線A以上
    if not _line_rank_at_least(p.lineRank, str(must["min_line_rank"])):
        return False, 0, False
    # 修正2: ブリーフ §5.4「利回りより立地。利回り妥協」と整合させるため
    # MUST 4 として要求していた「yield ≥ 4%」を削除し、PREFER 加点 (bonus_yield_min) に格下げ。
    # used_estimated は PREFER bonus_yield_min を発火させたときのみ True にする。
    yld, is_actual = _resolve_yield(p)

    # ----- PREFER -----
    prefer_count = 0
    pref = spec["prefer"]
    # PREFER 1: 立地S
    if p.locationGrade == str(pref["s_location_grade"]):
        prefer_count += 1
    # PREFER 2: 駅徒歩5分以内
    if (p.walkMinutes is not None
            and p.walkMinutes <= int(pref["walk_premium_minutes"])):
        prefer_count += 1
    # PREFER 3: 再開発
    if pref.get("require_redevelopment") and p.inRedevelopmentZone:
        prefer_count += 1
    # PREFER 4: 単身向け
    if _is_layout_single(p.layout or "", pref["single_layouts"]):
        prefer_count += 1
    # PREFER 5: ハザード low
    if pref.get("require_hazard_low") and p.hazardFlag == "low":
        prefer_count += 1
    # PREFER 6 (修正2): 利回り bonus — 立地+利回りも揃えば最高評価
    bonus_yield_min = pref.get("bonus_yield_min")
    yield_bonus_fired = (
        bonus_yield_min is not None
        and yld is not None
        and yld >= float(bonus_yield_min)
    )
    if yield_bonus_fired:
        prefer_count += 1

    # 利回りを実際に使ったときのみ estimated 補正を適用。
    # MUST 撤廃により、利回り未取得 / actual / estimated の3パターン。
    # PREFER bonus を estimated 値で発火させた場合のみ -1 を入れる。
    used_estimated = yield_bonus_fired and (not is_actual)
    return True, prefer_count, used_estimated


# ===== ペルソナ判定: 5. 築古再生型 ====================================

def _match_renovation(p: Property, cfg: dict) -> tuple[bool, int, bool]:
    spec = cfg["renovation"]
    must = spec["must"]
    never = spec["never"]

    # NEVER: 旧耐震
    if (p.builtYear is not None
            and p.builtYear < int(never["min_built_year_hard"])):
        return False, 0, False
    # NEVER: 北九州以遠
    if _city_matches_substring(p.city or "",
                               never.get("forbidden_cities_substring") or []):
        return False, 0, False

    # MUST 1: 価格上限 (1,000万円以下)
    if p.price is None or p.price > int(must["max_price"]):
        return False, 0, False
    # MUST 2: 物件種別（戸建系）
    if p.propertyType not in must["allowed_property_types"]:
        return False, 0, False
    # MUST 3: 新耐震
    if p.builtYear is None or p.builtYear < int(must["min_built_year"]):
        return False, 0, False
    # MUST 4: 福岡市内 + 近郊
    if not _city_matches_substring(p.city or "",
                                   must["required_cities_substring"]):
        return False, 0, False

    # ----- PREFER -----
    prefer_count = 0
    pref = spec["prefer"]
    # PREFER 1: 築 25〜40年（再生価値帯）
    if (p.age is not None
            and int(pref["age_min"]) <= p.age <= int(pref["age_max"])):
        prefer_count += 1
    # PREFER 2: 60m²以上
    if p.area is not None and p.area >= float(pref["min_area_sqm"]):
        prefer_count += 1
    # PREFER 3: 値下げ履歴
    if pref.get("require_price_drop") and _has_price_drop(p):
        prefer_count += 1
    # PREFER 4: 滞留 90日以上
    days = _days_since_first_seen(p)
    if days is not None and days >= int(pref["min_days_listed"]):
        prefer_count += 1

    # 築古再生型は MUST に利回りを使わない（再生後利回り目当て）
    return True, prefer_count, False


# ===== トップレベル =========================================================

PERSONA_FUNCS = {
    "income": _match_income,
    "loan_strategy": _match_loan_strategy,
    "capital_gain": _match_capital_gain,
    "location": _match_location,
    "renovation": _match_renovation,
}


def _calc_stars(
    persona_id: str,
    p: Property,
    prefer_count: int,
    used_estimated: bool,
    common: dict,
) -> int:
    """personaStars を算出する。

    補正:
        - estimated 利回り使用時 → -1
        - structureEstimated=True かつ persona_id が
          common.estimated_structure_penalty_personas に含まれる場合 → 追加 -1
          （v2.6.2 以降、対象は loan_strategy / income / capital_gain / location。
           renovation は structure 判定しないため対象外）
    最低/最大は common.min_stars / common.max_stars でクリップ。
    """
    base = int(common.get("base_stars", 3))
    max_stars = int(common.get("max_stars", 5))
    min_stars = int(common.get("min_stars", 1))
    yield_penalty = int(common.get("estimated_yield_star_penalty", 1))
    structure_penalty = int(common.get("estimated_structure_star_penalty", 1))
    structure_penalty_personas = (
        common.get("estimated_structure_penalty_personas") or []
    )

    stars = base + prefer_count
    if used_estimated:
        stars -= yield_penalty
    # 修正3: structure 推定補正
    if (
        bool(getattr(p, "structureEstimated", False))
        and persona_id in structure_penalty_personas
    ):
        stars -= structure_penalty
    return max(min_stars, min(max_stars, stars))


def match_personas(p: Property) -> tuple[list[str], dict[str, int]]:
    """5 ペルソナの MUST/PREFER 評価。

    Args:
        p: 対象物件 (score_property / estimate_yield_for_property を通過済みが前提)

    Returns:
        (matches, stars):
            matches: MUST 全クリアしたペルソナID のリスト
            stars:   ペルソナID → 1〜5
                MUST 全クリア=★3 から PREFER 加点 / estimated 補正 / structureEst 補正。
    """
    cfg = get_persona_config()
    common = cfg.get("common", {})
    matches: list[str] = []
    stars: dict[str, int] = {}

    for pid, fn in PERSONA_FUNCS.items():
        try:
            matched, prefer_count, used_est = fn(p, cfg)
        except Exception:  # noqa: BLE001 — 個別失敗で全体は落とさない
            continue
        if not matched:
            continue
        matches.append(pid)
        stars[pid] = _calc_stars(pid, p, prefer_count, used_est, common)

    return matches, stars
