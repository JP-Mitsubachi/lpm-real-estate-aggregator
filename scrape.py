"""Standalone scraper — runs all sites and writes properties.json.

Run this in GitHub Actions to generate static data for the frontend.
Usage: python scrape.py [--output FILE] [--city CITY]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from models import SearchQuery
from services.orchestrator import run_search

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def main():
    parser = argparse.ArgumentParser(description="L-008 scraper — writes properties.json")
    parser.add_argument("--output", default="static/data/properties.json",
                        help="Output JSON file path")
    parser.add_argument("--city", default=None,
                        help="Filter by city (e.g. '福岡市博多区'). Omit for all wards.")
    args = parser.parse_args()

    query = SearchQuery(prefecture="福岡県", city=args.city)
    logger.info("Starting scrape: city=%s", args.city or "ALL")

    start = datetime.utcnow()
    result = await run_search(query)
    elapsed = (datetime.utcnow() - start).total_seconds()

    # Load previous data for diff detection
    out_path = Path(args.output)
    prev_ids: set[str] = set()
    if out_path.exists():
        try:
            prev_data = json.loads(out_path.read_text(encoding="utf-8"))
            prev_ids = {p["id"] for p in prev_data.get("properties", [])}
            logger.info("Previous data: %d properties", len(prev_ids))
        except Exception:
            logger.warning("Could not read previous data, treating all as new")

    # Convert to plain dict for JSON
    new_props = []
    current_ids: set[str] = set()
    for p in result.properties:
        d = p.model_dump()
        current_ids.add(d["id"])
        d["isNew"] = d["id"] not in prev_ids  # 前回にないIDは新着
        new_props.append(d)

    removed_count = len(prev_ids - current_ids)

    output = {
        "properties": new_props,
        "meta": result.meta.model_dump(),
        "diff": {
            "newCount": sum(1 for p in new_props if p["isNew"]),
            "removedCount": removed_count,
            "totalPrev": len(prev_ids),
        },
        "generatedAt": datetime.utcnow().isoformat() + "Z",
        "elapsedSec": round(elapsed, 1),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    logger.info("Wrote %d properties to %s (%.1fs)",
                len(output["properties"]), out_path, elapsed)
    logger.info("By source: %s", output["meta"]["bySource"])
    logger.info("Diff: +%d new, -%d removed (prev: %d)",
                output["diff"]["newCount"], removed_count, len(prev_ids))

    if output["meta"].get("errors"):
        logger.warning("Errors: %s", output["meta"]["errors"])

    # Exit with error code if all sites failed
    if len(output["properties"]) == 0:
        logger.error("No properties obtained from any site")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
