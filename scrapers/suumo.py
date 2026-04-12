"""SUUMO (suumo.jp) scraper — used-mansion listings in Fukuoka."""
from __future__ import annotations

import asyncio
import hashlib
import logging

from datetime import datetime
from typing import Optional

from playwright.async_api import async_playwright, Page

from models import Property, SearchQuery
from scrapers.base import BaseScraper, capture_diagnostics
from scrapers.config import (
    SUUMO_SELECTORS as SEL,
    MAX_PAGES,
    REQUEST_INTERVAL_SEC,
    PAGE_TIMEOUT_MS,
    USER_AGENT,
)

logger = logging.getLogger(__name__)


class SuumoScraper(BaseScraper):
    """Scraper for SUUMO used-mansion (中古マンション) listings."""

    site_name = "SUUMO"

    async def search(self, query: SearchQuery) -> list[Property]:
        """Scrape SUUMO and return Property list."""
        properties: list[Property] = []
        first_url_error: Optional[Exception] = None
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 800},
                extra_http_headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
                    "Accept-Encoding": "gzip, deflate, br",
                    "Upgrade-Insecure-Requests": "1",
                },
            )
            page = await context.new_page()
            page.set_default_timeout(PAGE_TIMEOUT_MS)

            try:
                urls = self._build_urls(query)
                for idx, url in enumerate(urls):
                    try:
                        url_props = await self._scrape_url(page, url)
                        properties.extend(url_props)
                    except Exception as exc:
                        logger.error("SUUMO %s failed: %s", url, exc)
                        if idx == 0:
                            # First URL failure - propagate so orchestrator sees it
                            first_url_error = exc
                    await asyncio.sleep(REQUEST_INTERVAL_SEC)
            finally:
                await browser.close()

        # If we got nothing at all and the first URL failed, propagate the error
        if not properties and first_url_error is not None:
            raise first_url_error

        return properties

    # ---------- URL building ----------
    def _build_urls(self, query: SearchQuery) -> list[str]:
        """Build SUUMO search URLs.

        SUUMO has no investment category, so we search per city ward.
        If query.city specifies a ward, search that ward only.
        Otherwise search all Fukuoka city wards.
        """
        city_codes = SEL["city_codes"]
        base = SEL["base_url"] + SEL["search_path"]

        if query.city:
            # Try to match a specific ward
            for ward_name, code in city_codes.items():
                if query.city in ward_name or ward_name in query.city:
                    return [base + code + "/"]
            # City specified but not in our mapping - search all
            logger.warning("SUUMO: city '%s' not in mapping, searching all wards", query.city)

        # Default: major 3 wards only (memory-safe for Render)
        major_wards = ["福岡市博多区", "福岡市中央区", "福岡市南区"]
        return [base + city_codes[w] + "/" for w in major_wards if w in city_codes]

    # ---------- per-URL scraping ----------
    async def _scrape_url(self, page: Page, url: str) -> list[Property]:
        """Scrape one SUUMO listing URL with pagination."""
        properties: list[Property] = []
        logger.info("SUUMO: navigating to %s", url)
        # Use 'load' to allow post-HTML scripts to render the property list.
        resp = await page.goto(url, wait_until="load", timeout=45000)
        # On Render Free, JS rendering can be slow after HOME'S consumed CPU.
        # Wait for network to settle before checking for the selector.
        try:
            await page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            logger.warning("SUUMO: networkidle timeout, proceeding anyway")
        # SUUMO: elements exist in DOM (qsa_count=20 confirmed) but
        # wait_for_selector misses them — likely a race where JS replaces DOM.
        # Skip wait_for_selector; poll query_selector_all with retries instead.
        items_found = False
        for _attempt in range(6):  # up to 30s total (6 × 5s)
            els = await page.query_selector_all(SEL["item"])
            if els:
                items_found = True
                logger.info("SUUMO: found %d items after %d attempts", len(els), _attempt + 1)
                break
            await asyncio.sleep(5)
        if not items_found:
            diag = await capture_diagnostics(page, resp, SEL["item"])
            raise RuntimeError("SUUMO no items after retries at " + url + ". " + diag)

        for page_num in range(1, MAX_PAGES + 1):
            page_props = await self._parse_page(page)
            properties.extend(page_props)
            logger.info("SUUMO page %d (%s): %d items", page_num, url.split("/")[-2], len(page_props))

            if page_num >= MAX_PAGES:
                break

            # Navigate to next page
            next_url = self._next_page_url(url, page_num + 1)
            await asyncio.sleep(REQUEST_INTERVAL_SEC)
            try:
                await page.goto(next_url, wait_until="domcontentloaded")
                await page.wait_for_selector(SEL["item"], timeout=10000)
            except Exception:
                logger.info("SUUMO: no more pages after %d", page_num)
                break

        return properties

    def _next_page_url(self, base_url: str, page_num: int) -> str:
        sep = "&" if "?" in base_url else "?"
        return "{}{}page={}".format(base_url, sep, page_num)

    # ---------- parsing ----------
    async def _parse_page(self, page: Page) -> list[Property]:
        items = await page.query_selector_all(SEL["item"])
        results: list[Property] = []
        for item in items:
            try:
                prop = await self._parse_item(item)
                if prop:
                    results.append(prop)
            except Exception as exc:
                logger.warning("SUUMO: failed to parse item: %s", exc)
        return results

    async def _parse_item(self, item) -> Optional[Property]:
        """Parse a single .property_unit element."""
        # Source URL from title link
        link_el = await item.query_selector(SEL["title_link"])
        href = await link_el.get_attribute("href") if link_el else ""
        source_url = SEL["base_url"] + href if href and not href.startswith("http") else (href or "")
        if not source_url:
            return None

        prop_id = hashlib.md5(source_url.encode()).hexdigest()

        # Parse dt/dd pairs from the dottable
        fields = await self._parse_dottable(item)

        name = fields.get("物件名", "")
        price_text = fields.get("販売価格", "")
        price = self.parse_price(price_text)
        address = fields.get("所在地", "")
        station = fields.get("沿線・駅", "")
        area_text = fields.get("専有面積", "")
        area = self.parse_area(area_text)
        layout = fields.get("間取り", "")
        built_text = fields.get("築年月", "")
        built_year = self.parse_built_year(built_text)

        city = self.extract_city(address)
        current_year = datetime.now().year
        age = (current_year - built_year) if built_year else None

        # Image
        img_el = await item.query_selector(SEL["image"])
        img_src = ""
        if img_el:
            img_src = (
                await img_el.get_attribute("data-src")
                or await img_el.get_attribute("src")
                or ""
            )
            # Filter out placeholder data-uri images
            if img_src.startswith("data:"):
                img_src = ""

        return Property(
            id=prop_id,
            name=name,
            price=price,
            priceText=price_text,
            yieldGross=None,  # SUUMO has no yield info
            yieldNet=None,
            address=address,
            prefecture="福岡県",
            city=city,
            nearestStation=station,
            builtYear=built_year,
            age=age,
            layout=layout,
            area=area,
            structure="",  # Not available in SUUMO list view
            propertyType="区分マンション",  # SUUMO chuko mansions
            imageUrl=img_src,
            sourceUrl=source_url,
            sourceName=self.site_name,
        )

    async def _parse_dottable(self, item) -> dict[str, str]:
        """Extract dt/dd pairs from a property unit."""
        fields: dict[str, str] = {}
        dts = await item.query_selector_all("dt")
        dds = await item.query_selector_all("dd")
        for dt_el, dd_el in zip(dts, dds):
            key = (await dt_el.inner_text()).strip()
            val = (await dd_el.inner_text()).strip()
            if key:
                fields[key] = val
        return fields

    # Shared parsers inherited from BaseScraper:
    # parse_price, parse_built_year, parse_area, extract_city
