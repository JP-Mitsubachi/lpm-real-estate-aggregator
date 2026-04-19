"""Re-score production properties.json with v2.1 scoring (PRD §5).

Usage:
    python -m scripts.eval_scoring_v21
    # or from L-008-deploy root:
    PYTHONPATH=. python scripts/eval_scoring_v21.py

Outputs to stdout:
    - Total / N/A率 / ランク分布
    - Top10物件サマリ
    - Sランクエリア偏在表
    - C1〜C5 自己評価チェック
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# repo root to PYTHONPATH
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from models import Property  # noqa: E402
from services.scoring import score_property  # noqa: E402

PROPERTIES_JSON = REPO_ROOT / "static" / "data" / "properties.json"
RANK_ORDER = ("S", "A", "B", "C", "D", "N/A")


def load_props() -> list[Property]:
    with PROPERTIES_JSON.open("r", encoding="utf-8") as f:
        data = json.load(f)
    raw_props: list[dict[str, Any]] = data.get("properties", [])
    out: list[Property] = []
    for raw in raw_props:
        # Strip extra fields not in model (Pydantic v2 ignore by default)
        try:
            out.append(Property(**raw))
        except Exception:
            # 価格ガード等で弾かれた場合はスキップ
            continue
    return out


def score_all(props: list[Property]) -> list[Property]:
    return [score_property(p) for p in props]


def rank_distribution(props: list[Property]) -> dict[str, int]:
    c = Counter(p.dealRank or "N/A" for p in props)
    return {r: c.get(r, 0) for r in RANK_ORDER}


def top_n(props: list[Property], n: int = 10) -> list[Property]:
    """v2.2: compositeRankValue を二次キーにし dealScore 同点を順位確定."""
    rank_priority = {"S": 0, "A": 1, "B": 2, "C": 3, "D": 4, "N/A": 5}
    return sorted(
        props,
        key=lambda p: (
            rank_priority.get(p.dealRank or "N/A", 9),
            -(p.compositeRankValue or p.dealScore or 0),
        ),
    )[:n]


def s_rank_area_breakdown(props: list[Property]) -> dict[str, int]:
    s_props = [p for p in props if p.dealRank == "S"]
    return dict(Counter(p.city or "(unknown)" for p in s_props).most_common())


def is_top_quality_pick(p: Property) -> bool:
    """C1 自己評価: 投資家視点で4条件を満たすか.

    ① 福岡市7区
    ② 残存耐用年数 ≥ 10年（or 構造不明時は age ≤ 30）
    ③ price ≤ 30,000,000
    ④ 物件タイプが投資対象
    """
    fukuoka_inner = {
        "福岡市中央区", "福岡市博多区", "福岡市早良区", "福岡市南区",
        "福岡市西区", "福岡市城南区", "福岡市東区",
    }
    valid_types = {
        "区分マンション", "中古マンション", "投資用マンション",
        "一棟売りマンション", "一棟売りアパート", "戸建賃貸",
        "新築マンション",
    }
    if p.city not in fukuoka_inner:
        return False
    if p.price is None or p.price > 30_000_000:
        return False
    if p.propertyType not in valid_types:
        return False
    if p.remainingDurableYears is not None:
        if p.remainingDurableYears < 10:
            return False
    else:
        if p.age is not None and p.age > 30:
            return False
    return True


def main() -> int:
    print("=" * 80)
    print("L-008 Scoring v2.1 — Re-evaluation Report")
    print("=" * 80)

    props = load_props()
    print(f"\nLoaded properties: {len(props)}")

    scored = score_all(props)
    print("Scoring complete.")

    # 1) Rank distribution
    dist = rank_distribution(scored)
    total = sum(dist.values())
    print("\n--- Rank Distribution ---")
    for r in RANK_ORDER:
        n = dist[r]
        pct = (n / total * 100) if total else 0
        print(f"  {r:<5}: {n:>5} ({pct:5.1f}%)")
    na_pct = dist["N/A"] / total * 100 if total else 0
    print(f"\nN/A率: {na_pct:.1f}% (target ≤ 15%)")

    # 2) Top 10
    print("\n--- Top 10 (by rank then dealScore) ---")
    print(f"{'#':<3} {'rank':<4} {'score':<5} {'city':<14} {'type':<14} {'age':>3} "
          f"{'rem':>4} {'yld':>5} {'loc':>3} {'lpoint':>6} {'lrank':>5} {'walk':>4}")
    for i, p in enumerate(top_n(scored, 10), 1):
        print(
            f"{i:<3} {p.dealRank or '-':<4} {p.dealScore or 0:<5} "
            f"{(p.city or '')[:14]:<14} {(p.propertyType or '')[:14]:<14} "
            f"{p.age if p.age is not None else '-':>3} "
            f"{p.remainingDurableYears if p.remainingDurableYears is not None else '-':>4} "
            f"{f'{p.yieldGross:.2f}' if p.yieldGross is not None else '-':>5} "
            f"{p.locationGrade or '-':>3} "
            f"{p.locationScore if p.locationScore is not None else '-':>6} "
            f"{p.lineRank or '-':>5} "
            f"{p.walkMinutes if p.walkMinutes is not None else '-':>4}"
        )

    # C1: top10 quality
    top10 = top_n(scored, 10)
    quality_count = sum(1 for p in top10 if is_top_quality_pick(p))
    print(f"\n[C1] Top10 quality pick count: {quality_count}/10 (target ≥ 8)")

    # 3) Sランク偏在
    print("\n--- Sランク エリア分布 ---")
    sb = s_rank_area_breakdown(scored)
    s_total = sum(sb.values())
    for city, n in sb.items():
        pct = (n / s_total * 100) if s_total else 0
        print(f"  {city:<20}: {n:>4} ({pct:5.1f}%)")

    chuo_hakata_sawara = sum(
        n for c, n in sb.items()
        if c in ("福岡市中央区", "福岡市博多区", "福岡市早良区")
    )
    s_top3_pct = (chuo_hakata_sawara / s_total * 100) if s_total else 0
    print(f"\nS中央+博多+早良: {chuo_hakata_sawara}/{s_total} ({s_top3_pct:.1f}%, target ≥50%)")

    # C2: Top10エリア集計
    top10_cities = Counter(p.city or "(unknown)" for p in top10)
    chuo_hakata_sawara_top10 = sum(
        n for c, n in top10_cities.items()
        if c in ("福岡市中央区", "福岡市博多区", "福岡市早良区")
    )
    out_of_fukuoka_top10 = sum(
        n for c, n in top10_cities.items()
        if c.startswith("北九州市") or not c.startswith("福岡市")
    )
    print(f"\n[C2] Top10 中央+博多+早良: {chuo_hakata_sawara_top10}/10 (target ≥6)")
    print(f"[C2] Top10 北九州/その他県外: {out_of_fukuoka_top10}/10 (target ≤1)")

    # C3: Sランクで残存5年未満が混入していないか
    s_with_short_remaining = [
        p for p in scored
        if p.dealRank == "S"
        and p.remainingDurableYears is not None
        and p.remainingDurableYears < 5
    ]
    print(f"\n[C3] Sランクに残存5年未満: {len(s_with_short_remaining)} (target =0)")

    # C5: dealModelVersion 確認
    versions = Counter(p.dealModelVersion for p in scored)
    print(f"\n[C5] dealModelVersion 分布: {dict(versions)}")

    print("\n" + "=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
