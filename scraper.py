"""Core scraping engine — async fetching with anti-detection and JS rendering."""

from __future__ import annotations

import asyncio
import hashlib
import random
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
from playwright.async_api import async_playwright

import config


@dataclass
class ScrapeResult:
    url: str
    status: int = 0
    success: bool = False
    html: str = ""
    error: str = ""
    redirect_chain: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0
    rendered: bool = False


class Scraper:
    """Async scraper with adaptive throttling, retry, UA rotation, and JS rendering support."""

    def __init__(self, proxy: str | None = None, render_js: bool = False):
        self.proxy = proxy
        self.render_js = render_js
        self.delays: dict[str, float] = {}  # per-domain adaptive delay
        self.seen: set[str] = set()  # fingerprint dedup
        self.semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_REQUESTS)
        self._client: httpx.AsyncClient | None = None
        self._playwright = None
        self._browser = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                headers=dict(config.DEFAULT_HEADERS),
                timeout=httpx.Timeout(config.REQUEST_TIMEOUT),
                follow_redirects=True,
                http2=True,
                proxy=self.proxy,
            )
        return self._client

    async def _get_browser(self):
        if self._playwright is None:
            pw = await async_playwright().start()
            self._playwright = pw
            launch_options = {"headless": config.BROWSER_HEADLESS}
            if config.BROWSER_TYPE == "chromium":
                self._browser = await pw.chromium.launch(**launch_options)
            elif config.BROWSER_TYPE == "firefox":
                self._browser = await pw.firefox.launch(**launch_options)
            else:
                self._browser = await pw.webkit.launch(**launch_options)
        return self._browser

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def scrape_urls(self, urls: list[str]) -> list[ScrapeResult]:
        """Scrape a batch of URLs with dedup and concurrency control."""
        unique_urls: list[str] = []
        for url in urls:
            fp = self._fingerprint(url)
            if fp not in self.seen:
                self.seen.add(fp)
                unique_urls.append(url)

        tasks = [self._fetch_with_retry(url) for url in unique_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out: list[ScrapeResult] = []
        for url, result in zip(unique_urls, results):
            if isinstance(result, Exception):
                out.append(ScrapeResult(url=url, error=str(result)))
            elif isinstance(result, ScrapeResult):
                out.append(result)
            else:
                out.append(ScrapeResult(url=url, error="Unknown result type"))

        await self.close()
        return out

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    async def _fetch_with_retry(self, url: str) -> ScrapeResult:
        """Fetch a URL with retries and exponential backoff."""
        last_error = ""
        retry_times = int(config.RETRY_TIMES)
        for attempt in range(1 + retry_times):
            try:
                if self.render_js:
                    result = await self._fetch_js(url)
                else:
                    result = await self._fetch_static(url)
                
                if result.success:
                    return result
                
                if result.status in config.RETRY_HTTP_CODES and attempt < retry_times:
                    last_error = f"HTTP {result.status}"
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(backoff)
                    continue
                return result
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < retry_times:
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(backoff)
                    continue

        return ScrapeResult(url=url, error=last_error)

    async def _fetch_static(self, url: str) -> ScrapeResult:
        """Single fetch using httpx."""
        domain = urlparse(url).netloc
        delay = float(self._get_delay(domain))
        await asyncio.sleep(delay + (delay * random.uniform(0, 0.5)))

        client = await self._get_client()
        ua = random.choice(config.USER_AGENTS)
        headers = {"User-Agent": ua}

        async with self.semaphore:
            start = time.perf_counter()
            response = await client.get(url, headers=headers)
            elapsed = float((time.perf_counter() - start) * 1000)

        self._adjust_delay(domain, float(elapsed / 1000), response.status_code)
        
        success = 200 <= response.status_code < 400
        html = self._decode_response(response) if success else ""
        return ScrapeResult(
            url=str(response.url),
            status=response.status_code,
            success=success,
            html=html,
            error="" if success else f"HTTP {response.status_code}",
            redirect_chain=[str(r.url) for r in response.history],
            elapsed_ms=round(elapsed, 1),
            rendered=False
        )

    async def _fetch_js(self, url: str) -> ScrapeResult:
        """Fetch using Playwright."""
        domain = urlparse(url).netloc
        delay = float(self._get_delay(domain))
        await asyncio.sleep(delay + (delay * random.uniform(0, 0.5)))

        browser = await self._get_browser()
        if not browser:
            return ScrapeResult(url=url, error="Browser not initialized")

        ua = random.choice(config.USER_AGENTS)
        
        async with self.semaphore:
            start = time.perf_counter()
            context = await browser.new_context(user_agent=ua)
            page = await context.new_page()
            try:
                response = await page.goto(url, wait_until="networkidle", timeout=config.JS_RENDER_TIMEOUT)
                elapsed = float((time.perf_counter() - start) * 1000)
                status = response.status if response else 0
                html = await page.content()
                success = 200 <= status < 400
                
                self._adjust_delay(domain, float(elapsed / 1000), status)
                
                return ScrapeResult(
                    url=url,
                    status=status,
                    success=success,
                    html=html,
                    error="" if success else f"HTTP {status}",
                    elapsed_ms=round(elapsed, 1),
                    rendered=True
                )
            except Exception as e:
                return ScrapeResult(url=url, error=str(e), rendered=True)
            finally:
                await context.close()

    # ------------------------------------------------------------------
    # Adaptive Throttle
    # ------------------------------------------------------------------

    def _get_delay(self, domain: str) -> float:
        return self.delays.get(domain, config.MIN_DELAY)

    def _adjust_delay(self, domain: str, latency: float, status: int) -> None:
        target_delay = latency / config.AUTOTHROTTLE_TARGET_CONCURRENCY
        current = self.delays.get(domain, config.MIN_DELAY)
        new_delay = max(target_delay, (current + target_delay) / 2.0)
        new_delay = max(config.MIN_DELAY, min(new_delay, config.MAX_DELAY))
        if status >= 400 and new_delay <= current:
            return
        self.delays[domain] = new_delay

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_response(response: httpx.Response) -> str:
        content_type = response.headers.get("content-type", "")
        if "charset" in content_type.lower():
            return response.text
        raw = response.content
        head = raw[:4096]
        match = re.search(rb'charset=["\']?\s*([a-zA-Z0-9_-]+)', head, re.I)
        if match:
            charset = match.group(1).decode("ascii", errors="ignore")
            try: return raw.decode(charset)
            except: pass
        for enc in ("utf-8", "latin-1", "cp1252"):
            try: return raw.decode(enc)
            except: continue
        return raw.decode("utf-8", errors="replace")

    @staticmethod
    def _fingerprint(url: str) -> str:
        parsed = urlparse(url)
        normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
        if parsed.query:
            normalized += "?" + "&".join(sorted(parsed.query.split("&")))
        return hashlib.sha1(normalized.encode()).hexdigest()
