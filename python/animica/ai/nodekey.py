"""
animica.ai.nodekey — the receipt-signing identity for ``animica ai serve``.

7.1.1 (Verifiable Inference Engine) signs every Proof-of-Inference receipt with
an ML-DSA-65 (FIPS-204, alg_id 0x1003) key. This module loads — or, on first
run, creates — a *dedicated inference-signing key* stored under the ENA home.

Security posture (deliberate):
  * This is NOT the node's consensus key and NOT a wallet key. It signs receipts
    only and controls no funds, so ``animica ai serve`` never holds a
    funds-bearing secret. (The 7.1.0 wallet-drain incident is exactly why we do
    not reuse a spending key here.)
  * The secret never leaves this process: no RPC, no HTTP route, no log line ever
    returns it. Only the *public* key + address travel inside receipts so anyone
    can verify a signature.
  * Fail-open: when ``ANIMICA_PQ_MODE=disabled`` (or the PQ backend is
    unavailable, or the key dir is unwritable) this returns ``None`` and the
    caller emits hash-only, unsigned receipts. Signing is a bonus, never a
    hard dependency of serving a completion.
"""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# The FIPS-204 signature scheme used across the chain/wallet (0x1003).
ML_DSA_65 = "ml_dsa_65"

_KEY_FILENAME = "inference_signing_key.json"
_cached: "Optional[InferenceKey]" = None
_cache_path: Optional[str] = None


@dataclass(frozen=True)
class InferenceKey:
    """A loaded ML-DSA-65 identity used only to sign inference receipts."""

    alg_name: str
    alg_id: int
    public_key: bytes
    secret_key: bytes
    address: str

    @property
    def public_key_hex(self) -> str:
        return self.public_key.hex()


def pq_disabled() -> bool:
    return (os.environ.get("ANIMICA_PQ_MODE", "").strip().lower()
            in {"disabled", "off", "0", "false", "no"})


def ena_home() -> Path:
    """Resolve the ENA home dir the serve process persists state under."""
    for var in ("ANIMICA_AI_HOME", "ANIMICA_ENA_HOME"):
        val = os.environ.get(var)
        if val:
            return Path(val).expanduser()
    return Path(os.environ.get("ANIMICA_HOME", str(Path.home() / ".animica"))).expanduser()


def key_path(home: Optional[Path] = None) -> Path:
    return (home or ena_home()) / _KEY_FILENAME


def _write_atomic(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    # Create the temp file 0600 ATOMICALLY (O_CREAT with mode) so the secret is
    # never world-readable, not even for a transient window — os.replace then
    # preserves the mode. A prior chmod-after-write left a 0644 race if chmod was
    # swallowed.
    fd = os.open(tmp, os.O_CREAT | os.O_WRONLY | os.O_TRUNC,
                 stat.S_IRUSR | stat.S_IWUSR)
    try:
        os.write(fd, data.encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


def load_or_create_key(home: Optional[Path] = None) -> Optional[InferenceKey]:
    """Return the inference-signing key, generating one on first use.

    Returns ``None`` (fail-open) when PQ is disabled/unavailable or the key
    directory cannot be created — callers then emit unsigned, hash-only receipts.
    """
    global _cached, _cache_path
    if pq_disabled():
        return None

    path = key_path(home)
    if _cached is not None and _cache_path == str(path):
        return _cached

    try:
        from pq.py.keygen import keygen_sig  # noqa: PLC0415 — lazy: heavy vendor import
    except Exception:  # noqa: BLE001 — PQ backend missing → fail-open unsigned
        return None

    key: Optional[InferenceKey] = None
    if path.exists():
        key = _load_from_disk(path)
    if key is None:
        try:
            kp = keygen_sig(ML_DSA_65)
            key = InferenceKey(
                alg_name=getattr(kp, "alg_name", ML_DSA_65),
                alg_id=int(getattr(kp, "alg_id", 0x1003)),
                public_key=bytes(kp.public_key),
                secret_key=bytes(kp.secret_key),
                address=str(getattr(kp, "address", "")),
            )
            _persist(path, key)
        except Exception:  # noqa: BLE001 — keygen or write failed → fail-open
            return None

    _cached, _cache_path = key, str(path)
    return key


def _load_from_disk(path: Path) -> Optional[InferenceKey]:
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        return InferenceKey(
            alg_name=blob.get("alg_name", ML_DSA_65),
            alg_id=int(blob.get("alg_id", 0x1003)),
            public_key=bytes.fromhex(blob["public_key_hex"]),
            secret_key=bytes.fromhex(blob["secret_key_hex"]),
            address=blob.get("address", ""),
        )
    except Exception:  # noqa: BLE001 — corrupt/partial file → regenerate
        return None


def _persist(path: Path, key: InferenceKey) -> None:
    _write_atomic(path, json.dumps({
        "alg_name": key.alg_name,
        "alg_id": key.alg_id,
        "public_key_hex": key.public_key.hex(),
        "secret_key_hex": key.secret_key.hex(),
        "address": key.address,
        "purpose": "animica.ai.proof-of-inference — receipt signing only (no funds)",
    }, indent=2))


def public_identity(home: Optional[Path] = None) -> Optional[dict]:
    """Non-secret identity (pubkey + address) for /health and diagnostics."""
    key = load_or_create_key(home)
    if key is None:
        return None
    return {"alg_name": key.alg_name, "alg_id": key.alg_id,
            "public_key_hex": key.public_key_hex, "address": key.address}


def reset_cache() -> None:
    """Drop the in-process cache (tests set a fresh ANIMICA_AI_HOME)."""
    global _cached, _cache_path
    _cached, _cache_path = None, None
