"""Standalone scraper — runs all sites and writes properties.json.

Run this in GitHub Actions to generate static data for the frontend.
Usage:
    python scrape.py [--output FILE] [--city CITY] [--with-ai]

`--with-ai` を付けるとスコアリング後 Claude Haiku 4.5 で dealReasons を上書き。
前日 properties.json があれば差分検出で API 呼び出しを最小化する。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from models import Property, SearchQuery
from services.medians import compute_medians
from services.orchestrator import run_search
from services.persona_matcher import match_personas
from services.scoring import score_property
from services.yield_estimator import estimate_yield_for_property

# v2.5: priceHistory の最大保持件数
PRICE_HISTORY_MAX_ENTRIES = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _load_previous_properties(out_path: Path) -> tuple[list[Property], set[str]]:
    """前日 properties.json を Property リスト + ID set で返す."""
    if not out_path.exists():
        return [], set()
    try:
        prev_data = json.loads(out_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not read previous data: %s", e)
        return [], set()

    raw_props = prev_data.get("properties", [])
    parsed: list[Property] = []
    for raw in raw_props:
        # downstream フィールド (isNew 等) は無視
        clean = {k: v for k, v in raw.items() if k != "isNew"}
        try:
            parsed.append(Property(**clean))
        except Exception:  # noqa: BLE001
            continue
    return parsed, {p.id for p in parsed}


def _apply_history_tracking(
    today_props: list[Property],
    previous_props: list[Property],
    today_iso: str,
    today_date: str,
) -> None:
    """v2.5: firstSeenAt と priceHistory を継承・更新する.

    Args:
        today_props: 今回スクレイピングした物件（mutate される）
        previous_props: 前日 properties
        today_iso: 今日のISO8601 タイムスタンプ（例: "2026-04-19T12:00:00Z"）
        today_date: 今日の日付（例: "2026-04-19"）
    """
    prev_by_id: dict[str, Property] = {p.id: p for p in previous_props}

    for p in today_props:
        prev = prev_by_id.get(p.id)

        # firstSeenAt: 既存物件なら継承、新規なら今日
        if prev is not None and prev.firstSeenAt:
            p.firstSeenAt = prev.firstSeenAt
        else:
            p.firstSeenAt = today_iso

        # priceHistory:
        #   新規 → [{date, price}]
        #   既存・価格不変 → 継承（増えない）
        #   既存・価格変動 → 継承 + 今日のエントリ append（最大 N 件）
        if prev is None:
            if p.price is not None:
                p.priceHistory = [{"date": today_date, "price": p.price}]
            else:
                p.priceHistory = []
        else:
            inherited = list(prev.priceHistory or [])
            # 価格が前日と異なる（かつ今回 price が取れている）場合のみ追記
            last_price = inherited[-1]["price"] if inherited else None
            if p.price is not None and p.price != last_price:
                inherited.append({"date": today_date, "price": p.price})
                # 最大 N 件にトリム（古いものから drop）
                if len(inherited) > PRICE_HISTORY_MAX_ENTRIES:
                    inherited = inherited[-PRICE_HISTORY_MAX_ENTRIES:]
            p.priceHistory = inherited


def _enrich_with_ai(
    today: list[Property],
    previous: list[Property],
    state_dir: Path,
    today_iso: str,
) -> dict:
    """AI 根拠生成パイプラインを呼ぶ. 例外は raise せず meta に積む."""
    from services.ai_reasons import AnthropicClient, generate_reasons_batch
    from services.cost_ledger import CostLedger

    ledger = CostLedger(state_dir=state_dir, today=today_iso)
    client = AnthropicClient(strict=False)
    if not client.enabled:
        logger.warning(
            "ANTHROPIC_API_KEY 未設定 — AI 根拠生成はスキップし全件 fallback で継続"
        )
    stats = generate_reasons_batch(
        today, ledger=ledger, previous_props=previous or None, client=client
    )
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description="L-008 scraper — writes properties.json")
    parser.add_argument(
        "--output", default="static/data/properties.json",
        help="Output JSON file path",
    )
    parser.add_argument(
        "--city", default=None,
        help="Filter by city (e.g. '福岡市博多区'). Omit for all wards.",
    )
    parser.add_argument(
        "--with-ai", action="store_true",
        help="Claude Haiku 4.5 で dealReasons を上書き (要 ANTHROPIC_API_KEY)",
    )
    parser.add_argument(
        "--skip-ai", action="store_true",
        help="(後方互換) AI 上書きをスキップする — --with-ai 未指定と同じ",
    )
    parser.add_argument(
        "--state-dir", default="data/state",
        help="cost_ledger の保存先ディレクトリ",
    )
    args = parser.parse_args()

    use_ai = bool(args.with_ai) and not args.skip_ai

    query = SearchQuery(prefecture="福岡県", city=args.city)
    logger.info("Starting scrape: city=%s with_ai=%s", args.city or "ALL", use_ai)

    start = datetime.utcnow()
    result = await run_search(query)
    elapsed = (datetime.utcnow() - start).total_seconds()

    # v2.5: 母集団中央値を先に計算（スコアリングで書き戻すため）
    medians = compute_medians(result.properties)

    # v2.5 B案: SUUMO/ふれんず等 yieldGross 未掲載物件の利回りを推計
    # compute_medians の結果を使い、score_property の前に書き戻す。
    # （scoring.py 側は yieldGross が無ければ yieldEstimated を使うよう拡張済み）
    for p in result.properties:
        est, conf = estimate_yield_for_property(p, medians)
        p.yieldEstimated = est
        p.yieldSourceConfidence = conf

    # スコアリング (v2.5) を全件に適用
    for p in result.properties:
        score_property(p, medians=medians)

    # v2.6: 投資家ペルソナマッチング（5ペルソナ）
    # score_property の後で実行 — pricePerSqmMedian / yieldMedianInArea /
    # locationGrade / lineRank / walkMinutes / inRedevelopmentZone /
    # remainingDurableYears / yieldDeviation などを参照するため。
    for p in result.properties:
        matches, stars = match_personas(p)
        p.personaMatches = matches
        p.personaStars = stars

    # 前日 properties を読み込み (差分検出 / AI 上書き / 履歴継承で使う)
    out_path = Path(args.output)
    prev_props, prev_ids = _load_previous_properties(out_path)
    logger.info("Previous data: %d properties", len(prev_ids))

    # v2.5: firstSeenAt と priceHistory を継承・更新
    today_iso = datetime.utcnow().isoformat() + "Z"
    today_date = datetime.utcnow().strftime("%Y-%m-%d")
    _apply_history_tracking(
        result.properties, prev_props, today_iso=today_iso, today_date=today_date,
    )

    # AI 根拠生成 (差分のみ・予算ガード)
    ai_stats: Optional[dict] = None
    if use_ai:
        try:
            ai_stats = _enrich_with_ai(
                today=result.properties,
                previous=prev_props,
                state_dir=Path(args.state_dir),
                today_iso=today_date,
            )
            logger.info("AI stats: %s", ai_stats)
        except Exception as e:  # noqa: BLE001
            logger.error("AI enrichment failed: %s — fallback to rule-based", e)

    # Convert to plain dict for JSON
    new_props = []
    current_ids: set[str] = set()
    for p in result.properties:
        d = p.model_dump()
        current_ids.add(d["id"])
        d["isNew"] = d["id"] not in prev_ids
        new_props.append(d)

    removed_count = len(prev_ids - current_ids)

    meta_out = result.meta.model_dump()
    meta_out["scoring"] = {
        "modelVersion": "v2.6",
        "withAi": use_ai,
        "costJpy": ai_stats["cost_jpy"] if ai_stats else 0.0,
        "aiCallCount": ai_stats["ai_call_count"] if ai_stats else 0,
        "fallbackCount": ai_stats["fallback_count"] if ai_stats else 0,
        "inheritedCount": ai_stats["inherited_count"] if ai_stats else 0,
    }

    output = {
        "properties": new_props,
        "meta": meta_out,
        "diff": {
            "newCount": sum(1 for p in new_props if p["isNew"]),
            "removedCount": removed_count,
            "totalPrev": len(prev_ids),
        },
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "elapsedSec": round(elapsed, 1),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info(
        "Wrote %d properties to %s (%.1fs)",
        len(output["properties"]), out_path, elapsed,
    )
    logger.info("By source: %s", output["meta"]["bySource"])
    logger.info(
        "Diff: +%d new, -%d removed (prev: %d)",
        output["diff"]["newCount"], removed_count, len(prev_ids),
    )

    if output["meta"].get("errors"):
        logger.warning("Errors: %s", output["meta"]["errors"])

    if len(output["properties"]) == 0:
        logger.error("No properties obtained from any site")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
