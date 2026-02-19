"""HTML parsing & data cleaning pipeline."""

from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, Tag


def extract(html: str, url: str) -> dict:
    """Extract structured data from raw HTML."""
    soup = BeautifulSoup(html, "lxml")

    # Build a clean content subtree for text extraction
    content_soup = _get_content_root(soup)

    return {
        "url": url,
        "title": _get_title(soup),
        "meta_description": _get_meta(soup, "description"),
        "headings": _get_headings(content_soup),
        "paragraphs": _get_paragraphs(content_soup),
        "text_content": _get_text(content_soup),
        "links": _get_links(content_soup, url),
        "images": _get_images(content_soup, url),
        "meta_tags": _get_all_meta(soup),
    }


# ---------------------------------------------------------------------------
# Main content detection
# ---------------------------------------------------------------------------

# Elements that are almost never article content
_JUNK_TAGS = [
    "script", "style", "noscript", "svg", "template",
    "nav", "footer", "header", "aside",
    "iframe", "object", "embed",
]

# CSS selectors for common non-content blocks (nav, sidebar, ToC, etc.)
_JUNK_SELECTORS = [
    "[role='navigation']",
    "[role='banner']",
    "[role='contentinfo']",
    "[role='complementary']",
    "[aria-hidden='true']",
    ".sidebar", "#sidebar",
    ".nav", ".navbar", ".navigation",
    ".menu", ".header", ".footer",
    ".toc", "#toc",                           # Wikipedia table of contents
    ".mw-jump-link",                           # Wikipedia skip-links
    "#catlinks",                               # Wikipedia category links
    "#mw-navigation", "#mw-panel",             # Wikipedia chrome
    ".navbox", ".navbox-inner",                # Wikipedia navboxes at bottom
    ".sistersitebox",
    ".mw-editsection",                         # [edit] links
    ".reflist", ".references",                 # reference lists
    ".external", ".mw-authority-control",
    ".mw-indicators",
    ".noprint",
    ".cookie-banner", ".cookie-consent",
    ".ad", ".ads", ".advertisement",
    "#comments", ".comments",
]

# Candidate selectors for the main content area, tried in order
_CONTENT_SELECTORS = [
    "#mw-content-text",          # Wikipedia / MediaWiki
    "article",
    "[role='main']",
    "main",
    "#content",
    "#main-content",
    ".main-content",
    ".post-content",
    ".article-content",
    ".entry-content",
    ".page-content",
    "#bodyContent",
]


def _get_content_root(soup: BeautifulSoup) -> BeautifulSoup:
    """Return a cleaned copy of the most likely content subtree."""
    # Try to find a content container
    root = None
    for sel in _CONTENT_SELECTORS:
        root = soup.select_one(sel)
        if root:
            break

    # Fall back to <body> or the whole soup
    if root is None:
        root = soup.find("body") or soup

    # Clone so we don't mutate the original
    clone = BeautifulSoup(str(root), "lxml")

    # Remove junk tags
    for tag in clone.find_all(_JUNK_TAGS):
        tag.decompose()

    # Remove junk by CSS selector
    for sel in _JUNK_SELECTORS:
        for el in clone.select(sel):
            el.decompose()

    # Remove HTML comments
    for comment in clone.find_all(string=lambda t: isinstance(t, Comment)):
        comment.extract()

    return clone


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _get_title(soup: BeautifulSoup) -> str:
    tag = soup.find("title")
    return _clean(tag.get_text()) if tag else ""


def _get_meta(soup: BeautifulSoup, name: str) -> str:
    tag = soup.find("meta", attrs={"name": re.compile(f"^{name}$", re.I)})
    if tag:
        return _clean(tag.get("content", ""))
    return ""


def _get_headings(soup: BeautifulSoup) -> dict[str, list[str]]:
    headings: dict[str, list[str]] = {}
    for level in range(1, 7):
        tag_name = f"h{level}"
        found = [_clean(h.get_text()) for h in soup.find_all(tag_name)]
        # Filter out empty strings
        found = [h for h in found if h]
        if found:
            headings[tag_name] = found
    return headings


def _get_paragraphs(soup: BeautifulSoup) -> list[str]:
    """Extract clean paragraph text from <p> tags."""
    paragraphs = []
    for p in soup.find_all("p"):
        text = _clean(p.get_text())
        # Skip very short fragments (likely UI remnants)
        if text and len(text) > 20:
            paragraphs.append(text)
    return paragraphs


def _get_text(soup: BeautifulSoup) -> str:
    """Extract visible text from the content area, paragraph by paragraph.

    Uses block-level elements to insert newlines so the output reads
    like natural prose instead of a wall of concatenated strings.
    """
    _BLOCK_TAGS = {
        "p", "div", "section", "article", "blockquote",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "tr", "td", "th", "dt", "dd",
        "pre", "figure", "figcaption", "details", "summary",
    }

    parts: list[str] = []
    for element in soup.descendants:
        if isinstance(element, str):
            text = element.strip()
            if text:
                parts.append(text)
        elif isinstance(element, Tag) and element.name in _BLOCK_TAGS:
            parts.append("\n")

    raw = " ".join(parts)
    # Normalize whitespace
    raw = re.sub(r"[ \t]+", " ", raw)
    raw = re.sub(r" ?\n ?", "\n", raw)
    raw = re.sub(r"\n{3,}", "\n\n", raw)
    return raw.strip()


def _get_links(soup: BeautifulSoup, base_url: str) -> list[dict]:
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        links.append({
            "text": _clean(a.get_text()),
            "href": urljoin(base_url, href),
        })
    return links


def _get_images(soup: BeautifulSoup, base_url: str) -> list[dict]:
    images = []
    for img in soup.find_all("img", src=True):
        images.append({
            "alt": _clean(img.get("alt", "")),
            "src": urljoin(base_url, img["src"].strip()),
        })
    return images


def _get_all_meta(soup: BeautifulSoup) -> dict[str, str]:
    meta = {}
    for tag in soup.find_all("meta"):
        key = tag.get("name") or tag.get("property") or tag.get("http-equiv")
        content = tag.get("content", "")
        if key and content:
            meta[key] = _clean(content)
    return meta


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

_MULTI_WHITESPACE = re.compile(r"[ \t]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")


def _clean(text: str) -> str:
    """Strip, collapse whitespace, normalize unicode."""
    text = text.strip()
    text = _MULTI_WHITESPACE.sub(" ", text)
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text
