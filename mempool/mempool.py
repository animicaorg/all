from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from mempool.tx_hash import normalized_tx_bytes, tx_hash_bytes


@dataclass(frozen=True)
class MempoolEntry:
    txid: bytes
    tx_bytes: bytes
    origin: str
    received_at: float


class Mempool:
    def __init__(self, *, max_tx_bytes: int = 1_048_576) -> None:
        self.max_tx_bytes = int(max_tx_bytes)
        self._txs: Dict[bytes, MempoolEntry] = {}

    def add_tx(self, tx: bytes, origin: str) -> bytes:
        raw = normalized_tx_bytes(tx)
        if len(raw) > self.max_tx_bytes:
            raise ValueError("tx too large")
        txid = tx_hash_bytes(raw)
        if txid not in self._txs:
            self._txs[txid] = MempoolEntry(
                txid=txid, tx_bytes=raw, origin=str(origin), received_at=time.time()
            )
        return txid

    def has(self, txid: bytes) -> bool:
        return txid in self._txs

    def get(self, txid: bytes) -> Optional[MempoolEntry]:
        return self._txs.get(txid)

    def list(self) -> List[MempoolEntry]:
        return list(self._txs.values())


__all__ = ["Mempool", "MempoolEntry"]
