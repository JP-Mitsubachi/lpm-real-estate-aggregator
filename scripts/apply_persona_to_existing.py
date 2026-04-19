"""Apply yield_estimator + persona_matcher to existing properties.json (no AI calls).

PGE Round 2 修正5:
    Round 1 の Generator が scrape.py を再実行しなかったため、本番 properties.json に
    personaMatches が一切付与されていなかった。Anthropic API 課金を避けつつペルソナ
    マッチング結果だけを反映するため、本スクリプトを使う。

実行内容:
    1. static/data/properties.json を読み込み
    2. compute_medians で母集団中央値を再計算
    3. 各 Property について:
        - yieldGross が None なら yield_estimator で推計（信頼度ラベル付与）
        - yieldGross が None でなければ yieldEstimated = yieldGross / confidence = "actual"
        - persona_matcher で 5 ペルソナ判定し personaMatches / personaStars を上書き
    4. dealReasons / dealScore / dealRank などその他のフィールドは保持
    5. JSON を書き戻す

実行:
    cd L-008-deploy
    python scripts/apply_persona_to_existing.py
"""
from __future__ import annotations

import json
import logging
import sys
from collections import Counter
from pathlib import Path

# scripts/ から実行されるので、プロジェクトルートを sys.path に追加
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from models import Property  # noqa: E402
from services.medians import compute_medians  # noqa: E402
from services.persona_matcher import match_personas, reset_persona_cache  # noqa: E402
from services.yield_estimator import estimate_yield_for_property  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("apply_persona")


PROPERTIES_JSON = ROOT / "static" / "data" / "properties.json"


def load_properties(path: Path) -> tuple[list[Property], dict]:
    """properties.json を読み込み、Property リストとトップレベル辞書を返す."""
    if not path.exists():
        raise FileNotFoundError(f"properties.json not found: {path}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    props_raw = raw.get("properties", [])
    parsed: list[Property] = []
    skipped = 0
    for p in props_raw:
        # downstream フィールド (isNew 等) は無視
        clean = {k: v for k, v in p.items() if k != "isNew"}
        try:
            parsed.append(Property(**clean))
        except Exception as e:  # noqa: BLE001
            skipped += 1
            logger.debug("Skipped invalid property %s: %s", p.get("id"), e)
    if skipped:
        logger.warning("Skipped %d invalid properties", skipped)
    return parsed, raw


def apply_persona_pipeline(props: list[Property]) -> None:
    """yield_estimator + persona_matcher を全件に適用 (mutate)."""
    medians = compute_medians(props)

    for p in props:
        if p.yieldGross is not None:
            p.yieldEstimated = p.yieldGross
            p.yieldSourceConfidence = "actual"
        else:
            est, conf = estimate_yield_for_property(p, medians)
            p.yieldEstimated = est
            p.yieldSourceConfidence = conf

        matches, stars = match_personas(p)
        p.personaMatches = matches
        p.personaStars = stars


def serialize_properties(props: list[Property], original_raw: list[dict]) -> list[dict]:
    """Property -> dict 変換。既存 isNew 等のフィールドを保持。"""
    raw_by_id = {p.get("id"): p for p in original_raw if p.get("id")}
    out: list[dict] = []
    for p in props:
        d = p.model_dump()
        original = raw_by_id.get(p.id, {})
        # downstream フィールドを保持（既存 isNew があれば残す）
        for key in ("isNew",):
            if key in original:
                d[key] = original[key]
        out.append(d)
    return out


def report_stats(props: list[Property]) -> dict:
    """ペルソナマッチング結果の集計レポート."""
    persona_counts: Counter = Counter()
    source_total: Counter = Counter()
    source_matched: Counter = Counter()
    yield_conf_counts: Counter = Counter()
    multi_match_counts: Counter = Counter()
    unmatched_count = 0

    for p in props:
        matches = p.personaMatches or []
        source = p.sourceName or "unknown"
        source_total[source] += 1
        yield_conf_counts[p.yieldSourceConfidence or "none"] += 1

        if not matches:
            unmatched_count += 1
        else:
            source_matched[source] += 1
            for m in matches:
                persona_counts[m] += 1
        multi_match_counts[len(matches)] += 1

    total = len(props)
    return {
        "total": total,
        "unmatched": unmatched_count,
        "unmatched_pct": (unmatched_count / total * 100) if total else 0,
        "by_persona": dict(persona_counts),
        "by_source_total": dict(source_total),
        "by_source_matched": dict(source_matched),
        "by_source_matched_pct": {
            s: (source_matched[s] / source_total[s] * 100) if source_total[s] else 0
            for s in source_total
        },
        "yield_confidence": dict(yield_conf_counts),
        "match_count_distribution": dict(sorted(multi_match_counts.items())),
        "premium_3plus_personas": sum(
            v for k, v in multi_match_counts.items() if k >= 3
        ),
    }


def main() -> None:
    reset_persona_cache()  # YAML 再ロード保証
    logger.info("Loading %s", PROPERTIES_JSON)
    props, raw = load_properties(PROPERTIES_JSON)
    logger.info("Loaded %d properties", len(props))

    logger.info("Applying yield_estimator + persona_matcher pipeline...")
    apply_persona_pipeline(props)

    # シリアライズして JSON に書き戻す
    new_props_dicts = serialize_properties(props, raw.get("properties", []))
    raw["properties"] = new_props_dicts

    PROPERTIES_JSON.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Updated %s with %d properties.", PROPERTIES_JSON, len(props))

    # 集計レポート
    stats = report_stats(props)
    print()
    print("==================== Persona Matching Report ====================")
    print(f"Total properties        : {stats['total']}")
    print(f"Unmatched              : {stats['unmatched']} ({stats['unmatched_pct']:.1f}%)")
    print(f"Premium (≥3 personas)  : {stats['premium_3plus_personas']}")
    print()
    print("By persona:")
    for persona, count in sorted(stats["by_persona"].items(), key=lambda x: -x[1]):
        print(f"  {persona:<16} : {count:>5}")
    print()
    print("By source (matched / total / pct):")
    for source in sorted(stats["by_source_total"]):
        total = stats["by_source_total"][source]
        matched = stats["by_source_matched"].get(source, 0)
        pct = stats["by_source_matched_pct"][source]
        print(f"  {source:<10} : {matched:>5} / {total:>5}  ({pct:5.1f}%)")
    print()
    print("Yield source confidence distribution:")
    for conf, count in sorted(stats["yield_confidence"].items(), key=lambda x: -x[1]):
        print(f"  {conf:<10} : {count:>5}")
    print()
    print("Match count distribution (n_personas: count):")
    for n, count in stats["match_count_distribution"].items():
        print(f"  {n} match       : {count:>5}")
    print("=================================================================")


if __name__ == "__main__":
    main()
