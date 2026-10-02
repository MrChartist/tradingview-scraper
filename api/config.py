"""Runtime configuration, read from environment variables (see .env.example)."""
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, FrozenSet, List, Optional


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
class ClientPolicy:
    """What one product (one key) may do. None means "no extra limit"."""
    name: str
    operations: Optional[FrozenSet[str]] = None     # request operations it may call (public help ops are always allowed)
    channels: Optional[FrozenSet[str]] = None       # live channels it may subscribe to
    rate_limit_per_minute: Optional[int] = None     # overrides the server default
    max_symbols: Optional[int] = None               # live symbols per connection / symbols per quotes request

    def allows_operation(self, name: str) -> bool:
        return self.operations is None or name in self.operations

    def allows_channel(self, name: str) -> bool:
        return self.channels is None or name in self.channels


CLIENT_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,40}$")


def sha256_hex(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def load_clients_file(path: str):
    """Read CLIENTS_FILE. Returns (plain_keys {key: name}, hashed_keys {sha256: name}, policies {name: ClientPolicy}).

    Each entry: {"name", "key" or "key_sha256", "operations"?, "channels"?, "rate_limit_per_minute"?, "max_symbols"?}
    """
    with open(path, encoding="utf-8") as fh:
        entries = json.load(fh)
    if not isinstance(entries, list):
        raise ValueError(f"{path}: expected a list of clients")
    plain: Dict[str, str] = {}
    hashed: Dict[str, str] = {}
    policies: Dict[str, ClientPolicy] = {}
    for i, e in enumerate(entries):
        where = f"{path}, client #{i + 1}"
        name = e.get("name", "")
        if not CLIENT_NAME.match(str(name)):
            raise ValueError(f"{where}: 'name' must be 1-40 letters, numbers, dots, dashes or underscores")
        if name in policies:
            raise ValueError(f"{where}: the name '{name}' is used twice")
        key, digest = e.get("key"), (e.get("key_sha256") or "").lower()
        if bool(key) == bool(digest):
            raise ValueError(f"{where}: give exactly one of 'key' or 'key_sha256'")
        if key:
            if len(key) < 16:
                raise ValueError(f"{where}: a plain 'key' must be at least 16 characters (use: python -m api.keys {name})")
            if key in plain:
                raise ValueError(f"{where}: this key is already used by another client")
            plain[key] = name
        else:
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError(f"{where}: 'key_sha256' must be 64 hex characters")
            hashed[digest] = name
        for field_name in ("operations", "channels"):
            if field_name in e and not (isinstance(e[field_name], list) and all(isinstance(x, str) for x in e[field_name])):
                raise ValueError(f"{where}: '{field_name}' must be a list of names")
        for field_name in ("rate_limit_per_minute", "max_symbols"):
            if field_name in e and not (isinstance(e[field_name], int) and e[field_name] > 0):
                raise ValueError(f"{where}: '{field_name}' must be a positive whole number")
        policies[name] = ClientPolicy(
            name=name,
            operations=frozenset(e["operations"]) if "operations" in e else None,
            channels=frozenset(e["channels"]) if "channels" in e else None,
            rate_limit_per_minute=e.get("rate_limit_per_minute"),
            max_symbols=e.get("max_symbols"),
        )
    return plain, hashed, policies


@dataclass(frozen=True)
class Settings:
    # Access control. No keys configured = open mode (fine for local use, not for production).
    api_keys: Dict[str, str] = field(default_factory=dict)
    # Per-product access (CLIENTS_FILE): keys stored as SHA-256 hashes, and what each client may do.
    hashed_keys: Dict[str, str] = field(default_factory=dict)
    clients: Dict[str, ClientPolicy] = field(default_factory=dict)
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
    # Data sources, in the order they are asked. Leave one out to turn it off. Brokers arrive as plugins.
    providers: List[str] = field(default_factory=lambda: ["tradingview"])
    # Reserved. Tickvale is read-only; this only lets a provider declare order capabilities (none are used yet).
    allow_trading: bool = False

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_keys or self.hashed_keys)


def load_settings() -> Settings:
    keys = parse_api_keys(os.getenv("API_KEYS", ""))
    hashed: Dict[str, str] = {}
    clients: Dict[str, ClientPolicy] = {}
    clients_file = os.getenv("CLIENTS_FILE", "").strip()
    if clients_file:
        plain, hashed, clients = load_clients_file(clients_file)
        keys = {**keys, **plain}
    return Settings(
        api_keys=keys,
        hashed_keys=hashed,
        clients=clients,
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
        providers=_list("PROVIDERS") or ["tradingview"],
        allow_trading=_bool("ALLOW_TRADING", False),
    )
