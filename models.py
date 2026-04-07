"""Pydantic models for L-008 Real Estate Aggregator."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


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
    """Unified property schema (Tech Spec Data Model)."""
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
