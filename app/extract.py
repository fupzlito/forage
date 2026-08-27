"""Hybrid extraction: static HTTP first, browser fallback.

Top-level flow of ``extract_url`` (config-driven):

  1. Normalize the Reddit URL and resolve any domain override.
  2. Document path (PDF/DOCX/XLSX/PPTX/RTF) if not force-rendered.
  3. Reddit fast path: Tier 1 (official ``.json``) -> Tier 2 (Redlib mirror)
     -> Tier 3 (browser fallback).
  4. Static fetch with markdown negotiation.
  5. Hybrid browser fallback when the static result is not enough:
     SPA markers, content density, empty ``<main>``, or text below the
     configured minimum.
  6. Challenge detection -> solver retry.
  7. Convert the result with ``_to_output``.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

import httpx
import trafilatura
from markdownify import markdownify as _markdownify

from .browser import BrowserPool
from .config import ForageConfig
from .documents import extract_document_bytes, looks_like_document, parse_reddit_json

logger = logging.getLogger(__name__)

# One short retry for transient server-side errors (rate limit / 5xx blips).
# Static-only: the hybrid flow already falls back to the browser on 403/429,
# so retrying here targets brief 429/5xx spikes, not persistent blocks.
RETRY_STATUS = {429, 500, 502, 503, 504}
RETRY_DELAY = 0.5
RETRY_ATTEMPTS = 2  # total attempts: 1 initial + 1 retry

SPA_MARKERS = [
    'id="root"',
    'id="app"',
    'id="__next"',
    'id="app-root"',
    'id="nuxt"',
    'id="svelte"',
    "data-reactroot",
    "ng-app=",          # attribute form only; bare "ng-app" matches "shopping-app" in prose
    "ng-version",       # Angular runtime
    "__NEXT_DATA__",
    "__NUXT__",
    "__INITIAL_STATE__",
    "__APOLLO_STATE__",
    "data-svelte",      # Svelte compiler marker
    'id="__gatsby"',    # Gatsby
    "ytInitialData",  # YouTube (present in the static HTML shell)
    "ytcfg",          # YouTube config blob
]

# A page whose <main> exists but is (nearly) empty in the static HTML is
# almost certainly rendered by JS: the container is a placeholder and the
# real content only mounts client-side. Only fires when <main> is present,
# so pages that never use <main> are untouched.
EMPTY_MAIN_CHARS = 100


def looks_like_spa(html: str) -> bool:
    low = html.lower()
    return any(marker in low for marker in SPA_MARKERS)


def _main_text_chars(html: str) -> Optional[int]:
    """Return the visible text length inside <main>...</main>, or None when
    the page has no <main> element."""
    match = re.search(r"<main[^>]*>(.*?)</main>", html, flags=re.S | re.I)
    if not match:
        return None
    inner = match.group(1)
    inner = re.sub(r"<script[^>]*>.*?</script>", " ", inner, flags=re.S | re.I)
    inner = re.sub(r"<style[^>]*>.*?</style>", " ", inner, flags=re.S | re.I)
    inner = re.sub(r"<[^>]+>", " ", inner)
    return len(re.sub(r"\s+", " ", inner).strip())


def needs_browser_render(
    html: str,
    text: str,
    min_content_chars: int,
) -> Tuple[bool, str]:
    """Decide whether a statically-fetched page needs browser rendering.

    Multiple independent checks; if ANY of them fires, the page goes to the
    browser:
      1. Framework markers in the static HTML (React/Next/Vue/Angular/Svelte/
         Gatsby/YouTube shells).
      2. Content density: a large HTML document (>= 50 KB) whose extracted
         text is tiny relative to its size (<= max(500, 1% of HTML)). Static
         pages have a much higher text/HTML ratio; a big shell with almost no
         text means the content is mounted by JS.
      3. Empty <main> container: <main> exists in the static HTML but holds
         virtually no text (<= 100 chars), so it is a placeholder awaiting
         client-side rendering.
      4. Extracted text below the configured absolute minimum.

    Returns (needs_render, reason) where reason describes which check fired.
    """
    if looks_like_spa(html):
        return True, "SPA markers in static HTML"
    html_len = len(html)
    if html_len >= 50_000 and len(text) <= max(500, html_len // 100):
        return True, (
            f"content density too low ({len(text)} chars of text "
            f"in {html_len} chars of HTML)"
        )
    main_chars = _main_text_chars(html)
    if main_chars is not None and main_chars <= EMPTY_MAIN_CHARS:
        return True, f"<main> container empty in static HTML ({main_chars} chars)"
    if len(text) < min_content_chars:
        return True, f"low content ({len(text)} chars < min {min_content_chars})"
    return False, ""


def _domain(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _host_and_path(url: str) -> Tuple[str, str]:
    """Return (www-normalized host, path) for a URL."""
    host = _domain(url)
    if host.startswith("www."):
        host = host[4:]
    path = urlparse(url).path or "/"
    return host, path


def _host_suffixes(host: str) -> Tuple[str, ...]:
    """All suffix labels of a host, e.g. a.b.example.com ->
    (a.b.example.com, b.example.com, example.com, com)."""
    parts = host.split(".")
    return tuple(".".join(parts[i:]) for i in range(len(parts)))


def _pattern_matches(url: str, pattern: str) -> bool:
    """Match a URL against a domain override pattern.

    Pattern syntax (www-insensitive, case-insensitive):
      - ``x.com``            -> host or any subdomain (endswith match)
      - ``.x.com``           -> same, leading dot is explicit convention
      - ``amazon.*``         -> wildcard on a host label (fnmatch); matches
                                the host itself and any subdomain suffix
      - ``reddit.com/r/``    -> exact host + path prefix (path matching)
    A pattern with a path requires an exact host match, so
    ``reddit.com/r/`` does NOT match ``old.reddit.com/r/``.
    """
    pattern = (pattern or "").lower().strip()
    if not pattern:
        return False
    if pattern.startswith("www."):
        pattern = pattern[4:]
    # Leading dot is an explicit "base domain + subdomains" convention.
    if pattern.startswith("."):
        pattern = pattern[1:]
    if "/" in pattern:
        host_pat, _, path_pat = pattern.partition("/")
        path_pat = "/" + path_pat
        host, path = _host_and_path(url)
        if host != host_pat:
            return False
        return path.startswith(path_pat.rstrip("/"))
    host, _ = _host_and_path(url)
    return any(fnmatch.fnmatch(s, pattern) for s in _host_suffixes(host))


def _find_override(url: str, overrides: Tuple[Any, ...]) -> Optional[Any]:
    """Return the most specific domain override matching a URL, or None.

    Specificity: pattern length (a ``reddit.com/r/`` pattern beats
    ``reddit.com``), then declaration order. Patterns are matched on the
    original URL BEFORE any rewrite is applied."""
    best = None
    best_len = -1
    for override in overrides:
        if not _pattern_matches(url, override.pattern):
            continue
        if len(override.pattern) > best_len:
            best = override
            best_len = len(override.pattern)
    return best


def rewrite_url(url: str, override: Optional[Any]) -> str:
    """Apply a domain override's url_rewrite to a URL.

    The override pattern is the match (host[/path-prefix], www-insensitive)
    and ``override.url_rewrite`` is the replacement. Scheme, the remaining
    path, query and fragment are preserved.

    Example: pattern="reddit.com/r/", url_rewrite="old.reddit.com/r/" turns
    https://www.reddit.com/r/selfhosted/comments/xyz into
    https://old.reddit.com/r/selfhosted/comments/xyz.
    """
    if override is None or not override.url_rewrite:
        return url
    match = (override.pattern or "").lower()
    if match.startswith("www."):
        match = match[4:]
    if match.startswith("."):
        match = match[1:]
    m_host, _, m_path = match.partition("/")
    m_path = "/" + m_path if m_path else ""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path or "/"
    if host != m_host or not path.startswith(m_path):
        return url
    replace = (override.url_rewrite or "").lower()
    if replace.startswith("www."):
        replace = replace[4:]
    r_host, _, r_path = replace.partition("/")
    r_path = "/" + r_path if r_path else ""
    rest = path[len(m_path):]
    if r_path:
        new_path = r_path.rstrip("/") + "/" + rest.lstrip("/") if rest else r_path.rstrip("/") + "/"
    else:
        new_path = "/" + rest.lstrip("/") if rest else "/"
    new_url = f"{parsed.scheme}://{r_host}{new_path}"
    if parsed.query:
        new_url += "?" + parsed.query
    if parsed.fragment:
        new_url += "#" + parsed.fragment
    return new_url


CHALLENGE_TITLES = [
    "attention required",
    "just a moment",
    "checking your browser",
    "verifying your browser",
    "verify you are human",
    "security check",
    "robot or human",
    "access denied",
    "ddos-guard",
    "sucuri",
    "website is using a security service",
]
# NOTE: "challenge-platform" is deliberately NOT a marker. Cloudflare injects
# /cdn-cgi/challenge-platform/scripts/jsd/main.js into EVERY page it serves
# (JS detections), even with no active challenge - the substring would false
# positive on any Cloudflare-backed site.
CHALLENGE_MARKERS = [
    "cf-challenge",
    "cf-browser-verification",
    "cf-error-details",
    "protected by anubis",
    "anubis uses a proof-of-work",
]


def looks_like_challenge(html: str, title: str) -> bool:
    """Detect anti-bot challenge pages (Cloudflare, DDoS-Guard, etc.).

    Title match is the primary signal; marker match is secondary. The
    generic word "captcha" is deliberately NOT a marker; MediaWiki and
    other sites embed it in edit/config scripts (false positive).
    """
    low_title = title.lower()
    if any(marker in low_title for marker in CHALLENGE_TITLES):
        return True
    low_html = html.lower()
    return any(marker in low_html for marker in CHALLENGE_MARKERS)


def _extract_title(html: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.S | re.I)
    if match:
        return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def _title_from_url(url: str) -> str:
    """Derive a human-readable title fallback from a URL path."""
    parsed = urlparse(url)
    slug = parsed.path.rstrip("/").split("/")[-1]
    if slug:
        slug = re.sub(r"[-_]+", " ", slug).strip().capitalize()
        return slug
    return parsed.netloc or url


def _markdown_title(markdown: str, url: str) -> str:
    """Derive a title from markdown: the first ``#`` heading, else the URL
    path slug, else the host. Used for native ``text/markdown`` responses
    (which have no HTML <title> to scrape)."""
    for line in markdown.splitlines():
        m = re.match(r"^\s*#\s+(.+)$", line)
        if m:
            return re.sub(r"\s+", " ", m.group(1)).strip()
    slug = urlparse(url).path.rstrip("/").split("/")[-1]
    if slug:
        # Title-case each word (not .capitalize(), which lowercases the rest).
        return re.sub(r"[-_]+", " ", slug).strip().title()
    host = urlparse(url).netloc
    if host:
        # Strip a leading "www." (case-insensitive) then title-case the rest.
        host = re.sub(r"^\*?www\.", "", host, flags=re.I).strip()
        return host.capitalize()
    return url


async def _check_robots(client: httpx.AsyncClient, config: ForageConfig, url: str) -> Optional[str]:
    """Return an error string when robots.txt disallows the URL, else None."""
    if not config.extract.respect_robots:
        return None
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = await client.get(robots_url, timeout=5)
        if resp.status_code != 200:
            return None
        path = parsed.path or "/"
        disallowed = False
        user_agent = "*"
        for raw in resp.text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            key, value = key.strip().lower(), value.strip()
            if key == "user-agent":
                user_agent = value
            elif key == "disallow" and user_agent == "*":
                if value == "":
                    disallowed = False
                elif value == "/" or path.startswith(value):
                    disallowed = True
        if disallowed:
            return f"Blocked by robots.txt ({robots_url})"
    except httpx.RequestError:
        pass
    return None


async def _extract_document(
    config: ForageConfig,
    url: str,
    timeout: int,
) -> Optional[Dict[str, Any]]:
    """Download and extract a document (pdf/docx/xlsx/pptx/rtf).

    Returns the Hermes envelope entry when the URL yields a parseable
    document; None when it is not a document or parsing fails, so the
    caller falls through to the normal hybrid flow."""
    headers = {
        "User-Agent": config.extract.user_agent,
        "Accept": "*/*",
    }
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            resp = await client.get(url, headers=headers)
    except httpx.RequestError as exc:
        logger.warning("Document download failed for %s: %s", url, exc)
        return None
    if resp.status_code != 200:
        logger.info("%s -> HTTP %d, falling back to hybrid", url, resp.status_code)
        return None
    content_type = resp.headers.get("content-type", "")
    if not looks_like_document(url, content_type):
        return None
    try:
        text, title, method_label = extract_document_bytes(
            resp.content,
            url,
            content_type=content_type,
            max_chars=config.extract.max_content_chars,
        )
    except ValueError as exc:
        logger.info("%s -> document parse failed (%s), falling back to hybrid", url, exc)
        return None
    if config.extract.raw_content_markdown:
        raw_content = text
    else:
        raw_content = ""
    return {
        "url": url,
        "title": title,
        "content": text,
        "raw_content": raw_content,
        "method": method_label,
    }


async def fetch_static(
    config: ForageConfig,
    url: str,
    extra_headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
) -> Tuple[Optional[str], int, str, str]:
    """Fetch URL with plain HTTP. Returns (html, status, final_url, content_type).

    When ``config.extract.prefer_markdown`` is true, the request negotiates
    ``Accept: text/markdown`` first. A server that implements markdown
    negotiation (e.g. via .htaccess / Vary: Accept) answers with
    ``text/markdown``; the caller then uses the body directly as markdown
    without running trafilatura. Servers without negotiation ignore the
    Accept and return ``text/html``, so the normal hybrid flow continues."""
    accept = (
        "text/markdown,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        if config.extract.prefer_markdown
        else "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    )
    headers = {
        "User-Agent": config.extract.user_agent,
        "Accept": accept,
    }
    if extra_headers:
        headers.update(extra_headers)

    client_kwargs: Dict[str, Any] = {
        "follow_redirects": True,
        "timeout": config.extract.timeout,
    }
    if cookies:
        client_kwargs["cookies"] = cookies

    async with httpx.AsyncClient(**client_kwargs) as client:
        robots_error = await _check_robots(client, config, url)
        if robots_error:
            return None, 0, url, ""  # caller treats 0 as blocked-by-robots
        for attempt in range(RETRY_ATTEMPTS):
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code in RETRY_STATUS and attempt < RETRY_ATTEMPTS - 1:
                    logger.info(
                        "%s -> HTTP %d (transient), retrying in %.1fs",
                        url, resp.status_code, RETRY_DELAY,
                    )
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                return resp.text, resp.status_code, str(resp.url), content_type
            except httpx.RequestError as exc:
                if attempt < RETRY_ATTEMPTS - 1:
                    logger.info("%s -> request error, retrying in %.1fs: %s", url, RETRY_DELAY, exc)
                    await asyncio.sleep(RETRY_DELAY)
                    continue
                logger.warning("Static fetch failed for %s: %s", url, exc)
                return None, 0, url, ""
        return None, 0, url, ""


def _plain_text(html: str) -> str:
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()


def _extract_text(html: str, only_main_content: bool, max_chars: int) -> str:
    if only_main_content:
        text = trafilatura.extract(
            html,
            output_format="markdown",  # structured markdown (headings, bold, lists)
            include_comments=False,
            include_tables=True,
            favor_precision=False,
        )
        text = text or ""
    else:
        text = _plain_text(html)
    return text[:max_chars]


def _strip_reddit_ads_from_html(html: str) -> str:
    """Strip Reddit ad web-components and preserve timestamp elements prior to extraction."""
    if not html:
        return html
    # Remove <shreddit-ad-post>...</shreddit-ad-post>
    html = re.sub(r"<shreddit-ad-post[^>]*>.*?</shreddit-ad-post>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove <shreddit-comments-page-ad>...</shreddit-comments-page-ad>
    html = re.sub(r"<shreddit-comments-page-ad[^>]*>.*?</shreddit-comments-page-ad>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove promoted links (<div class="...promotedlink...">...</div>)
    html = re.sub(r'<div[^>]*class="[^"]*promotedlink[^"]*"[^>]*>.*?</div>', "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove tracking ad links (<a href="...alb.reddit.com...">...</a>)
    html = re.sub(r'<a[^>]*href="[^"]*alb\.reddit\.com[^"]*"[^>]*>.*?</a>', "", html, flags=re.DOTALL | re.IGNORECASE)

    # Convert <faceplate-time-ago ts="..."> or <time datetime="..."> into visible timestamp text
    def _render_ts(match: re.Match) -> str:
        ts_val = match.group(1)
        try:
            val = float(ts_val)
            if val > 1e11:  # ms
                val /= 1000.0
            dt = datetime.fromtimestamp(val, timezone.utc)
            return f" [{dt.strftime('%Y-%m-%d %H:%M UTC')}] "
        except Exception:
            return match.group(0)

    html = re.sub(r'<faceplate-time-ago[^>]*ts="(\d+)"[^>]*>.*?</faceplate-time-ago>', _render_ts, html, flags=re.DOTALL | re.IGNORECASE)
    return html


def _clean_reddit_markdown(text: str) -> str:
    """Clean up Reddit markdown: strip avatars, community icons, navigation remnants, floating numbers, duplicate links, and excessive blank lines."""
    if not text:
        return text
    # Strip community icon embeds and avatars
    text = re.sub(r'\[!\[[^\]]*\]\([^)]*(?:communityIcon|redditmedia\.com|thumbs\.redditmedia\.com)[^)]*\)\s*', '[', text, flags=re.IGNORECASE)
    text = re.sub(r'!\[[^\]]*\]\([^)]*(?:communityIcon|emoji\.redditmedia\.com|styles\.redditmedia\.com)[^)]*\)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\[!\[[^\]]*avatar\]\([^)]+\)\]\([^)]+\)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'!\[[^\]]*avatar[^\]]*\]\([^)]+\)', '', text, flags=re.IGNORECASE)

    # Strip Reddit navigation and web UI boilerplate
    boilerplate = [
        r"Skip to main content\s*",
        r"Open menu\s*",
        r"Advertise on Reddit\s*",
        r"Open chat\s*",
        r"Create post\s*",
        r"Open inbox\s*",
        r"Expand user menu\s*",
        r"Open profile menu\s*",
        r"Get your post the attention it deserves\s*",
        r"Close Repost Nudge Dialog\s*",
        r"Repost into other communities and help your post get seen by more people\.?\s*",
        r"Submit a report\s*",
        r"Report Submitted\s*",
        r"Open sort options\s*",
        r"Change post view\s*",
        r"Open navigation\s*",
        r"Go to Reddit Home\s*",
        r"\[Sign Up\]\([^\)]+\)Sign up for Reddit\s*",
        r"\[Log In\]\([^\)]+\)Log in to Reddit\s*",
        r"Open settings menu\s*",
        r"View more comments\s*",
        r"Card Compact Community highlights\s*",
        r"Top Best Hot New Top Rising Today Now Today This Week This Month This Year All Time\s*",
        r"\b\d+\s*votes\s*•\s*\d+\s*comments\b",
    ]
    for b in boilerplate:
        text = re.sub(b, '', text, flags=re.IGNORECASE)

    # Strip lone floating numbers on their own lines (leftover upvote buttons)
    text = re.sub(r'(?<=\n)\s*\d+(?:\.\d+)?(?:k|K)?\s*(?=\n|\Z)', '', text)

    # Remove duplicate consecutive links produced by card headers + titles
    text = re.sub(r'(\[[^\]]+\]\([^)]+\))\s*\n+(?:---\s*\n+)?(?:\s*\[r/[^\]]+\]\([^)]+\)\s*\n+)?\1', r'\1', text)

    lines = text.splitlines()
    header = lines[0] if lines and lines[0].startswith("#") else ""
    rest = "\n".join(lines[1:]) if header else text
    rest = re.sub(r"^(?:\s*---\s*\n)+", "", rest)
    rest = re.sub(r"\n{3,}", "\n\n", rest).strip()
    return f"{header}\n\n{rest}" if header else rest


def _to_output(
    html: str,
    fmt: str,
    readability: bool,
    main: bool,
    max_chars: int,
    raw_md: bool,
) -> tuple[str, str]:
    """Convert the final HTML to the requested output pair (content, raw_content).

    - fmt == "html": raw HTML in both (raw truncated at max_chars).
    - readability engine: the article HTML (already main-content filtered by
      Readability.js in the browser) is converted to markdown with markdownify.
    - default engine: trafilatura markdown (only_main_content) or plain text
      (full_text override)."""
    if fmt == "html":
        return html, html[:max_chars]
    if readability:
        content = _markdownify(html, heading_style="ATX")
        return content, (content if raw_md else html[:max_chars])
    content = _extract_text(html, main, max_chars)
    return content, (content if raw_md else html[:max_chars])


def _scroll_steps_for(config: ForageConfig, scroll: bool) -> int:
    """Effective scroll rounds for a render call.

    The domain override ``scroll: true`` forces at least one round even when
    the global ``browser.scroll_steps`` is 0 (the default), so lazy content
    (Reddit/YouTube comments) gets a chance to mount."""
    if not scroll:
        return 0
    return max(config.browser.scroll_steps, 1)


def normalize_reddit_url(url: str) -> str:
    """Normalize Reddit URLs (old.reddit, sh.reddit, new.reddit, direct .json endpoints)
    to canonical https://www.reddit.com/... web URLs."""
    if not url:
        return url
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if host in ("reddit.com", "www.reddit.com", "old.reddit.com", "new.reddit.com", "sh.reddit.com", "safereddit.com"):
            path = parsed.path or "/"
            if path.endswith(".json"):
                path = path[:-5]
            canonical = f"https://www.reddit.com{path}"
            if parsed.query:
                canonical += f"?{parsed.query}"
            return canonical
    except Exception:
        pass
    return url


_reddit_req_lock = asyncio.Lock()
_reddit_last_req_time = 0.0
_reddit_json_cooldown_until = 0.0


async def _throttle_reddit_request(min_interval: float = 0.75) -> None:
    """Stagger concurrent Reddit HTTP requests to prevent burst rate-limiting."""
    global _reddit_last_req_time
    async with _reddit_req_lock:
        now = time.monotonic()
        elapsed = now - _reddit_last_req_time
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        _reddit_last_req_time = time.monotonic()


def _is_reddit_json_on_cooldown() -> bool:
    """Check if Reddit .json API is currently in a rate-limit cooldown window."""
    return time.monotonic() < _reddit_json_cooldown_until


def _set_reddit_json_cooldown(cooldown_seconds: float = 30.0) -> None:
    """Trigger a cooldown window after receiving a 403 or 429 from Reddit .json."""
    global _reddit_json_cooldown_until
    _reddit_json_cooldown_until = max(_reddit_json_cooldown_until, time.monotonic() + cooldown_seconds)
    logger.info("Reddit .json API rate-limited (403/429); entering %.1fs cooldown", cooldown_seconds)


async def _try_reddit_extract(
    config: ForageConfig,
    url: str,
    timeout: int,
    extra_headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """3-tier extraction for Reddit: 1) .json API, 2) Redlib mirror, 3) None (fallback to browser)."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host not in ("reddit.com", "www.reddit.com"):
        return None
    path = parsed.path or ""
    # Support subreddit listings (/r/...), comments threads, user pages (/user/), searches (/search)
    if not (
        path.startswith("/r/")
        or path.startswith("/user/")
        or path.startswith("/u/")
        or path.startswith("/search")
        or path.startswith("/top")
        or path.startswith("/hot")
        or path.startswith("/new")
        or "/comments/" in path
    ):
        return None

    # --- Tier 1: Official Reddit .json endpoint ---
    if not _is_reddit_json_on_cooldown():
        clean_path = path.rstrip("/")
        if not clean_path.endswith(".json"):
            clean_path += ".json"
        if "/comments/" in clean_path:
            json_url = f"https://www.reddit.com{clean_path}?raw_json=1&limit=100&depth=10"
        elif "search" in clean_path:
            q_part = f"{parsed.query}&raw_json=1" if parsed.query else "raw_json=1"
            if "type=" not in q_part:
                q_part += "&type=link"
            json_url = f"https://www.reddit.com{clean_path}?{q_part}"
        elif parsed.query:
            json_url = f"https://www.reddit.com{clean_path}?{parsed.query}&raw_json=1"
        else:
            json_url = f"https://www.reddit.com{clean_path}?raw_json=1"

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.reddit.com/",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
        }
        if extra_headers:
            headers.update(extra_headers)

        try:
            await _throttle_reddit_request()
            async with httpx.AsyncClient(timeout=min(timeout, 8), headers=headers, cookies=cookies, follow_redirects=True) as client:
                resp = await client.get(json_url)
                if resp.status_code in (403, 429):
                    _set_reddit_json_cooldown(30.0)
                elif resp.status_code == 404:
                    return {
                        "title": "Reddit - Not Found",
                        "content": "The requested Reddit post, subreddit, or user does not exist (404 Not Found).",
                        "raw_content": "The requested Reddit post, subreddit, or user does not exist (404 Not Found).",
                        "method": "reddit+not_found",
                    }
                elif resp.status_code == 200 and resp.text:
                    try:
                        data = resp.json()
                        if isinstance(data, dict) and data.get("error") in (404, "404", "Not Found"):
                            return {
                                "title": "Reddit - Not Found",
                                "content": data.get("message") or "The requested Reddit community or post was not found.",
                                "raw_content": data.get("message") or "The requested Reddit community or post was not found.",
                                "method": "reddit+not_found",
                            }
                        markdown_content, title = parse_reddit_json(data)
                        return {
                            "title": title,
                            "content": markdown_content,
                            "raw_content": markdown_content,
                            "method": "reddit+json",
                        }
                    except Exception as parse_err:  # noqa: BLE001
                        logger.debug("Reddit JSON parse failed: %s", parse_err)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Reddit Tier 1 (.json) failed for %s: %s", url, exc)

    # --- Tier 2: Redlib Mirror (safereddit) ---
    # The mirror is a plain static HTML endpoint. It does NOT need the Chrome
    # navigation headers (Sec-Fetch-*) built for Tier 1's ``.json`` request;
    # those navigation headers are meaningless against a non-origin mirror and
    # only add a failure mode. Use a lean profile here instead.
    mirror_headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.reddit.com/",
    }
    clean_html_path = path.replace("/.json", "/").replace(".json", "")
    mirror_url = f"https://safereddit.com{clean_html_path}"
    if parsed.query:
        mirror_url += f"?{parsed.query}"

    try:
        await _throttle_reddit_request()
        async with httpx.AsyncClient(timeout=min(timeout, 4), headers=mirror_headers, cookies=cookies, follow_redirects=True) as client:
            mresp = await client.get(mirror_url)
            if mresp.status_code == 404:
                return {
                    "title": "Reddit - Not Found",
                    "content": "The requested Reddit post, subreddit, or user was not found.",
                    "raw_content": "The requested Reddit post, subreddit, or user was not found.",
                    "method": "reddit+not_found",
                }
            if mresp.status_code == 200 and mresp.text:
                html = mresp.text
                title = _extract_title(html)
                lower_html = html.lower()
                not_found_markers = (
                    "subreddit not found",
                    "community not found",
                    "user not found",
                    "page not found",
                    "this community does not exist",
                    "this community doesn't exist",
                )
                if any(m in lower_html for m in not_found_markers):
                    return {
                        "title": title or "Reddit - Not Found",
                        "content": "The requested Reddit post, subreddit, or user was not found.",
                        "raw_content": "The requested Reddit post, subreddit, or user was not found.",
                        "method": "reddit+not_found",
                    }
                content, raw_content = _to_output(
                    html,
                    "markdown",
                    readability=False,
                    main=False,
                    max_chars=config.extract.max_content_chars,
                    raw_md=config.extract.raw_content_markdown,
                )
                if (
                    content
                    and not looks_like_challenge(html, title)
                    and "welcome to reddit" not in title.lower()
                    and "log in to use old reddit" not in content.lower()
                    and "verifying your browser" not in title.lower()
                    and "anubis" not in html.lower()
                    and len(content) > 100
                ):
                    return {
                        "title": title or _title_from_url(url),
                        "content": content,
                        "raw_content": raw_content,
                        "method": "reddit+mirror",
                    }
    except Exception as exc:  # noqa: BLE001
        logger.debug("Reddit Tier 2 (mirror) failed for %s: %s", url, exc)

    # --- Tier 3: None (caller falls back to browser render) ---
    return None


async def extract_url(
    config: ForageConfig,
    pool: BrowserPool,
    url: str,
    *,
    position: int = 1,
    force_render: bool = False,
    wait_for: Optional[str] = None,
    output_format: str = "markdown",
    only_main_content: bool = True,
    timeout: Optional[int] = None,
    engine: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract a single URL using the hybrid strategy. Hermes-envelope entry.

    Top-level flow of ``extract_url`` (config-driven):

      1. Normalize the Reddit URL and resolve any domain override.
      2. Document path (PDF/DOCX/XLSX/PPTX/RTF) if not force-rendered.
      3. Reddit fast path: Tier 1 (official ``.json``) -> Tier 2 (Redlib mirror)
         -> Tier 3 (browser fallback).
      4. Static fetch with markdown negotiation.
      5. Hybrid browser fallback when the static result is not enough:
         SPA markers, content density, empty ``<main>``, or text below the
         configured minimum.
      6. Challenge detection -> solver retry.
      7. Convert the result with ``_to_output``.

    Domain overrides (``extract.domain_overrides``) are resolved on the
    original URL and may: rewrite the URL, force the browser, force the
    whole-page text path, set a default wait_for selector, or enable
    scrolling. Request-level parameters are absolute: when the caller passes
    ``force_render`` or ``wait_for`` explicitly, they override the domain
    override."""
    method = "static"

    original_url = url
    url = normalize_reddit_url(url)

    # Resolve the domain override on the ORIGINAL URL (before any rewrite).
    override = _find_override(url, config.extract.domain_overrides)
    if override is not None:
        logger.debug("%s -> domain override %r applied", url, override.pattern)

    # Request-level params are absolute: they win over the domain override.
    effective_force_render = force_render or bool(override and override.force_render)
    effective_wait_for = wait_for if wait_for is not None else (
        override.wait_for if override is not None else None
    )
    effective_main = only_main_content and not bool(override and override.full_text)
    effective_scroll = bool(override and override.scroll)
    # Extract engine: request-level is absolute; then the domain override;
    # then Reddit default ("readability", required for custom <shreddit-comment> components);
    # then the global default ("trafilatura").
    is_reddit = "reddit.com" in url.lower() or "safereddit.com" in url.lower()
    effective_engine = (
        engine
        or (override.engine if override is not None else None)
        or ("readability" if is_reddit else None)
        or config.extract.engine
    )
    effective_readability = effective_engine == "readability"
    effective_timeout = (
        timeout
        or (override.timeout if override is not None else None)
        or config.extract.timeout
    )
    effective_idle = (
        override.network_idle_timeout if override is not None else None
    )
    effective_challenge = (
        override.challenge_timeout if override is not None else None
    )

    rewritten = rewrite_url(url, override)
    if rewritten != url:
        logger.info("%s -> URL rewritten to %s", url, rewritten)
        url = rewritten

    override_headers = override.headers if override else {}
    override_cookies = override.cookies if override else {}

    # Documents (pdf/docx/xlsx/pptx/rtf) are extracted from raw bytes -
    # never through the browser (Chromium renders PDFs poorly). Falls back
    # to the hybrid flow when the URL is not actually a document.
    if not effective_force_render:
        doc_result = await _extract_document(config, url, effective_timeout)
        if doc_result is not None:
            doc_result["url"] = original_url
            return doc_result

    # Reddit fast path: Tier 1 (.json) -> Tier 2 (Redlib mirror) -> Tier 3 (browser fallback).
    # Always attempt the lightweight Reddit pipeline before launching a heavy browser session.
    reddit_result = await _try_reddit_extract(
        config,
        url,
        effective_timeout,
        extra_headers=override_headers,
        cookies=override_cookies,
    )
    if reddit_result is not None:
        reddit_result["url"] = original_url
        reddit_result["citation"] = f"[Source: {reddit_result['title']}]({original_url})"
        return reddit_result

    # The extract engine (trafilatura vs readability) applies ONLY to browser
    # renders. It must NOT force the browser: pages that extract fine with
    # plain HTTP + trafilatura stay on the static path. The browser is only
    # used when an override/request demands it or the hybrid check needs it.
    want_browser = effective_force_render or bool(effective_wait_for)

    html: Optional[Union[str, Dict[str, str]]] = None
    status = 0
    content_type = ""
    native_markdown = False
    readability_title: Optional[str] = None
    readability_rendered = False

    if not want_browser:
        html, status, _, content_type = await fetch_static(
            config,
            url,
            extra_headers=override_headers,
            cookies=override_cookies,
        )
        if status == 0:
            # network error or robots-blocked; browser rarely helps, fail clean
            return {"url": original_url, "error": "Failed to fetch URL (network error or robots.txt)"}
        if status in (401, 403, 429):
            logger.info("%s -> HTTP %d, falling back to browser", url, status)
            want_browser = True
        elif html is not None and looks_like_challenge(html, _extract_title(html)):
            # Some anti-bot setups (e.g. Cloudflare managed challenge) answer
            # 200 with a challenge page. Give the browser a shot before failing.
            logger.info("%s -> static anti-bot challenge page, falling back to browser", url)
            want_browser = True
        elif content_type == "text/markdown":
            # The server implements markdown negotiation and served native
            # markdown. Use it directly, skipping trafilatura conversion.
            native_markdown = True
            logger.info("%s -> native markdown served (text/markdown)", url)

    if native_markdown:
        # The body is already markdown, not HTML: trafilatura, title
        # extraction and the challenge check (all HTML-based) do not apply.
        # Truncate to the configured cap and mirror raw_content per the
        # raw_content_markdown contract (Hermes reads raw_content first).
        md = html if isinstance(html, str) else ""
        content = md[: config.extract.max_content_chars]
        raw_content = content if config.extract.raw_content_markdown else ""
        if not content:
            return {"url": original_url, "error": "No content extracted"}
        from .searxng import extract_domain, format_citation
        domain = extract_domain(original_url)
        title = _markdown_title(md, original_url)
        # Native markdown has no <title> to scrape, so use a source citation
        # style ("[Source: <name>](url)"). If the title is empty, fall back to
        # the domain so the citation is never an empty link.
        label = title if title else domain
        citation = f"[Source: {label}]({original_url})"
        if url != original_url:
            citation += f" ({url})"
        result: Dict[str, Any] = {
            "position": position,
            "domain": domain,
            "url": original_url,
            "title": title,
            "content": content,
            "raw_content": raw_content,
            "method": "markdown",
            "citation": citation,
            "extracted_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        }
        if url != original_url:
            result["rewritten_url"] = url
        return result

    if want_browser:
        try:
            html = await pool.render(
                url,
                wait_for=effective_wait_for,
                timeout=effective_timeout,
                scroll_steps=_scroll_steps_for(config, effective_scroll),
                network_idle_timeout=effective_idle,
                challenge_timeout=effective_challenge,
                readability=effective_readability,
                extra_headers=override_headers,
                cookies=override_cookies,
            )
            method = "browser"
            if effective_readability and isinstance(html, dict):
                readability_rendered = True
                readability_title = html.get("title") or ""
                html = html.get("content") or ""
            else:
                readability_title = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Browser render failed for %s: %s", url, exc)
            if html is None:
                return {"url": original_url, "error": f"Browser render failed: {exc}"}
            # static html (if any) is still better than nothing

    if html is None:
        return {"url": original_url, "error": "No content extracted"}

    # Hybrid analysis on the static HTML (only relevant when not forced to browser)
    if not want_browser:
        text = _extract_text(html, effective_main, config.extract.max_content_chars)
        needs_render, render_reason = needs_browser_render(
            html, text, config.extract.min_content_chars
        )
        if needs_render:
            logger.info("%s -> %s, falling back to browser", url, render_reason)
            try:
                html = await pool.render(
                    url,
                    wait_for=effective_wait_for,
                    timeout=effective_timeout,
                    scroll_steps=_scroll_steps_for(config, effective_scroll),
                    network_idle_timeout=effective_idle,
                    challenge_timeout=effective_challenge,
                    readability=effective_readability,
                    extra_headers=override_headers,
                    cookies=override_cookies,
                )
                method = "browser"
                if effective_readability and isinstance(html, dict):
                    readability_rendered = True
                    readability_title = html.get("title") or ""
                    html = html.get("content") or ""
            except Exception as exc:  # noqa: BLE001
                logger.warning("Browser render failed for %s: %s", url, exc)

    if isinstance(html, str) and ("reddit.com" in original_url.lower() or "safereddit.com" in original_url.lower()):
        html = _strip_reddit_ads_from_html(html)

    content, raw_content = _to_output(
        html,
        output_format,
        readability_rendered,
        effective_main,
        config.extract.max_content_chars,
        config.extract.raw_content_markdown,
    )

    if not content:
        return {"url": original_url, "error": "No content extracted"}

    title = readability_title or _extract_title(html)
    if looks_like_challenge(html, title):
        if config.browser.fallback_solver:
            # Last-resort retry: the scrapling built-in solver handles
            # challenges (including interactive ones) that the page_action
            # poll cannot. Only pays the ~5s/page solver cost on failure.
            logger.info("%s -> anti-bot challenge, retrying with scrapling solver", url)
            try:
                solver_html = await pool.render_with_solver(
                    url,
                    wait_for=effective_wait_for,
                    timeout=effective_timeout,
                    scroll_steps=_scroll_steps_for(config, effective_scroll),
                    network_idle_timeout=effective_idle,
                    extra_headers=override_headers,
                    cookies=override_cookies,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Solver retry failed for %s: %s", url, exc)
                solver_html = None
            if solver_html:
                html = _strip_reddit_ads_from_html(solver_html) if ("reddit.com" in original_url.lower() or "safereddit.com" in original_url.lower()) else solver_html
                method = "browser+solver"
                title = _extract_title(html)
                content, raw_content = _to_output(
                    html,
                    output_format,
                    readability_rendered,
                    effective_main,
                    config.extract.max_content_chars,
                    config.extract.raw_content_markdown,
                )
                if not content:
                    return {"url": original_url, "error": "No content extracted"}
        if looks_like_challenge(html, title):
            logger.warning("%s -> anti-bot challenge page detected%s", url, " (after solver retry)" if method == "browser+solver" else "")
            return {
                "url": original_url,
                "title": title,
                "method": method,
                "error": "Blocked by anti-bot challenge (Cloudflare or similar)",
            }

    if readability_rendered and method == "browser":
        method = "browser+readability"

    if "reddit.com" in original_url.lower() or "safereddit.com" in original_url.lower():
        if title and not content.startswith("# "):
            content = f"# {title}\n\n" + content
            raw_content = f"# {title}\n\n" + raw_content
        content = _clean_reddit_markdown(content)
        raw_content = _clean_reddit_markdown(raw_content)

    from .searxng import extract_domain, get_favicon_url, format_citation

    domain = extract_domain(original_url)
    include_fav = config.extract.include_favicon if getattr(config.extract, "include_favicon", None) is not None else getattr(config.tools, "include_favicon", False)
    favicon = get_favicon_url(domain) if include_fav else None
    extracted_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    cit_style = getattr(config.extract, "citation_style", "site_name")
    citation_markdown = format_citation(title, domain, original_url, position=position, style=cit_style)

    result: Dict[str, Any] = {
        "position": position,
        "domain": domain,
        "url": original_url,
        "title": title,
        "content": content,
        "citation": citation_markdown,
        "method": method,
        "extracted_at": extracted_at,
    }
    if include_fav and favicon:
        result["favicon"] = favicon
    if raw_content and raw_content != content:
        result["raw_content"] = raw_content
    if url != original_url:
        result["rewritten_url"] = url
    return result
