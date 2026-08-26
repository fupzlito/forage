"""Configuration loading for Forage.

Resolution order:
  1. Built-in defaults (this module)
  2. YAML file (FORAGE_CONFIG env var, default /etc/forage/config.yaml)
  3. Env vars (secrets / overrides only)

The YAML file deep-merges over the defaults, so partial files are fine.
Secrets NEVER belong in the YAML; they come from the environment.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields
from typing import Any, Dict, Optional

import yaml

DEFAULT_CONFIG_PATH = "/etc/forage/config.yaml"

DEFAULTS: Dict[str, Any] = {
    "server": {
        "host": "0.0.0.0",
        "port": 3672,
        "workers": 2,
        "log_level": "info",
    },
    "cache": {
        "enabled": True,
        "max_entries": 500,
        "search": {"enabled": True, "ttl": 300},
        "extract": {"enabled": False, "ttl": 3600},
    },
    "tools": {
        "search_name": "web_search",
        "extract_name": "web_extract",
        "include_favicon": False,
    },
    "search": {
        "searxng_url": "http://searxng:8080",
        "default_lang": "en-US",
        "engines": ["google", "bing", "brave", "duckduckgo", "qwant"],
        "available_engines": None,  # None = auto-discover live text/web engines from SearXNG GET /config
        "timeout": 15,
        "default_limit": 10,
        "max_limit": 50,
        "max_snippet_chars": 350,
        "max_total_snippet_chars": 3000,
        "citation_style": "site_name",
        "include_favicon": None,
    },
    "extract": {
        "timeout": 30,
        "max_content_chars": 100000,
        "only_main_content": True,
        "engine": "trafilatura",   # "trafilatura" (default) or "readability" (Readability.js + markdownify)
        "user_agent": "ForageBot/0.1 (+https://github.com/aldemaroc/forage)",
        "browser_user_agent": None,
        "respect_robots": False,
        "force_render": False,
        "wait_for": None,
        "min_content_chars": 200,
        "raw_content_markdown": True,
        "prefer_markdown": True,   # negotiate Accept: text/markdown; use native markdown when the server serves it
        "domain_overrides": {},
        "citation_style": "site_name",
        "include_favicon": None,
        "require_max_chars": False,
    },
    "browser": {
        "engine": "scrapling",
        "cdp_url": "",       # engine=obscura: CDP endpoint of the Obscura server (http://host:port or ws://host:port)
        "min_idle": 1,
        "max_instances": 5,
        "idle_timeout": 60,
        "headless": True,
        "launch_timeout": 30,
        "stealth": True,
        "network_idle_timeout": 5,
        "scroll_steps": 0,
        "challenge_timeout": 15,
        "solve_cloudflare": False,
        "fallback_solver": True,
    },
    "auth": {"enabled": False},
    "prompts": {
        "prompts_path": None,
        "citation_guidelines": None,
        "search_tool_description": None,
        "extract_tool_description": None,
        "search_params": None,
        "extract_params": None,
    },
}

logger = logging.getLogger(__name__)


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge ``override`` into ``base`` (new dict)."""
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _filter_dataclass_dict(cls: type, data: Dict[str, Any]) -> Dict[str, Any]:
    """Filter dict keys to only include valid fields defined on the dataclass."""
    valid_fields = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in valid_fields}


@dataclass(frozen=True)
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 3672
    workers: int = 2
    log_level: str = "info"


@dataclass(frozen=True)
class CacheOpConfig:
    enabled: bool = True
    ttl: int = 300


@dataclass(frozen=True)
class CacheConfig:
    enabled: bool = True
    max_entries: int = 500
    search: CacheOpConfig = field(default_factory=lambda: CacheOpConfig(True, 300))
    extract: CacheOpConfig = field(default_factory=lambda: CacheOpConfig(False, 3600))


@dataclass(frozen=True)
class ToolsConfig:
    search_name: str = "web_search"
    extract_name: str = "web_extract"
    include_favicon: bool = False


@dataclass(frozen=True)
class SearchConfig:
    searxng_url: str = "http://searxng:8080"
    default_lang: str = "en-US"
    engines: tuple = ("google", "bing", "brave", "duckduckgo", "qwant")
    available_engines: Optional[tuple] = None
    timeout: int = 15
    default_limit: int = 10
    max_limit: int = 50
    max_snippet_chars: int = 350
    max_total_snippet_chars: int = 3000
    citation_style: str = "site_name"
    include_favicon: Optional[bool] = None

    @property
    def default_engines(self) -> tuple:
        return self.engines


@dataclass(frozen=True)
class YouTubeConfig:
    enabled: bool = True
    api_key: Optional[str] = None
    default_limit: int = 20
    max_limit: int = 50


@dataclass(frozen=True)
class DomainOverride:
    """Per-domain extraction overrides.

    Fields are Optional so an override can set only what it cares about.
    ``pattern`` is the YAML key it was declared under (e.g. ``amazon.*`` or
    ``reddit.com/r/``); it is carried here so url_rewrite knows the match.
    """

    pattern: str
    force_render: Optional[bool] = None
    full_text: Optional[bool] = None
    engine: Optional[str] = None    # "trafilatura" (default) or "readability"
    wait_for: Optional[str] = None
    url_rewrite: Optional[str] = None
    scroll: Optional[bool] = None
    timeout: Optional[int] = None
    network_idle_timeout: Optional[int] = None
    challenge_timeout: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)
    cookies: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractConfig:
    timeout: int = 30
    max_content_chars: int = 100000
    only_main_content: bool = True
    engine: str = "trafilatura"   # "trafilatura" (default) or "readability"
    user_agent: str = "ForageBot/0.1 (+https://github.com/aldemaroc/forage)"
    browser_user_agent: Optional[str] = None
    respect_robots: bool = False
    force_render: bool = False
    wait_for: Optional[str] = None
    min_content_chars: int = 200
    raw_content_markdown: bool = True
    prefer_markdown: bool = True
    domain_overrides: tuple = ()
    citation_style: str = "site_name"
    include_favicon: Optional[bool] = None
    require_max_chars: bool = False


@dataclass(frozen=True)
class BrowserConfig:
    engine: str = "scrapling"  # "scrapling" (default), "playwright", "patchright" or "obscura"
    cdp_url: str = ""  # engine=obscura: CDP endpoint (http://host:port or ws://host:port)
    min_idle: int = 1
    max_instances: int = 5
    idle_timeout: int = 60
    headless: bool = True
    launch_timeout: int = 30
    stealth: bool = True
    network_idle_timeout: int = 5
    scroll_steps: int = 0
    challenge_timeout: int = 15  # max seconds to wait for an anti-bot challenge to auto-resolve (scrapling engine)
    solve_cloudflare: bool = False  # scrapling: use its built-in Cloudflare solver (adds ~5s/page) or the page_action poll (default)
    fallback_solver: bool = True  # on anti-bot failure, retry with scrapling + built-in solver as last resort


@dataclass(frozen=True)
class AuthConfig:
    enabled: bool = False


@dataclass(frozen=True)
class PromptsConfig:
    prompts_path: Optional[str] = None
    citation_guidelines: str = ""
    search_tool_description: str = ""
    extract_tool_description: str = ""
    youtube_tool_description: str = ""
    search_params: Dict[str, str] = field(default_factory=dict)
    extract_params: Dict[str, str] = field(default_factory=dict)
    youtube_params: Dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ForageConfig:
    server: ServerConfig
    cache: CacheConfig
    tools: ToolsConfig
    search: SearchConfig
    extract: ExtractConfig
    browser: BrowserConfig
    auth: AuthConfig
    prompts: PromptsConfig
    youtube: YouTubeConfig
    source_path: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any], source_path: str) -> "ForageConfig":
        server = _filter_dataclass_dict(ServerConfig, data.get("server", {}))
        cache = data.get("cache", {})
        tools = _filter_dataclass_dict(ToolsConfig, data.get("tools", {}))
        search_data = dict(data.get("search", {}))
        if "default_engines" in search_data and "engines" not in search_data:
            search_data["engines"] = search_data["default_engines"]
        search = _filter_dataclass_dict(SearchConfig, search_data)
        extract = _filter_dataclass_dict(ExtractConfig, data.get("extract", {}))
        browser = _filter_dataclass_dict(BrowserConfig, data.get("browser", {}))
        auth = _filter_dataclass_dict(AuthConfig, data.get("auth", {}))
        prompts = _filter_dataclass_dict(PromptsConfig, data.get("prompts", {}))
        youtube = _filter_dataclass_dict(YouTubeConfig, data.get("youtube", {}))

        if "engines" in search:
            search["engines"] = tuple(search["engines"])
        if "available_engines" in search and search["available_engines"] is not None:
            search["available_engines"] = tuple(search["available_engines"])

        if "domain_overrides" in extract:
            raw_overrides = extract.get("domain_overrides", {})
            if isinstance(raw_overrides, dict):
                extract["domain_overrides"] = tuple(
                    DomainOverride(pattern=pattern, **_filter_dataclass_dict(DomainOverride, over))
                    for pattern, over in raw_overrides.items()
                )

        cache_search = _filter_dataclass_dict(CacheOpConfig, cache.get("search", {}))
        cache_extract = _filter_dataclass_dict(CacheOpConfig, cache.get("extract", {}))

        return cls(
            server=ServerConfig(**server),
            cache=CacheConfig(
                enabled=cache.get("enabled", True),
                max_entries=cache.get("max_entries", 500),
                search=CacheOpConfig(**cache_search),
                extract=CacheOpConfig(**cache_extract),
            ),
            tools=ToolsConfig(**tools),
            search=SearchConfig(**search),
            extract=ExtractConfig(**extract),
            browser=BrowserConfig(**browser),
            auth=AuthConfig(**auth),
            prompts=PromptsConfig(**prompts),
            youtube=YouTubeConfig(**youtube),
            source_path=source_path,
        )

    def validate(self) -> None:
        """Raise ValueError on invalid configuration values."""
        if not (0 < self.server.port < 65536):
            raise ValueError(f"server.port invalid: {self.server.port}")
        if self.server.workers < 1:
            raise ValueError(f"server.workers must be >= 1: {self.server.workers}")
        if self.cache.max_entries < 1:
            raise ValueError(f"cache.max_entries must be >= 1: {self.cache.max_entries}")
        for name, op in (("search", self.cache.search), ("extract", self.cache.extract)):
            if op.ttl < 0:
                raise ValueError(f"cache.{name}.ttl must be >= 0: {op.ttl}")
        if self.browser.max_instances < 0 or self.browser.min_idle < 0:
            raise ValueError("browser pool sizes must be >= 0")
        if self.browser.scroll_steps < 0:
            raise ValueError("browser.scroll_steps must be >= 0")
        if self.browser.challenge_timeout < 0:
            raise ValueError("browser.challenge_timeout must be >= 0")
        if self.browser.solve_cloudflare and self.browser.engine != "scrapling":
            raise ValueError("browser.solve_cloudflare only applies to engine=scrapling")
        if self.browser.engine not in ("playwright", "patchright", "scrapling", "obscura"):
            raise ValueError(
                f"browser.engine invalid: {self.browser.engine!r} "
                "(use playwright, patchright, scrapling, or obscura)"
            )
        if self.browser.engine == "obscura" and not self.browser.cdp_url:
            raise ValueError("browser.engine=obscura requires browser.cdp_url (e.g. http://127.0.0.1:9223)")
        if self.extract.engine not in ("trafilatura", "readability"):
            raise ValueError(
                f"extract.engine invalid: {self.extract.engine!r} "
                "(use trafilatura or readability)"
            )
        if self.browser.min_idle > self.browser.max_instances and self.browser.max_instances > 0:
            raise ValueError("browser.min_idle cannot exceed browser.max_instances")
        for override in self.extract.domain_overrides:
            if not override.pattern:
                raise ValueError("domain_overrides entry must have a non-empty pattern")
            if override.url_rewrite is not None and "/" not in override.url_rewrite:
                raise ValueError(
                    f"domain_overrides[{override.pattern}]: url_rewrite must be "
                    f"'host[/path-prefix]' (e.g. old.reddit.com/r/): {override.url_rewrite!r}"
                )
            if override.timeout is not None and not (1 <= override.timeout <= 120):
                raise ValueError(
                    f"domain_overrides[{override.pattern}]: timeout must be between 1 and 120s"
                )
            if override.network_idle_timeout is not None and not (0 <= override.network_idle_timeout <= 60):
                raise ValueError(
                    f"domain_overrides[{override.pattern}]: network_idle_timeout must be between 0 and 60s"
                )
            if override.challenge_timeout is not None and not (0 <= override.challenge_timeout <= 120):
                raise ValueError(
                    f"domain_overrides[{override.pattern}]: challenge_timeout must be between 0 and 120s"
                )
            if override.engine is not None and override.engine not in ("trafilatura", "readability"):
                raise ValueError(
                    f"domain_overrides[{override.pattern}]: engine must be trafilatura or readability"
                )
        if self.youtube.default_limit < 1:
            raise ValueError(f"youtube.default_limit must be >= 1: {self.youtube.default_limit}")
        if self.youtube.max_limit < self.youtube.default_limit:
            raise ValueError(
                f"youtube.max_limit ({self.youtube.max_limit}) must be >= youtube.default_limit ({self.youtube.default_limit})"
            )


def _filter_dataclass_dict(cls: Any, d: Dict[str, Any]) -> Dict[str, Any]:
    """Return only the dictionary keys that match the dataclass fields."""
    valid_fields = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in valid_fields}


def _deep_merge_dict(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively deep-merge dict `override` into `base`."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge_dict(out[k], v)
        else:
            out[k] = v
    return out


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Public deep merge function."""
    return _deep_merge_dict(base, override)


def _load_yaml(path: str) -> Dict[str, Any]:
    """Load a YAML file, returning {} when the file does not exist."""
    if not os.path.exists(path):
        logger.info("Config file not found (%s), using defaults", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse YAML ({path}): {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"YAML file ({path}) must be a mapping at the root level")
    return data


def _parse_bool(val: str) -> bool:
    """Parse boolean from string."""
    return val.strip().lower() in ("true", "1", "yes", "on", "t")


def _apply_env_overrides(merged: Dict[str, Any]) -> Dict[str, Any]:
    """Apply environment variable overrides on top of merged YAML config."""
    # Server
    if "FORAGE_PORT" in os.environ or "PORT" in os.environ:
        raw_port = os.environ.get("FORAGE_PORT") or os.environ.get("PORT")
        try:
            merged.setdefault("server", {})["port"] = int(raw_port)
        except (ValueError, TypeError):
            pass
    if "FORAGE_HOST" in os.environ:
        merged.setdefault("server", {})["host"] = os.environ["FORAGE_HOST"]
    if "FORAGE_LOG_LEVEL" in os.environ:
        merged.setdefault("server", {})["log_level"] = os.environ["FORAGE_LOG_LEVEL"].lower()

    # Auth
    if "FORAGE_AUTH_ENABLED" in os.environ:
        merged.setdefault("auth", {})["enabled"] = _parse_bool(os.environ["FORAGE_AUTH_ENABLED"])

    # Search
    searxng_url = os.environ.get("FORAGE_SEARXNG_URL") or os.environ.get("SEARXNG_URL")
    if searxng_url:
        merged.setdefault("search", {})["searxng_url"] = searxng_url
    raw_default_engines = os.environ.get("FORAGE_DEFAULT_ENGINES") or os.environ.get("FORAGE_SEARCH_ENGINES")
    if raw_default_engines is not None:
        merged.setdefault("search", {})["engines"] = [e.strip() for e in raw_default_engines.split(",") if e.strip()]
    if "FORAGE_AVAILABLE_ENGINES" in os.environ:
        raw_avail = os.environ["FORAGE_AVAILABLE_ENGINES"]
        merged.setdefault("search", {})["available_engines"] = [e.strip() for e in raw_avail.split(",") if e.strip()]
    if "FORAGE_SEARCH_DEFAULT_LANG" in os.environ:
        merged.setdefault("search", {})["default_lang"] = os.environ["FORAGE_SEARCH_DEFAULT_LANG"]

    # Browser
    if "FORAGE_BROWSER_ENGINE" in os.environ:
        merged.setdefault("browser", {})["engine"] = os.environ["FORAGE_BROWSER_ENGINE"].lower()
    if "FORAGE_BROWSER_CDP_URL" in os.environ:
        merged.setdefault("browser", {})["cdp_url"] = os.environ["FORAGE_BROWSER_CDP_URL"]
    if "FORAGE_BROWSER_HEADLESS" in os.environ:
        merged.setdefault("browser", {})["headless"] = _parse_bool(os.environ["FORAGE_BROWSER_HEADLESS"])

    # Extract
    if "FORAGE_EXTRACT_ENGINE" in os.environ:
        merged.setdefault("extract", {})["engine"] = os.environ["FORAGE_EXTRACT_ENGINE"].lower()
    if "FORAGE_REQUIRE_MAX_CHARS" in os.environ:
        merged.setdefault("extract", {})["require_max_chars"] = _parse_bool(os.environ["FORAGE_REQUIRE_MAX_CHARS"])

    # Cache
    if "FORAGE_CACHE_ENABLED" in os.environ:
        merged.setdefault("cache", {})["enabled"] = _parse_bool(os.environ["FORAGE_CACHE_ENABLED"])

    # Tools
    search_name = os.environ.get("FORAGE_SEARCH_NAME") or os.environ.get("FORAGE_TOOL_SEARCH_NAME") or os.environ.get("FORAGE_SEARCH_TOOL_NAME")
    if search_name:
        merged.setdefault("tools", {})["search_name"] = search_name.strip()
    extract_name = os.environ.get("FORAGE_EXTRACT_NAME") or os.environ.get("FORAGE_TOOL_EXTRACT_NAME") or os.environ.get("FORAGE_EXTRACT_TOOL_NAME")
    if extract_name:
        merged.setdefault("tools", {})["extract_name"] = extract_name.strip()

    # Reddit Cookies & Auth Overrides
    def _clean_val(v: str) -> str:
        s = v.strip()
        if len(s) >= 2 and ((s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'"))):
            s = s[1:-1].strip()
        return s

    reddit_session = os.environ.get("FORAGE_REDDIT_SESSION") or os.environ.get("REDDIT_SESSION")
    reddit_token = (
        os.environ.get("FORAGE_REDDIT_TOKEN_V2")
        or os.environ.get("REDDIT_TOKEN_V2")
        or os.environ.get("FORAGE_REDDIT_TOKEN")
        or os.environ.get("REDDIT_TOKEN")
    )
    reddit_cookies_raw = os.environ.get("FORAGE_REDDIT_COOKIES")

    reddit_cookies: Dict[str, str] = {}
    if reddit_cookies_raw:
        cleaned_raw = _clean_val(reddit_cookies_raw)
        sep = ";" if ";" in cleaned_raw else ","
        for pair in cleaned_raw.split(sep):
            if "=" in pair:
                k, v = pair.split("=", 1)
                clean_k = _clean_val(k)
                if clean_k:
                    reddit_cookies[clean_k] = _clean_val(v)
    if reddit_session:
        reddit_cookies["reddit_session"] = _clean_val(reddit_session)
    if reddit_token:
        reddit_cookies["token_v2"] = _clean_val(reddit_token)

    if reddit_cookies:
        extract_dict = merged.setdefault("extract", {})
        overrides = extract_dict.setdefault("domain_overrides", {})
        if isinstance(overrides, dict):
            matched = False
            for pat in ("reddit.com", "reddit.com/r/", ".reddit.com"):
                if pat in overrides and isinstance(overrides[pat], dict):
                    existing_cookies = overrides[pat].setdefault("cookies", {})
                    if isinstance(existing_cookies, dict):
                        existing_cookies.update(reddit_cookies)
                        matched = True
            if not matched:
                reddit_entry = overrides.setdefault("reddit.com", {})
                if isinstance(reddit_entry, dict):
                    existing_cookies = reddit_entry.setdefault("cookies", {})
                    if isinstance(existing_cookies, dict):
                        existing_cookies.update(reddit_cookies)

    # YouTube
    if "FORAGE_YOUTUBE_ENABLED" in os.environ:
        merged.setdefault("youtube", {})["enabled"] = _parse_bool(os.environ["FORAGE_YOUTUBE_ENABLED"])
    yt_api_key = os.environ.get("FORAGE_YOUTUBE_API_KEY") or os.environ.get("YOUTUBE_API_KEY")
    if yt_api_key is not None:
        merged.setdefault("youtube", {})["api_key"] = _clean_val(yt_api_key)
    if "FORAGE_YOUTUBE_DEFAULT_LIMIT" in os.environ:
        try:
            merged.setdefault("youtube", {})["default_limit"] = int(os.environ["FORAGE_YOUTUBE_DEFAULT_LIMIT"])
        except ValueError:
            pass
    if "FORAGE_YOUTUBE_MAX_LIMIT" in os.environ:
        try:
            merged.setdefault("youtube", {})["max_limit"] = int(os.environ["FORAGE_YOUTUBE_MAX_LIMIT"])
        except ValueError:
            pass

    return merged


def _find_template_file(filename: str) -> Optional[str]:
    """Find pristine template file within container or package search paths."""
    candidates = (
        f"/srv/forage/{filename}",
        os.path.join(os.path.dirname(__file__), "..", filename),
        filename,
    )
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _seed_file_if_missing(target_dir: str, target_filename: str, template_filename: str) -> Optional[str]:
    """Seed target file from template if neither .yaml nor .yml exists in target_dir.

    Returns the path to the existing or newly seeded file, or None if unwritable.
    """
    base_name = target_filename.rsplit(".", 1)[0]
    for ext in (".yaml", ".yml"):
        existing = os.path.join(target_dir, f"{base_name}{ext}")
        if os.path.isfile(existing):
            return existing

    target_path = os.path.join(target_dir, target_filename)
    template_path = _find_template_file(template_filename)
    if template_path:
        try:
            import shutil
            shutil.copy2(template_path, target_path)
            logger.info("Auto-seeded default %s to %s", target_filename, target_path)
            return target_path
        except (PermissionError, OSError):
            logger.warning(
                "Config directory %s is read-only. Cannot auto-seed %s. "
                "Remove the bind mount if you're only using docker compose environment overrides, "
                "or allow writes / create %s manually if you need custom config settings.",
                target_dir,
                target_filename,
                target_filename,
            )
            return None
    return None


def _resolve_config_path(path: Optional[str]) -> str:
    """Resolve config file path from explicit path, env var, or defaults.

    Supports both file paths and directory mounts (e.g. mounting a folder
    to /etc/forage). Auto-seeds config.yaml if directory is empty and writable.
    """
    raw = path or os.environ.get("FORAGE_CONFIG") or DEFAULT_CONFIG_PATH
    if os.path.isdir(raw):
        seeded = _seed_file_if_missing(raw, "config.yaml", "config.example.yaml")
        if seeded:
            return seeded
        return os.path.join(raw, "config.yaml")

    parent_dir = os.path.dirname(raw)
    if parent_dir and os.path.isdir(parent_dir):
        _seed_file_if_missing(parent_dir, "config.yaml", "config.example.yaml")

    if not os.path.exists(raw) and raw.endswith(".yaml"):
        alt = raw[:-5] + ".yml"
        if os.path.exists(alt):
            return alt
    return raw


def _resolve_prompts_path(raw_path: Optional[str], config_dir: Optional[str] = None) -> Optional[str]:
    """Resolve prompts file path from explicit path, env var, or config directory."""
    if raw_path:
        if os.path.isdir(raw_path):
            seeded = _seed_file_if_missing(raw_path, "prompts.yaml", "prompts.example.yaml")
            if seeded:
                return seeded
            return os.path.join(raw_path, "prompts.yaml")
        if not os.path.exists(raw_path) and raw_path.endswith(".yaml"):
            alt = raw_path[:-5] + ".yml"
            if os.path.exists(alt):
                return alt
        return raw_path

    # If no explicit prompts path is set, auto-seed/load prompts.yaml in config_dir
    if config_dir and os.path.isdir(config_dir):
        seeded = _seed_file_if_missing(config_dir, "prompts.yaml", "prompts.example.yaml")
        if seeded and os.path.isfile(seeded):
            return seeded

    return None


def load_config(path: Optional[str] = None) -> ForageConfig:
    """Load configuration from defaults + optional YAML file + environment overrides.

    Resolution order (lowest to highest precedence):
      1. Built-in DEFAULTS (this module)
      2. Main config YAML (FORAGE_CONFIG env var, default /etc/forage/config.yaml)
      3. External prompts YAML (prompts.prompts_path in config, or FORAGE_PROMPTS_CONFIG env var, or <config_dir>/prompts.yaml)
      4. Environment variable overrides (e.g. FORAGE_SEARXNG_URL, FORAGE_BROWSER_ENGINE, etc.)

    The prompts YAML is merged only into the ``prompts`` sub-dict so it cannot
    affect server/search/browser settings.
    """
    import copy
    config_path = _resolve_config_path(path)
    file_data = _load_yaml(config_path)
    merged = deep_merge(copy.deepcopy(DEFAULTS), file_data)

    config_dir = config_path if os.path.isdir(config_path) else os.path.dirname(config_path)

    # External prompts file: prompts_path in config OR FORAGE_PROMPTS_CONFIG env var OR <config_dir>/prompts.yaml
    raw_prompts_path = merged.get("prompts", {}).get("prompts_path") or os.environ.get("FORAGE_PROMPTS_CONFIG")
    prompts_path = _resolve_prompts_path(raw_prompts_path, config_dir=config_dir)
    if prompts_path:
        prompts_data = _load_yaml(prompts_path)
        if prompts_data:
            # If the external YAML has a top-level "prompts" key, unnest it
            if "prompts" in prompts_data and isinstance(prompts_data["prompts"], dict):
                prompts_data = prompts_data["prompts"]
            merged["prompts"] = deep_merge(merged.get("prompts", {}), prompts_data)

    # Apply environment variable overrides (highest precedence)
    merged = _apply_env_overrides(merged)

    cfg = ForageConfig.from_dict(merged, source_path=config_path)
    cfg.validate()
    return cfg

