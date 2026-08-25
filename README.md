<div align="center">

# 🐔 Forage

**Self-hosted, ultra-fast web search & extraction engine for LLMs, OpenWebUI, and AI Agents.**

A high-performance, single-container drop-in alternative to heavy multi-container scrapers like Firecrawl. Built for local AI agents, OpenWebUI, and Hermes Agent.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg)](#quick-start)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB.svg)](app/)
[![MCP](https://img.shields.io/badge/MCP-2024--11--05-green.svg)](#openwebui--mcp-model-context-protocol-integration)

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
  - Automatic SearXNG engine failure tracking and temporary suspension to prevent slow/broken search engines from crashing queries.
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

---

## 🚀 Quick Start

### 1. Requirements
- Docker Engine 24+ with Docker Compose v2
- A running SearXNG instance on a shared network (see [docs/SEARXNG.md](docs/SEARXNG.md))

### 2. Setup & Launch

```bash
git clone https://github.com/aldemaroc/forage.git
cd forage
cp .env.example .env                 # configure FORAGE_API_KEYS, TZ
cp config.example.yaml config.yaml   # customize search engines, domain overrides, cache

docker compose up -d --build
```

Verify service health:
```bash
curl http://localhost:3672/health
```

---

## 📖 API & Tool Calling Examples

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

### 4. OpenAI Chat Completions Streaming (`POST /v1/chat/completions`)

```bash
curl -N -X POST http://localhost:3672/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "web_extract",
    "messages": [
      {"role": "user", "content": "https://en.wikipedia.org/wiki/Python_(programming_language)"}
    ],
    "stream": true,
    "max_chars": 5000
  }'
```

### 5. OpenWebUI & MCP Integration

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
  include_favicon: false

search:
  searxng_url: http://searxng:8080
  engines: [google, qwant, brave, bing, duckduckgo, startpage, reddit]
  citation_style: site_name

extract:
  timeout: 30
  max_content_chars: 100000
  require_max_chars: false
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
```

---

## 📄 License & Credits

- License: [GPL v3](LICENSE)
- Original creator: **Aldemaro Campos** ([aldemaroc/forage](https://github.com/aldemaroc/forage))
- Enhancements, MCP protocol, streaming, prompt engine & Reddit pipeline: Vibecoded by the community.
