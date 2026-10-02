"""Runtime configuration, read from environment variables (see .env.example)."""
import os
from dataclasses import dataclass, field
from typing import Dict, List


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _list(name: str) -> List[str]:
    return [p.strip() for p in os.getenv(name, "").split(",") if p.strip()]


def parse_api_keys(raw: str) -> Dict[str, str]:
    """Parse 'name:key,name2:key2' or bare 'key1,key2' into {key: client_name}."""
    keys: Dict[str, str] = {}
    for i, part in enumerate(p.strip() for p in raw.split(",") if p.strip()):
        name, sep, key = part.partition(":")
        if sep and key:
            keys[key] = name
        else:
            keys[part] = f"client-{i + 1}"
    return keys


@dataclass(frozen=True)
class Settings:
    # Access control. No keys configured = open mode (fine for local use, not for production).
    api_keys: Dict[str, str] = field(default_factory=dict)
    require_api_key: bool = False
    # Fixed-window rate limit per client (API key, or IP in open mode).
    rate_limit_per_minute: int = 120
    # Browser access to /v1 from other origins. Empty = same-origin only.
    cors_origins: List[str] = field(default_factory=list)
    # Serve the bundled web UI and its /api routes.
    enable_web_ui: bool = True
    # Response cache lifetime for REST data.
    cache_ttl_seconds: int = 60
    # Live streaming limits.
    ws_max_symbols_per_client: int = 100
    ws_max_clients_per_key: int = 5
    hub_max_upstream_symbols: int = 500
    # Honour X-Forwarded-For for client IP (only behind a trusted proxy).
    trust_proxy_headers: bool = False
    log_level: str = "INFO"
    # Breaks ties when a name matches listings in several countries (for example TCS). "" = no preference.
    default_country: str = "IN"
    # Extra modules to load at startup. Each registers its own operations and channels (docs/EXTENDING.md).
    plugins: List[str] = field(default_factory=list)

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_keys)


def load_settings() -> Settings:
    keys = parse_api_keys(os.getenv("API_KEYS", ""))
    return Settings(
        api_keys=keys,
        require_api_key=_bool("REQUIRE_API_KEY", False),
        rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 120),
        cors_origins=_list("CORS_ORIGINS"),
        enable_web_ui=_bool("ENABLE_WEB_UI", True),
        cache_ttl_seconds=_int("CACHE_TTL_SECONDS", 60),
        ws_max_symbols_per_client=_int("WS_MAX_SYMBOLS_PER_CLIENT", 100),
        ws_max_clients_per_key=_int("WS_MAX_CLIENTS_PER_KEY", 5),
        hub_max_upstream_symbols=_int("HUB_MAX_UPSTREAM_SYMBOLS", 500),
        trust_proxy_headers=_bool("TRUST_PROXY_HEADERS", False),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        default_country=os.getenv("DEFAULT_COUNTRY", "IN").strip().upper(),
        plugins=_list("PLUGINS"),
    )
