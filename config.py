# Scraper configuration — settings & anti-detection data

# ---------------------------------------------------------------------------
# Concurrency & Timing
# ---------------------------------------------------------------------------
MAX_CONCURRENT_REQUESTS = 5
REQUEST_TIMEOUT = 30  # seconds
RETRY_TIMES = 3
RETRY_HTTP_CODES = [500, 502, 503, 504, 408, 429]
MIN_DELAY = 1.0  # seconds between requests to the same domain
MAX_DELAY = 5.0
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0

# ---------------------------------------------------------------------------
# JS Rendering (Playwright)
# ---------------------------------------------------------------------------
JS_RENDER_TIMEOUT = 30000  # ms
BROWSER_HEADLESS = True
BROWSER_TYPE = "chromium"  # chromium, firefox, webkit

# ---------------------------------------------------------------------------
# User-Agent Pool (~15 realistic strings)
# ---------------------------------------------------------------------------
USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    # Chrome on iPhone
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4.1 Mobile/15E148 Safari/604.1",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# ---------------------------------------------------------------------------
# Default Browser-like Headers
# ---------------------------------------------------------------------------
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}
