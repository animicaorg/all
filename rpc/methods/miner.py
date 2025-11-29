"""Mining-related JSON-RPC methods used by the Stratum pool."""

from __future__ import annotations

import os
import time
import uuid
from dataclasses import asdict
from typing import Any, Dict, Tuple

from rpc.methods import method
from rpc import deps

try:  # Optional helper to compute share target from Θ
    from consensus.difficulty import share_microtarget
except Exception:  # pragma: no cover
    share_microtarget = None  # type: ignore[assignment]

try:  # canonical zero constant
    from core.types.hash import ZERO32
except Exception:  # pragma: no cover
    ZERO32 = b"\x00" * 32  # type: ignore[assignment]

# Fallback Θ (µ-nats) if nothing else is available
_DEFAULT_THETA_MICRO = int(os.getenv("ANIMICA_DEFAULT_THETA_MICRO", "3000000"))
_DEFAULT_SHARE_TARGET = float(os.getenv("ANIMICA_DEFAULT_SHARE_TARGET", "0.01"))
_DEFAULT_SHA256_BITS = os.getenv("ANIMICA_SHA256_NBITS", "1d00ffff")


def _to_hex(b: bytes | None) -> str | None:
    return None if b is None else "0x" + b.hex()


def _resolve_theta() -> int:
    # Try the live consensus state if available
    try:
        from consensus.state import consensus_state  # type: ignore

        st = consensus_state()
        if st and getattr(st, "theta_micro", None):
            return int(st.theta_micro)
    except Exception:
        pass
    return _DEFAULT_THETA_MICRO


def _ctx():
    try:
        return deps.get_ctx()
    except Exception:
        # In tests the FastAPI lifecycle may not have run yet; fall back to a
        # one-off context.
        return deps.build_context()


def _head_info() -> Tuple[bytes, int, bytes, int, bytes]:
    ctx = _ctx()
    snap = ctx.get_head()
    header = snap.get("header") if isinstance(snap, dict) else None
    height = int(snap.get("height") or 0)
    chain_id = int(getattr(header, "chain_id", None) or ctx.cfg.chain_id)

    parent_hash_hex = snap.get("hash") if isinstance(snap, dict) else None
    if parent_hash_hex and isinstance(parent_hash_hex, str):
        parent_hash = bytes.fromhex(parent_hash_hex[2:] if parent_hash_hex.startswith("0x") else parent_hash_hex)
    else:
        parent_hash = getattr(header, "hash", None) or ZERO32
    parent_mix_seed = getattr(header, "mix_seed", None) or ZERO32
    parent_state_root = getattr(header, "state_root", None) or ZERO32
    return parent_hash, height or 0, parent_mix_seed, chain_id, parent_state_root


def _policy_roots() -> Tuple[bytes, bytes]:
    ctx = _ctx()
    pow_params = (ctx.params or {}).get("pow", {}) if hasattr(ctx, "params") else {}
    pq_root = pow_params.get("pqAlgPolicyRoot") or ZERO32
    poies_root = pow_params.get("poiesPolicyRoot") or ZERO32
    if isinstance(pq_root, str):
        pq_root = bytes.fromhex(pq_root[2:] if pq_root.startswith("0x") else pq_root)
    if isinstance(poies_root, str):
        poies_root = bytes.fromhex(poies_root[2:] if poies_root.startswith("0x") else poies_root)
    if not isinstance(pq_root, (bytes, bytearray)):
        pq_root = ZERO32
    if not isinstance(poies_root, (bytes, bytearray)):
        poies_root = ZERO32
    return bytes(pq_root), bytes(poies_root)


def _beacon() -> bytes:
    try:
        from randomness.beacon import get_beacon_bytes  # type: ignore

        return get_beacon_bytes() or b""
    except Exception:
        return b""


def _bits_to_target(bits_hex: str) -> int:
    bits = int(bits_hex, 16)
    exponent = bits >> 24
    mantissa = bits & 0xFFFFFF
    return mantissa * (1 << (8 * (exponent - 3)))


@method("miner.getWork", desc="Return a mining work template for Stratum/CPU miners")
def miner_get_work(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    from mining.templates import TemplateBuilder

    _ = params  # currently unused but reserved for future extensions
    tb = TemplateBuilder(
        get_head_info=_head_info,
        get_theta=_resolve_theta,
        get_policy_roots=_policy_roots,
        get_beacon=_beacon,
    )
    tpl = tb.current_template(force=True)

    theta = tpl.theta_target_micro
    share_target = _DEFAULT_SHARE_TARGET
    if share_microtarget is not None:
        try:
            share_target = float(share_microtarget(theta, shares_per_block=1)) / float(theta or 1)
        except Exception:
            share_target = _DEFAULT_SHARE_TARGET

    header_dict = asdict(tpl.header)
    # asdict preserves bytes; coerce to hex for JSON clients
    header_view = {k: (_to_hex(v) if isinstance(v, (bytes, bytearray)) else v) for k, v in header_dict.items()}

    try:
        sign_bytes = tpl.header.to_sign_bytes()
    except Exception:
        # msgspec may not be available in lightweight environments; fall back
        # to a deterministic JSON encoding with hex-encoded bytes.
        import json

        body = {k: (v if not isinstance(v, (bytes, bytearray)) else v.hex()) for k, v in header_dict.items()}
        sign_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")

    return {
        "jobId": uuid.uuid4().hex,
        "header": header_view,
        "thetaMicro": int(theta),
        "shareTarget": float(share_target),
        "height": int(tpl.height),
        "hints": {"mixSeed": _to_hex(tpl.mix_seed)},
        "signBytes": _to_hex(sign_bytes),
    }


@method("miner.submitShare", desc="Accept a submitted share from the mining pool")
def miner_submit_share(**payload: Any) -> Dict[str, Any]:
    # TODO: wire into real PoW validation once available. For now accept and echo.
    share = payload.get("payload") if len(payload) == 1 and "payload" in payload else payload
    return {"accepted": True, "reason": None, "share": share}


@method("miner.get_sha256_job", desc="Return a Bitcoin-style Stratum v1 job template")
def miner_get_sha256_job(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Provide a lightweight SHA-256 template for ASIC-oriented Stratum clients."""

    params = params or {}
    address = params.get("address") or params.get("poolAddress") or ""
    parent_hash, height, _mix_seed, chain_id, _state_root = _head_info()

    prevhash = parent_hash[::-1].hex()  # Stratum v1 expects little-endian hex
    coinb1 = (
        "01000000"  # version
        + f"{height:08x}"  # fake height marker
        + f"{chain_id:08x}"  # chain id marker
    )
    coinb2 = (address or "").replace("0x", "") + "00"
    merkle_branch: list[str] = []

    bits = _DEFAULT_SHA256_BITS
    nbits = bits if isinstance(bits, str) else str(bits)
    ntime = f"{int(time.time()):08x}"
    version = "20000000"

    block_target = _bits_to_target(nbits)
    share_target = _DEFAULT_SHARE_TARGET
    if share_microtarget is not None:
        try:
            share_target = float(share_microtarget(_resolve_theta(), shares_per_block=1)) / float(_resolve_theta() or 1)
        except Exception:
            share_target = _DEFAULT_SHARE_TARGET

    return {
        "jobId": uuid.uuid4().hex,
        "prevhash": prevhash,
        "coinb1": coinb1,
        "coinb2": coinb2,
        "merkle_branch": merkle_branch,
        "version": version,
        "nbits": nbits,
        "ntime": ntime,
        "clean_jobs": True,
        "target": hex(block_target),
        "difficulty": share_target,
        "height": height,
    }


@method("miner.submit_sha256_block", desc="Accept a candidate SHA-256 block from the pool")
def miner_submit_sha256_block(**payload: Any) -> Dict[str, Any]:
    # Stub for integration with the Animica orchestrator. For now we simply echo success.
    block = payload.get("payload") if len(payload) == 1 and "payload" in payload else payload
    return {"accepted": True, "payload": block}
