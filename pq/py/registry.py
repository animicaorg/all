from __future__ import annotations

"""
pq.py.registry
==============

Canonical registry for **post-quantum algorithms** used by Animica.

- Loads numeric algorithm IDs from `../alg_ids.yaml`
- Provides typed metadata (key sizes, signature/ciphertext sizes)
- Detects optional backends (liboqs, WASM, pure-Python fallbacks)
- Offers convenience helpers to resolve algs by id or name, and to pick defaults

This module is **non-consensus** (it does not do any crypto itself). Consensus-
critical encodings and hash domains live in spec/ and lower-level packages.
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional, Union

try:
    import yaml  # type: ignore
except Exception as _e:
    yaml = None

# ---------------------------
# Typed metadata structures
# ---------------------------


@dataclass(frozen=True)
class AlgInfoBase:
    alg_id: int  # canonical numeric ID loaded from alg_ids.yaml
    name: str  # canonical snake-case name
    display: str  # human label
    kind: str  # "sig" or "kem"
    provider_hint: str  # "liboqs", "pure", "wasm", "system"
    security_bits: int  # claimed classical security bits (approx)
    notes: str  # short notes (variant, hash family, etc.)


@dataclass(frozen=True)
class SigAlgInfo(AlgInfoBase):
    pubkey_size: int
    seckey_size: int
    signature_size: int

    # Friendly aliases expected by tests/SDKs
    @property
    def pk_len(self) -> int:  # pragma: no cover - trivial alias
        return self.pubkey_size

    @property
    def sk_len(self) -> int:  # pragma: no cover
        return self.seckey_size

    @property
    def sig_len(self) -> int:  # pragma: no cover
        return self.signature_size


@dataclass(frozen=True)
class KemAlgInfo(AlgInfoBase):
    pubkey_size: int
    seckey_size: int
    ciphertext_size: int
    shared_secret_size: int

    @property
    def pk_len(self) -> int:  # pragma: no cover
        return self.pubkey_size

    @property
    def sk_len(self) -> int:  # pragma: no cover
        return self.seckey_size

    @property
    def ct_len(self) -> int:  # pragma: no cover
        return self.ciphertext_size

    @property
    def ss_len(self) -> int:  # pragma: no cover
        return self.shared_secret_size


# ---------------------------
# Backend feature detection
# ---------------------------


def _has_liboqs() -> bool:
    try:
        # Local optional binding loader (guards its own dlopen)
        from .algs import oqs_backend  # type: ignore

        return oqs_backend.is_available()
    except Exception:
        return False


def _has_wasm() -> bool:
    # Placeholder flag for browser/wasm contexts (extension/SDK may flip this).
    return False


FEATURES = {
    "liboqs": _has_liboqs(),
    "wasm": _has_wasm(),
    "pure_python": True,  # we always ship guarded pure-Python fallbacks for dev/test
}


# ---------------------------
# Load canonical alg IDs
# ---------------------------


def _load_alg_ids_yaml() -> Dict[str, int]:
    """
    Read ../alg_ids.yaml which maps canonical names → numeric IDs.

    The YAML may contain hex (e.g. 0x0103) or decimal. We normalize to int.
    """
    # Resolve relative to this file: pq/py/registry.py → pq/alg_ids.yaml
    here = Path(__file__).resolve()
    alg_ids_path = here.parent.parent / "alg_ids.yaml"
    if yaml is None or not alg_ids_path.exists():
        # Sensible hard-coded fallback that will be overridden when the YAML is present.
        return {
            "dilithium3": 0x0103,
            "sphincs_shake_128s": 0x0201,
            "kyber768": 0x0300,
        }
    data = yaml.safe_load(alg_ids_path.read_text(encoding="utf-8"))
    out: Dict[str, int] = {}
    if isinstance(data, dict):
        registry_entries = data.get("registry")
        if isinstance(registry_entries, list):
            for entry in registry_entries:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name")
                raw_id = entry.get("id")
                if not isinstance(name, str) or raw_id is None:
                    continue
                if isinstance(raw_id, str):
                    raw_id = raw_id.strip().lower()
                    num = (
                        int(raw_id, 16)
                        if raw_id.startswith("0x")
                        else int(re.sub(r"_", "", raw_id), 10)
                    )
                elif isinstance(raw_id, int):
                    num = raw_id
                else:
                    continue
                out[name] = num
        else:
            for k, v in data.items():
                if isinstance(v, str):
                    # accept "0xABCD" or "1234"
                    v = v.strip().lower()
                    if v.startswith("0x"):
                        num = int(v, 16)
                    else:
                        # allow underscores
                        num = int(re.sub(r"_", "", v), 10)
                elif isinstance(v, int):
                    num = v
                else:
                    # Ignore non-numeric metadata keys (e.g., updated timestamps)
                    continue
                out[str(k)] = num
    return out


ALG_IDS: Dict[str, int] = _load_alg_ids_yaml()

# Reverse map for fast lookup
ALG_NAMES_BY_ID: Dict[int, str] = {v: k for k, v in ALG_IDS.items()}
# Backwards-compatibility aliases used by downstream modules
ALG_ID = ALG_IDS
ALG_NAME = ALG_NAMES_BY_ID


# ---------------------------
# Canonical metadata table
# ---------------------------

# Sizes taken from NIST PQC finalists/standards (approx, may vary by impl):
# Dilithium3: pk=1952, sk=4000, sig=3293
# SPHINCS+-SHAKE-128s: pk=32, sk=64, sig=7856
# Kyber-768 (ML-KEM-768): pk=1184, sk=2400, ct=1088, ss=32


def _mk_sig_info(
    name: str,
    display: str,
    sec_bits: int,
    pk: int,
    sk: int,
    sig: int,
    notes: str = "",
    provider: str = "liboqs",
) -> SigAlgInfo:
    return SigAlgInfo(
        alg_id=ALG_IDS.get(name, -1),
        name=name,
        display=display,
        kind="sig",
        provider_hint=provider,
        security_bits=sec_bits,
        pubkey_size=pk,
        seckey_size=sk,
        signature_size=sig,
        notes=notes,
    )


def _mk_kem_info(
    name: str,
    display: str,
    sec_bits: int,
    pk: int,
    sk: int,
    ct: int,
    ss: int = 32,
    notes: str = "",
    provider: str = "liboqs",
) -> KemAlgInfo:
    return KemAlgInfo(
        alg_id=ALG_IDS.get(name, -1),
        name=name,
        display=display,
        kind="kem",
        provider_hint=provider,
        security_bits=sec_bits,
        pubkey_size=pk,
        seckey_size=sk,
        ciphertext_size=ct,
        shared_secret_size=ss,
        notes=notes,
    )


_SIGS: Dict[str, SigAlgInfo] = {
    "dilithium3": _mk_sig_info(
        "dilithium3",
        "CRYSTALS-Dilithium3",
        128,
        pk=1952,
        sk=4000,
        sig=3293,
        notes="L3; lattice (MLWE); NIST PQC standard",
        provider="liboqs" if FEATURES["liboqs"] else "pure",
    ),
    "sphincs_shake_128s": _mk_sig_info(
        "sphincs_shake_128s",
        "SPHINCS+ SHAKE-128s",
        128,
        pk=32,
        sk=64,
        sig=7856,
        notes="L1; stateless hash-based; SHAKE variant",
        provider=(
            "liboqs" if FEATURES["liboqs"] else ("wasm" if FEATURES["wasm"] else "pure")
        ),
    ),
}

_KEMS: Dict[str, KemAlgInfo] = {
    "kyber768": _mk_kem_info(
        "kyber768",
        "ML-KEM / Kyber-768",
        128,
        pk=1184,
        sk=2400,
        ct=1088,
        ss=32,
        notes="L3; IND-CCA2 KEM; NIST PQC standard",
        provider="liboqs" if FEATURES["liboqs"] else "pure",
    ),
}

# Maps by numeric id too (filled using ALG_IDS)
_SIGS_BY_ID: Dict[int, SigAlgInfo] = {
    ALG_IDS[k]: v for k, v in _SIGS.items() if k in ALG_IDS
}
_KEMS_BY_ID: Dict[int, KemAlgInfo] = {
    ALG_IDS[k]: v for k, v in _KEMS.items() if k in ALG_IDS
}

# Public registries consumed by tests/SDKs
SIGNATURES = _SIGS
KEMS = _KEMS
BY_NAME: Dict[str, AlgInfo] = {**SIGNATURES, **KEMS}
BY_ID: Dict[int, AlgInfo] = {**_SIGS_BY_ID, **_KEMS_BY_ID}


# ---------------------------
# Public API
# ---------------------------

AlgNameOrId = Union[str, int]
AlgInfo = Union[SigAlgInfo, KemAlgInfo]


def is_signature_alg(name_or_id: AlgNameOrId) -> bool:
    if isinstance(name_or_id, str):
        return name_or_id in _SIGS
    return name_or_id in _SIGS_BY_ID


def is_kem_alg(name_or_id: AlgNameOrId) -> bool:
    if isinstance(name_or_id, str):
        return name_or_id in _KEMS
    return name_or_id in _KEMS_BY_ID


def is_known_alg_id(alg_id: int) -> bool:
    return alg_id in ALG_NAMES_BY_ID


def is_sig_alg_id(alg_id: int) -> bool:
    return alg_id in _SIGS_BY_ID


def is_kem_alg_id(alg_id: int) -> bool:
    return alg_id in _KEMS_BY_ID


def get(name_or_id: AlgNameOrId) -> Optional[AlgInfo]:
    if isinstance(name_or_id, str):
        return _SIGS.get(name_or_id) or _KEMS.get(name_or_id)
    return _SIGS_BY_ID.get(name_or_id) or _KEMS_BY_ID.get(name_or_id)


def get_sig(name_or_id: AlgNameOrId) -> Optional[SigAlgInfo]:
    if isinstance(name_or_id, str):
        return _SIGS.get(name_or_id)
    return _SIGS_BY_ID.get(name_or_id)


def get_kem(name_or_id: AlgNameOrId) -> Optional[KemAlgInfo]:
    if isinstance(name_or_id, str):
        return _KEMS.get(name_or_id)
    return _KEMS_BY_ID.get(name_or_id)


def require_sig(name_or_id: AlgNameOrId) -> SigAlgInfo:
    out = get_sig(name_or_id)
    if out is None:
        raise KeyError(f"Signature algorithm not found: {name_or_id!r}")
    if out.alg_id < 0:
        raise RuntimeError(f"Signature algorithm {out.name} has no canonical ID loaded")
    return out


def require_kem(name_or_id: AlgNameOrId) -> KemAlgInfo:
    out = get_kem(name_or_id)
    if out is None:
        raise KeyError(f"KEM algorithm not found: {name_or_id!r}")
    if out.alg_id < 0:
        raise RuntimeError(f"KEM algorithm {out.name} has no canonical ID loaded")
    return out


def list_signature_algs() -> Iterable[SigAlgInfo]:
    return _SIGS.values()


def list_kem_algs() -> Iterable[KemAlgInfo]:
    return _KEMS.values()


def list_all() -> Iterable[AlgInfo]:
    yield from _SIGS.values()
    yield from _KEMS.values()


def default_signature_alg() -> SigAlgInfo:
    """
    Choose the chain's *default* signature algorithm for new wallets/addresses.

    Policy (subject to pq_policy.yaml at higher layers):
    - Prefer Dilithium3 when liboqs is available (fast, widely supported).
    - Otherwise fall back to SPHINCS+ SHAKE-128s (hash-based, slower, robust).
    """
    if FEATURES["liboqs"]:
        return require_sig("dilithium3")
    return require_sig("sphincs_shake_128s")


def default_kem_alg() -> KemAlgInfo:
    """
    Default KEM for P2P handshakes and session key establishment.
    """
    return require_kem("kyber768")


def id_of(name: str) -> int:
    info = get(name)
    if not info:
        raise KeyError(f"Unknown algorithm name {name!r}")
    return info.alg_id


def name_of(alg_id: int) -> str:
    n = ALG_NAMES_BY_ID.get(alg_id)
    if n is None:
        raise KeyError(f"Unknown algorithm id 0x{alg_id:04x}")
    return n


def describe(name_or_id: AlgNameOrId) -> str:
    info = get(name_or_id)
    if info is None:
        return f"<unknown:{name_or_id}>"
    if info.kind == "sig":
        s = info  # type: ignore
        return (
            f"{s.display} [{s.name}] id=0x{s.alg_id:04x} kind=sig "
            f"pk={s.pubkey_size}B sk={s.seckey_size}B sig={s.signature_size}B "
            f"sec≈{s.security_bits}b provider={s.provider_hint}"
        )
    else:
        k = info  # type: ignore
        return (
            f"{k.display} [{k.name}] id=0x{k.alg_id:04x} kind=kem "
            f"pk={k.pubkey_size}B sk={k.seckey_size}B ct={k.ciphertext_size}B ss={k.shared_secret_size}B "
            f"sec≈{k.security_bits}b provider={k.provider_hint}"
        )


def dump_registry_json() -> str:
    """
    Debug helper: JSON dump of all algs with sizes and ids.
    """

    def asdict(a: AlgInfoBase) -> dict:
        d = a.__dict__.copy()
        d["alg_id_hex"] = f"0x{a.alg_id:04x}" if a.alg_id >= 0 else None
        return d

    all_objs = [asdict(x) for x in list_all()]
    return json.dumps(all_objs, indent=2, sort_keys=True)


# ---------------------------
# Module-level constants
# ---------------------------

# Export commonly used constants, *resolved* from YAML to avoid drift.
DILITHIUM3_ID: int = ALG_IDS.get("dilithium3", -1)
SPHINCS_SHAKE_128S_ID: int = ALG_IDS.get("sphincs_shake_128s", -1)
KYBER768_ID: int = ALG_IDS.get("kyber768", -1)

# Sanity checks in dev: ensure ids exist (won't raise in prod; used by tests)
if __name__ == "__main__":
    print("PQ Registry:", dump_registry_json())
