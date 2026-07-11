from __future__ import annotations

import ipaddress
import os
import time
import typing as t
from dataclasses import dataclass, field
from enum import Enum

from animica.config import parse_env_bool
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
    return parse_env_bool(raw, False)


def _extract_ip(ctx: t.Any) -> str | None:
    """Best-effort client IP for LOGGING and RATE-LIMITING only.

    Prefers the forwarded header so per-client rate limits work behind the nginx
    edge. This value is CLIENT-CONTROLLED and MUST NOT be used for any is-local /
    loopback authorization decision — use _is_local_request()/_socket_peer_ip()
    for that (see the 7.0.0 header-spoof fix below).
    """
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


def _socket_peer_ip(ctx: t.Any) -> str | None:
    """The REAL transport peer IP (ctx.client) — never a client-supplied header.

    The only trustworthy source for a loopback / is-local security decision.
    """
    try:
        client = getattr(ctx, "client", None)
        if client:
            if isinstance(client, tuple):
                return t.cast(str, client[0]) if client else None
            host = getattr(client, "host", None)
            if host:
                return str(host)
    except Exception:
        pass
    return None


def _came_through_public_edge(ctx: t.Any) -> bool:
    """True when the request carries proxy forwarding headers.

    The node RPC binds loopback and every public nginx vhost sets
    X-Real-IP / X-Forwarded-For, so a genuine internal caller connects DIRECTLY
    with NONE of these. Presence => the request originated remotely, regardless of
    the loopback socket peer (always nginx/docker-proxy) or any client-spoofed
    header value. Mirrors rpc.methods.wallet._is_public_edge_request (the
    incident-2026-07-09 hardening that this module was missing): before 7.0.0,
    _extract_ip trusted X-Forwarded-For, so `X-Forwarded-For: 127.0.0.1` let any
    remote caller pass the loopback gate for admin/sensitive methods.
    """
    try:
        headers = {k.lower(): v for k, v in getattr(ctx, "headers", {}).items()}
    except Exception:
        return False
    return bool(
        headers.get("x-forwarded-for")
        or headers.get("x-real-ip")
        or headers.get("forwarded")
        or headers.get("x-animica-public-edge")
    )


@dataclass
class AccessPolicy:
    mode: AccessMode = AccessMode.LOCAL_DEV
    admin_token: str | None = None
    admin_allowlist: list[ipaddress._BaseNetwork] = field(default_factory=list)
    bootstrap_rate_limit_rpm: int = 0
    # 6.0.0 (ANM-M02): when True AND the server is bound to a non-loopback
    # address, admin/debug/dangerous methods are refused from non-loopback
    # clients (local clients / valid admin token still allowed). Safe default
    # False preserves current behavior; server.py emits a loud startup warning.
    restrict_sensitive: bool = False
    # The host the RPC server is bound to (e.g. "127.0.0.1" or "0.0.0.0"). Used
    # only to decide whether the sensitive-method gate is active.
    bind_host: str | None = None
    admin_methods: set[str] = field(default_factory=lambda: {
        "p2p.addPeer",
        "p2p.addPeers",
        "p2p.importPeers",
        "p2p.syncDebug",
        "sync.force",
        "sync.trigger",
        "sync.start",
        "wallet.createAddress",
        "wallet.send",
        "wallet_createAddress",
        "wallet_send",
    })
    # Additional sensitive method names (beyond admin_methods and the debug.*
    # namespace) that the ANM-M02 gate should refuse from non-loopback clients.
    sensitive_methods: set[str] = field(default_factory=lambda: {
        "debug.traceTx",
        "debug.txStatus",
        "debug.mempoolTxTrace",
        "debug.verifyUsefulWorkProof",
        "tx.explainReject",
        "debug.explainReject",
    })
    bootstrap_methods: set[str] = field(default_factory=lambda: {
        "bootstrap.getManifest",
        "bootstrap.getSeeds",
        "bootstrap.getPeers",
        "bootstrap.getSnapshotManifest",
        "net.getBootstrapSeeds",
        "chain.getHead",
        "chain.getHeaders",
        "chain.getBlockByNumber",
        "chain.getBlockByHash",
        "chain.getBlockByHeight",
        "chain.getGenesis",
        "chain.getCheckpoints",
        "chain_getHead",
        "chain_getBlockByNumber",
        "chain_getBlockByHash",
        "chain_getBlockByHeight",
        "chain.getChainId",
        "chain_getCheckpoints",
        "chain_getChainId",
        "eth_chainId",
        "eth_getBlockByNumber",
        "eth_getBlockByHash",
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

        restrict_sensitive = (
            _is_truthy(os.getenv("ANIMICA_RPC_RESTRICT_SENSITIVE"))
            or bool(getattr(cfg, "restrict_sensitive", False))
            or bool(getattr(getattr(cfg, "access", None), "restrict_sensitive", False))
        )
        bind_host = (
            getattr(cfg, "host", None)
            or getattr(getattr(cfg, "access", None), "host", None)
        )

        return cls(
            mode=mode,
            admin_token=str(token) if token else None,
            admin_allowlist=_parse_allowlist(allowlist_raw),
            bootstrap_rate_limit_rpm=max(0, rate_limit),
            restrict_sensitive=restrict_sensitive,
            bind_host=str(bind_host) if bind_host else None,
        )

    @property
    def server_bound_public(self) -> bool:
        """True when the RPC server is bound to a non-loopback interface.

        ``0.0.0.0`` / ``::`` (unspecified = all interfaces) and any concrete
        non-loopback address count as public. Loopback / ``localhost`` / unset
        count as private.
        """
        host = self.bind_host
        if not host:
            return False
        h = str(host).strip().strip("[]")
        if not h or h.lower() == "localhost":
            return False
        try:
            ip = ipaddress.ip_address(h)
        except Exception:
            # A hostname other than localhost — treat as public to be safe.
            return True
        if ip.is_unspecified:
            return True
        return not ip.is_loopback

    def _is_sensitive(self, method: str) -> bool:
        if not method:
            return False
        if method in self.admin_methods or method in self.sensitive_methods:
            return True
        low = method.lower()
        return low.startswith("debug.") or low.startswith("debug_")

    def _is_local_client(self, client_ip: str | None) -> bool:
        if not client_ip:
            return False
        try:
            ip_obj = ipaddress.ip_address(client_ip)
        except Exception:
            return False
        return bool(ip_obj.is_loopback or ip_obj.is_unspecified)

    def _authorized(self, ctx: t.Any, client_ip: str | None = None) -> bool:
        if self.admin_token:
            headers = {k.lower(): v for k, v in getattr(ctx, "headers", {}).items()}
            if headers.get("x-animica-admin-token") == self.admin_token:
                return True
        # IP allowlist is matched against the REAL transport peer only — NEVER the
        # client-controlled X-Forwarded-For/X-Real-IP (the `client_ip` arg comes
        # from _extract_ip, which trusts those headers for logging/rate-limit). A
        # remote attacker could otherwise spoof `X-Forwarded-For: <allowlisted-ip>`
        # to pass this gate. Behind the public nginx edge the peer is the proxy
        # (loopback), so a REMOTE allowlist entry correctly does NOT match — such
        # operators must authenticate with the admin token instead. Direct callers
        # (no proxy) match their genuine source IP.
        peer_ip = _socket_peer_ip(ctx)
        if peer_ip:
            try:
                ip_obj = ipaddress.ip_address(peer_ip)
                for net in self.admin_allowlist:
                    if ip_obj in net:
                        return True
            except Exception:
                return False
        return False

    def _is_local_request(self, ctx: t.Any) -> bool:
        """Security-grade 'genuine localhost caller' check.

        Uses the REAL transport peer only, and treats any proxy forwarding header
        as REMOTE — so a spoofed ``X-Forwarded-For: 127.0.0.1`` can never
        masquerade as loopback (pre-7.0.0 bug: the loopback grant read that
        client-controlled header via _extract_ip).
        """
        if _came_through_public_edge(ctx):
            return False
        return self._is_local_client(_socket_peer_ip(ctx))

    def _peer_injection_authorized(self, ctx: t.Any, client_ip: str | None) -> bool:
        if self._is_local_request(ctx):
            return True
        return self._authorized(ctx, client_ip)

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
        client_ip = _extract_ip(ctx)

        # ANM-M02: fail-closed for sensitive/admin/debug methods when explicitly
        # enabled AND the server is bound to a public (non-loopback) address.
        # Local clients and valid admin tokens are still allowed. This runs
        # ahead of the mode checks so it also covers LOCAL_DEV. Off by default so
        # existing deployments keep working (server.py warns loudly instead).
        if (
            self.restrict_sensitive
            and self.server_bound_public
            and self._is_sensitive(method)
        ):
            if self._peer_injection_authorized(ctx, client_ip):
                return
            raise errors.RpcMethodRestricted(
                detail="Sensitive method restricted on public bind",
                method=method,
                required="localhost or valid admin token",
            )

        if self.mode == AccessMode.LOCAL_DEV:
            return

        if method in self.admin_methods:
            if self._peer_injection_authorized(ctx, client_ip):
                return
            raise errors.AccessDenied(
                "Unauthorized",
                method=method,
                required="localhost or valid admin token",
            )

        if self.mode == AccessMode.PRIVATE_FULL:
            if self._authorized(ctx, client_ip):
                return
            raise errors.RpcMethodRestricted(method=method)

        if self.mode == AccessMode.PUBLIC_BOOTSTRAP:
            if method in self.bootstrap_methods or method.startswith("bootstrap."):
                self._enforce_rate_limit(client_ip)
                return
            if self._authorized(ctx, client_ip):
                return
            raise errors.RpcMethodRestricted(method=method)

        raise errors.RpcMethodRestricted(method=method)


_ACTIVE_POLICY: AccessPolicy = AccessPolicy.from_config(object())


def set_active_policy(policy: AccessPolicy) -> None:
    global _ACTIVE_POLICY
    _ACTIVE_POLICY = policy


def get_active_policy() -> AccessPolicy:
    return _ACTIVE_POLICY
