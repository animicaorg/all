"""
File keystore (AES-256-GCM) — lock/unlock a master seed or key bundle.

Design
------
- We encrypt an opaque byte payload (typically the 32-byte master seed from
  `mnemonic_to_seed`) using AES-GCM with a key derived via
  PBKDF2-HMAC-SHA3-256.
- On disk we store a small JSON envelope alongside the ciphertext.
- Changing the passphrase re-encrypts the payload with a new salt & nonce.
- No plaintext is written to disk; writes are atomic (tmp file + replace).

JSON envelope schema (version=1)
--------------------------------
{
  "version": 1,
  "kdf": "PBKDF2-SHA3-256",
  "kdf_iters": 200000,
  "salt": "<hex>",
  "aead": "AES-256-GCM",
  "nonce": "<hex>",
  "ciphertext": "<hex>",  # includes GCM tag (as returned by cryptography's AESGCM)
  "created_at": "2025-01-01T00:00:00Z",
  "updated_at": "2025-01-01T00:00:00Z",
  "meta": { ... }         # optional user metadata (label, network, etc.)
}

Dependencies
------------
- Requires `cryptography` package (AESGCM). If missing, we raise ImportError
  with a helpful message.
"""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import hashlib
import hmac

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
except Exception as e:  # pragma: no cover - optional path
    AESGCM = None  # type: ignore


# ----- Errors -----------------------------------------------------------------


class KeystoreError(Exception):
    """Base keystore error."""


class KeystoreLocked(KeystoreError):
    """Raised when trying to access secret material without unlocking."""


class KeystoreCryptoError(KeystoreError):
    """Cryptographic failure (bad passphrase, corrupted file, etc.)."""


class KeystoreIOError(KeystoreError):
    """Filesystem or serialization error."""


# ----- Datatypes ---------------------------------------------------------------


@dataclass
class KeystoreInfo:
    path: Path
    version: int
    kdf: str
    kdf_iters: int
    aead: str
    created_at: str
    updated_at: str
    meta: Dict[str, Any]


# ----- Public API --------------------------------------------------------------


def create(
    path: os.PathLike[str] | str,
    payload: bytes,
    passphrase: str,
    *,
    kdf_iters: int = 200_000,
    meta: Optional[Dict[str, Any]] = None,
) -> KeystoreInfo:
    """
    Create a new keystore file at `path` encrypting `payload` with AES-GCM.

    Parameters
    ----------
    payload : bytes
        Secret material to protect (e.g., 32-byte master seed).
    passphrase : str
        Passphrase used for key derivation (PBKDF2-HMAC-SHA3-256).
    kdf_iters : int
        PBKDF2 iteration count. Defaults to 200k (fast enough for tests,
        consider 300k–600k for production).
    meta : dict
        Optional metadata to store alongside (labels, etc.).

    Returns
    -------
    KeystoreInfo
    """
    _require_crypto()

    salt = secrets.token_bytes(16)
    key = _pbkdf2_sha3(passphrase, salt, kdf_iters, dklen=32)

    nonce = secrets.token_bytes(12)
    aes = AESGCM(key)
    ciphertext = aes.encrypt(nonce, payload, associated_data=None)

    now = _now_iso()
    envelope = {
        "version": 1,
        "kdf": "PBKDF2-SHA3-256",
        "kdf_iters": kdf_iters,
        "salt": _hex(salt),
        "aead": "AES-256-GCM",
        "nonce": _hex(nonce),
        "ciphertext": _hex(ciphertext),
        "created_at": now,
        "updated_at": now,
        "meta": meta or {},
    }

    _atomic_write_json(path, envelope)
    _chmod_private(path)

    info = KeystoreInfo(
        path=Path(path),
        version=1,
        kdf="PBKDF2-SHA3-256",
        kdf_iters=kdf_iters,
        aead="AES-256-GCM",
        created_at=now,
        updated_at=now,
        meta=envelope["meta"],
    )
    return info


def unlock(path: os.PathLike[str] | str, passphrase: str) -> Tuple[KeystoreInfo, bytes]:
    """
    Decrypt and return the protected payload.

    Raises KeystoreCryptoError on wrong passphrase or file corruption.
    """
    _require_crypto()

    env = _read_json(path)
    try:
        _check_envelope(env)
        kdf_iters = int(env["kdf_iters"])
        salt = _unhex(env["salt"])
        nonce = _unhex(env["nonce"])
        ciphertext = _unhex(env["ciphertext"])
    except Exception as e:
        raise KeystoreIOError(f"Malformed keystore: {e}") from e

    key = _pbkdf2_sha3(passphrase, salt, kdf_iters, dklen=32)
    aes = AESGCM(key)
    try:
        payload = aes.decrypt(nonce, ciphertext, associated_data=None)
    except Exception as e:
        raise KeystoreCryptoError("Decryption failed (bad passphrase or corrupted file)") from e

    info = KeystoreInfo(
        path=Path(path),
        version=int(env["version"]),
        kdf=str(env["kdf"]),
        kdf_iters=kdf_iters,
        aead=str(env["aead"]),
        created_at=str(env["created_at"]),
        updated_at=str(env["updated_at"]),
        meta=dict(env.get("meta") or {}),
    )
    return info, payload


def change_passphrase(
    path: os.PathLike[str] | str, old_passphrase: str, new_passphrase: str, *, new_iters: Optional[int] = None
) -> KeystoreInfo:
    """
    Re-encrypt the payload with a new passphrase (and optionally a new iteration count).
    """
    info, payload = unlock(path, old_passphrase)
    kdf_iters = int(new_iters or info.kdf_iters)

    # Write a new envelope
    return create(path, payload, new_passphrase, kdf_iters=kdf_iters, meta=info.meta)


def read_info(path: os.PathLike[str] | str) -> KeystoreInfo:
    """Read envelope metadata without decrypting the payload."""
    env = _read_json(path)
    _check_envelope(env)
    return KeystoreInfo(
        path=Path(path),
        version=int(env["version"]),
        kdf=str(env["kdf"]),
        kdf_iters=int(env["kdf_iters"]),
        aead=str(env["aead"]),
        created_at=str(env["created_at"]),
        updated_at=str(env["updated_at"]),
        meta=dict(env.get("meta") or {}),
    )


# ----- Internals ---------------------------------------------------------------


def _require_crypto() -> None:
    if AESGCM is None:
        raise ImportError(
            "Keystore requires the 'cryptography' package. "
            "Install with: pip install cryptography"
        )


def _pbkdf2_sha3(passphrase: str, salt: bytes, iters: int, dklen: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha3_256", passphrase.encode("utf-8"), salt, iters, dklen=dklen)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _hex(b: bytes) -> str:
    return b.hex()


def _unhex(s: str) -> bytes:
    # Accept both lowercase/uppercase hex; strip '0x' if present.
    s = s[2:] if s.startswith(("0x", "0X")) else s
    return bytes.fromhex(s)


def _atomic_write_json(path: os.PathLike[str] | str, obj: Dict[str, Any]) -> None:
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")
        with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(path.parent)) as tmp:
            tmp.write(data)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_name = tmp.name
        os.replace(tmp_name, path)  # atomic on POSIX
    except Exception as e:  # pragma: no cover - hard to simulate all FS errors
        raise KeystoreIOError(f"Failed to write keystore: {e}") from e


def _read_json(path: os.PathLike[str] | str) -> Dict[str, Any]:
    try:
        with open(path, "rb") as f:
            return json.loads(f.read().decode("utf-8"))
    except FileNotFoundError as e:
        raise KeystoreIOError(f"Keystore not found: {path}") from e
    except Exception as e:
        raise KeystoreIOError(f"Failed to read keystore: {e}") from e


def _chmod_private(path: os.PathLike[str] | str) -> None:
    try:
        p = Path(path)
        if os.name == "posix":
            os.chmod(p, 0o600)
        # On Windows we skip explicit ACLs; users should rely on profile isolation.
    except Exception:
        # Best-effort; do not fail the operation if chmod is unavailable.
        pass


def _check_envelope(env: Dict[str, Any]) -> None:
    if int(env.get("version", 0)) != 1:
        raise KeystoreIOError("Unsupported keystore version")
    if env.get("kdf") != "PBKDF2-SHA3-256":
        raise KeystoreIOError("Unsupported KDF")
    if env.get("aead") != "AES-256-GCM":
        raise KeystoreIOError("Unsupported AEAD")
    if not isinstance(env.get("kdf_iters"), int) or env["kdf_iters"] <= 0:
        raise KeystoreIOError("Invalid kdf_iters")
    for k in ("salt", "nonce", "ciphertext", "created_at", "updated_at"):
        if k not in env:
            raise KeystoreIOError(f"Missing field: {k}")


__all__ = [
    "KeystoreError",
    "KeystoreLocked",
    "KeystoreCryptoError",
    "KeystoreIOError",
    "KeystoreInfo",
    "create",
    "unlock",
    "change_passphrase",
    "read_info",
]
