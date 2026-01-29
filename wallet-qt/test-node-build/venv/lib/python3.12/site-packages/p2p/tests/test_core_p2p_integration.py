import asyncio
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

import pytest

from p2p.core_p2p.addrman import AddressManager
from p2p.core_p2p.connman import ConnectionManager
from p2p.core_p2p.net_processing import NetProcessing
from p2p.core_p2p.netaddress import NetAddress


@dataclass
class FakeChain:
    headers: List[bytes] = field(default_factory=list)
    blocks: Dict[bytes, bytes] = field(default_factory=dict)
    txs: Dict[bytes, bytes] = field(default_factory=dict)

    def best_header(self) -> bytes:
        return self.headers[-1] if self.headers else b""

    def locator(self) -> Sequence[bytes]:
        hashes = [hashlib.sha256(h).digest() for h in self.headers]
        return list(reversed(hashes[-10:])) or [b"\x00" * 32]

    def process_headers(self, headers: Sequence[bytes]) -> None:
        for header in headers:
            if header not in self.headers:
                self.headers.append(header)

    def headers_since(self, locator: Sequence[bytes], stop_hash: bytes) -> Sequence[bytes]:
        if not self.headers:
            return []
        hashes = [hashlib.sha256(h).digest() for h in self.headers]
        start_index = 0
        for loc in locator:
            if loc in hashes:
                start_index = hashes.index(loc) + 1
                break
        result = []
        for header in self.headers[start_index:]:
            if hashlib.sha256(header).digest() == stop_hash:
                break
            result.append(header)
        return result

    def get_block(self, block_hash: bytes) -> bytes | None:
        return self.blocks.get(block_hash)

    def get_tx(self, tx_hash: bytes) -> bytes | None:
        return self.txs.get(tx_hash)

    def process_block(self, block: bytes) -> None:
        header = block[:80]
        block_hash = hashlib.sha256(header).digest()
        self.blocks[block_hash] = block
        if header and header not in self.headers:
            self.headers.append(header)

    def process_tx(self, tx: bytes) -> None:
        self.txs[hashlib.sha256(tx).digest()] = tx


async def _wait_until(predicate, timeout=2.0):
    start = asyncio.get_event_loop().time()
    while asyncio.get_event_loop().time() - start < timeout:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("condition not met")


@pytest.mark.asyncio
async def test_three_node_sync_and_relay():
    chain_a = FakeChain()
    chain_b = FakeChain()
    chain_c = FakeChain()

    def make_block(height: int) -> bytes:
        header = height.to_bytes(4, "little") + b"\x00" * 76
        return header + b"block" + height.to_bytes(1, "little")

    for height in range(1, 4):
        chain_a.process_block(make_block(height))

    net_a = NetProcessing(chain=chain_a, addrman=AddressManager())
    net_b = NetProcessing(chain=chain_b, addrman=AddressManager())
    net_c = NetProcessing(chain=chain_c, addrman=AddressManager())

    conn_a = ConnectionManager("127.0.0.1", 0, AddressManager(), net_a)
    conn_b = ConnectionManager("127.0.0.1", 0, AddressManager(), net_b)
    conn_c = ConnectionManager("127.0.0.1", 0, AddressManager(), net_c)

    await conn_a.start()
    await conn_b.start()
    await conn_c.start()

    await conn_b.dial(NetAddress(services=1, ip="127.0.0.1", port=conn_a.port))
    await conn_c.dial(NetAddress(services=1, ip="127.0.0.1", port=conn_a.port))

    await _wait_until(lambda: len(chain_b.headers) == len(chain_a.headers))
    await _wait_until(lambda: len(chain_c.headers) == len(chain_a.headers))

    tx = b"tx-from-b"
    chain_b.process_tx(tx)
    tx_hash = hashlib.sha256(tx).digest()
    await net_b.announce_tx(conn_b.peers().values(), tx_hash, conn_b._send)

    await _wait_until(lambda: tx_hash in chain_c.txs)

    new_block = make_block(4)
    chain_a.process_block(new_block)
    block_hash = hashlib.sha256(new_block[:80]).digest()
    await net_a.announce_block(conn_a.peers().values(), block_hash, conn_a._send)

    await _wait_until(lambda: block_hash in chain_c.blocks)

    await conn_a.stop()
    await conn_b.stop()
    await conn_c.stop()
