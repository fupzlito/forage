"""Forage: self-hosted web search & extract service for Hermes and OpenWebUI.

Provides /search (SearXNG), /extract (hybrid static -> browser), and full
MCP (Model Context Protocol) & OpenAI API tool call compatibility.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from . import __version__
from .auth import extract_bearer, key_is_valid, load_api_keys
from .browser import BrowserPool
from .cache import TTLCache
from .config import load_config
from .extract import extract_url
from .mcp import mcp_router
from .searxng import search_searxng

config = load_config()

logging.basicConfig(
    level=getattr(logging, config.server.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("forage")

search_cache = TTLCache(max_entries=config.cache.max_entries)
extract_cache = TTLCache(max_entries=config.cache.max_entries)
browser_pool = BrowserPool(config.browser, user_agent=config.extract.browser_user_agent)

api_keys = load_api_keys()
bearer_scheme = HTTPBearer(auto_error=False)


def require_auth(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    """Reject unauthenticated requests when auth.enabled is true."""
    cfg = getattr(request.app.state, "config", config)
    if not cfg.auth.enabled:
        return
    keys = load_api_keys() or api_keys
    token = credentials.credentials if credentials else None
    if not token:
        token = request.headers.get("x-api-key") or request.headers.get("api-key")
    if not token:
        token = (
            request.query_params.get("api_key")
            or request.query_params.get("token")
            or request.query_params.get("key")
            or request.query_params.get("auth")
        )
    if not key_is_valid(token, keys):
        raise HTTPException(status_code=401, detail="Unauthorized")


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.config = config
    app.state.browser_pool = browser_pool
    await browser_pool.start()
    yield
    await browser_pool.stop()


app = FastAPI(
    title="Forage",
    version=__version__,
    description="Self-hosted web search & extract service for OpenWebUI and Hermes.",
    lifespan=lifespan,
)

app.include_router(mcp_router)


class SearchRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="The search query string (keywords or question). Be specific and concise.",
    )
    limit: Optional[int] = Field(
        default=None,
        ge=1,
        le=50,
        description=f"Number of search results to return (1 to 50, default {config.search.default_limit}).",
    )
    language: Optional[str] = Field(
        default=None,
        max_length=20,
        description="Optional language code for search results (e.g. 'en-US', 'pt-BR', 'es', 'de').",
    )
    engines: Optional[List[str]] = Field(
        default=None,
        description=(
            f"Optional list of search engines to query. Available engines: {', '.join(config.search.available_engines)}. "
            "Engine name aliases (e.g. 'ddg', 'google_search') will be auto-mapped or filtered gracefully."
        ),
    )


class ExtractRequest(BaseModel):
    urls: List[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="List of HTTP/HTTPS URLs to fetch and extract content from (1 to 20 URLs).",
    )
    formats: Optional[List[str]] = Field(
        default=None,
        max_length=5,
        description="Desired output formats: ['markdown'] (default) or ['html'].",
    )
    only_main_content: bool = Field(
        default=True,
        description="If true (default), strips headers, footers, ads, and navigation for clean article text. Set to false to extract full page content including comments and sidebars.",
    )
    force_render: bool = Field(
        default=False,
        description="Force full headless browser rendering (Chromium) for JavaScript SPAs, dynamic sites, or when static fetch returns incomplete content.",
    )
    wait_for: Optional[str] = Field(
        default=None,
        max_length=200,
        description="Optional CSS selector or delay in seconds to wait for before extracting page content (browser mode only).",
    )
    timeout: Optional[int] = Field(
        default=None,
        ge=1,
        le=120,
        description="Maximum extraction timeout per URL in seconds (1 to 120).",
    )
    engine: Optional[str] = Field(
        default=None,
        pattern="^(trafilatura|readability)$",
        description="Extraction engine: 'trafilatura' (default, fast main-content markdown parser) or 'readability' (Mozilla Readability.js + markdownify, recommended for e-commerce buyboxes, forums, and complex page layouts).",
    )
    max_chars: Optional[int] = Field(
        default=None,
        ge=500,
        le=500000,
        description="Optional maximum characters to return per URL. Truncates long pages to save context.",
    )
    stream: bool = Field(
        default=False,
        description="Stream extraction results via Server-Sent Events (SSE) as each URL finishes.",
    )


def _search_cache_key(req: SearchRequest) -> str:
    engines = ",".join(sorted(req.engines)) if req.engines else ""
    eff_limit = req.limit if req.limit is not None else config.search.default_limit
    return f"search:{req.query}|{eff_limit}|{req.language or ''}|{engines}"


def _extract_cache_key(urls: List[str], force_render: bool, wait_for: Optional[str], fmt: str, engine: Optional[str], only_main_content: bool = True) -> str:
    return f"extract:{','.join(urls)}|{force_render}|{wait_for or ''}|{fmt}|{engine or ''}|{only_main_content}"


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title="Forage API",
        version=__version__,
        description="Self-hosted web search & extract service for OpenWebUI and LLM harnesses.",
        routes=app.routes,
    )
    paths = openapi_schema.get("paths", {})
    components = openapi_schema.get("components", {})
    schemas = components.get("schemas", {})

    from .mcp import get_tool_definitions

    tools_def = get_tool_definitions(config)
    search_name = config.tools.search_name
    extract_name = config.tools.extract_name

    search_def = next((t for t in tools_def if t["name"] == search_name), None)
    extract_def = next((t for t in tools_def if t["name"] == extract_name), None)

    if "/search" in paths and "post" in paths["/search"]:
        paths["/search"]["post"]["operationId"] = search_name
        paths["/search"]["post"]["summary"] = "Web Search"
        if search_def:
            paths["/search"]["post"]["description"] = search_def["description"]
        if search_def and "SearchRequest" in schemas:
            for prop_name, prop_spec in search_def["inputSchema"].get("properties", {}).items():
                if prop_name in schemas["SearchRequest"].get("properties", {}):
                    if "description" in prop_spec:
                        schemas["SearchRequest"]["properties"][prop_name]["description"] = prop_spec["description"]

    if "/extract" in paths and "post" in paths["/extract"]:
        paths["/extract"]["post"]["operationId"] = extract_name
        paths["/extract"]["post"]["summary"] = "Web Extract"
        if extract_def:
            paths["/extract"]["post"]["description"] = extract_def["description"]
        if extract_def and "ExtractRequest" in schemas:
            for prop_name, prop_spec in extract_def["inputSchema"].get("properties", {}).items():
                if prop_name in schemas["ExtractRequest"].get("properties", {}):
                    if "description" in prop_spec:
                        schemas["ExtractRequest"]["properties"][prop_name]["description"] = prop_spec["description"]

    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi


@app.get("/")
@app.post("/")
async def root() -> dict:
    """Root status probe for tool server and proxy compatibility."""
    return {
        "status": "ok",
        "service": "forage",
        "version": __version__,
        "tools": [config.tools.search_name, config.tools.extract_name],
    }


@app.get("/health")
async def health() -> dict:
    """Liveness & readiness probe with SearXNG backend heartbeat."""
    import time
    import httpx

    searxng_info: Dict[str, Any] = {
        "url": config.search.searxng_url,
        "status": "unknown",
    }
    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(f"{config.search.searxng_url.rstrip('/')}/healthz")
            latency_ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code == 200:
                searxng_info.update({"status": "connected", "latency_ms": latency_ms})
            else:
                searxng_info.update({"status": "degraded", "http_status": resp.status_code, "latency_ms": latency_ms})
    except Exception as exc:
        searxng_info.update({"status": "unreachable", "error": str(exc)})

    return {
        "status": "ok",
        "service": "forage",
        "version": __version__,
        "config_source": config.source_path,
        "browser_engine": config.browser.engine,
        "searxng": searxng_info,
        "tools": {
            "search_name": config.tools.search_name,
            "extract_name": config.tools.extract_name,
        },
        "search": {
            "available_engines": list(config.search.available_engines),
            "default_engines": list(config.search.engines),
        },
        "cache": {
            "enabled": config.cache.enabled,
            "max_entries": config.cache.max_entries,
            "search": {
                "enabled": config.cache.search.enabled,
                "ttl": config.cache.search.ttl,
            },
            "extract": {
                "enabled": config.cache.extract.enabled,
                "ttl": config.cache.extract.ttl,
            },
        },
    }


@app.post("/search")
async def search(
    req: SearchRequest,
    request: Request,
    cache_control: Optional[str] = Header(default=None),
    _auth: None = Depends(require_auth),
) -> JSONResponse:
    """Search via SearXNG, normalized to the web-search envelope with sources & citations."""
    bypass = bool(cache_control and "no-cache" in cache_control.lower())
    cache_enabled = config.cache.enabled and config.cache.search.enabled and not bypass

    key = _search_cache_key(req)
    if cache_enabled:
        cached = search_cache.get(key)
        if cached is not None:
            return JSONResponse(content=cached, headers={"X-Forage-Cache": "hit"})

    eff_limit = req.limit if req.limit is not None else config.search.default_limit
    result = search_searxng(
        config,
        query=req.query,
        limit=eff_limit,
        language=req.language,
        engines=req.engines,
    )

    if cache_enabled and result.get("success"):
        search_cache.set(key, result, ttl=config.cache.search.ttl)

    header = "miss" if cache_enabled else ("bypass" if bypass else "disabled")
    return JSONResponse(content=result, headers={"X-Forage-Cache": header})


@app.post("/extract")
async def extract(
    req: ExtractRequest,
    request: Request,
    accept: Optional[str] = Header(default=None),
    cache_control: Optional[str] = Header(default=None),
    _auth: None = Depends(require_auth),
) -> Response:
    """Extract URLs using the hybrid strategy (static -> browser fallback)."""
    import json
    from fastapi.responses import StreamingResponse

    if config.extract.require_max_chars and req.max_chars is None:
        raise HTTPException(
            status_code=422,
            detail=f"Missing required parameter 'max_chars'. Specify a character limit per URL (between 500 and {config.extract.max_content_chars:,}).",
        )

    bypass = bool(cache_control and "no-cache" in cache_control.lower())
    cache_enabled = config.cache.enabled and config.cache.extract.enabled and not bypass
    is_stream = req.stream or bool(accept and "text/event-stream" in accept.lower())

    fmt = "markdown"
    if req.formats:
        if "html" in req.formats:
            fmt = "html"
        elif "raw_html" in req.formats:
            fmt = "html"

    def _truncate_result(res: Dict[str, Any], max_chars: Optional[int]) -> Dict[str, Any]:
        if not max_chars or "content" not in res:
            return res
        copied = dict(res)
        full_len = len(copied["content"])
        if full_len > max_chars:
            copied["content"] = copied["content"][:max_chars] + f"\n\n[TRUNCATED at {max_chars:,} of {full_len:,} chars]"
            if "raw_content" in copied:
                copied["raw_content"] = copied["raw_content"][:max_chars]
        return copied

    async def _extract_one_full(url: str, pos: int) -> Dict[str, Any]:
        try:
            return await extract_url(
                config,
                browser_pool,
                url,
                position=pos,
                force_render=req.force_render,
                wait_for=req.wait_for,
                output_format=fmt,
                only_main_content=req.only_main_content,
                timeout=req.timeout,
                engine=req.engine,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Extract failed for %s", url)
            return {"url": url, "error": str(exc)}

    if is_stream:
        async def _stream_events():
            tasks = [asyncio.create_task(_extract_one_full(u, idx + 1)) for idx, u in enumerate(req.urls)]
            for coro in asyncio.as_completed(tasks):
                res = await coro
                truncated = _truncate_result(res, req.max_chars)
                yield f"data: {json.dumps(truncated, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(_stream_events(), media_type="text/event-stream")

    key = _extract_cache_key(req.urls, req.force_render, req.wait_for, fmt, req.engine, req.only_main_content)
    if cache_enabled:
        cached = extract_cache.get(key)
        if cached is not None and isinstance(cached, dict):
            truncated_data = [_truncate_result(r, req.max_chars) for r in cached.get("data", [])]
            return JSONResponse(content={"success": True, "data": truncated_data}, headers={"X-Forage-Cache": "hit"})

    full_results = await asyncio.gather(*(_extract_one_full(u, idx + 1) for idx, u in enumerate(req.urls)))

    if cache_enabled:
        all_ok = all("error" not in r for r in full_results)
        if all_ok:
            extract_cache.set(key, {"success": True, "data": full_results}, ttl=config.cache.extract.ttl)

    truncated_data = [_truncate_result(r, req.max_chars) for r in full_results]
    payload = {"success": True, "data": truncated_data}
    header = "miss" if cache_enabled else ("bypass" if bypass else "disabled")
    return JSONResponse(content=payload, headers={"X-Forage-Cache": header})


@app.post("/admin/cache/purge")
async def purge_cache(_auth: None = Depends(require_auth)) -> dict:
    """Clear the in-memory caches (search + extract)."""
    cleared = search_cache.clear() + extract_cache.clear()
    return {"cleared": cleared}


if __name__ == "__main__":
    import uvicorn

    logger.info(
        "Starting Forage %s on %s:%s (config: %s)",
        __version__,
        config.server.host,
        config.server.port,
        config.source_path,
    )
    uvicorn.run(
        "app.main:app",
        host=config.server.host,
        port=config.server.port,
        workers=config.server.workers,
        log_level=config.server.log_level,
    )
