"""Pydantic models for L-008 Real Estate Aggregator (v2.1)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# 投資物件の最低価格ガード（賃貸・極端な安値を除外）
MIN_INVESTMENT_PRICE_YEN = 1_000_000  # 100万円

# v2.1: AI掘り出し物スコアリング（PRD §5）
DEAL_RANKS = ("S", "A", "B", "C", "D", "N/A")
LOCATION_GRADES = ("S", "A", "B", "C", "D")
LINE_RANKS = ("S", "A", "B", "C")
HAZARD_FLAGS = ("high", "medium", "low")
DEAL_MODEL_VERSION_DEFAULT = "v2.5"


class SearchQuery(BaseModel):
    """Search query from frontend."""
    prefecture: str = "福岡県"
    city: Optional[str] = None
    priceMin: Optional[int] = None      # yen
    priceMax: Optional[int] = None      # yen
    yieldMin: Optional[float] = None    # %
    layout: Optional[list[str]] = None  # e.g. ["1K", "1DK"]
    ageMax: Optional[int] = None        # years
    propertyType: Optional[list[str]] = None  # e.g. ["区分マンション"]


class Property(BaseModel):
    """Unified property schema (v2.1, Tech Spec Data Model).

    価格ガード: MIN_INVESTMENT_PRICE_YEN (100万円) 未満の物件は
    ValidationError を発生させる。
    """
    id: str
    name: str
    price: Optional[int] = None
    priceText: str = ""
    yieldGross: Optional[float] = None
    yieldNet: Optional[float] = None
    address: str = ""
    prefecture: str = "福岡県"
    city: str = ""
    nearestStation: str = ""
    builtYear: Optional[int] = None
    age: Optional[int] = None
    layout: str = ""
    area: Optional[float] = None
    structure: str = ""
    propertyType: str = ""
    imageUrl: str = ""
    sourceUrl: str = ""
    sourceName: str = ""
    scrapedAt: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    duplicateFlag: bool = False
    duplicateCandidates: list[str] = Field(default_factory=list)

    # --- v1.0 → v2.1 共通: スコアリングコア ---
    dealScore: Optional[int] = None
    dealRank: Optional[str] = None
    dealReasons: list[str] = Field(default_factory=list)
    yieldMedianInArea: Optional[float] = None
    pricePerSqmMedian: Optional[float] = None
    yieldDeviation: Optional[float] = None
    dealModelVersion: str = DEAL_MODEL_VERSION_DEFAULT
    isAutoFallback: bool = False

    # --- v2.1 新規フィールド（PRD §5.3.1） ---
    walkMinutes: Optional[int] = None
    locationGrade: Optional[str] = None       # S/A/B/C/D
    lineRank: Optional[str] = None            # S/A/B/C
    remainingDurableYears: Optional[int] = None
    locationScore: Optional[int] = None       # 0-30
    yieldBenchmarkScore: Optional[int] = None  # 0-30
    loanScore: Optional[int] = None           # 0-20
    stagnationScore: int = 0                  # 0-10 (v2.1は0固定)
    riskScore: Optional[int] = None           # 0-10
    inRedevelopmentZone: bool = False
    hazardFlag: Optional[str] = None          # "high"/"medium"/"low"
    structureEstimated: bool = False          # 構造推定フラグ
    benchmarkCapRate: Optional[float] = None  # 適用したベンチマーク値
    # v2.2: タイブレーカー用の合成ランク値（dealScore + 0.0xx）
    compositeRankValue: Optional[float] = None

    # v2.4: AI 根拠生成のタイムスタンプ（差分検出ヒント用）
    aiReasonsGeneratedAt: Optional[str] = None

    # v2.5: 滞留シグナル基盤
    # firstSeenAt: 物件が初めてスクレイピングで観測された日時 (ISO8601)
    firstSeenAt: Optional[str] = None
    # priceHistory: 価格変更履歴（最大10件、{"date": "YYYY-MM-DD", "price": int}）
    priceHistory: list[dict] = Field(default_factory=list)

    @field_validator("price")
    @classmethod
    def validate_investment_price(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < MIN_INVESTMENT_PRICE_YEN:
            raise ValueError(
                "price {} yen is below investment threshold ({} yen). "
                "Likely a rental listing.".format(v, MIN_INVESTMENT_PRICE_YEN)
            )
        return v

    @field_validator("dealScore")
    @classmethod
    def validate_deal_score(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v < 0 or v > 100:
            raise ValueError(f"dealScore must be 0-100, got {v}")
        return v

    @field_validator("dealRank")
    @classmethod
    def validate_deal_rank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in DEAL_RANKS:
            raise ValueError(f"dealRank must be one of {DEAL_RANKS}, got {v!r}")
        return v

    @field_validator("locationGrade")
    @classmethod
    def validate_location_grade(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in LOCATION_GRADES:
            raise ValueError(f"locationGrade must be one of {LOCATION_GRADES}, got {v!r}")
        return v

    @field_validator("lineRank")
    @classmethod
    def validate_line_rank(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in LINE_RANKS:
            raise ValueError(f"lineRank must be one of {LINE_RANKS}, got {v!r}")
        return v

    @field_validator("hazardFlag")
    @classmethod
    def validate_hazard_flag(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if v not in HAZARD_FLAGS:
            raise ValueError(f"hazardFlag must be one of {HAZARD_FLAGS}, got {v!r}")
        return v

    @field_validator("locationScore")
    @classmethod
    def validate_location_score(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v < 0 or v > 30:
            raise ValueError(f"locationScore must be 0-30, got {v}")
        return v

    @field_validator("yieldBenchmarkScore")
    @classmethod
    def validate_yield_benchmark_score(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v < 0 or v > 30:
            raise ValueError(f"yieldBenchmarkScore must be 0-30, got {v}")
        return v

    @field_validator("loanScore")
    @classmethod
    def validate_loan_score(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v < 0 or v > 20:
            raise ValueError(f"loanScore must be 0-20, got {v}")
        return v

    @field_validator("stagnationScore")
    @classmethod
    def validate_stagnation_score(cls, v: int) -> int:
        if v < 0 or v > 10:
            raise ValueError(f"stagnationScore must be 0-10, got {v}")
        return v

    @field_validator("riskScore")
    @classmethod
    def validate_risk_score(cls, v: Optional[int]) -> Optional[int]:
        if v is None:
            return v
        if v < 0 or v > 10:
            raise ValueError(f"riskScore must be 0-10, got {v}")
        return v

    @field_validator("compositeRankValue")
    @classmethod
    def validate_composite_rank_value(cls, v: Optional[float]) -> Optional[float]:
        if v is None:
            return v
        if v < 0 or v > 101:
            raise ValueError(f"compositeRankValue must be 0-101, got {v}")
        return v


class ScraperError(BaseModel):
    """Error from a single scraper."""
    siteName: str
    errorType: str  # TIMEOUT / BLOCKED / PARSE_ERROR / SITE_DOWN
    message: str
    retryCount: int = 0


class Meta(BaseModel):
    """Response metadata."""
    total: int = 0
    bySource: dict[str, int] = Field(default_factory=dict)
    duplicateCandidates: int = 0
    errors: list[ScraperError] = Field(default_factory=list)
    scrapedAt: str = Field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    elapsed: str = ""


class SearchResponse(BaseModel):
    """Top-level API response."""
    properties: list[Property] = Field(default_factory=list)
    meta: Meta = Field(default_factory=Meta)
