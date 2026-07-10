"""PTL transaction selection for block building."""

from __future__ import annotations

import logging
from typing import List, Optional

from core.ptl.model import PtlEntry, TxStatus
from core.ptl.service import PtlService

try:
    from execution.migrations.address_freeze_2026 import is_frozen as _consensus_frozen
except Exception:  # pragma: no cover - fail-open; block validation still enforces
    def _consensus_frozen(addr, *, height=None):  # type: ignore
        return False

try:
    from core.utils.tx import normalize_tx_envelope as _normalize_tx_envelope
except Exception:  # pragma: no cover - decoder unavailable; sender fast-path only
    _normalize_tx_envelope = None  # type: ignore

log = logging.getLogger("animica.ptl.selection")


def _normalized_recipient(body: object) -> object:
    """Recipient bytes from a canonical tx body (payload.v.to / payload.to / to)."""
    if not isinstance(body, dict):
        return None
    payload = body.get("payload")
    if isinstance(payload, dict):
        v = payload.get("v")
        if isinstance(v, dict) and v.get("to") is not None:
            return v.get("to")
        if payload.get("to") is not None:
            return payload.get("to")
    return body.get("to")


def _entry_spends_frozen(entry: "PtlEntry") -> bool:
    """True if a PTL candidate moves value FROM or TO a consensus-frozen account.

    PTL is the DEFAULT tx system, so this is the live block-building pre-gate: it
    keeps an upgraded miner from selecting a tx that its own block-validation rule
    (block_import._scan_block_frozen_addresses) would orphan — otherwise a peer
    that replicates a frozen-recipient tx into this node's PTL could make it
    repeatedly build self-orphaning blocks (a liveness grief). PtlEntry carries no
    recipient and an unreliable ``sender``, so we decode ``tx_bytes`` canonically
    to check BOTH. ``height=None`` => exclude unconditionally (never build it, even
    below the activation height). Undecodable/opaque bytes are treated as not
    frozen here — the authoritative block-import gate is the backstop.
    """
    if _consensus_frozen(getattr(entry, "sender", None)):
        return True
    raw = getattr(entry, "tx_bytes", None)
    if not raw or _normalize_tx_envelope is None:
        return False
    try:
        env = _normalize_tx_envelope(raw)
    except Exception:
        return False
    body = env.get("tx") if isinstance(env, dict) else None
    if isinstance(body, dict):
        if _consensus_frozen(body.get("from")):
            return True
        if _consensus_frozen(_normalized_recipient(body)):
            return True
    return False


class PtlSelector:
    """Transaction selector for block building."""

    def __init__(
        self,
        service: PtlService,
        *,
        max_block_size: int = 1_000_000,
        max_block_gas: int = 10_000_000,
    ) -> None:
        self.service = service
        self.max_block_size = max_block_size
        self.max_block_gas = max_block_gas

    async def select_for_block(
        self,
        *,
        max_txs: Optional[int] = None,
        excluded_txids: Optional[set[bytes]] = None,
    ) -> list[PtlEntry]:
        """Select transactions for a block.

        Prioritizes by:
        1. Fee (highest first)
        2. Size (smallest first)
        3. Age (oldest first)
        """
        candidates = await self.service.get_for_mining(limit=5000)

        # Filter out excluded transactions
        if excluded_txids:
            candidates = [tx for tx in candidates if tx.txid not in excluded_txids]

        # Address freeze (FORK_ADDRESS_FREEZE): never select a tx moving value
        # FROM or TO a consensus-frozen account, so an upgraded miner never builds
        # a block its own block-validation rule would orphan. Checks both ends by
        # decoding tx_bytes (PtlEntry has no recipient field).
        candidates = [tx for tx in candidates if not _entry_spends_frozen(tx)]

        # Sort by priority: fee (desc), size (asc), age (asc)
        candidates.sort(
            key=lambda tx: (-tx.fee, tx.size, tx.received_at)
        )

        selected: list[PtlEntry] = []
        total_size = 0
        total_gas = 0

        for tx in candidates:
            if max_txs and len(selected) >= max_txs:
                break

            # Check size limit
            if total_size + tx.size > self.max_block_size:
                continue

            # Estimate gas (simplified: 21000 base + size * 100)
            estimated_gas = 21000 + tx.size * 100
            if total_gas + estimated_gas > self.max_block_gas:
                continue

            selected.append(tx)
            total_size += tx.size
            total_gas += estimated_gas

        log.info(
            "PTL selected transactions for block",
            extra={
                "count": len(selected),
                "total_size": total_size,
                "estimated_gas": total_gas,
            },
        )
        return selected


__all__ = ["PtlSelector"]
