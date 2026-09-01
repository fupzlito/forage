# AGENTS.md

Guidance for AI agents and contributors working on this repository. Read this
before changing code.

## What this is

Forage is a self-hosted, single-container web search and extraction service:
a REST API that fetches a URL and returns clean, structured content (markdown)
without running a cloud scraper. It is designed to be lightweight, fast and
easy to operate: plain HTTP fetch first, headless browser only when the page
really needs JavaScript.

The project is developed by the repository owner together with an AI coding
assistant; the repository is public and must stay professional. Everything in
this repo (code, docs, commits) is written in English.

## Architecture

Single Python service (FastAPI + uvicorn), one container. Modules in `app/`:

| Module | Responsibility |
|---|---|
| `main.py` | FastAPI app, endpoints, pool lifespan, auth dependency |
| `config.py` | YAML loader: defaults -> deep merge -> validation; typed dataclasses |
| `prompts.py` | Safe template rendering for dynamic tool descriptions and citation guidelines |
| `mcp.py` | MCP protocol endpoints (`/mcp`, `/mcp/sse`), OpenAI compatibility, tool schemas |
| `youtube.py` | `youtube_search` tool + `POST /v1/youtube/search`: direct YouTube Data API v3. Active only when `youtube.api_key` is set; no keyless fallback |
| `cache.py` | Thread-safe TTL LRU cache (search and extract), `clear()` for purge |
| `searxng.py` | SearXNG client (`/search?format=json`), live engine discovery + validation, clean snippet results |
| `extract.py` | Hybrid extraction heuristic (static -> browser), Reddit 3-tier pipeline, document path |
| `browser.py` | In-process Chromium pool (playwright/patchright) OR single Scrapling session; semaphore, cleanup, page_action |
| `documents.py` | PDF/docx/xlsx/pptx/rtf extraction from raw bytes (never through the browser) |
| `reddit.py` | Reddit JSON parsing + comment-tree rendering (Tier-1 of the Reddit pipeline) |
| `auth.py` | Bearer API keys (env `FORAGE_API_KEYS`), constant-time comparison |

API: `GET /health`, `POST /search`, `POST /extract`, `POST /v1/youtube/search` (alias `/youtube/search`), `GET /v1/tools`, `POST /v1/tools/call`, `POST /v1/chat/completions`, `GET /v1/models`, `POST /mcp`, `GET /mcp/sse`, `POST /admin/cache/purge`.

## Core design decisions (do not reverse without a strong reason)

- **Static first, browser as fallback.** A full browser is expensive (RAM and
  latency). Most pages extract fine with plain HTTP + trafilatura. The browser
  is the exception, not the default.
- **Hybrid decision flow** in `extract.py` (in order; see the module docstring):
  1. Normalize Reddit URL; resolve domain override
  2. Document path (PDF/DOCX/XLSX/PPTX/RTF) if not force-rendered
  3. Reddit 3-tier fast path: Tier 1 official `.json` -> Tier 2 Redlib mirror -> Tier 3 browser
  4. Static fetch with markdown negotiation (`text/markdown`)
  5. Hybrid browser fallback when the static result is not enough
  6. Challenge detection -> solver retry
  7. `_to_output` conversion + full envelope (position, domain, citation, favicon, ...)
- **`needs_browser_render` runs 4 independent checks; any one of them sends the
  page to the browser:**
  1. SPA framework markers in the static HTML (`id="root"`, `id="__next"`,
     `data-reactroot`, `__NEXT_DATA__`, `ng-version`, `__INITIAL_STATE__`,
     `data-svelte`, `id="__gatsby"`, `ytInitialData`, `ytcfg`, ...).
  2. Content density: HTML >= 50 KB with extracted text <= max(500, 1% of
     HTML). A big shell with almost no text means content is mounted by JS.
  3. Empty `<main>`: a `<main>` element exists in the static HTML but holds
     <= 100 chars of visible text (placeholder awaiting client-side render).
  4. Extracted text below `min_content_chars` (default 200).
- **Markdown output via trafilatura** (`output_format="markdown"`): preserves
  headings, bold, lists, code blocks. Tested against html2text/markdownify/
  readability-lxml: trafilatura markdown is the best cost/benefit.
- **Stealth Chromium pool**: built-in stealth flag on the browser launcher
  sets `--disable-blink-features=AutomationControlled` + an init script that
  masks `navigator.webdriver`/`chrome`/`languages`/`plugins`, plus a real
  desktop Chrome UA. Do not add playwright-stealth/curl_cffi unless a real case
  demands it (YAGNI, maintenance cost).
- **Engine is pluggable via config** (`browser.engine`):
  `scrapling` (default; strongest anti-bot), `playwright`, `patchright`,
  `obscura` (experimental Rust/V8 browser via CDP).
- **Cache in memory, per operation**: search ON (TTL 300s, protects SearXNG engines
  from bot detection); extract OFF by **code** default (`extract.enabled: false,
  extract.ttl: 3600`; always fresh when off). `config.example.yaml` ships the worked
  example `extract.enabled: true / ttl: 60`. Master switch `cache.enabled` + per-section toggles.
- **Config reload = container restart only.** The config decides whether
  browser processes start; hot reload would be fragile. 1-2s downtime is fine.
- **`raw_content_markdown: true` (default)**: `raw_content` mirrors the clean
  markdown so consumers that read `raw_content` first (Hermes' web extract
  tool) get clean text. `false` returns raw HTML in `raw_content`.
- **Auth optional (Bearer)**: `auth.enabled: false` default (local use + bind
  127.0.0.1). Keys via env, `hmac.compare_digest`. `/health` always open.
- **`default_limit` resolution**: Pydantic models (`SearchRequest`) and MCP tool
  handlers default `limit` to `None` so omitted limits consistently fall back to
  `config.search.default_limit` at runtime (capped by `config.search.max_limit`).
  Do not hardcode integer defaults in endpoint models that shadow YAML config.
- **Favicon inclusion hierarchy**: `include_favicon: false` (default) omits
  favicon URLs from search/extract responses and MCP text blocks. Configured
  globally under `tools.include_favicon` or overridden per-section under
  `search.include_favicon` and `extract.include_favicon`.
- **SearXNG Engine Discovery & Auto-Pruning**: `available_engines` is dynamically
  auto-discovered from SearXNG (`GET /config`, 2 s probe, 5-min TTL cache) by
  filtering for `enabled: true` engines assigned to `categories: [general]`,
  stripping sub-scrapers (`.images`, `.audio`) and translation tools.
  `default_engines` (legacy key: `engines`, env `FORAGE_DEFAULT_ENGINES`) defines
  the baseline engines queried when omitted in requests. When querying SearXNG,
  `normalize_and_validate_engines()` automatically prunes inactive engines from
  the defaults before sending the request. Explicit `available_engines` in
  `config.yaml` or `FORAGE_AVAILABLE_ENGINES` immediately bypasses auto-discovery.
  **Fragile fallback path:** if the `/config` probe fails (e.g. SearXNG still warming up;
  timeout is 2 s), `get_live_available_engines` returns the built-in
  `DEFAULT_AVAILABLE_ENGINES` tuple (a plausible catalog that is NOT validated against
  your instance; e.g. it names `youtube` and `reddit` whether or not they are registered).
  The failure is `logger.debug`-only, and subsequent `POST /search` calls then emit
  "ignored unknown engine(s)" warnings and fall back to `search.default_engines`.
  See `IDEAS.md` section 2 for hardening options.
- **YouTube search / channel discovery**: active if and only if `youtube.api_key` is set
  (env `FORAGE_YOUTUBE_API_KEY` / `YOUTUBE_API_KEY`). Queries hit the YouTube Data API v3
  directly and `@handle` resolves to a Channel ID. Without a key the tool is not registered
  and calls return a clear "key required" error. There is no keyless / SearXNG fallback.

## API contract (critical, tested against the Hermes consumer)

- `POST /search` returns the envelope
  `{"success": true, "data": {"web": [{title, url, description, position}]}}`.
- `POST /extract` returns a LIST of result dicts per URL, one entry per URL
  (not the envelope): `{url, title, content, raw_content, method}`.
- A URL that could not be extracted returns `{url, error}`.
- `method` is one of `static`, `browser`, `browser+solver`, `browser+readability`,
  or a document method (pdf/docx/xlsx/pptx/rtf).

## Configuration

All options live in `config.yaml` (instance) / `config.example.yaml` (repo).
Full reference: `docs/CONFIG.md`.

### Domain overrides (`extract.domain_overrides`, since 0.8.0)

One structure per URL pattern, replacing the older separate lists
(`force_render_domains`, `url_rewrites`, `full_text_domains`). A domain can now
combine overrides (e.g. Amazon needs `force_render` + `full_text` for the buy
box price; Reddit needs `url_rewrite` + `full_text` for comments; YouTube needs
`force_render` + `scroll`).

Pattern syntax (www-insensitive, case-insensitive):

| Pattern | Matches |
|---|---|
| `x.com` | host or any subdomain (endswith) |
| `.amazon.*` | base domain + subdomains, wildcard anchored per label (never substring: does NOT match `buyamazon.com`) |
| `reddit.com/r/` | EXACT host + path prefix (does NOT match `old.reddit.com/r/`) |

Available overrides: `force_render`, `full_text`, `engine` ("trafilatura" |
"readability"), `wait_for`, `url_rewrite`, `scroll`, `timeout` (1-120s),
`network_idle_timeout` (0-60s), `challenge_timeout` (0-120s), `headers` (dict),
`cookies` (dict).

Precedence: the most specific override wins (longest pattern); request-level
params (`force_render`/`wait_for` in the call) are ABSOLUTE and beat the
override.

## Golden rules (learned the hard way)

- **Dynamic `max_chars` caching**: Extract cache stores the full, untruncated
  document (up to `max_content_chars`). When an LLM re-fetches a cached URL
  with a larger `max_chars`, Forage serves an instant cache hit (<1ms) sliced to
  the new limit with zero network or browser overhead.
- **Reddit 3-tier extraction pipeline**: Reddit URLs bypass standard static fetch
  and run through: Tier 1 (`.json` API with browser navigation headers and
  session cookies) -> Tier 2 (Redlib / SafeReddit mirror with 4s timeout) ->
  Tier 3 (Browser + Readability with semantic `h3`-`h6` comment depth).
- **Streaming websocket sites never idle**: `reddit.com`, `x.com` and `twitter.com`
  maintain live websockets and must bypass `page.wait_for_load_state("networkidle")`
  to avoid unnecessary 5s stall penalties.
- **Browser fetches must have an asyncio timeout**: Always wrap `session.fetch()`
  in `asyncio.wait_for(..., timeout=timeout + 5)` so broken tabs or hung promises
  never lock the pool semaphore indefinitely.
- **When a page seems to be missing data, do NOT blame the browser/fingerprint
  first.** Test the browser engine directly against the URL and save the full
  HTML before concluding. The pipeline itself can lose data in two places:
  1. trafilatura `only_main_content=True` discards non-article content (buy
     boxes inside `<form>`, comments) as boilerplate.
  2. `raw_content = html[:max_content_chars]` (default 100k) truncates large
     pages (a 1.6MB Amazon page keeps the price at position ~272k, cut off).
  Fix with `engine: readability` in the domain override (Readability.js keeps
  the buybox and returns markdown) or by raising `max_content_chars`.
- **`challenge-platform` must NEVER be a challenge marker**: Cloudflare injects
  `/cdn-cgi/challenge-platform/...` into every page it serves, even without an
  active challenge. It caused false "Blocked by anti-bot challenge" on
  openai/discord/fandom/canva/dailymail. Title is the primary signal; markers
  are secondary.
- **Marker `ng-app` needs the attribute form (`ng-app=`)**: bare `ng-app`
  false-positives on "shopping-app" in prose.
- **Cloudflare managed challenge can answer 200 on static fetch**: after the
  static fetch, if `looks_like_challenge` -> force browser instead of
  delivering an error.
- **`network_idle=True` on the Scrapling session hangs streaming pages**
  (x.com never idles). Keep `network_idle=False` on the session and replicate
  network-idle-with-cap inside the `page_action`.
- **`browser.solve_cloudflare: false` is the default**: the native solver waits
  ~5s of networkidle on EVERY page before detecting a challenge (even without
  one). Title polling in `page_action` resolves non-interactive challenges with
  no fixed cost. Turn the solver on only for interactive challenges (rare).
- **`disable_resources=True` (Scrapling) must NOT be used**: it blocks
  stylesheets/fonts and breaks pages (fandom returned 1KB, x.com incomplete).
- **Scrapling's `start()`/`close()` are coroutines**: always `await` them.
- **`host.docker.internal` does not resolve in a custom compose network**: use
  a shared docker network and the service name (`searxng:8080`).
- **File bind mounts do not follow renames**: editing `config.yaml` in place
  swaps the inode, and a file bind mount keeps pointing at the old inode. After
  editing the config, recreate the container (`docker compose up -d --build`).
- **Smooth scrolling is required to trigger lazy load** on some sites
  (IntersectionObserver): `scrollBy`/viewport jumps do not fire it. Use
  `browser.scroll_steps` or the `scroll` override.
- **Sites vary between runs**: anti-bot is intermittent (stackoverflow, tiktok,
  ebay). A failure in one benchmark round is not a regression; re-test isolated
  before concluding.

## Testing

```bash
# Health
curl -s http://localhost:3672/health

# Search
curl -s -X POST http://localhost:3672/search -H 'Content-Type: application/json' \
  -d '{"query":"test","limit":3}'

# Static extract
curl -s -X POST http://localhost:3672/extract -H 'Content-Type: application/json' \
  -d '{"urls":["https://en.wikipedia.org/wiki/Web_scraping"],"formats":["markdown"]}'

# Force browser (x.com)
curl -s -X POST http://localhost:3672/extract -H 'Content-Type: application/json' \
  -d '{"urls":["https://x.com/OpenAI"]}'
```

Reference validation set (regression baseline): Wikipedia/GitHub/docs = static;
x.com = browser; a Cloudflare-protected page = browser + challenge handling.
A benchmark script lives in `benchmark/` (three containers with configs that
differ only in `browser.engine`, same URL list, same criteria; ✅ = no error,
content >= 100 chars, non-empty title).

## Conventions

- All code, docs, commit messages and PR text in **English**.
- No em dashes (—) or en dashes (–) anywhere; use hyphens or commas.
- Do not write anything that reads like generic AI output; be direct and
  specific.
- License: GPL v3 (see LICENSE). Docs/README in English, no personal data.
- Credits in the README follow the project convention.
- No semantic version bump per feature while the project is in testing phase;
  version only when leaving testing (owner decides).
- Do not commit on every change: the owner asks for consolidated commits.

## Docs map

- `README.md` — what it is, quickstart, API reference
- `docs/CONFIG.md` — every config key
- `docs/SEARXNG.md` — SearXNG install + docker network pitfalls
- `docs/HERMES.md` — integration with the Hermes agent
- `docs/BENCHMARK.md` — engine comparison tables
- `config.example.yaml` — default configuration (keep in sync with CONFIG.md)
- `IDEAS.md` — behavior/design notes: youtube_search availability, fragile SearXNG engine discovery, Reddit 3-tier pipeline
- `MASTER_CODE_REVIEW.md` — consolidated issue registry (per-issue fixes, implementing commits, open items)
