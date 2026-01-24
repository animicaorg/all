from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

HANDSHAKE_MAGIC = b"ANIMICA/HS1\n"
HANDSHAKE_VERSION = 1
MAX_HANDSHAKE_BYTES = 64 * 1024


class HandshakeError(Exception):
    """Base class for handshake encoding/decoding errors."""


class HandshakeDecodeError(HandshakeError):
    """Raised when handshake bytes cannot be decoded."""


class HandshakeValidationError(HandshakeError):
    """Raised when handshake contents fail validation."""


@dataclass(frozen=True)
class HandshakeV1:
    protocol_version: int
    network: str
    chain_id: int
    genesis_hash: str
    node_id: str
    pubkey: str
    listen_addrs: list[str] = field(default_factory=list)
    timestamp: int = 0
    nonce: int = 0
    capabilities: Dict[str, Any] = field(default_factory=dict)
    head_height: Optional[int] = None
    head_hash: Optional[str] = None
    network_best_height: Optional[int] = None
    consensus_id: Optional[str] = None
    fork_id: Optional[int] = None
    protocol_version_str: Optional[str] = None
    network_magic: Optional[str] = None

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "protocol_version": int(self.protocol_version),
            "network": str(self.network),
            "chain_id": int(self.chain_id),
            "genesis_hash": str(self.genesis_hash),
            "node_id": str(self.node_id),
            "pubkey": str(self.pubkey),
            "listen_addrs": list(self.listen_addrs),
            "timestamp": int(self.timestamp),
            "nonce": int(self.nonce),
            "capabilities": dict(self.capabilities),
        }
        if self.head_height is not None:
            payload["head_height"] = int(self.head_height)
        if self.head_hash is not None:
            payload["head_hash"] = str(self.head_hash)
        if self.network_best_height is not None:
            payload["network_best_height"] = int(self.network_best_height)
        if self.consensus_id is not None:
            payload["consensus_id"] = str(self.consensus_id)
        if self.fork_id is not None:
            payload["fork_id"] = int(self.fork_id)
        if self.protocol_version_str is not None:
            payload["protocol_version_str"] = str(self.protocol_version_str)
        if self.network_magic is not None:
            payload["network_magic"] = str(self.network_magic)
        return payload


def _ensure_hex(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise HandshakeValidationError(f"{field} must be hex string")
    cleaned = value.lower().removeprefix("0x")
    if not cleaned or any(c not in "0123456789abcdef" for c in cleaned):
        raise HandshakeValidationError(f"{field} must be hex string")
    return cleaned


def _ensure_b64(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise HandshakeValidationError(f"{field} must be base64 string")
    try:
        base64.b64decode(value.encode("utf-8"), validate=True)
    except Exception as exc:
        raise HandshakeValidationError(f"{field} must be base64 string") from exc
    return value


def _ensure_list_str(value: Any, *, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise HandshakeValidationError(f"{field} must be list[str]")
    return list(value)


def encode_handshake(message: HandshakeV1) -> bytes:
    payload = {
        "type": "handshake",
        "v": HANDSHAKE_VERSION,
        "payload": message.to_payload(),
    }
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(data) > MAX_HANDSHAKE_BYTES:
        raise HandshakeError("handshake payload too large")
    return HANDSHAKE_MAGIC + data + b"\n"


def encode_error(
    *, code: str, reason: str, details: Optional[Dict[str, Any]] = None
) -> bytes:
    payload = {
        "type": "error",
        "v": HANDSHAKE_VERSION,
        "code": code,
        "reason": reason,
        "details": details or {},
        "timestamp": int(time.time()),
    }
    data = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(data) > MAX_HANDSHAKE_BYTES:
        data = json.dumps(
            {
                "type": "error",
                "v": HANDSHAKE_VERSION,
                "code": code,
                "reason": reason,
                "timestamp": int(time.time()),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    return HANDSHAKE_MAGIC + data + b"\n"


def decode_handshake(data: bytes) -> HandshakeV1:
    if not isinstance(data, (bytes, bytearray)):
        raise HandshakeDecodeError("handshake payload must be bytes")
    if len(data) > MAX_HANDSHAKE_BYTES + len(HANDSHAKE_MAGIC) + 1:
        raise HandshakeDecodeError("handshake payload too large")
    if not data.startswith(HANDSHAKE_MAGIC):
        raise HandshakeDecodeError("handshake magic mismatch")
    payload_bytes = data[len(HANDSHAKE_MAGIC) :].strip()
    try:
        obj = json.loads(payload_bytes.decode("utf-8"))
    except Exception as exc:
        raise HandshakeDecodeError("invalid handshake JSON") from exc
    if not isinstance(obj, dict):
        raise HandshakeDecodeError("handshake payload must be JSON object")
    if obj.get("type") != "handshake":
        raise HandshakeDecodeError("handshake payload type mismatch")
    if int(obj.get("v", 0)) != HANDSHAKE_VERSION:
        raise HandshakeValidationError("handshake version incompatible")
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        raise HandshakeDecodeError("handshake payload missing")

    protocol_version = int(payload.get("protocol_version", 0))
    network = payload.get("network")
    chain_id = int(payload.get("chain_id", -1))
    genesis_hash = _ensure_hex(payload.get("genesis_hash", ""), field="genesis_hash")
    node_id = _ensure_hex(payload.get("node_id", ""), field="node_id")
    pubkey = _ensure_b64(payload.get("pubkey", ""), field="pubkey")
    listen_addrs = _ensure_list_str(payload.get("listen_addrs"), field="listen_addrs")
    timestamp = int(payload.get("timestamp", 0))
    nonce = int(payload.get("nonce", 0))
    capabilities = payload.get("capabilities") or {}
    if not isinstance(capabilities, dict):
        raise HandshakeValidationError("capabilities must be dict")

    if not isinstance(network, str) or not network:
        raise HandshakeValidationError("network must be string")
    if chain_id < 0:
        raise HandshakeValidationError("chain_id must be non-negative int")
    if protocol_version != HANDSHAKE_VERSION:
        raise HandshakeValidationError("protocol_version incompatible")

    head_height = payload.get("head_height")
    if head_height is not None:
        head_height = int(head_height)
    head_hash = payload.get("head_hash")
    if head_hash is not None:
        head_hash = _ensure_hex(str(head_hash), field="head_hash")
    network_best_height = payload.get("network_best_height")
    if network_best_height is not None:
        network_best_height = int(network_best_height)
    consensus_id = payload.get("consensus_id")
    if consensus_id is not None:
        consensus_id = str(consensus_id)
    fork_id = payload.get("fork_id")
    if fork_id is not None:
        fork_id = int(fork_id)
    protocol_version_str = payload.get("protocol_version_str")
    if protocol_version_str is not None:
        protocol_version_str = str(protocol_version_str)
    network_magic = payload.get("network_magic")
    if network_magic is not None:
        network_magic = _ensure_hex(str(network_magic), field="network_magic")

    return HandshakeV1(
        protocol_version=protocol_version,
        network=network,
        chain_id=chain_id,
        genesis_hash=genesis_hash,
        node_id=node_id,
        pubkey=pubkey,
        listen_addrs=listen_addrs,
        timestamp=timestamp,
        nonce=nonce,
        capabilities=capabilities,
        head_height=head_height,
        head_hash=head_hash,
        network_best_height=network_best_height,
        consensus_id=consensus_id,
        fork_id=fork_id,
        protocol_version_str=protocol_version_str,
        network_magic=network_magic,
    )
