"""Tests for services/config_loader.py — scoring.yaml v2.1 loader."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.config_loader import ScoringConfigError, load_scoring_config  # noqa: E402


# --- Default file load ---------------------------------------------------

def test_load_default_scoring_yaml_v21():
    """Default `config/scoring.yaml` v2.1 → 5軸の重み・閾値・マスター."""
    cfg = load_scoring_config()
    # 5 axis weights
    assert cfg["weights"]["location"] == 30
    assert cfg["weights"]["yield"] == 30
    assert cfg["weights"]["loan"] == 20
    assert cfg["weights"]["stagnation"] == 10
    assert cfg["weights"]["risk"] == 10
    # ranks
    assert cfg["thresholds"]["rank_S"] == 80
    assert cfg["thresholds"]["rank_A"] == 65
    assert cfg["thresholds"]["rank_B"] == 50
    assert cfg["thresholds"]["rank_C"] == 35
    # composite S guard thresholds
    assert cfg["thresholds"]["s_rank_min_location"] == 15
    assert cfg["thresholds"]["s_rank_min_loan"] == 10
    assert cfg["thresholds"]["s_rank_min_yield"] == 15
    assert cfg["thresholds"]["s_rank_min_remaining_years"] == 10
    # area_rank master (10 entries+)
    assert cfg["area_rank"]["福岡市中央区"] == "S"
    assert cfg["area_rank"]["福岡市博多区"] == "S"
    assert cfg["area_rank"]["福岡市早良区"] == "A"
    assert cfg["area_rank"]["福岡市南区"] == "A"
    assert cfg["area_rank"]["福岡市西区"] == "B"
    assert cfg["area_rank"]["福岡市城南区"] == "B"
    assert cfg["area_rank"]["福岡市東区"] == "B"
    assert cfg["area_rank"]["北九州市小倉北区"] == "C"
    assert cfg["area_rank"]["久留米市"] == "C"
    assert cfg["default_area_rank"] == "D"
    # route_rank master (6 lines)
    assert "空港線" in cfg["route_rank"]["S"]
    assert "鹿児島本線" in cfg["route_rank"]["A"]
    assert "天神大牟田線" in cfg["route_rank"]["A"]
    assert "七隈線" in cfg["route_rank"]["B"]
    assert "箱崎線" in cfg["route_rank"]["B"]
    assert "筑肥線" in cfg["route_rank"]["C"]
    # Cap Rate benchmark
    assert cfg["cap_rate_benchmark"]["fukuoka_city_single"] == 5.25
    assert cfg["cap_rate_benchmark"]["fukuoka_city_family"] == 6.0
    assert cfg["cap_rate_benchmark"]["kitakyushu"] == 10.0
    assert cfg["cap_rate_benchmark"]["kurume"] == 10.0
    # structure lifespan
    assert cfg["structure_lifespan"]["RC"] == 47
    assert cfg["structure_lifespan"]["SRC"] == 47
    assert cfg["structure_lifespan"]["S造"] == 34
    assert cfg["structure_lifespan"]["木造"] == 22
    # walk thresholds
    assert cfg["location_score"]["walk_thresholds"]["full"] == 10
    assert cfg["location_score"]["walk_thresholds"]["half"] == 15
    # 4 redevelopment zones
    redev = cfg["redevelopment_zones"]
    assert len(redev) == 4
    names = [z["name"] for z in redev]
    assert "天神ビッグバン" in names
    assert "博多コネクティッド" in names
    assert "千早再開発" in names
    assert "七隈線天神南延伸後" in names


def test_weights_sum_to_100():
    cfg = load_scoring_config()
    total = sum(cfg["weights"].values())
    assert total == 100, f"weights must sum to 100, got {total}"


# --- Explicit path -------------------------------------------------------

def _minimal_v21_yaml() -> str:
    return """
version: "2.1"
weights: {location: 30, yield: 30, loan: 20, stagnation: 10, risk: 10}
thresholds:
  rank_S: 80
  rank_A: 65
  rank_B: 50
  rank_C: 35
  s_rank_min_location: 15
  s_rank_min_loan: 10
  s_rank_min_yield: 15
  s_rank_min_remaining_years: 10
  s_rank_max_price: 30000000
median: {min_sample_size: 15, fallback_min: 5}
location_score:
  area_points: {S: 20, A: 15, B: 10, C: 5, D: 0}
  walk_points: {within_10min: 10, within_15min: 5, over_15min: 0, unparseable: 0}
  walk_thresholds: {full: 10, half: 15}
  line_bonus: {S: 3, A: 2, B: 1, C: 0}
  cap: 30
area_rank:
  "福岡市中央区": S
  "福岡市博多区": S
default_area_rank: D
route_rank:
  S: ["空港線"]
  A: ["鹿児島本線"]
  B: []
  C: []
cap_rate_benchmark:
  fukuoka_city_single: 5.25
  fukuoka_city_family: 6.0
  kitakyushu: 10.0
  kurume: 10.0
  default: 6.0
single_layout_keywords: ["1K"]
yield_score: {multiplier: 1.5, cap: 30, benchmark_floor_score: 0}
structure_lifespan: {RC: 47, SRC: 47, S造: 34, 木造: 22, default: 47}
structure_aliases: {"RC造": RC}
structure_estimation:
  rules:
    - keywords_in_property_type: ["マンション"]
      assumed_structure: RC
  default: RC
loan_score:
  brackets:
    - {min_remaining: 20, points: 20}
    - {min_remaining: 10, points: 15}
    - {min_remaining: 5, points: 10}
    - {min_remaining: 1, points: 5}
    - {min_remaining: 0, points: 0}
  land_only_points: 10
stagnation_score: {default: 0, enabled: false}
risk_score:
  base_safety_points: 5
  redevelopment_bonus: 5
  hazard_high_penalty: -5
  cap_min: 0
  cap_max: 10
redevelopment_zones:
  - name: "天神ビッグバン"
    cities: ["福岡市中央区"]
    station_keywords: ["天神"]
hazard_caution_zones: {high: [], medium: [], default: low}
na_rules: {require_city: true, require_prefecture: true, require_property_type: true, require_age_unless_land: true}
"""


def test_load_explicit_path(tmp_path: Path):
    custom = tmp_path / "scoring.yaml"
    custom.write_text(_minimal_v21_yaml(), encoding="utf-8")
    cfg = load_scoring_config(custom)
    assert cfg["weights"]["location"] == 30


# --- Validation: weights -------------------------------------------------

def test_negative_weight_rejected(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        _minimal_v21_yaml().replace(
            "weights: {location: 30, yield: 30, loan: 20, stagnation: 10, risk: 10}",
            "weights: {location: -1, yield: 30, loan: 20, stagnation: 10, risk: 10}",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ScoringConfigError):
        load_scoring_config(bad)


def test_weights_not_summing_to_100_rejected(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        _minimal_v21_yaml().replace(
            "weights: {location: 30, yield: 30, loan: 20, stagnation: 10, risk: 10}",
            "weights: {location: 50, yield: 30, loan: 20, stagnation: 10, risk: 10}",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ScoringConfigError):
        load_scoring_config(bad)


def test_weights_keys_mismatch_rejected(tmp_path: Path):
    """v2.0 のキー（yield/price/condition/rarity）は v2.1 では拒否."""
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        _minimal_v21_yaml().replace(
            "weights: {location: 30, yield: 30, loan: 20, stagnation: 10, risk: 10}",
            "weights: {yield: 40, price: 30, condition: 20, rarity: 10}",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ScoringConfigError):
        load_scoring_config(bad)


# --- Validation: thresholds ----------------------------------------------

def test_thresholds_must_be_descending(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        _minimal_v21_yaml().replace(
            "rank_S: 80",
            "rank_S: 50",
        ),
        encoding="utf-8",
    )
    with pytest.raises(ScoringConfigError):
        load_scoring_config(bad)


# --- Validation: missing keys --------------------------------------------

def test_missing_top_level_key_rejected(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    # weights だけのminimal
    bad.write_text(
        "weights: {location: 30, yield: 30, loan: 20, stagnation: 10, risk: 10}\n",
        encoding="utf-8",
    )
    with pytest.raises(ScoringConfigError):
        load_scoring_config(bad)


def test_unknown_top_level_key_rejected(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        _minimal_v21_yaml() + "\nunknown_section: {foo: 1}\n",
        encoding="utf-8",
    )
    with pytest.raises(ScoringConfigError):
        load_scoring_config(bad)


# --- File errors ---------------------------------------------------------

def test_missing_file_raises(tmp_path: Path):
    with pytest.raises(ScoringConfigError):
        load_scoring_config(tmp_path / "nope.yaml")


# --- v2.2: s_rank_max_price ---------------------------------------------

def test_default_yaml_includes_s_rank_max_price():
    cfg = load_scoring_config()
    assert cfg["thresholds"]["s_rank_max_price"] == 30_000_000


def test_s_rank_max_price_required(tmp_path: Path):
    """thresholds から s_rank_max_price を抜くと拒否される."""
    bad = tmp_path / "bad.yaml"
    yaml_text = _minimal_v21_yaml().replace("  s_rank_max_price: 30000000\n", "")
    bad.write_text(yaml_text, encoding="utf-8")
    with pytest.raises(ScoringConfigError):
        load_scoring_config(bad)


# --- v2.3: hazard_high entries + property_type_modifiers ----------------

def test_default_yaml_hazard_high_has_entries():
    """v2.3 で hazard_caution_zones.high にエントリが入っていること."""
    cfg = load_scoring_config()
    high = cfg["hazard_caution_zones"]["high"]
    assert len(high) >= 3, f"hazard high zones should have >=3 entries, got {len(high)}"
    # 各エントリは dict（city + station_keywords）
    for z in high:
        assert isinstance(z, dict)
        assert "city" in z
        assert "station_keywords" in z


def test_property_type_modifiers_section_optional(tmp_path: Path):
    """property_type_modifiers が無くてもバリデーション通る（後方互換）."""
    custom = tmp_path / "no_modifiers.yaml"
    yaml_text = _minimal_v21_yaml()  # 含めない
    custom.write_text(yaml_text, encoding="utf-8")
    # OPTIONAL_TOP_LEVEL に含まれているはず → load 成功
    cfg = load_scoring_config(custom)
    # property_type_modifiers が無くてもクラッシュしない
    assert cfg.get("property_type_modifiers", {}) == {} or "property_type_modifiers" not in cfg


def test_default_yaml_includes_property_type_modifiers():
    """v2.3 default config に property_type_modifiers が定義されている."""
    cfg = load_scoring_config()
    mods = cfg.get("property_type_modifiers", {})
    assert "一棟売りマンション" in mods
    assert "一棟売りアパート" in mods
    assert mods["一棟売りアパート"]["s_rank_min_yield"] == 20
    assert mods["一棟売りマンション"]["s_rank_min_loan"] == 15
