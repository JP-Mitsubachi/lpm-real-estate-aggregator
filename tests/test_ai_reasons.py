"""Tests for services/ai_reasons.py — Claude Haiku 4.5 batch enrichment (E-4).

PRD §5.3.2 + §5.3.3 + §5.3.4 / AC-010-1〜AC-010-3 に準拠。

テストはすべて mock。実 API は scripts/test_ai_reasons_live.py 側で別管理。

戦略:
- AnthropicClient ラッパーは ANTHROPIC_API_KEY がない/モック注入で完全制御
- 8 失敗ケース（401/429/timeout/予算超過/禁止語/不確実語/字数/JSONパース）+
  正常系3 + 差分検出2 + コスト推定2 + cache_control 検証1 + isAutoFallback 1 +
  リトライ動作 + 行数不足 + 規定どおり1回再生成
- mock のレスポンス整形: messages.create.return_value に
  Mock(usage=Mock(...), content=[Mock(text="...")]) を貼る
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Property  # noqa: E402
from services.cost_ledger import CostLedger  # noqa: E402


# --- fixtures ---------------------------------------------------------------


def _make_property(**overrides) -> Property:
    """Sランク級の物件を1件作る（dealReasons 上書きの起点）."""
    base = dict(
        id="suumo-1",
        name="博多駅徒歩5分・RC築12年",
        price=28_000_000,
        yieldGross=8.5,
        prefecture="福岡県",
        city="福岡市博多区",
        propertyType="区分マンション",
        builtYear=2014,
        age=12,
        structure="RC",
        area=42.0,
        nearestStation="JR鹿児島本線「博多」徒歩5分",
        layout="1LDK",
        dealScore=85,
        dealRank="S",
        locationScore=30,
        yieldBenchmarkScore=22,
        loanScore=20,
        riskScore=10,
        walkMinutes=5,
        locationGrade="S",
        lineRank="A",
        remainingDurableYears=35,
        benchmarkCapRate=6.0,
        dealReasons=[
            "福岡市博多区・路線A・徒歩5分の立地はSランクです。",
            "RC築12年で残存35年、長期融資が見込めます。",
            "表面8.5%はベンチマーク6.0%に対し+42%、市場との乖離を狙える水準です。",
        ],
        dealModelVersion="v2.5",
    )
    base.update(overrides)
    return Property(**base)


def _mock_anthropic_response(text: str, input_tokens: int = 50, output_tokens: int = 120,
                             cache_read: int = 0, cache_creation: int = 0):
    """anthropic SDK の messages.create() の戻り値風 mock."""
    msg = MagicMock()
    msg.content = [MagicMock(text=text, type="text")]
    msg.usage = MagicMock(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=cache_creation,
    )
    msg.id = "msg_mock"
    msg.model = "claude-haiku-4-5-20251001"
    msg.stop_reason = "end_turn"
    return msg


_GOOD_LINES = [
    # 各 40-50字、合計 120-180字 を満たす
    "福岡市博多区・路線A・徒歩5分の立地は希少なSランク評価で投資妙味の高いエリアです。",
    "RC築12年で残存耐用年数35年、地銀含む長期融資が射程に入るバランスの良い物件です。",
    "表面8.5%はベンチマーク6.0%に対し+42%、市場との乖離が大きい優良水準と言えます。",
]


def _good_reasons_text() -> str:
    """validate_reasons 通過する3行（各40-60字 / 合計120-180字）."""
    return "\n".join(_GOOD_LINES)


# ============================================================================
# C1-1: estimate_monthly_jpy() helper
# ============================================================================

def test_estimate_monthly_jpy_default_under_budget():
    """500件/日 × 30日 のデフォルト試算が ¥1000 以下に収まる."""
    from services.ai_reasons import estimate_monthly_jpy
    jpy = estimate_monthly_jpy()
    assert jpy < 1000, f"monthly estimate must be < ¥1000, got ¥{jpy:.0f}"


def test_estimate_monthly_jpy_with_explicit_volume():
    """500件/日 × 30日 を明示しても 1000 円以下."""
    from services.ai_reasons import estimate_monthly_jpy
    jpy = estimate_monthly_jpy(daily_property_count=500, days_per_month=30)
    assert jpy < 1000


def test_estimate_monthly_jpy_scales_with_volume():
    """件数2倍で 1.5倍以上スケールすること（キャッシュ効率は同一）."""
    from services.ai_reasons import estimate_monthly_jpy
    base = estimate_monthly_jpy(daily_property_count=500, days_per_month=30)
    doubled = estimate_monthly_jpy(daily_property_count=1000, days_per_month=30)
    assert doubled > base * 1.5


# ============================================================================
# C1-2: validate_reasons (禁止語・不確実語・行数・字数)
# ============================================================================

def test_validate_reasons_passes_for_good_text():
    from services.ai_reasons import validate_reasons
    ok, reason = validate_reasons(list(_GOOD_LINES))
    assert ok, f"expected pass, got reject: {reason}"


@pytest.mark.parametrize("forbidden", [
    "買うべき", "おすすめ", "絶対", "必ず儲かる", "確実", "お得", "今が買い時", "推奨", "お買い得",
])
def test_validate_reasons_blocks_all_forbidden_words(forbidden):
    """PRD AC-010-2 リスト準拠 — 禁止語1語でも含めば reject."""
    from services.ai_reasons import validate_reasons
    reasons = list(_GOOD_LINES)
    # 1行目の末尾に禁止語を仕込む（合計字数は範囲を維持できるよう短縮で調整）
    reasons[0] = f"福岡市博多区・路線A・徒歩5分立地はSランク、{forbidden}と言える優良エリアです。"
    ok, reason = validate_reasons(reasons)
    assert not ok
    assert "禁止語" in reason or "forbidden" in reason.lower()


@pytest.mark.parametrize("uncertain", [
    "おそらく", "と思われる", "通常は", "一般的に",
])
def test_validate_reasons_blocks_uncertain_words(uncertain):
    """不確実語も reject."""
    from services.ai_reasons import validate_reasons
    reasons = list(_GOOD_LINES)
    reasons[0] = f"福岡市博多区・路線A・徒歩5分は{uncertain}Sランク評価で投資妙味あるエリア。"
    ok, reason = validate_reasons(reasons)
    assert not ok


def test_validate_reasons_rejects_two_lines():
    """3行未満は reject."""
    from services.ai_reasons import validate_reasons
    reasons = [
        "福岡市博多区・路線A・徒歩5分の立地はSランク投資妙味があります。",
        "RC築12年で残存35年、長期融資が射程圏に入る物件です。",
    ]
    ok, reason = validate_reasons(reasons)
    assert not ok
    assert "行数" in reason or "lines" in reason.lower()


def test_validate_reasons_rejects_four_lines():
    """3行超えも reject."""
    from services.ai_reasons import validate_reasons
    reasons = [
        "福岡市博多区・路線A・徒歩5分の立地はSランクで投資妙味があります。",
        "RC築12年で残存35年、長期融資が射程圏のバランス良物件です。",
        "表面8.5%はベンチマーク6.0%に対し+42%、市場との乖離が大きい水準です。",
        "余分な4行目です。",
    ]
    ok, _ = validate_reasons(reasons)
    assert not ok


def test_validate_reasons_rejects_total_over_180_chars():
    """合計 > 180 字 reject."""
    from services.ai_reasons import validate_reasons
    long = "あ" * 70
    reasons = [long, long, long]
    ok, reason = validate_reasons(reasons)
    assert not ok
    assert "字数" in reason or "char" in reason.lower()


def test_validate_reasons_rejects_total_under_120_chars():
    """合計 < 120 字も reject (3行ともショートな機械文除外)."""
    from services.ai_reasons import validate_reasons
    short = "短い行"  # 3 chars * 3 = 9 chars total
    reasons = [short, short, short]
    ok, _ = validate_reasons(reasons)
    assert not ok


def test_validate_reasons_accepts_120_char_boundary():
    """ちょうど 120字 (合計) は OK."""
    from services.ai_reasons import validate_reasons
    line = "あ" * 40  # 各40字 × 3 = 120字
    ok, _ = validate_reasons([line, line, line])
    assert ok


# ============================================================================
# C1-3: AnthropicClient ラッパー
# ============================================================================

def test_anthropic_client_init_requires_api_key(monkeypatch):
    """ANTHROPIC_API_KEY 未設定で AnthropicClient(strict=True) は ValueError."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from services.ai_reasons import AnthropicClient
    with pytest.raises(ValueError):
        AnthropicClient(strict=True)


def test_anthropic_client_init_optional_when_no_key(monkeypatch):
    """key 無しでも strict=False なら enabled=False で構築できる（fallback専用モード）."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    from services.ai_reasons import AnthropicClient
    cli = AnthropicClient(strict=False)
    assert cli.enabled is False


# ============================================================================
# C1-4: generate_reasons_for_property — 正常応答
# ============================================================================

def test_generate_reasons_property_normal_response(tmp_path, monkeypatch):
    """mock 200 OK → 3行根拠を Property.dealReasons に上書き."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key-fake")
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property

    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()

    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.return_value = _mock_anthropic_response(_good_reasons_text())

    out = generate_reasons_for_property(p, ledger=led, client=cli)

    assert len(out.dealReasons) == 3
    assert out.isAutoFallback is False
    assert led.call_count() == 1
    # API was actually called once
    assert cli._client.messages.create.call_count == 1


def test_generate_reasons_property_normal_writes_aiReasonsGeneratedAt(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()
    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.return_value = _mock_anthropic_response(_good_reasons_text())
    out = generate_reasons_for_property(p, ledger=led, client=cli)
    assert out.aiReasonsGeneratedAt is not None and out.aiReasonsGeneratedAt.endswith("Z")


def test_generate_reasons_cache_control_present_in_system(tmp_path, monkeypatch):
    """system プロンプトの最後ブロックに cache_control: ephemeral が付与されている."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key-fake")
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property

    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()

    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.return_value = _mock_anthropic_response(_good_reasons_text())

    generate_reasons_for_property(p, ledger=led, client=cli)

    call_args = cli._client.messages.create.call_args
    kwargs = call_args.kwargs
    sys_param = kwargs.get("system")
    assert isinstance(sys_param, list), f"system must be a list of blocks, got {type(sys_param)}"
    assert any(
        isinstance(b, dict) and b.get("cache_control", {}).get("type") == "ephemeral"
        for b in sys_param
    ), f"no system block has cache_control ephemeral; got {sys_param}"


def test_generate_reasons_uses_haiku_4_5_model(tmp_path, monkeypatch):
    """モデル名が claude-haiku-4-5-20251001 で送信される."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()
    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.return_value = _mock_anthropic_response(_good_reasons_text())
    generate_reasons_for_property(p, ledger=led, client=cli)
    kwargs = cli._client.messages.create.call_args.kwargs
    assert kwargs.get("model") == "claude-haiku-4-5-20251001"


# ============================================================================
# C1-5: 禁止語混入 → 1回再生成
# ============================================================================

def test_generate_reasons_forbidden_first_then_good_succeeds(tmp_path, monkeypatch):
    """禁止語混入応答 → 1回再生成 → 合格 → LLM 結果採用."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property

    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()

    bad_lines = list(_GOOD_LINES)
    bad_lines[0] = "福岡市博多区・路線A・徒歩5分立地はSランクで買うべき水準の優良エリアです。"
    bad_text = "\n".join(bad_lines)

    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.side_effect = [
        _mock_anthropic_response(bad_text),
        _mock_anthropic_response(_good_reasons_text()),
    ]

    out = generate_reasons_for_property(p, ledger=led, client=cli)
    assert out.isAutoFallback is False
    assert "買うべき" not in "".join(out.dealReasons)
    assert cli._client.messages.create.call_count == 2  # 初回 + 再生成
    assert led.call_count() == 2


def test_generate_reasons_forbidden_twice_falls_back_to_machine(tmp_path, monkeypatch):
    """禁止語連続2回 → fallback 機械文 + isAutoFallback=True."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property

    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()

    bad_lines = list(_GOOD_LINES)
    bad_lines[0] = "福岡市博多区・路線A・徒歩5分立地はSランクで買うべき水準の優良エリアです。"
    bad_text = "\n".join(bad_lines)

    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.side_effect = [
        _mock_anthropic_response(bad_text),
        _mock_anthropic_response(bad_text),
    ]

    out = generate_reasons_for_property(p, ledger=led, client=cli)
    assert out.isAutoFallback is True
    assert len(out.dealReasons) == 3
    assert "買うべき" not in "".join(out.dealReasons)
    assert cli._client.messages.create.call_count == 2  # 初回 + 1回再生成のみ
    assert led.call_count() == 2


def test_generate_reasons_too_long_then_good(tmp_path, monkeypatch):
    """字数超過 → 再生成1回 → 合格."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()

    long_line = "あ" * 70
    too_long = "\n".join([long_line, long_line, long_line])

    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.side_effect = [
        _mock_anthropic_response(too_long),
        _mock_anthropic_response(_good_reasons_text()),
    ]

    out = generate_reasons_for_property(p, ledger=led, client=cli)
    assert out.isAutoFallback is False
    assert sum(len(r) for r in out.dealReasons) <= 180


def test_generate_reasons_too_short_falls_back(tmp_path, monkeypatch):
    """3行揃ってるが合計字数 < 120 → 再生成 → 同じ短文 → fallback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()
    short = "短い"
    short_text = "\n".join([short, short, short])
    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.side_effect = [
        _mock_anthropic_response(short_text),
        _mock_anthropic_response(short_text),
    ]
    out = generate_reasons_for_property(p, ledger=led, client=cli)
    assert out.isAutoFallback is True


def test_generate_reasons_two_lines_response_falls_back(tmp_path, monkeypatch):
    """応答が2行（行数不足）連続 → fallback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()
    two_lines = "\n".join([
        "福岡市博多区・路線A・徒歩5分の立地はSランクで投資妙味があります。",
        "RC築12年で残存35年、長期融資が射程圏のバランス良物件です。",
    ])
    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.side_effect = [
        _mock_anthropic_response(two_lines),
        _mock_anthropic_response(two_lines),
    ]
    out = generate_reasons_for_property(p, ledger=led, client=cli)
    assert out.isAutoFallback is True
    assert len(out.dealReasons) == 3  # fallback で3行確保


# ============================================================================
# C1-6: HTTP エラー → fallback (raise しない)
# ============================================================================

def test_generate_reasons_http_401_falls_back(tmp_path, monkeypatch):
    """401 AuthenticationError → raise せず fallback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-bad-key")
    import anthropic
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property

    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()

    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    err = anthropic.AuthenticationError(
        message="invalid key",
        response=MagicMock(status_code=401),
        body=None,
    )
    cli._client.messages.create.side_effect = err

    out = generate_reasons_for_property(p, ledger=led, client=cli)
    assert out.isAutoFallback is True
    assert len(out.dealReasons) == 3
    # 401 はリトライしない（即 fallback）
    assert cli._client.messages.create.call_count == 1
    # ledger 加算は行われない
    assert led.call_count() == 0


def test_generate_reasons_http_429_falls_back(tmp_path, monkeypatch):
    """429 RateLimitError → 1回再試行 → 同じ → fallback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    import anthropic
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()
    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    err = anthropic.RateLimitError(
        message="rate limited",
        response=MagicMock(status_code=429),
        body=None,
    )
    cli._client.messages.create.side_effect = err
    out = generate_reasons_for_property(p, ledger=led, client=cli)
    assert out.isAutoFallback is True
    assert led.call_count() == 0  # 429 で課金なし


def test_generate_reasons_timeout_falls_back(tmp_path, monkeypatch):
    """APITimeoutError → fallback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    import anthropic
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()
    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    err = anthropic.APITimeoutError(request=MagicMock())
    cli._client.messages.create.side_effect = err
    out = generate_reasons_for_property(p, ledger=led, client=cli)
    assert out.isAutoFallback is True


def test_generate_reasons_5xx_falls_back(tmp_path, monkeypatch):
    """5xx InternalServerError → fallback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    import anthropic
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()
    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    err = anthropic.InternalServerError(
        message="server boom",
        response=MagicMock(status_code=500),
        body=None,
    )
    cli._client.messages.create.side_effect = err
    out = generate_reasons_for_property(p, ledger=led, client=cli)
    assert out.isAutoFallback is True


def test_generate_reasons_generic_exception_falls_back(tmp_path, monkeypatch):
    """予期しない一般例外も fallback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    p = _make_property()
    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.side_effect = ValueError("unexpected")
    out = generate_reasons_for_property(p, ledger=led, client=cli)
    assert out.isAutoFallback is True


# ============================================================================
# C1-7: 予算超過 → API 呼ばずに fallback
# ============================================================================

def test_generate_reasons_budget_exceeded_skips_api(tmp_path, monkeypatch):
    """is_within_budget=False の状況では API を呼ばずに fallback."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from services.ai_reasons import AnthropicClient, generate_reasons_for_property

    # 1000円ぴったり積んで超過状態に
    led = CostLedger(state_dir=tmp_path, today="2026-04-19", budget_jpy=1000)
    led.add(usd=1000 / 150)  # ¥1000 (= 予算ぴったり、is_within_budget は False)

    p = _make_property()
    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.return_value = _mock_anthropic_response(_good_reasons_text())

    out = generate_reasons_for_property(p, ledger=led, client=cli)

    assert out.isAutoFallback is True
    # API は1回も呼ばれていない（call_count ベース検証）
    assert cli._client.messages.create.call_count == 0
    # ledger も追加加算されない（最初の add() の1のみ）
    assert led.call_count() == 1


# ============================================================================
# C1-8: バッチ実行 + 差分検出
# ============================================================================

def test_get_properties_needing_scoring_no_previous():
    """前日 properties が無ければ全件 needing."""
    from services.ai_reasons import get_properties_needing_scoring
    today = [_make_property(id=f"p{i}") for i in range(3)]
    needing = get_properties_needing_scoring(today, yesterday=None)
    assert len(needing) == 3


def test_get_properties_needing_scoring_same_id_same_score_skipped():
    """同一id・同一price・同一yieldGross・同一dealModelVersion → スキップ."""
    from services.ai_reasons import get_properties_needing_scoring
    today = [_make_property(id="dup1")]
    yesterday = [_make_property(
        id="dup1",
        # 前日の dealReasons は LLM 由来として残っている
        dealReasons=[
            "前日のLLM由来1行目です、長文にして字数チェック通過の十分な説明を含む。",
            "前日のLLM由来2行目です、長文にして字数チェック通過の十分な説明を含む。",
            "前日のLLM由来3行目です、長文にして字数チェック通過の十分な説明を含む。",
        ],
        isAutoFallback=False,
        dealModelVersion="v2.5",
    )]
    needing = get_properties_needing_scoring(today, yesterday=yesterday)
    assert len(needing) == 0


def test_get_properties_needing_scoring_model_version_mismatch_regen():
    """前日 v2.3 → 今日 v2.5 で強制再生成."""
    from services.ai_reasons import get_properties_needing_scoring
    today = [_make_property(id="x", dealModelVersion="v2.5")]
    yesterday = [_make_property(id="x", dealModelVersion="v2.3")]
    needing = get_properties_needing_scoring(today, yesterday=yesterday)
    assert len(needing) == 1


def test_get_properties_needing_scoring_price_changed_regen():
    """price 変動 → 再生成対象."""
    from services.ai_reasons import get_properties_needing_scoring
    today = [_make_property(id="x", price=28_000_000)]
    yesterday = [_make_property(id="x", price=29_000_000)]
    needing = get_properties_needing_scoring(today, yesterday=yesterday)
    assert len(needing) == 1


def test_get_properties_needing_scoring_yield_changed_regen():
    """yieldGross 変動 → 再生成対象."""
    from services.ai_reasons import get_properties_needing_scoring
    today = [_make_property(id="x", yieldGross=8.5)]
    yesterday = [_make_property(id="x", yieldGross=8.0)]
    needing = get_properties_needing_scoring(today, yesterday=yesterday)
    assert len(needing) == 1


def test_get_properties_needing_scoring_yesterday_fallback_regen():
    """前日が isAutoFallback=True → LLM 化を狙って再生成."""
    from services.ai_reasons import get_properties_needing_scoring
    today = [_make_property(id="x")]
    yesterday = [_make_property(id="x", isAutoFallback=True)]
    needing = get_properties_needing_scoring(today, yesterday=yesterday)
    assert len(needing) == 1


def test_generate_reasons_batch_inherits_from_yesterday(tmp_path, monkeypatch):
    """同一物件は前日のreasonを継承し、API は呼ばれない."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from services.ai_reasons import AnthropicClient, generate_reasons_batch

    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    yesterday_text = [
        "前日LLM由来1行目です、長文にして字数チェックを通過する分量を入れる。",
        "前日LLM由来2行目です、長文にして字数チェックを通過する分量を入れる。",
        "前日LLM由来3行目です、長文にして字数チェックを通過する分量を入れる。",
    ]
    today = [_make_property(id="x", dealReasons=["a", "b", "c"])]  # 入力 reasons は仮（fallback文）
    yesterday = [_make_property(
        id="x",
        dealReasons=yesterday_text,
        isAutoFallback=False,
        dealModelVersion="v2.5",
    )]

    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.return_value = _mock_anthropic_response(_good_reasons_text())

    stats = generate_reasons_batch(today, ledger=led, previous_props=yesterday, client=cli)

    assert cli._client.messages.create.call_count == 0
    assert stats["ai_call_count"] == 0
    assert stats["inherited_count"] == 1
    # 継承されたreasonが反映されている
    assert today[0].dealReasons == yesterday_text


def test_generate_reasons_batch_runs_for_new_properties(tmp_path, monkeypatch):
    """新規物件3件 → API が3回呼ばれる."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from services.ai_reasons import AnthropicClient, generate_reasons_batch

    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    today = [_make_property(id=f"new{i}") for i in range(3)]

    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.return_value = _mock_anthropic_response(_good_reasons_text())

    stats = generate_reasons_batch(today, ledger=led, previous_props=None, client=cli)

    assert cli._client.messages.create.call_count == 3
    assert stats["ai_call_count"] == 3
    assert stats["fallback_count"] == 0


def test_generate_reasons_batch_stops_when_budget_exceeded_mid(tmp_path, monkeypatch):
    """途中で予算超過 → 残りは fallback で処理続行."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    from services.ai_reasons import AnthropicClient, generate_reasons_batch, _estimate_per_call_jpy

    # 1呼び出し ¥45 の実コスト（重い）+ 予算 ¥150 → 3回呼んだ後ガード発動想定
    BUDGET = 150
    led = CostLedger(state_dir=tmp_path, today="2026-04-19", budget_jpy=BUDGET)

    # 1呼び出し $0.30 = ¥45 の重さで返す
    expensive = MagicMock()
    expensive.content = [MagicMock(text=_good_reasons_text(), type="text")]
    expensive.usage = MagicMock(
        input_tokens=300_000,
        output_tokens=120,
        cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )

    today = [_make_property(id=f"new{i}") for i in range(10)]

    cli = AnthropicClient(strict=True)
    cli._client = MagicMock()
    cli._client.messages.create.return_value = expensive

    stats = generate_reasons_batch(today, ledger=led, previous_props=None, client=cli)

    # 全10件は走らないはず（途中打ち切り）
    assert stats["ai_call_count"] < 10
    assert stats["fallback_count"] >= 1
    # 予算ガードは「次呼び出し前に current_jpy >= budget なら以降は API skip」仕様。
    # 実 usage が estimated より大幅に大きいケースは1呼び出し分のオーバーシュート許容。
    # ただし全10件分（¥450）まで暴走することは絶対に無いことを担保する。
    assert led.current_jpy() < 450  # 全件呼び出しに比べて十分削減されている


# ============================================================================
# C1-9: モデルフィールド検証
# ============================================================================

def test_property_has_aiReasonsGeneratedAt_field():
    """Property モデルに aiReasonsGeneratedAt フィールドが存在する."""
    p = Property(id="x", name="n", aiReasonsGeneratedAt="2026-04-19T10:00:00Z")
    assert p.aiReasonsGeneratedAt == "2026-04-19T10:00:00Z"


def test_property_aiReasonsGeneratedAt_default_none():
    p = Property(id="x", name="n")
    assert p.aiReasonsGeneratedAt is None
