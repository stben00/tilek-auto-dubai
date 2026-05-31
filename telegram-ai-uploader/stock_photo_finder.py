"""
Stock car photo finder.

Searches free internet sources for clean professional car photos based on
brand + model + year. Returns the photo bytes ready to save as the car's
main image — no AI generation, just real photographs.

Sources tried in order:
  1. Wikimedia Commons (free, CC-licensed, very high quality)
  2. DuckDuckGo image search (free fallback when Wiki has no match)

Usage:
    bytes_or_none = await find_stock_photo(brand="Toyota", model="Camry", year="2024")
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx

log = logging.getLogger(__name__)

WIKI_API = "https://commons.wikimedia.org/w/api.php"
USER_AGENT = "TilekAutoDubai/1.0 (https://stben00.github.io/tilek-auto-dubai/)"
MIN_BYTES = 30_000          # skip tiny/icon files
MAX_BYTES = 8_000_000       # 8 MB hard cap
MIN_WIDTH = 800             # poster needs at least 800px wide
TIMEOUT = 30.0


async def _wiki_search_filenames(query: str, limit: int = 15) -> list[str]:
    """Search Wikimedia Commons for file titles matching the query."""
    params = {
        "action": "query",
        "format": "json",
        "list": "search",
        "srnamespace": "6",          # File: namespace
        "srsearch": query,
        "srlimit": str(limit),
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as c:
            r = await c.get(WIKI_API, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("Wiki search failed for %r: %s", query, e)
        return []
    return [hit["title"] for hit in data.get("query", {}).get("search", [])]


async def _wiki_get_image_url(filename: str) -> Optional[tuple[str, int, int]]:
    """Resolve a File:... title to (url, width, height)."""
    params = {
        "action": "query",
        "format": "json",
        "titles": filename,
        "prop": "imageinfo",
        "iiprop": "url|size|mime",
    }
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers={"User-Agent": USER_AGENT}) as c:
            r = await c.get(WIKI_API, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        log.warning("Wiki imageinfo failed for %r: %s", filename, e)
        return None
    pages = data.get("query", {}).get("pages", {})
    for _, page in pages.items():
        info = (page.get("imageinfo") or [{}])[0]
        url = info.get("url")
        w = int(info.get("width") or 0)
        h = int(info.get("height") or 0)
        mime = info.get("mime") or ""
        if url and mime.startswith("image/") and "svg" not in mime:
            return url, w, h
    return None


def _score_filename(filename: str, brand: str, model: str, year: str) -> int:
    """Heuristic: pick the file title most likely to be a clean exterior shot."""
    name = filename.lower()
    score = 0
    if brand.lower() in name:
        score += 5
    # Model with parenthesised separator like "(XV80)" or " " — accept either
    for token in model.lower().split():
        if token and token in name:
            score += 3
    if year and year in name:
        score += 4
    # Prefer exterior/front shots
    for good in ("front", "exterior", "side", "right", "left", "factory"):
        if good in name:
            score += 2
    # Penalise unlikely-good shots
    for bad in ("interior", "engine", "dashboard", "trunk", "rear", "tail", "logo",
                "badge", "wheel", "tyre", "tire", "fuel", "key", "concept", "rendering",
                "crash", "damaged", "wreck"):
        if bad in name:
            score -= 3
    return score


async def _search_wikipedia(brand: str, model: str, year: str) -> Optional[bytes]:
    """Try Wikipedia Commons for a clean professional car photo."""
    # Build query variations from most-specific → most-generic
    queries = []
    if brand and model and year:
        queries.append(f"{brand} {model} {year}")
    if brand and model:
        queries.append(f"{brand} {model}")
    # Generation-name fallback (e.g. "Camry XV80")
    m = re.match(r"^([A-Za-z]+)\s*(\d+)?", model or "")
    if m and brand:
        queries.append(f"{brand} {m.group(0)}")

    titles: list[str] = []
    seen: set[str] = set()
    for q in queries:
        for t in await _wiki_search_filenames(q):
            if t not in seen:
                seen.add(t)
                titles.append(t)
        if len(titles) >= 20:
            break

    if not titles:
        return None

    # Rank by heuristic and try the top candidates
    titles.sort(key=lambda t: _score_filename(t, brand, model, year), reverse=True)
    log.info("Wiki: %d candidates for %s %s %s — top: %s",
             len(titles), brand, model, year, titles[:3])

    for title in titles[:5]:
        info = await _wiki_get_image_url(title)
        if not info:
            continue
        url, w, h = info
        if w < MIN_WIDTH:
            log.debug("Wiki skipping %s (too small: %dx%d)", title, w, h)
            continue
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                         headers={"User-Agent": USER_AGENT}) as c:
                r = await c.get(url)
                r.raise_for_status()
                data = r.content
        except Exception as e:
            log.warning("Download failed for %s: %s", title, e)
            continue
        if len(data) < MIN_BYTES or len(data) > MAX_BYTES:
            continue
        log.info("✓ Wiki photo: %s (%dx%d, %.1fKB)", title, w, h, len(data) / 1024)
        return data

    return None


async def _search_duckduckgo(brand: str, model: str, year: str) -> Optional[bytes]:
    """Fallback: DuckDuckGo image search via their HTML/JSON endpoint."""
    try:
        from ddgs import DDGS  # type: ignore
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except ImportError:
            log.info("ddgs/duckduckgo_search not installed; skipping DDG fallback")
            return None

    query = f"{brand} {model} {year} car official photo high resolution"
    log.info("DDG: searching %r", query)
    try:
        # DDGS is sync; run in a worker thread to keep the bot loop free
        import asyncio
        def _do_search():
            with DDGS() as ddgs:
                return list(ddgs.images(query, max_results=15, safesearch="off", size="Large"))
        results = await asyncio.to_thread(_do_search)
    except Exception as e:
        log.warning("DDG search failed: %s", e)
        return None

    if not results:
        return None

    for hit in results[:8]:
        img_url = hit.get("image") or hit.get("url")
        if not img_url or not isinstance(img_url, str):
            continue
        if not img_url.lower().startswith(("http://", "https://")):
            continue
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                         headers={"User-Agent": USER_AGENT}) as c:
                r = await c.get(img_url)
                if r.status_code != 200:
                    continue
                ct = r.headers.get("content-type", "")
                if "image" not in ct:
                    continue
                data = r.content
        except Exception:
            continue
        if len(data) < MIN_BYTES or len(data) > MAX_BYTES:
            continue
        log.info("✓ DDG photo: %s (%.1fKB)", img_url[:80], len(data) / 1024)
        return data

    return None


async def find_stock_photo(brand: str, model: str, year: str = "") -> Optional[bytes]:
    """
    Find a clean professional photo of the given car from free internet sources.

    Returns image bytes (JPEG/PNG) ready to save, or None if nothing decent was
    found. Tries Wikimedia Commons first, then DuckDuckGo image search.
    """
    brand = (brand or "").strip()
    model = (model or "").strip()
    year = (year or "").strip()
    if not brand or not model:
        log.info("Stock photo search needs at least brand+model; skipping")
        return None

    log.info("Stock photo lookup: %s %s %s", brand, model, year)

    result = await _search_wikipedia(brand, model, year)
    if result:
        return result

    log.info("Wiki found nothing — trying DDG fallback...")
    result = await _search_duckduckgo(brand, model, year)
    if result:
        return result

    log.info("No stock photo found for %s %s %s", brand, model, year)
    return None
