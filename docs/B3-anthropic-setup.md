# B3: Claude Haiku 4.5 セットアップ手順（てるさん向け）

L-008 v2.4 で Claude Haiku 4.5 による AI 根拠テキスト生成を有効化するための **手動設定** 手順。

## 0. 全体像

```
ローカル dry-run (Step 1-3)
    ↓ 動作 OK
Anthropic Console で予算上限設定 (Step 4)
    ↓
GitHub repo に Secret 登録 (Step 5)
    ↓ 自動
GHA scrape-daily.yml が --with-ai で実行 (Step 6)
    ↓
properties.json に AI 生成 reasons が反映
```

---

## Step 1. Anthropic Console で API key 発行

1. https://console.anthropic.com にアクセス（既存アカウントでログイン）
2. **API Keys** → `Create Key`
3. Key 名: `lpm-real-estate-aggregator-prod`
4. 表示された `sk-ant-api03-...` を**安全な場所にコピー**（再表示不可）

## Step 2. 月間予算上限を物理ガード（重要）

**コスト爆発を物理的に阻止するため必須。**

1. Console 上部メニュー → **Settings** → **Limits**
2. **Monthly spend limit**: `$10` （¥1,500 相当・PRD 月¥1,000 + バッファ余裕）
3. **Email alert at**: `$5` （¥750 で警告メール）
4. 保存

## Step 3. ローカル dry-run（5件サンプル）

```bash
cd /Users/manhui/Desktop/Teru_comapny/company/engineering/prototypes/L-008-deploy
export ANTHROPIC_API_KEY="sk-ant-api03-..."
python scripts/test_ai_reasons_live.py --count 5 --rank S
```

期待出力:
- 5件の物件に AI 生成 dealReasons (3行) が表示
- 各物件で「🤖 AI」マークと ¥X.XX のコスト
- 最後に累計 ¥（5件で ¥10 以下が目安）

**目視確認ポイント:**
- 日本語として自然か
- 禁止語（買うべき・おすすめ等）が含まれていないか
- 数値がプロンプト値と一致しているか
- 行3に懸念点が必ず含まれているか

不自然なら **`services/ai_reasons.py` の `_SYSTEM_PROMPT`** を再調整。

mock テストだけしたい場合（API 課金なし）:
```bash
python scripts/test_ai_reasons_live.py --mock --count 3
```

## Step 4. GitHub Secret 登録

1. GitHub: https://github.com/jp-mitsubachi/lpm-real-estate-aggregator/settings/secrets/actions
2. **New repository secret**
3. Name: `ANTHROPIC_API_KEY`
4. Value: Step 1 でコピーした `sk-ant-api03-...`
5. 保存

## Step 5. GHA workflow_dispatch で初回テスト実行

1. GitHub: Actions → **Daily Scrape** → `Run workflow`
2. Branch: `main` を選択して実行
3. ジョブログで以下を確認:
   - `pip install -r requirements.txt` で `anthropic` がインストールされる
   - `python scrape.py --with-ai` が完走する
   - 「No changes to commit」または「daily scrape ...」commit が走る
   - GitHub Pages デプロイが緑

## Step 6. 月初の運用チェック（毎月 1 日）

1. **Anthropic Console** → Usage で当月利用額を確認
2. リポジトリの `data/state/cost_ledger_YYYY-MM.json` と突合（差分10%以内なら OK）
3. 超過傾向なら `config/scoring.yaml` の `ai_reasons.estimation.diff_inheritance_rate` を上げる（差分継承率）

---

## トラブルシュート

### dry-run で「ANTHROPIC_API_KEY 未設定」
→ `export ANTHROPIC_API_KEY=sk-ant-...` を同じシェルで実行してから再試行

### dry-run で全件「🔧 fallback」
→ API key 不正、もしくは予算超過。ledger ファイルを `--reset-ledger` でクリア

### GHA で「Resource not accessible by integration」
→ Secret 登録漏れ。Settings → Secrets and variables → Actions に `ANTHROPIC_API_KEY` があるか確認

### コスト試算と実績が大きく乖離
→ Anthropic SDK のバージョンを確認（>=0.40 必須）。prompt caching が効いていない可能性

---

## 参考

- 月予算試算: `config/scoring.yaml` の `ai_reasons` セクション
- コード: `services/ai_reasons.py`
- 単価: $1/M input + $5/M output + Cache hit $0.10/M（Haiku 4.5）
- PRD §5.3.3 で月¥1,000 上限を物理ガード（ledger）
- PRD §5.3.4 で4種類の失敗パターン（401/429/timeout/予算超過）すべて fallback に流れる
