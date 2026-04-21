# L-008 不動産投資物件アグリゲーター

福岡県の投資物件を **HOME'S / SUUMO / ふれんず** から横断取得し、AI スコアリング + 投資家ペルソナマッチングで「誰にとっての掘り出し物か」を可視化する個人プロジェクトです。

- **配信:** GitHub Pages（毎日 JST 6:00 スクレイプ + 自動デプロイ）
- **バージョン:** v2.6（2026-04 時点）
- **物件数:** 約 4,000〜4,500件（HOME'S 約1,485 + SUUMO 約1,370 + ふれんず 約1,316）
- **PRD:** `company/pm/projects/L-008-real-estate-aggregator.md`

## 主な機能

- **5軸スコアリング**（立地30 + 収益30 + 融資20 + 滞留10 + リスク10 = 100）
  - 絶対値 yield 評価（v2.6 新方式、SUUMO/ふれんず対応）
  - dealRank S/A/B/C/D + 複合ガード
- **5ペルソナマッチング**（インカム / 融資戦略 / キャピタル / 立地特化 / 築古再生）
- **プレミアム物件抽出**（3ペルソナ以上マッチ = 多視点で優良）
- **AI 根拠生成**（Claude Haiku 4.5、月¥1,000上限の cost ledger 付き）
- **ランクフィルタ / ソート / お気に入り** UI（レスポンシブ対応）
- **スコアリング解説 LP**（`/scoring-explainer.html`、ロジックを視覚化）

## 技術スタック

- **Python 3.11** — Pydantic v2 / Playwright / httpx / Anthropic SDK
- **フロントエンド** — Tailwind CSS (CDN) / バニラ JavaScript / SVG（ビルドツール不要）
- **配信** — GitHub Pages
- **CI** — GitHub Actions（scrape-daily.yml で毎日バッチ + Pages デプロイ）

## ディレクトリ構成

```
.
├── models.py                  # Pydantic Property モデル
├── scrape.py                  # スクレイプ + スコアリング + AI 生成のオーケストレーター
├── scrapers/                  # サイト別スクレイパー（HOME'S / SUUMO / ふれんず）
├── services/
│   ├── scoring.py             # 5軸スコアリング v2.6
│   ├── persona_matcher.py     # 5ペルソナ判定 v2.6.x
│   ├── yield_estimator.py     # エリア中央値ベース yield 推計（B案）
│   ├── ai_reasons.py          # Claude Haiku 4.5 での dealReasons 生成
│   ├── cost_ledger.py         # 月次予算ガード
│   └── ...
├── config/
│   ├── scoring.yaml           # 配点・閾値（Sランク複合ガード含む）
│   └── personas.yaml          # 5ペルソナの MUST/PREFER/NEVER
├── scripts/
│   ├── apply_persona_to_existing.py  # AI 呼び出しなしで JSON 再計算
│   └── eval_scoring_v21.py    # Top10 検証スクリプト
├── tests/                     # pytest（現状 400+ 件）
├── static/
│   ├── index.html             # メインアプリ UI
│   ├── scoring-explainer.html # AI スコアリング解説 LP
│   └── data/properties.json   # 本番データ（CI が毎日更新）
└── .github/workflows/
    ├── scrape-daily.yml       # 毎日バッチ + Pages デプロイ
    └── deploy-pages.yml       # HTML/CSS 変更時のみ Pages デプロイ
```

## ローカル実行

```bash
# 依存インストール
pip install -r requirements.txt
playwright install chromium

# ローカルサーバー起動
cd static && python -m http.server 8765
# → http://localhost:8765

# スクレイプ（AI なし、開発時）
python scrape.py

# 既存 properties.json に scoring + persona を再適用（AI 呼ばない、無料）
python scripts/apply_persona_to_existing.py

# テスト
pytest -q
```

## 利用規約・免責事項（重要）

**本プロジェクトは、長野満輝（以下「作者」）個人の投資判断を補助する目的で開発された非公開のプロトタイプです。** 以下の条件を遵守してください。

### 個人利用限定

- 本システムおよび配信される HTML / JSON データは **作者個人の検証・調査目的にのみ利用**されます
- 第三者への提供・共有・公衆送信・商用利用は禁止します
- **再配布禁止**: 本リポジトリのフォーク、コードの転載、データのダウンロード後の公開配布は行わないでください
- GitHub Pages による配信は技術検証目的で公開しているものであり、サービス提供ではありません

### 収集データの取り扱い

- 収集する情報は **物件情報**（価格・利回り・所在地・間取り・築年・画像 URL 等）に限定し、**個人情報（物件所有者名・連絡先等）は収集・表示しません**
- 著作権法第30条の4「情報解析のための利用」の範囲内での情報解析を前提としています
- 各サイト（HOME'S / SUUMO / ふれんず）の robots.txt および利用規約を尊重し、違反が判明した場合は直ちに取得を停止します
- 収集データを第三者に販売する予定はありません

### スコープ外

以下のサイトはスクレイピングを行いません（`company/research/topics/2026-04-21-l008-kenbiya-reins-api-investigation.md` 参照）:

- **楽待** — 利用規約 第10条 第1項(6)号で「クローラー等によるアクセス」を明示禁止
- **健美家** — robots.txt で AI クローラー disallow + 利用規約非公開
- **レインズ** — 宅建業者ライセンス必須、API 提供なし、二次利用禁止

### 投資判断の免責

- 本システムのスコアリング・ランク・ペルソナマッチングは **投資判断の代替ではなく、一次スクリーニングの補助情報**です
- 作者は本システムの出力に基づく投資判断の結果について一切の責任を負いません
- 表示される価格・利回り・物件情報の正確性は保証されません（各ソースサイトの表示を優先してください）

### 将来の公開化について

将来、本プロジェクトを外部提供サービスとして公開する場合は、**各データソースサイトの利用規約を再確認**のうえ、必要な許諾を個別に取得します。それまでは本リポジトリは作者個人の検証用途にのみ使用されます。

## ライセンス

**本プロジェクトにオープンソースライセンスは適用されません。** 個人開発プロジェクトとして、作者の許諾なくコードの転載・改変・再配布を行うことを禁じます。

## 関連ドキュメント

- **PRD**: `company/pm/projects/L-008-real-estate-aggregator.md`
- **スコアリング設計**: `company/research/topics/2026-04-19-real-estate-scoring-research.md`
- **ペルソナ設計**: `company/research/topics/2026-04-19-real-estate-investor-personas-brief.md`
- **LP 設計**: `company/engineering/docs/L-008-scoring-explainer-design.md`
- **外部サイト API 調査**: `company/research/topics/2026-04-21-l008-kenbiya-reins-api-investigation.md`
