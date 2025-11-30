from __future__ import annotations

"""
Core chain types for the Python SDK.

This module provides two complementary representations for common objects:
- Lightweight `TypedDict` shapes mirroring JSON-RPC payloads.
- Ergonomic `@dataclass` models with bytes-friendly fields and helpers.

The goal is to keep transport-vs-local concerns clean:
- RPC dicts use hex strings for binary fields (e.g., "0xdeadbeef").
- Dataclasses use Python `bytes` where appropriate and provide
  `.to_rpc_dict()` / `from_rpc_dict()` helpers.

Nothing here performs network I/O; these are just types and converters.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, TypedDict, Union

# --- Hex helpers -------------------------------------------------------------

try:
    # Prefer the shared SDK helpers if available.
    from omni_sdk.utils.bytes import bytes_to_hex as _bytes_to_hex
    from omni_sdk.utils.bytes import hex_to_bytes as _hex_to_bytes
except Exception:  # pragma: no cover - fallback is tiny and safe

    def _bytes_to_hex(data: bytes) -> str:
        return "0x" + data.hex()

    def _hex_to_bytes(s: str) -> bytes:
        s = s[2:] if s.startswith("0x") or s.startswith("0X") else s
        if len(s) % 2:
            s = "0" + s
        return bytes.fromhex(s)


# --- Common aliases ----------------------------------------------------------

Address = str  # bech32m (anim1...) or hex-addr depending on network rules
Hash = str  # 0x-prefixed hex string
Hex = str  # 0x-prefixed hex string
ChainId = int


# --- JSON-RPC TypedDict shapes ----------------------------------------------


class LogDict(TypedDict, total=False):
    address: Address
    topics: List[Hash]
    data: Hex
    index: int
    txHash: Hash
    blockHash: Hash
    blockNumber: int


class ReceiptDict(TypedDict, total=False):
    txHash: Hash
    index: int
    blockHash: Hash
    blockNumber: int
    status: int  # 1 = success, 0 = revert/failure
    gasUsed: int
    cumulativeGasUsed: int
    logs: List[LogDict]


class TxDict(TypedDict, total=False):
    # Unsigned/signed common fields for Animica txs (kept minimal & future-proof)
    from_: Address  # "from" is a keyword in Python; RPC uses "from"
    to: Optional[Address]
    nonce: int
    value: int
    data: Hex
    gasLimit: int
    maxFee: int
    chainId: ChainId
    # Signed-only (present when fetched by RPC or sending already-signed)
    hash: Hash
    signature: Hex  # PQ signature blob (scheme-specific, hex-encoded)
    pubkey: Hex  # optional explicit pubkey


class BlockDict(TypedDict, total=False):
    number: int
    hash: Hash
    parentHash: Hash
    timestamp: int
    gasLimit: int
    gasUsed: int
    stateRoot: Hash
    receiptsRoot: Hash
    daRoot: Hash
    transactions: List[Union[Hash, TxDict]]  # node may return hashes or full txs


class HeadDict(TypedDict):
    height: int
    hash: Hash
    parentHash: Hash
    timestamp: int


# --- Dataclasses (bytes-friendly) -------------------------------------------


@dataclass(slots=True, frozen=True)
class Log:
    address: Address
    topics: Sequence[Hash]
    data: bytes
    index: int
    tx_hash: Optional[Hash] = None
    block_hash: Optional[Hash] = None
    block_number: Optional[int] = None

    def to_rpc_dict(self) -> LogDict:
        d: LogDict = {
            "address": self.address,
            "topics": list(self.topics),
            "data": _bytes_to_hex(self.data),
            "index": self.index,
        }
        if self.tx_hash is not None:
            d["txHash"] = self.tx_hash
        if self.block_hash is not None:
            d["blockHash"] = self.block_hash
        if self.block_number is not None:
            d["blockNumber"] = self.block_number
        return d

    @staticmethod
    def from_rpc_dict(d: LogDict) -> "Log":
        return Log(
            address=d["address"],
            topics=d.get("topics", []),
            data=_hex_to_bytes(d.get("data", "0x")),
            index=d.get("index", 0),
            tx_hash=d.get("txHash"),
            block_hash=d.get("blockHash"),
            block_number=d.get("blockNumber"),
        )


@dataclass(slots=True, frozen=True)
class Tx:
    from_addr: Address
    to: Optional[Address]
    nonce: int
    value: int
    data: bytes
    gas_limit: int
    max_fee: int
    chain_id: ChainId
    # Signed-only (optional)
    hash: Optional[Hash] = None
    signature: Optional[bytes] = None
    pubkey: Optional[bytes] = None

    def to_rpc_dict(self, include_signature: bool = True) -> TxDict:
        d: TxDict = {
            "from_": self.from_addr,  # note: adapter maps to "from" on the wire
            "to": self.to,
            "nonce": self.nonce,
            "value": self.value,
            "data": _bytes_to_hex(self.data),
            "gasLimit": self.gas_limit,
            "maxFee": self.max_fee,
            "chainId": self.chain_id,
        }
        if include_signature:
            if self.hash is not None:
                d["hash"] = self.hash
            if self.signature is not None:
                d["signature"] = _bytes_to_hex(self.signature)
            if self.pubkey is not None:
                d["pubkey"] = _bytes_to_hex(self.pubkey)
        return d

    @staticmethod
    def from_rpc_dict(d: TxDict) -> "Tx":
        return Tx(
            from_addr=d.get("from_", d.get("from")),  # tolerate wire shape
            to=d.get("to"),
            nonce=int(d.get("nonce", 0)),
            value=int(d.get("value", 0)),
            data=_hex_to_bytes(d.get("data", "0x")),
            gas_limit=int(d.get("gasLimit", 0)),
            max_fee=int(d.get("maxFee", 0)),
            chain_id=int(d.get("chainId", 0)),
            hash=d.get("hash"),
            signature=_hex_to_bytes(d["signature"]) if "signature" in d else None,
            pubkey=_hex_to_bytes(d["pubkey"]) if "pubkey" in d else None,
        )


@dataclass(slots=True, frozen=True)
class Receipt:
    tx_hash: Hash
    index: int
    block_hash: Hash
    block_number: int
    status: int
    gas_used: int
    cumulative_gas_used: Optional[int] = None
    logs: Sequence[Log] = field(default_factory=tuple)

    def to_rpc_dict(self) -> ReceiptDict:
        d: ReceiptDict = {
            "txHash": self.tx_hash,
            "index": self.index,
            "blockHash": self.block_hash,
            "blockNumber": self.block_number,
            "status": self.status,
            "gasUsed": self.gas_used,
            "logs": [lg.to_rpc_dict() for lg in self.logs],
        }
        if self.cumulative_gas_used is not None:
            d["cumulativeGasUsed"] = self.cumulative_gas_used
        return d

    @staticmethod
    def from_rpc_dict(d: ReceiptDict) -> "Receipt":
        return Receipt(
            tx_hash=d["txHash"],
            index=int(d.get("index", 0)),
            block_hash=d["blockHash"],
            block_number=int(d["blockNumber"]),
            status=int(d.get("status", 0)),
            gas_used=int(d.get("gasUsed", 0)),
            cumulative_gas_used=(
                int(d["cumulativeGasUsed"]) if "cumulativeGasUsed" in d else None
            ),
            logs=tuple(Log.from_rpc_dict(ld) for ld in d.get("logs", [])),
        )


@dataclass(slots=True, frozen=True)
class Block:
    number: int
    hash: Hash
    parent_hash: Hash
    timestamp: int
    gas_limit: int
    gas_used: int
    state_root: Optional[Hash] = None
    receipts_root: Optional[Hash] = None
    da_root: Optional[Hash] = None
    transactions: Sequence[Union[Hash, Tx]] = field(default_factory=tuple)

    def to_rpc_dict(self) -> BlockDict:
        txs: List[Union[Hash, TxDict]] = []
        for t in self.transactions:
            if isinstance(t, str):
                txs.append(t)
            else:
                txs.append(t.to_rpc_dict(include_signature=True))
        d: BlockDict = {
            "number": self.number,
            "hash": self.hash,
            "parentHash": self.parent_hash,
            "timestamp": self.timestamp,
            "gasLimit": self.gas_limit,
            "gasUsed": self.gas_used,
            "transactions": txs,
        }
        if self.state_root is not None:
            d["stateRoot"] = self.state_root
        if self.receipts_root is not None:
            d["receiptsRoot"] = self.receipts_root
        if self.da_root is not None:
            d["daRoot"] = self.da_root
        return d

    @staticmethod
    def from_rpc_dict(d: BlockDict) -> "Block":
        txs_in = d.get("transactions", [])
        txs: List[Union[Hash, Tx]] = []
        for item in txs_in:
            if isinstance(item, str):
                txs.append(item)
            else:
                txs.append(Tx.from_rpc_dict(item))
        return Block(
            number=int(d["number"]),
            hash=d["hash"],
            parent_hash=d["parentHash"],
            timestamp=int(d["timestamp"]),
            gas_limit=int(d.get("gasLimit", 0)),
            gas_used=int(d.get("gasUsed", 0)),
            state_root=d.get("stateRoot"),
            receipts_root=d.get("receiptsRoot"),
            da_root=d.get("daRoot"),
            transactions=tuple(txs),
        )


@dataclass(slots=True, frozen=True)
class Head:
    height: int
    hash: Hash
    parent_hash: Hash
    timestamp: int

    def to_rpc_dict(self) -> HeadDict:
        return {
            "height": self.height,
            "hash": self.hash,
            "parentHash": self.parent_hash,
            "timestamp": self.timestamp,
        }

    @staticmethod
    def from_rpc_dict(d: HeadDict) -> "Head":
        return Head(
            height=int(d["height"]),
            hash=d["hash"],
            parent_hash=d["parentHash"],
            timestamp=int(d["timestamp"]),
        )


# --- module exports ----------------------------------------------------------

__all__ = [
    # aliases
    "Address",
    "Hash",
    "Hex",
    "ChainId",
    # rpc dicts
    "TxDict",
    "ReceiptDict",
    "BlockDict",
    "HeadDict",
    "LogDict",
    # dataclasses
    "Tx",
    "Receipt",
    "Block",
    "Head",
    "Log",
]
