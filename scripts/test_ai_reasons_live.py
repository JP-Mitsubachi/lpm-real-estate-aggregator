"""てるさん手動実行用: Claude Haiku 4.5 で 5件サンプル生成 (B3 dry-run).

使い方:
    cd /Users/manhui/Desktop/Teru_comapny/company/engineering/prototypes/L-008-deploy
    export ANTHROPIC_API_KEY=sk-ant-...
    python scripts/test_ai_reasons_live.py

オプション:
    --mock           実 API ではなく mock 応答を返す (動作確認用)
    --count N        生成数 (default 5)
    --rank S         指定ランクから抽出 (default S)
    --reset-ledger   テスト用 ledger をリセット

テスト用 ledger は本番と分離: data/state/cost_ledger_test_YYYY-MM.json
本番 cost_ledger に影響を与えない。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from models import Property  # noqa: E402
from services.ai_reasons import (  # noqa: E402
    AnthropicClient,
    estimate_monthly_jpy,
    generate_reasons_for_property,
)
from services.cost_ledger import CostLedger  # noqa: E402
from services.scoring import score_property  # noqa: E402

PROPERTIES_JSON = REPO_ROOT / "static" / "data" / "properties.json"
LEDGER_DIR = REPO_ROOT / "data" / "state"
TODAY = "2026-04-19"


def _check_api_key(use_mock: bool) -> None:
    if use_mock:
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ERROR: ANTHROPIC_API_KEY 環境変数が未設定です。")
        print("以下のように設定してから再実行してください:")
        print('  export ANTHROPIC_API_KEY="sk-ant-..."')
        print("または mock 実行:")
        print("  python scripts/test_ai_reasons_live.py --mock")
        sys.exit(1)


def _load_props() -> list[Property]:
    data = json.loads(PROPERTIES_JSON.read_text(encoding="utf-8"))
    out = []
    for raw in data.get("properties", []):
        try:
            out.append(Property(**raw))
        except Exception:
            continue
    return out


def _select_samples(props: list[Property], rank: str, n: int) -> list[Property]:
    """指定ランクから n 件、city バリエーションを意識して抽出."""
    matched = [p for p in props if p.dealRank == rank]
    if not matched:
        # 指定ランクが空なら A → B → C → all の順でフォールバック
        for fallback in ("A", "B", "C"):
            matched = [p for p in props if p.dealRank == fallback]
            if matched:
                print(f"⚠️  ランク {rank} が0件、ランク {fallback} で代替")
                break
    if not matched:
        matched = props
    # city ばらして上位 n 件
    picked: list[Property] = []
    seen_cities: set[str] = set()
    for p in sorted(matched, key=lambda x: -(x.compositeRankValue or x.dealScore or 0)):
        key = p.city
        if key in seen_cities and len(picked) < n - 1:
            continue
        picked.append(p)
        seen_cities.add(key)
        if len(picked) >= n:
            break
    return picked[:n]


def _format_property_header(p: Property) -> str:
    price_man = f"{p.price/10000:.0f}万円" if p.price else "—"
    walk = f"徒歩{p.walkMinutes}分" if p.walkMinutes is not None else "—"
    return (
        f"[{p.dealRank}/{p.dealScore}] {p.city} / {p.propertyType} / {price_man} / "
        f"{p.structure or '構造?'}築{p.age}年 / 利回{p.yieldGross}% / {walk}"
    )


class _MockClient(AnthropicClient):
    """--mock 用: 実 API を呼ばずに固定応答 + 単価計算だけ走らせる."""

    def __init__(self) -> None:
        super().__init__(strict=False)
        self.enabled = True  # mock として有効化
        self._call_count = 0

    def call(self, *, system_blocks, user_text, max_tokens):
        self._call_count += 1
        return (
            "立地・融資・収益の3軸で総合判断、Aランク水準の物件です。\n"
            "ベンチマーク利回りに対し正の乖離があり、市場対比で割安寄りです。\n"
            "ただし築年経過分の修繕履歴は問合せ時に必ず確認すべきです。",
            {"input_tokens": 100, "output_tokens": 80,
             "cache_read_input_tokens": 500, "cache_creation_input_tokens": 0},
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="B3 dry-run: Claude Haiku で 5件サンプル生成")
    parser.add_argument("--mock", action="store_true", help="実 API を使わない mock モード")
    parser.add_argument("--count", type=int, default=5, help="サンプル数")
    parser.add_argument("--rank", default="S", help="抽出元ランク (default: S)")
    parser.add_argument("--reset-ledger", action="store_true", help="テスト ledger 初期化")
    args = parser.parse_args()

    _check_api_key(args.mock)

    print("=" * 70)
    print(f"L-008 B3 dry-run ({'MOCK' if args.mock else 'LIVE API'})")
    print("=" * 70)

    # 月額試算
    est_jpy = estimate_monthly_jpy()
    print(f"\n📊 月額試算 (config 由来): ¥{est_jpy:,.0f}")
    print(f"   想定: 500件/日 × 30日 × 差分継承70% = 4,500件/月 API 呼び出し")
    print(f"   予算ハードリミット: ¥1,000")
    if est_jpy > 1000:
        print(f"   ⚠️  config 試算が予算超過: 単価/トークン数を見直し")
    else:
        print(f"   ✅ config 試算が予算内")

    # ledger 準備
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    if args.reset_ledger:
        for f in LEDGER_DIR.glob("cost_ledger_test_*.json"):
            f.unlink()
            print(f"🧹 削除: {f}")

    test_ledger_dir = LEDGER_DIR / "test"
    test_ledger_dir.mkdir(parents=True, exist_ok=True)
    ledger = CostLedger(state_dir=test_ledger_dir, today=TODAY, budget_jpy=1000)
    print(f"\n💰 テスト ledger: {test_ledger_dir / f'cost_ledger_{TODAY[:7]}.json'}")
    print(f"   開始残: ¥{ledger.remaining_jpy():,.0f}")

    # 物件抽出
    print(f"\n🏠 物件読み込み...")
    all_props = _load_props()
    # スコアリング (新フィールドが None ならここで埋める)
    for p in all_props:
        if p.dealScore is None:
            score_property(p)
    samples = _select_samples(all_props, args.rank, args.count)
    print(f"   {args.rank} ランクから {len(samples)} 件抽出")

    # クライアント
    client: AnthropicClient
    if args.mock:
        client = _MockClient()
        print("   🧪 MOCK クライアント使用 (実 API 呼ばず)\n")
    else:
        client = AnthropicClient(strict=True)
        if not client.enabled:
            print("   ❌ AnthropicClient が無効化されています (環境変数 / SDK 確認)")
            return 1
        from services.ai_reasons import _resolved_model
        print(f"   🌐 LIVE API: {_resolved_model()}\n")

    # 生成
    print("-" * 70)
    for i, p in enumerate(samples, 1):
        print(f"\n#{i} {_format_property_header(p)}")
        before_jpy = ledger.current_jpy()
        scored = generate_reasons_for_property(p, ledger=ledger, client=client)
        after_jpy = ledger.current_jpy()
        delta = after_jpy - before_jpy
        for j, r in enumerate(scored.dealReasons, 1):
            print(f"   {j}. {r}")
        flag = "🤖 AI" if not scored.isAutoFallback else "🔧 fallback (機械文)"
        print(f"   {flag} | コスト: ¥{delta:.2f} | 累計: ¥{after_jpy:.2f}")

    # 集計
    print("\n" + "=" * 70)
    print("📈 集計")
    print(f"   API 累計呼び出し: {ledger.call_count()} 回")
    print(f"   累計コスト: ¥{ledger.current_jpy():.2f}")
    print(f"   残予算: ¥{ledger.remaining_jpy():,.2f}")
    if ledger.call_count() > 0:
        avg = ledger.current_jpy() / ledger.call_count()
        print(f"   平均1呼出: ¥{avg:.2f}")
    print("=" * 70)
    print("\n✅ dry-run 完了。dealReasons が日本語として自然か目視確認してください。")
    print("   不自然なら system prompt (services/ai_reasons.py) を再調整。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
