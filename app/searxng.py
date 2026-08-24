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
    "israelhayom.com": "Israel Hayom",
    "timesofisrael.com": "Times of Israel",
    "vinnews.com": "VINnews",
    "yahoo.com": "Yahoo Finance",
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
    """Format citation link based on requested citation_style."""
    site_name = get_clean_source_name(title, domain)
    if style == "site_name":
        return f"[{site_name}]({url})"
    elif style == "site_name_code":
        return f"[`{site_name}`]({url})"
    elif style == "site_name_bold":
        return f"[**{site_name}**]({url})"
    elif style == "site_name_italic":
        return f"[*{site_name}*]({url})"
    elif style == "superscript":
        return f"<sup>[{site_name}]({url})</sup>"
    elif style == "bracket_numeric":
        return f"[{position}]({url})"
    elif style == "bracket_domain":
        return f"[{domain}]({url})" if domain else f"[{position}]({url})"
    elif style == "bracket_title":
        return f"[Source: {site_name}]({url})"
    else:
        return f"[{site_name}]({url})"


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


def search_searxng(
    config: ForageConfig,
    query: str,
    limit: int,
    language: Optional[str] = None,
    engines: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute a search against SearXNG and return normalized web results with sources & guidance."""
    base = config.search.searxng_url.rstrip("/")
    validated_engines, engine_warning = normalize_and_validate_engines(
        engines,
        config.search.engines,
        config.search.available_engines,
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
            "available_engines": list(config.search.available_engines),
        }
    except httpx.RequestError as exc:
        logger.warning("SearXNG request error: %s", exc)
        return {
            "success": False,
            "error": f"Could not reach SearXNG at {base} (timeout or network error): {exc}. Avoid rapid repeated retries.",
            "retryable": False,
            "available_engines": list(config.search.available_engines),
        }

    try:
        data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("SearXNG response parse error: %s", exc)
        return {"success": False, "error": "Could not parse SearXNG response as JSON"}

    raw_results = data.get("results", [])
    raw_unresponsive = data.get("unresponsive_engines", [])
    unresponsive_engines = [
        {"engine": item[0], "reason": item[1]} if isinstance(item, (list, tuple)) and len(item) >= 2 else {"engine": str(item), "reason": "unknown"}
        for item in raw_unresponsive
    ]

    # Identify engines that successfully returned data vs unresponsive
    unresponsive_set = {u["engine"].lower() for u in unresponsive_engines}
    engines_with_data = {r.get("engine", "").lower() for r in raw_results if r.get("engine")}
    successful_engines = sorted(list(engines_with_data or ({e for e in validated_engines if e not in unresponsive_set})))

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    effective_limit = min(limit or getattr(config.search, "default_limit", 10), getattr(config.search, "max_limit", 50))

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
        fav = get_favicon_url(dom)

        if len(c) > max_snippet_len:
            c = c[:max_snippet_len].rsplit(" ", 1)[0] + "..."

        if total_snippet_chars + len(c) > max_total_len and idx > 0:
            c = c[: max(0, max_total_len - total_snippet_chars)].rsplit(" ", 1)[0] + "..."

        cit_style = getattr(config.search, "citation_style", "superscript")
        cit_link = format_citation(t, dom, u, position=idx + 1, style=cit_style)

        unified_results.append({
            "id": idx + 1,
            "source_id": str(idx + 1),
            "position": idx + 1,
            "name": t,
            "title": t,
            "domain": dom,
            "favicon": fav,
            "url": u,
            "snippet": c,
            "citation": cit_link,
        })

    # Formulate explicit model guidance warning if empty or unresponsive
    warnings: List[str] = []
    if engine_warning:
        warnings.append(engine_warning)

    if unresponsive_engines:
        unresponsive_summary = ", ".join(f"{u['engine']} ({u['reason']})" for u in unresponsive_engines)
        warnings.append(f"Unresponsive engines (failed/CAPTCHA'd): {unresponsive_summary}.")
        if successful_engines:
            warnings.append(f"Results were retrieved ONLY from working engine(s): {successful_engines}. Results may be off-topic or limited.")

    if not sorted_results:
        if unresponsive_engines:
            warnings.append("Search returned 0 results because underlying engines timed out or failed. Do NOT hammer the search tool with repeated identical queries.")
        else:
            warnings.append(f"No search results found for query '{query}'. Consider revising search keywords or adjusting filters.")

    combined_warning = " ".join(warnings) if warnings else None

    return {
        "success": True,
        "timestamp": now_utc,
        "results": unified_results,
        "warning": combined_warning,
        "successful_engines": successful_engines,
        "unresponsive_engines": unresponsive_engines,
        "used_engines": validated_engines,
        "available_engines": list(config.search.available_engines),
    }
