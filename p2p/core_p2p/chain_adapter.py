from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from core.types.block import Block
from core.types.header import Header
from core.types.tx import Tx
from core.utils.hash import sha3_256

from p2p.deps import P2PDeps

log = logging.getLogger("animica.p2p.core_p2p")


def _encode_header(header: Header) -> bytes:
    height = int(header.height).to_bytes(4, "little", signed=False)
    timestamp = int(header.timestamp).to_bytes(4, "little", signed=False)
    parent_hash = bytes(header.parentHash)
    header_hash = bytes(header.hash())
    return height + timestamp + parent_hash + header_hash + (b"\x00" * 8)


@dataclass
class CoreChainAdapter:
    deps: P2PDeps
    _seen_remote_headers: set[bytes] = field(default_factory=set, init=False)

    def best_header(self) -> bytes:
        head = self._head_header()
        return _encode_header(head) if head is not None else b""

    def locator(self) -> Sequence[bytes]:
        locator = self.deps.header_locator()
        return locator or [b"\x00" * 32]

    def process_headers(self, headers: Sequence[bytes]) -> None:
        if not headers:
            return
        for header in headers:
            self._seen_remote_headers.add(header)

    def headers_since(
        self, locator: Sequence[bytes], stop_hash: bytes
    ) -> Sequence[bytes]:
        head = self._head_header()
        if head is None:
            return []

        head_height = int(head.height)
        locator_set = {bytes(h) for h in locator}
        start_height = -1
        for height in range(head_height, -1, -1):
            header = self.deps.header_by_number(height)
            if header is None:
                continue
            if bytes(header.hash()) in locator_set:
                start_height = height
                break

        out = []
        for height in range(start_height + 1, head_height + 1):
            header = self.deps.header_by_number(height)
            if header is None:
                continue
            header_hash = bytes(header.hash())
            if stop_hash and stop_hash != b"\x00" * 32 and header_hash == stop_hash:
                break
            out.append(_encode_header(header))
        return out

    def get_block(self, block_hash: bytes) -> Optional[bytes]:
        block = self.deps.block_by_hash(block_hash)
        if block is None:
            return None
        return bytes(block.to_cbor())

    def get_tx(self, tx_hash: bytes) -> Optional[bytes]:
        tx = self.deps.tx_by_hash(tx_hash)
        if tx is None:
            return None
        return bytes(tx.to_cbor())

    def process_block(self, block: bytes) -> None:
        try:
            decoded = Block.from_cbor(block)
        except Exception as exc:
            log.warning("core p2p failed to decode block payload", exc_info=exc)
            return
        accepted, reason = self.deps.import_block(decoded)
        if not accepted:
            log.warning("core p2p block rejected", extra={"reason": reason})

    def process_tx(self, tx: bytes) -> None:
        try:
            decoded = Tx.from_cbor(tx)
        except Exception as exc:
            log.warning("core p2p failed to decode tx payload", exc_info=exc)
            return
        accepted, reason = self.deps.admit_tx(decoded)
        if not accepted:
            log.warning("core p2p tx rejected", extra={"reason": reason})

    def best_header_hash(self) -> Optional[bytes]:
        head = self._head_header()
        return bytes(head.hash()) if head is not None else None

    def best_height(self) -> int:
        head = self._head_header()
        return int(head.height) if head is not None else 0

    def block_hash(self, payload: bytes) -> bytes:
        try:
            block = Block.from_cbor(payload)
        except Exception:
            return sha3_256(payload)
        return bytes(block.header.hash())

    def tx_hash(self, payload: bytes) -> bytes:
        try:
            tx = Tx.from_cbor(payload)
        except Exception:
            return sha3_256(payload)
        return bytes(tx.hash())

    def _head_header(self) -> Optional[Header]:
        try:
            _height, header = self.deps.head()
            return header
        except Exception as exc:
            log.warning("core p2p head lookup failed", exc_info=exc)
            return None
