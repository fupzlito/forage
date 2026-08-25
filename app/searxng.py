"""SearXNG client for Forage search.

Calls the configured SearXNG instance (/search?format=json) and normalizes
the results into the Hermes web-search envelope enhanced with OpenWebUI
citation formatting, engine alias mapping, and error/timeout guidance:

    {
        "success": true,
        "data": {
            "web": [{title, url, description, position}],
            "sources": [{id, title, url, snippet, citation}],
            "formatted_results": "[1] [Title](url)\nSnippet..."
        },
        "warning": None,
        "unresponsive_engines": [...],
        "used_engines": [...],
        "available_engines": [...]
    }
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import ForageConfig

logger = logging.getLogger(__name__)

from datetime import datetime, timezone
from urllib.parse import urlparse

# Standard model alias normalization table for common hallucinated engine names
ENGINE_ALIASES: Dict[str, str] = {
    "google_search": "google",
    "googlesearch": "google",
    "bing_search": "bing",
    "bingsearch": "bing",
    "ddg": "duckduckgo",
    "duckduckgo_search": "duckduckgo",
    "brave_search": "brave",
    "startpage_search": "startpage",
    "qwant_news": "qwant news",
    "qwantnews": "qwant news",
    "wiki": "wikipedia",
    "wikipedia_search": "wikipedia",
    "github_search": "github",
    "yahoo_search": "yahoo",
}


def extract_domain(url: str) -> str:
    """Extract clean domain name (TLD/host) from a URL."""
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or parsed.path).split(":")[0].lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return url


def get_favicon_url(domain: str) -> str:
    """Generate Google favicon service URL for domain."""
    if not domain:
        return ""
    return f"https://www.google.com/s2/favicons?domain={domain}&sz=32"


KNOWN_DOMAINS = {
    "finance.yahoo.com": "Yahoo Finance",
    "marketwatch.com": "MarketWatch",
    "bloomberg.com": "Bloomberg",
    "reuters.com": "Reuters",
    "wikipedia.org": "Wikipedia",
    "impactwealth.org": "Impact Wealth",
}


def get_clean_source_name(title: str, domain: str) -> str:
    """Extract clean brand/site name from title or fallback to domain."""
    if title:
        clean = title.strip()
        for delim in (" - ", " | ", " — ", " :: "):
            if delim in clean:
                parts = clean.split(delim)
                last = parts[-1].strip()
                if 2 <= len(last) <= 30:
                    return last
                first = parts[0].strip()
                if 2 <= len(first) <= 30:
                    return first
        if len(clean) <= 25:
            return clean

    if domain:
        dom_lower = domain.lower()
        if dom_lower in KNOWN_DOMAINS:
            return KNOWN_DOMAINS[dom_lower]
        dom_name = domain.split(".")[0]
        return dom_name.title()
    return "Source"


def format_citation(
    title: str,
    domain: str,
    url: str,
    position: int = 1,
    style: str = "site_name",
) -> str:
    """Format citation link based on requested citation_style.

    All styles produce standard markdown links that render as clickable
    text in OpenWebUI and other markdown consumers.
    """
    site_name = get_clean_source_name(title, domain)
    if style == "site_name":
        return f"[{site_name}]({url})"
    elif style == "site_name_bold":
        return f"[**{site_name}**]({url})"
    elif style == "site_name_italic":
        return f"[*{site_name}*]({url})"
    elif style == "site_name_brackets":
        return f"[[{site_name}]({url})]"
    elif style == "academic":
        return f"[[{position}]({url})]"
    elif style == "bracket_domain":
        return f"[{domain}]({url})" if domain else f"[{site_name}]({url})"
    elif style == "bracket_title":
        return f"[Source: {site_name}]({url})"
DEFAULT_AVAILABLE_ENGINES = (
    "google", "qwant", "qwant news", "brave", "bing", "startpage", "duckduckgo", "reddit",
    "wikipedia", "youtube", "github", "searxng", "yahoo", "wikidata"
)
TEXT_SEARCH_CATEGORIES = {
    "general", "news", "it", "science", "scientific publications",
    "social media", "social_media", "q&a", "web", "software wikis", "repos", "blogs", "books", "dictionaries"
}
NON_TEXT_CATEGORIES = {"images", "videos", "music", "audio", "files", "map", "radio", "weather", "icons", "currency", "translate"}
NON_TEXT_SUFFIXES = (".images", ".videos", ".audio", ".files", " images", " videos", " audio", " music", " weather")

_cached_available_engines: Optional[Tuple[str, ...]] = None
_cached_general_engines: Optional[Tuple[str, ...]] = None
_last_engine_fetch: float = 0.0


def fetch_searxng_engines_sync(searxng_url: str, timeout: float = 2.0) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Query SearXNG GET /config synchronously to discover active text/web engines and default category engines.

    Filters out media/torrent/package/map engines to keep tool descriptions and prompt context concise.

    Returns:
        (available_engines_tuple, general_category_engines_tuple)
    """
    url = f"{searxng_url.rstrip('/')}/config"
    try:
        resp = httpx.get(url, timeout=timeout, headers={"Accept": "application/json"})
        if resp.status_code == 200:
            data = resp.json()
            raw_engines = data.get("engines", [])
            available: List[str] = []
            general: List[str] = []
            for e in raw_engines:
                if isinstance(e, dict):
                    name = e.get("name")
                    is_enabled = e.get("enabled") if "enabled" in e else not e.get("disabled", False)
                    if name and is_enabled:
                        categories = [c.lower().strip() for c in e.get("categories", [])]
                        is_general_cat = "general" in categories or not categories
                        is_media_cat = any(cat in NON_TEXT_CATEGORIES for cat in categories)
                        is_media_name = any(name.lower().endswith(sfx) for sfx in NON_TEXT_SUFFIXES)

                        if is_general_cat and not is_media_cat and not is_media_name:
                            available.append(name)
                            general.append(name)
                elif isinstance(e, str) and e:
                    clean = e.lower().strip()
                    if not any(clean.endswith(sfx) for sfx in NON_TEXT_SUFFIXES):
                        available.append(e)
            if available:
                return tuple(available), tuple(general)
    except Exception as exc:
        logger.debug("Failed to fetch SearXNG /config (%s): %s", url, exc)
    return (), ()


def get_live_available_engines(config: ForageConfig) -> Tuple[str, ...]:
    """Return live available engines.

    If config.search.available_engines is explicitly configured, returns it directly.
    Otherwise, auto-discovers live text/web engines from SearXNG GET /config with a 5-minute cache.
    """
    if config.search.available_engines is not None:
        return config.search.available_engines

    global _cached_available_engines, _cached_general_engines, _last_engine_fetch
    import time
    now = time.monotonic()
    if _cached_available_engines and (now - _last_engine_fetch < 300):
        return _cached_available_engines

    avail, gen = fetch_searxng_engines_sync(config.search.searxng_url)
    if avail:
        _cached_available_engines = avail
        _cached_general_engines = gen
        _last_engine_fetch = now
        return avail

    # Fallback default text engines baseline
    return DEFAULT_AVAILABLE_ENGINES


def normalize_and_validate_engines(
    requested: Optional[List[str]],
    default_engines: Tuple[str, ...],
    available_engines: Tuple[str, ...],
) -> Tuple[List[str], Optional[str]]:
    """Resolve aliases and validate requested engines against available SearXNG engines.

    Returns:
        (valid_engines_list, warning_message_if_any)
    """
    if not requested:
        return list(default_engines), None

    available_set = {e.lower() for e in available_engines}
    normalized: List[str] = []
    invalid: List[str] = []

    for eng in requested:
        clean = eng.strip().lower()
        resolved = ENGINE_ALIASES.get(clean, clean)
        if resolved in available_set:
            if resolved not in normalized:
                normalized.append(resolved)
        else:
            invalid.append(eng)

    warning: Optional[str] = None
    if invalid:
        if normalized:
            warning = (
                f"Ignored unknown or unsupported search engine(s): {invalid}. "
                f"Used available engine(s): {normalized}. Available engines: {list(available_engines)}."
            )
        else:
            normalized = list(default_engines)
            warning = (
                f"None of the requested engines {invalid} are available. "
                f"Auto-selected default engine(s): {normalized}. Available engines: {list(available_engines)}."
            )

    return normalized, warning


def _format_published_date(raw: Any) -> Optional[str]:
    """Format SearXNG publishedDate into a clean ISO or UTC string."""
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    if isinstance(raw, str):
        raw_str = raw.strip()
        if not raw_str:
            return None
        try:
            clean = raw_str.replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            if dt.hour == 0 and dt.minute == 0 and dt.second == 0 and ("T" not in raw_str and ":" not in raw_str):
                return dt.strftime("%Y-%m-%d")
            return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            return raw_str
    return str(raw).strip() or None


_MONTHS_PATTERN = (
    r"Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?"
)
_REL_UNITS_PATTERN = (
    r"min|mins|minute|minutes|hour|hours|hr|hrs|day|days|week|weeks|month|months|year|years"
)

_SNIPPET_DATE_PATTERNS = [
    # 1. Google / Bing prefix: 'Jul 1, 2026 ...', 'August 7, 2026 —', 'Jan 15, 2026 ·'
    re.compile(
        rf"^((?:{_MONTHS_PATTERN})\.?\s+\d{{1,2}},?\s+\d{{4}})\s*(?:\.\.\.|[-—–·|:]|\s{{2,}})\s*(.*)$",
        re.IGNORECASE,
    ),
    # 2. Relative time prefix: '3 hours ago ...', '2 days ago -'
    re.compile(
        rf"^(\d+\s+(?:{_REL_UNITS_PATTERN})\s+ago)\s*(?:\.\.\.|[-—–·|:]|\s{{2,}})\s*(.*)$",
        re.IGNORECASE,
    ),
    # 3. ISO date prefix: '2026-08-07 ...'
    re.compile(r"^(\d{4}-\d{2}-\d{2})\s*(?:\.\.\.|[-—–·|:]|\s{2,})\s*(.*)$"),
    # 4. News dateline: 'Jerusalem, Israel (February 10, 2026) Israel is...'
    re.compile(
        rf"^([A-Za-z\s,.\-\'/]+?)\s*\(((?:{_MONTHS_PATTERN})\s+\d{{1,2}},?\s+\d{{4}})\)\s*[-—–·:]?\s*(.*)$",
        re.IGNORECASE,
    ),
]


def _extract_snippet_date(snippet: str) -> tuple[Optional[str], str]:
    """Extract leading published date from search snippet text if present.

    Returns (date_str, cleaned_snippet).
    """
    if not snippet:
        return None, snippet
    s = snippet.strip()
    for p in _SNIPPET_DATE_PATTERNS[:3]:
        m = p.match(s)
        if m:
            date_str = m.group(1).strip()
            rest = m.group(2).strip()
            return date_str, rest
    m = _SNIPPET_DATE_PATTERNS[3].match(s)
    if m:
        location = m.group(1).strip()
        date_str = m.group(2).strip()
        body = m.group(3).strip()
        rest = f"{location} - {body}" if location else body
        return date_str, rest
    return None, snippet


def search_searxng(
    config: ForageConfig,
    query: str,
    limit: Optional[int] = None,
    language: Optional[str] = None,
    engines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute a search against SearXNG and return normalized web results with sources & guidance."""
    base = config.search.searxng_url.rstrip("/")
    live_available = get_live_available_engines(config)
    validated_engines, engine_warning = normalize_and_validate_engines(
        engines,
        config.search.engines,
        live_available,
    )

    params: Dict[str, Any] = {
        "q": query,
        "format": "json",
        "pageno": 1,
    }
    if language:
        params["language"] = language
    if validated_engines:
        params["engines"] = ",".join(validated_engines)

    try:
        resp = httpx.get(
            f"{base}/search",
            params=params,
            timeout=config.search.timeout,
            headers={"Accept": "application/json"},
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning("SearXNG HTTP error: %s", exc)
        return {
            "success": False,
            "error": f"SearXNG returned HTTP {exc.response.status_code}. Do not hammer search; wait or try alternative query terms.",
            "retryable": False,
            "all_engines": ", ".join(live_available),
        }
    except httpx.RequestError as exc:
        logger.warning("SearXNG request error: %s", exc)
        return {
            "success": False,
            "error": f"Could not reach SearXNG at {base} (timeout or network error): {exc}. Avoid rapid repeated retries.",
            "retryable": False,
            "all_engines": ", ".join(live_available),
        }

    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("SearXNG response parse error: %s", exc)
        return {"success": False, "error": "Could not parse SearXNG response as JSON"}

    raw_results = data.get("results", [])
    raw_unresponsive = data.get("unresponsive_engines", [])
    unresponsive_engines = [\
        {"engine": item[0], "reason": item[1]} if isinstance(item, (list, tuple)) and len(item) >= 2 else {"engine": str(item), "reason": "unknown"}\
        for item in raw_unresponsive\
    ]

    # Identify engines that returned data vs unresponsive
    unresponsive_set = {u["engine"].lower() for u in unresponsive_engines}
    engines_with_data = {r.get("engine", "").lower() for r in raw_results if r.get("engine")}
    if raw_results:
        successful_engines = sorted(list(engines_with_data or ({e for e in validated_engines if e not in unresponsive_set})))
    else:
        successful_engines = []

    searched_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    default_lim = getattr(config.search, "default_limit", 10)
    max_lim = getattr(config.search, "max_limit", 50)
    requested_limit = limit if limit is not None else default_lim
    effective_limit = max(1, min(requested_limit, max_lim))

    include_fav = config.search.include_favicon if getattr(config.search, "include_favicon", None) is not None else getattr(config.tools, "include_favicon", False)

    unified_results = []
    total_snippet_chars = 0
    max_snippet_len = getattr(config.search, "max_snippet_chars", 350)
    max_total_len = getattr(config.search, "max_total_snippet_chars", 3000)

    # Rank by SearXNG score
    sorted_results = sorted(
        raw_results,
        key=lambda r: float(r.get("score", 0) or 0),
        reverse=True,
    )

    for idx, r in enumerate(sorted_results[:effective_limit]):
        u = r.get("url", "")
        t = r.get("title", "")
        c = r.get("content", "") or ""
        dom = extract_domain(u)
        fav = get_favicon_url(dom) if include_fav else None
        pub_date = _format_published_date(r.get("publishedDate") or r.get("pubdate") or r.get("published_date") or r.get("date"))

        # Fallback: Extract high-confidence date prefix from snippet if SearXNG didn't provide publishedDate
        if not pub_date and c:
            extracted_date, clean_c = _extract_snippet_date(c)
            if extracted_date:
                pub_date = extracted_date
                c = clean_c

        if len(c) > max_snippet_len:
            c = c[:max_snippet_len].rsplit(" ", 1)[0] + "... [TRUNCATED]"

        if total_snippet_chars + len(c) > max_total_len and idx > 0:
            c = c[: max(0, max_total_len - total_snippet_chars)].rsplit(" ", 1)[0] + "... [TRUNCATED]"

        cit_style = getattr(config.search, "citation_style", "site_name")
        cit_link = format_citation(t, dom, u, position=idx + 1, style=cit_style)

        item: Dict[str, Any] = {
            "position": idx + 1,
            "domain": dom,
            "url": u,
            "title": t,
        }
        if pub_date:
            item["published_date"] = pub_date
        item["snippet"] = c
        item["citation"] = cit_link
        if include_fav and fav:
            item["favicon"] = fav

        unified_results.append(item)

    # Format single-line string representations
    returned_engines_str = ", ".join(successful_engines)
    unresponsive_str = "; ".join(f"{u['engine']} - {u['reason']}" for u in unresponsive_engines) if unresponsive_engines else None
    requested_engines_str = ", ".join(validated_engines)
    all_engines_str = ", ".join(live_available)

    # Formulate deduplicated model guidance warning without listing engines
    warnings: List[str] = []
    if engine_warning:
        warnings.append(engine_warning)

    if unresponsive_engines and sorted_results:
        warnings.append("Some requested search engines failed or were unresponsive; results may be limited or skewed.")

    if not sorted_results:
        if unresponsive_engines:
            warnings.append("Search returned 0 results because underlying engines timed out or failed. Do NOT hammer the search tool with repeated identical queries.")
        else:
            warnings.append(f"No search results found for query '{query}'. Consider revising search keywords or adjusting filters.")

    combined_warning = " ".join(warnings) if warnings else None

    return {
        "success": True,
        "searched_at": searched_at,
        "requested_engines": requested_engines_str,
        "returned_engines": returned_engines_str,
        "unresponsive_engines": unresponsive_str,
        "results": unified_results,
        "warning": combined_warning,
        "all_engines": all_engines_str,
    }
