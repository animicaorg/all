from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, List

from .wire.message_ids import MsgID


def _now_ts() -> int:
    import time

    return int(time.time())


def _ensure_hashes(txids: Iterable[bytes]) -> list[bytes]:
    items: list[bytes] = []
    for h in txids:
        if not isinstance(h, (bytes, bytearray)):
            raise ValueError("txid must be bytes")
        b = bytes(h)
        if len(b) != 32:
            raise ValueError(f"txid must be 32 bytes, got {len(b)}")
        items.append(b)
    return items


@dataclass(frozen=True)
class TxInv:
    msg_id: int = int(MsgID.TX_INV)
    timestamp: int = field(default_factory=_now_ts)
    txids: List[bytes] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {"msg_id": self.msg_id, "timestamp": int(self.timestamp), "txids": self.txids}


@dataclass(frozen=True)
class TxGet:
    msg_id: int = int(MsgID.TX_GET)
    timestamp: int = field(default_factory=_now_ts)
    txids: List[bytes] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {"msg_id": self.msg_id, "timestamp": int(self.timestamp), "txids": self.txids}


@dataclass(frozen=True)
class TxDataItem:
    txid: bytes
    tx_bytes: bytes

    def to_payload(self) -> dict[str, Any]:
        return {"txid": self.txid, "tx_bytes": self.tx_bytes}


@dataclass(frozen=True)
class TxData:
    msg_id: int = int(MsgID.TX_DATA)
    timestamp: int = field(default_factory=_now_ts)
    items: List[TxDataItem] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        for item in self.items:
            if isinstance(item, TxDataItem):
                items.append(item.to_payload())
            elif isinstance(item, dict):
                items.append({"txid": item.get("txid"), "tx_bytes": item.get("tx_bytes")})
            else:
                raise ValueError("invalid tx data item")
        return {
            "msg_id": self.msg_id,
            "timestamp": int(self.timestamp),
            "items": items,
        }


@dataclass(frozen=True)
class TxNotFound:
    msg_id: int = int(MsgID.TX_NOTFOUND_V2)
    timestamp: int = field(default_factory=_now_ts)
    txids: List[bytes] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {"msg_id": self.msg_id, "timestamp": int(self.timestamp), "txids": self.txids}


@dataclass(frozen=True)
class TxMempoolReq:
    msg_id: int = int(MsgID.TX_MEMPOOL_REQ)
    timestamp: int = field(default_factory=_now_ts)
    limit: int = 2000

    def to_payload(self) -> dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "timestamp": int(self.timestamp),
            "limit": int(self.limit),
        }


@dataclass(frozen=True)
class TxMempoolResp:
    msg_id: int = int(MsgID.TX_MEMPOOL_RESP)
    timestamp: int = field(default_factory=_now_ts)
    txids: List[bytes] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {"msg_id": self.msg_id, "timestamp": int(self.timestamp), "txids": self.txids}


def parse_txids(value: Any) -> list[bytes]:
    if not isinstance(value, list):
        raise ValueError("txids must be list")
    return _ensure_hashes(value)


def parse_tx_data_items(value: Any) -> list[TxDataItem]:
    if not isinstance(value, list):
        raise ValueError("items must be list")
    items: list[TxDataItem] = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ValueError("item must be dict")
        txid = entry.get("txid")
        tx_bytes = entry.get("tx_bytes")
        if not isinstance(tx_bytes, (bytes, bytearray)):
            raise ValueError("tx_bytes must be bytes")
        txid_list = _ensure_hashes([txid])
        items.append(TxDataItem(txid=txid_list[0], tx_bytes=bytes(tx_bytes)))
    return items


__all__ = [
    "TxInv",
    "TxGet",
    "TxData",
    "TxDataItem",
    "TxNotFound",
    "TxMempoolReq",
    "TxMempoolResp",
    "parse_txids",
    "parse_tx_data_items",
]
