"""Orchestrator: run scrapers sequentially to save memory on Render."""
from __future__ import annotations

import importlib
import logging
import time
from datetime import datetime

from models import Meta, Property, ScraperError, SearchQuery, SearchResponse
from scrapers.config import SCRAPER_REGISTRY
from services.dedup import flag_duplicates

logger = logging.getLogger(__name__)


def _load_scrapers():
    """Dynamically load all scrapers from SCRAPER_REGISTRY."""
    instances = []
    for site_name, entry in SCRAPER_REGISTRY.items():
        module = importlib.import_module(entry["module"])
        cls = getattr(module, entry["class_name"])
        instances.append(cls())
    return instances


async def run_search(query: SearchQuery) -> SearchResponse:
    """Execute scrapers sequentially (memory-safe for Render Free tier)."""
    start = time.time()

    scrapers = _load_scrapers()

    all_properties: list[Property] = []
    errors: list[ScraperError] = []
    by_source: dict[str, int] = {}

    # Run sequentially to avoid 3 Chromium instances at once
    for scraper in scrapers:
        props, errs, site_name = await _safe_scrape(scraper, query)
        all_properties.extend(props)
        errors.extend(errs)
        by_source[site_name] = len(props)
        logger.info("%s: %d properties", site_name, len(props))

    # Dedup
    dup_count = flag_duplicates(all_properties)

    elapsed = time.time() - start

    meta = Meta(
        total=len(all_properties),
        bySource=by_source,
        duplicateCandidates=dup_count,
        errors=errors,
        scrapedAt=datetime.utcnow().isoformat() + "Z",
        elapsed="{:.1f}s".format(elapsed),
    )

    return SearchResponse(properties=all_properties, meta=meta)


async def _safe_scrape(
    scraper, query: SearchQuery, max_retries: int = 1
) -> tuple[list[Property], list[ScraperError], str]:
    """Run a single scraper with error handling."""
    site_name = scraper.site_name
    try:
        props = await scraper.search(query)
        return (props, [], site_name)
    except Exception as exc:
        logger.error("%s failed: %s", site_name, exc)
        error = ScraperError(
            siteName=site_name,
            errorType="SITE_DOWN",
            message=str(exc),
            retryCount=0,
        )
        return ([], [error], site_name)
