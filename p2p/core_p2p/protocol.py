from __future__ import annotations

import hashlib
import os
import struct
import time
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence, Tuple

from .errors import ProtocolError
from .netaddress import NetAddress

COMMAND_LEN = 12
HEADER_LEN = 24
DEFAULT_MAGIC = bytes.fromhex(os.getenv("ANIMICA_P2P_MAGIC", "414e4d43"))  # 'ANMC'
PROTOCOL_VERSION = int(os.getenv("ANIMICA_P2P_PROTOCOL", "70015"))
USER_AGENT = os.getenv("ANIMICA_P2P_USER_AGENT", "/animicad:0.1/")
SERVICES_NODE_NETWORK = 1
MAX_MESSAGE_LENGTH = int(os.getenv("ANIMICA_P2P_MAX_MESSAGE", str(4 << 20)))


def _checksum(payload: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(payload).digest()).digest()[:4]


def verify_checksum(payload: bytes, expected: bytes) -> None:
    if _checksum(payload) != expected:
        raise ProtocolError("checksum mismatch")


def encode_command(command: str) -> bytes:
    raw = command.encode("ascii")
    if len(raw) > COMMAND_LEN:
        raise ProtocolError(f"command too long: {command}")
    return raw.ljust(COMMAND_LEN, b"\x00")


def decode_command(raw: bytes) -> str:
    if len(raw) != COMMAND_LEN:
        raise ProtocolError("invalid command length")
    return raw.split(b"\x00", 1)[0].decode("ascii")


def encode_message(command: str, payload: bytes, *, magic: bytes = DEFAULT_MAGIC) -> bytes:
    if len(magic) != 4:
        raise ProtocolError("magic must be 4 bytes")
    if len(payload) > MAX_MESSAGE_LENGTH:
        raise ProtocolError("payload too large")
    header = struct.pack("<4s12sI4s", magic, encode_command(command), len(payload), _checksum(payload))
    return header + payload


def decode_header(header: bytes) -> Tuple[str, int, bytes]:
    if len(header) != HEADER_LEN:
        raise ProtocolError("invalid header length")
    magic, raw_cmd, length, checksum = struct.unpack("<4s12sI4s", header)
    if magic != DEFAULT_MAGIC:
        raise ProtocolError("magic mismatch")
    if length > MAX_MESSAGE_LENGTH:
        raise ProtocolError("payload too large")
    return decode_command(raw_cmd), length, checksum


# CompactSize / varint helpers

def encode_compact_size(value: int) -> bytes:
    if value < 0:
        raise ProtocolError("negative compact size")
    if value < 253:
        return struct.pack("<B", value)
    if value <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", value)
    if value <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", value)
    return b"\xff" + struct.pack("<Q", value)


def read_compact_size(buffer: bytes, offset: int = 0) -> Tuple[int, int]:
    if offset >= len(buffer):
        raise ProtocolError("compact size out of range")
    first = buffer[offset]
    if first < 253:
        return first, offset + 1
    if first == 253:
        return struct.unpack_from("<H", buffer, offset + 1)[0], offset + 3
    if first == 254:
        return struct.unpack_from("<I", buffer, offset + 1)[0], offset + 5
    return struct.unpack_from("<Q", buffer, offset + 1)[0], offset + 9


def encode_varint(value: int) -> bytes:
    return encode_compact_size(value)


def read_varint(buffer: bytes, offset: int = 0) -> Tuple[int, int]:
    return read_compact_size(buffer, offset)


@dataclass(frozen=True)
class InventoryVector:
    inv_type: int
    inv_hash: bytes

    def serialize(self) -> bytes:
        if len(self.inv_hash) != 32:
            raise ProtocolError("inventory hash must be 32 bytes")
        return struct.pack("<I", self.inv_type) + self.inv_hash

    @staticmethod
    def parse(buffer: bytes, offset: int = 0) -> Tuple["InventoryVector", int]:
        if offset + 36 > len(buffer):
            raise ProtocolError("inventory vector truncated")
        inv_type = struct.unpack_from("<I", buffer, offset)[0]
        inv_hash = buffer[offset + 4 : offset + 36]
        return InventoryVector(inv_type, inv_hash), offset + 36


@dataclass(frozen=True)
class VersionMessage:
    version: int
    services: int
    timestamp: int
    addr_recv: NetAddress
    addr_from: NetAddress
    nonce: int
    user_agent: str
    start_height: int
    relay: bool

    def serialize(self) -> bytes:
        user_agent_bytes = self.user_agent.encode("utf-8")
        payload = struct.pack("<iQQ", self.version, self.services, self.timestamp)
        payload += self.addr_recv.serialize(include_time=False)
        payload += self.addr_from.serialize(include_time=False)
        payload += struct.pack("<Q", self.nonce)
        payload += encode_compact_size(len(user_agent_bytes)) + user_agent_bytes
        payload += struct.pack("<i", self.start_height)
        payload += struct.pack("<?", self.relay)
        return payload

    @staticmethod
    def parse(buffer: bytes) -> "VersionMessage":
        if len(buffer) < 86:
            raise ProtocolError("version message too short")
        version, services, timestamp = struct.unpack_from("<iQQ", buffer, 0)
        offset = 20
        addr_recv, offset = NetAddress.parse(buffer, offset, include_time=False)
        addr_from, offset = NetAddress.parse(buffer, offset, include_time=False)
        nonce = struct.unpack_from("<Q", buffer, offset)[0]
        offset += 8
        ua_len, offset = read_compact_size(buffer, offset)
        user_agent = buffer[offset : offset + ua_len].decode("utf-8")
        offset += ua_len
        start_height = struct.unpack_from("<i", buffer, offset)[0]
        offset += 4
        relay = bool(buffer[offset]) if offset < len(buffer) else True
        return VersionMessage(
            version=version,
            services=services,
            timestamp=timestamp,
            addr_recv=addr_recv,
            addr_from=addr_from,
            nonce=nonce,
            user_agent=user_agent,
            start_height=start_height,
            relay=relay,
        )


@dataclass(frozen=True)
class AddrMessage:
    addresses: Sequence[NetAddress]

    def serialize(self) -> bytes:
        payload = encode_compact_size(len(self.addresses))
        for addr in self.addresses:
            payload += addr.serialize(include_time=True)
        return payload

    @staticmethod
    def parse(buffer: bytes) -> "AddrMessage":
        count, offset = read_compact_size(buffer, 0)
        addresses = []
        for _ in range(count):
            addr, offset = NetAddress.parse(buffer, offset, include_time=True)
            addresses.append(addr)
        return AddrMessage(addresses=addresses)


@dataclass(frozen=True)
class InvMessage:
    inventory: Sequence[InventoryVector]

    def serialize(self) -> bytes:
        payload = encode_compact_size(len(self.inventory))
        for inv in self.inventory:
            payload += inv.serialize()
        return payload

    @staticmethod
    def parse(buffer: bytes) -> "InvMessage":
        count, offset = read_compact_size(buffer, 0)
        items = []
        for _ in range(count):
            inv, offset = InventoryVector.parse(buffer, offset)
            items.append(inv)
        return InvMessage(inventory=items)


@dataclass(frozen=True)
class HeadersMessage:
    headers: Sequence[bytes]

    def serialize(self) -> bytes:
        payload = encode_compact_size(len(self.headers))
        for header in self.headers:
            payload += header
            payload += b"\x00"  # txn count = 0
        return payload

    @staticmethod
    def parse(buffer: bytes) -> "HeadersMessage":
        count, offset = read_compact_size(buffer, 0)
        headers = []
        for _ in range(count):
            if offset + 80 > len(buffer):
                raise ProtocolError("header truncated")
            headers.append(buffer[offset : offset + 80])
            offset += 80
            _, offset = read_compact_size(buffer, offset)
        return HeadersMessage(headers=headers)


@dataclass(frozen=True)
class GetHeadersMessage:
    locator_hashes: Sequence[bytes]
    stop_hash: bytes

    def serialize(self) -> bytes:
        payload = struct.pack("<i", PROTOCOL_VERSION)
        payload += encode_compact_size(len(self.locator_hashes))
        for h in self.locator_hashes:
            if len(h) != 32:
                raise ProtocolError("locator hash must be 32 bytes")
            payload += h
        if len(self.stop_hash) != 32:
            raise ProtocolError("stop hash must be 32 bytes")
        payload += self.stop_hash
        return payload

    @staticmethod
    def parse(buffer: bytes) -> "GetHeadersMessage":
        if len(buffer) < 4:
            raise ProtocolError("getheaders truncated")
        offset = 4
        count, offset = read_compact_size(buffer, offset)
        locator_hashes = []
        for _ in range(count):
            locator_hashes.append(buffer[offset : offset + 32])
            offset += 32
        stop_hash = buffer[offset : offset + 32]
        return GetHeadersMessage(locator_hashes=locator_hashes, stop_hash=stop_hash)


def build_version_message(
    *,
    addr_recv: NetAddress,
    addr_from: NetAddress,
    start_height: int,
    services: int = SERVICES_NODE_NETWORK,
    user_agent: str = USER_AGENT,
    relay: bool = True,
) -> VersionMessage:
    return VersionMessage(
        version=PROTOCOL_VERSION,
        services=services,
        timestamp=int(time.time()),
        addr_recv=addr_recv,
        addr_from=addr_from,
        nonce=int.from_bytes(os.urandom(8), "little"),
        user_agent=user_agent,
        start_height=start_height,
        relay=relay,
    )
