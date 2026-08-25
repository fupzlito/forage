"""MCP (Model Context Protocol) & OpenAI API Tools Handler for Forage.

Implements:
1. MCP Protocol over HTTP POST (/mcp) and SSE (/mcp/sse, /mcp/messages).
2. OpenAI-compatible tools endpoints (/v1/tools, /v1/tools/call).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .auth import key_is_valid, load_api_keys
from .browser import BrowserPool
from .config import ForageConfig
from .extract import extract_url
from .searxng import search_searxng

logger = logging.getLogger("forage.mcp")

mcp_router = APIRouter()

# Active SSE sessions for MCP
_sse_sessions: Dict[str, asyncio.Queue] = {}


def _get_config_and_pool(request: Request):
    """Retrieve config and browser_pool from app.state or fallback to main module globals."""
    cfg = getattr(request.app.state, "config", None)
    pool = getattr(request.app.state, "browser_pool", None)
    if cfg is None or pool is None:
        from .main import browser_pool as main_pool, config as main_config
        cfg = cfg or main_config
        pool = pool or main_pool
    return cfg, pool


def get_tool_definitions(config: ForageConfig) -> List[Dict[str, Any]]:
    """Return JSON schemas for MCP and OpenAI API tools."""
    from datetime import datetime, timezone
    from .prompts import render_prompt

    search_name = config.tools.search_name
    extract_name = config.tools.extract_name
    default_engines_str = ", ".join(config.search.engines)
    available_engines_str = ", ".join(config.search.available_engines)
    now_dt = datetime.now().astimezone()
    now_date = now_dt.strftime("%Y-%m-%d %H:%M %Z")
    year = str(now_dt.year)

    context = {
        "now_date": now_date,
        "year": year,
        "default_engines": default_engines_str,
        "available_engines": available_engines_str,
        "default_limit": config.search.default_limit,
        "max_content_chars": config.extract.max_content_chars,
        "citation_guidelines": config.prompts.citation_guidelines,
    }

    # Render reusable citation guidelines with context if template variables are inside it
    rendered_citations = render_prompt(config.prompts.citation_guidelines, context)
    context["citation_guidelines"] = rendered_citations

    search_desc = render_prompt(config.prompts.search_tool_description, context)
    extract_desc = render_prompt(config.prompts.extract_tool_description, context)

    search_params = config.prompts.search_params or {}
    extract_params = config.prompts.extract_params or {}

    search_schema = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": render_prompt(
                    search_params.get("query", "Search query terms. Keep queries focused on essential keywords."),
                    context,
                ),
            },
            "limit": {
                "type": "integer",
                "description": render_prompt(
                    search_params.get("limit", f"Number of search results to return (1 to 50, default {config.search.default_limit})."),
                    context,
                ),
                "default": config.search.default_limit,
                "minimum": 1,
                "maximum": config.search.max_limit,
            },
            "engines": {
                "type": "array",
                "items": {"type": "string"},
                "description": render_prompt(
                    search_params.get("engines", f"Optional list of specific engines to query. Default engines: [{default_engines_str}]. Available: [{available_engines_str}]."),
                    context,
                ),
            },
            "language": {
                "type": "string",
                "description": render_prompt(
                    search_params.get("language", "Optional language code for search results (e.g. 'en-US', 'pt-BR', 'es', 'de')."),
                    context,
                ),
            },
        },
        "required": ["query"],
    }

    extract_schema = {
        "type": "object",
        "properties": {
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": render_prompt(
                    extract_params.get("urls", "List of HTTP/HTTPS URLs to fetch and extract content from (1 to 20 URLs)."),
                    context,
                ),
            },
            "force_render": {
                "type": "boolean",
                "description": render_prompt(
                    extract_params.get("force_render", "Force full headless browser rendering (Chromium) for JavaScript SPAs or dynamic sites."),
                    context,
                ),
                "default": False,
            },
            "only_main_content": {
                "type": "boolean",
                "description": render_prompt(
                    extract_params.get("only_main_content", "If true (default), strips headers, footers, ads, and navigation for clean article text."),
                    context,
                ),
                "default": True,
            },
            "wait_for": {
                "type": "string",
                "description": render_prompt(
                    extract_params.get("wait_for", "Optional CSS selector or delay in seconds to wait for before extracting page DOM (browser mode only)."),
                    context,
                ),
            },
            "engine": {
                "type": "string",
                "enum": ["trafilatura", "readability"],
                "description": render_prompt(
                    extract_params.get("engine", "Extraction engine: 'trafilatura' (default) or 'readability' (Mozilla Readability.js + markdownify)."),
                    context,
                ),
                "default": "trafilatura",
            },
            "timeout": {
                "type": "integer",
                "description": render_prompt(
                    extract_params.get("timeout", "Maximum extraction timeout per URL in seconds (1 to 120)."),
                    context,
                ),
                "default": 30,
                "minimum": 1,
                "maximum": 120,
            },
            "max_chars": {
                "type": "integer",
                "description": render_prompt(
                    extract_params.get("max_chars", "Optional maximum characters to return per URL. Truncates long pages to save context."),
                    context,
                ),
                "minimum": 500,
                "maximum": config.extract.max_content_chars,
            },
            "formats": {
                "type": "array",
                "items": {"type": "string"},
                "description": render_prompt(
                    extract_params.get("formats", "Desired output format: ['markdown'] (default) or ['html']."),
                    context,
                ),
            },
        },
        "required": ["urls", "max_chars"] if getattr(config.extract, "require_max_chars", False) else ["urls"],
    }

    return [
        {
            "name": search_name,
            "description": search_desc,
            "inputSchema": search_schema,
            "parameters": search_schema,  # OpenAI compatibility
        },
        {
            "name": extract_name,
            "description": extract_desc,
            "inputSchema": extract_schema,
            "parameters": extract_schema,  # OpenAI compatibility
        },
    ]


async def execute_tool_call(
    name: str,
    arguments: Dict[str, Any],
    config: ForageConfig,
    browser_pool: BrowserPool,
) -> Dict[str, Any]:
    """Execute a tool call by name and return a structured response dictionary."""
    search_name = config.tools.search_name
    extract_name = config.tools.extract_name

    if name == search_name or name == "web_search":
        query = str(arguments.get("query", "")).strip()
        if not query:
            return {"error": "Missing required parameter 'query'"}
        raw_limit = arguments.get("limit")
        default_lim = config.search.default_limit
        max_lim = config.search.max_limit
        if raw_limit is not None:
            try:
                limit = max(1, min(int(raw_limit), max_lim))
            except (ValueError, TypeError):
                limit = default_lim
        else:
            limit = default_lim
        language = arguments.get("language")
        engines = arguments.get("engines")
        if isinstance(engines, str):
            engines = [e.strip() for e in engines.split(",") if e.strip()]

        res = search_searxng(config, query=query, limit=limit, language=language, engines=engines)
        if not res.get("success"):
            return {"error": res.get("error", "Search failed")}

        results = res.get("results", [])
        warning = res.get("warning")
        engines_ok = res.get("successful_engines", [])

        header_parts = [f'SEARCH RESULTS for "{query}" | {res.get("searched_at", res.get("timestamp", ""))}']
        if engines_ok:
            header_parts.append(f"Engines: {', '.join(engines_ok)}")

        include_fav = config.search.include_favicon if getattr(config.search, "include_favicon", None) is not None else getattr(config.tools, "include_favicon", False)

        formatted_lines = [" | ".join(header_parts), "---"]
        for r in results:
            block = (
                f"[{r['position']}] {r['domain']}\n"
                f"URL: {r['url']}\n"
                f"TITLE: {r['title']}\n"
                f"SNIPPET: {r['snippet']}\n"
                f"CITE AS: {r['citation']}"
            )
            if include_fav and r.get('favicon'):
                block += f"\nFAVICON: {r['favicon']}"
            formatted_lines.append(block)

        output_text = "\n\n".join(formatted_lines)
        if warning:
            output_text = f"⚠️ {warning}\n\n" + output_text

        return {
            "results": results,
            "formatted_text": output_text,
            "warning": warning,
            "successful_engines": res.get("successful_engines"),
            "unresponsive_engines": res.get("unresponsive_engines"),
            "used_engines": res.get("used_engines"),
        }

    elif name == extract_name or name == "web_extract":
        raw_urls = arguments.get("urls") or arguments.get("url")
        if isinstance(raw_urls, str):
            urls = [raw_urls]
        elif isinstance(raw_urls, list):
            urls = [str(u) for u in raw_urls if u]
        else:
            urls = []

        if not urls:
            return {"error": "Missing required parameter 'urls'"}

        urls = urls[:20]
        force_render = bool(arguments.get("force_render", False))
        only_main_content = bool(arguments.get("only_main_content", True))
        wait_for = arguments.get("wait_for")
        timeout = arguments.get("timeout")
        engine = arguments.get("engine")
        max_chars = arguments.get("max_chars")
        formats = arguments.get("formats")
        fmt = "markdown"
        if formats and ("html" in formats or "raw_html" in formats):
            fmt = "html"

        async def _one(u: str, pos: int) -> Dict[str, Any]:
            try:
                result = await extract_url(
                    config,
                    browser_pool,
                    u,
                    position=pos,
                    force_render=force_render,
                    wait_for=wait_for,
                    output_format=fmt,
                    only_main_content=only_main_content,
                    timeout=timeout,
                    engine=engine,
                )
                # Apply per-request max_chars truncation
                if max_chars and "content" in result:
                    full_len = len(result["content"])
                    if full_len > max_chars:
                        result["content"] = result["content"][:max_chars] + f"\n\n[TRUNCATED at {max_chars:,} of {full_len:,} chars]"
                        if "raw_content" in result:
                            result["raw_content"] = result["raw_content"][:max_chars]
                return result
            except Exception as exc:  # noqa: BLE001
                error_msg = str(exc)
                error_type = "network"
                lower = error_msg.lower()
                if "anti-bot" in lower or "challenge" in lower or "cloudflare" in lower:
                    error_type = "blocked"
                elif "timeout" in lower or "timed out" in lower:
                    error_type = "timeout"
                elif "no content" in lower or "empty" in lower:
                    error_type = "empty"
                return {"url": u, "error": error_msg, "error_type": error_type}

        extracted = await asyncio.gather(*(_one(u, idx + 1) for idx, u in enumerate(urls)))
        sources = [
            {
                "url": r.get("url"),
                "title": r.get("title", ""),
                "citation": r.get("citation", f"[Source]({r.get('url')})"),
            }
            for r in extracted if "error" not in r
        ]

        formatted_blocks = []
        ok_count = sum(1 for r in extracted if "error" not in r)
        from datetime import datetime as _dt
        _now = _dt.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
        formatted_blocks.append(f"EXTRACTED {ok_count} of {len(urls)} URLs | {_now}\n---")

        include_fav = config.extract.include_favicon if getattr(config.extract, "include_favicon", None) is not None else getattr(config.tools, "include_favicon", False)

        for idx, r in enumerate(extracted):
            if "error" in r:
                formatted_blocks.append(
                    f"[{idx+1}] {r['url']}\n"
                    f"ERROR: {r['error']}"
                )
            else:
                t = r.get("title", "")
                dom = r.get("domain", "")
                u = r.get("url", "")
                c = r.get("content", "")
                m = r.get("method", "unknown")
                cit = r.get("citation", f"[{dom}]({u})")
                block = (
                    f"[{idx+1}] {dom} | {m}\n"
                    f"URL: {u}\n"
                    f"TITLE: {t}\n"
                    f"CITE AS: {cit}"
                )
                if include_fav and r.get('favicon'):
                    block += f"\nFAVICON: {r['favicon']}"
                block += f"\n\n{c}"
                formatted_blocks.append(block)

        return {
            "results": list(extracted),
            "sources": sources,
            "formatted_text": "\n\n---\n\n".join(formatted_blocks),
        }

    else:
        return {"error": f"Unknown tool: '{name}'. Available tools: '{search_name}', '{extract_name}'."}


async def process_mcp_rpc(
    payload: Dict[str, Any],
    config: ForageConfig,
    browser_pool: BrowserPool,
) -> Optional[Dict[str, Any]]:
    """Process an MCP JSON-RPC request and return the JSON-RPC response dict (or None for notifications)."""
    msg_id = payload.get("id")
    method = payload.get("method", "")
    params = payload.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "forage",
                    "version": "0.9.0",
                },
            },
        }

    elif method == "notifications/initialized":
        # Client acknowledgement, no RPC response needed
        return None

    elif method == "ping":
        return {"jsonrpc": "2.0", "id": msg_id, "result": {}}

    elif method == "tools/list":
        tools_def = get_tool_definitions(config)
        mcp_tools = [
            {
                "name": t["name"],
                "description": t["description"],
                "inputSchema": t["inputSchema"],
            }
            for t in tools_def
        ]
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": mcp_tools},
        }

    elif method == "tools/call":
        tool_name = params.get("name", "")
        arguments = params.get("arguments", {})
        res = await execute_tool_call(tool_name, arguments, config, browser_pool)

        if "error" in res:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": f"Error executing tool '{tool_name}': {res['error']}"}],
                    "isError": True,
                },
            }

        formatted_text = res.get("formatted_text", json.dumps(res, indent=2))
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": formatted_text,
                    }
                ],
                "isError": False,
            },
        }

    else:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": -32601,
                "message": f"Method '{method}' not found",
            },
        }


# --- API Routes ---

@mcp_router.post("/mcp")
async def mcp_post(
    request: Request,
) -> JSONResponse:
    """Standard HTTP POST JSON-RPC endpoint for MCP clients."""
    config, browser_pool = _get_config_and_pool(request)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    if isinstance(body, list):
        # Batch RPC requests
        responses = []
        for item in body:
            resp = await process_mcp_rpc(item, config, browser_pool)
            if resp:
                responses.append(resp)
        return JSONResponse(content=responses)
    else:
        resp = await process_mcp_rpc(body, config, browser_pool)
        if resp is None:
            return JSONResponse(status_code=202, content={"status": "accepted"})
        return JSONResponse(content=resp)


@mcp_router.get("/mcp/sse")
async def mcp_sse(request: Request) -> StreamingResponse:
    """SSE endpoint for OpenWebUI MCP connection."""
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _sse_sessions[session_id] = queue

    async def event_generator():
        try:
            # First message: tell client where to POST messages
            yield f"event: endpoint\ndata: /mcp/messages?session_id={session_id}\n\n"
            while True:
                data = await queue.get()
                yield f"event: message\ndata: {json.dumps(data)}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            _sse_sessions.pop(session_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@mcp_router.post("/mcp/messages")
async def mcp_messages(
    request: Request,
    session_id: Optional[str] = None,
) -> JSONResponse:
    """Handle incoming JSON-RPC messages from an SSE client."""
    config, browser_pool = _get_config_and_pool(request)

    sid = session_id or request.query_params.get("session_id")
    if not sid or sid not in _sse_sessions:
        raise HTTPException(status_code=404, detail="Invalid or expired SSE session")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    resp = await process_mcp_rpc(body, config, browser_pool)
    if resp:
        await _sse_sessions[sid].put(resp)

    return JSONResponse(content={"status": "accepted"})


@mcp_router.get("/v1/tools")
async def v1_tools_list(request: Request) -> dict:
    """OpenAI API compatible tool definitions."""
    config, _ = _get_config_and_pool(request)
    tools_def = get_tool_definitions(config)
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t["description"],
                "parameters": t["inputSchema"],
            },
        }
        for t in tools_def
    ]
    return {"tools": openai_tools}


@mcp_router.post("/v1/tools/call")
async def v1_tools_call(
    request: Request,
    _auth: None = Depends(lambda: None),
) -> JSONResponse:
    """Execute a single OpenAI-compatible function tool call."""
    config, browser_pool = _get_config_and_pool(request)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    name = body.get("name") or (body.get("function", {}) if isinstance(body.get("function"), dict) else {}).get("name")
    arguments = body.get("arguments") or (body.get("function", {}) if isinstance(body.get("function"), dict) else {}).get("arguments")

    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except Exception:
            arguments = {}
    elif not isinstance(arguments, dict):
        arguments = {}

    if not name:
        raise HTTPException(status_code=400, detail="Missing tool name")

    result = await execute_tool_call(name, arguments, config, browser_pool)
    return JSONResponse(content={"success": "error" not in result, "result": result})


@mcp_router.get("/v1/models")
@mcp_router.get("/models")
async def get_v1_models(request: Request) -> JSONResponse:
    """OpenAI API compatible models listing endpoint."""
    config, _ = _get_config_and_pool(request)
    search_name = config.tools.search_name
    extract_name = config.tools.extract_name
    return JSONResponse(
        content={
            "object": "list",
            "data": [
                {
                    "id": search_name,
                    "object": "model",
                    "created": 1700000000,
                    "owned_by": "forage",
                    "permission": [],
                    "root": search_name,
                    "parent": None,
                },
                {
                    "id": extract_name,
                    "object": "model",
                    "created": 1700000000,
                    "owned_by": "forage",
                    "permission": [],
                    "root": extract_name,
                    "parent": None,
                },
            ],
        }
    )


@mcp_router.post("/v1/chat/completions")
async def post_v1_chat_completions(request: Request) -> JSONResponse:
    """OpenAI API compatible chat completion endpoint for tool invocation."""
    config, browser_pool = _get_config_and_pool(request)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    messages = body.get("messages", [])
    model_requested = body.get("model", config.tools.search_name)

    query = ""
    for msg in reversed(messages):
        content = msg.get("content", "")
        if isinstance(content, str) and content.strip():
            query = content.strip()
            break

    search_name = config.tools.search_name
    extract_name = config.tools.extract_name

    import re as _re
    urls = _re.findall(r'https?://[^\s>"]+', query)

    if model_requested == extract_name or (urls and model_requested != search_name):
        tool_name = extract_name
        args: Dict[str, Any] = {"urls": urls if urls else [query]}
    else:
        tool_name = search_name
        args = {"query": query or "test"}

    result = await execute_tool_call(tool_name, args, config, browser_pool)
    formatted_text = result.get("formatted_text", json.dumps(result, indent=2))

    return JSONResponse(
        content={
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": 1700000000,
            "model": model_requested,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": formatted_text,
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
        }
    )

