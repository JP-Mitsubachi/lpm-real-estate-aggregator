"""Area median computation + Cap Rate benchmark resolution (PRD §5.2.3, v2.1).

v2.1 では `cap_rate_benchmark`（config由来）が **収益スコアの主指標**。
本モジュールの母集団中央値は **補助役** で、UI 表示・将来の差分分析・
ベンチマーク不適切時のフェイルセーフとして残す。

Two-stage grouping for population medians:
1. exact key (prefecture, city, propertyType) — needs ≥15 properties
2. fallback key (prefecture, propertyType)   — needs ≥`fallback_min` properties
"""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Optional

from models import Property
from services.config_loader import get_default_config


def group_key(p: Property) -> tuple[str, str, str]:
    return (p.prefecture, p.city, p.propertyType)


def _fallback_key(p: Property) -> tuple[str, str]:
    return (p.prefecture, p.propertyType)


def _yields(props: list[Property]) -> list[float]:
    return [p.yieldGross for p in props if p.yieldGross is not None]


def _price_per_sqm_values(props: list[Property]) -> list[float]:
    out: list[float] = []
    for p in props:
        if p.price is None or p.area is None or p.area <= 0:
            continue
        out.append(p.price / p.area)
    return out


def _median_or_none(values: list[float]) -> Optional[float]:
    return median(values) if values else None


def compute_medians(properties: list[Property]) -> dict[tuple, dict]:
    """Return medians keyed by exact group key (3-tuple) and fallback key (2-tuple).

    Schema:
        result[(pref, city, type)] = {
            "yield_median": float | None,
            "price_per_sqm_median": float | None,
            "source": "exact" | "fallback",
            "sample_size": int,
        }
        result[(pref, type)] = {
            ...
            "source": "fallback_aggregate",
            "sample_size": int,
        }
    """
    cfg = get_default_config()
    min_exact = cfg["median"]["min_sample_size"]
    fallback_min = cfg["median"]["fallback_min"]

    by_exact: dict[tuple[str, str, str], list[Property]] = defaultdict(list)
    by_fallback: dict[tuple[str, str], list[Property]] = defaultdict(list)
    for p in properties:
        by_exact[group_key(p)].append(p)
        by_fallback[_fallback_key(p)].append(p)

    out: dict[tuple, dict] = {}

    # Pass 1: fallback aggregates (needed before exact-with-fallback resolves)
    for fkey, group in by_fallback.items():
        if len(group) >= fallback_min:
            out[fkey] = {
                "yield_median": _median_or_none(_yields(group)),
                "price_per_sqm_median": _median_or_none(_price_per_sqm_values(group)),
                "source": "fallback_aggregate",
                "sample_size": len(group),
            }

    # Pass 2: exact groups, with fallback inheritance
    for ekey, group in by_exact.items():
        if len(group) >= min_exact:
            out[ekey] = {
                "yield_median": _median_or_none(_yields(group)),
                "price_per_sqm_median": _median_or_none(_price_per_sqm_values(group)),
                "source": "exact",
                "sample_size": len(group),
            }
            continue
        if len(group) >= fallback_min:
            fkey = (ekey[0], ekey[2])
            agg = out.get(fkey)
            if agg is None:
                continue
            out[ekey] = {
                "yield_median": agg["yield_median"],
                "price_per_sqm_median": agg["price_per_sqm_median"],
                "source": "fallback",
                "sample_size": agg["sample_size"],
            }
        # else: <fallback_min → omit entirely → caller handles N/A

    return out


def lookup_medians(p: Property, medians: dict[tuple, dict]) -> Optional[dict]:
    """Return medians dict for a property, or None if no statistics available."""
    return medians.get(group_key(p))


# ===== Cap Rate ベンチマーク（v2.1 主指標） =================================

def get_yield_benchmark(p: Property) -> float:
    """物件の (city, layout) → 福岡市 Cap Rate ベンチマーク値を返す。

    PRD §5.2.6 マスター:
        福岡市内ワンルーム/単身向け = 5.25%
        福岡市内ファミリー = 6.0%
        北九州市・久留米市・筑後 = 10.0%
        その他 = 6.0%（福岡市ファミリー値で仮置き）
    """
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


def benchmark_or_population_median(
    p: Property, medians: dict[tuple, dict]
) -> tuple[float, str]:
    """ベンチマーク優先、不適切時のみ母集団中央値にフォールバック。

    Returns:
        (利回り基準値, ソース名 "benchmark" | "population_median" | "default")
    """
    bench = get_yield_benchmark(p)
    if bench > 0:
        return bench, "benchmark"
    pop = lookup_medians(p, medians)
    if pop and pop.get("yield_median"):
        return float(pop["yield_median"]), "population_median"
    return 6.0, "default"
