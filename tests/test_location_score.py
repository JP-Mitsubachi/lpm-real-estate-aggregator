"""Tests for services/location_score.py — 立地スコア (PRD §5.2.3, v2.1).

Coverage:
- 駅徒歩のパース（半角・全角・name/address フォールバック）
- エリアランク取得（10区分マスター）
- 路線ランク取得（6路線マスター）
- 立地スコア合計（cap=30）
- 再開発圏内判定
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Property  # noqa: E402
from services.location_score import (  # noqa: E402
    parse_walk_minutes,
    get_area_grade,
    get_line_rank,
    get_area_score,
    get_walk_score,
    get_line_bonus,
    calc_location_score,
    is_in_redevelopment_zone,
    get_hazard_flag,
)


# --- parse_walk_minutes ---------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("ＪＲ鹿児島本線「竹下」徒歩14分", 14),
    ("地下鉄空港線「赤坂」徒歩7分", 7),
    ("徒歩10分", 10),
    ("徒歩 10 分", 10),
    ("徒歩1分", 1),
    # ふれんず・HOME'S系は「歩X分」表記
    ("福岡市地下鉄空港線 赤坂駅 歩8分", 8),
    ("歩 5 分", 5),
])
def test_parse_walk_minutes_basic(text, expected):
    assert parse_walk_minutes(text) == expected


def test_parse_walk_minutes_returns_none_when_absent():
    # バス便（停歩4分）・車距離は徒歩扱いしない
    assert parse_walk_minutes("九州新幹線「博多」バス20分停歩4分") is None
    assert parse_walk_minutes("ＪＲ鹿児島本線「笹原」車1.8km") is None
    assert parse_walk_minutes("") is None
    assert parse_walk_minutes(None) is None


def test_parse_walk_minutes_picks_first_match():
    assert parse_walk_minutes("徒歩10分・自転車5分") == 10


def test_parse_walk_minutes_strict_takes_priority_over_loose():
    """同じ文に「徒歩」と「歩」両方ある場合は徒歩優先."""
    assert parse_walk_minutes("徒歩7分（または歩8分）") == 7


# --- get_area_grade -------------------------------------------------------

@pytest.mark.parametrize("city,expected", [
    ("福岡市中央区", "S"),
    ("福岡市博多区", "S"),
    ("福岡市早良区", "A"),
    ("福岡市南区", "A"),
    ("福岡市西区", "B"),
    ("福岡市城南区", "B"),
    ("福岡市東区", "B"),
    ("北九州市小倉北区", "C"),
    ("北九州市八幡西区", "C"),
    ("久留米市", "C"),
])
def test_get_area_grade_known_cities(city, expected):
    assert get_area_grade(city) == expected


def test_get_area_grade_unknown_returns_default_d():
    assert get_area_grade("春日市") == "D"
    assert get_area_grade("東京都港区") == "D"


def test_get_area_grade_empty_returns_none():
    assert get_area_grade("") is None
    assert get_area_grade(None) is None


# --- get_line_rank --------------------------------------------------------

@pytest.mark.parametrize("station_text,expected", [
    ("地下鉄空港線「赤坂」徒歩7分", "S"),
    ("ＪＲ鹿児島本線「竹下」徒歩14分", "A"),
    ("JR鹿児島本線「竹下」徒歩14分", "A"),
    ("西鉄天神大牟田線「大橋」徒歩5分", "A"),
    ("地下鉄七隈線「桜坂」徒歩6分", "B"),
    ("地下鉄箱崎線「貝塚」徒歩4分", "B"),
    ("ＪＲ筑肥線「下山門」徒歩8分", "C"),
])
def test_get_line_rank_known_lines(station_text, expected):
    assert get_line_rank(station_text) == expected


def test_get_line_rank_unknown_returns_none():
    assert get_line_rank("バス20分") is None
    assert get_line_rank("") is None


# --- get_area_score / get_walk_score / get_line_bonus -------------------

def test_area_score_mapping():
    assert get_area_score("S") == 20
    assert get_area_score("A") == 15
    assert get_area_score("B") == 10
    assert get_area_score("C") == 5
    assert get_area_score("D") == 0
    assert get_area_score(None) == 0


@pytest.mark.parametrize("walk,expected", [
    (1, 10),
    (10, 10),
    (11, 5),
    (15, 5),
    (16, 0),
    (30, 0),
    (None, 0),
])
def test_walk_score_thresholds(walk, expected):
    assert get_walk_score(walk) == expected


def test_line_bonus_mapping():
    assert get_line_bonus("S") == 3
    assert get_line_bonus("A") == 2
    assert get_line_bonus("B") == 1
    assert get_line_bonus("C") == 0
    assert get_line_bonus(None) == 0


# --- calc_location_score (integration) ----------------------------------

def test_location_score_central_full():
    """中央区S(20) + 徒歩7分(10) + 空港線(3) = 33 → cap30."""
    p = Property(
        id="x", name="n",
        city="福岡市中央区",
        nearestStation="地下鉄空港線「赤坂」徒歩7分",
    )
    assert calc_location_score(p) == 30


def test_location_score_haakata_with_jr():
    """博多区S(20) + 徒歩5分(10) + 鹿児島本線A(2) = 32 → cap30."""
    p = Property(
        id="x", name="n",
        city="福岡市博多区",
        nearestStation="ＪＲ鹿児島本線「博多」徒歩5分",
    )
    assert calc_location_score(p) == 30


def test_location_score_higashi_distant_walk():
    """東区B(10) + 徒歩18分(0) + 鹿児島本線A(2) = 12."""
    p = Property(
        id="x", name="n",
        city="福岡市東区",
        nearestStation="ＪＲ鹿児島本線「香椎」徒歩18分",
    )
    assert calc_location_score(p) == 12


def test_location_score_kitakyushu_low():
    """北九州市C(5) + 徒歩14分(5) + 路線評価なし(0) = 10."""
    p = Property(
        id="x", name="n",
        city="北九州市小倉北区",
        nearestStation="モノレール「旦過」徒歩14分",
    )
    assert calc_location_score(p) == 10


def test_location_score_unknown_city_gets_zero_area():
    p = Property(
        id="x", name="n",
        city="神奈川県横浜市",
        nearestStation="JR京浜東北線「桜木町」徒歩6分",
    )
    # area=D=0, walk=10, no fukuoka line -> 10
    assert calc_location_score(p) == 10


def test_location_score_no_walk_no_line_only_area():
    p = Property(
        id="x", name="n",
        city="福岡市早良区",
        nearestStation="バス便",
    )
    # A=15, walk=0, line=0
    assert calc_location_score(p) == 15


def test_location_score_empty_city_returns_none():
    p = Property(id="x", name="n", city="", nearestStation="徒歩7分")
    assert calc_location_score(p) is None


# --- redevelopment zone --------------------------------------------------

def test_in_redevelopment_zone_tenjin_bigbang():
    p = Property(
        id="x", name="n",
        city="福岡市中央区",
        nearestStation="地下鉄空港線「赤坂」徒歩5分",
    )
    assert is_in_redevelopment_zone(p) is True


def test_in_redevelopment_zone_hakata_connected():
    p = Property(
        id="x", name="n",
        city="福岡市博多区",
        nearestStation="ＪＲ鹿児島本線「博多」徒歩3分",
    )
    assert is_in_redevelopment_zone(p) is True


def test_in_redevelopment_zone_chihaya():
    p = Property(
        id="x", name="n",
        city="福岡市東区",
        nearestStation="ＪＲ鹿児島本線「千早」徒歩8分",
    )
    assert is_in_redevelopment_zone(p) is True


def test_in_redevelopment_zone_excluded_outside():
    p = Property(
        id="x", name="n",
        city="北九州市小倉北区",
        nearestStation="モノレール「旦過」徒歩5分",
    )
    assert is_in_redevelopment_zone(p) is False


def test_in_redevelopment_zone_city_match_required():
    """中央区でない（早良区）の駅名が天神の場合は対象外."""
    p = Property(
        id="x", name="n",
        city="福岡市早良区",
        nearestStation="天神付近の物件",  # cityが対象外
    )
    assert is_in_redevelopment_zone(p) is False


# --- hazard flag ---------------------------------------------------------

def test_hazard_flag_medium_for_hakata():
    assert get_hazard_flag("福岡市博多区") == "medium"


def test_hazard_flag_medium_for_sawara():
    assert get_hazard_flag("福岡市早良区") == "medium"


def test_hazard_flag_low_for_chuo():
    assert get_hazard_flag("福岡市中央区") == "low"


def test_hazard_flag_low_for_kitakyushu():
    assert get_hazard_flag("北九州市小倉北区") == "low"


def test_hazard_flag_low_for_unknown():
    assert get_hazard_flag("不明市") == "low"


# --- v2.3: hazard high (city + station) ----------------------------------

def test_hazard_high_for_hakata_minami_station():
    """博多区 + 南福岡駅（御笠川流域）→ high."""
    assert get_hazard_flag("福岡市博多区", "ＪＲ鹿児島本線「南福岡」徒歩5分") == "high"


def test_hazard_high_for_hakata_takeshita():
    """博多区 + 竹下駅（那珂川流域）→ high."""
    assert get_hazard_flag("福岡市博多区", "ＪＲ鹿児島本線「竹下」徒歩7分") == "high"


def test_hazard_high_for_sawara_minami():
    """早良区 + 樋井川流域駅 → high."""
    assert get_hazard_flag("福岡市早良区", "地下鉄空港線「藤崎」徒歩6分") == "high"


def test_hazard_medium_for_hakata_normal_station():
    """博多区 + 天神駅（流域外）→ high になってはいけない、medium 維持."""
    assert get_hazard_flag("福岡市博多区", "地下鉄空港線「天神」徒歩3分") == "medium"


def test_hazard_low_for_chuo_with_any_station():
    """中央区はどの駅でも low（中央区は high 対象外）."""
    assert get_hazard_flag("福岡市中央区", "地下鉄空港線「天神」徒歩3分") == "low"


def test_hazard_flag_no_station_falls_back_to_city_level():
    """station 引数なしの呼び出しは既存挙動（city-only judgment）を維持."""
    # 博多区はcity-onlyではmedium
    assert get_hazard_flag("福岡市博多区") == "medium"
    # 中央区は low
    assert get_hazard_flag("福岡市中央区") == "low"
