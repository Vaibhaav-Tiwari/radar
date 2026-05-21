"""
main.py — FastAPI backend for Competitive Intelligence Radar.
Serves the frontend and exposes API endpoints for competitor analysis.
"""

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl
from dotenv import load_dotenv

load_dotenv()

# Import Exa service (relative path when running from project root)
import sys
sys.path.insert(0, str(Path(__file__).parent))
from exa_service import (
    discover_competitors,
    get_recent_news,
    get_job_signals,
    get_product_updates,
)

app = FastAPI(title="Competitive Intelligence Radar", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files
FRONTEND_DIR = Path(__file__).parent.parent / "frontend"
if (FRONTEND_DIR / "static").exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR / "static")), name="static")


# ── Models ────────────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    url: str


class CompetitorIntel(BaseModel):
    name: str
    url: str
    description: str
    news: list[dict]
    jobs: list[dict]
    product_updates: list[dict]


class RadarResponse(BaseModel):
    target_url: str
    competitors: list[CompetitorIntel]


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_path = FRONTEND_DIR / "templates" / "index.html"
    if index_path.exists():
        return HTMLResponse(content=index_path.read_text())
    return HTMLResponse("<h1>Radar — frontend not found</h1>")


@app.post("/api/scan", response_model=RadarResponse)
async def scan_competitors(request: ScanRequest):
    """
    Main endpoint: given a company URL, discover competitors and gather intel.
    Steps:
      1. Use Exa findSimilar to discover competitors
      2. For each competitor, fetch news, jobs, and product updates in parallel
    """
    url = request.url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    exa_key = os.getenv("EXA_API_KEY", "")
    if not exa_key or exa_key == "your_exa_api_key_here":
        raise HTTPException(
            status_code=500,
            detail="EXA_API_KEY not configured. Please add it to your .env file.",
        )

    try:
        # Step 1: Discover real competitors via two-stage article mining + findSimilar
        companies = await discover_competitors(url, num_results=6)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Competitor discovery failed: {str(e)}")

    if not companies:
        raise HTTPException(status_code=404, detail="No competitors found for this URL.")

    # Step 2: Enrich each company in parallel
    async def enrich(company: dict) -> CompetitorIntel:
        name = company["name"]
        comp_url = company["url"]
        try:
            news, jobs, updates = await asyncio.gather(
                get_recent_news(name, comp_url),
                get_job_signals(name),
                get_product_updates(name, comp_url),
                return_exceptions=True,
            )
            # If any call failed, default to empty list
            news = news if isinstance(news, list) else []
            jobs = jobs if isinstance(jobs, list) else []
            updates = updates if isinstance(updates, list) else []
        except Exception:
            news, jobs, updates = [], [], []

        return CompetitorIntel(
            name=name,
            url=comp_url,
            description=company.get("description", ""),
            news=news,
            jobs=jobs,
            product_updates=updates,
        )

    competitors = await asyncio.gather(*[enrich(c) for c in companies])

    return RadarResponse(
        target_url=url,
        competitors=list(competitors),
    )


@app.get("/api/health")
async def health():
    return {"status": "ok", "exa_configured": bool(os.getenv("EXA_API_KEY"))}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
