"""Pydantic models for L-008 Real Estate Aggregator."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# 投資物件の最低価格ガード（賃貸・極端な安値を除外）
# 月額5万円の賃貸物件（price=50,000）を確実に弾くための閾値
MIN_INVESTMENT_PRICE_YEN = 1_000_000  # 100万円


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
    """Unified property schema (Tech Spec Data Model).

    価格ガード: MIN_INVESTMENT_PRICE_YEN (100万円) 未満の物件は
    ValidationError を発生させる。スクレイパー側は try/except で握りつぶし
    スキップする想定。これにより月5万円等の賃貸物件が確実に除外される。
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

    @field_validator("price")
    @classmethod
    def validate_investment_price(cls, v: Optional[int]) -> Optional[int]:
        """100万円未満（賃貸・極端な安値）を拒否。priceがNoneは許容（利回り不明物件用）。"""
        if v is not None and v < MIN_INVESTMENT_PRICE_YEN:
            raise ValueError(
                "price {} yen is below investment threshold ({} yen). "
                "Likely a rental listing.".format(v, MIN_INVESTMENT_PRICE_YEN)
            )
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
