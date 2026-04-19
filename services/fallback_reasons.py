"""Machine-generated 3-line fallback reasoning text (PRD §5.3.2 v2.1).

Produced when the Claude API call fails or is intentionally skipped.
v2.1 では立地・融資・収益の3軸構造に整列。`scoring.py` 側で本関数を
呼ばずに直接生成しているケースもあるため、本関数は LLM フェイルオーバー
専用の単独入口として機能する。

Caller is expected to set `Property.isAutoFallback = True`.
"""
from __future__ import annotations

from typing import Optional

from models import Property


def _line1_location(p: Property) -> str:
    """立地行: city + 路線 + 駅徒歩 + locationGrade."""
    parts = []
    if p.city:
        parts.append(p.city)
    if p.lineRank:
        parts.append(f"路線{p.lineRank}")
    if p.walkMinutes is not None:
        parts.append(f"徒歩{p.walkMinutes}分")
    grade = p.locationGrade or "−"
    if not parts:
        return f"立地情報が限定的（{grade}ランク）です。"
    return f"{'・'.join(parts)} の立地は{grade}ランクです。"


def _line2_loan(p: Property) -> str:
    """融資行: 構造（推定有無）+ 残存耐用年数."""
    structure = p.structure or "構造不明"
    if p.structureEstimated:
        structure = f"{structure}（推定）"
    if p.age is None:
        return f"{structure}・築年不明のため融資判定保留です。"
    rem = p.remainingDurableYears
    if rem is None:
        return f"{structure}築{p.age}年で残存耐用年数の計算ができません。"
    if rem >= 20:
        return f"{structure}築{p.age}年で残存{rem}年、長期融資が見込めます。"
    if rem >= 10:
        return f"{structure}築{p.age}年で残存{rem}年、地銀・信金で条件付き融資の射程です。"
    if rem >= 5:
        return f"{structure}築{p.age}年で残存{rem}年、自己資金比率を厚くする必要があります。"
    if rem >= 0:
        return f"{structure}築{p.age}年で残存{rem}年、フルローンは厳しく現金主体の判断になります。"
    return f"{structure}築{p.age}年で法定耐用年数超え、原則回避ゾーンです。"


def _line3_yield(p: Property, medians: Optional[dict] = None) -> str:
    """収益行: ベンチマーク利回りとの乖離."""
    bench = p.benchmarkCapRate
    if bench is None and medians:
        bench = medians.get("yield_median")
    if p.yieldGross is None:
        if bench:
            return (
                f"表面利回り未掲載。福岡市ベンチマーク{bench:.1f}%との比較は要詳細確認です。"
            )
        return "表面利回り未掲載のため収益判定はホールド、現地確認が必要です。"
    if bench is None or bench <= 0:
        return f"表面{p.yieldGross:.1f}%。比較対象のベンチマークが取得できませんでした。"
    deviation = (p.yieldGross - bench) / bench * 100
    sign = "+" if deviation >= 0 else ""
    direction = "狙える" if deviation > 0 else "下回る"
    return (
        f"表面{p.yieldGross:.1f}%はベンチマーク{bench:.1f}%に対し"
        f"{sign}{deviation:.0f}%、市場との乖離を{direction}水準です。"
    )


def generate_fallback_reasons(p: Property, medians: Optional[dict] = None) -> list[str]:
    """3行根拠テキスト（立地→融資→収益）。"""
    return [
        _line1_location(p),
        _line2_loan(p),
        _line3_yield(p, medians),
    ]
