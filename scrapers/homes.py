"""HOME'S (toushi.homes.co.jp) scraper."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime
from typing import Optional

from playwright.async_api import async_playwright, Page

from models import Property, SearchQuery
from scrapers.base import BaseScraper, capture_diagnostics
from scrapers.config import (
    HOMES_SELECTORS as SEL,
    MAX_PAGES,
    REQUEST_INTERVAL_SEC,
    PAGE_TIMEOUT_MS,
    USER_AGENT,
)

logger = logging.getLogger(__name__)


class HomesScraper(BaseScraper):
    """Scraper for HOME'S investment property portal."""

    site_name = "HOME'S"

    # ---------- public API ----------
    async def search(self, query: SearchQuery) -> list[Property]:
        """Scrape HOME'S and return Property list."""
        properties: list[Property] = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 800},
            )
            page = await context.new_page()
            page.set_default_timeout(PAGE_TIMEOUT_MS)

            try:
                url = self._build_url(query)
                logger.info("HOME'S: navigating to %s", url)
                resp = await page.goto(url, wait_until="domcontentloaded")
                try:
                    await page.wait_for_selector(SEL["item"], timeout=15000)
                except Exception:
                    diag = await capture_diagnostics(page, resp, SEL["item"])
                    raise RuntimeError("HOME'S first page selector not found. " + diag)

                for page_num in range(1, MAX_PAGES + 1):
                    page_props = await self._parse_page(page)
                    properties.extend(page_props)
                    logger.info(
                        "HOME'S page %d: %d items (total %d)",
                        page_num, len(page_props), len(properties),
                    )

                    if page_num >= MAX_PAGES:
                        break

                    # Navigate to next page
                    next_url = self._next_page_url(url, page_num + 1)
                    await asyncio.sleep(REQUEST_INTERVAL_SEC)
                    await page.goto(next_url, wait_until="domcontentloaded")
                    # Check if items exist on next page
                    try:
                        await page.wait_for_selector(SEL["item"], timeout=10000)
                    except Exception:
                        logger.info("HOME'S: no more pages after %d", page_num)
                        break

            finally:
                await browser.close()

        return properties



    # ---------- internal ----------
    def _build_url(self, query: SearchQuery) -> str:
        """Build HOME'S search URL from query parameters."""
        base = SEL["base_url"] + SEL["search_path"]
        params: list[str] = []

        # Price filter
        if query.priceMin is not None:
            man = query.priceMin // 10000
            params.append("pri1={}".format(man))
        if query.priceMax is not None:
            man = query.priceMax // 10000
            params.append("pri2={}".format(man))

        # Yield filter
        if query.yieldMin is not None:
            params.append("yie1={}".format(int(query.yieldMin)))

        # Property type filter: map to HOME'S tbg[] codes
        type_map = {
            "区分マンション": "1",
            "投資用マンション": "1",
            "一棟アパート": "2",
            "一棟売りアパート": "2",
            "一棟マンション": "3",
            "一棟売りマンション": "3",
            "戸建": "8",
            "戸建賃貸": "8",
        }
        if query.propertyType:
            for pt in query.propertyType:
                code = type_map.get(pt)
                if code:
                    params.append("tbg[]={}".format(code))

        # Age filter
        if query.ageMax is not None:
            params.append("cny2={}".format(query.ageMax))

        if params:
            return base + "?" + "&".join(params)
        return base

    def _next_page_url(self, base_url: str, page_num: int) -> str:
        """Append page parameter."""
        sep = "&" if "?" in base_url else "?"
        return "{}{}page={}".format(base_url, sep, page_num)

    async def _parse_page(self, page: Page) -> list[Property]:
        """Parse all property items on the current page."""
        items = await page.query_selector_all(SEL["item"])
        results: list[Property] = []
        for item in items:
            try:
                prop = await self._parse_item(item, page)
                if prop:
                    results.append(prop)
            except Exception as exc:
                logger.warning("HOME'S: failed to parse item: %s", exc)
        return results

    async def _parse_item(self, item, page: Page) -> Optional[Property]:
        """Parse a single property list item element."""
        # Source URL
        link_el = await item.query_selector(SEL["link"])
        href = await link_el.get_attribute("href") if link_el else ""
        source_url = SEL["base_url"] + href if href and not href.startswith("http") else (href or "")
        if not source_url:
            return None

        # ID = md5 of source URL
        prop_id = hashlib.md5(source_url.encode()).hexdigest()

        # Name
        name_el = await item.query_selector(SEL["name"])
        name = (await name_el.inner_text()).strip() if name_el else ""

        # Price
        price_el = await item.query_selector(SEL["price"])
        price_text = (await price_el.inner_text()).strip() if price_el else ""
        price = self.parse_price(price_text)

        # Yield
        yield_el = await item.query_selector(SEL["yield_gross"])
        yield_text = (await yield_el.inner_text()).strip() if yield_el else ""
        yield_gross = self._parse_yield(yield_text)

        # Address
        addr_el = await item.query_selector(SEL["address"])
        address = (await addr_el.inner_text()).strip() if addr_el else ""

        # Station
        station_el = await item.query_selector(SEL["station"])
        station = (await station_el.inner_text()).strip() if station_el else ""

        # Built year
        age_el = await item.query_selector(SEL["built_year"])
        age_text = (await age_el.inner_text()).strip() if age_el else ""
        built_year = self.parse_built_year(age_text)

        # Area
        area_el = await item.query_selector(SEL["area"])
        area_text = (await area_el.inner_text()).strip() if area_el else ""
        area = self.parse_area(area_text)

        # Structure
        struct_el = await item.query_selector(SEL["structure"])
        structure = (await struct_el.inner_text()).strip() if struct_el else ""
        structure = structure.replace("建物構造", "").strip()

        # Property type
        type_el = await item.query_selector(SEL["property_type"])
        property_type = (await type_el.inner_text()).strip() if type_el else ""

        # Image
        img_el = await item.query_selector(SEL["image"])
        # If selector matches a non-img element (div/figure), look for child img
        if img_el:
            tag = await img_el.evaluate("el => el.tagName.toLowerCase()")
            if tag != "img":
                img_el = await img_el.query_selector("img") or img_el
        img_src = ""
        if img_el:
            img_src = (
                await img_el.get_attribute("data-src")
                or await img_el.get_attribute("src")
                or ""
            )
            if img_src.startswith("data:"):
                img_src = ""
            if img_src and not img_src.startswith("http"):
                img_src = SEL["base_url"] + img_src

        # City extraction from address
        city = self.extract_city(address)

        # Age calculation
        current_year = datetime.now().year
        age = (current_year - built_year) if built_year else None

        return Property(
            id=prop_id,
            name=name,
            price=price,
            priceText=price_text,
            yieldGross=yield_gross,
            yieldNet=None,
            address=address,
            prefecture="福岡県" if "福岡" in address else self.extract_prefecture(address),
            city=city,
            nearestStation=station,
            builtYear=built_year,
            age=age,
            layout="",  # HOME'S list view doesn't show layout directly
            area=area,
            structure=structure,
            propertyType=property_type,
            imageUrl=img_src,
            sourceUrl=source_url,
            sourceName=self.site_name,
        )

    # ---------- HOME'S-specific parsers ----------
    @staticmethod
    def _parse_yield(text: str) -> Optional[float]:
        """Parse '7.13％' or '7.13%' -> float."""
        if not text:
            return None
        nums = re.findall(r"[\d.]+", text)
        if nums:
            try:
                return float(nums[0] + ("." + nums[1] if len(nums) > 1 else ""))
            except (ValueError, IndexError):
                pass
        return None
