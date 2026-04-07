"""Base scraper abstract class with shared parsers."""
from __future__ import annotations

import abc
import re
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from models import SearchQuery, Property


class BaseScraper(abc.ABC):
    """Abstract base class for all site scrapers.

    To add a new site:
      1. Inherit this class in a new file under scrapers/
      2. Implement ``search()``
      3. Add config entry in scrapers/config.py SCRAPER_REGISTRY
    """

    site_name: str = ""

    @abc.abstractmethod
    async def search(self, query: "SearchQuery") -> list["Property"]:
        """Run scraping and return a list of Property objects.

        Must handle its own errors gracefully and return partial results
        when possible (e.g. if pagination breaks mid-way).
        """
        ...

    # ---------- shared parsers ----------
    @staticmethod
    def parse_price(text: str) -> Optional[int]:
        """Parse '1,500万円' or '2億3,640万円' -> int (yen)."""
        if not text:
            return None
        text = text.replace(",", "").replace(" ", "")
        total = 0
        oku_match = re.search(r"(\d+)億", text)
        if oku_match:
            total += int(oku_match.group(1)) * 100000000
        man_match = re.search(r"(\d+)万", text)
        if man_match:
            total += int(man_match.group(1)) * 10000
        return total if total > 0 else None

    @staticmethod
    def parse_built_year(text: str) -> Optional[int]:
        """Parse '築年月 1985年7月' -> 1985."""
        m = re.search(r"(\d{4})年", text)
        return int(m.group(1)) if m else None

    @staticmethod
    def parse_area(text: str) -> Optional[float]:
        """Parse '72㎡' or '78.45m²' -> float."""
        m = re.search(r"([\d.]+)\s*[㎡m]", text)
        return float(m.group(1)) if m else None

    @staticmethod
    def extract_city(address: str) -> str:
        """Extract city/ward from address string."""
        m = re.search(r"((?:福岡市|北九州市|久留米市|[^\s県]+市)[^\s区町村]*(?:区|町|村)?)", address)
        return m.group(1) if m else ""

    @staticmethod
    def extract_prefecture(address: str) -> str:
        """Extract prefecture from address."""
        m = re.match(r"(.+?[都道府県])", address)
        return m.group(1) if m else ""
