from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .models import EnaConfigModel

try:  # pragma: no cover - Python 3.11+
    import tomllib
except Exception:  # pragma: no cover - fallback for older environments
    tomllib = None  # type: ignore


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _default_home() -> Path:
    override = os.getenv("ANIMICA_ENA_HOME")
    if override:
        return Path(override).expanduser()
    return (Path.cwd() / ".animica" / "ena").resolve()


def _workspace_config_candidates(start: Optional[Path] = None) -> list[Path]:
    current = (start or Path.cwd()).resolve()
    candidates: list[Path] = []
    for parent in (current, *current.parents):
        base = parent / ".animica" / "ena"
        candidates.extend(
            [
                base / "config.toml",
                base / "config.yaml",
                base / "config.yml",
                base / "config.json",
            ]
        )
    return candidates


def _user_config_candidates(home: Path) -> list[Path]:
    return [
        home / "config.toml",
        home / "config.yaml",
        home / "config.yml",
        home / "config.json",
    ]


def _load_config_file(path: Path) -> Dict[str, Any]:
    suffix = path.suffix.lower()
    raw = path.read_bytes()
    if suffix == ".toml":
        if tomllib is None:
            raise RuntimeError("TOML config requested but tomllib is unavailable")
        return tomllib.loads(raw.decode("utf-8"))
    if suffix in {".yaml", ".yml"}:
        return yaml.safe_load(raw) or {}
    if suffix == ".json":
        return json.loads(raw.decode("utf-8"))
    raise ValueError(f"Unsupported config format: {path}")


def _ensure_default_provider_section(
    env: Dict[str, Any],
    *,
    section: str,
    provider_name: str,
    provider_defaults: Dict[str, Any],
) -> Dict[str, Any]:
    providers = dict(env.get(section, {}))
    current = dict(providers.get(provider_name, {}))
    current = _deep_merge(provider_defaults, current)
    providers[provider_name] = current
    env[section] = providers
    return current


def _apply_env_overrides(config: Dict[str, Any]) -> Dict[str, Any]:
    env = dict(config)
    network = dict(env.get("network", {}))
    shell = dict(env.get("shell", {}))
    storage = dict(env.get("storage", {}))

    if os.getenv("ANIMICA_ENA_ALLOWED_DOMAINS"):
        network["allow_domains"] = [
            item.strip()
            for item in os.getenv("ANIMICA_ENA_ALLOWED_DOMAINS", "").split(",")
            if item.strip()
        ]
    if os.getenv("ANIMICA_ENA_DENIED_DOMAINS"):
        network["deny_domains"] = [
            item.strip()
            for item in os.getenv("ANIMICA_ENA_DENIED_DOMAINS", "").split(",")
            if item.strip()
        ]
    if os.getenv("ANIMICA_ENA_MAX_REQUESTS"):
        network["max_requests"] = int(os.environ["ANIMICA_ENA_MAX_REQUESTS"])
    if os.getenv("ANIMICA_ENA_MAX_CRAWL_DEPTH"):
        network["max_depth"] = int(os.environ["ANIMICA_ENA_MAX_CRAWL_DEPTH"])
    if os.getenv("ANIMICA_ENA_USER_AGENT"):
        network["user_agent"] = os.environ["ANIMICA_ENA_USER_AGENT"]

    if os.getenv("ANIMICA_ENA_ALLOW_SHELL") is not None:
        shell["allow_shell"] = os.environ["ANIMICA_ENA_ALLOW_SHELL"].lower() in {"1", "true", "yes", "on"}
    if os.getenv("ANIMICA_ENA_ALLOW_DESTRUCTIVE") is not None:
        shell["allow_destructive"] = os.environ["ANIMICA_ENA_ALLOW_DESTRUCTIVE"].lower() in {"1", "true", "yes", "on"}

    if os.getenv("ANIMICA_ENA_HOME"):
        storage["home"] = os.environ["ANIMICA_ENA_HOME"]

    env["network"] = network
    env["shell"] = shell
    env["storage"] = storage

    if os.getenv("ANIMICA_ENA_AICF_DB"):
        env["aicf_db_path"] = os.environ["ANIMICA_ENA_AICF_DB"]
    if os.getenv("ANIMICA_ENA_WORKER_ID"):
        env["default_worker_id"] = os.environ["ANIMICA_ENA_WORKER_ID"]
    if os.getenv("ANIMICA_ENA_MINER_ADDRESS"):
        env["default_miner_address"] = os.environ["ANIMICA_ENA_MINER_ADDRESS"]
    if os.getenv("ANIMICA_ENA_MODEL_ENDPOINT"):
        env["model_endpoint"] = os.environ["ANIMICA_ENA_MODEL_ENDPOINT"]
    if os.getenv("ANIMICA_ENA_LOG_LEVEL"):
        env["log_level"] = os.environ["ANIMICA_ENA_LOG_LEVEL"]
    if os.getenv("ANIMICA_ENA_SEMANTIC_BACKEND"):
        env["semantic_search_backend"] = os.environ["ANIMICA_ENA_SEMANTIC_BACKEND"]

    if os.getenv("ANIMICA_ENA_MODEL_PROVIDER"):
        env["default_model_provider"] = os.environ["ANIMICA_ENA_MODEL_PROVIDER"]
    if os.getenv("ANIMICA_ENA_EMBEDDING_PROVIDER"):
        env["default_embedding_provider"] = os.environ["ANIMICA_ENA_EMBEDDING_PROVIDER"]

    model_env_keys = [
        "ANIMICA_ENA_MODEL_PROVIDER",
        "ANIMICA_ENA_MODEL_ADAPTER",
        "ANIMICA_ENA_MODEL_TRANSPORT",
        "ANIMICA_ENA_MODEL_NAME",
        "ANIMICA_ENA_MODEL_BASE_URL",
        "ANIMICA_ENA_MODEL_API_KEY_ENV",
        "ANIMICA_ENA_MODEL_MAX_TOKENS",
        "ANIMICA_ENA_MODEL_TEMPERATURE",
        "ANIMICA_ENA_MODEL_TIMEOUT",
        "ANIMICA_ENA_MODEL_RETRY_ATTEMPTS",
    ]
    if any(os.getenv(key) is not None for key in model_env_keys):
        model_provider_name = env.get("default_model_provider") or "default"
        model_defaults = {
            "provider": os.getenv("ANIMICA_ENA_MODEL_ADAPTER", "openai_compatible"),
            "transport": os.getenv("ANIMICA_ENA_MODEL_TRANSPORT", "remote_api"),
            "model": os.getenv("ANIMICA_ENA_MODEL_NAME", "default"),
        }
        model_provider = _ensure_default_provider_section(
            env,
            section="model_providers",
            provider_name=model_provider_name,
            provider_defaults=model_defaults,
        )
        if os.getenv("ANIMICA_ENA_MODEL_BASE_URL"):
            model_provider["base_url"] = os.environ["ANIMICA_ENA_MODEL_BASE_URL"]
            model_provider["endpoint"] = os.environ["ANIMICA_ENA_MODEL_BASE_URL"]
        if os.getenv("ANIMICA_ENA_MODEL_API_KEY_ENV"):
            model_provider["api_key_env_vars"] = [
                item.strip()
                for item in os.getenv("ANIMICA_ENA_MODEL_API_KEY_ENV", "").split(",")
                if item.strip()
            ]
        if os.getenv("ANIMICA_ENA_MODEL_MAX_TOKENS"):
            model_provider["max_tokens"] = int(os.environ["ANIMICA_ENA_MODEL_MAX_TOKENS"])
        if os.getenv("ANIMICA_ENA_MODEL_TEMPERATURE"):
            model_provider["temperature"] = float(os.environ["ANIMICA_ENA_MODEL_TEMPERATURE"])
        if os.getenv("ANIMICA_ENA_MODEL_TIMEOUT"):
            model_provider["timeout_seconds"] = float(os.environ["ANIMICA_ENA_MODEL_TIMEOUT"])
        if os.getenv("ANIMICA_ENA_MODEL_RETRY_ATTEMPTS"):
            retry = dict(model_provider.get("retry_policy", {}))
            retry["attempts"] = int(os.environ["ANIMICA_ENA_MODEL_RETRY_ATTEMPTS"])
            model_provider["retry_policy"] = retry

    embedding_env_keys = [
        "ANIMICA_ENA_EMBEDDING_PROVIDER",
        "ANIMICA_ENA_EMBEDDING_ADAPTER",
        "ANIMICA_ENA_EMBEDDING_TRANSPORT",
        "ANIMICA_ENA_EMBEDDING_MODEL",
        "ANIMICA_ENA_EMBEDDING_BASE_URL",
        "ANIMICA_ENA_EMBEDDING_API_KEY_ENV",
        "ANIMICA_ENA_EMBEDDING_DIMENSIONS",
        "ANIMICA_ENA_EMBEDDING_TIMEOUT",
        "ANIMICA_ENA_EMBEDDING_RETRY_ATTEMPTS",
    ]
    if any(os.getenv(key) is not None for key in embedding_env_keys):
        embedding_provider_name = env.get("default_embedding_provider") or "default"
        embedding_defaults = {
            "provider": os.getenv("ANIMICA_ENA_EMBEDDING_ADAPTER", "openai_compatible"),
            "transport": os.getenv("ANIMICA_ENA_EMBEDDING_TRANSPORT", "remote_api"),
            "model": os.getenv("ANIMICA_ENA_EMBEDDING_MODEL", ""),
        }
        embedding_provider = _ensure_default_provider_section(
            env,
            section="embedding_providers",
            provider_name=embedding_provider_name,
            provider_defaults=embedding_defaults,
        )
        if os.getenv("ANIMICA_ENA_EMBEDDING_BASE_URL"):
            embedding_provider["base_url"] = os.environ["ANIMICA_ENA_EMBEDDING_BASE_URL"]
            embedding_provider["endpoint"] = os.environ["ANIMICA_ENA_EMBEDDING_BASE_URL"]
        if os.getenv("ANIMICA_ENA_EMBEDDING_API_KEY_ENV"):
            embedding_provider["api_key_env_vars"] = [
                item.strip()
                for item in os.getenv("ANIMICA_ENA_EMBEDDING_API_KEY_ENV", "").split(",")
                if item.strip()
            ]
        if os.getenv("ANIMICA_ENA_EMBEDDING_DIMENSIONS"):
            embedding_provider["dimensions"] = int(os.environ["ANIMICA_ENA_EMBEDDING_DIMENSIONS"])
        if os.getenv("ANIMICA_ENA_EMBEDDING_TIMEOUT"):
            embedding_provider["timeout_seconds"] = float(os.environ["ANIMICA_ENA_EMBEDDING_TIMEOUT"])
        if os.getenv("ANIMICA_ENA_EMBEDDING_RETRY_ATTEMPTS"):
            retry = dict(embedding_provider.get("retry_policy", {}))
            retry["attempts"] = int(os.environ["ANIMICA_ENA_EMBEDDING_RETRY_ATTEMPTS"])
            embedding_provider["retry_policy"] = retry

    return env


def _normalize_provider_config(merged: Dict[str, Any]) -> Dict[str, Any]:
    model_providers = dict(merged.get("model_providers", {}))
    embedding_providers = dict(merged.get("embedding_providers", {}))

    if "deterministic" not in model_providers:
        model_providers["deterministic"] = {
            "provider": "deterministic",
            "transport": "fallback",
            "model": "deterministic",
            "max_tokens": 1024,
            "temperature": 0.0,
            "timeout_seconds": 15.0,
        }

    if merged.get("model_endpoint") and "legacy_remote" not in model_providers:
        model_providers["legacy_remote"] = {
            "provider": "openai_compatible",
            "transport": "remote_api",
            "model": os.getenv("ANIMICA_ENA_MODEL_NAME", "legacy-default"),
            "base_url": merged["model_endpoint"],
            "endpoint": merged["model_endpoint"],
            "api_key_env_vars": ["OPENAI_API_KEY"],
            "max_tokens": 1024,
            "temperature": 0.2,
            "timeout_seconds": 30.0,
        }

    default_model = merged.get("default_model_provider")
    if not default_model:
        default_model = "legacy_remote" if "legacy_remote" in model_providers else "deterministic"
    merged["default_model_provider"] = default_model
    merged["model_providers"] = model_providers

    if merged.get("semantic_search_backend") == "hashing" and "hashing" not in embedding_providers:
        embedding_providers["hashing"] = {
            "provider": "hashing",
            "transport": "fallback",
            "model": "legacy-hash",
            "batch_size": 64,
        }
    if "disabled" not in embedding_providers:
        embedding_providers["disabled"] = {
            "provider": "disabled",
            "transport": "fallback",
            "model": "disabled",
            "batch_size": 1,
        }

    default_embedding = merged.get("default_embedding_provider")
    if not default_embedding:
        if merged.get("semantic_search_backend") == "hashing":
            default_embedding = "hashing"
        else:
            default_embedding = "disabled"
    merged["default_embedding_provider"] = default_embedding
    merged["embedding_providers"] = embedding_providers
    return merged


def load_ena_config(
    *,
    cwd: Optional[Path] = None,
    explicit_path: Optional[Path] = None,
) -> EnaConfigModel:
    if explicit_path is None and os.getenv("ANIMICA_ENA_CONFIG"):
        explicit_path = Path(os.environ["ANIMICA_ENA_CONFIG"]).expanduser()
    home = _default_home()
    merged: Dict[str, Any] = {}

    file_candidates: list[Path] = []
    file_candidates.extend(path for path in _user_config_candidates(home) if path.exists())
    file_candidates.extend(path for path in _workspace_config_candidates(cwd) if path.exists())
    if explicit_path:
        file_candidates.append(explicit_path.expanduser())

    seen: set[Path] = set()
    for path in file_candidates:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        merged = _deep_merge(merged, _load_config_file(path))

    merged = _apply_env_overrides(merged)
    merged = _normalize_provider_config(merged)
    merged.setdefault("storage", {})
    merged["storage"].setdefault("home", home)
    merged.setdefault("workspace", str((cwd or Path.cwd()).resolve()))

    config = EnaConfigModel.model_validate(merged)
    storage = config.storage
    storage.home = Path(storage.home).expanduser()
    storage.db_path = storage.db_path or storage.home / "state" / "ena.db"
    storage.artifacts_dir = storage.artifacts_dir or storage.home / "artifacts"
    storage.datasets_dir = storage.datasets_dir or storage.home / "datasets"
    storage.indexes_dir = storage.indexes_dir or storage.home / "indexes"
    storage.sessions_dir = storage.sessions_dir or storage.home / "sessions"
    storage.logs_dir = storage.logs_dir or storage.home / "logs"
    storage.manifests_dir = storage.manifests_dir or storage.home / "manifests"
    config.default_output_dir = config.default_output_dir or storage.home / "outputs"
    config.workspace = Path(config.workspace).resolve()
    config.aicf_db_path = Path(config.aicf_db_path).expanduser() if config.aicf_db_path else storage.home / "state" / "aicf_protocol.sqlite3"
    return config


def save_default_config(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    default_text = f"""version = "0.2"
log_level = "INFO"
semantic_search_backend = "external"
default_model_provider = "deterministic"
default_embedding_provider = "disabled"
default_worker_id = "local-worker"

[storage]
home = "{_default_home()}"

[network]
allow_domains = []
deny_domains = []
max_requests = 100
max_depth = 2
size_limit_bytes = 2000000
request_timeout_seconds = 20.0
retries = 2
backoff_seconds = 0.5
rate_limit_per_domain_per_minute = 30
user_agent = "Animica-ENA/0.2 (+https://animica.org)"
respect_robots = true
allow_browser_automation = false
allow_login = false

[shell]
allow_shell = false
allow_destructive = false
allow_write_outside_workspace = false
approval_required = true
approved_prefixes = []

[model_providers.deterministic]
provider = "deterministic"
transport = "fallback"
model = "deterministic"
max_tokens = 1024
temperature = 0.0
timeout_seconds = 15.0

[model_providers.openai]
provider = "openai_compatible"
transport = "remote_api"
model = "gpt-4.1-mini"
base_url = "https://api.openai.com/v1"
api_key_env_vars = ["OPENAI_API_KEY"]
max_tokens = 1024
temperature = 0.2
timeout_seconds = 30.0

[model_providers.ollama]
provider = "ollama"
transport = "local_runtime"
model = "llama3.1"
base_url = "http://127.0.0.1:11434"
api_key_env_vars = []
max_tokens = 1024
temperature = 0.2
timeout_seconds = 30.0

[embedding_providers.disabled]
provider = "disabled"
transport = "fallback"
model = "disabled"

[embedding_providers.hashing]
provider = "hashing"
transport = "fallback"
model = "legacy-hash"
batch_size = 64

[embedding_providers.openai]
provider = "openai_compatible"
transport = "remote_api"
model = "text-embedding-3-small"
base_url = "https://api.openai.com/v1"
api_key_env_vars = ["OPENAI_API_KEY"]
batch_size = 16
timeout_seconds = 30.0

[embedding_providers.ollama]
provider = "ollama"
transport = "local_runtime"
model = "nomic-embed-text"
base_url = "http://127.0.0.1:11434"
api_key_env_vars = []
batch_size = 16
timeout_seconds = 30.0
"""
    path.write_text(default_text, encoding="utf-8")
    return path
