"""
SDK configuration: RPC endpoints, chain id, and retry/timeouts.

- Loads sane defaults and supports overrides via environment variables (OMNI_*).
- Provides helpers for building HTTP headers and validating endpoints.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .version import __version__

_DEFAULT_RPC = "http://127.0.0.1:8545"
_DEFAULT_WS = "ws://127.0.0.1:8546"


_HEX_RE = re.compile(r"^0x[0-9a-fA-F]+$")


def _parse_chain_id(val: Any, default: int = 31337) -> int:
    """
    Accepts int, decimal str, or 0x-hex str and returns int.
    """
    if val is None or val == "":
        return int(default)
    if isinstance(val, int):
        return val
    s = str(val).strip()
    if _HEX_RE.match(s):
        return int(s, 16)
    return int(s, 10)


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    v = os.getenv(name)
    return v if v is not None else default


def _ensure_scheme(url: Optional[str], allowed: tuple[str, ...]) -> Optional[str]:
    if not url:
        return url
    lower = url.lower()
    if not any(lower.startswith(f"{sch}://") for sch in allowed):
        raise ValueError(f"URL must start with {allowed}, got: {url!r}")
    return url


@dataclass(slots=True)
class SDKConfig:
    # Core
    rpc_url: str = field(default_factory=lambda: _DEFAULT_RPC)
    chain_id: int = field(default_factory=lambda: _parse_chain_id(None))
    # Optional WS (for subscriptions)
    ws_url: Optional[str] = field(default=None)
    # HTTP/WS behavior
    request_timeout: float = 10.0
    ws_connect_timeout: float = 10.0
    max_retries: int = 3
    backoff_factor: float = 0.25
    # Headers / identity
    user_agent: str = field(default_factory=lambda: f"omni-sdk-py/{__version__}")

    @classmethod
    def from_env(cls, prefix: str = "OMNI_") -> "SDKConfig":
        """
        Create config from environment variables:

        OMNI_RPC_URL            (http/https)
        OMNI_WS_URL             (ws/wss) optional
        OMNI_CHAIN_ID           (int or 0x-hex)
        OMNI_TIMEOUT            (float seconds, HTTP)
        OMNI_WS_TIMEOUT         (float seconds, WS connect)
        OMNI_MAX_RETRIES        (int)
        OMNI_BACKOFF            (float)
        OMNI_USER_AGENT         (str)
        """
        rpc = _env(f"{prefix}RPC_URL", _DEFAULT_RPC)
        ws = _env(f"{prefix}WS_URL", None)
        chain_id = _parse_chain_id(_env(f"{prefix}CHAIN_ID", None))
        timeout = float(_env(f"{prefix}TIMEOUT", "10.0"))
        ws_timeout = float(_env(f"{prefix}WS_TIMEOUT", "10.0"))
        retries = int(_env(f"{prefix}MAX_RETRIES", "3"))
        backoff = float(_env(f"{prefix}BACKOFF", "0.25"))
        ua = _env(f"{prefix}USER_AGENT", f"omni-sdk-py/{__version__}")

        _ensure_scheme(rpc, ("http", "https"))
        _ensure_scheme(ws, ("ws", "wss"))

        return cls(
            rpc_url=rpc or _DEFAULT_RPC,
            ws_url=ws,
            chain_id=chain_id,
            request_timeout=timeout,
            ws_connect_timeout=ws_timeout,
            max_retries=retries,
            backoff_factor=backoff,
            user_agent=ua or f"omni-sdk-py/{__version__}",
        )

    @classmethod
    def with_overrides(
        cls, base: Optional["SDKConfig"] = None, **overrides: Any
    ) -> "SDKConfig":
        """
        Build from an existing config plus keyword overrides.
        Unknown keys are ignored.
        """
        base = base or cls.from_env()
        data = base.to_dict()
        data.update({k: v for k, v in overrides.items() if k in data})
        # Validate/normalize a couple of fields
        if "chain_id" in overrides:
            data["chain_id"] = _parse_chain_id(overrides["chain_id"], base.chain_id)
        if "rpc_url" in overrides:
            _ensure_scheme(data["rpc_url"], ("http", "https"))
        if "ws_url" in overrides:
            _ensure_scheme(data["ws_url"], ("ws", "wss"))
        return cls(**data)

    def http_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": self.user_agent,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rpc_url": self.rpc_url,
            "ws_url": self.ws_url,
            "chain_id": int(self.chain_id),
            "request_timeout": float(self.request_timeout),
            "ws_connect_timeout": float(self.ws_connect_timeout),
            "max_retries": int(self.max_retries),
            "backoff_factor": float(self.backoff_factor),
            "user_agent": self.user_agent,
        }


# Convenience singleton (safe to use for simple scripts)
DEFAULT = SDKConfig.from_env()

__all__ = ["SDKConfig", "DEFAULT"]
