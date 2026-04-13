"""FastAPI app for L-008 Real Estate Aggregator."""
from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from models import SearchQuery
from services.orchestrator import run_search

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

app = FastAPI(
    title="L-008 Real Estate Aggregator",
    description="HOME'S + SUUMO + ふれんず property search API",
    version="0.3.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Resolve HTML file path
STATIC_DIR = Path(__file__).parent / "static"

# In-memory job store for async search
_jobs: dict[str, dict] = {}


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the frontend HTML."""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Frontend not found</h1>", status_code=404)


@app.get("/dev-process", response_class=HTMLResponse)
async def serve_dev_process():
    """Serve the development process report page."""
    html_path = STATIC_DIR / "dev-process.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Page not found</h1>", status_code=404)


@app.post("/api/search")
async def api_search(query: SearchQuery):
    """Start async search — returns jobId immediately, poll /api/result/{jobId}."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running"}
    asyncio.create_task(_run_and_store(job_id, query))
    return {"jobId": job_id, "status": "running"}


async def _run_and_store(job_id: str, query: SearchQuery):
    """Background task: run search and store result."""
    try:
        result = await run_search(query)
        _jobs[job_id] = {"status": "done", "result": result.model_dump()}
    except Exception as e:
        logging.getLogger(__name__).error("Search job %s failed: %s", job_id, e)
        _jobs[job_id] = {"status": "error", "error": str(e)}


@app.get("/api/result/{job_id}")
async def get_result(job_id: str):
    """Poll for search result by jobId."""
    job = _jobs.get(job_id)
    if not job:
        return {"error": "not found"}
    return job


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/api/debug/{site}")
async def debug_site(site: str):
    """Diagnostic: load one page from a site and return HTML snippet + metadata."""
    import time
    from playwright.async_api import async_playwright
    from scrapers.config import USER_AGENT

    url_map = {
        "homes": "https://toushi.homes.co.jp/bukkensearch/addr11=40/",
        "suumo": "https://suumo.jp/ms/chuko/fukuoka/sc_fukuokashihakata/",
        "ftakken": "https://www.f-takken.com/freins/buy/mansion",
    }
    target_url = url_map.get(site)
    if not target_url:
        return {"error": "unknown site. use: homes, suumo, ftakken"}

    start = time.time()
    result = {}
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8",
            },
        )
        page = await context.new_page()
        try:
            resp = await page.goto(target_url, wait_until="load", timeout=45000)
            result["status"] = resp.status if resp else None
            result["final_url"] = page.url
            result["title"] = await page.title()
            body = await page.content()
            result["body_length"] = len(body)
            result["body_head"] = body[:2000]
            selectors = {
                "homes": ".propertyList__item",
                "suumo": ".property_unit",
                "ftakken": "#app",
            }
            sel = selectors.get(site, "body")
            els = await page.query_selector_all(sel)
            result["selector"] = sel
            result["selector_count"] = len(els)
        except Exception as exc:
            result["error"] = str(exc)
        finally:
            await browser.close()

    result["elapsed"] = "{:.1f}s".format(time.time() - start)
    return result
