from __future__ import annotations

import base64
import hashlib
import logging
from pathlib import Path
from typing import Optional

from core.snapshot.manifest import SnapshotManifest

log = logging.getLogger("animica.snapshot.verify")


def _decode_signature(sig: str) -> bytes:
    if not sig:
        return b""
    try:
        return bytes.fromhex(sig)
    except ValueError:
        return base64.b64decode(sig)


def verify_manifest_signature(
    manifest: SnapshotManifest, trusted_pubkeys: list[str]
) -> tuple[bool, Optional[str]]:
    if not trusted_pubkeys:
        return True, None
    if not manifest.signature:
        return False, "manifest signature missing"

    alg = manifest.signature.get("alg") or manifest.signature.get("algorithm")
    sig = manifest.signature.get("sig") or manifest.signature.get("signature")
    if not alg or not sig:
        return False, "manifest signature missing alg or sig"

    try:
        from pq.py import verify as pq_verify
    except Exception as exc:
        return False, f"signature verification unavailable: {exc}"

    payload = manifest.canonical_payload()
    signature = _decode_signature(str(sig))
    for pk in trusted_pubkeys:
        try:
            pubkey = bytes.fromhex(pk)
        except ValueError:
            continue
        try:
            if pq_verify.verify_detached(str(alg), pubkey, payload, signature, domain="snapshot"):
                return True, None
        except Exception as exc:  # noqa: BLE001
            log.debug("Signature verification error", exc_info=exc)
            continue
    return False, "manifest signature did not match trusted keys"


def verify_chunk_hash(chunk_path: Path, expected: str) -> tuple[bool, Optional[str]]:
    if not expected:
        return False, "missing chunk hash"
    h = hashlib.sha256()
    with open(chunk_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            h.update(chunk)
    actual = "0x" + h.hexdigest()
    if actual != expected:
        return False, f"hash mismatch expected={expected} got={actual}"
    return True, None
