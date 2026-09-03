<div align="center">

# 🐔 Forage

**Self-hosted, ultra-fast web search & extraction engine for LLMs, OpenWebUI, and AI Agents.**

A high-performance, single-container drop-in alternative to heavy multi-container scrapers like Firecrawl. Built for local AI agents, OpenWebUI, and Hermes Agent.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg)](#quick-start)
[![Python](https://img.shields.io/badge/Python-3.13-3776AB.svg)](app/)
[![MCP](https://img.shields.io/badge/MCP-2024--11--05-green.svg)](#5-openwebui--mcp-integration)

</div>

---

## 🌟 About This Fork & Credits

This project is a fork of **[aldemaroc/forage](https://github.com/aldemaroc/forage)** (forked at commit `93920b3`).

Huge props and credit go to the original creator, **Aldemaro Campos**, who vibecoded the original foundation of Forage: an exceptionally reliable hybrid scraping engine, intelligent anti-bot heuristics, and rich browser customization.

Everything built on top of commit `93920b3` has been vibecoded to transform Forage into a first-class, agentic tool server with native MCP support, SSE streaming, dynamic LLM prompt templating, multi-tier Reddit scraping, and sub-millisecond pre-truncation caching.

---

## 🚀 What's New in this Fork

- 🔌 **Native Model Context Protocol (MCP) & OpenAI Compatibility**:
  - MCP over SSE (`GET /mcp/sse`, `POST /mcp/messages`) and standard JSON-RPC (`POST /mcp`).
  - OpenAI Function-Calling & Chat endpoint (`GET /v1/tools`, `POST /v1/tools/call`, `POST /v1/chat/completions`, `GET /v1/models`).
  - Full OpenAI SSE streaming support on `POST /v1/chat/completions` (`chat.completion.chunk`) when `"stream": true` is passed.
  - Direct OpenAPI schema (`GET /openapi.json`) for seamless OpenWebUI integration.
  - **YouTube search & channel discovery** (`youtube_search` tool + `POST /v1/youtube/search`): native YouTube Data API v3, active when `FORAGE_YOUTUBE_API_KEY` is set (see "YouTube search" under API examples).
- ⚡ **Real-Time SSE Streaming**:
  - `POST /extract` with `stream: true` or `Accept: text/event-stream` progressively streams extracted URLs as Server-Sent Events as soon as each finishes.
  - `POST /v1/chat/completions` with `stream: true` progressively streams tool execution chunks directly to OpenAI clients and OpenWebUI.
- 🎯 **Advanced 3-Tier Reddit Extraction Engine**:
  - **Tier 1 (`reddit+json`)**: High-throughput direct JSON API with Akamai-compliant `Sec-Fetch-*` navigation headers, intra-call rate throttling (0.75s), and rate-limit cooldown. Extracts full threads or multi-post feeds in **<1s** with ~0% CPU/RAM.
  - **Tier 2 (`reddit+mirror`)**: Redlib / SafeReddit mirror failover with 4.0s fast timeout and instant 404 detection.
  - **Tier 3 (`browser+readability`)**: Scrapling browser fallback with semantic `<h1>` titles, `<p>Posted by u/...` author tags, dynamic comment depth hierarchy (`h3`-`h6`), multi-post feed card wrappers, and websocket `networkidle` bypass.
  - **Deep Markdown Cleanup**: Aggressively strips Reddit navigation bars, chat buttons, repost nudges, sort pills, avatar embeds, and UI noise.
- 🏎️ **Dynamic Pre-Truncation Extract Caching**:
  - In-memory extract cache stores the **full, untruncated** document.
  - When an LLM initially fetches a URL with a low `max_chars` (e.g. 2,000) and later requests higher context (e.g. 20,000), Forage serves an **instant sub-millisecond cache hit (`<1ms`)** sliced to the new limit with zero network or browser overhead.
- 📝 **Dynamic Prompt & Citation System**:
  - Customizable tool descriptions supporting live template variables: `{now_date}`, `{year}`, `{default_engines}`, `{available_engines}`, `{default_limit}`, `{citation_guidelines}`.
  - Timezone-aware date injection (`TZ` env var) so models always know current date/time context.
  - 7 customizable citation formats (`site_name`, `site_name_brackets`, `academic`, `site_name_bold`, `site_name_italic`, `bracket_domain`, `bracket_title`).
  - Optional standalone `prompts.yaml` file support.
- 📏 **LLM Character Budgeting (`max_chars` & `require_max_chars`)**:
  - Allows LLMs to specify character budgets per URL with clear inline `[TRUNCATED at X of Y chars]` markers.
  - Configurable `require_max_chars` setting to prompt models to budget tokens responsibly.
- 🛡️ **Per-Domain Auth Overrides (`headers` & `cookies`)**:
  - Pass custom HTTP headers and session cookies (e.g. `reddit_session`, `token_v2`) per domain override directly into static HTTP and browser sessions.
- 🔍 **SearXNG Engine Management & Resiliency**:
  - Separation of default engines (`search.engines`) vs all available engines (`search.available_engines`).
  - Dynamic engine alias resolution and model-passable `language` parameter.
  - Real-time engine health tracking: parses SearXNG `unresponsive_engines` (suspensions, CAPTCHAs, network timeouts) to report active vs failing engines and prevent LLMs from hammering broken engines.
  - In-process rate-limit cooldown (30s) and intra-batch request throttling (0.75s) on native extract engines (Reddit `.json` API).
  - Search cache (TTL 300s) to shield SearXNG upstream search providers from bot detection and bans.
  - Publication date extraction (`published_date`) and snippet date prioritization.
- 🔒 **Browser Resiliency & Deadlock Safety**:
  - Strict `asyncio.wait_for` timeout guards on all browser operations, eliminating stuck browser tabs and semaphore deadlocks.
  - Automatic `networkidle` bypass for streaming websocket domains (`reddit.com`, `x.com`, `twitter.com`).
- 💓 **Enhanced Healthcheck**:
  - Live heartbeat probe to SearXNG backend with latency tracking (`GET /health`).

---

## 🏗️ Architecture

```
OpenWebUI / Hermes / LLM Agents
   │
   ├─► MCP (SSE / HTTP)           /mcp/sse, /mcp
   ├─► OpenAI Tool Calling        /v1/tools, /v1/tools/call
   └─► REST API                   /search, /extract
          │
          ▼
   FORAGE (Single Container, :3672)
   ├── FastAPI & Prompts Engine   (Dynamic templates, timezone context, citation formatting)
   ├── In-Memory LRU Cache        (Dynamic pre-truncation slice cache)
   ├── Extraction Engine
   │   ├── Document Parser        (PDF, DOCX, XLSX, PPTX, RTF from raw bytes)
   │   ├── Reddit 3-Tier Pipeline (Tier 1: .json API → Tier 2: Redlib → Tier 3: Scrapling Browser)
   │   ├── Static Extractor       (httpx + trafilatura markdown)
   │   └── Browser Extractor      (Scrapling / Playwright / Mozilla Readability.js)
   └── SearXNG Client             (Engine suspension, deduplication, snippet caps)
```

---

## ⚙️ Browser Engines

Forage defaults to **`scrapling`** (StealthyFetcher with fingerprint impersonation and Cloudflare Turnstile bypass), which is the fastest, stealthiest, and most memory-efficient engine.

Other engines are supported:
- **`playwright`**: Vanilla Chromium pool.
- **`patchright`**: Anti-detection Playwright fork. To use, install `patchright` in the container and set `browser.engine: patchright`.
- **`obscura`**: External Rust/V8 headless browser via CDP. Set `browser.engine: obscura` and specify `browser.cdp_url: "ws://127.0.0.1:9223"`.

**The browser is opt-in, not the default.** Static extraction (plain HTTP + trafilatura/readability) handles most pages; the browser fires only when a page fails the static path — `force_render: true`, 401/403/429 responses, SPA/challenge detection, or a `domain_overrides` rule says so. When it does run, it ships stealth flags (realistic fingerprint impersonation, masked automation signals), your configured per-domain session cookies, and a Cloudflare Turnstile solver as a last-resort retry — so a browser fetch is as quiet as the static path where it matters.

---

## 🚀 Quick Start

### Requirements
- Docker Engine 24+ with Docker Compose v2
- A running SearXNG instance on a shared network (see [docs/SEARXNG.md](docs/SEARXNG.md))

---

### 🐳 Docker Compose (Plex-style Simple Setup)

No git clone or compilation needed. Create a `docker-compose.yml` file:

```yaml
---
services:
  forage:
    image: fupzlito/forage:latest
    container_name: forage
    restart: unless-stopped
    ports:
      - "3672:3672"
    environment:
      - TZ=America/New_York                    # sets local time context for LLM prompts
      - FORAGE_SEARXNG_URL=http://searxng:8080 # SearXNG backend service
      - FORAGE_DEFAULT_ENGINES=google-cse,brave,bing,duckduckgo,startpage # default engines queried when search engine(s) omitted by LLMs
#     - FORAGE_REDDIT_SESSION="your_session_cookie_here"     # optional: logged-in reddit session (quote string to escape special characters)
#     - FORAGE_REDDIT_TOKEN_V2="your_token_v2_cookie_here"   # optional: reddit token_v2 cookie (quote string to escape special characters)
#     - FORAGE_REDDIT_MIRROR=your-redlib-host  # tier 2 redlib mirror (set to enable tier 2)
      - FORAGE_AUTH_ENABLED=false              # set true if exposing to the public internet
      - FORAGE_API_KEYS=your_api_key_here      # comma-separated keys when auth is enabled
      - FORAGE_REQUIRE_MAX_CHARS=false         # require LLMs to pass character budget per URL
#     - FORAGE_YOUTUBE_API_KEY="your_youtube_key_here"   # enables youtube_search (YouTube Data API v3)
#     - FORAGE_YOUTUBE_NAME="youtube_search"             # custom tool name exposed to LLMs for YouTube search
#   volumes:
#     # Uncomment if detailed config/prompt overrides are desired.
#     # Stores config.yaml and prompts.yaml (auto-seeded on first run if directory is empty):
#     # - ./config:/etc/forage
    networks:
      - searxng_default
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:3672/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s

networks:
  searxng_default:
    external: true
```

Start the container:

```bash
docker compose up -d
```

If SearXNG is not already running, create the shared network first:

```bash
docker network create searxng_default
```

Verify service health:
```bash
curl http://localhost:3672/health
```

---

### 🛠️ Building from Source & Container Builders

For developers, contributors, or users who want to modify browser dependencies, install custom Playwright/Patchright binaries, or run from local source:

#### 1. Clone the repository
```bash
git clone https://github.com/fupzlito/forage.git
cd forage
```

> **Pinned dependencies.** `requirements.txt` pins every package to an exact version (`==`). To bump a dependency, edit the `==` line, rebuild, and run the test suite. See the header comment in `requirements.txt` for the full workflow.

#### 2. Optional: Custom configuration
```bash
cp config.example.yaml config.yaml
```

#### 3. Build & Run with Docker Compose
```bash
# Build local image and launch
docker compose up -d --build
```

#### 4. Or Build Image Directly with Docker CLI
```bash
# Build custom image
docker build -t forage:local .

# Run standalone container
docker run -d \
  --name forage \
  -p 3672:3672 \
  -e TZ=America/New_York \
  -e FORAGE_SEARXNG_URL=http://searxng:8080 \
  -v $(pwd)/config:/etc/forage \
  --network searxng_default \
  forage:local
```

---

## 📖 API & Tool Calling Examples

### YouTube search (`POST /v1/youtube/search`)

`query` plus optional `channel` (`@handle` or `UCC...` id), `sort_by` (`date`, `popular`, `relevance`, `rating`), and `limit`.

```bash
curl -s -X POST http://localhost:3672/v1/youtube/search -H 'Content-Type: application/json' \
  -d '{"query":"quantum computing","channel":"@aboutoliver","sort_by":"date","limit":5}'
```

- **Requires `FORAGE_YOUTUBE_API_KEY`**: the tool is active only when a YouTube Data API v3 key is configured. `@handle` resolves to a Channel ID. Without a key, `youtube_search` is not registered and calls return a clear "key required" error.

### 1. Web Search (`POST /search`)

```bash
curl -s -X POST http://localhost:3672/search \
  -H 'Content-Type: application/json' \
  -d '{"query":"local llm vision models","limit":5,"language":"en-US"}'
```

### 2. Web Extract (`POST /extract`)

```bash
curl -s -X POST http://localhost:3672/extract \
  -H 'Content-Type: application/json' \
  -d '{"urls":["https://en.wikipedia.org/wiki/Artificial_intelligence"],"max_chars":5000}'
```

### 3. Progressive SSE Streaming (`POST /extract`)

```bash
curl -N -X POST http://localhost:3672/extract \
  -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d '{"urls":["https://en.wikipedia.org/wiki/Python_(programming_language)","https://x.com/OpenAI"],"stream":true}'
```

### 4. OpenWebUI & MCP Integration

Connect OpenWebUI directly using either:
- **Native MCP (Streamable HTTP)**: `http://forage:3672/mcp`
- **OpenAI Compatible Tool Server**: `http://forage:3672/v1` (with full streaming support)
- **OpenAPI Tool**: Import `http://forage:3672/openapi.json`
- **MCP SSE (Claude Desktop / Cursor / Cline)**: `http://forage:3672/mcp/sse`

---

## 🔧 Configuration

All behavior is configured via `config.yaml`. See **[docs/CONFIG.md](docs/CONFIG.md)** for the full configuration reference.

Example configuration snippet:

```yaml
server:
  host: 0.0.0.0
  port: 3672

cache:
  enabled: true
  search:
    enabled: true
    ttl: 300
  extract:
    enabled: true        # stores full text for instant dynamic max_chars cache hits
    ttl: 120

tools:
  search_name: web_search
  extract_name: web_extract
  youtube_name: youtube_search
  include_favicon: false

search:
  searxng_url: http://searxng:8080
  default_engines: [google, bing, brave, duckduckgo, qwant]
  citation_style: site_name

extract:
  timeout: 30
  max_content_chars: 100000
  require_max_chars: false
  max_document_bytes: 150000000  # max document download size (150 MB)
  max_response_bytes: 50000000   # max static HTML response size (50 MB)
  reddit_mirror: null    # tier 2 redlib mirror (set to a self-hosted redlib instance to enable tier 2)
  domain_overrides:
    reddit.com:
      engine: readability
      timeout: 30
      # Optional: pass your logged-in cookies for unrestricted sub-second .json pulls
      # cookies:
      #   reddit_session: "..."
      #   token_v2: "..."
    ".amazon.*":
      force_render: true
      engine: readability

browser:
  engine: scrapling
  fallback_solver: true

youtube:
  api_key: null          # YouTube Data API v3 key (or set FORAGE_YOUTUBE_API_KEY). When set, enables youtube_search.
  default_limit: 20
  max_limit: 50
```

### 🐳 Docker Environment Variables

All settings can be dynamically overridden via Docker environment variables without modifying `config.yaml`:

| Variable | Description | Default |
|---|---|---|
| `FORAGE_CONFIG` | Config YAML file or directory path inside container | `/etc/forage/config.yaml` |
| `FORAGE_PROMPTS_CONFIG` | Prompts YAML file or directory path inside container | `/etc/forage/prompts.yaml` |
| `FORAGE_SEARXNG_URL` | SearXNG backend service URL | `http://searxng:8080` |
| `FORAGE_DEFAULT_ENGINES` | Comma-separated default engine filter when no engine is specified | `google,bing,brave,duckduckgo,qwant` |
| `FORAGE_AVAILABLE_ENGINES` | Comma-separated engine catalog (optional; overrides auto-discovery) | *Auto-discovered from SearXNG* |
| `FORAGE_BROWSER_ENGINE` | Browser engine (`scrapling`, `playwright`, `patchright`, `obscura`) | `scrapling` |
| `FORAGE_EXTRACT_ENGINE` | Markdown extraction engine (`trafilatura`, `readability`) | `trafilatura` |
| `FORAGE_SEARCH_NAME` | Custom tool name exposed to LLMs for web search | `web_search` |
| `FORAGE_EXTRACT_NAME` | Custom tool name exposed to LLMs for web extraction | `web_extract` |
| `FORAGE_REDDIT_SESSION` | Authenticated `reddit_session` cookie for Reddit JSON API (enclose in quotes `"..."`) | `""` |
| `FORAGE_REDDIT_TOKEN_V2` | Authenticated `token_v2` cookie for Reddit JSON API (enclose in quotes `"..."`) | `""` |
| `FORAGE_REDDIT_COOKIES` | Full raw Reddit cookie string (`k=v, k2=v2`, enclose in quotes) | - |
| `FORAGE_REDDIT_MIRROR` | Tier 2 redlib mirror (set to enable tier 2; all public mirrors are Anubis/Turnstile-blocked) | `null` |
| `FORAGE_EXTRACT_MAX_DOCUMENT_SIZE` | Max size for document downloads in MB (PDF/DOCX/XLSX/PPTX/RTF) | `150` |
| `FORAGE_EXTRACT_MAX_WEBPAGE_SIZE` | Max size for static HTML responses in MB | `50` |
| `FORAGE_YOUTUBE_API_KEY` / `YOUTUBE_API_KEY` | Google YouTube Data API v3 key; when set, enables the `youtube_search` tool | `""` |
| `FORAGE_YOUTUBE_NAME` | Custom tool name exposed to LLMs for YouTube search | `youtube_search` |
| `FORAGE_YOUTUBE_DEFAULT_LIMIT` | Default YouTube results per search | `20` |
| `FORAGE_YOUTUBE_MAX_LIMIT` | Max YouTube results per search | `50` |
| `FORAGE_REQUIRE_MAX_CHARS` | Require LLMs to specify character budgets (`true`/`false`) | `false` |
| `FORAGE_EXTRACT_ALLOW_PRIVATE_IPS` | Allow extraction of URLs resolving to private/reserved IPs (`true`/`false`) | `false` |
| `FORAGE_AUTH_ENABLED` | Enable Bearer API authentication (`true`/`false`) | `false` |
| `FORAGE_API_KEYS` | Comma-separated API keys (when auth is enabled) | `""` |
| `FORAGE_PORT` / `PORT` | Container HTTP listen port | `3672` |
| `FORAGE_LOG_LEVEL` | Log level (`debug`, `info`, `warning`, `error`) | `info` |
| `TZ` | Container timezone for dynamic prompt time context | `America/Recife` |

See **[docs/CONFIG.md](docs/CONFIG.md)** for the complete configuration reference.

---

## 📄 License & Credits

- License: [GPL v3](LICENSE)
- Original creator: **Aldemaro Campos** ([aldemaroc/forage](https://github.com/aldemaroc/forage))
- Enhancements, MCP protocol, streaming, prompt engine & Reddit pipeline: Vibecoded by the community.
