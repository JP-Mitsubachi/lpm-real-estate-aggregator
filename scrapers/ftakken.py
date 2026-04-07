"""f-takken.com (ふれんず - 福岡県宅建協会) scraper via internal API."""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import unquote

from playwright.async_api import async_playwright

from models import Property, SearchQuery
from scrapers.base import BaseScraper
from scrapers.config import (
    FTAKKEN_SELECTORS as SEL,
    REQUEST_INTERVAL_SEC,
    PAGE_TIMEOUT_MS,
    USER_AGENT,
)

logger = logging.getLogger(__name__)

# f-takken area codes for Fukuoka city wards
FUKUOKA_WARD_CODES = {
    "福岡市東区": "40131",
    "福岡市博多区": "40132",
    "福岡市中央区": "40133",
    "福岡市南区": "40134",
    "福岡市西区": "40135",
    "福岡市城南区": "40136",
    "福岡市早良区": "40137",
}

# listtype mapping
LISTTYPE_MAP = {
    "区分マンション": "mansion",
    "投資用マンション": "mansion",
    "一棟アパート": "other",
    "一棟売りアパート": "other",
    "一棟マンション": "other",
    "一棟売りマンション": "other",
    "戸建": "detached",
    "戸建賃貸": "detached",
}


class FtakkenScraper(BaseScraper):
    """Scraper for f-takken.com (ふれんず) using its internal getBdata API.

    f-takken is a SPA that loads data via POST to getBdata.php.
    We use Playwright to get a valid session context, then call the API directly.
    """

    site_name = "ふれんず"

    async def search(self, query: SearchQuery) -> list[Property]:
        """Scrape f-takken and return Property list."""
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
                # Navigate to establish session context (needed for jQuery + appConfig)
                await page.goto(
                    SEL["base_url"] + "/freins/buy/mansion",
                    wait_until="domcontentloaded",
                )
                await asyncio.sleep(2)

                # Determine area codes to search
                area_codes = self._get_area_codes(query)

                # Determine listtypes to search
                listtypes = self._get_listtypes(query)

                for listtype in listtypes:
                    for area_code in area_codes:
                        await asyncio.sleep(REQUEST_INTERVAL_SEC)
                        items = await self._fetch_api(
                            page, listtype, area_code, query
                        )
                        properties.extend(items)
                        logger.info(
                            "f-takken %s area=%s: %d items (total %d)",
                            listtype, area_code, len(items), len(properties),
                        )

            except Exception as exc:
                logger.error("f-takken scraping error: %s", exc)
            finally:
                await browser.close()

        return properties

    def _get_area_codes(self, query: SearchQuery) -> list[str]:
        """Determine area codes from query."""
        if query.city:
            for ward_name, code in FUKUOKA_WARD_CODES.items():
                if query.city in ward_name or ward_name in query.city:
                    return [code]
        # Default: all Fukuoka city wards
        return list(FUKUOKA_WARD_CODES.values())

    def _get_listtypes(self, query: SearchQuery) -> list[str]:
        """Determine listtypes from query property types."""
        if query.propertyType:
            types = set()
            for pt in query.propertyType:
                lt = LISTTYPE_MAP.get(pt, "mansion")
                types.add(lt)
            return list(types)
        # Default: mansion + other (covers most investment properties)
        return ["mansion", "other"]

    async def _fetch_api(
        self,
        page,
        listtype: str,
        area_code: str,
        query: SearchQuery,
    ) -> list[Property]:
        """Call getBdata.php API and parse results."""
        params = {
            "offset": 0,
            "limit": SEL["max_items"],
            "listtype": listtype,
            "lang": "ja",
            "location": "area",
            "locate[]": area_code,
            "order1": "pl",
            "key": SEL["api_key"],
        }

        # Price filter (万円 unit)
        if query.priceMin is not None:
            params["data_21"] = str(int(query.priceMin / 10000))
        if query.priceMax is not None:
            params["data_22"] = str(int(query.priceMax / 10000))

        result = await page.evaluate(
            """(params) => {
            return new Promise(function(resolve) {
                $.ajax({
                    url: appConfig.apiPath + 'getBdata.php',
                    type: 'POST', dataType: 'JSON', data: params,
                    success: function(d) {
                        var items = [];
                        if (d.data) {
                            d.data.forEach(function(item) {
                                var decoded = {};
                                Object.keys(item).forEach(function(k) {
                                    try { decoded[k] = decodeURIComponent(item[k]); }
                                    catch(e) { decoded[k] = item[k]; }
                                });
                                items.push(decoded);
                            });
                        }
                        resolve({total: d.totalrow, items: items});
                    },
                    error: function(err) { resolve({error: err.status || 'unknown'}); }
                });
                setTimeout(function() { resolve({error: 'timeout'}); }, 15000);
            });
        }""",
            params,
        )

        if "error" in result:
            logger.warning("f-takken API error: %s", result["error"])
            return []

        properties = []
        for item in result.get("items", []):
            prop = self._parse_item(item, listtype)
            if prop:
                properties.append(prop)
        return properties

    def _parse_item(self, item: dict, listtype: str) -> Optional[Property]:
        """Parse a single API response item into Property."""
        bno = item.get("d003", "")
        if not bno:
            return None

        source_url = "{}/freins/items/{}".format(SEL["base_url"], bno)
        prop_id = hashlib.md5(source_url.encode()).hexdigest()

        name = item.get("d021", "").strip()
        price_text = item.get("d048unit", "")
        price_val = item.get("d048")
        price = int(float(price_val) * 10000) if price_val else None

        # Address
        prefecture = item.get("kanji_1", "福岡県")
        city = item.get("kanji_2", "") or item.get("addrname2", "")
        town = item.get("kanji_3", "") or item.get("addrname3", "")
        address = prefecture + city + town

        # Station
        station_name = item.get("eki", "")
        line_name = item.get("ensen", "")
        walk_info = item.get("d029name", "")
        nearest_station = ""
        if line_name and station_name:
            nearest_station = "{} {}".format(line_name, station_name)
            if walk_info:
                nearest_station += " {}".format(walk_info)

        # Built year: d024 is a code like 9901 (1999/01) or 0512 (2005/12)
        built_year = self._parse_built_code(item.get("d024"))
        current_year = datetime.now().year
        age = (current_year - built_year) if built_year else None

        # Area (㎡) — d026 appears to be in units of 0.1㎡ (110 = 11.0㎡)
        area_raw = item.get("d026")
        area = None
        if area_raw:
            try:
                area_val = float(area_raw)
                # f-takken stores area as integer * 0.1 for some types
                area = area_val if area_val > 500 else area_val / 10.0
            except (ValueError, TypeError):
                pass

        # Property type
        property_type = item.get("d009name", "")

        # Image
        img_path = item.get("d478", "")
        image_url = ""
        if img_path and not img_path.startswith("data:"):
            if img_path.startswith("/"):
                image_url = "https://www.f-takken.com/photo/{}478.jpg".format(bno)
            else:
                image_url = img_path

        # Layout (not directly available in API, but d009name gives type)
        layout = ""

        return Property(
            id=prop_id,
            name=name,
            price=price,
            priceText=price_text if price_text else "{}万円".format(price_val) if price_val else "",
            yieldGross=None,  # f-takken doesn't provide yield in list API
            yieldNet=None,
            address=address,
            prefecture=prefecture,
            city=city,
            nearestStation=nearest_station,
            builtYear=built_year,
            age=age,
            layout=layout,
            area=area,
            structure="",
            propertyType=property_type,
            imageUrl=image_url,
            sourceUrl=source_url,
            sourceName=self.site_name,
        )

    @staticmethod
    def _parse_built_code(code) -> Optional[int]:
        """Parse f-takken built year code.

        Format: YYMM where YY maps to:
        - 99xx = 19xx (e.g. 9901 = 1999/01)
        - 00xx-30xx = 20xx (e.g. 0512 = 2005/12)
        - 9951 = likely an era-based code
        """
        if not code:
            return None
        try:
            code_str = str(code).zfill(4)
            yy = int(code_str[:2])
            if yy >= 50:
                return 1900 + yy
            else:
                return 2000 + yy
        except (ValueError, IndexError):
            return None
