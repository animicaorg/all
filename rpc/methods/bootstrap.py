from __future__ import annotations

import os
import time
import typing as t

from rpc import deps
from rpc import version as rpc_version
from rpc.config import resolve_chain_id
from rpc.methods import method

BOOTSTRAP_CACHE_TTL = min(120, max(30, int(os.getenv("ANIMICA_BOOTSTRAP_CACHE_TTL", "60") or 60)))
_CACHE: dict[str, tuple[float, t.Any]] = {}


def _cached(key: str, builder: t.Callable[[], t.Any]) -> t.Any:
    now = time.monotonic()
    entry = _CACHE.get(key)
    if entry:
        ts, val = entry
        if now - ts < BOOTSTRAP_CACHE_TTL:
            return val
    val = builder()
    _CACHE[key] = (now, val)
    return val


def _genesis_hash() -> str | None:
    try:
        ctx = deps.ensure_started()
        bdb = getattr(ctx, "block_db", None)
        if bdb is None:
            return None
        getter = getattr(bdb, "get_genesis_hash", None)
        if callable(getter):
            gh = getter()
            if gh:
                return "0x" + bytes(gh).hex()
        canonical = getattr(bdb, "get_canonical_hash", None)
        if callable(canonical):
            h0 = canonical(0)
            if h0:
                return "0x" + bytes(h0).hex()
    except Exception:
        return None
    return None


def _load_seeds() -> list[str]:
    try:
        from p2p import config as p2p_config

        cfg = p2p_config.load_config()
        return list(cfg.seeds)
    except Exception:
        return []


def _rpc_ports() -> list[int]:
    try:
        import rpc.config as rpc_config

        cfg = rpc_config.load()
        return [int(getattr(cfg, "port", 8545))]
    except Exception:
        return [8545]


def _p2p_ports() -> list[int]:
    try:
        from p2p import constants

        return [int(constants.DEFAULT_TCP_PORT), int(constants.DEFAULT_QUIC_PORT), int(constants.DEFAULT_WS_PORT)]
    except Exception:
        return [30333]


def _network_defaults() -> dict[str, t.Any]:
    cid = resolve_chain_id()
    manifest: dict[str, t.Any] = {
        "chain_id": cid,
        "chainId": cid,
        "protocol": {
            "rpcVersion": getattr(rpc_version, "__version__", "dev"),
        },
        "rpc": {"ports": _rpc_ports()},
        "p2p": {"ports": _p2p_ports(), "seeds": _load_seeds()},
        "minPeers": 8,
    }

    try:
        from p2p import constants

        manifest["protocol"]["p2p"] = constants.PROTOCOL_ID
    except Exception:
        pass

    gh = _genesis_hash()
    if gh:
        manifest["genesis_hash"] = gh

    return manifest


@method("bootstrap.getManifest", desc="Lightweight chain manifest for bootstrapping")
def bootstrap_get_manifest() -> dict[str, t.Any]:
    return _cached("manifest", _network_defaults)


@method("bootstrap.getSeeds", desc="Return a short list of public seed peers", aliases=("bootstrap.getPeers",))
def bootstrap_get_seeds() -> dict[str, t.Any]:
    def _build() -> dict[str, t.Any]:
        seeds = _load_seeds()
        return {"seeds": seeds[:16], "ttl": BOOTSTRAP_CACHE_TTL}

    return _cached("seeds", _build)


@method("bootstrap.getSnapshotManifest", desc="Return snapshot manifest for bootstrap")
def bootstrap_get_snapshot_manifest() -> dict[str, t.Any]:
    url = os.getenv("ANIMICA_SNAPSHOT_URL")
    sha = os.getenv("ANIMICA_SNAPSHOT_SHA256")
    if not url:
        return {}
    manifest: dict[str, t.Any] = {"url": url}
    if sha:
        manifest["sha256"] = sha
    return manifest
