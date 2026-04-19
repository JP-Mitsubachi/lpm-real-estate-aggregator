"""5-axis property scoring (PRD §5 v2.1).

Composition:
    location (0-30) + yield_benchmark (0-30) + loan (0-20)
    + stagnation (0 fixed) + risk (0-10) = 0-100

`score_property` mutates a Property to populate:
    dealScore, dealRank, locationScore, yieldBenchmarkScore, loanScore,
    stagnationScore, riskScore, walkMinutes, locationGrade, lineRank,
    remainingDurableYears, inRedevelopmentZone, hazardFlag,
    structureEstimated, benchmarkCapRate, dealReasons, dealModelVersion

Sランク複合条件 (PRD §5.2.2):
    score >= 80 AND location >= 15 AND loan >= 10 AND yield >= 15
    AND remaining_years >= 10
を満たさない場合、A に降格。
残存5年未満は強制 D 降格（融資不可ゾーン排除）。

N/A 判定 (PRD §5.2.10):
    city / prefecture / propertyType いずれか空 → N/A
    age が None かつ propertyType が「土地」以外 → N/A
"""
from __future__ import annotations

from typing import Optional, Tuple

from models import DEAL_MODEL_VERSION_DEFAULT, Property
from services.config_loader import get_default_config
from services.location_score import (
    calc_location_score,
    get_area_grade,
    get_hazard_flag,
    get_line_rank,
    is_in_redevelopment_zone,
    parse_walk_minutes,
)
from services.medians import lookup_medians


# ===== Cap Rate ベンチマーク =================================================

def get_benchmark_cap_rate(p: Property) -> float:
    """物件の (city, layout) からベンチマーク利回りを返す。"""
    cfg = get_default_config()
    crb = cfg["cap_rate_benchmark"]
    single_kw = cfg["single_layout_keywords"]
    city = p.city or ""
    layout = p.layout or ""

    if city.startswith("北九州市"):
        return float(crb["kitakyushu"])
    if city.startswith("久留米市") or city.startswith("筑後") or city.startswith("大牟田"):
        return float(crb["kurume"])
    if city.startswith("福岡市"):
        if any(kw in layout for kw in single_kw):
            return float(crb["fukuoka_city_single"])
        return float(crb["fukuoka_city_family"])
    return float(crb["default"])


# ===== 収益スコア (yieldBenchmarkScore) =====================================

def calc_yield_benchmark_score(p: Property) -> Optional[int]:
    """0-30 点。+20%乖離で30点。yieldGross None → None。"""
    if p.yieldGross is None:
        return None
    cfg = get_default_config()
    bench = get_benchmark_cap_rate(p)
    if bench <= 0:
        return None
    cap = int(cfg["yield_score"]["cap"])
    multiplier = float(cfg["yield_score"]["multiplier"])
    deviation_pct = (p.yieldGross - bench) / bench * 100
    raw = deviation_pct * multiplier
    return int(min(cap, max(0, raw)))


# ===== 構造推定 + 残存耐用年数 ===============================================

def _normalize_structure(structure: str) -> str:
    """alias を正規化キー（RC/SRC/S造/木造）に変換。"""
    if not structure or structure in ("-", "その他"):
        return ""
    cfg = get_default_config()
    aliases = cfg.get("structure_aliases", {})
    if structure in aliases:
        return aliases[structure]
    # 完全一致しない場合、lifespan の主要キーをそのまま受け入れる
    lifespan = cfg["structure_lifespan"]
    if structure in lifespan:
        return structure
    return ""


def estimate_structure(p: Property) -> Tuple[Optional[str], bool]:
    """(構造キー, 推定したかフラグ) を返す。

    structure が空/その他 のときに propertyType + name から推定する。
    土地は (None, True)。
    """
    normalized = _normalize_structure(p.structure or "")
    if normalized:
        return normalized, False

    cfg = get_default_config()
    rules = cfg["structure_estimation"]["rules"]
    pt = p.propertyType or ""
    name = p.name or ""
    haystack = pt + " " + name
    for rule in rules:
        for kw in rule["keywords_in_property_type"]:
            if kw in haystack:
                return rule["assumed_structure"], True
    return cfg["structure_estimation"]["default"], True


def get_structure_lifespan(structure: Optional[str]) -> Optional[int]:
    """構造キー → 法定耐用年数。Noneは None。"""
    if structure is None:
        return None
    cfg = get_default_config()
    lifespan = cfg["structure_lifespan"]
    if structure in lifespan:
        return int(lifespan[structure])
    return int(lifespan.get("default", 47))


def get_remaining_durable_years(p: Property) -> Optional[int]:
    """残存耐用年数 = lifespan - age。 age None → None。"""
    if p.age is None:
        return None
    structure, _ = estimate_structure(p)
    if structure is None:
        # 土地など
        return None
    lifespan = get_structure_lifespan(structure)
    if lifespan is None:
        return None
    return int(lifespan - p.age)


# ===== 融資スコア (loanScore) =================================================

def calc_loan_score(p: Property) -> Optional[int]:
    """0-20 点。残存耐用年数ブラケット参照。土地は中位点(10)。age None → None。"""
    cfg = get_default_config()
    ln_cfg = cfg["loan_score"]
    structure, _ = estimate_structure(p)

    # 土地のみ
    if structure is None and p.propertyType and "土地" in p.propertyType:
        return int(ln_cfg["land_only_points"])

    if p.age is None:
        return None

    remaining = get_remaining_durable_years(p)
    if remaining is None:
        return None

    # ブラケット降順で評価（max_remaining 順）
    # 残存20年以上 → 20, 10-19 → 15, 5-9 → 10, 1-4 → 5, それ以下 → 0
    brackets = sorted(ln_cfg["brackets"], key=lambda b: -b["min_remaining"])
    for b in brackets:
        if remaining >= b["min_remaining"]:
            return int(b["points"])
    return 0


# ===== リスクスコア ==========================================================

def calc_risk_score(p: Property) -> int:
    """0-10 点。base安全(5) + 再開発(5) - ハザードhigh(5).

    v2.3: ハザード判定は city × nearestStation 組合せで行う（過剰判定回避）。
    """
    cfg = get_default_config()
    rs = cfg["risk_score"]
    base = int(rs["base_safety_points"])
    redev = int(rs["redevelopment_bonus"])
    high_pen = int(rs["hazard_high_penalty"])

    score = base
    if is_in_redevelopment_zone(p):
        score += redev
    flag = get_hazard_flag(p.city, p.nearestStation)
    if flag == "high":
        score += high_pen
    return max(int(rs["cap_min"]), min(int(rs["cap_max"]), score))


# ===== 滞留スコア ============================================================

def calc_stagnation_score(p: Property) -> int:
    """v2.1 は常に0（将来枠）。"""
    cfg = get_default_config()
    return int(cfg["stagnation_score"]["default"])


# ===== ランク割当 + Sランクガード ============================================

def assign_rank(score: Optional[int]) -> str:
    if score is None:
        return "N/A"
    cfg = get_default_config()
    th = cfg["thresholds"]
    if score >= th["rank_S"]:
        return "S"
    if score >= th["rank_A"]:
        return "A"
    if score >= th["rank_B"]:
        return "B"
    if score >= th["rank_C"]:
        return "C"
    return "D"


def apply_s_rank_guard(
    *,
    rank: str,
    score: int,
    location_score: int,
    loan_score: int,
    yield_score: int,
    remaining_years: Optional[int],
    price: Optional[int] = None,
    structure_estimated: bool = False,
    property_type: Optional[str] = None,
) -> str:
    """Sランク複合条件・残存年数・price 上限・推定構造・propertyType ガード。

    v2.1: 立地・融資・収益・残存の AND 条件 + 残存5年未満強制 D 降格
    v2.2: + price 上限ガード（s_rank_max_price）
    v2.3: + structure_estimated=True で A 降格
          + property_type modifier（一棟物件は yield/loan 閾値が厳しめ）
    """
    cfg = get_default_config()
    th = cfg["thresholds"]
    # 残存5年未満強制降格（remaining_years が int で得られているときのみ）
    if remaining_years is not None and remaining_years < 5:
        return "D"

    if rank != "S":
        return rank

    # propertyType modifier で yield/loan 閾値を上書き（区分は default 継承）
    pt_modifiers = cfg.get("property_type_modifiers", {}) or {}
    pt_overrides = pt_modifiers.get(property_type or "", {}) if isinstance(pt_modifiers, dict) else {}
    min_yield = int(pt_overrides.get("s_rank_min_yield", th["s_rank_min_yield"]))
    min_loan = int(pt_overrides.get("s_rank_min_loan", th["s_rank_min_loan"]))

    if location_score < int(th["s_rank_min_location"]):
        return "A"
    if loan_score < min_loan:
        return "A"
    if yield_score < min_yield:
        return "A"
    if remaining_years is None or remaining_years < int(th["s_rank_min_remaining_years"]):
        return "A"
    # v2.2: price 上限ガード
    if price is not None and price > int(th["s_rank_max_price"]):
        return "A"
    # v2.3: 構造推定ベースのSランクは認めない
    if structure_estimated:
        return "A"
    return "S"


# ===== N/A 判定 ==============================================================

def _is_na_eligible(p: Property) -> bool:
    cfg = get_default_config()
    rules = cfg.get("na_rules", {})
    if rules.get("require_prefecture") and not p.prefecture:
        return True
    if rules.get("require_city") and not p.city:
        return True
    if rules.get("require_property_type") and not p.propertyType:
        return True
    if rules.get("require_age_unless_land"):
        is_land = "土地" in (p.propertyType or "")
        if p.age is None and not is_land:
            return True
    return False


# ===== reasons 生成 ==========================================================

def _build_reasons(
    p: Property,
    *,
    location_grade: Optional[str],
    walk: Optional[int],
    line_rank: Optional[str],
    remaining_years: Optional[int],
    structure_key: Optional[str],
    structure_estimated: bool,
    benchmark: float,
) -> list[str]:
    """3行根拠テキスト（PRD §5.3.2 構造）。"""
    # 行1: 立地
    line1_parts = [p.city or "立地不明"]
    if line_rank:
        line1_parts.append(f"路線{line_rank}")
    if walk is not None:
        line1_parts.append(f"徒歩{walk}分")
    grade_label = location_grade or "−"
    line1 = f"{'・'.join(line1_parts)} の立地は{grade_label}ランクです。"

    # 行2: 融資
    structure_label = structure_key or "構造不明"
    if structure_estimated and structure_key:
        structure_label = f"{structure_key}（構造推定）"
    if p.age is None:
        line2 = f"{structure_label}・築年不明のため融資判定保留です。"
    elif remaining_years is None:
        line2 = f"{structure_label}・築{p.age}年で残存耐用年数の計算ができません。"
    elif remaining_years >= 20:
        line2 = f"{structure_label}築{p.age}年で残存{remaining_years}年、長期融資が見込めます。"
    elif remaining_years >= 10:
        line2 = f"{structure_label}築{p.age}年で残存{remaining_years}年、地銀・信金で条件付き融資の射程です。"
    elif remaining_years >= 5:
        line2 = f"{structure_label}築{p.age}年で残存{remaining_years}年、自己資金比率を厚くする必要があります。"
    elif remaining_years >= 0:
        line2 = f"{structure_label}築{p.age}年で残存{remaining_years}年、フルローンは厳しく現金主体の判断になります。"
    else:
        line2 = f"{structure_label}築{p.age}年で法定耐用年数超え、原則回避ゾーンです。"

    # 行3: 収益
    if p.yieldGross is None:
        line3 = f"表面利回り未掲載。福岡市ベンチマーク{benchmark:.1f}%との比較は要詳細確認です。"
    else:
        deviation = (p.yieldGross - benchmark) / benchmark * 100
        sign = "+" if deviation >= 0 else ""
        line3 = (
            f"表面{p.yieldGross:.1f}%はベンチマーク{benchmark:.1f}%に対し"
            f"{sign}{deviation:.0f}%、市場との乖離を{('狙える' if deviation > 0 else '下回る')}水準です。"
        )

    return [line1, line2, line3]


# ===== トップレベル: score_property ==========================================

def score_property(
    p: Property,
    medians: Optional[dict[tuple, dict]] = None,
) -> Property:
    """v2.1 5軸スコアリング。Property をミューテートして返す。

    v2.5: medians 引数を受け取ると yieldMedianInArea / pricePerSqmMedian /
          yieldDeviation を書き戻す（M2 UI の中央値比表示用）。
    """
    p.dealModelVersion = DEAL_MODEL_VERSION_DEFAULT

    # メタ情報（軸を計算する前に立ててリーズンに使う）
    p.locationGrade = get_area_grade(p.city) if p.city else None
    p.lineRank = get_line_rank(p.nearestStation or "")
    p.walkMinutes = parse_walk_minutes(p.nearestStation, p.address, p.name)
    p.benchmarkCapRate = get_benchmark_cap_rate(p)
    p.inRedevelopmentZone = is_in_redevelopment_zone(p)
    p.hazardFlag = get_hazard_flag(p.city, p.nearestStation)

    structure_key, estimated = estimate_structure(p)
    p.structureEstimated = estimated
    p.remainingDurableYears = get_remaining_durable_years(p)

    # N/A 早期判定
    if _is_na_eligible(p):
        p.dealScore = None
        p.dealRank = "N/A"
        p.locationScore = None
        p.yieldBenchmarkScore = None
        p.loanScore = None
        p.stagnationScore = 0
        p.riskScore = None
        p.dealReasons = _build_reasons(
            p,
            location_grade=p.locationGrade,
            walk=p.walkMinutes,
            line_rank=p.lineRank,
            remaining_years=p.remainingDurableYears,
            structure_key=structure_key,
            structure_estimated=estimated,
            benchmark=p.benchmarkCapRate,
        )
        if estimated and structure_key:
            p.dealReasons.append("構造推定（propertyTypeから仮置き）")
        return p

    # 各軸計算
    loc_s = calc_location_score(p)
    yld_s = calc_yield_benchmark_score(p)
    ln_s = calc_loan_score(p)
    stg_s = calc_stagnation_score(p)
    rsk_s = calc_risk_score(p)

    p.locationScore = loc_s
    p.yieldBenchmarkScore = yld_s
    p.loanScore = ln_s
    p.stagnationScore = stg_s
    p.riskScore = rsk_s

    # 合算（None は 0 として加算するが、Sランク複合条件は厳しく見る）
    total = (
        (loc_s or 0)
        + (yld_s or 0)
        + (ln_s or 0)
        + (stg_s or 0)
        + (rsk_s or 0)
    )
    p.dealScore = int(total)
    base_rank = assign_rank(p.dealScore)

    # Sランク複合条件 + 残存5年未満降格 + v2.2 price 上限 + v2.3 構造推定/PT modifiers
    p.dealRank = apply_s_rank_guard(
        rank=base_rank,
        score=p.dealScore,
        location_score=loc_s or 0,
        loan_score=ln_s or 0,
        yield_score=yld_s or 0,
        remaining_years=p.remainingDurableYears,
        price=p.price,
        structure_estimated=p.structureEstimated,
        property_type=p.propertyType,
    )

    # v2.2: タイブレーカー用の合成ランク値を計算
    # ランク境界を絶対跨がないよう、サブスコアの加算は最大 0.0325 程度
    p.compositeRankValue = (
        float(p.dealScore)
        + (loc_s or 0) * 0.001
        + (ln_s or 0) * 0.0001
        + (yld_s or 0) * 0.00001
        + max(0, p.remainingDurableYears or 0) * 0.000001
    )

    # v2.5: 母集団中央値の書き戻し（M2 UI 中央値比表示用）
    # 1) exact group key の母集団中央値を優先
    # 2) yield_median が None の場合、Cap Rate ベンチマークでフォールバック
    #    （区分マンション等で yieldGross 掲載率が低いため、benchmark を中央値とみなす）
    if medians is not None:
        med_entry = lookup_medians(p, medians)
        if med_entry is not None:
            p.yieldMedianInArea = med_entry.get("yield_median")
            p.pricePerSqmMedian = med_entry.get("price_per_sqm_median")
        # yield_median 不在時は Cap Rate ベンチマークでフォールバック
        if p.yieldMedianInArea is None and p.benchmarkCapRate:
            p.yieldMedianInArea = p.benchmarkCapRate
        if p.yieldGross is not None and p.yieldMedianInArea:
            p.yieldDeviation = (
                (p.yieldGross - p.yieldMedianInArea) / p.yieldMedianInArea * 100
            )

    # 根拠テキスト
    p.dealReasons = _build_reasons(
        p,
        location_grade=p.locationGrade,
        walk=p.walkMinutes,
        line_rank=p.lineRank,
        remaining_years=p.remainingDurableYears,
        structure_key=structure_key,
        structure_estimated=estimated,
        benchmark=p.benchmarkCapRate,
    )
    if estimated and structure_key:
        p.dealReasons.append("構造推定（propertyTypeから仮置き）")

    return p
