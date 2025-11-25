"""
Config helpers for the Oracle DA Poster template.

This module builds on the lightweight primitives exposed in `oracle_poster.__init__`
and adds:

- .env file loading (no external deps)
- strict validation and friendly error messages
- redaction utilities for safe logging
- CLI for inspecting/validating config

Usage (library):
    from oracle_poster.config import resolve_config, validate_config, config_summary
    cfg = resolve_config(env_file=".env")
    validate_config(cfg)
    print(config_summary(cfg))

Usage (CLI):
    python -m oracle_poster.config --env-file .env --validate --print
"""

from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from . import PosterEnv, ENV_KEYS, load_env, get_logger

_LOG = get_logger("oracle_poster.config")


# --------------------------------------------------------------------------------------
# .env loading (zero external deps)
# --------------------------------------------------------------------------------------

def load_dotenv_file(path: str | os.PathLike, *, override: bool = True) -> Dict[str, str]:
    """
    Minimal .env loader supporting KEY=VALUE lines, quotes, and comments.
    Existing os.environ keys are preserved unless override=True.

    Returns a dict of the keys that were loaded.
    """
    p = Path(path)
    loaded: Dict[str, str] = {}
    if not p.exists():
        raise FileNotFoundError(f".env file not found: {p}")

    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        # Allow "export KEY=VALUE" style
        if line.lower().startswith("export "):
            line = line[7:].lstrip()

        if "=" not in line:
            continue

        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()

        # Strip matching quotes if present
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]

        if override or key not in os.environ:
            os.environ[key] = val
            loaded[key] = val

    return loaded


# --------------------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------------------

_HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")
_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")  # 20 bytes, hex-encoded
_METHOD_SIG_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\((?:[A-Za-z0-9_,\[\] ]*)\)$")
_SIGNER_ALGS = {"dilithium3", "dilithium2", "sphincs_shake_128s", "sphincs_shake_192s"}


class ConfigError(ValueError):
    pass


def _path_readable(p: Optional[str]) -> bool:
    return bool(p) and Path(p).expanduser().exists()


def _validate_addr(name: str, addr: str) -> None:
    if not _ADDR_RE.match(addr or ""):
        raise ConfigError(f"{name} must be a 20-byte hex address like 0xabc… (got {addr!r})")


def _validate_namespace(ns: str) -> None:
    if not _HEX_RE.match(ns or ""):
        raise ConfigError("DA_NAMESPACE_ID must be hex like 0x<even-length>. Example: 0x74656d706c617465")
    # Require 4..32 bytes (8..64 hex chars after '0x')
    hexlen = len(ns) - 2
    if hexlen % 2 != 0 or not (8 <= hexlen <= 64):
        raise ConfigError("DA_NAMESPACE_ID hex length must be even and between 8 and 64 characters (4..32 bytes)")


def _validate_method(sig: str) -> None:
    if not _METHOD_SIG_RE.match(sig or ""):
        raise ConfigError(
            "ORACLE_UPDATE_METHOD must look like name(type1,type2). Example: set_commitment(bytes32,uint64)"
        )


def _validate_signer(alg: str) -> None:
    if alg not in _SIGNER_ALGS:
        raise ConfigError(
            f"SIGNER_ALG_ID must be one of {sorted(_SIGNER_ALGS)} (got {alg!r})"
        )


def _validate_source(cfg: PosterEnv) -> None:
    both = bool(cfg.source_file_path) and bool(cfg.source_command)
    none = not cfg.source_file_path and not cfg.source_command
    if both:
        raise ConfigError("Specify only one of SOURCE_FILE_PATH or SOURCE_COMMAND, not both")
    if none:
        _LOG.warning("Neither SOURCE_FILE_PATH nor SOURCE_COMMAND set; poster may be idle.")


def _validate_files(cfg: PosterEnv) -> None:
    if not _path_readable(cfg.oracle_abi_path):
        raise ConfigError(f"ORACLE_ABI_PATH is not readable: {cfg.oracle_abi_path!r}")
    if cfg.source_file_path and not _path_readable(cfg.source_file_path):
        raise ConfigError(f"SOURCE_FILE_PATH is not readable: {cfg.source_file_path!r}")


def _validate_lengths(cfg: PosterEnv) -> None:
    if cfg.da_max_blob_bytes <= 0:
        raise ConfigError("DA_MAX_BLOB_BYTES must be > 0")
    if cfg.min_change_bps is not None and not (1 <= cfg.min_change_bps <= 10000):
        raise ConfigError("MIN_CHANGE_BPS must be in [1, 10000] basis points (or unset)")
    if cfg.poll_interval_sec <= 0:
        raise ConfigError("POLL_INTERVAL_SEC must be > 0")
    if cfg.http_timeout_sec <= 0:
        raise ConfigError("HTTP_TIMEOUT_SEC must be > 0")
    if cfg.retry_max < 0:
        raise ConfigError("RETRY_MAX must be >= 0")
    if cfg.retry_backoff_initial_ms <= 0 or cfg.retry_backoff_max_ms <= 0:
        raise ConfigError("Retry backoff values must be > 0")
    if cfg.retry_backoff_initial_ms > cfg.retry_backoff_max_ms:
        raise ConfigError("RETRY_BACKOFF_INITIAL_MS must be <= RETRY_BACKOFF_MAX_MS")
    if cfg.chain_id <= 0:
        raise ConfigError("CHAIN_ID must be a positive integer")


def validate_config(cfg: PosterEnv) -> None:
    """
    Run a suite of validations and raise ConfigError on the first failure.
    """
    _validate_addr("ORACLE_CONTRACT_ADDR", cfg.oracle_contract_addr)
    _validate_namespace(cfg.da_namespace_id)
    _validate_method(cfg.oracle_update_method)
    _validate_signer(cfg.signer_alg_id)
    _validate_source(cfg)
    _validate_files(cfg)
    _validate_lengths(cfg)

    # Signing mode expectation (not strictly required, but sanity check)
    if not (cfg.oracle_mnemonic or cfg.keystore_path):
        _LOG.warning("No ORACLE_MNEMONIC or KEYSTORE_PATH provided — assuming external signer.")


# --------------------------------------------------------------------------------------
# Redaction and presentation
# --------------------------------------------------------------------------------------

_SECRET_KEYS = {
    "ORACLE_MNEMONIC",
    "KEYSTORE_PASSWORD",
    "KEYSTORE_PATH",
}


def _mask(val: Optional[str], keep: int = 4) -> Optional[str]:
    if val is None or val == "":
        return val
    if len(val) <= keep * 2:
        return "*" * len(val)
    return f"{val[:keep]}…{'*'*(max(0,len(val)-keep*2))}…{val[-keep:]}"


def redact_env_dump(raw: Dict[str, Optional[str]]) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {}
    for k in ENV_KEYS:
        v = raw.get(k)
        if k in _SECRET_KEYS:
            out[k] = _mask(v, keep=4)
        else:
            out[k] = v
    return out


def origins_list(cfg: PosterEnv) -> List[str]:
    if not cfg.allow_origins:
        return []
    # Comma or space separated
    parts = re.split(r"[,\s]+", cfg.allow_origins.strip())
    return [p for p in parts if p]


def config_as_dict(cfg: PosterEnv, *, redacted: bool = True) -> Dict[str, object]:
    base = asdict(cfg)
    if redacted:
        base["raw"] = redact_env_dump(cfg.raw)  # type: ignore[assignment]
    return base


def config_summary(cfg: PosterEnv) -> str:
    """
    Human-readable, single-paragraph summary suitable for logs.
    """
    src = "command" if cfg.source_command else ("file" if cfg.source_file_path else "none")
    parts = [
        f"rpc={cfg.rpc_url}",
        f"chain_id={cfg.chain_id}",
        f"contract={cfg.oracle_contract_addr}",
        f"abi={'present' if _path_readable(cfg.oracle_abi_path) else 'missing'}",
        f"update={cfg.oracle_update_method}",
        f"da={cfg.da_api_url}@{cfg.da_namespace_id}",
        f"source={src}",
        f"poll={cfg.poll_interval_sec}s",
        f"retry={cfg.retry_max} backoff={cfg.retry_backoff_initial_ms}..{cfg.retry_backoff_max_ms}ms",
        f"signer={effective_signing_mode(cfg)}:{cfg.signer_alg_id}",
        f"log={cfg.log_level}",
    ]
    return " | ".join(parts)


def effective_signing_mode(cfg: PosterEnv) -> str:
    if cfg.oracle_mnemonic:
        return "mnemonic"
    if cfg.keystore_path:
        return "keystore"
    return "external"


# --------------------------------------------------------------------------------------
# High-level resolver
# --------------------------------------------------------------------------------------

def resolve_config(
    env_file: Optional[str] = None,
    *,
    overrides: Optional[Dict[str, str]] = None,
    strict: bool = True,
) -> PosterEnv:
    """
    Load an optional .env file, apply optional overrides, then build PosterEnv.

    Args:
        env_file: path to .env
        overrides: dict of key → value to inject into environment (after file)
        strict: pass-through to load_env()

    Returns:
        PosterEnv
    """
    if env_file:
        load_dotenv_file(env_file, override=True)
    if overrides:
        for k, v in overrides.items():
            os.environ[k] = v
    cfg = load_env(strict=strict)
    return cfg


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------

def _parse_args(argv: Optional[Iterable[str]] = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="oracle_poster.config",
        description="Inspect and validate configuration for the Oracle DA Poster.",
        epilog="Example: python -m oracle_poster.config --env-file .env --validate --print",
    )
    ap.add_argument("--env-file", help="Path to .env file to load before validating")
    ap.add_argument("--validate", action="store_true", help="Validate config and exit non-zero on failure")
    ap.add_argument("--print", dest="do_print", action="store_true", help="Print a human-readable summary")
    ap.add_argument("--json", action="store_true", help="Emit JSON (redacted) config to stdout")
    ap.add_argument("--raw", action="store_true", help="Include raw environment (redacted) in JSON")
    return ap.parse_args(list(argv) if argv is not None else None)


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        cfg = resolve_config(args.env_file, strict=True)
        validate_config(cfg)
    except Exception as e:  # deliberate: emit friendly error, non-verbose by default
        if args.validate:
            _LOG.error("Config validation failed: %s", e)
            return 2
        else:
            _LOG.warning("Loaded config with issues: %s", e)

    if args.do_print:
        print(config_summary(cfg))

    if args.json:
        data = config_as_dict(cfg, redacted=True)
        if not args.raw:
            # Keep it concise unless --raw is present
            data.pop("raw", None)
        print(json.dumps(data, indent=2, sort_keys=True))

    if not args.do_print and not args.json and not args.validate:
        # Default behavior if no flags: show a compact summary
        print(config_summary(cfg))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
