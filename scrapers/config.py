"""Selector configuration for each scraping target site.

To add a new site:
1. Create a new scraper file inheriting BaseScraper
2. Add a selector dict here
3. Register in SCRAPER_REGISTRY
"""
from __future__ import annotations

# ---------- HOME'S (toushi.homes.co.jp) ----------
HOMES_SELECTORS = {
    "base_url": "https://toushi.homes.co.jp",
    "search_path": "/bukkensearch/addr11=40/",
    "list_container": "ul.propetyList",          # NOTE: typo in HOME'S actual class name
    "item": ".propertyList__item",
    "link": ".propertyList__link",
    "name": ".prg-propertyName",
    "price": ".prg-price",
    "yield_gross": ".prg-yield",
    "address": ".prg-address",
    "station": ".prg-walk",
    "built_year": ".prg-houseAge",
    "area": ".prg-houseAreaText",
    "structure": ".prg-structure",
    "property_type": ".propertyList__icon",
    "image": ".prg-propertyPhoto",
    "total_count": ".pagination .prg-pagination",
    "page_param": "page",
    "items_per_page_select": None,  # We use default 50-item display
}

# ---------- SUUMO (suumo.jp) ----------
SUUMO_SELECTORS = {
    "base_url": "https://suumo.jp",
    "search_path": "/ms/chuko/fukuoka/",
    "item": ".property_unit",
    "title_link": ".property_unit-title a",
    "dottable": ".dottable",
    "image": ".property_unit-thumb img, img.js-scrollLazy",
    "pagination_next": ".pagination-parts a",
    "page_param": "page",
    # SUUMO city code mapping for Fukuoka
    "city_codes": {
        "福岡市博多区": "sc_fukuokashihakata",
        "福岡市中央区": "sc_fukuokashichuo",
        "福岡市南区": "sc_fukuokashiminami",
        "福岡市東区": "sc_fukuokashihigashi",
        "福岡市西区": "sc_fukuokashinishi",
        "福岡市早良区": "sc_fukuokashisawara",
        "福岡市城南区": "sc_fukuokashijonan",
    },
}

# ---------- f-takken.com (ふれんず) ----------
FTAKKEN_SELECTORS = {
    "base_url": "https://www.f-takken.com",
    "api_path": "//www.f-takken.com/freins/api2/",
    "api_key": "8cf083855064d6cd42489436d0fb31d7k",
    "max_items": 200,  # Items per API call
}

# ---------- Scraper Registry ----------
# Maps site name -> { module, class_name, selectors }
SCRAPER_REGISTRY = {
    "HOME'S": {
        "module": "scrapers.homes",
        "class_name": "HomesScraper",
        "selectors": HOMES_SELECTORS,
    },
    "SUUMO": {
        "module": "scrapers.suumo",
        "class_name": "SuumoScraper",
        "selectors": SUUMO_SELECTORS,
    },
    "ふれんず": {
        "module": "scrapers.ftakken",
        "class_name": "FtakkenScraper",
        "selectors": FTAKKEN_SELECTORS,
    },
}

# ---------- Global Settings ----------
MAX_PAGES = 3  # Render Free: メモリ節約。Starter($7/月)なら10に引き上げ可
REQUEST_INTERVAL_SEC = 1.0
PAGE_TIMEOUT_MS = 30000  # 30秒（Render Freeのレスポンス時間制約対策）
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
