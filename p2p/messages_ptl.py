"""PTL P2P protocol messages."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List

from p2p.wire.message_ids import MsgID


def _now_ts() -> int:
    import time
    return int(time.time())


@dataclass(frozen=True)
class PtlAnnounce:
    """Announce available transactions in PTL."""

    msg_id: int = int(MsgID.PTL_ANNOUNCE)
    timestamp: int = field(default_factory=_now_ts)
    txids: List[bytes] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "timestamp": self.timestamp,
            "txids": self.txids,
        }


@dataclass(frozen=True)
class PtlWant:
    """Request specific transactions from PTL."""

    msg_id: int = int(MsgID.PTL_WANT)
    timestamp: int = field(default_factory=_now_ts)
    txids: List[bytes] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "timestamp": self.timestamp,
            "txids": self.txids,
        }


@dataclass(frozen=True)
class PtlPushItem:
    """Single transaction push item."""

    txid: bytes
    tx_bytes: bytes

    def to_payload(self) -> dict[str, Any]:
        return {"txid": self.txid, "tx_bytes": self.tx_bytes}


@dataclass(frozen=True)
class PtlPush:
    """Push transactions to peer."""

    msg_id: int = int(MsgID.PTL_PUSH)
    timestamp: int = field(default_factory=_now_ts)
    items: List[PtlPushItem] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "timestamp": self.timestamp,
            "items": [item.to_payload() for item in self.items],
        }


@dataclass(frozen=True)
class PtlAck:
    """Acknowledge receipt of transactions."""

    msg_id: int = int(MsgID.PTL_ACK)
    timestamp: int = field(default_factory=_now_ts)
    txids: List[bytes] = field(default_factory=list)
    status: str = "ack"  # "ack", "reject", "timeout"
    reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "timestamp": self.timestamp,
            "txids": self.txids,
            "status": self.status,
            "reason": self.reason,
        }


def parse_ptl_txids(value: Any) -> list[bytes]:
    """Parse transaction IDs from message."""
    if not isinstance(value, list):
        raise ValueError("txids must be list")
    items: list[bytes] = []
    for h in value:
        if not isinstance(h, (bytes, bytearray)):
            raise ValueError("txid must be bytes")
        b = bytes(h)
        if len(b) != 32:
            raise ValueError(f"txid must be 32 bytes, got {len(b)}")
        items.append(b)
    return items


def parse_ptl_push_items(value: Any) -> list[PtlPushItem]:
    """Parse push items from message."""
    if not isinstance(value, list):
        raise ValueError("items must be list")
    items: list[PtlPushItem] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("item must be dict")
        txid = entry.get("txid")
        tx_bytes = entry.get("tx_bytes")
        if not isinstance(txid, (bytes, bytearray)):
            raise ValueError("txid must be bytes")
        if not isinstance(tx_bytes, (bytes, bytearray)):
            raise ValueError("tx_bytes must be bytes")
        items.append(PtlPushItem(txid=bytes(txid), tx_bytes=bytes(tx_bytes)))
    return items


__all__ = [
    "PtlAnnounce",
    "PtlWant",
    "PtlPush",
    "PtlPushItem",
    "PtlAck",
    "parse_ptl_txids",
    "parse_ptl_push_items",
]
