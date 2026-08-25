# Forage Configuration Reference

All behavior is configured in `config.yaml`, bind-mounted read-only into the container at `/etc/forage/config.yaml`. Secrets **never** go in the YAML: use environment variables (see the table at the end).

**Reload**: after changing `config.yaml`, run `docker compose restart`. There is no hot reload by design: the config decides which processes (e.g. the browser pool) start, so a restart is the safe way to apply changes. Downtime is 1-2 seconds.

Resolution order: `built-in defaults → config.yaml → environment variables`.

---

## `server`

| Key | Default | Description |
|---|---|---|
| `host` | `0.0.0.0` | Bind address inside the container. Bridge networking requires `0.0.0.0` (docker-proxy routes to it); the real exposure is controlled by the compose `ports:` mapping. |
| `port` | `3672` | HTTP port (T9 of "FORA"). The app reads the config before starting uvicorn, so this is honored without rebuilding. |
| `workers` | `2` | uvicorn worker processes. |
| `log_level` | `info` | `debug` \| `info` \| `warning` \| `error` |

## `cache`

In-memory LRU only (lost on restart, by design). The master switch `enabled` turns everything off; per-section toggles can only disable further.

| Key | Default | Description |
|---|---|---|
| `enabled` | `true` | Master switch. `false` disables all caching regardless of section toggles. |
| `max_entries` | `500` | Global LRU cap across both caches. |
| `search.enabled` | `true` | Cache search results. |
| `search.ttl` | `300` | Search TTL in seconds (5 min). Keeps repeated queries from hammering SearXNG's engines (anti-bot protection). |
| `extract.enabled` | `false` | Extract caching off by default: extraction is always fresh (Firecrawl behavior). A short TTL (e.g. `120`) gives fast repeat extraction with bounded staleness. Stores full untruncated content in cache: when an LLM re-fetches a URL with higher `max_chars`, Forage returns an instant cache hit (<1ms) sliced to the new character limit. |
| `extract.ttl` | `3600` | Extract TTL in seconds (1 h). Pre-defined so enabling the toggle is enough. |

Bypass per request with the `Cache-Control: no-cache` header; the response header `X-Forage-Cache` reports `hit|miss|bypass|disabled`.

## `tools`

| Key | Default | Description |
|---|---|---|
| `search_name` | `web_search` | Custom tool call name presented in OpenAPI schemas and MCP tools list for web search (e.g. `web_search`, `search_web`). |
| `extract_name` | `web_extract` | Custom tool call name presented in OpenAPI schemas and MCP tools list for web extraction (e.g. `web_extract`, `fetch_page`). |
| `include_favicon` | `false` | Global toggle: if true, adds favicon URLs to search/extract results and formatted text blocks for models. Can also be overridden specifically under `search` or `extract`. |

## `search`

| Key | Default | Description |
|---|---|---|
| `searxng_url` | `http://searxng:8080` | Base URL of the SearXNG instance. On Docker, use the service name on the shared network (see docs/SEARXNG.md). |
| `default_lang` | `en-US` | Language passed to SearXNG. |
| `engines` | `[google, qwant, brave, bing, duckduckgo, startpage, reddit]` | Default engine filter sent to SearXNG. |
| `available_engines` | `[google, qwant, qwant news, brave, bing, startpage, duckduckgo, reddit, wikipedia, youtube, github, searxng, yahoo, wikidata]` | List of enabled search engines on your SearXNG instance. Used for validation, model guidance, and engine alias mapping. |
| `timeout` | `15` | Timeout in seconds per search request. |
| `default_limit` | `10` | Default number of search results returned when limit is omitted in requests. |
| `max_limit` | `50` | Maximum allowed search limit per query. |
| `max_snippet_chars` | `350` | Max character cap per search result snippet. |
| `max_total_snippet_chars` | `3000` | Total character cap across all snippets per response. |
| `citation_style` | `site_name` | Citation link style format: `site_name` (`[Site Name](url)`), `site_name_brackets` (`[[Site Name]](url)`), `academic` (`[[1]](url)` - numbered reference), `site_name_bold` (`[**Site Name**](url)`), `site_name_italic` (`[*Site Name*](url)`), `bracket_domain` (`[domain.com](url)`), `bracket_title` (`[Source: Title](url)`). |
| `include_favicon` | `null` | Optional override for `tools.include_favicon` specifically for search responses. |

## `extract`

| Key | Default | Description |
|---|---|---|
| `timeout` | `30` | Total seconds budget per URL (applies to static fetch and browser render). |
| `max_content_chars` | `100000` | Cap on extracted content size. |
| `only_main_content` | `true` | Strip navigation/ads/footer (trafilatura main-content extraction). |
| `engine` | `trafilatura` | Extract engine: `trafilatura` (default) or `readability`. `readability` runs Mozilla Readability.js inside the browser page (`page.evaluate`, no Node runtime) and converts the article with markdownify. Use it for product/forum pages where trafilatura's main-content heuristic drops the buybox or comments (Amazon). Can also be set per request or per domain override. |
| `user_agent` | `ForageBot/0.1 (+https://github.com/aldemaroc/forage)` | User-Agent for the **static** fetch (httpx). |
| `browser_user_agent` | `null` (commented) | User-Agent for the **browser** (Playwright). When unset, a real Chrome desktop UA is used (never a bot UA; it would be a giveaway). |
| `respect_robots` | `false` | Whether to honor `robots.txt`. Default is **false** (do not respect). |
| `force_render` | `false` | Always use the browser for extraction (skip the static attempt). Can also be set per request. |
| `wait_for` | `null` | CSS selector to wait for before extracting (browser mode). |
| `min_content_chars` | `200` | If static extraction yields less text than this, Forage falls back to the browser. |
| `raw_content_markdown` | `true` | `true`: `raw_content` mirrors the clean markdown (Firecrawl-style contract; what Hermes' `web_extract_tool` reads first). `false`: `raw_content` keeps the raw HTML. |
| `prefer_markdown` | `true` | Negotiate `Accept: text/markdown` on the static fetch. When the server implements markdown negotiation (e.g. via `.htaccess` / `Vary: Accept`) and serves `text/markdown`, Forage uses the body directly as markdown (`method: "markdown"`), skipping trafilatura conversion. Servers without negotiation ignore the Accept and return HTML, so the normal hybrid flow continues. |
| `citation_style` | `site_name` | Citation link style format for extracted content (same styles as `search.citation_style`). |
| `include_favicon` | `null` | Optional override for `tools.include_favicon` specifically for extract responses. |
| `require_max_chars` | `false` | If `true`, marks `max_chars` as a required parameter in the `web_extract` tool schema, prompting LLMs to always supply a character budget per call. |
| `domain_overrides` | `{}` | Per-pattern extraction overrides. The YAML key is a pattern (www-insensitive, case-insensitive): `x.com` matches the host or any subdomain; `.x.com` is the same with an explicit leading dot; `amazon.*` is a wildcard on a host label (fnmatch) that matches the host and any subdomain suffix; `reddit.com/r/` requires an exact host plus a path prefix. Supported keys per override: `force_render` (bool), `full_text` (bool), `engine` (str: `trafilatura` or `readability`), `wait_for` (str), `url_rewrite` (str, format `host[/path]`), `scroll` (bool), `timeout` (int 1-120), `network_idle_timeout` (int 0-60), `challenge_timeout` (int 0-120), `headers` (map of custom request headers), `cookies` (map of custom session cookies). Request-level `force_render`/`wait_for`/`timeout`/`engine` are absolute and override the domain override. |

Example: pass authenticated cookies to Reddit for high-throughput sub-second `.json` pulls, and keep the Amazon buybox via `readability`:

```yaml
extract:
  domain_overrides:
    ".amazon.*":              # all Amazon TLDs + subdomains
      force_render: true      # price arrives via JS after load
      engine: readability     # Readability.js keeps the buybox AND returns structured markdown
    reddit.com:               # 3-tier pipeline with optional session cookies
      engine: readability
      timeout: 30
      cookies:
        reddit_session: "..."
        token_v2: "..."
    youtube.com:
      force_render: true
      scroll: true            # comments mount on scroll
```

### Hybrid decision flow

```
1. Documents (pdf/docx/xlsx/pptx/rtf)                      → raw byte extraction (no browser)
2. Reddit URLs                                            → 3-tier pipeline:
                                                             Tier 1: .json API (sub-second, structured markdown)
                                                             Tier 2: Redlib / SafeReddit mirror
                                                             Tier 3: Browser + Readability (full comment tree)
3. Domain override force_render | request force_render    → browser
4. Static fetch (httpx)
5. Status 401/403/429                                     → browser
6. HTML looks like a SPA (#root, __NEXT_DATA__, ...)      → browser
7. Extracted text < min_content_chars                     → browser
8. Otherwise                                              → deliver static result
```

## `browser`

| Key | Default | Description |
|---|---|---|
| `engine` | `playwright` | Browser backend engine: `playwright` (default in example), `patchright`, `scrapling` (production default; stealthiest), or `obscura` (external Rust/V8 headless browser via CDP). |
| `cdp_url` | `""` | CDP endpoint URL when `engine: obscura` (e.g. `http://127.0.0.1:9223` or `ws://127.0.0.1:9223`). Ignored for other engines. |
| `min_idle` | `1` | Warm browser instances kept idle in pool. On `scrapling`, pool size is fixed at 1. |
| `max_instances` | `5` | Upper limit of concurrent browser instances. |
| `idle_timeout` | `60` | Idle seconds before closing an unused browser instance. |
| `headless` | `true` | Run headless. |
| `launch_timeout` | `30` | Seconds allowed for browser launch before failing. |
| `stealth` | `true` | Apply anti-detection patches (`navigator.webdriver` masking, realistic Chrome UA, platform flags). |
| `network_idle_timeout` | `5` | Max seconds to wait for network activity to settle. Set lower for fast SPAs, higher for heavy dashboards. Override per-domain via `network_idle_timeout`. |
| `scroll_steps` | `0` | Number of smooth scroll passes down the page before extracting. Essential for sites that lazy-load content or comments via IntersectionObserver (e.g. Reddit, YouTube). `0` disables scrolling (fastest). Override per-domain via `scroll: true`. |
| `challenge_timeout` | `15` | Max seconds to wait for an anti-bot challenge (Cloudflare, DDoS-GUARD) to resolve before extracting. Active only under `engine: scrapling`. Override per-domain via `challenge_timeout`. |
| `solve_cloudflare` | `false` | Scrapling engine only: use Scrapling's built-in Cloudflare solver on EVERY page. Adds ~5s per page of networkidle wait time. Keep `false` (default) to let the lightweight title-polling loop in `page_action` handle challenges with zero overhead on clean pages; set `true` only if targeting sites with interactive turnstile checkboxes. |
| `fallback_solver` | `true` | Scrapling engine only: when `looks_like_challenge()` detects a challenge after normal extraction, automatically retry that single URL with Scrapling's built-in challenge solver. Gives automatic bypass without paying the ~5s solver overhead on clean pages. |

## `auth`

| Key | Default | Description |
|---|---|---|
| `enabled` | `false` | Require `Authorization: Bearer <key>` on `/search`, `/extract` and `/admin/*`. `/health` stays open (healthcheck). |

Keys come from the `FORAGE_API_KEYS` env var (comma-separated) and are compared in constant time.

## `prompts`

Customize citation rules, tool descriptions, and OpenAPI/MCP parameter descriptions. Supports dynamic template placeholders: `{now_date}` (e.g. `2026-08-24 03:57 UTC`), `{year}` (`2026`), `{default_engines}`, `{available_engines}`, `{default_limit}`, `{citation_guidelines}`.

| Key | Default | Description |
|---|---|---|
| `prompts_path` | `null` | Optional absolute path to an external prompts YAML file (e.g. `/etc/forage/prompts.yaml`). When set, the file is loaded and merged into the prompts sub-config, overriding built-in defaults. Partial files are fine - missing keys fall back to the built-in values. |
| `citation_guidelines` | *(citation rules block)* | Reusable citation rules template. Automatically interpolated into tool descriptions via `{citation_guidelines}`. |
| `search_tool_description` | *(search template)* | Tool description presented in OpenAPI and MCP schemas for web search. |
| `extract_tool_description` | *(extract template)* | Tool description presented in OpenAPI and MCP schemas for web extraction. |
| `search_params` | `{query, limit, ...}` | Dictionary of argument descriptions for web search parameters. |
| `extract_params` | `{urls, force_render, ...}` | Dictionary of argument descriptions for web extract parameters. |

### External prompts file

You can keep prompts outside `config.yaml` by pointing `prompts_path` at a standalone YAML file (see `prompts.example.yaml`). The env var `FORAGE_PROMPTS_CONFIG` is an alternative to `prompts_path` (useful for injecting per-environment prompts without touching the main config file).

---

## Environment variables

| Variable | Where | Purpose |
|---|---|---|
| `FORAGE_API_KEYS` | service `.env` | Comma-separated Bearer API keys (used when `auth.enabled: true`). |
| `FORAGE_CONFIG` | service `.env` | Config file path inside the container (default `/etc/forage/config.yaml`). |
| `FORAGE_PROMPTS_CONFIG` | service `.env` | Optional standalone prompts config path. Overrides `prompts.prompts_path`. |
| `TZ` | service `.env` | Container timezone. |
| `FORAGE_URL` | Hermes `.env` | Base URL the Hermes plugin calls (e.g. `http://localhost:3672`). |
| `FORAGE_API_KEY` | Hermes `.env` | Key the plugin sends when auth is enabled. |
| `FORAGE_BYPASS_CACHE` | Hermes `.env` | `true` makes the plugin always send `Cache-Control: no-cache`. |
