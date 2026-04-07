"""Orchestrator: run scrapers in parallel and merge results."""
from __future__ import annotations

import asyncio
import importlib
import logging
import time
from datetime import datetime

from models import Meta, Property, ScraperError, SearchQuery, SearchResponse
from scrapers.config import SCRAPER_REGISTRY
from services.dedup import flag_duplicates

logger = logging.getLogger(__name__)


def _load_scrapers():
    """Dynamically load all scrapers from SCRAPER_REGISTRY (E5: plugin arch)."""
    instances = []
    for site_name, entry in SCRAPER_REGISTRY.items():
        module = importlib.import_module(entry["module"])
        cls = getattr(module, entry["class_name"])
        instances.append(cls())
    return instances


async def run_search(query: SearchQuery) -> SearchResponse:
    """Execute all scrapers in parallel, merge, dedup, and return response."""
    start = time.time()

    scrapers = _load_scrapers()

    # Run all scrapers concurrently
    tasks = [_safe_scrape(s, query) for s in scrapers]
    results = await asyncio.gather(*tasks)

    # Merge
    all_properties: list[Property] = []
    errors: list[ScraperError] = []
    by_source: dict[str, int] = {}

    for props, errs, site_name in results:
        all_properties.extend(props)
        errors.extend(errs)
        by_source[site_name] = len(props)

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
    scraper, query: SearchQuery, max_retries: int = 2
) -> tuple[list[Property], list[ScraperError], str]:
    """Run a single scraper with retry + error handling (E4: site failure isolation)."""
    site_name = scraper.site_name
    last_exc = None
    for attempt in range(1, max_retries + 1):
        try:
            props = await scraper.search(query)
            return (props, [], site_name)
        except Exception as exc:
            last_exc = exc
            logger.warning("%s attempt %d/%d failed: %s", site_name, attempt, max_retries, exc)
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)  # exponential backoff
    logger.error("%s failed after %d retries: %s", site_name, max_retries, last_exc)
    error = ScraperError(
        siteName=site_name,
        errorType="SITE_DOWN",
        message=str(last_exc),
        retryCount=max_retries,
    )
    return ([], [error], site_name)
