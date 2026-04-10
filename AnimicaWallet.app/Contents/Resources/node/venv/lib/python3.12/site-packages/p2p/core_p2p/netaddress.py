from __future__ import annotations

import ipaddress
import struct
import time
from dataclasses import dataclass
from typing import Tuple

from .errors import ProtocolError


@dataclass(frozen=True)
class NetAddress:
    services: int
    ip: str
    port: int
    timestamp: int = 0

    def serialize(self, *, include_time: bool) -> bytes:
        payload = b""
        if include_time:
            payload += struct.pack("<I", self.timestamp or int(time.time()))
        payload += struct.pack("<Q", self.services)
        ip_bytes = ipaddress.ip_address(self.ip).packed
        if len(ip_bytes) == 4:
            ip_bytes = b"\x00" * 10 + b"\xff\xff" + ip_bytes
        payload += ip_bytes
        payload += struct.pack(">H", self.port)
        return payload

    @staticmethod
    def parse(buffer: bytes, offset: int, *, include_time: bool) -> Tuple["NetAddress", int]:
        start = offset
        timestamp = 0
        if include_time:
            if offset + 4 > len(buffer):
                raise ProtocolError("netaddress truncated")
            timestamp = struct.unpack_from("<I", buffer, offset)[0]
            offset += 4
        if offset + 26 > len(buffer):
            raise ProtocolError("netaddress truncated")
        services = struct.unpack_from("<Q", buffer, offset)[0]
        offset += 8
        ip_raw = buffer[offset : offset + 16]
        offset += 16
        port = struct.unpack_from(">H", buffer, offset)[0]
        offset += 2
        if ip_raw.startswith(b"\x00" * 10 + b"\xff\xff"):
            ip_raw = ip_raw[-4:]
        ip = str(ipaddress.ip_address(ip_raw))
        return NetAddress(services=services, ip=ip, port=port, timestamp=timestamp), offset

    def key(self) -> str:
        return f"{self.ip}:{self.port}"
