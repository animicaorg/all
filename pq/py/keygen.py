from __future__ import annotations

"""
keygen.py — Uniform key generation API for Animica PQ cryptography.

Goals
-----
- One function to generate signature OR KEM keypairs, selected by alg name/id.
- Deterministic (seeded) or non-deterministic (OS RNG) generation.
- For signature keys, also derive a canonical Animica address (bech32m).
- Graceful failure if the selected algorithm backend is unavailable.

Public API
----------
- keygen(alg, *, seed: bytes|str|None = None, hrp: str = "anim")
    → SigKeypair | KemKeypair
- keygen_sig(alg, *, seed=None, hrp="anim")
    → SigKeypair
- keygen_kem(alg, *, seed=None)
    → KemKeypair

Where `alg` can be:
- an integer alg_id (as per pq/alg_ids.yaml / pq.py.registry),
- or a canonical string name, e.g. "dilithium3", "sphincs_shake_128s", "kyber768".

Conventions
-----------
Signature address format: see pq.py.address.address_from_pubkey
payload = alg_id(1) || sha3_256(pubkey)(32)  → bech32m "anim1..."

Backends
--------
This module dispatches to:
- pq.py.algs.dilithium3
- pq.py.algs.sphincs_shake_128s
- pq.py.algs.kyber768

Each backend SHOULD expose:
    generate_keypair(seed: bytes | None = None) -> tuple[bytes, bytes]
and MAY expose an alias:
    keypair(seed: bytes | None = None) -> tuple[bytes, bytes]

If unavailable, we raise NotImplementedError with a helpful message.

"""

from dataclasses import dataclass
from typing import Optional, Tuple, Union, Literal

from pq.py.utils.rng import os_random
from pq.py.utils.hash import sha3_256
from pq.py.address import address_from_pubkey
from pq.py.registry import (
    ALG_ID,          # dict[str,int]
    ALG_NAME,        # dict[int,str]
    is_known_alg_id, # (int)->bool
    is_sig_alg_id,   # (int)->bool
    is_kem_alg_id,   # (int)->bool
)

AlgKind = Literal["sig", "kem"]


# ------------------------------------------------------------------------------
# Dataclasses
# ------------------------------------------------------------------------------

@dataclass(frozen=True)
class SigKeypair:
    alg_id: int
    alg_name: str
    public_key: bytes
    secret_key: bytes
    address: str  # bech32m (e.g., anim1...)

    def __repr__(self) -> str:
        pk8 = self.public_key[:8].hex()
        return (f"SigKeypair(alg={self.alg_name}/0x{self.alg_id:02x}, "
                f"pk[:8]={pk8}…, addr={self.address[:12]}…)")


@dataclass(frozen=True)
class KemKeypair:
    alg_id: int
    alg_name: str
    public_key: bytes
    secret_key: bytes

    def __repr__(self) -> str:
        pk8 = self.public_key[:8].hex()
        return (f"KemKeypair(alg={self.alg_name}/0x{self.alg_id:02x}, "
                f"pk[:8]={pk8}…)")


# ------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------

def _normalize_alg(alg: Union[int, str]) -> tuple[int, str, AlgKind]:
    """
    Resolve `alg` to (alg_id, alg_name, kind).

    Raises:
        ValueError if unknown or ambiguous.
    """
    if isinstance(alg, int):
        if not is_known_alg_id(alg):
            raise ValueError(f"Unknown alg_id: 0x{alg:02x}")
        name = ALG_NAME.get(alg, f"0x{alg:02x}")
        kind: AlgKind
        if is_sig_alg_id(alg):
            kind = "sig"
        elif is_kem_alg_id(alg):
            kind = "kem"
        else:
            raise ValueError(f"Algorithm id 0x{alg:02x} not classified as sig or kem")
        return alg, name, kind

    if isinstance(alg, str):
        normalized = alg.strip().lower()
        if normalized not in ALG_ID:
            raise ValueError(f"Unknown algorithm name: {alg!r}")
        alg_id = ALG_ID[normalized]
        kind: AlgKind
        if is_sig_alg_id(alg_id):
            kind = "sig"
        elif is_kem_alg_id(alg_id):
            kind = "kem"
        else:
            raise ValueError(f"Algorithm name {alg!r} not classified as sig or kem")
        return alg_id, normalized, kind

    raise TypeError("alg must be int (alg_id) or str (name)")


def _ensure_bytes_seed(seed: Optional[Union[bytes, str]], *, min_len: int = 0) -> Optional[bytes]:
    """
    Normalize seed param:
    - None → None (backend will use OS RNG)
    - bytes → bytes (unchanged)
    - str  → if startswith 'hex:' parse hex; else utf-8 encode
    Then, if provided and shorter than min_len, left-pad with zeros to min_len.
    """
    if seed is None:
        return None
    if isinstance(seed, bytes):
        b = seed
    elif isinstance(seed, str):
        s = seed.strip()
        if s.startswith("hex:"):
            h = s[4:].replace("_", "").replace(" ", "")
            b = bytes.fromhex(h)
        else:
            b = s.encode("utf-8")
    else:
        raise TypeError("seed must be bytes | str | None")

    if min_len and len(b) < min_len:
        b = b.rjust(min_len, b"\x00")
    return b


def _call_backend_keypair(module, seed: Optional[bytes]) -> Tuple[bytes, bytes]:
    """
    Call the chosen backend's keypair API with a best-effort interface.
    """
    if hasattr(module, "generate_keypair"):
        return module.generate_keypair(seed=seed)  # type: ignore[attr-defined]
    if hasattr(module, "keypair"):
        return module.keypair(seed=seed)  # type: ignore[attr-defined]
    raise NotImplementedError(f"Backend {module.__name__} does not expose generate_keypair/keypair")


# ------------------------------------------------------------------------------
# Public keygen (dispatcher)
# ------------------------------------------------------------------------------

def keygen(alg: Union[int, str], *, seed: bytes | str | None = None, hrp: str = "anim") -> SigKeypair | KemKeypair:
    alg_id, alg_name, kind = _normalize_alg(alg)
    if kind == "sig":
        return keygen_sig(alg_id, seed=seed, hrp=hrp)
    else:
        return keygen_kem(alg_id, seed=seed)


def keygen_sig(alg: Union[int, str], *, seed: bytes | str | None = None, hrp: str = "anim") -> SigKeypair:
    alg_id, alg_name, kind = _normalize_alg(alg)
    if kind != "sig":
        raise ValueError(f"{alg_name} is not a signature algorithm")

    # Defensive: pass None to let backend draw OS randomness. If a seed is provided,
    # we domain-separate it to reduce cross-alg collisions.
    seed_bytes = _ensure_bytes_seed(seed)
    if seed_bytes is not None:
        seed_bytes = sha3_256(b"animica:keygen:sig|" + bytes([alg_id]) + seed_bytes)

    # Dispatch to backend
    try:
        if alg_name == "dilithium3":
            from pq.py.algs import dilithium3 as backend
        elif alg_name == "sphincs_shake_128s":
            from pq.py.algs import sphincs_shake_128s as backend
        else:
            raise NotImplementedError(f"Signature backend not wired for {alg_name}")
    except Exception as e:
        raise NotImplementedError(
            f"Signature backend for {alg_name} not available. Install/build PQ backend (e.g., liboqs) and ensure wrappers are importable. ({e})"
        ) from e

    pk, sk = _call_backend_keypair(backend, seed_bytes)
    addr = address_from_pubkey(pk, alg_id, hrp=hrp)
    return SigKeypair(alg_id=alg_id, alg_name=alg_name, public_key=pk, secret_key=sk, address=addr)


def keygen_kem(alg: Union[int, str], *, seed: bytes | str | None = None) -> KemKeypair:
    alg_id, alg_name, kind = _normalize_alg(alg)
    if kind != "kem":
        raise ValueError(f"{alg_name} is not a KEM algorithm")

    seed_bytes = _ensure_bytes_seed(seed)
    if seed_bytes is not None:
        seed_bytes = sha3_256(b"animica:keygen:kem|" + bytes([alg_id]) + seed_bytes)

    try:
        if alg_name == "kyber768":
            from pq.py.algs import kyber768 as backend
        else:
            raise NotImplementedError(f"KEM backend not wired for {alg_name}")
    except Exception as e:
        raise NotImplementedError(
            f"KEM backend for {alg_name} not available. Install/build PQ backend (e.g., liboqs) and ensure wrappers are importable. ({e})"
        ) from e

    pk, sk = _call_backend_keypair(backend, seed_bytes)
    return KemKeypair(alg_id=alg_id, alg_name=alg_name, public_key=pk, secret_key=sk)


# ------------------------------------------------------------------------------
# CLI-ish Smoke (python -m pq.py.keygen dilithium3|kyber768 [hex:seed])
# ------------------------------------------------------------------------------

def _main() -> None:
    import sys
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print("Usage: python -m pq.py.keygen <alg> [seed]\n"
              "  alg  = dilithium3 | sphincs_shake_128s | kyber768 | <alg_id int>\n"
              "  seed = optional; bytes interpreted as utf-8; prefix with 'hex:' for hex")
        sys.exit(0)

    alg_raw: Union[str, int] = args[0]
    if alg_raw.isdigit():
        alg_val: Union[str, int] = int(alg_raw)
    else:
        alg_val = alg_raw

    seed = args[1] if len(args) > 1 else None

    try:
        kp = keygen(alg_val, seed=seed)
    except Exception as e:
        print("keygen failed:", e)
        sys.exit(2)

    if isinstance(kp, SigKeypair):
        print("alg:", kp.alg_name, f"(0x{kp.alg_id:02x}) [sig]")
        print("addr:", kp.address)
        print("pk:", kp.public_key.hex())
        print("sk:", kp.secret_key[:16].hex(), "… (hidden)")
    else:
        print("alg:", kp.alg_name, f"(0x{kp.alg_id:02x}) [kem]")
        print("pk:", kp.public_key.hex())
        print("sk:", kp.secret_key[:16].hex(), "… (hidden)")


if __name__ == "__main__":
    _main()
