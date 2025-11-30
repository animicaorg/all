"""
Mnemonic helpers (BIP-39-like) → deterministic seed using PBKDF2/HKDF over SHA3.

Design notes
------------
- Generation: we lean on the widely used `mnemonic` (Trezor) package **only to
  generate and checksum phrases** (12 or 24 English words). Seed derivation
  is **not** BIP-39 standard here: we intentionally replace HMAC-SHA512 with
  HMAC-SHA3-256 as per Animica’s PQ-friendly policy to keep a consistent hash
  suite across the stack.

- Import: you can import *any* valid BIP-39 English phrase (12/24 words). We
  validate with `mnemonic.Mnemonic.check()` when the package is available; if
  it isn't installed we fall back to a light sanity check (word count only).

- Seed derivation (Animica):
    PBKDF2-HMAC-SHA3-256(
        password = NFKD(mnemonic),
        salt     = b"animica-mnemonic" + NFKD(passphrase),
        iter     = 2048,
        dkLen    = 32
    )  -> 32-byte seed

  From that 32-byte seed, use HKDF-SHA3-256 with domain-separated `info`
  strings to derive sub-keys for concrete algorithms (e.g. Dilithium3,
  SPHINCS+). See `derive_subseed()`.

- Compatibility: The browser extension uses the *same* PBKDF2/HKDF scheme and
  domain strings, so restoring a wallet between the extension and this SDK
  yields identical keys.

Install tip (optional):
    pip install mnemonic
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
from typing import Optional

# Optional dependency for generating / validating standard BIP-39 word phrases.
try:
    from mnemonic import Mnemonic  # type: ignore
except Exception:  # pragma: no cover - optional path
    Mnemonic = None  # type: ignore


# ---------- Public API ----------


def create_mnemonic(num_words: int = 24) -> str:
    """
    Create a new BIP-39 English mnemonic (12 or 24 words).

    Note: Requires the optional `mnemonic` package. We only use it for
    *generation & checksum*. Seed derivation uses SHA3 (see below).
    """
    if Mnemonic is None:
        raise ImportError(
            "The 'mnemonic' package is required to generate phrases. "
            "Install with: pip install mnemonic"
        )
    if num_words not in (12, 24):
        raise ValueError("num_words must be 12 or 24")
    strength = 128 if num_words == 12 else 256
    return Mnemonic("english").generate(strength=strength)


def validate_mnemonic(phrase: str) -> bool:
    """
    Validate a mnemonic phrase. If the optional `mnemonic` package is present,
    we verify checksum strictly; otherwise we only check word count (12/24).
    """
    words = [w for w in phrase.strip().split() if w]
    if Mnemonic is not None:
        return bool(Mnemonic("english").check(" ".join(words)))
    # Fallback: len check only (still lets users import; checksum is skipped)
    return len(words) in (12, 24)


def mnemonic_to_seed(
    phrase: str,
    passphrase: str = "",
    *,
    iterations: int = 2048,
    dklen: int = 32,
    salt_prefix: str = "animica-mnemonic",
) -> bytes:
    """
    Convert a mnemonic phrase to a 32-byte seed using PBKDF2-HMAC-SHA3-256.

    Parameters
    ----------
    phrase : str
        BIP-39-like mnemonic (English wordlist typical).
    passphrase : str
        Optional extra entropy; normalized with NFKD (like BIP-39).
    iterations : int
        PBKDF2 iteration count (default 2048).
    dklen : int
        Derived key length (default 32 bytes).
    salt_prefix : str
        Salt domain tag. Default "animica-mnemonic" to avoid confusing with
        BIP-39's "mnemonic".

    Returns
    -------
    bytes
        32-byte seed suitable as input to HKDF for algorithm-specific keys.
    """
    phrase_n = _normalize(phrase)
    pass_n = _normalize(passphrase)
    salt = (salt_prefix + pass_n).encode("utf-8")

    # Python's pbkdf2_hmac supports 'sha3_256' as the hash name.
    seed = hashlib.pbkdf2_hmac(
        "sha3_256",
        phrase_n.encode("utf-8"),
        salt,
        iterations,
        dklen=dklen,
    )
    return seed


def derive_subseed(
    master_seed: bytes,
    *,
    purpose: str,
    index: int = 0,
    length: int = 32,
    salt: bytes = b"AnimicaHKDFv1",
) -> bytes:
    """
    HKDF-SHA3-256 derive a sub-key from the 32-byte master seed with
    domain-separated `purpose`.

        prk = HMAC_SHA3(salt, IKM=master_seed)
        okm = HKDF-Expand(prk, info, L=length)

    Common purposes (conventions used in this repo):
        - "dilithium3"
        - "sphincs-shake-128s"
        - "address"      (deriving address material)
        - "encryption"   (vault/session keys)

    The `index` parameter lets you deterministically derive multiple children.
    """
    if len(master_seed) < 16:
        raise ValueError("master_seed should be at least 16 bytes")

    prk = _hkdf_extract_sha3(salt, master_seed)
    info = f"omni-sdk:{purpose}:{index}".encode("utf-8")
    return _hkdf_expand_sha3(prk, info, length)


def random_entropy(num_words: int = 24) -> bytes:
    """
    Generate raw entropy suitable for creating a mnemonic via the `mnemonic`
    package (helper for callers who want to BYO generator).

    Returns 16 bytes for 12 words, or 32 bytes for 24 words.
    """
    if num_words not in (12, 24):
        raise ValueError("num_words must be 12 or 24")
    size = 16 if num_words == 12 else 32
    return secrets.token_bytes(size)


# ---------- Internal helpers ----------


def _normalize(s: str) -> str:
    # Follow BIP-39 normalization for inputs.
    return unicodedata.normalize("NFKD", s)


def _hkdf_extract_sha3(salt: bytes, ikm: bytes) -> bytes:
    return hmac.new(salt, ikm, hashlib.sha3_256).digest()


def _hkdf_expand_sha3(prk: bytes, info: bytes, length: int) -> bytes:
    """
    RFC5869 HKDF-Expand with SHA3-256. Length must be <= 255*hash_len.
    """
    hash_len = hashlib.sha3_256().digest_size
    if length > 255 * hash_len:
        raise ValueError("Cannot expand to more than 255 * HashLen bytes")
    t = b""
    okm = b""
    counter = 1
    while len(okm) < length:
        t = hmac.new(prk, t + info + bytes([counter]), hashlib.sha3_256).digest()
        okm += t
        counter += 1
    return okm[:length]


__all__ = [
    "create_mnemonic",
    "validate_mnemonic",
    "mnemonic_to_seed",
    "derive_subseed",
    "random_entropy",
]
