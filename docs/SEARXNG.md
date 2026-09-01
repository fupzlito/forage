# Setting up SearXNG for Forage

Forage delegates all search to [SearXNG](https://github.com/searxng/searxng), a privacy-respecting metasearch engine that aggregates Google, Bing, Brave, etc. without API keys.

> **Why SearXNG?** It is a single lightweight container, self-hosted, and it does the hard part of talking to multiple search engines (including dealing with their anti-bot quirks). Forage caches results for 5 minutes by default, which further protects the engines.

## 1. Create the SearXNG compose

```yaml
# searxng/docker-compose.yml
services:
  searxng:
    image: searxng/searxng:latest
    container_name: searxng
    restart: always
    ports:
      - "127.0.0.1:8080:8080"   # host-only; Forage reaches it via the docker network
    volumes:
      - ./settings.yml:/etc/searxng/settings.yml:ro
    environment:
      - TZ=America/Recife
      - SEARXNG_BASE_URL=http://localhost:8080/
```

Start it:

```bash
cd searxng
docker compose up -d
```

## 2. settings.yml: the important parts

Create the file **before** running the compose. If the file does not exist, Docker will create a *directory* named `settings.yml` and SearXNG will fail to start.

SearXNG needs **JSON output enabled**, or Forage gets a 403 when calling `/search?format=json`:

```yaml
search:
  formats:
    - html
    - json
  ban_detector:
    default:
      http_code: 429
      suspend_time: 300   # 5 mins suspension instead of SearXNG's default 1 hour (3600s)

server:
  secret_key: "change-me-to-a-long-random-string"   # required
  limiter: false
```

Only enable the engines you want (defaults in the image already work):

```yaml
engines:
  - name: google
    engine: google_cse         # Google CSE backend is much more resilient against CAPTCHAs & bans
    shortcut: g
    categories: [general]
    disabled: false
  - name: google cse           # Disable duplicate default CSE entry if overriding 'google'
    disabled: true
  - name: bing
    engine: bing
    shortcut: b
    categories: [general]
    disabled: false
  - name: brave
    engine: brave
    shortcut: br
    categories: [general]
    disabled: false
  - name: duckduckgo
    engine: duckduckgo
    shortcut: ddg
    categories: [general]
    disabled: false
  - name: qwant
    engine: qwant
    shortcut: q
    categories: [general]
    disabled: false
  - name: youtube
    engine: youtube_api        # Official YouTube Data API v3
    shortcut: yt
    api_key: "YOUR_API_KEY"
    categories: [general]
    disabled: false
  - name: youtube noapi        # Disable scraper engine if using API key
    disabled: true
```

> **Engine names vs. backends (matters for Forage):** SearXNG reports each engine by the `name:` value you set, and Forage validates requested engines against those **names** (`searxng.py` engine-alias table + the live `available_engines` list). The dedicated `youtube_search` tool is native-only: it queries the YouTube Data API v3 directly and is active only when `FORAGE_YOUTUBE_API_KEY` is set. The SearXNG `youtube_api` engine (above) is a separate, optional engine for the general `search` tool — it is not used by `youtube_search`.

> **Why `google_cse`?** Standard Google scraping (`engine: google`) gets aggressively rate-limited and blocked by Google's anti-bot systems. Using the `google_cse` backend in SearXNG queries Google Custom Search endpoints, providing far higher uptime and eliminating ban locks.
>
> **YouTube Search**: In modern SearXNG, use `engine: youtube_api` with your Google Cloud `api_key`. Add `categories: [general]` so Forage auto-discovers it. Disable `youtube noapi` to avoid redundant fallback warnings.
>
> **Note on Reddit**: You do not need the SearXNG `reddit` engine enabled—Forage includes a dedicated high-throughput 3-tier Reddit extraction engine (`POST /extract`) with direct JSON API, comment hierarchy, and mirror fallbacks.
>
> **Category Auto-Discovery**: Forage auto-discovers all enabled engines in the `general` category. If you want custom engines (e.g. `github`, `youtube`, `wikipedia`) available to LLMs, ensure `categories: [general]` and `disabled: false` are set in `settings.yml`.
>
> **Duplicate Names**: Never define the same engine `name` twice in `settings.yml`. To enable an engine with a custom key, define it once with `disabled: false`.
>
> **Pitfall**: the `wikidata` engine fails on startup in some versions. If the container logs show a wikidata error, disable it (`disabled: true`).

## 3. Network layout: how Forage reaches SearXNG

Forage and SearXNG must be on the **same Docker network** so Forage can call SearXNG by service name (`http://searxng:8080`).

The SearXNG compose above creates a network named `searxng_default`. Forage's compose joins it as an external network:

```yaml
networks:
  searxng_default:
    external: true
```

If your SearXNG compose uses a different project name, the network will be `<project>_default`. Adjust Forage's `networks:` section and `search.searxng_url` accordingly (e.g. `http://searxng:8080`).

> **Why not `host.docker.internal`?** In a custom Compose network, `host.docker.internal` is **not** automatically resolved. Using the shared docker network + service name is the reliable pattern.

## 4. Verify

```bash
# From inside the Forage container network namespace (or via the API):
curl -s -X POST http://localhost:3672/search -H 'Content-Type: application/json' \
  -d '{"query":"hello world","limit":3}'
```

Expect `"success": true` with results.

> **Cold start:** Forage probes SearXNG `GET /config` (the live engine auto-discovery) with a short timeout, then caches the catalog for 5 minutes. If SearXNG is not ready or is slow to answer, the probe falls back to a built-in engine catalog that has **not** been validated against your instance (it can name engines you never registered, e.g. `youtube`, `reddit`); subsequent `POST /search` calls then report "ignored unknown engine(s)" warnings and fall back to the default engines. Bring up Forage after SearXNG is fully up, or pin the catalog by setting `FORAGE_AVAILABLE_ENGINES`.

## 5. Tuning & Anti-CAPTCHA Strategy

### `ban_detector` (Engine Suspension Timing)
SearXNG includes a built-in `ban_detector` that detects rate limits (HTTP 429) and CAPTCHA challenge pages from search providers.
- **Default SearXNG Behavior**: When an engine is blocked, SearXNG suspends it for **3600 seconds (1 hour)**. If Google gets flagged once, your search loses Google results for 60 minutes.
- **Recommended Forage Setting**: Lower `suspend_time` to **300 seconds (5 minutes)** in your SearXNG `settings.yml`:
  ```yaml
  search:
    ban_detector:
      default:
        http_code: 429
        suspend_time: 300   # 5 minutes
  ```
  This allows temporarily throttled engines to auto-recover quickly while other engines (`bing`, `brave`, `yahoo`, `startpage`) handle intermediate queries.

### Fallback Engine Rotation
Public engines like Google, Startpage, and DuckDuckGo get heavily rate-limited by anti-bot systems. Add resilient engines like `yahoo`, `bing`, `brave`, and `qwant news` to `search.engines` in your Forage `config.yaml` so search always succeeds even when Google is CAPTCHA'd.

### Anti-Bot Protection
Forage's in-memory search cache (TTL 300s by default) shields SearXNG engines from being suspended by upstream search providers during repeated query loops.
