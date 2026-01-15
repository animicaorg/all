"""
TCP transport layer for P2P2.

Handles TCP connections, framing, and reconnection logic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Awaitable

from ..protocol import Message, encode_message, decode_frame

logger = logging.getLogger(__name__)


@dataclass
class TransportConfig:
    """Configuration for TCP transport."""
    read_buffer_size: int = 65536  # 64 KB
    write_buffer_size: int = 65536
    connect_timeout: float = 10.0
    read_timeout: float = 60.0
    keepalive_interval: float = 30.0
    max_reconnect_delay: float = 60.0


class Connection:
    """
    Represents a single TCP connection to a peer.
    
    Handles framing, backpressure, and graceful shutdown.
    """
    
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        remote_addr: str,
        config: TransportConfig,
        on_message: Callable[[Message], Awaitable[None]],
        on_disconnect: Callable[[], Awaitable[None]],
    ):
        self.reader = reader
        self.writer = writer
        self.remote_addr = remote_addr
        self.config = config
        self.on_message = on_message
        self.on_disconnect = on_disconnect
        
        self._recv_buffer = bytearray()
        self._closed = False
        self._recv_task: Optional[asyncio.Task] = None
        self._last_activity = time.time()
        
        logger.info(f"Connection established to {remote_addr}")
    
    async def start(self):
        """Start receiving messages."""
        self._recv_task = asyncio.create_task(self._recv_loop())
    
    async def send(self, msg: Message) -> bool:
        """
        Send a message to the peer.
        
        Returns True if sent successfully, False if connection closed.
        """
        if self._closed:
            return False
        
        try:
            data = encode_message(msg)
            self.writer.write(data)
            await asyncio.wait_for(
                self.writer.drain(),
                timeout=self.config.read_timeout,
            )
            self._last_activity = time.time()
            return True
        except Exception as e:
            logger.warning(f"Send error to {self.remote_addr}: {e}")
            await self.close()
            return False
    
    async def _recv_loop(self):
        """Receive and decode messages in a loop."""
        try:
            while not self._closed:
                # Read with timeout
                try:
                    data = await asyncio.wait_for(
                        self.reader.read(self.config.read_buffer_size),
                        timeout=self.config.read_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"Read timeout from {self.remote_addr}")
                    break
                
                if not data:
                    # EOF - connection closed by peer
                    logger.info(f"Connection closed by {self.remote_addr}")
                    break
                
                self._recv_buffer.extend(data)
                self._last_activity = time.time()
                
                # Decode all available frames
                while True:
                    try:
                        msg, consumed = decode_frame(bytes(self._recv_buffer))
                        if msg is None:
                            # Need more data
                            break
                        
                        # Remove consumed bytes
                        del self._recv_buffer[:consumed]
                        
                        # Dispatch message
                        await self.on_message(msg)
                    except ValueError as e:
                        logger.error(f"Frame decode error from {self.remote_addr}: {e}")
                        break
                
        except Exception as e:
            logger.error(f"Recv loop error from {self.remote_addr}: {e}")
        finally:
            await self.close()
    
    async def close(self):
        """Close the connection gracefully."""
        if self._closed:
            return
        
        self._closed = True
        
        try:
            self.writer.close()
            await self.writer.wait_closed()
        except Exception:
            pass
        
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
        
        # Notify disconnect
        try:
            await self.on_disconnect()
        except Exception as e:
            logger.error(f"Disconnect handler error: {e}")
        
        logger.info(f"Connection closed to {self.remote_addr}")
    
    @property
    def is_closed(self) -> bool:
        return self._closed


class TCPTransport:
    """
    TCP transport layer for P2P2.
    
    Manages listening, dialing, and connection lifecycle.
    """
    
    def __init__(
        self,
        config: TransportConfig,
        on_connection: Callable[[Connection], Awaitable[None]],
    ):
        self.config = config
        self.on_connection = on_connection
        
        self._server: Optional[asyncio.Server] = None
        self._listen_addr: Optional[str] = None
        self._connections: set[Connection] = set()
        
        logger.info("TCP transport initialized")
    
    async def listen(self, host: str = "0.0.0.0", port: int = 9333):
        """Start listening for incoming connections."""
        self._server = await asyncio.start_server(
            self._handle_accept,
            host,
            port,
        )
        self._listen_addr = f"{host}:{port}"
        
        logger.info(f"TCP transport listening on {self._listen_addr}")
    
    async def dial(self, host: str, port: int) -> Optional[Connection]:
        """Dial out to a peer."""
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=self.config.connect_timeout,
            )
            
            remote_addr = f"{host}:{port}"
            
            # Create connection (callbacks will be set by caller)
            conn = Connection(
                reader=reader,
                writer=writer,
                remote_addr=remote_addr,
                config=self.config,
                on_message=lambda msg: asyncio.sleep(0),  # Placeholder
                on_disconnect=lambda: asyncio.sleep(0),  # Placeholder
            )
            
            self._connections.add(conn)
            return conn
            
        except Exception as e:
            logger.warning(f"Failed to dial {host}:{port}: {e}")
            return None
    
    async def _handle_accept(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        """Handle an incoming connection."""
        addr = writer.get_extra_info("peername")
        remote_addr = f"{addr[0]}:{addr[1]}" if addr else "unknown"
        
        logger.info(f"Accepted connection from {remote_addr}")
        
        # Create connection (callbacks will be set by caller via on_connection)
        conn = Connection(
            reader=reader,
            writer=writer,
            remote_addr=remote_addr,
            config=self.config,
            on_message=lambda msg: asyncio.sleep(0),  # Placeholder
            on_disconnect=lambda: asyncio.sleep(0),  # Placeholder
        )
        
        self._connections.add(conn)
        
        # Notify about new connection
        await self.on_connection(conn)
    
    async def close(self):
        """Close all connections and stop listening."""
        logger.info("Closing TCP transport")
        
        # Close all connections
        for conn in list(self._connections):
            await conn.close()
        
        # Stop server
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        
        logger.info("TCP transport closed")
    
    @property
    def listen_addr(self) -> Optional[str]:
        return self._listen_addr
