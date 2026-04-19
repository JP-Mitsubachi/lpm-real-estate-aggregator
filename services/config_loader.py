"""Loader for scoring configuration v2.1 (PRD §5.2.x).

Reads `config/scoring.yaml`, validates structure, and returns a dict.
Validation failures raise `ScoringConfigError`.

v2.1 schema (top-level required sections):
    weights, thresholds, median,
    location_score, area_rank, default_area_rank, route_rank,
    cap_rate_benchmark, single_layout_keywords, yield_score,
    structure_lifespan, structure_aliases, structure_estimation,
    loan_score, stagnation_score, risk_score,
    redevelopment_zones, hazard_caution_zones, na_rules
Optional: version
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

import yaml


class ScoringConfigError(Exception):
    """Raised when scoring.yaml is missing, malformed, or fails validation."""


REQUIRED_TOP_LEVEL = {
    "weights",
    "thresholds",
    "median",
    "location_score",
    "area_rank",
    "default_area_rank",
    "route_rank",
    "cap_rate_benchmark",
    "single_layout_keywords",
    "yield_score",
    "structure_lifespan",
    "structure_aliases",
    "structure_estimation",
    "loan_score",
    "stagnation_score",
    "risk_score",
    "redevelopment_zones",
    "hazard_caution_zones",
    "na_rules",
}
OPTIONAL_TOP_LEVEL = {"version", "property_type_modifiers", "ai_reasons"}

REQUIRED_WEIGHT_KEYS = {"location", "yield", "loan", "stagnation", "risk"}
REQUIRED_THRESHOLD_KEYS = {
    "rank_S", "rank_A", "rank_B", "rank_C",
    "s_rank_min_location", "s_rank_min_loan",
    "s_rank_min_yield", "s_rank_min_remaining_years",
    "s_rank_max_price",  # v2.2
}

# Project-relative default path: services/ → repo root → config/scoring.yaml
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "scoring.yaml"


def _validate(cfg: dict) -> None:
    if not isinstance(cfg, dict):
        raise ScoringConfigError("scoring config must be a mapping at the top level")

    keys = set(cfg.keys())
    missing = REQUIRED_TOP_LEVEL - keys
    if missing:
        raise ScoringConfigError(f"missing required sections: {sorted(missing)}")
    unknown = keys - REQUIRED_TOP_LEVEL - OPTIONAL_TOP_LEVEL
    if unknown:
        raise ScoringConfigError(f"unknown top-level sections: {sorted(unknown)}")

    weights = cfg["weights"]
    if set(weights.keys()) != REQUIRED_WEIGHT_KEYS:
        raise ScoringConfigError(
            f"weights keys must be exactly {sorted(REQUIRED_WEIGHT_KEYS)}, "
            f"got {sorted(weights.keys())}"
        )
    for k, v in weights.items():
        if not isinstance(v, (int, float)) or v < 0:
            raise ScoringConfigError(
                f"weights.{k} must be non-negative number, got {v!r}"
            )
    weight_sum = sum(weights.values())
    if weight_sum != 100:
        raise ScoringConfigError(f"weights must sum to 100, got {weight_sum}")

    thresholds = cfg["thresholds"]
    if set(thresholds.keys()) != REQUIRED_THRESHOLD_KEYS:
        raise ScoringConfigError(
            f"thresholds keys must include exactly {sorted(REQUIRED_THRESHOLD_KEYS)}, "
            f"got {sorted(thresholds.keys())}"
        )
    s, a, b, c = (
        thresholds["rank_S"],
        thresholds["rank_A"],
        thresholds["rank_B"],
        thresholds["rank_C"],
    )
    if not (s > a > b > c):
        raise ScoringConfigError(
            f"thresholds must be strictly descending S>A>B>C, got S={s}, A={a}, B={b}, C={c}"
        )

    median = cfg["median"]
    for key in ("min_sample_size", "fallback_min"):
        if key not in median or not isinstance(median[key], int) or median[key] < 1:
            raise ScoringConfigError(f"median.{key} must be a positive int")
    if median["fallback_min"] >= median["min_sample_size"]:
        raise ScoringConfigError(
            "median.fallback_min must be smaller than min_sample_size"
        )

    # location_score sub-validation
    ls = cfg["location_score"]
    for k in ("area_points", "walk_points", "walk_thresholds", "line_bonus", "cap"):
        if k not in ls:
            raise ScoringConfigError(f"location_score.{k} required")
    if set(ls["area_points"].keys()) != {"S", "A", "B", "C", "D"}:
        raise ScoringConfigError("location_score.area_points must have keys S/A/B/C/D")

    # cap_rate_benchmark
    crb = cfg["cap_rate_benchmark"]
    for k in ("fukuoka_city_single", "fukuoka_city_family",
              "kitakyushu", "kurume", "default"):
        if k not in crb:
            raise ScoringConfigError(f"cap_rate_benchmark.{k} required")

    # loan_score
    ln = cfg["loan_score"]
    if "brackets" not in ln or not isinstance(ln["brackets"], list):
        raise ScoringConfigError("loan_score.brackets must be a list")

    # stagnation_score
    if "default" not in cfg["stagnation_score"]:
        raise ScoringConfigError("stagnation_score.default required")

    # ai_reasons (optional, but if present must be well-formed)
    if "ai_reasons" in cfg:
        _validate_ai_reasons(cfg["ai_reasons"])


def _validate_ai_reasons(ar: dict) -> None:
    """v2.4 ai_reasons セクションの構造バリデーション."""
    if not isinstance(ar, dict):
        raise ScoringConfigError("ai_reasons must be a mapping")
    required = {"model", "pricing", "tokens", "budget", "estimation", "retries", "validation"}
    missing = required - set(ar.keys())
    if missing:
        raise ScoringConfigError(f"ai_reasons missing keys: {sorted(missing)}")

    if not isinstance(ar["model"], str) or not ar["model"]:
        raise ScoringConfigError("ai_reasons.model must be non-empty string")

    pricing = ar["pricing"]
    for k in ("input_per_mtok_usd", "output_per_mtok_usd",
              "cache_write_per_mtok_usd", "cache_read_per_mtok_usd"):
        if k not in pricing or not isinstance(pricing[k], (int, float)) or pricing[k] < 0:
            raise ScoringConfigError(f"ai_reasons.pricing.{k} must be non-negative number")

    tokens = ar["tokens"]
    for k in ("system_tokens", "user_tokens_per_property", "output_tokens_per_property"):
        if k not in tokens or not isinstance(tokens[k], int) or tokens[k] < 0:
            raise ScoringConfigError(f"ai_reasons.tokens.{k} must be non-negative int")

    budget = ar["budget"]
    if "monthly_jpy" not in budget or budget["monthly_jpy"] <= 0:
        raise ScoringConfigError("ai_reasons.budget.monthly_jpy must be positive")
    if "usd_to_jpy" not in budget or budget["usd_to_jpy"] <= 0:
        raise ScoringConfigError("ai_reasons.budget.usd_to_jpy must be positive")

    estimation = ar["estimation"]
    for k in ("daily_property_count", "days_per_month"):
        if k not in estimation or estimation[k] <= 0:
            raise ScoringConfigError(f"ai_reasons.estimation.{k} must be positive")
    rate = estimation.get("diff_inheritance_rate", 0)
    if not (0 <= rate <= 1):
        raise ScoringConfigError("ai_reasons.estimation.diff_inheritance_rate must be 0..1")

    retries = ar["retries"]
    max_regen = retries.get("max_regen", -1)
    if max_regen != 1:
        raise ScoringConfigError(
            "ai_reasons.retries.max_regen must be 1 (PRD AC-010-3)"
        )

    val = ar["validation"]
    for k in ("min_total_chars", "max_total_chars", "expected_lines"):
        if k not in val or val[k] <= 0:
            raise ScoringConfigError(f"ai_reasons.validation.{k} must be positive int")
    if val["min_total_chars"] >= val["max_total_chars"]:
        raise ScoringConfigError(
            "ai_reasons.validation.min_total_chars must be < max_total_chars"
        )
    for k in ("forbidden_words", "uncertain_words"):
        if k not in val or not isinstance(val[k], list):
            raise ScoringConfigError(f"ai_reasons.validation.{k} must be a list")


def load_scoring_config(path: Optional[Path] = None) -> dict:
    """Load and validate scoring.yaml. Defaults to repo's `config/scoring.yaml`."""
    target = Path(path) if path is not None else DEFAULT_PATH
    if not target.exists():
        raise ScoringConfigError(f"scoring config not found: {target}")
    try:
        with target.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ScoringConfigError(f"failed to parse YAML: {e}") from e

    _validate(cfg)
    return cfg


@lru_cache(maxsize=1)
def get_default_config() -> dict:
    """Cached singleton accessor for the default scoring config."""
    return load_scoring_config()


def reset_cache() -> None:
    """Reset the cached config (useful for tests after modifying config)."""
    get_default_config.cache_clear()
