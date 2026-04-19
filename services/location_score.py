"""Location scoring (PRD §5.2.3, v2.1).

立地スコア = エリアランク（最大20点） + 駅徒歩（最大10点） + 路線ボーナス（最大3点）
合計を 30点 で頭打ち。

依存:
- city → エリアランク（10区分マスター）
- nearestStation 文字列 → 路線マッチ + 徒歩分パース
- name / address → 徒歩分のフォールバックパース
- 再開発エリア・ハザードフラグも本モジュールで提供
"""
from __future__ import annotations

import re
from typing import Optional

from models import Property
from services.config_loader import get_default_config

# --- 駅徒歩パース ---
# 「徒歩14分」「徒歩 7 分」「歩8分」「歩 8 分」など
# - 「徒歩」優先（明確な徒歩表記）、「歩」のみは fallback（HOME'S/ふれんず系で頻出）
# - 「停歩4分」（バス停から徒歩）は徒歩扱いせず None
# - 「車X分」は徒歩扱いせず None
_WALK_RE_STRICT = re.compile(r"徒歩\s*(\d+)\s*分")
_WALK_RE_LOOSE = re.compile(r"(?<![停車])歩\s*(\d+)\s*分")


def parse_walk_minutes(*texts: Optional[str]) -> Optional[int]:
    """name / nearestStation / address いずれかから徒歩分を抽出。

    優先順位: 「徒歩X分」→「歩X分」（停歩・車を除外）。
    バス便・車距離は対象外（None）。
    """
    # Pass 1: 厳密「徒歩」マッチ
    for text in texts:
        if not text:
            continue
        m = _WALK_RE_STRICT.search(text)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, TypeError):
                continue
    # Pass 2: 緩い「歩X分」（停歩・車は事前に除外）
    for text in texts:
        if not text:
            continue
        m = _WALK_RE_LOOSE.search(text)
        if m:
            try:
                return int(m.group(1))
            except (ValueError, TypeError):
                continue
    return None


# --- エリアランク ---

def get_area_grade(city: Optional[str]) -> Optional[str]:
    """city → "S"/"A"/"B"/"C"/"D"。空文字は None。未登録は default(D)。"""
    if not city:
        return None
    cfg = get_default_config()
    rank_map = cfg["area_rank"]
    if city in rank_map:
        return rank_map[city]
    return cfg["default_area_rank"]


def get_area_score(grade: Optional[str]) -> int:
    if grade is None:
        return 0
    cfg = get_default_config()
    return int(cfg["location_score"]["area_points"].get(grade, 0))


# --- 路線ランク ---

def get_line_rank(station_text: Optional[str]) -> Optional[str]:
    """nearestStation 文字列を部分一致で評価し、最も高い路線ランクを返す。"""
    if not station_text:
        return None
    cfg = get_default_config()
    route = cfg["route_rank"]
    # 上位ランクから順にチェック
    for rank in ("S", "A", "B", "C"):
        keywords = route.get(rank, [])
        for kw in keywords:
            if kw in station_text:
                return rank
    return None


def get_line_bonus(line_rank: Optional[str]) -> int:
    if line_rank is None:
        return 0
    cfg = get_default_config()
    return int(cfg["location_score"]["line_bonus"].get(line_rank, 0))


# --- 駅徒歩スコア ---

def get_walk_score(walk_minutes: Optional[int]) -> int:
    if walk_minutes is None:
        return 0
    cfg = get_default_config()
    ls = cfg["location_score"]
    th = ls["walk_thresholds"]
    pts = ls["walk_points"]
    if walk_minutes <= 0:
        return 0
    if walk_minutes <= th["full"]:
        return int(pts["within_10min"])
    if walk_minutes <= th["half"]:
        return int(pts["within_15min"])
    return int(pts["over_15min"])


# --- 立地スコア合計 ---

def calc_location_score(p: Property) -> Optional[int]:
    """合計 = areaScore + walkScore + lineBonus, cap=30. cityなしでNone。"""
    if not p.city:
        return None
    cfg = get_default_config()
    cap = int(cfg["location_score"]["cap"])

    grade = get_area_grade(p.city)
    walk = parse_walk_minutes(p.nearestStation, p.address, p.name)
    line_rank = get_line_rank(p.nearestStation or "")

    total = (
        get_area_score(grade)
        + get_walk_score(walk)
        + get_line_bonus(line_rank)
    )
    return min(cap, max(0, total))


# --- 再開発圏内 ---

def is_in_redevelopment_zone(p: Property) -> bool:
    """city が対象 + nearestStation キーワードヒットで True."""
    if not p.city:
        return False
    cfg = get_default_config()
    zones = cfg["redevelopment_zones"]
    station_text = p.nearestStation or ""
    for z in zones:
        if p.city in z["cities"]:
            for kw in z["station_keywords"]:
                if kw in station_text or kw in (p.address or "") or kw in (p.name or ""):
                    return True
            # cityが対象でかつ駅名キーワードが空なら city 単独でtrue
            # 現在のmasterでは station_keywords ありなので、駅名/住所/名前マッチが必要
    return False


# --- ハザードフラグ ---

def get_hazard_flag(city: Optional[str], station_text: Optional[str] = None) -> str:
    """v2.3: city × 駅文字列の組合せで判定（過剰判定回避）。

    - high: hazard_caution_zones.high の各エントリ {city, station_keywords}
            のうち city一致 ∧ station_keywords いずれかが station_text に含まれる
    - medium: hazard_caution_zones.medium のシンプルな city-list（v2.1互換）
    - その他: default（"low"）

    station_text が None の場合は high 判定をスキップし city-only で評価する
    （後方互換: 既存の get_hazard_flag(city) は medium/low 判定のみ）。
    """
    if not city:
        return "low"
    cfg = get_default_config()
    hz = cfg["hazard_caution_zones"]
    high_entries = hz.get("high") or []
    medium_list = hz.get("medium") or []

    # v2.3: city × station 組合せで high 判定
    if station_text:
        for entry in high_entries:
            if not isinstance(entry, dict):
                # 旧 v2.1 形式（city文字列）への後方互換
                if entry == city:
                    return "high"
                continue
            if entry.get("city") != city:
                continue
            keywords = entry.get("station_keywords") or []
            for kw in keywords:
                if kw in station_text:
                    return "high"

    if city in medium_list:
        return "medium"
    return hz.get("default", "low")
