"""Prompt formatting and template rendering for Forage."""

from __future__ import annotations

import re
from typing import Any, Dict

DEFAULT_CITATION_GUIDELINES = (
    "CITATION RULES: Always link-cite sources inline next to claims, quotes, articles, repositories, or posts. "
    "Cite as markdown links using site names, short titles, usernames, or repository names as link text: "
    "'per [CNBC](url)', '[Reddit](url)', 'the [docs](url)', or '[org/repo](url)'. "
    "Results provide a ready-to-use 'citation' markdown link. "
    "Never put prepositions inside brackets: '[per CNBC](url)' is WRONG. "
    "Place periods before citation links: 'Shares fell 4%. [CNBC](url)'. "
    "List any remaining uncited sources under a 'Sources' heading at the bottom. "
    "Never duplicate a source that was already cited inline."
)

DEFAULT_SEARCH_TOOL_DESCRIPTION = (
    "Search the web via SearXNG to find relevant links and sources. Today is {now_date} (year {year}). "
    "Do not add dates or years to queries unless time-specific or directly relevant. "
    "Prioritize relevant sources based on snippet and domain before fetching full pages. "
    "To search Reddit or subreddits, use {extract_name} directly on Reddit search URLs (e.g. 'https://www.reddit.com/r/all/search/?q=...'). "
    "{citation_guidelines}"
)

DEFAULT_EXTRACT_TOOL_DESCRIPTION = (
    "Fetch and extract clean markdown content from web URLs. Supports rich forum threads (Reddit), articles, documentation, e-commerce, and direct Reddit search URLs (e.g. 'https://www.reddit.com/r/all/search/?q=QUERY'). "
    "{max_chars_requirement}: set max_chars per URL to budget context. Today is {now_date} (year {year}). "
    "{citation_guidelines}"
)

DEFAULT_SEARCH_PARAMS = {
    "query": "Search query terms. Keep queries focused on essential keywords. Do not add date/year unless directly relevant.",
    "limit": "Number of search results to return (1 to 50, default {default_limit}). Set higher (e.g. 10-15) for broad topics instead of making multiple separate search calls.",
    "engines": "Optional list of specific engines to query. Default engines: [{default_engines}]. Available: [{available_engines}]. Do NOT specify engines unless targeting a specific engine.",
    "language": "Optional language code for search results (e.g. 'en-US', 'pt-BR', 'es', 'de').",
}

DEFAULT_EXTRACT_PARAMS = {
    "urls": "List of HTTP/HTTPS URLs to fetch and extract content from (1 to 20 URLs). Supports direct Reddit search and subreddit feed URLs.",
    "force_render": "Force full headless browser rendering (Chromium) for JavaScript SPAs or dynamic sites.",
    "only_main_content": "If true (default), strips headers, footers, ads, and navigation. Set to false for full page text including comments and sidebars.",
    "wait_for": "Optional CSS selector or delay in seconds to wait for before extracting (browser mode only).",
    "engine": "Extraction engine: 'trafilatura' (default) or 'readability' (Mozilla Readability.js, better for e-commerce and forums).",
    "timeout": "Maximum extraction timeout per URL in seconds (1 to 120).",
    "max_chars": "{max_chars_requirement} maximum characters to return (applied per URL) (500 to {max_content_chars}). Truncates long pages to save context.",
    "formats": "Desired output format: ['markdown'] (default) or ['html'].",
}


def render_prompt(template: str, context: Dict[str, Any]) -> str:
    """Render a prompt template replacing `{var}` placeholders with context values.

    Handles missing keys gracefully and does not fail on extra JSON/code braces.
    """
    if not template:
        return ""

    def _replace(match: re.Match) -> str:
        key = match.group(1).strip()
        val = context.get(key)
        if val is not None:
            return str(val)
        return match.group(0)

    # Replace {key} where key is a valid identifier
    rendered = re.sub(r"\{([a-zA-Z0-9_]+)\}", _replace, template)
    return rendered.strip()
