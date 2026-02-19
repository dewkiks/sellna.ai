"""FastAPI application — endpoints and frontend serving."""

from __future__ import annotations

from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from extractor import extract
from scraper import Scraper

app = FastAPI(title="Web Scraper")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ScrapeRequest(BaseModel):
    urls: list[str]
    proxy: str | None = None


class ResultItem(BaseModel):
    url: str
    status: int
    success: bool
    data: dict | None = None
    error: str | None = None
    redirect_chain: list[str]
    elapsed_ms: float


class ScrapeResponse(BaseModel):
    results: list[ResultItem]
    total: int
    successful: int
    failed: int


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/scrape", response_model=ScrapeResponse)
async def scrape(req: ScrapeRequest):
    scraper = Scraper(proxy=req.proxy)
    raw_results = await scraper.scrape_urls(req.urls)

    items: list[ResultItem] = []
    for r in raw_results:
        if r.success:
            data = extract(r.html, r.url)
            items.append(ResultItem(
                url=r.url,
                status=r.status,
                success=True,
                data=data,
                redirect_chain=r.redirect_chain,
                elapsed_ms=r.elapsed_ms,
            ))
        else:
            items.append(ResultItem(
                url=r.url,
                status=r.status,
                success=False,
                error=r.error,
                redirect_chain=r.redirect_chain,
                elapsed_ms=r.elapsed_ms,
            ))

    successful = sum(1 for i in items if i.success)
    return ScrapeResponse(
        results=items,
        total=len(items),
        successful=successful,
        failed=len(items) - successful,
    )


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
