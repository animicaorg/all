"""
animica.ena.config
==================

Layered ENA configuration loader.

Resolution order (lowest → highest priority), per docs/ena/providers.md:

1. built-in defaults
2. user config under ``~/.animica/ena/config.{toml,yaml,json}``
3. workspace config under ``.animica/ena/config.{toml,yaml,json}``
4. explicit ``--config`` / ``ANIMICA_ENA_CONFIG``
5. environment overrides (``ANIMICA_ENA_*``)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .errors import ConfigError
from .models import (
    ENA_CONFIG_VERSION,
    EmbeddingProviderConfig,
    ModelProviderConfig,
    NetworkConfig,
    Schema,
)

try:  # Python 3.11+
    import tomllib  # type: ignore
except Exception:  # pragma: no cover
    tomllib = None  # type: ignore


_CONFIG_BASENAMES = ("config.toml", "config.yaml", "config.yml", "config.json")


@dataclass
class ENAConfig(Schema):
    version: str = ENA_CONFIG_VERSION
    log_level: str = "INFO"
    semantic_search_backend: str = "external"
    default_model_provider: str = "deterministic"
    default_embedding_provider: str = "hashing"
    home: str = "~/.animica/ena"
    model_providers: dict[str, ModelProviderConfig] = field(default_factory=dict)
    embedding_providers: dict[str, EmbeddingProviderConfig] = field(default_factory=dict)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    source_paths: list[str] = field(default_factory=list)
    # --- demand side (wallet-funded jobs) ---
    treasury_address: Optional[str] = None       # anim1… that receives ANM job funding
    rpc_url: str = "http://127.0.0.1:8545/rpc"   # Animica node RPC for payment verify
    chain_id: int = 1
    demand_min_anm: float = 0.1                   # minimum reward a requester may fund
    payment_confirmations: int = 1                # confirmations required before funding
    payment_require_memo: bool = True             # bind payment→job via the tx memo. Set False
                                                  # when the node's tx wire shape omits memo (the
                                                  # mainnet v2 tx format does); binding then relies
                                                  # on recipient + amount + txid de-duplication.
    public_url: str = "https://pool.animica.org"   # public origin (web-wallet callback)

    # -- resolved helpers -------------------------------------------------
    def home_path(self) -> Path:
        return Path(os.path.expanduser(self.home))

    def db_path(self) -> Path:
        return self.home_path() / "ena.db"

    def artifacts_dir(self) -> Path:
        return self.home_path() / "artifacts"

    def model_provider(self, name: Optional[str] = None) -> ModelProviderConfig:
        key = name or self.default_model_provider
        if key in self.model_providers:
            return self.model_providers[key]
        # Synthesize a deterministic fallback so ENA always has a usable model.
        return ModelProviderConfig(name=key, provider="deterministic",
                                   transport="fallback", model="deterministic")

    def demand_enabled(self) -> bool:
        return bool(self.treasury_address)

    def embedding_provider(self, name: Optional[str] = None) -> EmbeddingProviderConfig:
        key = name or self.default_embedding_provider
        if key in self.embedding_providers:
            return self.embedding_providers[key]
        return EmbeddingProviderConfig(name=key, provider="hashing",
                                       transport="fallback", model="legacy-hash")


# ---------------------------------------------------------------------------
# Raw file readers
# ---------------------------------------------------------------------------

def _read_file(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        import json
        return json.loads(text)
    if suffix in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise ConfigError(
                f"cannot read {path}: PyYAML not installed",
                hint="pip install pyyaml, or use a TOML/JSON config",
            ) from exc
        return yaml.safe_load(text) or {}
    # default: TOML
    if tomllib is None:  # pragma: no cover
        raise ConfigError("tomllib unavailable (need Python 3.11+) for TOML config")
    return tomllib.loads(text)


def _candidate_files(explicit: Optional[str]) -> list[Path]:
    """Return existing config files in increasing priority order."""
    files: list[Path] = []
    user_dir = Path(os.path.expanduser("~/.animica/ena"))
    ws_dir = Path.cwd() / ".animica" / "ena"
    for base_dir in (user_dir, ws_dir):
        for name in _CONFIG_BASENAMES:
            p = base_dir / name
            if p.is_file():
                files.append(p)
                break  # one format per dir
    explicit = explicit or os.environ.get("ANIMICA_ENA_CONFIG")
    if explicit:
        p = Path(os.path.expanduser(explicit))
        if not p.is_file():
            raise ConfigError(f"config file not found: {p}")
        files.append(p)
    return files


# ---------------------------------------------------------------------------
# Merge + build
# ---------------------------------------------------------------------------

def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _build_providers(raw: dict[str, Any]) -> tuple[dict[str, ModelProviderConfig],
                                                    dict[str, EmbeddingProviderConfig]]:
    models: dict[str, ModelProviderConfig] = {}
    for name, section in (raw.get("model_providers") or {}).items():
        data = dict(section)
        data["name"] = name
        models[name] = ModelProviderConfig.from_dict(data)
    embeds: dict[str, EmbeddingProviderConfig] = {}
    for name, section in (raw.get("embedding_providers") or {}).items():
        data = dict(section)
        data["name"] = name
        embeds[name] = EmbeddingProviderConfig.from_dict(data)
    return models, embeds


def _default_raw() -> dict[str, Any]:
    """Built-in defaults: deterministic model + hashing embeddings always exist."""
    return {
        "version": ENA_CONFIG_VERSION,
        "default_model_provider": "deterministic",
        "default_embedding_provider": "hashing",
        "model_providers": {
            "deterministic": {"provider": "deterministic", "transport": "fallback",
                              "model": "deterministic", "temperature": 0.0},
        },
        "embedding_providers": {
            "hashing": {"provider": "hashing", "transport": "fallback",
                        "model": "legacy-hash", "batch_size": 64},
        },
    }


def _apply_env_overrides(raw: dict[str, Any]) -> dict[str, Any]:
    env = os.environ
    mp = (env.get("ANIMICA_ENA_MODEL_PROVIDER") or "").strip()
    if mp:
        raw["default_model_provider"] = mp
        section = raw.setdefault("model_providers", {}).setdefault(mp, {})
        _env_into(section, {
            "provider": "ANIMICA_ENA_MODEL_ADAPTER",
            "model": "ANIMICA_ENA_MODEL_NAME",
            "base_url": "ANIMICA_ENA_MODEL_BASE_URL",
            "max_tokens": "ANIMICA_ENA_MODEL_MAX_TOKENS",
            "temperature": "ANIMICA_ENA_MODEL_TEMPERATURE",
            "timeout_seconds": "ANIMICA_ENA_MODEL_TIMEOUT",
            "retry_attempts": "ANIMICA_ENA_MODEL_RETRY_ATTEMPTS",
        })
        if env.get("ANIMICA_ENA_MODEL_API_KEY_ENV"):
            section["api_key_env_vars"] = [env["ANIMICA_ENA_MODEL_API_KEY_ENV"]]
    ep = (env.get("ANIMICA_ENA_EMBEDDING_PROVIDER") or "").strip()
    if ep:
        raw["default_embedding_provider"] = ep
        section = raw.setdefault("embedding_providers", {}).setdefault(ep, {})
        _env_into(section, {
            "provider": "ANIMICA_ENA_EMBEDDING_ADAPTER",
            "model": "ANIMICA_ENA_EMBEDDING_MODEL",
            "base_url": "ANIMICA_ENA_EMBEDDING_BASE_URL",
            "dimensions": "ANIMICA_ENA_EMBEDDING_DIMENSIONS",
            "timeout_seconds": "ANIMICA_ENA_EMBEDDING_TIMEOUT",
            "retry_attempts": "ANIMICA_ENA_EMBEDDING_RETRY_ATTEMPTS",
        })
        if env.get("ANIMICA_ENA_EMBEDDING_API_KEY_ENV"):
            section["api_key_env_vars"] = [env["ANIMICA_ENA_EMBEDDING_API_KEY_ENV"]]
    if env.get("ANIMICA_ENA_HOME"):
        raw.setdefault("storage", {})["home"] = env["ANIMICA_ENA_HOME"]
    demand = raw.setdefault("demand", {})
    if env.get("ENA_TREASURY_ADDRESS"):
        demand["treasury_address"] = env["ENA_TREASURY_ADDRESS"]
    if env.get("ANIMICA_RPC_URL"):
        demand["rpc_url"] = env["ANIMICA_RPC_URL"]
    if env.get("ANIMICA_CHAIN_ID"):
        demand["chain_id"] = int(env["ANIMICA_CHAIN_ID"])
    if env.get("ENA_DEMAND_MIN_ANM"):
        demand["demand_min_anm"] = float(env["ENA_DEMAND_MIN_ANM"])
    if env.get("ENA_PAYMENT_REQUIRE_MEMO") is not None:
        demand["payment_require_memo"] = (
            str(env["ENA_PAYMENT_REQUIRE_MEMO"]).strip().lower()
            in ("1", "true", "yes", "on")
        )
    if env.get("ENA_PUBLIC_URL"):
        demand["public_url"] = env["ENA_PUBLIC_URL"]
    _apply_network_env(env, raw)
    return raw


def _apply_network_env(env: dict, raw: dict[str, Any]) -> None:
    """Operator overrides for 5.1.1 SSRF-safe web acquisition (animica.ena.web).

    Lets the coordinator's web reach be tuned from the environment without a
    config file. Lists are comma-separated. Defaults stay safe (public web minus
    private/reserved ranges, text-only, robots respected).
    """
    net = raw.setdefault("network", {})

    def _csv(name: str):
        val = env.get(name)
        if val is None:
            return None
        return [p.strip() for p in val.split(",") if p.strip()]

    allow = _csv("ENA_ALLOW_DOMAINS")
    if allow is not None:
        net["allow_domains"] = allow
    deny = _csv("ENA_DENY_DOMAINS")
    if deny is not None:
        net["deny_domains"] = deny
    cts = _csv("ENA_WEB_CONTENT_TYPES")
    if cts is not None:
        net["content_type_allowlist"] = cts
    ports = _csv("ENA_WEB_EXTRA_PORTS")
    if ports is not None:
        net["extra_allowed_ports"] = [int(p) for p in ports if p.isdigit()]
    if env.get("ENA_WEB_SIZE_LIMIT"):
        net["size_limit_bytes"] = int(env["ENA_WEB_SIZE_LIMIT"])
    if env.get("ENA_WEB_TIMEOUT"):
        net["request_timeout_seconds"] = float(env["ENA_WEB_TIMEOUT"])
    if env.get("ENA_WEB_MAX_REDIRECTS"):
        net["max_depth"] = int(env["ENA_WEB_MAX_REDIRECTS"])
    if env.get("ENA_WEB_MAX_REQUESTS"):
        net["max_requests"] = int(env["ENA_WEB_MAX_REQUESTS"])
    if env.get("ENA_WEB_RATE_PER_MIN"):
        net["rate_limit_per_domain_per_minute"] = int(env["ENA_WEB_RATE_PER_MIN"])
    if env.get("ENA_WEB_USER_AGENT"):
        net["user_agent"] = env["ENA_WEB_USER_AGENT"]
    if env.get("ENA_RESPECT_ROBOTS") is not None:
        net["respect_robots"] = (
            str(env["ENA_RESPECT_ROBOTS"]).strip().lower()
            in ("1", "true", "yes", "on"))
    if env.get("ENA_WEB_ROBOTS_ON_ERROR"):
        mode = str(env["ENA_WEB_ROBOTS_ON_ERROR"]).strip().lower()
        if mode in ("allow", "deny"):
            net["robots_on_error"] = mode
    if env.get("ENA_ACQUIRE_ENABLED") is not None:
        net["acquire_enabled"] = (
            str(env["ENA_ACQUIRE_ENABLED"]).strip().lower()
            in ("1", "true", "yes", "on"))


def _env_into(section: dict[str, Any], mapping: dict[str, str]) -> None:
    int_keys = {"max_tokens", "retry_attempts", "dimensions"}
    float_keys = {"temperature", "timeout_seconds"}
    for field_name, env_var in mapping.items():
        val = os.environ.get(env_var)
        if val is None or val == "":
            continue
        if field_name in int_keys:
            section[field_name] = int(val)
        elif field_name in float_keys:
            section[field_name] = float(val)
        else:
            section[field_name] = val


def load_config(config_path: Optional[str] = None) -> ENAConfig:
    """Load and merge ENA configuration from all layers."""
    raw = _default_raw()
    sources: list[str] = []
    for path in _candidate_files(config_path):
        try:
            raw = _deep_merge(raw, _read_file(path))
            sources.append(str(path))
        except ConfigError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ConfigError(f"failed to parse {path}: {exc}") from exc
    raw = _apply_env_overrides(raw)

    models, embeds = _build_providers(raw)
    storage = raw.get("storage") or {}
    network = NetworkConfig.from_dict(raw.get("network") or {})
    demand = raw.get("demand") or {}
    return ENAConfig(
        version=str(raw.get("version", ENA_CONFIG_VERSION)),
        log_level=str(raw.get("log_level", "INFO")),
        semantic_search_backend=str(raw.get("semantic_search_backend", "external")),
        default_model_provider=str(raw.get("default_model_provider", "deterministic")),
        default_embedding_provider=str(raw.get("default_embedding_provider", "hashing")),
        home=str(storage.get("home", raw.get("home", "~/.animica/ena"))),
        model_providers=models,
        embedding_providers=embeds,
        network=network,
        source_paths=sources,
        treasury_address=demand.get("treasury_address"),
        rpc_url=str(demand.get("rpc_url", "http://127.0.0.1:8545/rpc")),
        chain_id=int(demand.get("chain_id", 1)),
        demand_min_anm=float(demand.get("demand_min_anm", 0.1)),
        payment_confirmations=int(demand.get("payment_confirmations", 1)),
        payment_require_memo=bool(demand.get("payment_require_memo", True)),
        public_url=str(demand.get("public_url", "https://pool.animica.org")),
    )


def init_config(target: Optional[str] = None, force: bool = False) -> Path:
    """Write a starter config file. Returns the path written."""
    dest = Path(os.path.expanduser(target or "~/.animica/ena/config.toml"))
    if dest.exists() and not force:
        raise ConfigError(f"config already exists: {dest}",
                          hint="pass --force to overwrite")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(_STARTER_TOML, encoding="utf-8")
    return dest


_STARTER_TOML = """\
version = "0.2"
log_level = "INFO"
default_model_provider = "deterministic"
default_embedding_provider = "hashing"

[storage]
home = "~/.animica/ena"

[model_providers.deterministic]
provider = "deterministic"
transport = "fallback"
model = "deterministic"
temperature = 0.0

[model_providers.ollama]
provider = "ollama"
transport = "local_runtime"
model = "llama3.1"
base_url = "http://127.0.0.1:11434"
max_tokens = 2048
temperature = 0.2

[embedding_providers.hashing]
provider = "hashing"
transport = "fallback"
model = "legacy-hash"
batch_size = 64

[embedding_providers.ollama]
provider = "ollama"
transport = "local_runtime"
model = "nomic-embed-text"
base_url = "http://127.0.0.1:11434"
batch_size = 16
"""
