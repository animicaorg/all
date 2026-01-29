"""Adapter for miner to select transactions from PTL or mempool."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.ptl.config import PtlConfig
from core.ptl.selection import PtlSelector
from core.ptl.service import PtlService

log = logging.getLogger("animica.ptl.miner_adapter")


class MinerTxAdapter:
    """Adapter to provide transactions for block building."""

    def __init__(
        self,
        ptl_service: Optional[PtlService] = None,
        ptl_selector: Optional[PtlSelector] = None,
        config: Optional[PtlConfig] = None,
    ):
        self.ptl_service = ptl_service
        self.ptl_selector = ptl_selector
        self.config = config or PtlConfig.from_env()

    async def select_for_block(
        self,
        *,
        max_txs: Optional[int] = None,
        excluded_txids: Optional[set[bytes]] = None,
    ) -> List[Dict[str, Any]]:
        """Select transactions for a block.

        Returns:
            List of transaction dictionaries with 'txid' and 'tx_bytes'
        """
        if self.config.use_ptl() and self.ptl_selector:
            return await self._select_from_ptl(
                max_txs=max_txs, excluded_txids=excluded_txids
            )
        else:
            # Fallback to mempool (existing behavior)
            return []

    async def _select_from_ptl(
        self, max_txs: Optional[int], excluded_txids: Optional[set[bytes]]
    ) -> List[Dict[str, Any]]:
        """Select transactions from PTL."""
        if not self.ptl_selector:
            log.warning("PTL selector not available")
            return []

        entries = await self.ptl_selector.select_for_block(
            max_txs=max_txs, excluded_txids=excluded_txids
        )

        return [
            {"txid": entry.txid, "tx_bytes": entry.tx_bytes} for entry in entries
        ]

    async def mark_included(self, txids: List[bytes], height: int) -> None:
        """Mark transactions as included in a block."""
        if self.config.use_ptl() and self.ptl_service:
            for txid in txids:
                await self.ptl_service.mark_included(txid, height)
                log.debug(
                    "Marked transaction as included",
                    extra={"txid": txid.hex()[:16], "height": height},
                )


__all__ = ["MinerTxAdapter"]
