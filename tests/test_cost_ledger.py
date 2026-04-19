"""Tests for services/cost_ledger.py — monthly Anthropic API budget guard (E-6).

PRD §5.3.3: monthly budget defaults to ¥1,000. State persisted to
data/state/cost_ledger_YYYY-MM.json.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.cost_ledger import (  # noqa: E402
    DEFAULT_BUDGET_JPY,
    USD_TO_JPY,
    CostLedger,
)


# --- basics --------------------------------------------------------------

def test_starts_empty(tmp_path: Path):
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    assert led.current_jpy() == 0.0
    assert led.is_within_budget(estimated_jpy=0)


def test_add_accumulates_usd_to_jpy(tmp_path: Path):
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    led.add(usd=0.10)  # $0.10 = ¥15 at default rate
    assert led.current_jpy() == pytest.approx(0.10 * USD_TO_JPY)


def test_within_budget_true_when_below_limit(tmp_path: Path):
    led = CostLedger(state_dir=tmp_path, today="2026-04-19", budget_jpy=1000)
    led.add(usd=2.0)  # ¥300
    assert led.is_within_budget(estimated_jpy=500) is True


def test_within_budget_false_when_estimate_pushes_over(tmp_path: Path):
    led = CostLedger(state_dir=tmp_path, today="2026-04-19", budget_jpy=1000)
    led.add(usd=5.0)  # ¥750
    assert led.is_within_budget(estimated_jpy=300) is False


def test_within_budget_at_exactly_limit_is_false(tmp_path: Path):
    """予算ぴったりは新規呼び出しを許容しない（厳格ガード）."""
    led = CostLedger(state_dir=tmp_path, today="2026-04-19", budget_jpy=1000)
    led.add(usd=1000 / USD_TO_JPY)
    assert led.is_within_budget(estimated_jpy=1) is False


# --- persistence ---------------------------------------------------------

def test_persists_to_monthly_file(tmp_path: Path):
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    led.add(usd=0.5)
    p = tmp_path / "cost_ledger_2026-04.json"
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["month"] == "2026-04"
    assert data["cumulative_usd"] == pytest.approx(0.5)
    assert data["cumulative_jpy"] == pytest.approx(0.5 * USD_TO_JPY)
    assert data["call_count"] == 1


def test_resumes_from_existing_file(tmp_path: Path):
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    led.add(usd=0.5)
    led.add(usd=0.3)

    # Re-instantiate — state should persist
    led2 = CostLedger(state_dir=tmp_path, today="2026-04-19")
    assert led2.current_jpy() == pytest.approx(0.8 * USD_TO_JPY)
    assert led2.call_count() == 2


# --- month boundary ------------------------------------------------------

def test_month_rollover_starts_fresh(tmp_path: Path):
    led_apr = CostLedger(state_dir=tmp_path, today="2026-04-30")
    led_apr.add(usd=5.0)
    assert led_apr.current_jpy() == pytest.approx(5.0 * USD_TO_JPY)

    led_may = CostLedger(state_dir=tmp_path, today="2026-05-01")
    assert led_may.current_jpy() == 0.0
    # April file untouched
    apr_data = json.loads((tmp_path / "cost_ledger_2026-04.json").read_text())
    assert apr_data["cumulative_usd"] == pytest.approx(5.0)


# --- robustness ----------------------------------------------------------

def test_corrupted_file_recovers_to_zero(tmp_path: Path):
    p = tmp_path / "cost_ledger_2026-04.json"
    p.write_text("{not valid json")
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    assert led.current_jpy() == 0.0  # corrupted state → restart


def test_negative_usd_rejected(tmp_path: Path):
    led = CostLedger(state_dir=tmp_path, today="2026-04-19")
    with pytest.raises(ValueError):
        led.add(usd=-0.1)


def test_default_budget_is_1000_jpy():
    assert DEFAULT_BUDGET_JPY == 1000
