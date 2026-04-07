"""FastAPI app for L-008 Real Estate Aggregator."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from models import SearchQuery, SearchResponse
from services.orchestrator import run_search

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)

app = FastAPI(
    title="L-008 Real Estate Aggregator",
    description="HOME'S + SUUMO + ふれんず property search API",
    version="0.2.0",
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


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """Serve the frontend HTML."""
    html_path = STATIC_DIR / "index.html"
    if html_path.exists():
        return HTMLResponse(content=html_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Frontend not found</h1>", status_code=404)


@app.post("/api/search", response_model=SearchResponse)
async def api_search(query: SearchQuery) -> SearchResponse:
    """Search properties across all configured sites."""
    return await run_search(query)


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok"}
