"""f-takken.com (ふれんず - 福岡県宅建協会) scraper via internal API.

httpx版: Playwrightを使わずHTTPクライアントで直接APIを叩く。
メモリ削減（Chromium不要）+ 高速化。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import unquote

import httpx

from models import Property, SearchQuery
from scrapers.base import BaseScraper
from scrapers.config import (
    FTAKKEN_SELECTORS as SEL,
    REQUEST_INTERVAL_SEC,
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

API_URL = "https://www.f-takken.com/freins/api2/getBdata.php"


class FtakkenScraper(BaseScraper):
    """Scraper for f-takken.com (ふれんず) using httpx direct API calls."""

    site_name = "ふれんず"

    async def search(self, query: SearchQuery, browser=None) -> list[Property]:
        """Fetch f-takken properties via HTTP API (no browser needed)."""
        properties: list[Property] = []
        area_codes = self._get_area_codes(query)
        listtypes = self._get_listtypes(query)

        async with httpx.AsyncClient(
            timeout=15.0,
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://www.f-takken.com/freins/buy/mansion",
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "Accept-Language": "ja-JP,ja;q=0.9",
                "X-Requested-With": "XMLHttpRequest",
            },
        ) as client:
            for listtype in listtypes:
                for area_code in area_codes:
                    await asyncio.sleep(REQUEST_INTERVAL_SEC)
                    items = await self._fetch_api_http(
                        client, listtype, area_code, query
                    )
                    properties.extend(items)
                    logger.info(
                        "f-takken %s area=%s: %d items (total %d)",
                        listtype, area_code, len(items), len(properties),
                    )

        return properties

    def _get_area_codes(self, query: SearchQuery) -> list[str]:
        """Determine area codes from query."""
        if query.city:
            for ward_name, code in FUKUOKA_WARD_CODES.items():
                if query.city in ward_name or ward_name in query.city:
                    return [code]
        return list(FUKUOKA_WARD_CODES.values())

    def _get_listtypes(self, query: SearchQuery) -> list[str]:
        """Determine listtypes from query property types."""
        if query.propertyType:
            types = set()
            for pt in query.propertyType:
                lt = LISTTYPE_MAP.get(pt, "mansion")
                types.add(lt)
            return list(types)
        return ["mansion", "other"]

    async def _fetch_api_http(
        self,
        client: httpx.AsyncClient,
        listtype: str,
        area_code: str,
        query: SearchQuery,
    ) -> list[Property]:
        """Call getBdata.php API via httpx and parse results."""
        params = {
            "offset": "0",
            "limit": str(SEL["max_items"]),
            "listtype": listtype,
            "lang": "ja",
            "location": "area",
            "locate[]": area_code,
            "order1": "pl",
            "key": SEL["api_key"],
        }

        if query.priceMin is not None:
            params["data_21"] = str(int(query.priceMin / 10000))
        if query.priceMax is not None:
            params["data_22"] = str(int(query.priceMax / 10000))

        try:
            resp = await client.post(API_URL, data=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning("f-takken API error: %s", exc)
            return []

        properties = []
        for item in data.get("data", []):
            try:
                # Decode URL-encoded values
                decoded = {}
                for k, v in item.items():
                    try:
                        decoded[k] = unquote(str(v))
                    except Exception:
                        decoded[k] = v
                prop = self._parse_item(decoded, listtype)
                if prop:
                    properties.append(prop)
            except Exception as exc:
                logger.debug("f-takken: skipped item: %s", exc)
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

        prefecture = item.get("kanji_1", "福岡県")
        city = item.get("kanji_2", "") or item.get("addrname2", "")
        town = item.get("kanji_3", "") or item.get("addrname3", "")
        address = prefecture + city + town

        station_name = item.get("eki", "")
        line_name = item.get("ensen", "")
        walk_info = item.get("d029name", "")
        nearest_station = ""
        if line_name and station_name:
            nearest_station = "{} {}".format(line_name, station_name)
            if walk_info:
                nearest_station += " {}".format(walk_info)

        built_year = self._parse_built_code(item.get("d024"))
        current_year = datetime.now().year
        age = (current_year - built_year) if built_year else None

        area_raw = item.get("d026")
        area = None
        if area_raw:
            try:
                area_val = float(area_raw)
                area = area_val if area_val > 500 else area_val / 10.0
            except (ValueError, TypeError):
                pass

        property_type = item.get("d009name", "")

        img_path = item.get("d478", "")
        image_url = ""
        if img_path and not img_path.startswith("data:"):
            if img_path.startswith("/"):
                image_url = "https://www.f-takken.com/photo/{}478.jpg".format(bno)
            else:
                image_url = img_path

        return Property(
            id=prop_id,
            name=name,
            price=price,
            priceText=price_text if price_text else "{}万円".format(price_val) if price_val else "",
            yieldGross=None,
            yieldNet=None,
            address=address,
            prefecture=prefecture,
            city=city,
            nearestStation=nearest_station,
            builtYear=built_year,
            age=age,
            layout="",
            area=area,
            structure="",
            propertyType=property_type,
            imageUrl=image_url,
            sourceUrl=source_url,
            sourceName=self.site_name,
        )

    @staticmethod
    def _parse_built_code(code) -> Optional[int]:
        """Parse f-takken built year code."""
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
