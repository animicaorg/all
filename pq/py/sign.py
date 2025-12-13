
from __future__ import annotations

import ctypes
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal, Optional, Tuple

Prehash = Literal["none", "sha3-256", "sha256"]

# Animica PQ algorithm IDs (chain-level identifiers)
DILITHIUM3_ID = 0x1001

# Mechanism preference lists.
# We try multiple names because liboqs/liboqs-python naming varies across versions.
MECH_PREFS = {
    DILITHIUM3_ID: (
        "Dilithium3",
        "DILITHIUM_3",
        "ML-DSA-65",
        "MLDSA65",
        "MLDSA_65",
    ),
}

# -------------------------- liboqs loader helpers --------------------------


def _first_existing(paths: Iterable[Path]) -> Optional[Path]:
    for p in paths:
        try:
            if p.exists():
                return p
        except Exception:
            continue
    return None


def _resolve_liboqs_from_env() -> Optional[Path]:
    """
    Accepts either:
      - LIBOQS_PATH=/path/to/liboqs.so
      - LIBOQS_PATH=/prefix (we search prefix/lib*/liboqs.so*)
    Also supports OQS_INSTALL_PATH similarly.
    """
    candidates: list[Path] = []

    for key in ("LIBOQS_PATH", "OQS_INSTALL_PATH"):
        val = os.environ.get(key, "").strip()
        if not val:
            continue
        p = Path(val)

        # If user mistakenly set /usr/local (a directory), search within it.
        if p.is_dir():
            candidates.extend(
                [
                    p / "lib" / "liboqs.so",
                    p / "lib" / "liboqs.so.0",
                    p / "lib64" / "liboqs.so",
                    p / "lib64" / "liboqs.so.0",
                    p / "lib" / "liboqs.so.0.0.0",
                    p / "lib64" / "liboqs.so.0.0.0",
                ]
            )
        else:
            # File path; accept as-is.
            candidates.append(p)

    # Also try repo-local .deps (common in Animica setup)
    repo_root = Path(__file__).resolve().parents[2]  # .../pq/py -> repo
    deps_prefix = repo_root / ".deps" / "liboqs-install"
    candidates.extend(
        [
            deps_prefix / "lib" / "liboqs.so",
            deps_prefix / "lib" / "liboqs.so.0",
            deps_prefix / "lib64" / "liboqs.so",
            deps_prefix / "lib64" / "liboqs.so.0",
        ]
    )

    return _first_existing(candidates)


def _preload_liboqs_best_effort() -> None:
    """
    Best-effort preload so that liboqs-python binds to the intended liboqs.
    Never hard-fails; callers can still fall back to ed25519-fallback elsewhere.
    """
    path = _resolve_liboqs_from_env()
    if not path:
        return
    try:
        ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
    except Exception as e:
        # Keep this message very close to what you saw, but do not kill execution.
        print(f"Failed to load liboqs from LIBOQS_PATH {path.parent}: {path.parent}: {e}")


# -------------------------- signing + verification --------------------------


@dataclass(frozen=True)
class PQSig:
    alg_id: int
    mech: str
    prehash: Prehash
    domain: str
    chain_id: int
    public_key: bytes
    signature: bytes


def _hash_message(msg: bytes, prehash: Prehash) -> bytes:
    if prehash == "none":
        return msg
    if prehash == "sha3-256":
        return hashlib.sha3_256(msg).digest()
    if prehash == "sha256":
        return hashlib.sha256(msg).digest()
    raise ValueError(f"Unknown prehash: {prehash}")


def _select_mech(alg_id: int, enabled: list[str]) -> str:
    prefs = MECH_PREFS.get(alg_id)
    if not prefs:
        raise ValueError(f"Unsupported PQ alg_id: {alg_id}")

    enabled_upper = {m.upper(): m for m in enabled}
    for want in prefs:
        # direct match (case-insensitive)
        got = enabled_upper.get(want.upper())
        if got:
            return got

        # fuzzy match
        for m in enabled:
            if want.upper() in m.upper():
                return m

    raise ValueError(
        f"No supported mechanism enabled for alg_id={hex(alg_id)}. "
        f"Enabled mechanisms sample={enabled[:20]}"
    )


def _new_signature_obj(oqs_mod, mech: str, secret_key: Optional[bytes] = None):
    """
    liboqs-python Signature() constructor differs slightly across versions.
    Try several patterns.
    """
    Sig = oqs_mod.Signature

    if secret_key is None:
        return Sig(mech)

    # Try keyword first
    try:
        return Sig(mech, secret_key=secret_key)
    except TypeError:
        pass

    # Try positional secret_key
    try:
        return Sig(mech, secret_key)
    except TypeError:
        pass

    # Try create empty + import_secret_key if available
    s = Sig(mech)
    if hasattr(s, "import_secret_key"):
        s.import_secret_key(secret_key)  # type: ignore[attr-defined]
        return s

    raise TypeError("liboqs-python Signature() does not accept secret_key on this version")


def pq_sign_detached(
    msg: bytes,
    *,
    alg_id: int,
    secret_key: bytes,
    public_key: bytes,
    domain: str,
    chain_id: int,
    prehash: Prehash = "none",
) -> PQSig:
    """
    Signs msg with OQS Signature.

    IMPORTANT: We intentionally sign the *canonical CBOR bytes* passed in by the caller.
    We only apply optional prehashing here.
    """
    if not isinstance(chain_id, int):
        raise ValueError("chain_id must be an int")

    _preload_liboqs_best_effort()

    import oqs  # type: ignore

    enabled = list(oqs.get_enabled_sig_mechanisms())
    mech = _select_mech(alg_id, enabled)

    to_sign = _hash_message(msg, prehash)

    s = _new_signature_obj(oqs, mech, secret_key=secret_key)
    sig = s.sign(to_sign)

    # Local verification guardrail (this catches secret_key/public_key mismatch immediately)
    ok = s.verify(to_sign, sig, public_key)
    if not ok:
        raise ValueError("Local PQ signature verification failed (secret_key/public_key mismatch?)")

    return PQSig(
        alg_id=alg_id,
        mech=mech,
        prehash=prehash,
        domain=domain,
        chain_id=chain_id,
        public_key=public_key,
        signature=sig,
    )


# Back-compat for older imports
sign_detached = pq_sign_detached


def pq_verify_detached(
    msg: bytes,
    *,
    alg_id: int,
    public_key: bytes,
    signature: bytes,
    chain_id: int,
    domain: str,
    prehash: Prehash = "none",
) -> bool:
    _preload_liboqs_best_effort()
    import oqs  # type: ignore

    enabled = list(oqs.get_enabled_sig_mechanisms())
    mech = _select_mech(alg_id, enabled)

    to_verify = _hash_message(msg, prehash)
    v = _new_signature_obj(oqs, mech, secret_key=None)
    return bool(v.verify(to_verify, signature, public_key))


verify_detached = pq_verify_detached


