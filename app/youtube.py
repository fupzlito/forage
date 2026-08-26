# SPDX-License-Identifier: GPL-3.0-or-later
"""
Native YouTube Data API & Search Module for Forage.

Supports:
- Direct Google YouTube Data API v3 integration with ultra-low latency (<40ms)
- Automatic @handle resolution (e.g. @aboutoliver -> UCC-0KKfcSG4BGpMeyUXhu0Q)
- Channel uploads discovery & channel-scoped keyword searches
- Sorting by date, popular/views, rating, and relevance
- Seamless fallback to SearXNG youtube-api engine when no API key is configured
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dateutil import parser as date_parser

from app.config import ForageConfig
from app.searxng import format_citation, search_searxng

logger = logging.getLogger("forage.youtube")

_CHANNEL_ID_PATTERN = re.compile(r"(?:youtube\.com/channel/|channel_id=|^|\s)(UC[a-zA-Z0-9_-]{22})(?:\s|$)", re.IGNORECASE)
_HANDLE_PATTERN = re.compile(r"(?:youtube\.com/@|@)([a-zA-Z0-9_.-]{3,30})", re.IGNORECASE)

_YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
_BASE_WATCH_URL = "https://www.youtube.com/watch?v="
_BASE_EMBED_URL = "https://www.youtube-nocookie.com/embed/"


def extract_channel_id_or_handle(channel_input: str) -> Tuple[Optional[str], Optional[str]]:
    """Extract (channel_id, handle) from a user/model-supplied string or URL.

    Returns:
        (channel_id, None) if a 24-character UC... ID is found.
        (None, handle) if a handle (@creator) or handle URL is found.
        (None, None) if neither is found.
    """
    if not channel_input:
        return None, None

    s = channel_input.strip()

    # 1. Check for Channel ID (UC...)
    cid_match = _CHANNEL_ID_PATTERN.search(s)
    if cid_match:
        return cid_match.group(1), None

    # 2. Check for @handle in URL or string (e.g. @aboutoliver or https://youtube.com/@aboutoliver)
    handle_match = _HANDLE_PATTERN.search(s)
    if handle_match:
        return None, handle_match.group(1).lstrip("@")

    # 3. Check for bare handle identifier
    if re.match(r"^[a-zA-Z0-9_.-]{3,30}$", s):
        return None, s

    return None, None


def resolve_handle_to_channel_id(
    handle: str,
    api_key: str,
    client: Optional[httpx.Client] = None,
    timeout: float = 5.0,
) -> Optional[str]:
    """Resolve a YouTube @handle (e.g. 'aboutoliver') to a channel ID ('UC...').

    Calls YouTube Data API /channels?part=id&forHandle=handle (or forUsername fallback).
    """
    clean_handle = handle.lstrip("@").strip()
    if not clean_handle:
        return None

    # Try forHandle first
    url = f"{_YOUTUBE_API_BASE}/channels"
    params = {
        "part": "id",
        "forHandle": clean_handle,
        "key": api_key,
    }

    own_client = False
    if client is None:
        client = httpx.Client(timeout=timeout)
        own_client = True

    try:
        resp = client.get(url, params=params)
        if resp.status_code == 200:
            data = resp.json()
            items = data.get("items", [])
            if items and isinstance(items[0], dict) and "id" in items[0]:
                return items[0]["id"]

        # Fallback to forUsername
        params_user = {
            "part": "id",
            "forUsername": clean_handle,
            "key": api_key,
        }
        resp_user = client.get(url, params=params_user)
        if resp_user.status_code == 200:
            data_user = resp_user.json()
            items_user = data_user.get("items", [])
            if items_user and isinstance(items_user[0], dict) and "id" in items_user[0]:
                return items_user[0]["id"]
    except Exception as exc:
        logger.warning("Failed to resolve YouTube handle @%s: %s", clean_handle, exc)
    finally:
        if own_client:
            client.close()

    return None


def search_youtube_direct(
    api_key: str,
    query: Optional[str] = None,
    channel: Optional[str] = None,
    sort_by: Optional[str] = None,
    limit: int = 20,
    time_range: Optional[str] = None,
    language: Optional[str] = None,
    client: Optional[httpx.Client] = None,
    timeout: float = 10.0,
    citation_style: str = "site_name",
) -> Dict[str, Any]:
    """Execute direct YouTube Data API search and return normalized video results."""
    clean_query = (query or "").strip()
    clean_channel = (channel or "").strip()

    cid = None
    if clean_channel:
        extracted_cid, extracted_handle = extract_channel_id_or_handle(clean_channel)
        if extracted_cid:
            cid = extracted_cid
        elif extracted_handle:
            cid = resolve_handle_to_channel_id(extracted_handle, api_key=api_key, client=client, timeout=timeout)

    api_params: Dict[str, Any] = {
        "part": "snippet",
        "type": "video",
        "maxResults": max(1, min(limit, 50)),
        "key": api_key,
    }

    if cid:
        api_params["channelId"] = cid

    if clean_query:
        api_params["q"] = clean_query

    # Sort order
    sort_key = (sort_by or "").lower().strip()
    if sort_key in ("date", "newest"):
        api_params["order"] = "date"
    elif sort_key in ("views", "popular", "viewcount"):
        api_params["order"] = "viewCount"
    elif sort_key == "rating":
        api_params["order"] = "rating"
    elif sort_key == "relevance":
        api_params["order"] = "relevance"
    elif cid and not clean_query:
        # Default channel video discovery to newest uploads
        api_params["order"] = "date"

    # Time range
    if time_range:
        tr = time_range.lower().strip()
        now = datetime.now(timezone.utc)
        if tr == "day":
            api_params["publishedAfter"] = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif tr == "week":
            api_params["publishedAfter"] = (now - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif tr == "month":
            api_params["publishedAfter"] = (now - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif tr == "year":
            api_params["publishedAfter"] = (now - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")

    if language and language.lower() != "all":
        api_params["relevanceLanguage"] = language.split("-")[0]

    own_client = False
    if client is None:
        client = httpx.Client(timeout=timeout)
        own_client = True

    try:
        resp = client.get(f"{_YOUTUBE_API_BASE}/search", params=api_params)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("YouTube API HTTP error: %s", exc)
        return {
            "success": False,
            "error": f"YouTube API returned HTTP {exc.response.status_code}: {exc.response.text}",
            "results": [],
        }
    except Exception as exc:
        logger.warning("YouTube API request error: %s", exc)
        return {
            "success": False,
            "error": f"YouTube API request failed: {exc}",
            "results": [],
        }
    finally:
        if own_client:
            client.close()

    raw_items = data.get("items", [])
    results: List[Dict[str, Any]] = []
    searched_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    for idx, item in enumerate(raw_items):
        item_id = item.get("id", {})
        vid = item_id.get("videoId")
        if not vid:
            continue

        snippet = item.get("snippet", {})
        video_title = snippet.get("title", "").strip()
        channel_title = snippet.get("channelTitle", "").strip()
        channel_id = snippet.get("channelId", "").strip()
        description = snippet.get("description", "").strip()
        pub_at = snippet.get("publishedAt", "")
        live_status = snippet.get("liveBroadcastContent", "none")

        try:
            pub_date = date_parser.parse(pub_at).strftime("%Y-%m-%d %H:%M:%S UTC")
        except Exception:
            pub_date = pub_at

        # Composed Title: "Channel | Video Title"
        if channel_title and not video_title.startswith(f"{channel_title} |"):
            composed_title = f"{channel_title} | {video_title}"
        else:
            composed_title = video_title

        # Channel label: "Channel (Channel_ID)"
        channel_label = f"{channel_title} ({channel_id})" if channel_id else channel_title

        url = f"{_BASE_WATCH_URL}{vid}"
        cit_link = format_citation(composed_title, "youtube.com", url, position=idx + 1, style=citation_style, is_video=True)

        res_item: Dict[str, Any] = {
            "position": idx + 1,
            "domain": "youtube.com",
            "url": url,
            "title": composed_title,
            "channel": channel_label,
            "engine": "youtube-api",
            "published_date": pub_date,
            "snippet": description,
            "citation": cit_link,
            "iframe_src": f"{_BASE_EMBED_URL}{vid}",
        }

        thumbs = snippet.get("thumbnails", {})
        if "high" in thumbs and "url" in thumbs["high"]:
            res_item["thumbnail"] = thumbs["high"]["url"]
        elif "medium" in thumbs and "url" in thumbs["medium"]:
            res_item["thumbnail"] = thumbs["medium"]["url"]
        elif "default" in thumbs and "url" in thumbs["default"]:
            res_item["thumbnail"] = thumbs["default"]["url"]

        if live_status == "live":
            res_item["live_status"] = "[🔴 LIVE]"
        elif live_status == "upcoming":
            res_item["live_status"] = "[Upcoming Premiere]"

        results.append(res_item)

    warning = None
    if not results:
        warning = f"No YouTube videos found matching query '{clean_query or clean_channel}'."

    return {
        "success": True,
        "searched_at": searched_at,
        "requested_engines": "youtube-api",
        "returned_engines": "youtube-api" if results else "",
        "unresponsive_engines": None,
        "results": results,
        "warning": warning,
    }


def search_youtube(
    config: ForageConfig,
    query: Optional[str] = None,
    channel: Optional[str] = None,
    sort_by: Optional[str] = None,
    limit: Optional[int] = None,
    time_range: Optional[str] = None,
    language: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute YouTube search via direct YouTube Data API (if key configured) or SearXNG proxy fallback."""
    effective_limit = max(1, min(limit or config.youtube.default_limit, config.youtube.max_limit))
    cit_style = getattr(config.search, "citation_style", "site_name")

    # Path 1: Direct YouTube API
    if config.youtube.api_key:
        return search_youtube_direct(
            api_key=config.youtube.api_key,
            query=query,
            channel=channel,
            sort_by=sort_by,
            limit=effective_limit,
            time_range=time_range,
            language=language or config.search.default_lang,
            citation_style=cit_style,
        )

    # Path 2: Fallback to SearXNG youtube-api proxy
    query_parts: List[str] = []
    clean_channel = (channel or "").strip()
    clean_query = (query or "").strip()

    if clean_channel:
        query_parts.append(clean_channel)
    if clean_query:
        query_parts.append(clean_query)
    if sort_by:
        query_parts.append(f"sort:{sort_by.lower().strip()}")

    constructed_query = " ".join(query_parts).strip()
    if not constructed_query:
        constructed_query = "youtube"

    return search_searxng(
        config=config,
        query=constructed_query,
        limit=effective_limit,
        language=language or config.search.default_lang,
        engines=["youtube-api"],
    )
