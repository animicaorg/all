"""
Animica RPC configuration.

This module centralizes tunables for the HTTP/WS RPC service:
- host/port
- CORS policy
- rate limits (global + per-method)
- DB path (sqlite/Rocks URI understood by core.db.*)
- chainId
- logging level
- metrics & OpenRPC toggles

Environment variables (examples):
  ANIMICA_RPC_HOST=0.0.0.0
  ANIMICA_RPC_PORT=8545
  ANIMICA_RPC_WS_PATH=/ws
  ANIMICA_RPC_CORS_ORIGINS=["http://localhost:5173","https://studio.animica.org"]
  ANIMICA_RPC_RATE_RPS=50
  ANIMICA_RPC_RATE_BURST=200
  ANIMICA_RPC_RATE_PER_METHOD='{"tx.sendRawTransaction": 5, "chain.getHead": 40}'
  ANIMICA_RPC_DB_URI=sqlite:///~/animica/data/chain.db
  ANIMICA_CHAIN_ID=1
  ANIMICA_LOG_LEVEL=INFO
  ANIMICA_METRICS_ENABLED=true
  ANIMICA_METRICS_PORT=9100
  ANIMICA_OPENRPC_ENABLED=true

Notes
- JSON-like env values accept either JSON or a comma-separated list.
- Paths beginning with ~ are expanded.
- This module has no external deps (no dotenv). Use your process manager to inject env.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


def _expand_sqlite_uri(uri: str) -> str:
    """
    Expand ~/… in sqlite URIs while preserving scheme.
    Accepts:
      - sqlite:///~/animica/data/chain.db
      - sqlite:///home/user/animica/data/chain.db
      - rocksdb:///var/lib/animica
    Returns unchanged if scheme is not sqlite or rocksdb or if no ~ present.
    """
    if ":///" not in uri:
        # Allow bare file path; convert to sqlite URI.
        return "sqlite:///" + str(Path(uri).expanduser())
    scheme, rest = uri.split(":///", 1)
    if scheme in ("sqlite", "rocksdb") and rest.startswith("~"):
        rest_expanded = str(Path(rest).expanduser())
        return f"{scheme}:///{rest_expanded}"
    return uri


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v is not None else default


def _env_bool(name: str, default: bool) -> bool:
    v = _env(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    v = _env(name)
    if v is None:
        return default
    try:
        return int(v.strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    v = _env(name)
    if v is None:
        return default
    try:
        return float(v.strip())
    except Exception:
        return default


def _env_list(name: str, default: List[str]) -> List[str]:
    """
    Parse JSON array or comma-separated string into a list of strings.
    """
    v = _env(name)
    if v is None or v.strip() == "":
        return list(default)
    s = v.strip()
    # Try JSON first
    if (s.startswith("[") and s.endswith("]")) or (s.startswith('"') and s.endswith('"')):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
            # Single string JSON
            return [str(parsed)]
        except Exception:
            pass
    # Fallback: comma-separated
    return [item.strip() for item in s.split(",") if item.strip()]


def _env_json_map_float(name: str, default: Dict[str, float]) -> Dict[str, float]:
    v = _env(name)
    if v is None or v.strip() == "":
        return dict(default)
    try:
        m = json.loads(v)
        if isinstance(m, dict):
            out: Dict[str, float] = {}
            for k, val in m.items():
                out[str(k)] = float(val)
            return out
    except Exception:
        # best-effort: key1=1.0;key2=2
        items = [p for p in v.split(";") if p.strip()]
        out = dict(default)
        for item in items:
            if "=" in item:
                k, val = item.split("=", 1)
                try:
                    out[k.strip()] = float(val.strip())
                except Exception:
                    continue
        return out
    return dict(default)


DEFAULT_PER_METHOD_RPS: Dict[str, float] = {
    # Chain & blocks
    "chain.getParams": 5.0,
    "chain.getChainId": 10.0,
    "chain.getHead": 40.0,
    "chain.getBlockByNumber": 15.0,
    "chain.getBlockByHash": 15.0,
    # Tx flow
    "tx.sendRawTransaction": 3.0,
    "tx.getTransactionByHash": 25.0,
    "tx.getTransactionReceipt": 25.0,
    # State
    "state.getBalance": 40.0,
    "state.getNonce": 40.0,
    # DA
    "da.putBlob": 2.0,
    "da.getBlob": 10.0,
    "da.getProof": 10.0,
    # Randomness
    "rand.getRound": 20.0,
    "rand.getBeacon": 20.0,
    "rand.commit": 5.0,
    "rand.reveal": 5.0,
    # Miner (getwork)
    "miner.getWork": 10.0,
    "miner.submitShare": 20.0,
}


@dataclass(frozen=True)
class CorsConfig:
    allow_origins: List[str] = field(default_factory=lambda: ["http://localhost:5173"])
    allow_credentials: bool = False
    allow_methods: List[str] = field(default_factory=lambda: ["POST", "GET"])
    allow_headers: List[str] = field(default_factory=lambda: ["content-type"])


@dataclass(frozen=True)
class RateLimitConfig:
    # Global default requests-per-second and burst tokens.
    default_rps: float = 50.0
    burst: int = 200
    # Per-method overrides (method name → rps).
    per_method_rps: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_PER_METHOD_RPS))

    def method_rps(self, method: str) -> float:
        return self.per_method_rps.get(method, self.default_rps)


@dataclass(frozen=True)
class RpcConfig:
    host: str
    port: int
    ws_path: str
    cors: CorsConfig
    rate: RateLimitConfig
    db_uri: str
    chain_id: int
    log_level: str
    metrics_enabled: bool
    metrics_port: int
    openrpc_enabled: bool

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def ws_url(self) -> str:
        # Consumers can build ws://…/ws; server mounts WS on same port by default.
        scheme = "ws"
        return f"{scheme}://{self.host}:{self.port}{self.ws_path}"


def load() -> RpcConfig:
    """
    Build a RpcConfig from environment variables with sensible defaults.
    """
    host = _env("ANIMICA_RPC_HOST", "127.0.0.1")
    port = _env_int("ANIMICA_RPC_PORT", 8545)
    ws_path = _env("ANIMICA_RPC_WS_PATH", "/ws")

    cors = CorsConfig(
        allow_origins=_env_list("ANIMICA_RPC_CORS_ORIGINS", ["http://localhost:5173"]),
        allow_credentials=_env_bool("ANIMICA_RPC_CORS_ALLOW_CREDENTIALS", False),
        allow_methods=_env_list("ANIMICA_RPC_CORS_METHODS", ["POST", "GET"]),
        allow_headers=_env_list("ANIMICA_RPC_CORS_HEADERS", ["content-type"]),
    )

    rate = RateLimitConfig(
        default_rps=_env_float("ANIMICA_RPC_RATE_RPS", 50.0),
        burst=_env_int("ANIMICA_RPC_RATE_BURST", 200),
        per_method_rps=_env_json_map_float(
            "ANIMICA_RPC_RATE_PER_METHOD", DEFAULT_PER_METHOD_RPS
        ),
    )

    db_uri = _expand_sqlite_uri(_env("ANIMICA_RPC_DB_URI", "sqlite:///~/animica/data/chain.db"))
    chain_id = _env_int("ANIMICA_CHAIN_ID", 1)
    log_level = (_env("ANIMICA_LOG_LEVEL", "INFO") or "INFO").upper()

    metrics_enabled = _env_bool("ANIMICA_METRICS_ENABLED", True)
    metrics_port = _env_int("ANIMICA_METRICS_PORT", 9100)
    openrpc_enabled = _env_bool("ANIMICA_OPENRPC_ENABLED", True)

    return RpcConfig(
        host=host,
        port=port,
        ws_path=ws_path,
        cors=cors,
        rate=rate,
        db_uri=db_uri,
        chain_id=chain_id,
        log_level=log_level,
        metrics_enabled=metrics_enabled,
        metrics_port=metrics_port,
        openrpc_enabled=openrpc_enabled,
    )


# Singleton-style accessor for frameworks that prefer module-level config.
CONFIG: RpcConfig = load()


def resolve_chain_id(cfg: RpcConfig | Config | None = None) -> int:
    """Best-effort accessor for chainId used by lightweight server banners.

    Some call sites pass the legacy ``Config`` shim while others rely on the
    newer ``RpcConfig``. This helper normalizes both shapes (and tolerates
    ``chainId``/``CHAIN_ID`` attribute variants) to avoid attribute errors when
    rendering metadata endpoints.
    """

    cfg = cfg or CONFIG
    for attr in ("chain_id", "chainId", "CHAIN_ID"):
        if hasattr(cfg, attr):
            try:
                return int(getattr(cfg, attr))
            except Exception:
                break
    # Fallback to default mainnet id if all else fails.
    return 1

@dataclass
class Config:
    """
    Backwards-compatible shim used by tests and simple scripts. Newer code
    should prefer `RpcConfig` above, but the lightweight `Config` mirrors the
    fields expected by rpc.tests helpers.
    """

    host: str
    port: int
    db_uri: str
    chain_id: int
    logging: str = "INFO"
    cors_allow_origins: list[str] = field(default_factory=list)
    rate_limit_per_ip: float = 0.0
    rate_limit_per_method: float = 0.0


def load_config() -> Config:
    cfg = load()
    return Config(
        host=cfg.host,
        port=cfg.port,
        db_uri=cfg.db_uri,
        chain_id=cfg.chain_id,
        logging=cfg.log_level,
        cors_allow_origins=list(cfg.cors.allow_origins),
        rate_limit_per_ip=cfg.rate.default_rps,
        rate_limit_per_method=cfg.rate.default_rps,
    )


__all__ = [
    "CorsConfig",
    "RateLimitConfig",
    "RpcConfig",
    "CONFIG",
    "resolve_chain_id",
    "load",
    "Config",
    "load_config",
]
