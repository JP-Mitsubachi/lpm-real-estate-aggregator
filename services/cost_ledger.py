"""Monthly Anthropic API cost ledger (PRD §5.3.3).

Persists cumulative spend to `data/state/cost_ledger_YYYY-MM.json`. The
ledger is the single physical guard preventing the AI scoring step from
exceeding the monthly budget.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# PRD-fixed defaults
DEFAULT_BUDGET_JPY = 1000
USD_TO_JPY = 150  # static conversion; revisit if exchange rate moves >10%


class CostLedger:
    """Tracks cumulative Anthropic API spend within a calendar month.

    The ledger is intentionally simple: append-only counter persisted to
    disk after every `add()` call so a crash can't lose accounting.
    """

    def __init__(
        self,
        state_dir: Path,
        today: str,
        budget_jpy: int = DEFAULT_BUDGET_JPY,
    ):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.budget_jpy = budget_jpy
        self.month = today[:7]  # "YYYY-MM"
        self.path = self.state_dir / f"cost_ledger_{self.month}.json"
        self._cumulative_usd: float = 0.0
        self._call_count: int = 0
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("month") != self.month:
                # File is from a different month — start fresh
                logger.warning("cost ledger month mismatch (%s vs %s); resetting", data.get("month"), self.month)
                return
            self._cumulative_usd = float(data.get("cumulative_usd", 0))
            self._call_count = int(data.get("call_count", 0))
        except (ValueError, OSError, json.JSONDecodeError) as e:
            logger.warning("cost ledger corrupted (%s); resetting to zero", e)

    def _flush(self) -> None:
        payload = {
            "month": self.month,
            "cumulative_usd": round(self._cumulative_usd, 6),
            "cumulative_jpy": round(self._cumulative_usd * USD_TO_JPY, 2),
            "call_count": self._call_count,
            "budget_jpy": self.budget_jpy,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, usd: float) -> None:
        if usd < 0:
            raise ValueError(f"usd must be non-negative, got {usd}")
        self._cumulative_usd += usd
        self._call_count += 1
        self._flush()

    def current_jpy(self) -> float:
        return self._cumulative_usd * USD_TO_JPY

    def call_count(self) -> int:
        return self._call_count

    def is_within_budget(self, estimated_jpy: float) -> bool:
        """Returns True iff (current + estimated) is strictly below budget."""
        return self.current_jpy() + estimated_jpy < self.budget_jpy

    def remaining_jpy(self) -> float:
        return max(0.0, self.budget_jpy - self.current_jpy())
