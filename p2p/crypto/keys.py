from __future__ import annotations

"""
Node identity keys (PQ signatures) for P2P
==========================================

Provides a small facade to generate, load, save, and use a node's static
identity key for the P2P layer. Supports Dilithium3 and SPHINCS+ (SHAKE-128s)
via the local `pq` package.

Keystore format (JSON)
----------------------
{
  "kty": "ANIMICA-P2P",
  "version": 1,
  "alg": "dilithium3" | "sphincs_shake_128s",
  "pubkey": "<hex>",
  "cipher": "AES-256-GCM",
  "kdf": {"name": "scrypt", "n": 32768, "r": 8, "p": 1, "salt": "<hex>"},
  "crypto": {"nonce": "<hex>", "ct": "<hex>"},
  "created_at": "2025-01-01T00:00:00Z"
}

Security notes
--------------
- Secret keys at rest are encrypted with AES-256-GCM using a key derived by
  hashlib.scrypt(passphrase, salt, n,r,p, dklen=32).
- Each direction (read/write) is constant-time with respect to key length but
  NOT side-channel hardened beyond what cryptography provides. Run on trusted
  hosts only.
"""

import json
import os
import time
from dataclasses import dataclass
from hashlib import scrypt as _scrypt
from typing import Optional, Tuple

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except Exception as _e:  # pragma: no cover
    AESGCM = None  # type: ignore
    _aead_err = _e
else:
    _aead_err = None

# --- PQ facade ---------------------------------------------------------------

# We depend on the previously provided pq package.
# The registry helps resolve algorithm names/ids and sizes.
from pq.py import keygen as pq_keygen
from pq.py import registry as pq_registry
from pq.py import sign as pq_sign
from pq.py import verify as pq_verify


def _resolve_alg_name(alg: str | int) -> str:
    """
    Turn a name or id into a canonical *name* ('dilithium3' or 'sphincs_shake_128s').
    We are defensive in case the registry exposes different helpers.
    """
    # If user already passed a canonical name, normalize it.
    if isinstance(alg, str):
        name = alg.strip().lower().replace("-", "_")
        # Try direct acceptance
        try:
            # Check existence in registry
            _ = pq_registry.id_of(name)  # type: ignore[attr-defined]
            return name
        except Exception:
            pass
        # Try a common alias map
        aliases = {
            "d3": "dilithium3",
            "sphincs": "sphincs_shake_128s",
            "sphincs_shake": "sphincs_shake_128s",
        }
        if name in aliases:
            return aliases[name]
        if name in ("dilithium3", "sphincs_shake_128s"):
            return name
        raise ValueError(f"unknown PQ algorithm name: {alg}")

    # It's an int id → ask the registry to map id→name.
    for attr in ("name_of", "name_from_id", "name"):
        fn = getattr(pq_registry, attr, None)
        if callable(fn):
            return fn(int(alg))  # type: ignore[call-arg]
    # Last resort: try a known table
    id_to_name = getattr(pq_registry, "ID_TO_NAME", None)
    if isinstance(id_to_name, dict) and int(alg) in id_to_name:
        return id_to_name[int(alg)]
    raise ValueError(f"cannot resolve algorithm id: {alg}")


def _ensure_sig_alg(name: str) -> None:
    """Ensure alg is a signature algorithm (not a KEM)."""
    chk = None
    for attr in ("is_signature_alg", "is_sig_alg", "is_signature"):
        fn = getattr(pq_registry, attr, None)
        if callable(fn):
            chk = fn(name)  # type: ignore[call-arg]
            break
    if chk is None:
        # fallback: assume both d3 and sphincs are signature algs
        chk = name in ("dilithium3", "sphincs_shake_128s")
    if not chk:
        raise ValueError(f"{name} is not a signature algorithm")


def _keypair(name: str) -> Tuple[bytes, bytes]:
    """Generate (pub, sec) for a signature algorithm using pq.keygen."""
    # Flexible call across possible registry styles:
    # Prefer keygen.keypair(name=...), else keygen.keypair(alg_id=...), else keygen.keygen(...)
    if hasattr(pq_keygen, "keypair"):
        try:
            return pq_keygen.keypair(name=name)  # type: ignore[call-arg]
        except TypeError:
            # maybe wants alg_id
            try:
                alg_id = pq_registry.id_of(name)  # type: ignore[attr-defined]
                return pq_keygen.keypair(alg_id=alg_id)  # type: ignore[call-arg]
            except Exception:
                pass
    # Fallback older signature: keygen(alg)
    try:
        return pq_keygen.keygen(name)  # type: ignore[arg-type]
    except Exception:
        alg_id = pq_registry.id_of(name)  # type: ignore[attr-defined]
        return pq_keygen.keygen(alg_id)  # type: ignore[arg-type]


def _sign(name: str, sk: bytes, msg: bytes, domain: bytes) -> bytes:
    if hasattr(pq_sign, "sign"):
        try:
            return pq_sign.sign(name=name, sk=sk, msg=msg, domain=domain)  # type: ignore[call-arg]
        except TypeError:
            alg_id = pq_registry.id_of(name)  # type: ignore[attr-defined]
            return pq_sign.sign(alg_id=alg_id, sk=sk, msg=msg, domain=domain)  # type: ignore[call-arg]
    raise RuntimeError("pq.sign.sign not found")


def _verify(name: str, pk: bytes, msg: bytes, sig: bytes, domain: bytes) -> bool:
    if hasattr(pq_verify, "verify"):
        try:
            return bool(
                pq_verify.verify(name=name, pk=pk, msg=msg, sig=sig, domain=domain)  # type: ignore[call-arg]
            )
        except TypeError:
            alg_id = pq_registry.id_of(name)  # type: ignore[attr-defined]
            return bool(
                pq_verify.verify(alg_id=alg_id, pk=pk, msg=msg, sig=sig, domain=domain)  # type: ignore[call-arg]
            )
    raise RuntimeError("pq.verify.verify not found")


# --- Identity object ---------------------------------------------------------

P2P_ID_SIGN_DOMAIN = b"animica/p2p/id-sign/v1"


@dataclass
class NodeIdentity:
    alg: str  # canonical name (e.g., 'dilithium3', 'sphincs_shake_128s')
    pubkey: bytes
    seckey: bytes  # decrypted in-memory

    def sign(self, msg: bytes, *, domain: bytes = P2P_ID_SIGN_DOMAIN) -> bytes:
        """Sign bytes with domain separation."""
        return _sign(self.alg, self.seckey, msg, domain)

    def verify(
        self, msg: bytes, sig: bytes, *, domain: bytes = P2P_ID_SIGN_DOMAIN
    ) -> bool:
        """Verify a signature against our public key."""
        return _verify(self.alg, self.pubkey, msg, sig, domain)

    def public_info(self) -> dict:
        """Export public description suitable for HELLO/IDENTIFY."""
        # We don’t compute peer-id here (lives in p2p/crypto/peer_id.py)
        return {
            "alg": self.alg,
            "pubkey": self.pubkey.hex(),
        }


# --- Keystore I/O ------------------------------------------------------------


def _kdf_scrypt(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return _scrypt(
        password=passphrase.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=32
    )


def _aead_encrypt(
    key: bytes, nonce: bytes, plaintext: bytes, aad: Optional[bytes] = None
) -> bytes:
    if AESGCM is None:  # pragma: no cover
        raise RuntimeError(f"cryptography AESGCM unavailable: {_aead_err}")
    return AESGCM(key).encrypt(nonce, plaintext, aad)


def _aead_decrypt(
    key: bytes, nonce: bytes, ciphertext: bytes, aad: Optional[bytes] = None
) -> bytes:
    if AESGCM is None:  # pragma: no cover
        raise RuntimeError(f"cryptography AESGCM unavailable: {_aead_err}")
    return AESGCM(key).decrypt(nonce, ciphertext, aad)


def save_keystore(
    ident: NodeIdentity,
    path: str,
    passphrase: str,
    *,
    n: int = 1 << 15,
    r: int = 8,
    p: int = 1,
) -> None:
    """
    Save identity into an encrypted keystore JSON file.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    salt = os.urandom(16)
    nonce = os.urandom(12)
    kdf_key = _kdf_scrypt(passphrase, salt, n, r, p)
    aad = b"animica/ks/v1"
    ct = _aead_encrypt(kdf_key, nonce, ident.seckey, aad)

    ks = {
        "kty": "ANIMICA-P2P",
        "version": 1,
        "alg": ident.alg,
        "pubkey": ident.pubkey.hex(),
        "cipher": "AES-256-GCM",
        "kdf": {"name": "scrypt", "n": n, "r": r, "p": p, "salt": salt.hex()},
        "crypto": {"nonce": nonce.hex(), "ct": ct.hex()},
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ks, f, separators=(",", ":"), sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def load_keystore(path: str, passphrase: str) -> NodeIdentity:
    """
    Load and decrypt a keystore JSON file.
    """
    with open(path, "r", encoding="utf-8") as f:
        ks = json.load(f)

    if ks.get("kty") != "ANIMICA-P2P" or ks.get("version") != 1:
        raise ValueError("unsupported keystore kind/version")

    alg = _resolve_alg_name(ks["alg"])
    _ensure_sig_alg(alg)

    kdf = ks.get("kdf") or {}
    if kdf.get("name") != "scrypt":
        raise ValueError("unsupported kdf")
    salt = bytes.fromhex(kdf["salt"])
    n = int(kdf["n"])
    r = int(kdf["r"])
    p = int(kdf["p"])
    key = _kdf_scrypt(passphrase, salt, n, r, p)

    crypto = ks.get("crypto") or {}
    nonce = bytes.fromhex(crypto["nonce"])
    ct = bytes.fromhex(crypto["ct"])
    aad = b"animica/ks/v1"
    sk = _aead_decrypt(key, nonce, ct, aad)
    pk = bytes.fromhex(ks["pubkey"])

    return NodeIdentity(alg=alg, pubkey=pk, seckey=sk)


# --- High-level helpers ------------------------------------------------------


def generate(alg: str = "dilithium3") -> NodeIdentity:
    """
    Generate a fresh identity for the given signature algorithm.
    """
    alg_name = _resolve_alg_name(alg)
    _ensure_sig_alg(alg_name)
    pk, sk = _keypair(alg_name)
    return NodeIdentity(alg=alg_name, pubkey=pk, seckey=sk)


def load_or_create(
    path: str, passphrase: str, *, alg: str = "dilithium3"
) -> NodeIdentity:
    """
    Load a keystore if present; otherwise generate & save a new identity.
    """
    if os.path.exists(path):
        return load_keystore(path, passphrase)
    ident = generate(alg=alg)
    save_keystore(ident, path, passphrase)
    return ident


# --- Small self-test (manual) ------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import argparse

    ap = argparse.ArgumentParser(description="Animica P2P identity keystore tool")
    ap.add_argument("--file", required=True, help="path to keystore.json")
    ap.add_argument("--pass", dest="pw", required=True, help="passphrase")
    ap.add_argument("--alg", default="dilithium3", help="dilithium3|sphincs_shake_128s")
    ap.add_argument(
        "--make", action="store_true", help="generate new keystore if missing"
    )
    args = ap.parse_args()

    if args.make:
        ident = load_or_create(args.file, args.pw, alg=args.alg)
        print(
            f"[+] keystore ready: {args.file} alg={ident.alg} pub={ident.pubkey.hex()[:16]}…"
        )
    else:
        ident = load_keystore(args.file, args.pw)
        print(f"[+] loaded: alg={ident.alg} pub={ident.pubkey.hex()[:16]}…")
        msg = b"test message"
        sig = ident.sign(msg)
        ok = ident.verify(msg, sig)
        print(f"[+] sign/verify ok={ok}")
