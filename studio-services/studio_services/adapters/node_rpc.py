"""
JSON-RPC client for talking to an Animica node.

This adapter is intentionally small and dependency-light. It provides:
- a retrying async JSON-RPC transport over HTTP(S)
- ergonomic methods for common endpoints used by studio-services:
  * chain.getParams / chain.getChainId / chain.getHead
  * chain.getBlockByNumber / chain.getBlockByHash
  * tx.sendRawTransaction / tx.getTransactionByHash / tx.getTransactionReceipt
  * state.getBalance / state.getNonce
- a helper to poll for a transaction receipt

Notes
-----
* The node's RPC surface is defined in spec/openrpc.json. Method names below
  match the repository layout you provided.
* Raw transactions are CBOR-encoded bytes. We submit them as 0x-prefixed hex.
* All hashes/hex data are treated as lowercase 0x-prefixed strings.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple, Union

import httpx

HexStr = str


# ----------------------------- Errors ---------------------------------------


class NodeRpcError(Exception):
    """Base class for all node RPC errors."""


class RpcTransportError(NodeRpcError):
    """Network/HTTP transport-level error."""


class RpcResponseError(NodeRpcError):
    """JSON-RPC error object returned from the node."""

    def __init__(self, code: int, message: str, data: Any | None = None):
        super().__init__(f"RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


# ----------------------------- Helpers --------------------------------------


def _to_0x(b: bytes) -> HexStr:
    return "0x" + b.hex()


def _as_hex_payload(x: Union[bytes, bytearray, memoryview, str]) -> HexStr:
    """
    Normalize a raw-bytes or hex string into 0x-prefixed lowercase hex.
    """
    if isinstance(x, (bytes, bytearray, memoryview)):
        return _to_0x(bytes(x))
    if isinstance(x, str):
        s = x.strip().lower()
        if s.startswith("0x"):
            # ensure it's valid hex
            int(s[2:] or "0", 16)
            return s
        # treat as raw hex without 0x
        int(s or "0", 16)
        return "0x" + s
    raise TypeError("Unsupported payload type; expected bytes or hex string")


def _should_retry(exc: Exception, status: Optional[int]) -> bool:
    if isinstance(exc, (httpx.TransportError, httpx.TimeoutException)):
        return True
    # Retry on typical transient HTTP statuses
    return status in (502, 503, 504)


def _build_headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    hdrs = {
        "content-type": "application/json",
        "accept": "application/json",
    }
    if extra:
        hdrs.update(extra)
    return hdrs


# ----------------------------- Client ---------------------------------------


@dataclass
class NodeRpcConfig:
    url: str
    timeout_s: float = 10.0
    max_retries: int = 3
    backoff_base_s: float = 0.25  # exponential backoff starting delay
    headers: Optional[Dict[str, str]] = None


class NodeRpc:
    """
    Minimal async JSON-RPC client for the Animica node.
    """

    def __init__(self, config: NodeRpcConfig):
        self._cfg = config
        self._id = 0
        self._client: Optional[httpx.AsyncClient] = None

    # ---------- lifecycle ----------

    async def start(self) -> None:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._cfg.url,
                timeout=self._cfg.timeout_s,
                headers=_build_headers(self._cfg.headers),
            )

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "NodeRpc":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # ---------- core transport ----------

    async def _call(self, method: str, params: Any | None = None) -> Any:
        """
        Perform a single JSON-RPC call with retries.
        """
        if self._client is None:
            await self.start()

        assert self._client is not None  # for type-checkers

        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or []}

        attempt = 0
        while True:
            attempt += 1
            try:
                resp = await self._client.post("/rpc", json=payload)
                status = resp.status_code
                text = await resp.aread()
                # Fast path for 200
                if status == 200:
                    data = json.loads(text)
                    if "error" in data and data["error"] is not None:
                        err = data["error"]
                        raise RpcResponseError(err.get("code", -32000), err.get("message", "Unknown error"), err.get("data"))
                    return data.get("result")
                # Non-200: check retry policy
                if _should_retry(Exception("http_status"), status):
                    raise RpcTransportError(f"HTTP {status}: {text[:256]!r}")
                # Non-retriable
                raise RpcTransportError(f"HTTP {status}: {text[:256]!r}")
            except (httpx.TimeoutException, httpx.TransportError, RpcTransportError) as exc:
                if attempt > self._cfg.max_retries:
                    raise RpcTransportError(f"RPC call failed after {attempt} attempts: {exc}") from exc
                delay = self._cfg.backoff_base_s * (2 ** (attempt - 1))
                await asyncio.sleep(delay)

    # ---------- typed methods ----------

    # Chain info
    async def get_chain_id(self) -> int:
        return await self._call("chain.getChainId")

    async def get_params(self) -> Dict[str, Any]:
        return await self._call("chain.getParams")

    async def get_head(self) -> Dict[str, Any]:
        return await self._call("chain.getHead")

    async def get_block_by_number(
        self,
        number: int,
        include_transactions: bool = False,
        include_receipts: bool = False,
    ) -> Optional[Dict[str, Any]]:
        return await self._call(
            "chain.getBlockByNumber",
            [number, {"includeTransactions": include_transactions, "includeReceipts": include_receipts}],
        )

    async def get_block_by_hash(
        self,
        block_hash: HexStr,
        include_transactions: bool = False,
        include_receipts: bool = False,
    ) -> Optional[Dict[str, Any]]:
        return await self._call(
            "chain.getBlockByHash",
            [block_hash, {"includeTransactions": include_transactions, "includeReceipts": include_receipts}],
        )

    # Transactions
    async def send_raw_transaction(self, raw_tx: Union[bytes, bytearray, memoryview, str]) -> HexStr:
        """
        Submit a signed CBOR-encoded transaction (raw bytes or hex str).
        Returns the transaction hash (0x…).
        """
        tx_hex = _as_hex_payload(raw_tx)
        return await self._call("tx.sendRawTransaction", [tx_hex])

    async def get_transaction_by_hash(self, tx_hash: HexStr) -> Optional[Dict[str, Any]]:
        return await self._call("tx.getTransactionByHash", [tx_hash])

    async def get_transaction_receipt(self, tx_hash: HexStr) -> Optional[Dict[str, Any]]:
        return await self._call("tx.getTransactionReceipt", [tx_hash])

    # State
    async def get_balance(self, address: str) -> int:
        """
        Returns integer balance (base units).
        """
        return int(await self._call("state.getBalance", [address]))

    async def get_nonce(self, address: str) -> int:
        return int(await self._call("state.getNonce", [address]))

    # ---------- convenience ----------

    async def poll_for_receipt(
        self,
        tx_hash: HexStr,
        *,
        timeout_s: float = 60.0,
        poll_interval_s: float = 1.0,
    ) -> Optional[Dict[str, Any]]:
        """
        Poll the node for a transaction receipt until found or timeout.
        Returns None if not found within the timeout.
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            rcpt = await self.get_transaction_receipt(tx_hash)
            if rcpt is not None:
                return rcpt
            await asyncio.sleep(poll_interval_s)
        return None


# ----------------------------- Factory --------------------------------------


def from_env() -> NodeRpc:
    """
    Helper factory: read RPC_URL and optional RPC_AUTH_HEADER from environment.
    """
    url = os.environ.get("RPC_URL")
    if not url:
        raise NodeRpcError("RPC_URL is not set in environment")
    auth_hdr = os.environ.get("RPC_AUTH_HEADER")  # e.g., "Bearer xyz" or custom
    headers = {"authorization": auth_hdr} if auth_hdr else None
    return NodeRpc(NodeRpcConfig(url=url, headers=headers))


# Compatibility alias (legacy code expects NodeRPC)
NodeRPC = NodeRpc


__all__ = [
    "NodeRpc",
    "NodeRPC",
    "NodeRpcConfig",
    "NodeRpcError",
    "RpcTransportError",
    "RpcResponseError",
    "from_env",
]
