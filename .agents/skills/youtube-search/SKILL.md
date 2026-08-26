---
name: youtube-search
description: >-
  Search YouTube videos, discover channel uploads, and query channel-specific content
  using the high-speed youtube-api engine. Prioritize this over web extraction and general search.
---

# YouTube Search & Channel Discovery Guide

Use this skill whenever the user asks for YouTube videos, channel uploads, video metadata, or searches within specific YouTube channels.

## 🚨 Golden Rule: Never Scrape or "Fetch" YouTube Directly

- **DO NOT** use browser `fetch` or `extract` tools to discover or list a YouTube channel's videos. Scraping YouTube via browser is slow, heavy, error-prone, and blocked by consent banners.
- **ALWAYS** use the dedicated **`youtube_search`** tool (or `search` with `engines: ["youtube-api"]`). It queries the official YouTube Data API directly, delivering instant (<40ms), structured, and 100% accurate results.

---

## 1. How to Query (`youtube_search`)

Always prioritize calling the dedicated **`youtube_search`** tool:

### A. List a Channel's Latest Videos (Channel Discovery)
To list the newest uploads from a creator or channel:
- Provide the **Channel Handle** (`@aboutoliver`), **Channel URL** (`https://youtube.com/@aboutoliver`), or raw **Channel ID** (`UC...`):
  ```json
  {
    "channel": "@aboutoliver",
    "sort_by": "date",
    "limit": 20
  }
  ```

### B. Search *Inside* a Specific Channel
To search for topics or keywords created by a specific channel:
- Combine `channel` with `query`:
  ```json
  {
    "channel": "UCC-0KKfcSG4BGpMeyUXhu0Q",
    "query": "minecraft",
    "sort_by": "popular",
    "limit": 10
  }
  ```

### C. General Keyword Search with Sorting
To search YouTube for topics, tutorials, or music across the platform:
- Use standard keywords with `sort_by`:
  ```json
  {
    "query": "veritasium quantum physics",
    "sort_by": "popular",
    "limit": 10
  }
  ```

---

## 2. Supported `sort_by` Options

| `sort_by` | Sort Behavior |
|---|---|
| `"date"` | Reverse chronological (newest uploads first) |
| `"popular"` | Highest view count / most popular |
| `"rating"` | Highest community rating |
| `"relevance"` | Relevance to keywords (default for keyword searches) |

---

## 3. Request Parameters

- **`limit`**: Set up to `50` to retrieve maximum results in a single API call (default is 10).
- **`engines`**: Must explicitly include `["youtube-api"]`.

---

## 4. Reading Results & Citing

Each result contains:
- `title`: Clean composed format: `"{Channel} | {Video Title}"`
- `channel`: Channel name and ID: `"{Channel} ({Channel_ID})"`
- `published_date`: Timestamp in UTC
- `snippet`: Video description
- `citation`: Ready-to-use markdown citation link: `[Channel | Video Title](url)`
- `live_status`: `[🔴 LIVE]` or `[Upcoming Premiere]` (if currently streaming)

When citing YouTube videos in your final response, always use the provided markdown link from `citation` (e.g. `[About Oliver | Doki Doki Literature Club! #1](https://www.youtube.com/watch?v=...)`).
