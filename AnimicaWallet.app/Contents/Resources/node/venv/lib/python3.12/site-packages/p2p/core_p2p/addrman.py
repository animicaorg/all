from __future__ import annotations

import hashlib
import random
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .netaddress import NetAddress

NEW_BUCKETS = 64
TRIED_BUCKETS = 64
BUCKET_SIZE = 64


def _group_key(ip: str) -> bytes:
    return ip.encode("utf-8")


def _bucket_index(key: bytes, addr: NetAddress, buckets: int) -> int:
    data = key + _group_key(addr.ip) + addr.key().encode("utf-8")
    digest = hashlib.sha256(data).digest()
    return int.from_bytes(digest[:4], "little") % buckets


@dataclass
class AddrInfo:
    address: NetAddress
    last_success: float = 0.0
    last_try: float = 0.0
    attempts: int = 0
    is_tried: bool = False

    def touch(self) -> None:
        self.last_success = time.time()
        self.attempts = 0


@dataclass
class AddressManager:
    key: bytes = field(default_factory=lambda: hashlib.sha256(b"animica-addrman").digest())
    new: Dict[str, AddrInfo] = field(default_factory=dict)
    tried: Dict[str, AddrInfo] = field(default_factory=dict)

    def add(self, addresses: Iterable[NetAddress]) -> None:
        for addr in addresses:
            key = addr.key()
            if key in self.tried:
                continue
            if key not in self.new:
                self.new[key] = AddrInfo(address=addr)

    def mark_good(self, addr: NetAddress) -> None:
        key = addr.key()
        info = self.new.pop(key, None)
        if info is None:
            info = self.tried.get(key) or AddrInfo(address=addr)
        info.is_tried = True
        info.touch()
        self.tried[key] = info

    def select(self) -> Optional[NetAddress]:
        if self.tried and random.random() < 0.7:
            return self._select_from(self.tried, TRIED_BUCKETS)
        return self._select_from(self.new, NEW_BUCKETS)

    def _select_from(self, bucket: Dict[str, AddrInfo], bucket_count: int) -> Optional[NetAddress]:
        if not bucket:
            return None
        info = random.choice(list(bucket.values()))
        info.attempts += 1
        info.last_try = time.time()
        return info.address

    def get_addresses(self, limit: int = 1000) -> List[NetAddress]:
        entries = list(self.tried.values()) + list(self.new.values())
        random.shuffle(entries)
        return [info.address for info in entries[:limit]]
