"""Core scraping engine — async fetching with anti-detection."""

from __future__ import annotations

import asyncio
import hashlib
import random
import re
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

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


class Scraper:
    """Async scraper with adaptive throttling, retry, UA rotation, and dedup."""

    def __init__(self, proxy: str | None = None):
        self.proxy = proxy
        self.delays: dict[str, float] = {}  # per-domain adaptive delay
        self.seen: set[str] = set()  # fingerprint dedup
        self.semaphore = asyncio.Semaphore(config.MAX_CONCURRENT_REQUESTS)
        self._client: httpx.AsyncClient | None = None

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

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

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
            else:
                out.append(result)

        await self.close()
        return out

    # ------------------------------------------------------------------
    # Fetching
    # ------------------------------------------------------------------

    async def _fetch_with_retry(self, url: str) -> ScrapeResult:
        """Fetch a URL with retries and exponential backoff."""
        last_error = ""
        for attempt in range(1 + config.RETRY_TIMES):
            try:
                result = await self._fetch(url)
                if result.success:
                    return result
                # Retry on configured status codes
                if result.status in config.RETRY_HTTP_CODES and attempt < config.RETRY_TIMES:
                    last_error = f"HTTP {result.status}"
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(backoff)
                    continue
                return result
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError,
                    httpx.WriteError, httpx.PoolTimeout, OSError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < config.RETRY_TIMES:
                    backoff = (2 ** attempt) + random.uniform(0, 1)
                    await asyncio.sleep(backoff)
                    continue

        return ScrapeResult(url=url, error=last_error)

    async def _fetch(self, url: str) -> ScrapeResult:
        """Single fetch with delay, UA rotation, and response tracking."""
        domain = urlparse(url).netloc

        # Apply adaptive delay with jitter
        delay = self._get_delay(domain)
        jitter = delay * random.uniform(0, 0.5)
        await asyncio.sleep(delay + jitter)

        client = await self._get_client()

        # Rotate User-Agent per request
        ua = random.choice(config.USER_AGENTS)
        headers = {"User-Agent": ua}

        async with self.semaphore:
            start = time.perf_counter()
            response = await client.get(url, headers=headers)
            elapsed = (time.perf_counter() - start) * 1000

        latency = elapsed / 1000  # seconds for throttle calc

        # Track redirect chain
        redirect_chain = [str(r.url) for r in response.history] if response.history else []

        # Adjust throttle
        self._adjust_delay(domain, latency, response.status_code)

        success = 200 <= response.status_code < 400
        html = self._decode_response(response) if success else ""
        return ScrapeResult(
            url=str(response.url),
            status=response.status_code,
            success=success,
            html=html,
            error="" if success else f"HTTP {response.status_code}",
            redirect_chain=redirect_chain,
            elapsed_ms=round(elapsed, 1),
        )

    # ------------------------------------------------------------------
    # Adaptive Throttle (from Scrapy's autothrottle algorithm)
    # ------------------------------------------------------------------

    def _get_delay(self, domain: str) -> float:
        return self.delays.get(domain, config.MIN_DELAY)

    def _adjust_delay(self, domain: str, latency: float, status: int) -> None:
        """Adapt per-domain delay based on server latency."""
        target_delay = latency / config.AUTOTHROTTLE_TARGET_CONCURRENCY
        current = self.delays.get(domain, config.MIN_DELAY)

        new_delay = max(target_delay, (current + target_delay) / 2.0)
        new_delay = max(config.MIN_DELAY, min(new_delay, config.MAX_DELAY))

        # Don't reduce delay on error responses
        if status >= 400 and new_delay <= current:
            return

        self.delays[domain] = new_delay

    # ------------------------------------------------------------------
    # Response decoding
    # ------------------------------------------------------------------

    @staticmethod
    def _decode_response(response: httpx.Response) -> str:
        """Decode response body with robust charset detection.

        httpx's response.text uses the charset from Content-Type.  If that's
        missing or wrong we try to sniff it from the HTML <meta> tag, then
        fall back to common encodings.
        """
        # If httpx already decoded it cleanly via Content-Type charset, use that
        content_type = response.headers.get("content-type", "")
        if "charset" in content_type.lower():
            return response.text

        raw = response.content
        # Try to find charset in HTML meta tags
        # e.g. <meta charset="utf-8"> or <meta http-equiv="Content-Type" content="...charset=gb2312">
        head = raw[:4096]
        match = re.search(
            rb'charset=["\']?\s*([a-zA-Z0-9_-]+)',
            head,
            re.IGNORECASE,
        )
        if match:
            charset = match.group(1).decode("ascii", errors="ignore")
            try:
                return raw.decode(charset)
            except (UnicodeDecodeError, LookupError):
                pass

        # Try common encodings
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue

        # Last resort — lossy utf-8
        return raw.decode("utf-8", errors="replace")

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def _fingerprint(url: str) -> str:
        """SHA1 fingerprint of normalized URL."""
        parsed = urlparse(url)
        # Normalize: lowercase scheme+host, sort query params
        normalized = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
        if parsed.query:
            params = sorted(parsed.query.split("&"))
            normalized += "?" + "&".join(params)
        return hashlib.sha1(normalized.encode()).hexdigest()
