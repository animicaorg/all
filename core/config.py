"""
Animica core configuration loader.

Goals
-----
- Zero external deps (stdlib only).
- Layered config with clear precedence:
    1) Explicit overrides passed to `load()` (highest)
    2) Environment variables (ANIMICA_*)
    3) Config file (TOML or JSON)
    4) Built-in defaults (lowest)
- Safe, typed dataclasses with validation.
- Sensible OS-specific defaults (XDG/APPDATA/~/Library).

This module configures only *node-core* concerns:
  - chain/network identity
  - data & logs paths
  - database URI
  - RPC listener (HTTP/WS)
  - P2P listener & seeds
  - genesis file location
  - algorithm-policy roots (hashes/files) hooks

Everything is standard-library to guarantee very-early import.
"""

from __future__ import annotations

import ipaddress
import json
import os
import platform
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional, Tuple, Dict, Any

# -- Optional TOML support (Python 3.11+ has tomllib). We use a tiny shim.
try:  # py311+
    import tomllib as _toml  # type: ignore[attr-defined]
except Exception:  # py310 or missing
    _toml = None  # type: ignore[assignment]


# ------------------------------
# Defaults & helpers
# ------------------------------

MAINNET_CHAIN_ID = 1      # animica:1
TESTNET_CHAIN_ID = 2      # animica:2
DEVNET_CHAIN_ID  = 1337   # animica:1337

DEFAULT_RPC_HOST = "127.0.0.1"
DEFAULT_RPC_HTTP_PORT = 8547
DEFAULT_RPC_WS_PORT   = 8548

DEFAULT_P2P_LISTEN_IP = "0.0.0.0"
DEFAULT_P2P_PORT = 30307

# SQLite default filename
DEFAULT_DB_FILENAME = "animica.db"


def _expand(p: str | Path) -> Path:
    return Path(p).expanduser().resolve()


def _xdg_data_home() -> Path:
    # Linux/Unix: $XDG_DATA_HOME or ~/.local/share
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return _expand(xdg)
    return _expand("~/.local/share")


def _os_default_data_root() -> Path:
    system = platform.system()
    if system == "Darwin":
        return _expand("~/Library/Application Support")
    if system == "Windows":
        appdata = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
        if appdata:
            return _expand(appdata)
        return _expand("~\\AppData\\Roaming")
    # Unix-like
    return _xdg_data_home()


def _default_data_dir() -> Path:
    # Allow ANIMICA_DATA_DIR override
    override = os.environ.get("ANIMICA_DATA_DIR")
    if override:
        return _expand(override)
    return _os_default_data_root() / "animica"


_DURATION_RE = re.compile(r"^\s*(\d+)\s*([smhd]?)\s*$", re.IGNORECASE)


def _parse_duration(value: str) -> float:
    """
    Parse a tiny duration language into seconds.
      "30" -> 30s
      "250ms" -> 0.25s (ms supported)
      "2s", "5m", "3h", "1d"
    """
    v = value.strip().lower()
    if v.endswith("ms"):
        num = float(v[:-2])
        return num / 1000.0
    m = _DURATION_RE.match(v)
    if not m:
        raise ValueError(f"Invalid duration: {value!r}")
    num = float(m.group(1))
    unit = m.group(2).lower()
    mult = {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
    return num * mult


def _parse_bool(v: str) -> bool:
    return v.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _split_list(v: str) -> List[str]:
    return [s.strip() for s in v.split(",") if s.strip()]


def _first_env(*keys: str) -> Optional[str]:
    for k in keys:
        if k in os.environ:
            return os.environ[k]
    return None


def _env_int(name: str, default: int) -> int:
    v = os.environ.get(name)
    if v is None or v == "":
        return default
    try:
        return int(v, 0)
    except Exception as e:
        raise ValueError(f"{name} must be int, got {v!r}") from e


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name)
    return _expand(v) if v else default


def _env_bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return _parse_bool(v) if v is not None else default


# ------------------------------
# Typed configuration model
# ------------------------------

@dataclass
class ChainConfig:
    chain_id: int = DEVNET_CHAIN_ID
    network_name: str = "devnet"  # "mainnet" | "testnet" | "devnet"

    @staticmethod
    def infer_from_env(default: int = DEVNET_CHAIN_ID) -> "ChainConfig":
        # Priority: explicit chain id → network name
        if "ANIMICA_CHAIN_ID" in os.environ:
            cid = _env_int("ANIMICA_CHAIN_ID", default)
            name = {"1": "mainnet", "2": "testnet", "1337": "devnet"}.get(str(cid), f"chain-{cid}")
            return ChainConfig(chain_id=cid, network_name=name)

        net = (os.environ.get("ANIMICA_NETWORK") or "").strip().lower()
        if net in {"main", "mainnet"}:
            return ChainConfig(chain_id=MAINNET_CHAIN_ID, network_name="mainnet")
        if net in {"test", "testnet"}:
            return ChainConfig(chain_id=TESTNET_CHAIN_ID, network_name="testnet")
        # default
        return ChainConfig(chain_id=default, network_name="devnet")


@dataclass
class PathsConfig:
    data_dir: Path
    logs_dir: Path
    genesis_path: Optional[Path] = None  # override path to genesis.json

    @staticmethod
    def defaults(chain: ChainConfig) -> "PathsConfig":
        root = _default_data_dir() / f"chain-{chain.chain_id}"
        return PathsConfig(
            data_dir=root,
            logs_dir=root / "logs",
            genesis_path=None,
        )


@dataclass
class DBConfig:
    uri: str  # e.g., sqlite:////home/user/.local/share/animica/chain-1337/animica.db

    @staticmethod
    def sqlite_default(paths: PathsConfig) -> "DBConfig":
        dbfile = paths.data_dir / DEFAULT_DB_FILENAME
        # Four slashes for absolute path with sqlite:
        return DBConfig(uri=f"sqlite:///{dbfile}")


@dataclass
class RPCConfig:
    host: str = DEFAULT_RPC_HOST
    http_port: int = DEFAULT_RPC_HTTP_PORT
    ws_port: int = DEFAULT_RPC_WS_PORT
    cors_allow_origins: List[str] = field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])  # studio-web dev
    rate_limit_rps: float = 100.0
    enable_openrpc: bool = True

    def http_url(self) -> str:
        return f"http://{self.host}:{self.http_port}"

    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.ws_port}/ws"


@dataclass
class P2PConfig:
    listen_ip: str = DEFAULT_P2P_LISTEN_IP
    listen_port: int = DEFAULT_P2P_PORT
    seeds: List[str] = field(default_factory=list)  # multiaddrs like /ip4/1.2.3.4/tcp/30307
    enable_quic: bool = False
    max_peers: int = 64

    def validate(self) -> None:
        # Validate listen IP
        try:
            ipaddress.ip_address(self.listen_ip)
        except Exception as e:
            raise ValueError(f"Invalid P2P listen_ip {self.listen_ip!r}") from e
        if not (1 <= self.listen_port <= 65535):
            raise ValueError(f"Invalid P2P port {self.listen_port}")


@dataclass
class PolicyRoots:
    # Optional precomputed hex roots for policies (binds headers to a known policy set)
    poies_policy_root_hex: Optional[str] = None
    pq_alg_policy_root_hex: Optional[str] = None


@dataclass
class Config:
    chain: ChainConfig
    paths: PathsConfig
    db: DBConfig
    rpc: RPCConfig
    p2p: P2PConfig
    policies: PolicyRoots

    def ensure_dirs(self) -> None:
        self.paths.data_dir.mkdir(parents=True, exist_ok=True)
        self.paths.logs_dir.mkdir(parents=True, exist_ok=True)

    # Convenience accessors
    @property
    def rpc_http_url(self) -> str:
        return self.rpc.http_url()

    @property
    def rpc_ws_url(self) -> str:
        return self.rpc.ws_url()

    def to_dict(self) -> Dict[str, Any]:
        # Path → str for JSON friendliness
        def _normalize(obj: Any) -> Any:
            if isinstance(obj, Path):
                return str(obj)
            if isinstance(obj, list):
                return [_normalize(i) for i in obj]
            if isinstance(obj, dict):
                return {k: _normalize(v) for k, v in obj.items()}
            return obj

        d = asdict(self)
        return _normalize(d)


# ------------------------------
# File loader (TOML / JSON)
# ------------------------------

def _load_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(str(path))
    suffix = path.suffix.lower()
    with path.open("rb") as f:
        if suffix in {".toml", ".tml"}:
            if not _toml:
                raise RuntimeError("tomllib is unavailable (Python < 3.11). Use JSON config or upgrade Python.")
            return _toml.load(f)  # type: ignore[no-any-return]
        if suffix in {".json"}:
            return json.load(f)
        raise ValueError(f"Unsupported config format: {suffix}. Use .toml or .json")


def _merge_dict(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Shallow + nested dict merge: values in b override a."""
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


# ------------------------------
# Main loader
# ------------------------------

def load(config_file: Optional[str | Path] = None, **overrides: Any) -> Config:
    """
    Load the node configuration.

    Precedence: overrides > env > file > defaults.

    Parameters
    ----------
    config_file : str | Path | None
        Optional path to a TOML or JSON file with keys:
          chain: { chain_id, network_name }
          paths: { data_dir, logs_dir, genesis_path }
          db:    { uri }
          rpc:   { host, http_port, ws_port, cors_allow_origins, rate_limit_rps, enable_openrpc }
          p2p:   { listen_ip, listen_port, seeds, enable_quic, max_peers }
          policies: { poies_policy_root_hex, pq_alg_policy_root_hex }

    overrides : Any
        Keyword overrides, e.g. load(chain={"chain_id":1}, rpc={"http_port":9000})
    """
    # 1) Defaults
    chain = ChainConfig.infer_from_env()
    paths = PathsConfig.defaults(chain)
    db    = DBConfig.sqlite_default(paths)
    rpc   = RPCConfig()
    p2p   = P2PConfig()
    policies = PolicyRoots()

    base: Dict[str, Any] = {
        "chain": asdict(chain),
        "paths": {
            "data_dir": str(paths.data_dir),
            "logs_dir": str(paths.logs_dir),
            "genesis_path": str(paths.genesis_path) if paths.genesis_path else None,
        },
        "db": asdict(db),
        "rpc": asdict(rpc),
        "p2p": asdict(p2p),
        "policies": asdict(policies),
    }

    # 2) File
    if config_file:
        file_conf = _load_file(_expand(config_file))
        base = _merge_dict(base, file_conf)

    # 3) Env
    # Chain
    if "ANIMICA_CHAIN_ID" in os.environ or "ANIMICA_NETWORK" in os.environ:
        chain_env = ChainConfig.infer_from_env(chain.chain_id)
        base = _merge_dict(base, {"chain": asdict(chain_env)})
    # Paths
    if "ANIMICA_DATA_DIR" in os.environ:
        base["paths"]["data_dir"] = str(_env_path("ANIMICA_DATA_DIR", Path(base["paths"]["data_dir"])))
    if "ANIMICA_LOGS_DIR" in os.environ:
        base["paths"]["logs_dir"] = str(_env_path("ANIMICA_LOGS_DIR", Path(base["paths"]["logs_dir"])))
    if "ANIMICA_GENESIS_PATH" in os.environ:
        base["paths"]["genesis_path"] = str(_env_path("ANIMICA_GENESIS_PATH", Path(base["paths"]["data_dir"]) / "genesis.json"))
    # DB
    if "ANIMICA_DB_URI" in os.environ:
        base["db"]["uri"] = os.environ["ANIMICA_DB_URI"]
    # RPC
    if "ANIMICA_RPC_HOST" in os.environ:
        base["rpc"]["host"] = os.environ["ANIMICA_RPC_HOST"].strip()
    if "ANIMICA_RPC_HTTP_PORT" in os.environ:
        base["rpc"]["http_port"] = _env_int("ANIMICA_RPC_HTTP_PORT", DEFAULT_RPC_HTTP_PORT)
    if "ANIMICA_RPC_WS_PORT" in os.environ:
        base["rpc"]["ws_port"] = _env_int("ANIMICA_RPC_WS_PORT", DEFAULT_RPC_WS_PORT)
    if "ANIMICA_RPC_CORS" in os.environ:
        base["rpc"]["cors_allow_origins"] = _split_list(os.environ["ANIMICA_RPC_CORS"])
    if "ANIMICA_RPC_RATE" in os.environ:
        base["rpc"]["rate_limit_rps"] = float(os.environ["ANIMICA_RPC_RATE"])
    if "ANIMICA_RPC_OPENRPC" in os.environ:
        base["rpc"]["enable_openrpc"] = _env_bool("ANIMICA_RPC_OPENRPC", True)
    # P2P
    if "ANIMICA_P2P_LISTEN_IP" in os.environ:
        base["p2p"]["listen_ip"] = os.environ["ANIMICA_P2P_LISTEN_IP"].strip()
    if "ANIMICA_P2P_PORT" in os.environ:
        base["p2p"]["listen_port"] = _env_int("ANIMICA_P2P_PORT", DEFAULT_P2P_PORT)
    if "ANIMICA_P2P_SEEDS" in os.environ:
        base["p2p"]["seeds"] = _split_list(os.environ["ANIMICA_P2P_SEEDS"])
    if "ANIMICA_P2P_QUIC" in os.environ:
        base["p2p"]["enable_quic"] = _env_bool("ANIMICA_P2P_QUIC", False)
    if "ANIMICA_P2P_MAX_PEERS" in os.environ:
        base["p2p"]["max_peers"] = _env_int("ANIMICA_P2P_MAX_PEERS", 64)
    # Policy roots
    if "ANIMICA_POIES_POLICY_ROOT" in os.environ:
        base["policies"]["poies_policy_root_hex"] = os.environ["ANIMICA_POIES_POLICY_ROOT"].strip()
    if "ANIMICA_PQ_POLICY_ROOT" in os.environ:
        base["policies"]["pq_alg_policy_root_hex"] = os.environ["ANIMICA_PQ_POLICY_ROOT"].strip()

    # 4) Overrides (highest)
    if overrides:
        base = _merge_dict(base, overrides)

    # Construct typed config
    cfg = Config(
        chain=ChainConfig(**base["chain"]),
        paths=PathsConfig(
            data_dir=_expand(base["paths"]["data_dir"]),
            logs_dir=_expand(base["paths"]["logs_dir"]),
            genesis_path=_expand(base["paths"]["genesis_path"]) if base["paths"]["genesis_path"] else None,
        ),
        db=DBConfig(**base["db"]),
        rpc=RPCConfig(
            host=base["rpc"]["host"],
            http_port=int(base["rpc"]["http_port"]),
            ws_port=int(base["rpc"]["ws_port"]),
            cors_allow_origins=list(base["rpc"]["cors_allow_origins"] or []),
            rate_limit_rps=float(base["rpc"]["rate_limit_rps"]),
            enable_openrpc=bool(base["rpc"]["enable_openrpc"]),
        ),
        p2p=P2PConfig(
            listen_ip=base["p2p"]["listen_ip"],
            listen_port=int(base["p2p"]["listen_port"]),
            seeds=list(base["p2p"]["seeds"] or []),
            enable_quic=bool(base["p2p"]["enable_quic"]),
            max_peers=int(base["p2p"]["max_peers"]),
        ),
        policies=PolicyRoots(
            poies_policy_root_hex=base["policies"].get("poies_policy_root_hex"),
            pq_alg_policy_root_hex=base["policies"].get("pq_alg_policy_root_hex"),
        ),
    )

    # Post-process defaults if DB URI is placeholder
    if not cfg.db.uri:
        cfg.db = DBConfig.sqlite_default(cfg.paths)

    # Validate & ensure dirs
    _validate_config(cfg)
    cfg.ensure_dirs()
    return cfg


def _validate_hex_root(hexstr: Optional[str], name: str) -> None:
    if hexstr is None:
        return
    h = hexstr.lower().strip()
    if h.startswith("0x"):
        h = h[2:]
    if not re.fullmatch(r"[0-9a-f]{64}|[0-9a-f]{128}", h):
        raise ValueError(f"{name} must be 32-byte or 64-byte hex (optionally 0x-prefixed), got: {hexstr!r}")


def _validate_db_uri(uri: str) -> None:
    # Accept sqlite:///..., rocksdb://..., memory://
    if uri.startswith("sqlite:///") or uri.startswith("memory://") or uri.startswith("rocksdb://"):
        return
    raise ValueError(f"Unsupported DB URI scheme in {uri!r}. Use sqlite:///path/to.db, rocksdb://path or memory://")


def _validate_config(cfg: Config) -> None:
    # Paths sanity
    if cfg.paths.genesis_path is not None and not cfg.paths.genesis_path.exists():
        # Not fatal: allow later creation; but warn via stderr for developer ergonomics.
        print(f"[config] Warning: genesis file not found at {cfg.paths.genesis_path}", file=sys.stderr)

    # DB
    _validate_db_uri(cfg.db.uri)

    # P2P
    cfg.p2p.validate()

    # Policy roots
    _validate_hex_root(cfg.policies.poies_policy_root_hex, "poies_policy_root_hex")
    _validate_hex_root(cfg.policies.pq_alg_policy_root_hex, "pq_alg_policy_root_hex")


# ------------------------------
# CLI helper
# ------------------------------

def _print_json(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def main(argv: List[str] | None = None) -> int:
    """
    CLI usage:

        python -m core.config                      # load defaults/env; print JSON
        python -m core.config path/to/config.toml  # load file; print JSON
    """
    argv = list(argv or sys.argv[1:])
    path = argv[0] if argv else None
    try:
        cfg = load(path)
        _print_json(cfg.to_dict())
        return 0
    except Exception as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
