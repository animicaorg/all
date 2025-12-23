from __future__ import annotations

import ipaddress
import os
import time
import typing as t
from dataclasses import dataclass, field
from enum import Enum

from rpc import errors


class AccessMode(str, Enum):
    PUBLIC_BOOTSTRAP = "PUBLIC_BOOTSTRAP"
    PRIVATE_FULL = "PRIVATE_FULL"
    LOCAL_DEV = "LOCAL_DEV"


def _parse_access_mode(value: str | None) -> AccessMode:
    if isinstance(value, AccessMode):
        return value
    if not value:
        return AccessMode.LOCAL_DEV
    try:
        return AccessMode[value.strip().upper()]
    except Exception:
        return AccessMode.LOCAL_DEV


def _parse_allowlist(raw: str | None) -> list[ipaddress._BaseNetwork]:
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        entries = [str(p).strip() for p in raw if str(p).strip()]
    else:
        entries = [p.strip() for p in str(raw).split(",") if p.strip()]
    parsed: list[ipaddress._BaseNetwork] = []
    for entry in entries:
        try:
            parsed.append(ipaddress.ip_network(entry, strict=False))
        except Exception:
            continue
    return parsed


def _is_truthy(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _extract_ip(ctx: t.Any) -> str | None:
    try:
        headers = {k.lower(): v for k, v in getattr(ctx, "headers", {}).items()}
        forwarded = headers.get("x-forwarded-for") or headers.get("x-real-ip")
        if forwarded:
            return forwarded.split(",")[0].strip()
        client = getattr(ctx, "client", None)
        if client:
            if isinstance(client, tuple):
                return t.cast(str, client[0])
            host = getattr(client, "host", None)
            if host:
                return str(host)
    except Exception:
        pass
    return None


@dataclass
class AccessPolicy:
    mode: AccessMode = AccessMode.LOCAL_DEV
    admin_token: str | None = None
    admin_allowlist: list[ipaddress._BaseNetwork] = field(default_factory=list)
    bootstrap_rate_limit_rpm: int = 0
    admin_methods: set[str] = field(default_factory=lambda: {
        "p2p.addPeer",
        "p2p.addPeers",
        "p2p.importPeers",
    })
    bootstrap_methods: set[str] = field(default_factory=lambda: {
        "bootstrap.getManifest",
        "bootstrap.getSeeds",
        "bootstrap.getPeers",
        "bootstrap.getSnapshotManifest",
        "net.getBootstrapSeeds",
        "chain.getHead",
        "chain.getHeaders",
        "chain.getGenesis",
        "chain.getCheckpoints",
        "chain_getHead",
        "chain.getChainId",
        "chain_getCheckpoints",
        "chain_getChainId",
        "eth_chainId",
        "net_version",
    })

    _rate_state: dict[str, tuple[float, int]] = field(default_factory=dict, init=False)

    @classmethod
    def from_config(cls, cfg: t.Any) -> "AccessPolicy":
        env_mode = os.getenv("ANIMICA_RPC_ACCESS_MODE")
        mode_source = getattr(cfg, "access_mode", None)
        if mode_source is None and hasattr(cfg, "access"):
            mode_source = getattr(getattr(cfg, "access"), "mode", None)
        mode = _parse_access_mode(mode_source or env_mode)

        bootstrap_node_flag = (
            _is_truthy(os.getenv("ANIMICA_RPC_BOOTSTRAP_NODE"))
            or getattr(cfg, "bootstrap_node", False)
            or getattr(getattr(cfg, "access", None), "bootstrap_node", False)
        )
        token = (
            getattr(cfg, "admin_token", None)
            or getattr(getattr(cfg, "access", None), "admin_token", None)
            or os.getenv("ANIMICA_RPC_ADMIN_TOKEN")
        )
        allowlist_raw = (
            getattr(cfg, "admin_allowlist", None)
            or getattr(getattr(cfg, "access", None), "admin_allowlist", None)
            or os.getenv("ANIMICA_RPC_ADMIN_ALLOWLIST")
        )
        rate_limit_raw = (
            getattr(cfg, "bootstrap_rate_limit", None)
            or getattr(getattr(cfg, "access", None), "bootstrap_rate_limit", None)
            or os.getenv("ANIMICA_BOOTSTRAP_RATE_LIMIT")
        )

        if bootstrap_node_flag:
            mode = AccessMode.PUBLIC_BOOTSTRAP
        try:
            rate_limit = int(rate_limit_raw) if rate_limit_raw not in (None, "", False) else 0
        except Exception:
            rate_limit = 0

        return cls(
            mode=mode,
            admin_token=str(token) if token else None,
            admin_allowlist=_parse_allowlist(allowlist_raw),
            bootstrap_rate_limit_rpm=max(0, rate_limit),
        )

    def _is_local_client(self, client_ip: str | None) -> bool:
        if not client_ip:
            return False
        try:
            ip_obj = ipaddress.ip_address(client_ip)
        except Exception:
            return False
        return bool(ip_obj.is_loopback or ip_obj.is_unspecified)

    def _authorized(self, ctx: t.Any, client_ip: str | None) -> bool:
        if self.admin_token:
            headers = {k.lower(): v for k, v in getattr(ctx, "headers", {}).items()}
            if headers.get("x-animica-admin-token") == self.admin_token:
                return True
        if client_ip:
            try:
                ip_obj = ipaddress.ip_address(client_ip)
                for net in self.admin_allowlist:
                    if ip_obj in net:
                        return True
            except Exception:
                return False
        return False

    def _peer_injection_authorized(self, ctx: t.Any, client_ip: str | None) -> bool:
        return True

    def _enforce_rate_limit(self, client_ip: str | None) -> None:
        if self.bootstrap_rate_limit_rpm <= 0:
            return
        key = client_ip or "unknown"
        now = time.monotonic()
        window = 60.0
        window_start, count = self._rate_state.get(key, (now, 0))
        if now - window_start >= window:
            window_start, count = now, 0
        count += 1
        self._rate_state[key] = (window_start, count)
        if count > self.bootstrap_rate_limit_rpm:
            retry_ms = int(max(0.0, window - (now - window_start)) * 1000)
            raise errors.RateLimited(retry_after_ms=retry_ms)

    def authorize(self, method: str, ctx: t.Any) -> None:
        return


_ACTIVE_POLICY: AccessPolicy = AccessPolicy.from_config(object())


def set_active_policy(policy: AccessPolicy) -> None:
    global _ACTIVE_POLICY
    _ACTIVE_POLICY = policy


def get_active_policy() -> AccessPolicy:
    return _ACTIVE_POLICY
