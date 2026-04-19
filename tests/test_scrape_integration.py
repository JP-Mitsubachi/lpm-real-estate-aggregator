"""Integration tests for scrape.py — orchestrator + scoring + AI enrichment.

orchestrator.run_search を mock し、Property リストを差し込んで
(a) v2.5 score が全件付与される
(b) --with-ai フラグ無し (use_ai=False) の場合、AI モジュールが呼ばれない
(c) --with-ai フラグありで前日 properties.json なし → 全件 AI 呼び出し
(d) --with-ai フラグありで同じ入力を2回流す → 2回目の AI call_count が 0
(e) 出力 JSON に meta.scoring が積まれている
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Meta, Property, SearchResponse  # noqa: E402


# ============================================================================
# Helpers
# ============================================================================

def _make_props(n: int = 3) -> list[Property]:
    return [
        Property(
            id=f"id-{i}",
            name=f"博多駅徒歩5分・RC築12年 #{i}",
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
            address=f"福岡県福岡市博多区博多駅前1-{i}-{i}",
        )
        for i in range(n)
    ]


def _good_text() -> str:
    return "\n".join([
        "福岡市博多区・路線A・徒歩5分の立地は希少なSランク評価で投資妙味の高いエリアです。",
        "RC築12年で残存耐用年数35年、地銀含む長期融資が射程に入るバランスの良い物件です。",
        "表面8.5%はベンチマーク6.0%に対し+42%、市場との乖離が大きい優良水準と言えます。",
    ])


def _mock_anthropic_response():
    msg = MagicMock()
    msg.content = [MagicMock(text=_good_text(), type="text")]
    msg.usage = MagicMock(
        input_tokens=50,
        output_tokens=120,
        cache_read_input_tokens=540,
        cache_creation_input_tokens=0,
    )
    msg.id = "msg_mock"
    msg.model = "claude-haiku-4-5-20251001"
    msg.stop_reason = "end_turn"
    return msg


def _run_scrape(args: list[str], mock_search_props: list[Property]):
    """Patch orchestrator.run_search and run scrape.main()."""
    import scrape as scrape_mod

    async def fake_search(_query):
        return SearchResponse(properties=mock_search_props, meta=Meta(total=len(mock_search_props)))

    with patch.object(sys, "argv", ["scrape.py"] + args):
        with patch.object(scrape_mod, "run_search", new=AsyncMock(side_effect=fake_search)):
            asyncio.run(scrape_mod.main())


# ============================================================================
# Tests
# ============================================================================

def test_scrape_assigns_v24_score_to_all_props(tmp_path, monkeypatch):
    """orchestrator → score_property で v2.5 が全件付与される (--with-ai 無し)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "out.json"
    props = _make_props(3)
    _run_scrape(
        ["--output", str(out), "--state-dir", str(tmp_path / "state")],
        props,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert len(data["properties"]) == 3
    for p in data["properties"]:
        assert p["dealModelVersion"] == "v2.5"
        assert p["dealRank"] in ("S", "A", "B", "C", "D", "N/A")
        # fallback の3行 reason が入っている
        assert isinstance(p["dealReasons"], list)
        assert len(p["dealReasons"]) == 3


def test_scrape_without_with_ai_does_not_call_anthropic(tmp_path, monkeypatch):
    """--with-ai 無しで anthropic.Anthropic が一度も import/instantiate されない."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "out.json"
    props = _make_props(2)

    with patch("anthropic.Anthropic") as anth_cls:
        _run_scrape(
            ["--output", str(out), "--state-dir", str(tmp_path / "state")],
            props,
        )
        assert anth_cls.call_count == 0


def test_scrape_with_ai_no_previous_calls_api_for_all(tmp_path, monkeypatch):
    """前日 properties なし + --with-ai → 全件 AI 呼び出し."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    out = tmp_path / "out.json"
    props = _make_props(3)

    with patch("anthropic.Anthropic") as anth_cls:
        client_inst = MagicMock()
        client_inst.messages.create.return_value = _mock_anthropic_response()
        anth_cls.return_value = client_inst

        _run_scrape(
            ["--output", str(out), "--with-ai", "--state-dir", str(tmp_path / "state")],
            props,
        )
        # 全 3 件
        assert client_inst.messages.create.call_count == 3

    data = json.loads(out.read_text(encoding="utf-8"))
    # meta.scoring が積まれている
    assert data["meta"]["scoring"]["aiCallCount"] == 3
    assert data["meta"]["scoring"]["modelVersion"] == "v2.5"
    assert data["meta"]["scoring"]["fallbackCount"] == 0
    assert data["meta"]["scoring"]["withAi"] is True


def test_scrape_with_ai_second_run_inherits_yesterday(tmp_path, monkeypatch):
    """同じ入力で2回流す → 2回目は AI call_count = 0 (差分検出で全件継承)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    out = tmp_path / "out.json"
    props = _make_props(3)

    with patch("anthropic.Anthropic") as anth_cls:
        client_inst = MagicMock()
        client_inst.messages.create.return_value = _mock_anthropic_response()
        anth_cls.return_value = client_inst

        # 1回目
        _run_scrape(
            ["--output", str(out), "--with-ai", "--state-dir", str(tmp_path / "state")],
            list(_make_props(3)),
        )
        first_calls = client_inst.messages.create.call_count
        assert first_calls == 3

        # 2回目（同じ入力 — id/price/yieldGross/dealModelVersion 全一致）
        client_inst.messages.create.reset_mock()
        _run_scrape(
            ["--output", str(out), "--with-ai", "--state-dir", str(tmp_path / "state")],
            list(_make_props(3)),
        )
        second_calls = client_inst.messages.create.call_count
        assert second_calls == 0  # 差分なし → 全件継承

    # meta.scoring に inheritedCount が記録されている
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["meta"]["scoring"]["aiCallCount"] == 0
    assert data["meta"]["scoring"]["inheritedCount"] >= 1


def test_scrape_writes_meta_scoring_section(tmp_path, monkeypatch):
    """--with-ai 無しでも meta.scoring が記録される (modelVersion 等)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "out.json"
    props = _make_props(2)
    _run_scrape(
        ["--output", str(out), "--state-dir", str(tmp_path / "state")],
        props,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "scoring" in data["meta"]
    assert data["meta"]["scoring"]["modelVersion"] == "v2.5"
    assert data["meta"]["scoring"]["withAi"] is False
    assert data["meta"]["scoring"]["costJpy"] == 0.0


# ============================================================================
# v2.5: first_seen_at（初出日）+ price_history（価格履歴）トラッキング
# ============================================================================

def test_first_seen_at_set_today_for_new_property(tmp_path, monkeypatch):
    """前日 properties なし → 全件 firstSeenAt が今日のタイムスタンプ."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "out.json"
    props = _make_props(3)
    _run_scrape(
        ["--output", str(out), "--state-dir", str(tmp_path / "state")],
        props,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    today_prefix = data["generatedAt"][:10]  # YYYY-MM-DD
    assert len(data["properties"]) == 3
    for p in data["properties"]:
        assert p.get("firstSeenAt") is not None
        # 新規物件 → 今日の日付（scrapedAt と同じ日）
        assert p["firstSeenAt"][:10] == today_prefix


def test_first_seen_at_inherited_for_existing_property(tmp_path, monkeypatch):
    """既存物件は前日の firstSeenAt を継承する."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "out.json"

    # 1回目: 全件新規 → firstSeenAt が今日入る
    _run_scrape(
        ["--output", str(out), "--state-dir", str(tmp_path / "state")],
        _make_props(3),
    )
    first_data = json.loads(out.read_text(encoding="utf-8"))

    # 既存ファイルの firstSeenAt を意図的に過去日に書き換え
    for p in first_data["properties"]:
        p["firstSeenAt"] = "2026-01-01T00:00:00Z"
    out.write_text(json.dumps(first_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2回目: 同じ物件群で再走 → 既存物件は過去日が継承される
    _run_scrape(
        ["--output", str(out), "--state-dir", str(tmp_path / "state")],
        _make_props(3),
    )
    second_data = json.loads(out.read_text(encoding="utf-8"))
    for p in second_data["properties"]:
        assert p["firstSeenAt"] == "2026-01-01T00:00:00Z"


def test_first_seen_at_today_for_truly_new_property(tmp_path, monkeypatch):
    """前日にないIDは firstSeenAt が今日."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "out.json"

    _run_scrape(
        ["--output", str(out), "--state-dir", str(tmp_path / "state")],
        _make_props(2),  # id-0, id-1
    )
    first_data = json.loads(out.read_text(encoding="utf-8"))
    for p in first_data["properties"]:
        p["firstSeenAt"] = "2026-01-01T00:00:00Z"
    out.write_text(json.dumps(first_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2回目: 1件追加 (id-2) → id-2 だけ firstSeenAt が今日
    _run_scrape(
        ["--output", str(out), "--state-dir", str(tmp_path / "state")],
        _make_props(3),  # id-0, id-1, id-2
    )
    second_data = json.loads(out.read_text(encoding="utf-8"))
    today_prefix = second_data["generatedAt"][:10]
    by_id = {p["id"]: p for p in second_data["properties"]}
    assert by_id["id-0"]["firstSeenAt"] == "2026-01-01T00:00:00Z"
    assert by_id["id-1"]["firstSeenAt"] == "2026-01-01T00:00:00Z"
    assert by_id["id-2"]["firstSeenAt"][:10] == today_prefix


def test_price_history_initialized_for_new_property(tmp_path, monkeypatch):
    """新規物件は priceHistory に1件（今日のエントリ）."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "out.json"
    props = _make_props(2)
    _run_scrape(
        ["--output", str(out), "--state-dir", str(tmp_path / "state")],
        props,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    for p in data["properties"]:
        assert isinstance(p.get("priceHistory"), list)
        assert len(p["priceHistory"]) == 1
        assert p["priceHistory"][0]["price"] == 28_000_000
        assert "date" in p["priceHistory"][0]


def test_price_history_unchanged_when_price_same(tmp_path, monkeypatch):
    """価格不変なら priceHistory は増えない（継承）."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "out.json"
    _run_scrape(
        ["--output", str(out), "--state-dir", str(tmp_path / "state")],
        _make_props(2),
    )
    _run_scrape(
        ["--output", str(out), "--state-dir", str(tmp_path / "state")],
        _make_props(2),
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    for p in data["properties"]:
        assert len(p["priceHistory"]) == 1


def test_price_history_appended_on_price_change(tmp_path, monkeypatch):
    """値下げが発生 → priceHistory が +1件."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "out.json"
    # 1回目
    _run_scrape(
        ["--output", str(out), "--state-dir", str(tmp_path / "state")],
        _make_props(2),  # price=28M
    )
    # 2回目: 同じIDだが値下げ
    cheaper = _make_props(2)
    for p in cheaper:
        p.price = 25_000_000
    _run_scrape(
        ["--output", str(out), "--state-dir", str(tmp_path / "state")],
        cheaper,
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    for p in data["properties"]:
        assert len(p["priceHistory"]) == 2
        prices = [e["price"] for e in p["priceHistory"]]
        assert 28_000_000 in prices
        assert 25_000_000 in prices


def test_price_history_capped_at_10_entries(tmp_path, monkeypatch):
    """11件目の値下げが入ると最古を drop して最大10件を保持."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out = tmp_path / "out.json"

    # 12回値下げを繰り返す
    base_price = 30_000_000
    for i in range(12):
        new_price = base_price - i * 500_000  # 30M, 29.5M, 29M, ...
        props = _make_props(1)
        props[0].price = new_price
        _run_scrape(
            ["--output", str(out), "--state-dir", str(tmp_path / "state")],
            props,
        )
    data = json.loads(out.read_text(encoding="utf-8"))
    p = data["properties"][0]
    # 最大10件
    assert len(p["priceHistory"]) <= 10
    # 最古2件は drop されているので、初期 30M はもう含まれない
    prices = [e["price"] for e in p["priceHistory"]]
    assert 30_000_000 not in prices
    # 最新は 24.5M (30M - 11*500K)
    assert prices[-1] == 30_000_000 - 11 * 500_000


def test_scrape_with_ai_dealReasons_three_lines(tmp_path, monkeypatch):
    """AI 経由でも dealReasons が3行で出力される."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    out = tmp_path / "out.json"
    props = _make_props(2)

    with patch("anthropic.Anthropic") as anth_cls:
        client_inst = MagicMock()
        client_inst.messages.create.return_value = _mock_anthropic_response()
        anth_cls.return_value = client_inst
        _run_scrape(
            ["--output", str(out), "--with-ai", "--state-dir", str(tmp_path / "state")],
            props,
        )

    data = json.loads(out.read_text(encoding="utf-8"))
    for p in data["properties"]:
        assert isinstance(p["dealReasons"], list)
        assert len(p["dealReasons"]) == 3
        total = sum(len(r) for r in p["dealReasons"])
        assert 120 <= total <= 180, f"total chars {total} out of [120,180]"
        # AI 経路 → isAutoFallback=False
        assert p["isAutoFallback"] is False
