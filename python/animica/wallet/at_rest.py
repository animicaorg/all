"""At-rest encryption for wallet secret-key material (ANM-C07).

Historically the wallet store (``~/.animica/wallets.json``) persisted the PQ
signing key as a plaintext ``secret_key_hex`` field. Anyone with read access to
the file (backup, stolen laptop, shared volume, log scrape) obtained a
spend-capable key.

This module adds *optional* at-rest encryption of that secret material. It is
intentionally **additive and backward compatible**:

  * The default on-disk format is unchanged. Existing plaintext wallets load and
    save exactly as before — nothing in a running deployment breaks.
  * Encryption is *opt-in*: an operator explicitly migrates a store with a
    passphrase (``encrypt_store_secrets``) or via the ``ANIMICA_WALLET_ENCRYPT``
    / ``ANIMICA_WALLET_PASSPHRASE`` env pair used by higher layers.
  * An encrypted entry replaces ``secret_key_hex`` with a self-describing
    ``secret_key_enc`` envelope (KDF params + salt + AEAD nonce + ciphertext).
    A reader *without* the passphrase still sees the wallet's address/pubkey
    (read-only ops keep working) but cannot recover the secret — fail-closed.
  * A reader *with* the passphrase transparently recovers ``secret_key_hex`` in
    memory only; the persisted canonical form never re-materializes plaintext
    (see ``serialization.export_canonical_store``).

Crypto choices (best available in the venv, degrading safely):

  * KDF   — argon2id (if ``argon2-cffi`` present) → scrypt (``hashlib.scrypt``)
            → PBKDF2-HMAC-SHA3-256 (high iteration count). All memory/CPU-hard
            or high-iteration; salt is random per wallet.
  * AEAD  — AES-256-GCM via ``cryptography`` (if present) → a vendored
            SHAKE256-CTR + HMAC-SHA3-256 encrypt-then-MAC construction that
            depends only on ``hashlib``/``hmac``. Nonce is random per wallet.

SECURITY: this module NEVER logs, prints, or otherwise emits secret key material
or the passphrase. Callers must uphold the same.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Envelope constants
# ---------------------------------------------------------------------------

ENVELOPE_FORMAT = "animica.secret.enc"
ENVELOPE_VERSION = 1

# Env used to supply the passphrase for transparent decrypt / migration.
PASSPHRASE_ENV = "ANIMICA_WALLET_PASSPHRASE"
PASSPHRASE_FILE_ENV = "ANIMICA_WALLET_PASSPHRASE_FILE"
# Opt-in signal that new/rewritten stores should encrypt secrets. Consumed by
# higher layers (CLI/save paths); this module exposes it for callers.
ENCRYPT_ENV = "ANIMICA_WALLET_ENCRYPT"

# KDF identifiers
KDF_ARGON2ID = "argon2id"
KDF_SCRYPT = "scrypt"
KDF_PBKDF2_SHA3 = "pbkdf2_hmac_sha3_256"

# AEAD identifiers
AEAD_AES_256_GCM = "aes-256-gcm"
AEAD_SHAKE_HMAC = "shake256ctr-hmac-sha3-256"

_KEY_LEN = 32  # 256-bit AEAD key
_GCM_NONCE_LEN = 12
_FALLBACK_NONCE_LEN = 24
_SALT_LEN = 16


class WalletEncryptionError(Exception):
    """Raised when encrypting/decrypting wallet secret material fails."""


# ---------------------------------------------------------------------------
# Capability probing
# ---------------------------------------------------------------------------

def _have_argon2() -> bool:
    try:
        import argon2  # type: ignore  # noqa: F401
        from argon2.low_level import hash_secret_raw  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def _have_scrypt() -> bool:
    return hasattr(hashlib, "scrypt")


def _have_aesgcm() -> bool:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def preferred_kdf() -> str:
    """Return the strongest KDF available in this environment."""
    if _have_argon2():
        return KDF_ARGON2ID
    if _have_scrypt():
        return KDF_SCRYPT
    return KDF_PBKDF2_SHA3


def preferred_aead() -> str:
    """Return the strongest AEAD available in this environment."""
    if _have_aesgcm():
        return AEAD_AES_256_GCM
    return AEAD_SHAKE_HMAC


def _default_kdf_params(kdf: str) -> Dict[str, Any]:
    if kdf == KDF_ARGON2ID:
        # ~64 MiB, 3 passes — interactive-secure defaults.
        return {"time_cost": 3, "memory_cost": 64 * 1024, "parallelism": 1, "dklen": _KEY_LEN}
    if kdf == KDF_SCRYPT:
        # n=2^15, r=8, p=1 — memory-hard, ~32 MiB working set.
        return {"n": 1 << 15, "r": 8, "p": 1, "dklen": _KEY_LEN}
    if kdf == KDF_PBKDF2_SHA3:
        return {"iterations": 600_000, "dklen": _KEY_LEN}
    raise WalletEncryptionError(f"unknown kdf '{kdf}'")


# ---------------------------------------------------------------------------
# Passphrase resolution
# ---------------------------------------------------------------------------

def resolve_passphrase(explicit: Optional[str] = None) -> Optional[str]:
    """Resolve a passphrase from an explicit value, env var, or env file.

    Order: explicit arg > ``ANIMICA_WALLET_PASSPHRASE`` > file named by
    ``ANIMICA_WALLET_PASSPHRASE_FILE``. Returns ``None`` when no passphrase is
    configured. NEVER logs the passphrase itself.
    """
    if explicit is not None and explicit != "":
        return explicit
    env_val = os.environ.get(PASSPHRASE_ENV)
    if env_val:
        return env_val
    file_path = os.environ.get(PASSPHRASE_FILE_ENV)
    if file_path:
        try:
            with open(os.path.expanduser(file_path), "r", encoding="utf-8") as fh:
                data = fh.read()
            # Strip a single trailing newline (common for `echo ... > file`).
            return data[:-1] if data.endswith("\n") else data
        except OSError as exc:
            log.warning("wallet passphrase file %s unreadable: %s", file_path, exc)
            return None
    return None


def _passphrase_bytes(passphrase: str) -> bytes:
    if isinstance(passphrase, bytes):
        return passphrase
    if not isinstance(passphrase, str):
        raise WalletEncryptionError("passphrase must be str or bytes")
    if passphrase == "":
        raise WalletEncryptionError("passphrase must not be empty")
    return passphrase.encode("utf-8")


# ---------------------------------------------------------------------------
# Key derivation
# ---------------------------------------------------------------------------

def _derive_key(passphrase: str, salt: bytes, *, kdf: str, params: Dict[str, Any]) -> bytes:
    pw = _passphrase_bytes(passphrase)
    dklen = int(params.get("dklen", _KEY_LEN))
    if kdf == KDF_ARGON2ID:
        try:
            from argon2.low_level import Type, hash_secret_raw  # type: ignore
        except Exception as exc:  # pragma: no cover - env dependent
            raise WalletEncryptionError("argon2 unavailable to derive key") from exc
        return hash_secret_raw(
            secret=pw,
            salt=salt,
            time_cost=int(params["time_cost"]),
            memory_cost=int(params["memory_cost"]),
            parallelism=int(params["parallelism"]),
            hash_len=dklen,
            type=Type.ID,
        )
    if kdf == KDF_SCRYPT:
        if not _have_scrypt():  # pragma: no cover - env dependent
            raise WalletEncryptionError("scrypt unavailable to derive key")
        n = int(params["n"])
        r = int(params["r"])
        p = int(params["p"])
        # OpenSSL enforces maxmem > 128*n*r*p; give generous headroom.
        maxmem = 128 * n * r * p + (1 << 25)
        return hashlib.scrypt(pw, salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=maxmem)
    if kdf == KDF_PBKDF2_SHA3:
        return hashlib.pbkdf2_hmac(
            "sha3_256", pw, salt, int(params["iterations"]), dklen=dklen
        )
    raise WalletEncryptionError(f"unknown kdf '{kdf}'")


# ---------------------------------------------------------------------------
# AEAD
# ---------------------------------------------------------------------------

def _aead_encrypt(aead: str, key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
    if aead == AEAD_AES_256_GCM:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
        return AESGCM(key).encrypt(nonce, plaintext, aad)
    if aead == AEAD_SHAKE_HMAC:
        return _fallback_encrypt(key, nonce, plaintext, aad)
    raise WalletEncryptionError(f"unknown aead '{aead}'")


def _aead_decrypt(aead: str, key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    if aead == AEAD_AES_256_GCM:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
        from cryptography.exceptions import InvalidTag  # type: ignore
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, aad)
        except InvalidTag as exc:
            raise WalletEncryptionError("authentication failed (wrong passphrase or corrupt data)") from exc
    if aead == AEAD_SHAKE_HMAC:
        return _fallback_decrypt(key, nonce, ciphertext, aad)
    raise WalletEncryptionError(f"unknown aead '{aead}'")


# Vendored AEAD (pure stdlib): SHAKE256 keystream (CTR-like) + encrypt-then-MAC
# with HMAC-SHA3-256. Used only when `cryptography` is not importable. This is a
# standard encrypt-then-MAC construction over a XOF keystream; it is authenticated.
_FALLBACK_TAG_LEN = 32


def _fallback_subkeys(key: bytes) -> tuple[bytes, bytes]:
    enc_key = hashlib.sha3_256(b"animica-at-rest-enc\x00" + key).digest()
    mac_key = hashlib.sha3_256(b"animica-at-rest-mac\x01" + key).digest()
    return enc_key, mac_key


def _fallback_keystream(enc_key: bytes, nonce: bytes, length: int) -> bytes:
    xof = hashlib.shake_256()
    xof.update(enc_key)
    xof.update(nonce)
    return xof.digest(length)


def _fallback_encrypt(key: bytes, nonce: bytes, plaintext: bytes, aad: bytes) -> bytes:
    enc_key, mac_key = _fallback_subkeys(key)
    ks = _fallback_keystream(enc_key, nonce, len(plaintext))
    ct = bytes(a ^ b for a, b in zip(plaintext, ks))
    # Authenticate nonce + aad-length + aad + ciphertext to bind everything.
    mac = hmac.new(mac_key, digestmod=hashlib.sha3_256)
    mac.update(nonce)
    mac.update(len(aad).to_bytes(8, "big"))
    mac.update(aad)
    mac.update(ct)
    return ct + mac.digest()


def _fallback_decrypt(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes) -> bytes:
    if len(ciphertext) < _FALLBACK_TAG_LEN:
        raise WalletEncryptionError("ciphertext too short")
    ct, tag = ciphertext[:-_FALLBACK_TAG_LEN], ciphertext[-_FALLBACK_TAG_LEN:]
    enc_key, mac_key = _fallback_subkeys(key)
    mac = hmac.new(mac_key, digestmod=hashlib.sha3_256)
    mac.update(nonce)
    mac.update(len(aad).to_bytes(8, "big"))
    mac.update(aad)
    mac.update(ct)
    if not hmac.compare_digest(mac.digest(), tag):
        raise WalletEncryptionError("authentication failed (wrong passphrase or corrupt data)")
    ks = _fallback_keystream(enc_key, nonce, len(ct))
    return bytes(a ^ b for a, b in zip(ct, ks))


# ---------------------------------------------------------------------------
# Envelope encode / decode
# ---------------------------------------------------------------------------

def is_encrypted_secret(value: Any) -> bool:
    """True iff ``value`` looks like one of our secret-key envelopes."""
    return (
        isinstance(value, dict)
        and value.get("_fmt") == ENVELOPE_FORMAT
        and isinstance(value.get("ct"), str)
        and isinstance(value.get("salt"), str)
        and isinstance(value.get("nonce"), str)
    )


def _aad_for(address: Optional[str]) -> bytes:
    # Bind the ciphertext to the wallet address so an envelope can't be swapped
    # between entries. Empty when address unknown (still authenticated).
    if isinstance(address, str) and address:
        return b"animica.wallet.addr:" + address.encode("utf-8")
    return b""


def encrypt_secret_hex(
    secret_key_hex: str,
    passphrase: str,
    *,
    kdf: Optional[str] = None,
    aead: Optional[str] = None,
    address: Optional[str] = None,
    kdf_params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Encrypt a hex secret key into a self-describing envelope dict.

    The returned dict contains NO plaintext key material. It is JSON-safe.
    """
    if not isinstance(secret_key_hex, str) or not secret_key_hex.strip():
        raise WalletEncryptionError("secret_key_hex must be a non-empty hex string")
    raw = secret_key_hex.strip().lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    try:
        pt = bytes.fromhex(raw)
    except ValueError as exc:
        raise WalletEncryptionError("secret_key_hex is not valid hex") from exc

    chosen_kdf = kdf or preferred_kdf()
    chosen_aead = aead or preferred_aead()
    params = dict(kdf_params) if kdf_params else _default_kdf_params(chosen_kdf)

    salt = secrets.token_bytes(_SALT_LEN)
    nonce_len = _GCM_NONCE_LEN if chosen_aead == AEAD_AES_256_GCM else _FALLBACK_NONCE_LEN
    nonce = secrets.token_bytes(nonce_len)

    key = _derive_key(passphrase, salt, kdf=chosen_kdf, params=params)
    aad = _aad_for(address)
    ct = _aead_encrypt(chosen_aead, key, nonce, pt, aad)

    return {
        "_fmt": ENVELOPE_FORMAT,
        "v": ENVELOPE_VERSION,
        "kdf": chosen_kdf,
        "kdf_params": params,
        "aead": chosen_aead,
        "salt": salt.hex(),
        "nonce": nonce.hex(),
        "ct": ct.hex(),
    }


def decrypt_secret_hex(
    envelope: Dict[str, Any],
    passphrase: str,
    *,
    address: Optional[str] = None,
) -> str:
    """Decrypt a secret-key envelope back to a lowercase hex string.

    Raises :class:`WalletEncryptionError` on any structural problem or an auth
    failure (wrong passphrase / tampering). Never logs the recovered secret.
    """
    if not is_encrypted_secret(envelope):
        raise WalletEncryptionError("not a valid encrypted secret envelope")
    kdf = str(envelope.get("kdf") or "")
    aead = str(envelope.get("aead") or "")
    params = envelope.get("kdf_params")
    if not isinstance(params, dict):
        raise WalletEncryptionError("envelope missing kdf_params")
    try:
        salt = bytes.fromhex(str(envelope["salt"]))
        nonce = bytes.fromhex(str(envelope["nonce"]))
        ct = bytes.fromhex(str(envelope["ct"]))
    except (ValueError, KeyError) as exc:
        raise WalletEncryptionError("envelope has malformed hex fields") from exc

    key = _derive_key(passphrase, salt, kdf=kdf, params=params)
    aad = _aad_for(address)
    try:
        pt = _aead_decrypt(aead, key, nonce, ct, aad)
    except WalletEncryptionError:
        # Retry once without address binding for envelopes written before the
        # address was known (e.g. imported without an address). Still fully
        # authenticated by the AEAD tag.
        if aad:
            pt = _aead_decrypt(aead, key, nonce, ct, b"")
        else:
            raise
    return pt.hex()


# ---------------------------------------------------------------------------
# Store-level helpers (operate on the canonical {"wallets": [...]} shape)
# ---------------------------------------------------------------------------

def store_is_encrypted(store: Dict[str, Any]) -> bool:
    """True if any wallet entry carries an encrypted secret envelope."""
    if not isinstance(store, dict):
        return False
    for w in store.get("wallets", []) or []:
        if isinstance(w, dict) and is_encrypted_secret(w.get("secret_key_enc")):
            return True
    return False


def encrypt_store_secrets(
    store: Dict[str, Any],
    passphrase: str,
    *,
    kdf: Optional[str] = None,
    aead: Optional[str] = None,
) -> Dict[str, Any]:
    """Return a copy of ``store`` with every plaintext secret encrypted.

    Each wallet's ``secret_key_hex`` is moved into a ``secret_key_enc``
    envelope and the plaintext field is removed. Entries already encrypted, or
    with no secret, are left untouched. This is the explicit opt-in migration
    path. NEVER logs secret material.
    """
    if not passphrase:
        raise WalletEncryptionError("a passphrase is required to encrypt the store")
    out = dict(store)
    new_wallets = []
    for w in store.get("wallets", []) or []:
        if not isinstance(w, dict):
            new_wallets.append(w)
            continue
        entry = dict(w)
        if is_encrypted_secret(entry.get("secret_key_enc")):
            entry.pop("secret_key_hex", None)
            new_wallets.append(entry)
            continue
        secret = entry.get("secret_key_hex") or entry.get("secretKeyHex")
        if isinstance(secret, str) and secret.strip() and secret.strip() != "0x":
            entry["secret_key_enc"] = encrypt_secret_hex(
                secret, passphrase, kdf=kdf, aead=aead, address=entry.get("address")
            )
            entry.pop("secret_key_hex", None)
            entry.pop("secretKeyHex", None)
        new_wallets.append(entry)
    out["wallets"] = new_wallets
    return out


def decrypt_store_secrets(
    store: Dict[str, Any],
    passphrase: str,
    *,
    strict: bool = False,
) -> Dict[str, Any]:
    """Return a copy of ``store`` with encrypted secrets restored *in memory*.

    For each encrypted wallet, ``secret_key_hex`` is repopulated from the
    envelope. The ``secret_key_enc`` field is preserved so a subsequent
    canonical save (which strips the transient plaintext) keeps the on-disk
    form encrypted.

    When ``strict`` is False (the default, used by transparent read paths) a
    per-entry decrypt failure is logged (without secret material) and that
    entry is left encrypted so read-only operations keep working. When
    ``strict`` is True, the first failure raises.
    """
    out = dict(store)
    new_wallets = []
    for w in store.get("wallets", []) or []:
        if not isinstance(w, dict):
            new_wallets.append(w)
            continue
        env = w.get("secret_key_enc")
        if not is_encrypted_secret(env):
            new_wallets.append(w)
            continue
        entry = dict(w)
        try:
            entry["secret_key_hex"] = decrypt_secret_hex(
                env, passphrase, address=entry.get("address")
            )
        except WalletEncryptionError as exc:
            if strict:
                raise
            # Do NOT include secret material; identify by address/label only.
            log.warning(
                "wallet secret decrypt failed for %s: %s (leaving encrypted)",
                entry.get("address") or entry.get("label") or "<unknown>",
                exc,
            )
        new_wallets.append(entry)
    out["wallets"] = new_wallets
    return out


__all__ = [
    "WalletEncryptionError",
    "ENVELOPE_FORMAT",
    "ENVELOPE_VERSION",
    "PASSPHRASE_ENV",
    "PASSPHRASE_FILE_ENV",
    "ENCRYPT_ENV",
    "KDF_ARGON2ID",
    "KDF_SCRYPT",
    "KDF_PBKDF2_SHA3",
    "AEAD_AES_256_GCM",
    "AEAD_SHAKE_HMAC",
    "preferred_kdf",
    "preferred_aead",
    "resolve_passphrase",
    "encrypt_secret_hex",
    "decrypt_secret_hex",
    "is_encrypted_secret",
    "store_is_encrypted",
    "encrypt_store_secrets",
    "decrypt_store_secrets",
]
