"""Yield estimator (v2.5 B案) — SUUMO/ふれんず物件の利回り推計.

本番 properties.json (3,183件) 調査結果:
    SUUMO   : 0/1369 (0%)  yieldGross 未掲載
    ふれんず : 0/1318 (0%)  同上
    HOME'S  : 442/496 (89%) yieldGross 掲載あり

→ 結果として Sランク26件/Aランク61件 すべて HOME'S に偏重。
   母集団中央値 + Cap Rate ベンチマークでフォールバック推計することで、
   SUUMO/ふれんず も同じ土俵に乗せる。

推計ロジック:
    1. p.yieldGross あり                → (yieldGross, "actual")
    2. p.yieldMedianInArea あり         → (yieldMedianInArea, "median")
    3. benchmark cap rate で fallback   → (cap_rate, "fallback")
    4. benchmark も取れない             → (None, "none")

信頼度ラベルは UI 側で「推定」バッジ表示および、scoring.py で
estimated 判定時に点数を最大値の半分にクリップするのに使う。
"""
from __future__ import annotations

from typing import Optional, Tuple

from models import Property
from services.medians import (
    get_yield_benchmark as _get_yield_benchmark,
    lookup_medians as _lookup_medians,
)

# Alias used by tests to monkeypatch the benchmark resolver.
# （service 内部では get_benchmark_cap_rate を参照する形で testability を確保）
get_benchmark_cap_rate = _get_yield_benchmark


YieldConfidence = str  # "actual" | "median" | "fallback" | "none"


def _resolve_median_from_dict(p: Property, medians: dict) -> Optional[float]:
    """medians 辞書から p のグループキー由来の yield_median を取得する."""
    if not medians:
        return None
    entry = _lookup_medians(p, medians)
    if entry is None:
        return None
    v = entry.get("yield_median")
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def estimate_yield_for_property(
    p: Property, medians: dict
) -> Tuple[Optional[float], YieldConfidence]:
    """yieldGross が None の物件に対し、中央値/ベンチマークでフォールバック推計する.

    Args:
        p:       対象物件（Property オブジェクト）
        medians: `compute_medians` で計算済みの母集団中央値辞書

    Returns:
        (推計利回り %, 信頼度ラベル) のタプル
        - 信頼度ラベル:
            * "actual"     : yieldGross が掲載されている
            * "median"     : 母集団中央値由来（p.yieldMedianInArea もしくは
                             medians 辞書の lookup 結果）
            * "fallback"   : 中央値も無いため Cap Rate ベンチマーク由来
            * "none"       : 推計不能
    """
    # 1) 実値掲載（HOME'S 等）
    if p.yieldGross is not None:
        return (p.yieldGross, "actual")

    # 2-a) score_property が既に書き戻し済みの yieldMedianInArea を優先
    if p.yieldMedianInArea is not None and p.yieldMedianInArea > 0:
        return (p.yieldMedianInArea, "median")

    # 2-b) score_property 前に呼ばれた場合は medians 辞書から直接 lookup
    med = _resolve_median_from_dict(p, medians)
    if med is not None:
        return (med, "median")

    # 3) Cap Rate ベンチマーク（最後の砦）
    try:
        bench = get_benchmark_cap_rate(p)
    except Exception:  # noqa: BLE001
        bench = 0.0
    if bench and bench > 0:
        return (float(bench), "fallback")

    # 4) どれも取れない
    return (None, "none")
