from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional

from .addrman import AddressManager
from .errors import ProtocolError
from .net_processing import NetProcessing
from .netaddress import NetAddress
from .peer import PeerState
from .protocol import HEADER_LEN, decode_header, encode_message, verify_checksum

log = logging.getLogger("animica.p2p.core_p2p")


@dataclass
class PeerConnection:
    peer: PeerState
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    task: asyncio.Task


@dataclass
class ConnectionManager:
    bind_addr: str
    port: int
    addrman: AddressManager
    net_processing: NetProcessing
    max_inbound: int = 16
    max_outbound: int = 8
    loop: asyncio.AbstractEventLoop = field(default_factory=asyncio.get_event_loop)

    _server: Optional[asyncio.AbstractServer] = field(default=None, init=False)
    _peers: Dict[str, PeerConnection] = field(default_factory=dict, init=False)

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._accept, self.bind_addr, self.port)
        if self._server.sockets:
            sockname = self._server.sockets[0].getsockname()
            if isinstance(sockname, tuple):
                self.port = int(sockname[1])
        log.info("core connman listening", extra={"addr": self.bind_addr, "port": self.port})

    async def stop(self) -> None:
        for peer_id, conn in list(self._peers.items()):
            await self._drop(peer_id, reason="shutdown")
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def dial(self, address: NetAddress) -> None:
        if self.outbound_count() >= self.max_outbound:
            return
        reader, writer = await asyncio.open_connection(address.ip, address.port)
        peer_id = str(uuid.uuid4())
        peer = PeerState(peer_id=peer_id, address=address, inbound=False)
        task = self.loop.create_task(self._peer_loop(peer, reader, writer))
        self._peers[peer_id] = PeerConnection(peer=peer, reader=reader, writer=writer, task=task)
        await self._send(peer, "version", self.net_processing.build_version(peer, self.local_address(), self.net_processing.chain_height()).serialize())
        peer.version_sent = True

    def peers(self) -> Dict[str, PeerState]:
        return {pid: conn.peer for pid, conn in self._peers.items()}

    def local_address(self) -> NetAddress:
        return NetAddress(services=1, ip=self.bind_addr, port=self.port)

    def inbound_count(self) -> int:
        return sum(1 for conn in self._peers.values() if conn.peer.inbound)

    def outbound_count(self) -> int:
        return sum(1 for conn in self._peers.values() if not conn.peer.inbound)

    async def broadcast(self, command: str, payload: bytes) -> None:
        for conn in list(self._peers.values()):
            await self._send(conn.peer, command, payload)

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self.inbound_count() >= self.max_inbound:
            writer.close()
            await writer.wait_closed()
            return
        addr = writer.get_extra_info("peername")
        address = NetAddress(services=1, ip=addr[0], port=addr[1])
        peer_id = str(uuid.uuid4())
        peer = PeerState(peer_id=peer_id, address=address, inbound=True)
        task = self.loop.create_task(self._peer_loop(peer, reader, writer))
        self._peers[peer_id] = PeerConnection(peer=peer, reader=reader, writer=writer, task=task)

    async def _peer_loop(self, peer: PeerState, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                header = await reader.readexactly(HEADER_LEN)
                command, length, checksum = decode_header(header)
                payload = await reader.readexactly(length)
                verify_checksum(payload, checksum)
                async def send_current(cmd: str, data: bytes) -> None:
                    await self._send(peer, cmd, data)

                await self.net_processing.handle_message(
                    peer,
                    command,
                    payload,
                    send=send_current,
                    disconnect=self._drop,
                    local_addr=self.local_address(),
                    peers=self.peers().values(),
                    send_peer=self._send,
                )
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        except ProtocolError as exc:
            log.warning("protocol error", extra={"peer": peer.peer_id, "error": str(exc)})
        finally:
            await self._drop(peer.peer_id, reason="disconnect")

    async def _send(self, peer: PeerState, command: str, payload: bytes) -> None:
        conn = self._peers.get(peer.peer_id)
        if not conn:
            return
        conn.writer.write(encode_message(command, payload))
        await conn.writer.drain()
        peer.mark_send()

    async def _drop(self, peer_id: str, reason: str) -> None:
        conn = self._peers.pop(peer_id, None)
        if conn is None:
            return
        try:
            conn.writer.close()
            await conn.writer.wait_closed()
        except Exception:
            pass
        if not conn.task.done():
            conn.task.cancel()
        log.info("peer dropped", extra={"peer": peer_id, "reason": reason})
