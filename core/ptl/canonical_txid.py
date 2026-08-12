"""Canonical PTL transaction-id derivation (ANM-H09 / ANM-M10).

Historically the PTL (the default tx system, ``ANIMICA_TX_SYSTEM=ptl``) computed
``txid = sha3_256(raw wire bytes)`` and stored the inbound bytes verbatim. Because
the transaction CBOR decoder accepts non-minimal integers and ignores extra fields
(ANM-M05), multiple byte encodings of one *logical* transaction hash to distinct
PTL txids. That is transaction-id malleability: it bypasses PTL dedup and makes the
PTL txid disagree with the canonical txid used by the rest of the node (indexers,
receipts, block inclusion).

This module derives the txid from a **strict canonical re-encode** of the decoded
transaction, reusing the consensus canonical CBOR codec in
``coretx.canonical`` (the same encoder legitimate wallets/SDKs use to build wire
bytes). Two byte encodings of the same logical tx therefore map to a single txid.

Live-safety / backward-compat gating
-------------------------------------
This is a live chain, so enforcement is gated with safe defaults:

* ``strict=False`` (default; env ``ANIMICA_PTL_STRICT_TXID`` unset/false):
    - A payload that decodes and is *already canonical* is unaffected — the
      canonical bytes equal the raw bytes, so the txid is byte-for-byte identical
      to the legacy raw hash. Legitimate wallets emit canonical bytes, so honest
      traffic sees no change.
    - A payload that decodes but is *non-canonical* is still accepted (legacy
      behavior preserved) but logged loudly; it keeps the legacy raw-hash txid.
    - A payload that does not decode as a transaction envelope is treated as
      opaque bytes and hashed as-is (PTL has always accepted arbitrary bytes).

* ``strict=True`` (opt-in, ``ANIMICA_PTL_STRICT_TXID=1``): non-canonical and
  undecodable payloads are **rejected** (fail-closed), and the txid is derived
  from the canonical re-encode.

The relay txid<->bytes binding (ANM-M10) uses :func:`canonical_ptl_txid` to obtain
the authoritative txid and rejects any push whose advertised txid does not match —
that check is safe on-by-default because an honest peer always advertises
``sha3_256(bytes)`` for the bytes it sends.
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

from core.utils.hash import sha3_256

log = logging.getLogger("animica.ptl.canonical")

_STRICT_ENV = "ANIMICA_PTL_STRICT_TXID"
_TRUE = {"1", "true", "yes", "on"}


class NonCanonicalTxError(ValueError):
    """Raised in strict mode when PTL input is non-canonical or undecodable."""


def strict_txid_enabled(override: Optional[bool] = None) -> bool:
    """Resolve whether strict canonical-txid enforcement is active.

    ``override`` (from a service/relay config) wins when not ``None``; otherwise
    the ``ANIMICA_PTL_STRICT_TXID`` environment variable is consulted (default
    off, to preserve current live behavior).
    """
    if override is not None:
        return bool(override)
    return os.environ.get(_STRICT_ENV, "").strip().lower() in _TRUE


def _canonical_reencode(raw: bytes) -> Optional[bytes]:
    """Return the strict canonical CBOR re-encode of ``raw`` if it decodes as a
    transaction envelope; otherwise ``None``. Never raises."""
    try:
        from coretx.canonical import decode_tx_envelope, encode_tx_envelope
    except Exception:  # pragma: no cover - coretx should always be importable
        return None
    try:
        envelope = decode_tx_envelope(raw)
        return encode_tx_envelope(envelope)
    except Exception:
        return None


def canonical_ptl_txid(
    tx_bytes: bytes,
    *,
    strict: Optional[bool] = None,
) -> Tuple[bytes, bytes]:
    """Derive ``(store_bytes, txid)`` for a PTL transaction.

    ``store_bytes`` is what should be persisted and ``txid`` is the id that binds
    to those bytes (``sha3_256(store_bytes)``).

    See the module docstring for the strict/legacy behavior matrix.

    Raises:
        NonCanonicalTxError: in strict mode, when the input is non-canonical or
            not a decodable transaction envelope.
    """
    raw = bytes(tx_bytes)
    enforce = strict_txid_enabled(strict)
    canonical = _canonical_reencode(raw)

    if canonical is None:
        # Not decodable as a transaction envelope.
        if enforce:
            raise NonCanonicalTxError(
                "PTL strict mode: transaction bytes are not a decodable "
                "canonical envelope"
            )
        return raw, sha3_256(raw)

    if canonical == raw:
        # Already canonical: canonical hash == legacy raw hash (no behavior change).
        return canonical, sha3_256(canonical)

    # Decodes, but the wire bytes are a non-canonical encoding (ANM-H09 / ANM-M05).
    if enforce:
        raise NonCanonicalTxError(
            "PTL strict mode: non-canonical transaction encoding rejected "
            "(raw bytes != canonical re-encode)"
        )
    log.warning(
        "PTL accepted a non-canonical transaction encoding in legacy mode "
        "(ANM-H09); raw-hash txid disagrees with the canonical txid. Set "
        "ANIMICA_PTL_STRICT_TXID=1 to reject.",
        extra={
            "raw_txid": sha3_256(raw).hex()[:16],
            "canonical_txid": sha3_256(canonical).hex()[:16],
        },
    )
    return raw, sha3_256(raw)


__all__ = [
    "NonCanonicalTxError",
    "canonical_ptl_txid",
    "strict_txid_enabled",
]
