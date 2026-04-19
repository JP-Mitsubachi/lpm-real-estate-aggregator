"""Apply yield_estimator + scoring (v2.6) + persona_matcher to existing properties.json.

PGE Round 2 修正5:
    Round 1 の Generator が scrape.py を再実行しなかったため、本番 properties.json に
    personaMatches が一切付与されていなかった。Anthropic API 課金を避けつつペルソナ
    マッチング結果だけを反映するため、本スクリプトを使う。

v2.6 改修:
    yield_score を絶対利回りモードで再計算 → dealScore/dealRank も再計算する。
    AI 呼び出しは行わず、dealReasons は既存値を保持する。

実行内容:
    1. static/data/properties.json を読み込み
    2. compute_medians で母集団中央値を再計算
    3. 各 Property について:
        - yieldGross が None なら yield_estimator で推計（信頼度ラベル付与）
        - yieldGross が None でなければ yieldEstimated = yieldGross / confidence = "actual"
        - score_property で yieldBenchmarkScore / dealScore / dealRank などを再計算
          （dealReasons は score_property が再生成するが、AI ではなく従来のテンプレ）
        - persona_matcher で 5 ペルソナ判定し personaMatches / personaStars を上書き
    4. v2.5 → v2.6 のランク移動マトリクスを表示
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
from services.config_loader import reset_cache as reset_config_cache  # noqa: E402
from services.medians import compute_medians  # noqa: E402
from services.persona_matcher import match_personas, reset_persona_cache  # noqa: E402
from services.scoring import score_property  # noqa: E402
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


def apply_persona_pipeline(
    props: list[Property],
) -> tuple[list[tuple[str, str]], list[Property]]:
    """yield_estimator + score_property (v2.6) + persona_matcher を全件に適用 (mutate).

    Returns:
        (rank_transitions, before_snapshots)
        - rank_transitions: 各物件の (旧 dealRank, 新 dealRank) リスト
          v2.5 → v2.6 のランク移動マトリクス算出用
        - before_snapshots: 旧 dealRank/dealScore を保持した shallow copy リスト
          （上位 5 件サンプル表示等で使用）
    """
    medians = compute_medians(props)

    rank_transitions: list[tuple[str, str]] = []
    before_snapshots: list[Property] = []

    for p in props:
        # 1. 旧 dealRank/Score を退避
        old_rank = p.dealRank or "N/A"
        old_score = p.dealScore
        old_yield_score = p.yieldBenchmarkScore
        old_dealReasons = list(p.dealReasons or [])
        before_snapshots.append(
            p.model_copy(update={"dealRank": old_rank, "dealScore": old_score})
        )

        # 2. yieldEstimated / yieldSourceConfidence を更新
        if p.yieldGross is not None:
            p.yieldEstimated = p.yieldGross
            p.yieldSourceConfidence = "actual"
        else:
            est, conf = estimate_yield_for_property(p, medians)
            p.yieldEstimated = est
            p.yieldSourceConfidence = conf

        # 3. v2.6 score_property で yield/loan/risk/location/dealScore/dealRank 再計算
        #    AI 呼び出しなし。dealReasons は既存値を保持（score_property がテンプレ
        #    再生成してしまうので、後で上書きで戻す）。
        score_property(p, medians=medians)

        # 4. dealReasons は既存 (LLM 由来含む) を尊重して戻す
        if old_dealReasons:
            p.dealReasons = old_dealReasons

        # 5. ペルソナ判定（v2.6 の dealRank を踏まえた上で再計算）
        matches, stars = match_personas(p)
        p.personaMatches = matches
        p.personaStars = stars

        new_rank = p.dealRank or "N/A"
        rank_transitions.append((old_rank, new_rank))

        # 旧スコアと比較ログ（DEBUG）
        if old_yield_score != p.yieldBenchmarkScore:
            logger.debug(
                "yield score changed for %s: %s -> %s (yld=%s, conf=%s)",
                p.id, old_yield_score, p.yieldBenchmarkScore,
                p.yieldEstimated, p.yieldSourceConfidence,
            )

    return rank_transitions, before_snapshots


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


# ============================================================================
# v2.6 受入条件 検証レポート
# ============================================================================

RANK_ORDER = ["S", "A", "B", "C", "D", "N/A"]


def report_v26_yield_score(props: list[Property]) -> dict:
    """source 別 yieldBenchmarkScore 分布."""
    by_source: dict[str, dict[str, int]] = {}
    for p in props:
        src = p.sourceName or "unknown"
        bucket = by_source.setdefault(src, {"total": 0, "ge15": 0, "ge20": 0, "ge25": 0, "ge30": 0})
        bucket["total"] += 1
        ys = p.yieldBenchmarkScore or 0
        if ys >= 15:
            bucket["ge15"] += 1
        if ys >= 20:
            bucket["ge20"] += 1
        if ys >= 25:
            bucket["ge25"] += 1
        if ys >= 30:
            bucket["ge30"] += 1
    return by_source


def report_rank_transitions(transitions: list[tuple[str, str]]) -> dict:
    """v2.5 → v2.6 ランク移動マトリクス."""
    matrix: dict[tuple[str, str], int] = Counter()
    for old, new in transitions:
        matrix[(old, new)] += 1
    return dict(matrix)


def report_s_rank_source_distribution(props: list[Property]) -> dict[str, int]:
    """新 v2.6 S ランクの sourceName 分布."""
    counter: Counter = Counter()
    for p in props:
        if p.dealRank == "S":
            counter[p.sourceName or "unknown"] += 1
    return dict(counter)


def report_new_s_promotions(
    props: list[Property],
    before: list[Property],
    *,
    target_sources: tuple[str, ...] = ("SUUMO", "ふれんず"),
    top_n: int = 5,
) -> list[dict]:
    """新たに S に昇格した SUUMO/ふれんず物件 上位 N 件サンプル."""
    promotions: list[dict] = []
    for new_p, old_p in zip(props, before):
        if (
            new_p.dealRank == "S"
            and old_p.dealRank != "S"
            and (new_p.sourceName or "") in target_sources
        ):
            promotions.append({
                "id": new_p.id,
                "name": new_p.name,
                "source": new_p.sourceName,
                "city": new_p.city,
                "yield": new_p.yieldGross or new_p.yieldEstimated,
                "yieldConf": new_p.yieldSourceConfidence,
                "yieldScore": new_p.yieldBenchmarkScore,
                "oldScore": old_p.dealScore,
                "newScore": new_p.dealScore,
                "oldRank": old_p.dealRank,
                "newRank": new_p.dealRank,
            })
    promotions.sort(key=lambda x: -(x.get("newScore") or 0))
    return promotions[:top_n]


def main() -> None:
    reset_config_cache()    # scoring.yaml 再ロード保証 (mode=absolute 反映)
    reset_persona_cache()  # personas.yaml 再ロード保証
    logger.info("Loading %s", PROPERTIES_JSON)
    props, raw = load_properties(PROPERTIES_JSON)
    logger.info("Loaded %d properties", len(props))

    logger.info("Applying yield_estimator + score_property (v2.6) + persona_matcher pipeline...")
    transitions, before_snapshots = apply_persona_pipeline(props)

    # シリアライズして JSON に書き戻す
    new_props_dicts = serialize_properties(props, raw.get("properties", []))
    raw["properties"] = new_props_dicts

    PROPERTIES_JSON.write_text(
        json.dumps(raw, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Updated %s with %d properties.", PROPERTIES_JSON, len(props))

    # ペルソナ集計レポート（既存）
    stats = report_stats(props)
    print()
    print("==================== Persona Matching Report ====================")
    print(f"Total properties        : {stats['total']}")
    print(f"Unmatched              : {stats['unmatched']} ({stats['unmatched_pct']:.1f}%)")
    print(f"Premium (>=3 personas) : {stats['premium_3plus_personas']}")
    print()
    print("By persona:")
    for persona, count in sorted(stats["by_persona"].items(), key=lambda x: -x[1]):
        print(f"  {persona:<16} : {count:>5}")
    print()
    print("By source (matched / total / pct):")
    for source in sorted(stats["by_source_total"]):
        tot = stats["by_source_total"][source]
        matched = stats["by_source_matched"].get(source, 0)
        pct = stats["by_source_matched_pct"][source]
        print(f"  {source:<10} : {matched:>5} / {tot:>5}  ({pct:5.1f}%)")
    print()
    print("Yield source confidence distribution:")
    for conf, count in sorted(stats["yield_confidence"].items(), key=lambda x: -x[1]):
        print(f"  {conf:<10} : {count:>5}")
    print()
    print("Match count distribution (n_personas: count):")
    for n, count in stats["match_count_distribution"].items():
        print(f"  {n} match       : {count:>5}")
    print("=================================================================")

    # ============================================================
    # v2.6 受入条件 検証
    # ============================================================
    print()
    print("==================== v2.6 yield_score Validation ===============")
    yield_dist = report_v26_yield_score(props)
    print()
    print("Source-wise yieldBenchmarkScore distribution:")
    print(f"  {'source':<10} {'total':>7} {'>=15':>6} {'>=20':>6} {'>=25':>6} {'>=30':>6}")
    for src in sorted(yield_dist):
        b = yield_dist[src]
        print(
            f"  {src:<10} {b['total']:>7} {b['ge15']:>6} {b['ge20']:>6} "
            f"{b['ge25']:>6} {b['ge30']:>6}"
        )
    # 受入条件: SUUMO で yield_score>=15 が 50件以上
    suumo_ge15 = yield_dist.get("SUUMO", {}).get("ge15", 0)
    print()
    print(f"  AC-CHECK: SUUMO yield>=15 count = {suumo_ge15}  "
          f"({'PASS' if suumo_ge15 >= 50 else 'FAIL (need >= 50)'})")

    # v2.5 → v2.6 ランク移動マトリクス
    print()
    print("v2.5 -> v2.6 dealRank transition matrix (count):")
    matrix = report_rank_transitions(transitions)
    header = "  old\\new " + "".join(f"{r:>6}" for r in RANK_ORDER)
    print(header)
    for old in RANK_ORDER:
        row = f"  {old:<7}"
        for new in RANK_ORDER:
            row += f"{matrix.get((old, new), 0):>6}"
        print(row)

    # v2.5 S → v2.6 ダウン件数
    s_to_lower = sum(
        cnt for (old, new), cnt in matrix.items()
        if old == "S" and new != "S"
    )
    s_kept = matrix.get(("S", "S"), 0)
    print()
    print(f"  v2.5 S total  : {s_to_lower + s_kept}")
    print(f"  v2.6 S kept   : {s_kept}")
    print(f"  v2.6 S downed : {s_to_lower}")

    # v2.6 S ランク source 分布
    print()
    print("v2.6 S rank by source (HOME'S 100% 解消確認):")
    s_dist = report_s_rank_source_distribution(props)
    s_total = sum(s_dist.values())
    print(f"  total v2.6 S = {s_total}")
    for src in sorted(s_dist, key=lambda s: -s_dist[s]):
        cnt = s_dist[src]
        pct = cnt / s_total * 100 if s_total else 0
        print(f"  {src:<10} : {cnt:>5}  ({pct:5.1f}%)")

    # 新 S 昇格 (SUUMO/ふれんず) 上位5件
    print()
    print("New S promotions (SUUMO/ふれんず, top 5):")
    promotions = report_new_s_promotions(props, before_snapshots, top_n=5)
    if not promotions:
        print("  (none — no SUUMO/ふれんず物件 newly promoted to S)")
    else:
        for i, item in enumerate(promotions, 1):
            print(
                f"  [{i}] {item['source']} {item['id']}  {item['city']}  "
                f"yield={item['yield']} ({item['yieldConf']}) "
                f"y_score={item['yieldScore']}  "
                f"score {item['oldScore']}->{item['newScore']}  "
                f"rank {item['oldRank']}->{item['newRank']}"
            )
            print(f"      name: {item['name'][:60]}")
    print("=================================================================")


if __name__ == "__main__":
    main()
