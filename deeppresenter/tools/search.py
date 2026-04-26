import asyncio
import hashlib
import json
import os
import re
import shutil
from io import BytesIO
from pathlib import Path
from typing import Any, Literal

import aiohttp
import httpx
import markdownify
from fake_useragent import UserAgent
from fastmcp import FastMCP
from PIL import Image
from playwright.async_api import TimeoutError
from trafilatura import extract

from deeppresenter.utils.constants import (
    DOWNLOAD_CACHE,
    MAX_RETRY_INTERVAL,
    MCP_CALL_TIMEOUT,
    RETRY_TIMES,
)
from deeppresenter.utils.log import debug, set_logger, warning
from deeppresenter.utils.webview import PlaywrightConverter

mcp = FastMCP(name="Search")

FAKE_UA = UserAgent()

_DOWNLOAD_INDEX_PATH = DOWNLOAD_CACHE / ".index.json"


def _load_download_index() -> dict[str, dict]:
    """Load the download cache index mapping URL hashes to cache entries."""
    if _DOWNLOAD_INDEX_PATH.exists():
        try:
            with open(_DOWNLOAD_INDEX_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_download_index(index: dict[str, dict]) -> None:
    """Save the download cache index atomically."""
    DOWNLOAD_CACHE.mkdir(parents=True, exist_ok=True)
    tmp = _DOWNLOAD_INDEX_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    tmp.replace(_DOWNLOAD_INDEX_PATH)


def _url_hash(url: str) -> str:
    """Return a stable SHA256 hex digest for a URL."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _cache_path_for_url(url: str, suffix: str = "") -> Path:
    """Return the canonical cache file path for a URL."""
    h = _url_hash(url)
    # shard by first 2 chars to avoid too many files in one dir
    shard = h[:2]
    ext = suffix.lower() if suffix else ".bin"
    return DOWNLOAD_CACHE / shard / f"{h}{ext}"

# Google (SerpAPI)
GOOGLE_KEYS = [i.strip() for i in os.getenv("SERPAPI_KEY", "").split(",") if i.strip()]
SERPAPI_URL = "https://serpapi.com/search"

# Tavily
TAVILY_KEYS = [
    i.strip()
    for i in os.getenv("TAVILY_API_KEY", "").split(",")
    if i.strip().startswith("tvly")
]
TAVILY_API_URL = "https://api.tavily.com/search"

debug(f"{len(GOOGLE_KEYS)} SerpAPI keys loaded")
debug(f"{len(TAVILY_KEYS)} TAVILY keys loaded")


# ── Google helpers ─────────────────────────────────────────────────────────────


async def _serpapi_request(params: dict[str, Any]) -> dict[str, Any]:
    params = {**params, "api_key": GOOGLE_KEYS[0]}
    async with aiohttp.ClientSession() as session:
        async with session.get(SERPAPI_URL, params=params) as response:
            if response.status == 200:
                return await response.json()
            body = await response.text()
            warning(f"SERPAPI Error [{response.status}] body={body}")
            response.raise_for_status()
    raise RuntimeError("SerpAPI request failed")


# ── Tavily helpers ─────────────────────────────────────────────────────────────


async def _tavily_request(idx: int, params: dict) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "User-Agent": FAKE_UA.random}
    async with aiohttp.ClientSession() as session:
        async with session.post(
            TAVILY_API_URL, headers=headers, json=params
        ) as response:
            if response.status == 200:
                return await response.json()
            body = await response.text()
            if response.status == 429:
                await asyncio.sleep(MAX_RETRY_INTERVAL)
            else:
                await asyncio.sleep(RETRY_TIMES)
            warning(f"TAVILY Error [{idx:02d}] [{response.status}] body={body}")
            response.raise_for_status()
    raise RuntimeError("TAVILY request failed after retries")


async def _tavily_search(**kwargs) -> dict[str, Any]:
    last_error = None
    for idx, api_key in enumerate(TAVILY_KEYS, start=1):
        try:
            params = {**kwargs, "api_key": api_key}
            return await _tavily_request(idx, params)
        except Exception as e:
            warning(f"TAVILY search error with key {api_key[:16]}...: {e}")
            last_error = e
    raise RuntimeError(
        f"TAVILY search failed after {len(TAVILY_KEYS)} retries"
    ) from last_error


# ── Search tools (only one backend registered) ────────────────────────────────

if len(GOOGLE_KEYS):

    @mcp.tool()
    async def search_web(
        query: str,
        max_results: int = 3,
        time_range: Literal["month", "year"] | None = None,
    ) -> dict:
        """
        Search the web via Google (SerpAPI)

        Args:
            query: Search keywords
            max_results: Maximum number of search results, default 3
            time_range: Time range filter, "month", "year", or None

        Returns:
            dict: with fields:
                - query: the search query
                - total_results: number of results returned
                - results: list of dicts with title, url, displayed_link, content
        """
        debug(f"search_web via SerpAPI query={query!r}")
        params: dict[str, Any] = {"engine": "google", "q": query, "num": max_results}
        if time_range == "month":
            params["tbs"] = "qdr:m"
        elif time_range == "year":
            params["tbs"] = "qdr:y"

        result = await _serpapi_request(params)
        results = [
            {
                "title": item.get("title", ""),
                "url": item["link"],
                "displayed_link": item.get("displayed_link", ""),
                "content": item.get("snippet", ""),
            }
            for item in result.get("organic_results", [])
        ]
        return {"query": query, "total_results": len(results), "results": results}

    @mcp.tool()
    async def search_images(query: str) -> dict:
        """
        Search for web images via Google (SerpAPI)

        Returns:
            dict: with fields:
                - query: the search query
                - total_results: number of results returned
                - images: list of dicts with url, thumbnail, description
        """
        debug(f"search_images via SerpAPI query={query!r}")
        params: dict[str, Any] = {"engine": "google_images", "q": query, "num": 4}
        result = await _serpapi_request(params)
        images = [
            {
                "url": item["original"],
                "thumbnail": item.get("thumbnail", ""),
                "description": item.get("title", query),
            }
            for item in result.get("images_results", [])[:4]
        ]
        return {"query": query, "total_results": len(images), "images": images}

elif len(TAVILY_KEYS):

    @mcp.tool()
    async def search_web(
        query: str,
        max_results: int = 3,
        time_range: Literal["month", "year"] | None = None,
    ) -> dict:
        """
        Search the web via Tavily

        Args:
            query: Search keywords
            max_results: Maximum number of search results, default 3
            time_range: Time range filter, "month", "year", or None

        Returns:
            dict: with fields:
                - query: the search query
                - total_results: number of results returned
                - results: list of dicts with url, content
        """
        debug(f"search_web via Tavily query={query!r}")
        kwargs: dict[str, Any] = {
            "query": query,
            "search_depth": "basic",
            "max_results": max_results,
            "include_images": False,
            "include_image_descriptions": False,
            "include_favicon": True,
            "include_usage": True,
            "country": "china",
        }
        if time_range:
            kwargs["time_range"] = time_range

        result = await _tavily_search(**kwargs)
        results = [
            {"url": item["url"], "content": item["content"]}
            for item in result.get("results", [])
        ]
        return {"query": query, "total_results": len(results), "results": results}

    @mcp.tool()
    async def search_images(query: str) -> dict:
        """
        Search for web images via Tavily

        Returns:
            dict: with fields:
                - query: the search query
                - total_results: number of results returned
                - images: list of dicts with url, description
        """
        debug(f"search_images via Tavily query={query!r}")
        result = await _tavily_search(
            query=query,
            search_depth="basic",
            max_results=3,
            include_images=True,
            include_image_descriptions=True,
            include_favicon=True,
            include_usage=True,
            country="china",
        )
        images = [
            {"url": img["url"], "description": img["description"]}
            for img in result.get("images", [])
        ]
        return {"query": query, "total_results": len(images), "images": images}


# ── Other tools ───────────────────────────────────────────────────────────────


@mcp.tool()
async def fetch_url(url: str, body_only: bool = True) -> str:
    """
    Fetch web page content

    Args:
        url: Target URL
        body_only: If True, return only main content; otherwise return full page, default True
    """

    async with httpx.AsyncClient(follow_redirects=True, timeout=10.0) as client:
        try:
            resp = await client.head(url)

            # Some servers may return error on HEAD; fall back to GET
            if resp.status_code >= 400:
                resp = await client.get(url, stream=True)

            content_type = resp.headers.get("Content-Type", "").lower()
            content_dispo = resp.headers.get("Content-Disposition", "").lower()

            if "attachment" in content_dispo or "filename=" in content_dispo:
                return f"URL {url} is a downloadable file (Content-Disposition: {content_dispo})"

            if not content_type.startswith("text/html"):
                return f"URL {url} returned {content_type}, not a web page"

        # Do not block Playwright: ignore errors from httpx for banned/blocked HEAD requests
        except Exception:
            pass

    async with PlaywrightConverter() as converter:
        try:
            await converter.page.goto(
                url, wait_until="domcontentloaded", timeout=MCP_CALL_TIMEOUT // 2 * 1000
            )
            html = await converter.page.content()
        except TimeoutError:
            return f"Timeout when loading URL: {url}"
        except Exception as e:
            return f"Failed to load URL {url}: {e}"

    # Detect common anti-bot / access-denied pages before converting
    _ACCESS_DENIED_PATTERNS = re.compile(
        r"access.denied|forbidden|block.*page|captcha|"
        r"cloudflare.*ray\s*id|challenge.*platform|"
        r"just.a.moment|checking.your.browser|"
        r"you.don.?t.have.permission|error\s*reference",
        re.IGNORECASE,
    )
    if _ACCESS_DENIED_PATTERNS.search(html[:5000]):
        return f"Unable to fetch {url}: the website blocked automated access (Access Denied / WAF / anti-bot). Try a different source."

    markdown = markdownify.markdownify(html, heading_style=markdownify.ATX)
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
    if body_only:
        result = extract(
            html,
            output_format="markdown",
            with_metadata=True,
            include_links=True,
            include_images=True,
            include_tables=True,
        )
        return result or markdown

    return markdown


@mcp.tool()
async def download_file(url: str, output_file: str) -> str:
    """
    Download a file from a URL and save it to a local path.

    If the same URL has been downloaded before, the cached copy is reused
    automatically to avoid redundant network requests.
    """

    async def _fetch_bytes(target_url: str) -> bytes:
        async with httpx.AsyncClient(
            headers={"User-Agent": FAKE_UA.random},
            follow_redirects=True,
            verify=False,
            timeout=5.0,
        ) as client:
            async with client.stream("GET", target_url, timeout=5.0) as response:
                response.raise_for_status()
                return await response.aread()

    workspace = Path(os.getcwd())
    output_path = Path(output_file).resolve()
    assert output_path.is_relative_to(workspace), (
        f"Access denied: path outside allowed workspace: {workspace}"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    suffix = Path(output_path).suffix.lower()
    ext_format_map = Image.registered_extensions()

    # ── Check global download cache first ────────────────────────────────
    index = _load_download_index()
    url_h = _url_hash(url)
    cache_entry = index.get(url_h)
    if cache_entry:
        cached_file = Path(cache_entry["path"])
        if cached_file.exists() and cached_file.stat().st_size > 0:
            try:
                shutil.copy2(cached_file, output_path)
                # If the cached file is an image, report its resolution
                try:
                    with Image.open(output_path) as img:
                        width, height = img.size
                        return (
                            f"File reused from cache: {output_path} "
                            f"(resolution: {width}x{height}, cached from {url})"
                        )
                except Exception:
                    return f"File reused from cache: {output_path} (cached from {url})"
            except Exception as e:
                warning(f"Cache copy failed for {url}: {e}, falling back to download")

    # ── Download and write to both workspace and cache ───────────────────
    for retry in range(3):
        try:
            await asyncio.sleep(retry)
            data = await asyncio.wait_for(_fetch_bytes(url), timeout=5.0)
            try:
                with Image.open(BytesIO(data)) as img:
                    img.load()
                    save_format = ext_format_map.get(suffix, img.format)
                    note = ""
                    if img.format == "WEBP" or suffix == ".webp":
                        output_path = output_path.with_suffix(".png")
                        save_format = "PNG"
                        note = " (converted from WEBP to PNG)"
                    img.save(output_path, format=save_format)
                    width, height = img.size
                    # Save to global cache
                    cache_path = _cache_path_for_url(url, output_path.suffix)
                    cache_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(output_path, cache_path)
                    index[url_h] = {
                        "url": url,
                        "path": str(cache_path),
                        "size": cache_path.stat().st_size,
                    }
                    _save_download_index(index)
                    return f"File downloaded to {output_path} (resolution: {width}x{height}){note}"
            except Exception:
                with open(output_path, "wb") as f:
                    f.write(data)
                # Save non-image files to cache as well
                cache_path = _cache_path_for_url(url, suffix or ".bin")
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(output_path, cache_path)
                index[url_h] = {
                    "url": url,
                    "path": str(cache_path),
                    "size": cache_path.stat().st_size,
                }
                _save_download_index(index)
            break
        except Exception:
            pass
    else:
        return f"Failed to download file from {url}"

    return f"File downloaded to {output_path}"


@mcp.tool()
def list_download_cache(keyword: str = "") -> dict:
    """
    List files in the global download cache.

    Call this BEFORE searching or downloading to check if the required
    content already exists locally.  When a match is found, reuse the
    cached file path directly \u2014 no need to download again.

    Args:
        keyword: Optional keyword to filter results by filename or URL.
                 Empty string returns all cached files.

    Returns:
        dict: with fields:
            - total: number of matching files
            - files: list of dicts with path, url, size
    """
    index = _load_download_index()
    results = []
    kw = keyword.lower()
    for entry in index.values():
        p = Path(entry["path"])
        if not p.exists():
            continue
        if kw and kw not in p.name.lower() and kw not in entry.get("url", "").lower():
            continue
        results.append({
            "path": str(p),
            "url": entry.get("url", ""),
            "size": p.stat().st_size,
        })
    # Also include legacy files (no URL mapping, named by original filename)
    legacy_dir = DOWNLOAD_CACHE / "legacy"
    if legacy_dir.exists():
        for f in legacy_dir.rglob("*"):
            if not f.is_file():
                continue
            if kw and kw not in f.name.lower():
                continue
            results.append({
                "path": str(f),
                "url": "",
                "size": f.stat().st_size,
            })
    return {"total": len(results), "files": results}


if __name__ == "__main__":
    work_dir = Path(os.environ["WORKSPACE"])
    assert work_dir.exists(), f"Workspace {work_dir} does not exist."
    os.chdir(work_dir)
    set_logger(f"search-{work_dir.stem}", work_dir / ".history" / "search.log")

    mcp.run(show_banner=False)
