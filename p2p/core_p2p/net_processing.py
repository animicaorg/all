from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence

import hashlib

from .addrman import AddressManager
from .errors import ProtocolError
from .netaddress import NetAddress
from .peer import PeerState
from .protocol import (
    AddrMessage,
    GetHeadersMessage,
    HeadersMessage,
    InvMessage,
    InventoryVector,
    VersionMessage,
    build_version_message,
)
from .sync_manager import ChainAdapter, SyncManager

INV_TYPE_TX = 1
INV_TYPE_BLOCK = 2


@dataclass
class NetProcessing:
    chain: ChainAdapter
    addrman: AddressManager
    max_addr_relay: int = 1000
    max_inv: int = int(os.getenv("ANIMICA_P2P_MAX_INV", "50000"))

    def __post_init__(self) -> None:
        self.sync = SyncManager(chain=self.chain)

    async def on_handshake_complete(self, peer: PeerState, send) -> None:
        msg = self.sync.build_getheaders()
        await send("getheaders", msg.serialize())

    def build_version(self, peer: PeerState, local_addr: NetAddress, height: int) -> VersionMessage:
        return build_version_message(addr_recv=peer.address, addr_from=local_addr, start_height=height)

    async def handle_message(
        self,
        peer: PeerState,
        command: str,
        payload: bytes,
        *,
        send,
        disconnect,
        local_addr: NetAddress,
        peers: Iterable[PeerState],
        send_peer,
    ) -> None:
        peer.mark_recv()
        if command == "version":
            msg = VersionMessage.parse(payload)
            peer.version_received = True
            peer.version = msg.version
            peer.services = msg.services
            peer.user_agent = msg.user_agent
            peer.start_height = msg.start_height
            peer.relay = msg.relay
            if not peer.version_sent:
                version = self.build_version(peer, local_addr, self.chain_height())
                await send("version", version.serialize())
                peer.version_sent = True
            await send("verack", b"")
            peer.verack_sent = True
            if peer.verack_received and not peer.handshake_complete:
                peer.handshake_complete = True
                await self.on_handshake_complete(peer, send)
            return

        if command == "verack":
            peer.verack_received = True
            if peer.version_received and not peer.handshake_complete:
                peer.handshake_complete = True
                await self.on_handshake_complete(peer, send)
            return

        if not peer.handshake_complete:
            raise ProtocolError("message before handshake")

        if command == "ping":
            await send("pong", payload)
            return

        if command == "addr":
            msg = AddrMessage.parse(payload)
            self.addrman.add(msg.addresses)
            return

        if command == "getaddr":
            addresses = self.addrman.get_addresses(limit=self.max_addr_relay)
            await send("addr", AddrMessage(addresses).serialize())
            return

        if command == "inv":
            msg = InvMessage.parse(payload)
            if len(msg.inventory) > self.max_inv:
                raise ProtocolError("inv too large")
            request = []
            for inv in msg.inventory:
                if inv.inv_hash in peer.known_inventory:
                    continue
                peer.known_inventory.add(inv.inv_hash)
                request.append(inv)
            if request:
                await send("getdata", InvMessage(request).serialize())
            return

        if command == "getdata":
            msg = InvMessage.parse(payload)
            response: List[InventoryVector] = []
            for inv in msg.inventory:
                if inv.inv_type == INV_TYPE_BLOCK:
                    block = self.chain.get_block(inv.inv_hash)
                    if block is None:
                        response.append(inv)
                        continue
                    await send("block", block)
                elif inv.inv_type == INV_TYPE_TX:
                    tx = self.chain.get_tx(inv.inv_hash)
                    if tx is None:
                        response.append(inv)
                        continue
                    await send("tx", tx)
            if response:
                await send("notfound", InvMessage(response).serialize())
            return

        if command == "getheaders":
            msg = GetHeadersMessage.parse(payload)
            headers = self.chain.headers_since(msg.locator_hashes, msg.stop_hash)
            await send("headers", HeadersMessage(headers).serialize())
            return

        if command == "headers":
            msg = HeadersMessage.parse(payload)
            self.sync.receive_headers(msg.headers)
            return

        if command == "block":
            self.chain.process_block(payload)
            await self.announce_block(peers, self._block_hash(payload), send_peer)
            return

        if command == "tx":
            self.chain.process_tx(payload)
            await self.announce_tx(peers, self._tx_hash(payload), send_peer)
            return

    def chain_height(self) -> int:
        header = self.chain.best_header()
        if not header:
            return 0
        return int.from_bytes(header[:4], "little")

    async def announce_block(self, peers: Iterable[PeerState], block_hash: bytes, send) -> None:
        inv = InventoryVector(inv_type=INV_TYPE_BLOCK, inv_hash=block_hash)
        payload = InvMessage([inv]).serialize()
        for peer in peers:
            if block_hash not in peer.known_inventory:
                peer.known_inventory.add(block_hash)
                await send(peer, "inv", payload)

    async def announce_tx(self, peers: Iterable[PeerState], tx_hash: bytes, send) -> None:
        inv = InventoryVector(inv_type=INV_TYPE_TX, inv_hash=tx_hash)
        payload = InvMessage([inv]).serialize()
        for peer in peers:
            if tx_hash not in peer.known_inventory:
                peer.known_inventory.add(tx_hash)
                await send(peer, "inv", payload)

    def _block_hash(self, payload: bytes) -> bytes:
        getter = getattr(self.chain, "block_hash", None)
        if callable(getter):
            return getter(payload)
        return hashlib.sha256(payload).digest()

    def _tx_hash(self, payload: bytes) -> bytes:
        getter = getattr(self.chain, "tx_hash", None)
        if callable(getter):
            return getter(payload)
        return hashlib.sha256(payload).digest()
