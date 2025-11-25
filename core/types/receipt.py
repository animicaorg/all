from __future__ import annotations

"""
Animica core/types/receipt.py
=============================

Minimal Receipt model used across RPC, execution, and explorer layers.

Fields (canonical, matches spec/tx_format.cddl "Receipt"):
- status    : SUCCESS (0) | REVERT (1) | OOG (2)
- gasUsed   : int (total gas consumed by the tx)
- logs      : list of Log entries (address, topics, data)

Notes
-----
- This file intentionally stays small and dependency-light. Anything heavier
  (e.g., bloom filters, Merkle roots, or extended status info) lives in
  execution/receipts/* and is optional. We only define a canonical shape and
  CBOR round-trips here.
- Address length matches core/types/tx.ADDRESS_LEN (32 bytes), topics are 32-byte
  blobs by convention, but we do not assume EVM semantics otherwise.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Mapping, Sequence, Tuple

from core.encoding.cbor import cbor_dumps, cbor_loads
from core.utils.bytes import expect_len
from core.types.tx import ADDRESS_LEN  # single-direction import (tx doesn't import receipts)

TOPIC_LEN = 32  # 32-byte topic elements (keccak-like), configurable at higher layers


class ReceiptStatus(IntEnum):
    SUCCESS = 0
    REVERT = 1
    OOG = 2  # Out-Of-Gas


@dataclass(frozen=True)
class Log:
    """
    A deterministic, chain-agnostic log/event record.

    - address: emitter (contract/account) raw 32-byte address
    - topics : tuple of 32-byte opaque selectors (0..N)
    - data   : unstructured bytes payload (ABI-encoded by higher layers)
    """
    address: bytes
    topics: Tuple[bytes, ...] = field(default_factory=tuple)
    data: bytes = b""

    def __post_init__(self) -> None:
        object.__setattr__(self, "address", expect_len(self.address, ADDRESS_LEN, name="Log.address"))
        for i, t in enumerate(self.topics):
            object.__setattr__(self, "topics", tuple(expect_len(x, TOPIC_LEN, name=f"Log.topics[{i}]") for x in self.topics))
        if not isinstance(self.data, (bytes, bytearray)):
            raise TypeError("Log.data must be bytes")

    def to_obj(self) -> Mapping[str, Any]:
        return {
            "address": bytes(self.address),
            "topics": [bytes(t) for t in self.topics],
            "data": bytes(self.data),
        }

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "Log":
        return Log(
            address=bytes(o["address"]),
            topics=tuple(bytes(t) for t in o.get("topics", [])),
            data=bytes(o.get("data", b"")),
        )


@dataclass(frozen=True)
class Receipt:
    """
    Minimal receipt: status + gasUsed + logs.
    """
    status: ReceiptStatus
    gas_used: int
    logs: Tuple[Log, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not isinstance(self.status, ReceiptStatus):
            raise TypeError("Receipt.status must be a ReceiptStatus")
        if self.gas_used < 0:
            raise ValueError("Receipt.gas_used must be ≥ 0")
        # Type check logs
        for lg in self.logs:
            if not isinstance(lg, Log):
                raise TypeError("Receipt.logs must contain Log entries")

    # ---- canonical mapping & CBOR round-trip ----

    def to_obj(self) -> Mapping[str, Any]:
        return {
            "v": 1,
            "status": int(self.status),
            "gasUsed": int(self.gas_used),
            "logs": [lg.to_obj() for lg in self.logs],
        }

    def to_cbor(self) -> bytes:
        return cbor_dumps(self.to_obj())

    @staticmethod
    def from_obj(o: Mapping[str, Any]) -> "Receipt":
        if int(o.get("v", 1)) != 1:
            raise ValueError("Unsupported receipt version")
        status = ReceiptStatus(int(o["status"]))
        gas_used = int(o["gasUsed"])
        logs = tuple(Log.from_obj(x) for x in o.get("logs", []))
        return Receipt(status=status, gas_used=gas_used, logs=logs)

    @staticmethod
    def from_cbor(b: bytes) -> "Receipt":
        return Receipt.from_obj(cbor_loads(b))

    # ---- convenience properties ----

    @property
    def ok(self) -> bool:
        return self.status == ReceiptStatus.SUCCESS

    def __str__(self) -> str:
        return f"Receipt<{self.status.name} gasUsed={self.gas_used} logs={len(self.logs)}>"


# ---- utilities: light validation helpers (pure, testable) ----

def validate_logs_shape(logs: Sequence[Log]) -> None:
    """
    Extra validation (used by builders or tests): ensure logs conform to size limits.
    """
    for i, lg in enumerate(logs):
        if len(lg.address) != ADDRESS_LEN:
            raise ValueError(f"logs[{i}].address must be {ADDRESS_LEN} bytes")
        for j, t in enumerate(lg.topics):
            if len(t) != TOPIC_LEN:
                raise ValueError(f"logs[{i}].topics[{j}] must be {TOPIC_LEN} bytes")


# Self-check
if __name__ == "__main__":  # pragma: no cover
    import secrets
    addr = secrets.token_bytes(ADDRESS_LEN)
    lg = Log(address=addr, topics=(secrets.token_bytes(TOPIC_LEN),), data=b"hello")
    rc = Receipt(status=ReceiptStatus.SUCCESS, gas_used=42_000, logs=(lg,))
    enc = rc.to_cbor()
    dec = Receipt.from_cbor(enc)
    assert dec.to_obj() == rc.to_obj()
    print(dec)
