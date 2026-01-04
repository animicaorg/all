"""PTL transaction selection for block building."""

from __future__ import annotations

import logging
from typing import List, Optional

from core.ptl.model import PtlEntry, TxStatus
from core.ptl.service import PtlService

log = logging.getLogger("animica.ptl.selection")


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
