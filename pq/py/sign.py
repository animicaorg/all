
from __future__ import annotations

import ctypes
import hashlib
import os
from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional, Tuple

PrehashKind = Literal["none", "sha3-256"]

# Animica algorithm IDs (keep stable; these are baked into addresses/tx envelopes)
DILITHIUM3_ID = 0x1001

# The *actual* liboqs mechanism name we want for Dilithium3.
# NOTE: liboqs 0.15+ removed Dilithium; for Animica "dilithium3" you must use liboqs 0.14.x.
OQS_MECH_FOR_DILITHIUM3 = "Dilithium3"


def _debug(msg: str) -> None:
    if os.environ.get("ANIMICA_PQ_DEBUG", ""):
        print(msg)


def _find_shared_lib_in_dir(d: str) -> Optional[str]:
    # Search common library filenames and locations
    cands = [
        os.path.join(d, "liboqs.so"),
        os.path.join(d, "liboqs.dylib"),
        os.path.join(d, "liboqs.dll"),
        os.path.join(d, "lib", "liboqs.so"),
        os.path.join(d, "lib", "liboqs.dylib"),
        os.path.join(d, "lib64", "liboqs.so"),
        os.path.join(d, "bin", "liboqs.dll"),
    ]
    # also accept version-suffixed .so.*
    for sub in ("", "lib", "lib64"):
        p = os.path.join(d, sub)
        if os.path.isdir(p):
            try:
                for name in os.listdir(p):
                    if name.startswith("liboqs.so."):
                        cands.append(os.path.join(p, name))
            except Exception:
                pass

    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def _ensure_liboqs_loaded() -> None:
    """
    Best-effort preload of liboqs into the process.

    Accepts:
      - LIBOQS_PATH as a FILE (liboqs.so) OR as a DIRECTORY (we'll search inside)
      - OQS_INSTALL_PATH similarly (dir)
    """
    path = os.environ.get("LIBOQS_PATH") or ""
    if not path:
        path = os.environ.get("OQS_INSTALL_PATH") or ""

    if not path:
        return

    try:
        if os.path.isdir(path):
            lib = _find_shared_lib_in_dir(path)
            if not lib:
                raise FileNotFoundError(f"No liboqs shared library found inside dir: {path}")
            path = lib

        if not os.path.isfile(path):
            raise FileNotFoundError(f"LIBOQS_PATH does not exist as a file: {path}")

        _debug(f"[pq] Preloading liboqs: {path}")
        ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
    except Exception as e:
        # Non-fatal, but extremely helpful to print once
        print(f"Failed to load liboqs from LIBOQS_PATH {os.environ.get('LIBOQS_PATH','')}: {e}")


@dataclass(frozen=True)
class Signature:
    alg_id: int
    alg_name: str
    domain: str
    prehash: PrehashKind
    chain_id: int
    pk: bytes
    sig: bytes


def _normalize_alg(alg: str | int) -> Tuple[int, str]:
    if isinstance(alg, int):
        if alg == DILITHIUM3_ID:
            return (DILITHIUM3_ID, "dilithium3")
        raise ValueError(f"Unknown alg id: {alg}")

    a = alg.strip().lower().replace("_", "-")
    if a in ("dilithium3", "dilithium-3"):
        return (DILITHIUM3_ID, "dilithium3")
    raise ValueError(f"Unknown alg name: {alg}")


def build_domain_tag(*, chain_id: int, domain: str) -> bytes:
    """
    Domain separation + chain binding.

    Keep this format stable once shipped; node verification must match.
    """
    if not isinstance(chain_id, int):
        raise TypeError("chain_id must be int")
    if not domain or not isinstance(domain, str):
        raise TypeError("domain must be non-empty str")

    # "animica" + NUL + u32be(chain_id) + NUL + domain(utf-8) + NUL
    cid = int(chain_id) & 0xFFFFFFFF
    return b"animica\x00" + cid.to_bytes(4, "big") + b"\x00" + domain.encode("utf-8") + b"\x00"


def build_sign_bytes(
    msg: bytes,
    *,
    domain: str,
    chain_id: Optional[int],
    prehash: PrehashKind = "none",
    context: bytes = b"",
) -> bytes:
    if not isinstance(msg, (bytes, bytearray, memoryview)):
        raise TypeError("msg must be bytes-like")

    if chain_id is None:
        raise ValueError("chain_id is required for Animica domain-tag signing")

    if context and not isinstance(context, (bytes, bytearray, memoryview)):
        raise TypeError("context must be bytes-like")

    domain_tag = build_domain_tag(chain_id=int(chain_id), domain=domain)

    payload = bytes(msg)
    if prehash == "sha3-256":
        payload = hashlib.sha3_256(payload).digest()
    elif prehash != "none":
        raise ValueError(f"Unknown prehash: {prehash}")

    # domain_tag || context_len(u16be) || context || payload
    ctx = bytes(context)
    if len(ctx) > 65535:
        raise ValueError("context too large")
    return domain_tag + len(ctx).to_bytes(2, "big") + ctx + payload


def _oqs_mech_for_alg(alg_id: int) -> str:
    # For now we only support Dilithium3 under the dilithium3 alg_id.
    if alg_id == DILITHIUM3_ID:
        return OQS_MECH_FOR_DILITHIUM3
    raise ValueError(f"No oqs mechanism mapping for alg_id={alg_id}")


def pq_sign_detached(
    msg: bytes,
    alg: str | int,
    sk: bytes,
    *,
    pk: bytes,
    domain: str,
    chain_id: int,
    prehash: PrehashKind = "none",
    context: bytes = b"",
) -> Dict[str, Any]:
    """
    Returns a tx signature envelope (CBOR-friendly map).
    Keys match what the CLI tx sender expects and what the node should verify.
    """
    _ensure_liboqs_loaded()

    alg_id, alg_name = _normalize_alg(alg)

    # Late import so our preload has a chance to work
    import oqs  # type: ignore

    mech = _oqs_mech_for_alg(alg_id)

    enabled = oqs.get_enabled_sig_mechanisms()
    if mech not in enabled:
        raise RuntimeError(
            f"Requested mechanism '{mech}' not enabled in liboqs runtime. "
            f"Enabled sample={tuple(enabled[:12])}. "
            f"Fix by installing liboqs v0.14.x and setting LIBOQS_PATH/LD_LIBRARY_PATH."
        )

    sign_bytes = build_sign_bytes(
        bytes(msg),
        domain=domain,
        chain_id=int(chain_id),
        prehash=prehash,
        context=context,
    )

    with oqs.Signature(mech) as s:
        sig = s.sign(sign_bytes, bytes(sk))

    return {
        "alg": int(alg_id),
        "pk": bytes(pk),
        "sig": bytes(sig),
        "domain": str(domain),
        "prehash": str(prehash),
    }


# Back-compat exports (some callers import one or the other)
def sign_detached(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    return pq_sign_detached(*args, **kwargs)

