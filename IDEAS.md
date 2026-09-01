# Forage — Cookbook / Behavior Ideas (current-state audit, 2026-08-28)

Scope: design/behavior ideas grounded in the **current code** at `de48ddb`, plus the fragile/undocumented parts of three subsystems the team flagged: the `youtube_search` tool, SearXNG engine auto-discovery, and the Reddit 3-tier pipeline. Fills the MASTER_CODE_REVIEW.md §4 "open issues" perspective from the *product/behavior* side.

---

## 1. `youtube_search` tool — native-only activation

### What the code actually does today
- The tool is active **if and only if** `config.youtube.api_key` is set (env `FORAGE_YOUTUBE_API_KEY` / `YOUTUBE_API_KEY`). `YouTubeConfig.enabled` is a property returning `bool(api_key)`.
- Registration checks `config.youtube.enabled` in `mcp.py` (tools list, declared-engine list, and the unknown-tool error) and `main.py` (`root()` tools list).
- There is a single execution path: the **direct** YouTube Data API v3 (`search_youtube_direct` + `resolve_handle_to_channel_id`). There is **no keyless / SearXNG fallback** — without a key the tool is not registered and any call returns a clear "key required" error.
- The response carries only `success`, `searched_at`, `results`, and `warning`. The SearXNG-flavored `requested_engines` / `returned_engines` / `unresponsive_engines` fields (which described which search engines responded) are gone — they had no meaning for a native-only tool.

### Proposed ideas
- **A. (Done) Native-only activation** — the SearXNG proxy fallback and the `enabled` / `searxng_engine` config knobs are removed; key presence is the sole gating condition.
- **B. (Done) Response bookkeeping** — the vestigial SearXNG engine fields are removed from the YouTube response; nothing downstream consumes them.
- **C. (Nice-to-have) Show the backend in tool output**, e.g. `"backend": "native"`, so the conversation can self-explain which path ran.

---

## 2. SearXNG engine auto-discovery — behavior + the fragile fallback

### What the code actually does today (`searxng.py:155–230`)
1. `get_live_available_engines` first honors `search.available_engines` if explicitly set (no discovery at all).
2. Otherwise it probes `GET /config` with a **2.0 s timeout**, only accepting engines that (a) are `enabled`, (b) belong to the `general` category (category-less engines count), and (c) are not translate/`currency` or media-suffix sub-scrapers (`NON_TEXT_SUFFIXES`).
3. Result is cached **300 s** (`_last_engine_fetch`).
4. **Fallback**: if the probe throws or times out, the function returns the hard-coded `DEFAULT_AVAILABLE_ENGINES` tuple (`searxng.py:148–152`) — a static list that can name engines your instance never had (e.g. `youtube`, `reddit`, `qwant news`) — or likewise when the probe "succeeds" but returns no engines. The static list and the live list can disagree about which engines are actually present.

So during (or after) a slow SearXNG cold start, Forage *tells the model* a static catalog that may name engines your instance never has (the tuple is `("google", "qwant", "qwant news", "brave", "bing", "startpage", "duckduckgo", "reddit", "wikipedia", "youtube", "github", "searxng", "yahoo", "wikidata")`, i.e. it asserts `reddit`/`wikidata`/`qwant news` whether or not they're registered); every call then yields the "ignored unknown engine(s)" warning + default-engine fallback. The failed probe is logged only at `logger.debug` (`searxng.py:186`), so the process is quiet while it's wrong.

### Proposed ideas
- **A. Document it** — a short "Engine auto-discovery & fallback" subsection: probe (2 s), 5-min cache, general-category filter, the static fallback list, and how to pin a manual catalog with `FORAGE_AVAILABLE_ENGINES` / `search.available_engines`.
- **B. Liveness hygiene** — warn-visible (not just debug) when the `/config` probe fails or returns empty; optionally negative-cache a failed probe for ~60 s so a query storm doesn't re-hammer the instance.
- **C. Cold-start awareness** — the SearXNG compose has no healthcheck today; document "wait for SearXNG to be ready (first engine `GET /` 200) before first Forage search", or bump the probe timeout to ~5 s.
- **D. (Optional) Per-call refresh** — let `POST /search` accept `fresh_engines: true` to bypass the 300 s cache when the model wants re-probe after a SearXNG restart.

---

## 3. Reddit 3-tier pipeline — efficiency & expectations

### What the code actually does today (`app/extract.py:643–806` + Tier 2 `:724–800` + fallback in `:1075–1111`)
- **Tier 1** – official `.json` (`?raw_json=1&limit=100&depth=10` for `/comments/`) with a Chrome UA + `Sec-Fetch-*` nav headers, 0.75 s throttle, and a 30 s cooldown on 403/429 (`_set_reddit_json_cooldown(30.0)`).
- **Tier 2** – Redlib/SafeReddit mirror with lean headers and a 4 s cap.
- **Tier 3** – stealth browser + Readability full comment tree.
- **The efficiency truth**: without authenticated cookies (`reddit_session`/`token_v2`), Reddit's JSON endpoint is *rate-limit- or auth-gated in the vast majority of cases* — 403/429 → cooldown → Tier 2 → if that's degraded too, Tier 3. With cookies, Tier 1 is sub-second and reliable. The code reflects this, but **nothing in the primary docs says "tier 1 needs cookies for consistent throughput; mirror/browser is the graceful degradation path"**. Also, there is currently no rate-limit reporting surface for *how* many Tier-1 attempts actually go through under the current call load.

### Proposed ideas
- **A. Document the expectation** — one paragraph in README "What's New" + a note under `extract.domain_overrides` in `docs/CONFIG.md`: *"Reddit JSON (tier 1) is most efficient with a logged-in session. Set `FORAGE_REDDIT_SESSION` / `FORAGE_REDDIT_TOKEN_V2` (see compose snippet), or rely on the mirror/browser fallback which trades ~sub-second for 5–30 s per page."*
- **B. Expose the tier used** — the result envelope already carries `method` (`web_eg` variants like `reddit+json`, `reddit+mirror`, `browser+solo`). Surface which tier produced the response in the formatted text (e.g. `[{position}] Reddit Thread (tier: 1 .json)`), so a model or user can see *why* it was slower. Low risk; `extract.py` already tags `method` per tier.
- **C. Search vs. link fetch** — see §4.

---

## 4. Splitting Reddit "search" from "link fetch" (or just search)

### Current state
- `web_extract` already handles Reddit *URLs* end-to-end (the three-tier pipeline, above), including `/r/<sub>/search?q=...` URLs, `/comments/<id>`, and user pages (see `extract.py:655–672` path matchers).
- There is **no Reddit search tool**; the only search-ish pattern is `web_search` using SearXNG's `reddit` engine when `/search?q=` URLs are hand-shaped. That is exactly the undreadable spot the `youtube_search` tool wanted to fix: mid-size models produce search-syntax URLs for Reddit all the time but rarely *call a dedicated* search tool.

### Ideas (ranked if you want to act)
1. **Just document Reddit URL search in `web_extract`** (cheapest, no new endpoint): add a tool-description nudge in `prompts.yaml` and one line in README: *"To fetch a Reddit thread or search, pass the URL to `web_extract` — e.g. `https://www.reddit.com/r/...` or `https://www.reddit.com/search?q=...`."*
2. **Dedicated `reddit_search` tool** (mirrors `youtube_search`'s routing): map `search: <query>` + optional `subreddit` param to the Reddit `.json` search endpoint (SearXNG's `reddit` engine or the `.json` search call at `/r/<sub>/search/.json`), races the same three-tier scrub pipeline as `web_extract`. Pros: handles the "model wants search, not URL" case cleanly; no API keys. Cons: one more surface to test; support for which a mid-size model picks the wrong tool is already real ("search" vs "extract" vs "fetch").
3. **Middle ground** — add a `reddit: {search_enabled: true}` config flag that only exposes `reddit_search` when an API/session cookie is configured (tier-1 can handle native JSON search in <1 s), keeping search for uncookie instances as a plain `web_extract` URL.

For now: **I'd stop at (1) if the goal is to reduce mis-calls without expanding surface** — that reuses already-working pipeline; but honestly that *might* be overkill to ship with a proper search tool. The max surface gain is in (2)/(3) because `youtube_search` already shows the model how to parse search-strategy queries.

---

## 5. Other current-state scraps (carried from MASTER_CODE_REVIEW §4/§5, O-…/F-…)

- `requirements.lock` — untracked & mispoken (contains `cptr`, `claude-agent-sdk`, …). Delete or clean-recreate.
- Git identity still `cptr <cptr@localhost>`.
- `origin/main` / `stable-checkpoint` were never advanced past `de48ddb`.
- `browser.py` single-retry heal (one-shot `_restart_scrapling_session`) under burst load.
- No direct tests for `browser.py` render paths.
- R-3 (`/extract` REST duplication) still open.
- SSE `/mcp/sse` + `/mcp/messages` queue branch untested.

---

## Action order (if you want a first pass)

1. **Fix the `youtube` name mismatch** (IDEA 1B): ~3 lines in `app/youtube.py`. Turns the keyless proxy behavior from silent-web to reliably-YouTube (when SearXNG has a youtube engine, whatever your name is). Do this before the availability gating so the tool is honest.
   - (Alternatively, wait: maybe `searxng.py` should treat `youtube_api` as a family alias like `duckduckgo_search` → `duckduckgo`; that also fixes *web_search* when a model says `engines: ["youtube-api"]`.)
2. **Docs/config sync (that pass):** README tool list + env-var table; CONFIG.md env-var rows (`FORAGE_YOUTUBE_NAME` / `DEFAULT_LIMIT` / `MAX_LIMIT` any missing `FORAGE_REDDIT_TOKEN_V2` comment); SEARXNG.md note on the name mismatch + cold-start probe fragility; config.example.yaml add `youtube_name` under `tools:` + note under `youtube.api_key` (the no-key fallback behavior).
3. **README + CONFIG.md Reddit-tier note** (IDEA 3A) — one paragraph + one config.example line-comment.
4. **IDEA 2 docs** + small `logger.warning` when the probe falls back (cheap telemetry, no behavior change).
5. **Decide on §4** (docs-only vs `reddit_search` tool) and schedule accordingly.
6. Then F-1..F-10 from MASTER_CODE_REVIEW §5 (code hygiene), knowing some (`logger`, lock, git id) are one-liners.