# -*- coding: utf-8 -*-
"""
contracts.stdlib.capabilities.zkverify
======================================

Deterministic contract-side helper for verifying zk proofs via the VM syscall
surface, with strict input guards and structured events for indexers.

Goals
-----
1) Call the host's zk verification syscall in a **deterministic** way.
2) Enforce conservative, deterministic size/type checks (fail-fast with clear
   revert codes so misuses are caught in tests).
3) Emit compact events that include **hashes** of inputs (not raw blobs) and
   optional costed "units" reported by the host.

Design
------
- Inputs are raw byte blobs:
    * circuit : bytes   (verification key, circuit descriptor, or artifact)
    * proof   : bytes   (zk proof bytes)
    * public  : bytes   (public input/instance bytes, caller-encoded)

- The syscall name is host-dependent; we try several common spellings and
  signatures for forward/backward compatibility:
    zk_verify(circuit, proof, public) -> bool | {"ok": bool, "units": int}
    zkverify(circuit, proof, public)  -> same
    zk_verify_v1(circuit, proof, public) -> same
    zk_verify({"circuit":..., "proof":..., "public":...}) -> same

- We **never** interpret circuit/public semantically here; encoding/format is
  a contract-level choice as long as it is deterministic.

- Bounds are deliberately conservative to keep per-call memory predictable.
  Tune at deploy-time if your network policies allow larger objects.

Revert codes
------------
- b"ZK:TYPE"  — argument is not bytes/bytearray
- b"ZK:LEN"   — zero length or exceeds max bound
- b"ZK:SYS"   — zk verification syscall unavailable or unusable
- b"ZK:RET"   — unexpected return type/shape from host

Events
------
- b"CAP:ZK:Verify" — {
      b"ok": b"\x01"|b"\x00",
      b"circuit_hash": sha3-256(circuit),
      b"proof_hash":   sha3-256(proof),
      b"public_hash":  sha3-256(public),
      b"units"?: uint64_be   # present only if host returns it
  }

Example
-------
>>> from contracts.stdlib.capabilities import zkverify as zk
>>> ok = zk.verify_proof(circuit=b"...", proof=b"...", public=b"...")
>>> assert isinstance(ok, bool)
"""
from __future__ import annotations

from typing import Final, Optional, Tuple, Dict, Any

from stdlib import abi, events, hash as _hash  # type: ignore

try:  # pragma: no cover - runtime-dependent
    from stdlib import syscalls as _syscalls  # type: ignore
except Exception:  # pragma: no cover
    _syscalls = None  # type: ignore


# -----------------------------------------------------------------------------
# Constants & bounds
# -----------------------------------------------------------------------------

# Domain tag (not used for host verify, but exported so callers can reuse)
DOMAIN_VERIFY: Final[bytes] = b"ZK:VERIFY:v1"

# Deterministic, conservative per-call size limits (tune per-network if needed)
MAX_CIRCUIT_LEN: Final[int] = 256 * 1024     # 256 KiB
MAX_PROOF_LEN:   Final[int] = 512 * 1024     # 512 KiB
MAX_PUBLIC_LEN:  Final[int] =  64 * 1024     #  64 KiB


# -----------------------------------------------------------------------------
# Local guards & utilities
# -----------------------------------------------------------------------------

def _ensure_bytes(x: object) -> bytes:
    if not isinstance(x, (bytes, bytearray)):
        abi.revert(b"ZK:TYPE")
    return bytes(x)


def _ensure_len(x: bytes, *, max_len: int) -> None:
    if len(x) == 0 or len(x) > max_len:
        abi.revert(b"ZK:LEN")


def _ok_flag(ok: bool) -> bytes:
    return b"\x01" if ok else b"\x00"


def _emit_verify_event(*, ok: bool, circuit: bytes, proof: bytes, public: bytes,
                       units: Optional[int]) -> None:
    fields: Dict[bytes, bytes] = {
        b"ok": _ok_flag(ok),
        b"circuit_hash": _hash.sha3_256(circuit),
        b"proof_hash":   _hash.sha3_256(proof),
        b"public_hash":  _hash.sha3_256(public),
    }
    if isinstance(units, int) and units >= 0:
        fields[b"units"] = units.to_bytes(8, "big", signed=False)
    events.emit(b"CAP:ZK:Verify", fields)


def _call_host_verify(circuit: bytes, proof: bytes, public: bytes) -> Tuple[bool, Optional[int]]:
    """
    Try several syscall spellings and signatures. Return (ok, units?).
    Revert with b"ZK:SYS" if none are available or usable.
    """
    if _syscalls is None:
        abi.revert(b"ZK:SYS")

    candidates = ("zk_verify", "zkverify", "zk_verify_v1")
    for name in candidates:
        fn = getattr(_syscalls, name, None)  # type: ignore[attr-defined]
        if fn is None:
            continue
        # 1) Positional triplet
        try:
            res = fn(circuit, proof, public)  # type: ignore[misc]
            ok, units = _interpret_host_result(res)
            return ok, units
        except TypeError:
            pass
        except Exception:
            # swallow and try the next spelling/shape
            pass
        # 2) Dict payload
        try:
            payload = {b"circuit": circuit, b"proof": proof, b"public": public}
            res = fn(payload)  # type: ignore[misc]
            ok, units = _interpret_host_result(res)
            return ok, units
        except Exception:
            continue

    abi.revert(b"ZK:SYS")
    raise RuntimeError("unreachable")  # pragma: no cover


def _interpret_host_result(res: Any) -> Tuple[bool, Optional[int]]:
    """
    Normalize host return shapes:
      - bool
      - {"ok": bool, "units"?: int}
      - {"valid": bool, "cost"?: int}  (compat)
    """
    # Simple bool
    if isinstance(res, bool):
        return res, None

    # Dict-like
    if isinstance(res, dict):
        # accept bytes or str keys
        def _g(*keys: object) -> Optional[Any]:
            for k in keys:
                if k in res:
                    return res[k]  # type: ignore[index]
            return None

        ok_val = _g(b"ok", "ok", b"valid", "valid", b"result", "result")
        if isinstance(ok_val, bool):
            units_val = _g(b"units", "units", b"cost", "cost", b"gas", "gas")
            units_int: Optional[int] = None
            if isinstance(units_val, int) and units_val >= 0:
                units_int = int(units_val)
            return ok_val, units_int

    abi.revert(b"ZK:RET")
    raise RuntimeError("unreachable")  # pragma: no cover


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------

def verify_proof(*, circuit: bytes, proof: bytes, public: bytes,
                 emit_event: bool = True) -> bool:
    """
    Verify a zk proof via the host syscall surface.

    Parameters
    ----------
    circuit : bytes
        Verification key / circuit descriptor blob (encoding is contract-defined).
    proof : bytes
        Proof bytes.
    public : bytes
        Public input/instance bytes (already deterministically encoded by the caller).
    emit_event : bool
        When True (default), emits b"CAP:ZK:Verify" with input hashes and optional units.

    Returns
    -------
    bool
        True if verification succeeded, False otherwise.

    Notes
    -----
    * Deterministic size/shape guards are applied before calling the host.
    * The function never catches or coerces host exceptions into True; if the
      host misbehaves (wrong return shape/type), we revert with b"ZK:RET".
    """
    c = _ensure_bytes(circuit)
    p = _ensure_bytes(proof)
    u = _ensure_bytes(public)

    _ensure_len(c, max_len=MAX_CIRCUIT_LEN)
    _ensure_len(p, max_len=MAX_PROOF_LEN)
    _ensure_len(u, max_len=MAX_PUBLIC_LEN)

    ok, units = _call_host_verify(c, p, u)
    if emit_event:
        _emit_verify_event(ok=ok, circuit=c, proof=p, public=u, units=units)
    return ok


__all__ = [
    "verify_proof",
    "DOMAIN_VERIFY",
    "MAX_CIRCUIT_LEN",
    "MAX_PROOF_LEN",
    "MAX_PUBLIC_LEN",
]
