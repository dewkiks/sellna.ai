"""FastAPI application — Web Scraping Module."""

from __future__ import annotations

import io
import csv
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from extractor import extract
from scraper import Scraper

app = FastAPI(title="Web Scraping Module", description="Advanced multi-engine web content extractor")

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class ScrapeRequest(BaseModel):
    urls: list[str]
    proxy: str | None = None
    render_js: bool = False


class ResultItem(BaseModel):
    url: str
    status: int
    success: bool
    data: dict | None = None
    error: str | None = None
    redirect_chain: list[str]
    elapsed_ms: float
    rendered: bool = False


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
    scraper = Scraper(proxy=req.proxy, render_js=req.render_js)
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
                rendered=r.rendered
            ))
        else:
            items.append(ResultItem(
                url=r.url,
                status=r.status,
                success=False,
                error=r.error,
                redirect_chain=r.redirect_chain,
                elapsed_ms=r.elapsed_ms,
                rendered=r.rendered
            ))

    successful = sum(1 for i in items if i.success)
    return ScrapeResponse(
        results=items,
        total=len(items),
        successful=successful,
        failed=len(items) - successful,
    )


@app.post("/api/export/csv")
async def export_csv(req: ScrapeResponse):
    """Export scraped data to CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    
    # Header
    writer.writerow(["URL", "Status", "Title", "Description", "Paragraphs", "Links", "Images", "Elapsed MS"])
    
    for r in req.results:
        if r.success and r.data is not None:
            d = r.data
            writer.writerow([
                r.url,
                r.status,
                d.get("title", ""),
                d.get("meta_description", ""),
                len(d.get("paragraphs", [])),
                len(d.get("links", [])),
                len(d.get("images", [])),
                r.elapsed_ms
            ])
        else:
            writer.writerow([r.url, f"Error: {r.error}", "", "", "", "", "", r.elapsed_ms])

    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=scrape_results.csv"}
    )


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
