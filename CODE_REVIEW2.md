# Forage — Code Review (Part 6)

## Scope

This review covers the `forage/` codebase end-to-end: `app/` (FastAPI server,
searxng client, extract/browser/reddit/document parsers), `scripts/`,
`hermes/`, `examples/`, tests, docs (`README.md`, `AGENTS.md`), and config.

## TL;DR

- The code is **well-structured and mostly correct**, with clear comments and
  good error handling.
- The biggest real bug — the native `text/markdown` branch returning an empty
  `title` and **no `citation`** — is now **FIXED** (see 2.1).
- Remaining concerns are mostly organizational / defensive, none blocking.

## 1. Overall Architecture

- `app/api.py` — FastAPI routes (MCP tools, REST `/mcp`, `/tools`, `/config`,
  `/chat`, `/agents`, `/automations`). Thin wrappers over the modules.
- `app/searxng.py` — SearXNG client; normalizes results and formats citations.
- `app/extract.py` — URL extraction (browser + native markdown), envelope,
  Reddit + document parsing.
- `app/browser.py` — stealth browser pool (scrapling / Playwright / obscura).
- `app/documents.py` — PDF/office parsing + Reddit JSON parsing.
- `app/prompts.py` — tool descriptions / prompt templates.
- `app/main.py` — FastAPI app, `/extract`, `/reddit`, `/search` endpoints.

## 2. Code Review

### 2.1 `extract.py` — native-markdown branch returns `title: ""` and no `citation`

The branch handling a server that answers `Accept: text/markdown` returned a
result envelope missing `title` (always `""`) and never computing `citation`.
Consumers reading `result["citation"]` on a native-markdown response would hit
a `KeyError`; the envelope was inconsistent with the normal HTML path.

**RESOLVED** (in `app/extract.py`):

1. New `_markdown_title(markdown, url)` helper (lines 298–312) derives a title
   from the first `#` heading, else the URL slug (title-cased), else the host.
2. The native-markdown branch now imports `extract_domain`/`format_citation`
   locally (so it does not require `app.searxng` to be imported at module load),
   sets a real `title`, and builds a `citation` via the existing `bracket_title`
   style (`[Source: <name>](url)`), falling back to the domain label when the
   title is empty so the citation is never an empty link.

```python
label = title if title else domain
citation = f"[Source: {label}]({original_url})"
if url != original_url:
    citation += f" ({url})"
```

The native-markdown envelope is now a **superset of the normal result keys**
(position/domain/url/title/content/citation/method/extracted_at), so consumers
read it identically.

**Covered by** `tests/test_extract_markdown.py` (10 tests, all passing). The test
installs fake `trafilatura`/`markdownify` modules in `sys.modules` so the module
imports without the heavy deps, and patches `_extract_document` /
`_try_reddit_extract` / `fetch_static` / `looks_like_challenge` to exercise the
native-markdown branch in isolation.

### 2.2 `documents.py` — mixed document + Reddit parsing

`parse_reddit_json` lives in `documents.py` alongside PDF/office extraction.
This is organizational: a dedicated `reddit.py` module would be cleaner, but it
works.

Edge cases (all covered by `tests/test_reddit.py`, class
`TestRedditJsonEdgeCases`):

- **Listing, empty children** → title "Reddit Posts", body "No posts found." (no crash, no post cards).
- **Listing, multiple subreddits** → generic title "Reddit Search / Listing Results".
- **Listing, single subreddit** → "{subreddit} - Reddit Posts".
- **Thread, post with no `data.children`** → defaults to an empty post, so the title falls back to "Reddit Post" (not the post title).
- **Thread, `raw_json[1]` is a list (not a dict)** → comments dropped silently, no crash.
- **Thread, `raw_json[1]` is a dict with no `children`** → no comments, no crash.
- **Thread, empty/`www.reddit.com` url** → the "Link" line is skipped.
- `[]` (empty) → `ValueError`.

### 2.3 `browser.py` — stealth browser pool

- Wraps scrapling (`_scrapling_render`) / Playwright (`render`) / obscura
  (`_cdp_render`) behind a shared API with per-request stealth, cookies,
  challenge handling, and timeouts.
- Powerful; should be documented as opt-in and reviewed for the privacy/footprint
  it leaves (real browser session with cookies/local storage).

### 2.4 Minor issues

- Legacy `engines` aliasing in `searxng.py` (e.g. `ddg`, `wiki`,
  `youtube_search`) is redundant but harmless.
- Some redundant `.json` handling in `searxng.py`.
- A few `# TODO`/`FIXME` comments worth revisiting before release.

## 3. Documentation

- **README.md** — clear usage/quick-start. Could add a note on the browser
  engines' privacy footprint.
- **AGENTS.md** — comprehensive dev guide; claims checked against the code.
- **CODE_REVIEW2.md** — Part 5 analysis of `parse_reddit_json` edge cases, now
  aligned with the actual behavior.

## 4. Tests

- `tests/test_extract_markdown.py` — new, 10 tests, all passing.
- `tests/test_reddit.py` — new `TestRedditJsonEdgeCases` (5 tests) covering the
  Reddit parsing edge cases above; all 11 tests pass.
- `tests/` has some coverage gaps (e.g. no direct tests for `browser.py` render
  paths), but the core parsing/extract logic is covered.

## 5. Example Files

- `examples/` — runnable snippets matching the documented API. Good.

## 6. Config

- `config.py` — reasonable defaults; `extract.browser.enabled` controls the
  browser path.

## Summary

The code is **well-structured and mostly correct**, with clear comments and good
error handling.

- **Native-markdown branch** — now **FIXED** (see 2.1). The envelope is
  consistent with the normal path.
- **`documents.py`** — still mixes document parsing with Reddit JSON parsing;
  consider a dedicated `reddit.py` module.
- **Anti-bot footprint** in `browser.py` is powerful but should be documented as
  opt-in.
- Minor: legacy `engines` aliasing, redundant `.json` handling.
