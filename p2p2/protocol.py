"""
Protocol message types and encoding for P2P2.

Uses CBOR for deterministic, canonical encoding with length-prefix framing.
"""

from __future__ import annotations

import cbor2
import hashlib
import struct
import time
from dataclasses import dataclass, asdict, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple


class MsgType(str, Enum):
    """Message types in the P2P2 protocol."""
    HELLO = "hello"
    HELLO_ACK = "hello_ack"
    PING = "ping"
    PONG = "pong"
    ADDR = "addr"
    GETADDR = "getaddr"
    INV = "inv"
    GETDATA = "getdata"
    TX = "tx"
    BLOCK = "block"
    GETHEADERS = "getheaders"
    HEADERS = "headers"
    GETBLOCKS = "getblocks"
    REJECT = "reject"
    DISCONNECT = "disconnect"


class ServiceFlags(int):
    """Service flags bitfield for peer capabilities."""
    NONE = 0
    SYNC = 1 << 0  # Full sync (headers + blocks)
    TX_GOSSIP = 1 << 1  # Transaction gossip
    SNAPSHOT = 1 << 2  # Snapshot serving
    MINING = 1 << 3  # Mining/share propagation


@dataclass
class Message:
    """
    Universal message envelope.
    
    All messages use this structure with type-specific payload.
    """
    type: str
    id: Optional[str] = None  # Request ID for request/response matching
    time: float = field(default_factory=time.time)
    payload: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for CBOR encoding."""
        return {
            "type": self.type,
            "id": self.id,
            "time": self.time,
            "payload": self.payload,
        }
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Message:
        """Construct from dictionary (after CBOR decode)."""
        return cls(
            type=d["type"],
            id=d.get("id"),
            time=d.get("time", time.time()),
            payload=d.get("payload", {}),
        )


# Hello message payload
@dataclass
class HelloPayload:
    """Handshake hello message."""
    node_id: str  # Stable pubkey-derived ID
    network_id: str  # Network name (mainnet, testnet, etc)
    chain_id: int
    genesis_hash: str  # Hex
    protocol_version: int
    services: int  # ServiceFlags bitfield
    listen_addrs: List[str]
    best_height: int
    best_hash: str  # Hex
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> HelloPayload:
        return cls(**d)


# Inv/GetData item
@dataclass
class InvItem:
    """Inventory item (hash reference)."""
    type: str  # "tx" or "block"
    hash: str  # Hex hash
    
    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "hash": self.hash}
    
    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> InvItem:
        return cls(type=d["type"], hash=d["hash"])


# Message framing: [u32 length][bytes payload]
MAX_MESSAGE_SIZE = 100 * 1024 * 1024  # 100 MB


def encode_message(msg: Message) -> bytes:
    """
    Encode message to framed CBOR bytes.
    
    Frame format: [u32 length (big-endian)][CBOR payload]
    """
    # Encode payload to CBOR
    payload_bytes = cbor2.dumps(msg.to_dict())
    
    # Check size limit
    if len(payload_bytes) > MAX_MESSAGE_SIZE:
        raise ValueError(f"Message too large: {len(payload_bytes)} > {MAX_MESSAGE_SIZE}")
    
    # Prefix with length
    length_prefix = struct.pack(">I", len(payload_bytes))
    return length_prefix + payload_bytes


def decode_frame(data: bytes) -> Tuple[Optional[Message], int]:
    """
    Decode a framed message from bytes.
    
    Returns:
        (message, bytes_consumed) if complete frame available
        (None, 0) if need more data
        
    Raises:
        ValueError: if frame is malformed
    """
    # Need at least 4 bytes for length prefix
    if len(data) < 4:
        return None, 0
    
    # Read length prefix
    length = struct.unpack(">I", data[:4])[0]
    
    # Validate length
    if length > MAX_MESSAGE_SIZE:
        raise ValueError(f"Message too large: {length} > {MAX_MESSAGE_SIZE}")
    
    # Check if we have full message
    if len(data) < 4 + length:
        return None, 0
    
    # Extract and decode payload
    payload_bytes = data[4:4 + length]
    msg_dict = cbor2.loads(payload_bytes)
    msg = Message.from_dict(msg_dict)
    
    return msg, 4 + length


def create_hello(
    node_id: str,
    network_id: str,
    chain_id: int,
    genesis_hash: str,
    protocol_version: int,
    services: int,
    listen_addrs: List[str],
    best_height: int,
    best_hash: str,
) -> Message:
    """Create a HELLO message."""
    payload = HelloPayload(
        node_id=node_id,
        network_id=network_id,
        chain_id=chain_id,
        genesis_hash=genesis_hash,
        protocol_version=protocol_version,
        services=services,
        listen_addrs=listen_addrs,
        best_height=best_height,
        best_hash=best_hash,
    )
    return Message(type=MsgType.HELLO, payload=payload.to_dict())


def create_ping(nonce: Optional[str] = None) -> Message:
    """Create a PING message."""
    if nonce is None:
        nonce = hashlib.sha256(str(time.time()).encode()).hexdigest()[:16]
    return Message(type=MsgType.PING, payload={"nonce": nonce})


def create_pong(nonce: str) -> Message:
    """Create a PONG message (response to PING)."""
    return Message(type=MsgType.PONG, payload={"nonce": nonce})


def create_inv(items: List[InvItem]) -> Message:
    """Create an INV message (advertise hashes)."""
    return Message(
        type=MsgType.INV,
        payload={"items": [item.to_dict() for item in items]},
    )


def create_getdata(items: List[InvItem]) -> Message:
    """Create a GETDATA message (request items by hash)."""
    return Message(
        type=MsgType.GETDATA,
        payload={"items": [item.to_dict() for item in items]},
    )


def create_getheaders(locator: List[str], stop: Optional[str] = None, limit: int = 2000) -> Message:
    """Create a GETHEADERS message (request headers)."""
    return Message(
        type=MsgType.GETHEADERS,
        payload={
            "locator": locator,
            "stop": stop,
            "limit": limit,
        },
    )


def create_disconnect(reason: str) -> Message:
    """Create a DISCONNECT message."""
    return Message(type=MsgType.DISCONNECT, payload={"reason": reason})
