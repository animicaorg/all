from __future__ import annotations

"""
Typed P2P wire messages for Animica.

This module defines *payload* structures for every MsgID (see message_ids.py).
Framing (envelope, AEAD, checksums) lives in frames.py; encoding/decoding in
encoding.py. These dataclasses are deliberately small and copy-friendly.

Schema versioning
-----------------
- WIRE_SCHEMA_VERSION (from message_ids.py) bumps when message *sets* change.
- FINGERPRINT (here) is a sha3-256 digest of the field layout; nodes MAY compare
  at HELLO to warn about mismatched minor layouts while still attempting to interop.
"""

import dataclasses as dc
from dataclasses import dataclass
from enum import IntEnum
from hashlib import sha3_256
from typing import Any, Dict, List, Optional, Tuple

from .message_ids import WIRE_SCHEMA_VERSION, MsgID

# ---------------------------
# Common aliases / small types
# ---------------------------

Hash32 = bytes  # 32-byte keccak/sha3 digest
Hash64 = bytes  # 64-byte sha3-512 (e.g., alg-policy root)
PeerID = bytes  # 32-byte (sha3(pubkey||alg_id)) from p2p.crypto.peer_id
Address = str  # multiaddr-like string ("/ip4/…/tcp/…")
NamespaceID = int  # DA namespace (uint32/uint64 per spec)
Height = int
ChainId = int


def _blen(b: bytes) -> int:
    return len(b) if isinstance(b, (bytes, bytearray)) else -1


def _ensure_len(name: str, b: bytes, n: int) -> None:
    if _blen(b) != n:
        raise ValueError(
            f"{name} must be {n} bytes, got {len(b) if isinstance(b, (bytes, bytearray)) else 'not-bytes'}"
        )


# ---------------------------
# 0x00xx — Core control
# ---------------------------


@dataclass(frozen=True)
class Hello:
    msg_id: MsgID = MsgID.HELLO
    version: str = "1"
    agent: str = "animica-node/unknown"
    chain_id: ChainId = 0
    peer_id: PeerID = b""
    head_height: Height = 0
    head_hash: Hash32 = b""
    alg_policy_root: Hash64 = b""
    capabilities: List[str] = dc.field(
        default_factory=list
    )  # e.g., ["tx", "blocks", "da", "randomness"]
    timestamp: int = 0  # unix seconds

    def __post_init__(self):
        _ensure_len("peer_id", self.peer_id, 32)
        _ensure_len("head_hash", self.head_hash, 32)
        if self.alg_policy_root:
            _ensure_len("alg_policy_root", self.alg_policy_root, 64)


@dataclass(frozen=True)
class HelloAck:
    msg_id: MsgID = MsgID.HELLO_ACK
    accepted: bool = True
    reason: Optional[str] = None
    schema_version: int = WIRE_SCHEMA_VERSION
    schema_fingerprint: str = ""  # hex of local FINGERPRINT


@dataclass(frozen=True)
class Ping:
    msg_id: MsgID = MsgID.PING
    nonce: int = 0


@dataclass(frozen=True)
class Pong:
    msg_id: MsgID = MsgID.PONG
    nonce: int = 0


@dataclass(frozen=True)
class Disconnect:
    msg_id: MsgID = MsgID.DISCONNECT
    code: int = 0
    reason: Optional[str] = None


@dataclass(frozen=True)
class Error:
    msg_id: MsgID = MsgID.ERROR
    code: int = 0
    message: str = ""
    details: Optional[str] = None


# ---------------------------
# 0x01xx — Peer management
# ---------------------------


@dataclass(frozen=True)
class Identify:
    msg_id: MsgID = MsgID.IDENTIFY
    want_addrs: bool = True


@dataclass(frozen=True)
class IdentifyResp:
    msg_id: MsgID = MsgID.IDENTIFY_RESP
    peer_id: PeerID = b""
    addresses: List[Address] = dc.field(default_factory=list)
    head_height: Height = 0
    head_hash: Hash32 = b""
    caps: List[str] = dc.field(default_factory=list)

    def __post_init__(self):
        _ensure_len("peer_id", self.peer_id, 32)
        _ensure_len("head_hash", self.head_hash, 32)


@dataclass(frozen=True)
class GetPeers:
    msg_id: MsgID = MsgID.GET_PEERS
    max_peers: int = 32


@dataclass(frozen=True)
class Peers:
    msg_id: MsgID = MsgID.PEERS
    entries: List[Tuple[PeerID, Address]] = dc.field(default_factory=list)

    def __post_init__(self):
        for pid, _addr in self.entries:
            _ensure_len("peer_id", pid, 32)


@dataclass(frozen=True)
class AddressAnnounce:
    msg_id: MsgID = MsgID.ADDRESS_ANNOUNCE
    addresses: List[Address] = dc.field(default_factory=list)


# ---------------------------
# 0x02xx — Inventory
# ---------------------------


class InvType(IntEnum):
    TX = 1
    BLOCK = 2
    SHARE = 3
    DA_COMMIT = 4


@dataclass(frozen=True)
class InvItem:
    typ: InvType
    h: Hash32

    def __post_init__(self):
        _ensure_len("h", self.h, 32)


@dataclass(frozen=True)
class Inv:
    msg_id: MsgID = MsgID.INV
    items: List[InvItem] = dc.field(default_factory=list)


@dataclass(frozen=True)
class GetData:
    msg_id: MsgID = MsgID.GETDATA
    items: List[InvItem] = dc.field(default_factory=list)


@dataclass(frozen=True)
class NotFound:
    msg_id: MsgID = MsgID.NOTFOUND
    items: List[InvItem] = dc.field(default_factory=list)


# ---------------------------
# 0x03xx — Headers & Blocks sync
# ---------------------------


@dataclass(frozen=True)
class GetHeaders:
    msg_id: MsgID = MsgID.GET_HEADERS
    locator: List[Hash32] = dc.field(default_factory=list)  # oldest→newest
    max_headers: int = 64

    def __post_init__(self):
        for h in self.locator:
            _ensure_len("locator hash", h, 32)


@dataclass(frozen=True)
class HeaderCompact:
    hash: Hash32
    height: Height
    parent: Hash32
    theta_micro: int  # Θ in micro-nats (or integer micro-target)
    timestamp: int

    def __post_init__(self):
        _ensure_len("hash", self.hash, 32)
        _ensure_len("parent", self.parent, 32)


@dataclass(frozen=True)
class Headers:
    msg_id: MsgID = MsgID.HEADERS
    headers: List[HeaderCompact] = dc.field(default_factory=list)


@dataclass(frozen=True)
class GetBlocks:
    msg_id: MsgID = MsgID.GET_BLOCKS
    by_hash: List[Hash32] = dc.field(default_factory=list)
    max_blocks: int = 16

    def __post_init__(self):
        for h in self.by_hash:
            _ensure_len("block hash", h, 32)


@dataclass(frozen=True)
class Blocks:
    msg_id: MsgID = MsgID.BLOCKS
    # Blocks are CBOR-encoded core.types.Block bytes. Chunking handled by transport.
    blocks: List[bytes] = dc.field(default_factory=list)


@dataclass(frozen=True)
class BlockAnnounce:
    msg_id: MsgID = MsgID.BLOCK_ANNOUNCE
    hash: Hash32 = b""
    height: Height = 0

    def __post_init__(self):
        _ensure_len("hash", self.hash, 32)


# ---------------------------
# 0x04xx — Transactions
# ---------------------------


@dataclass(frozen=True)
class Tx:
    msg_id: MsgID = MsgID.TX
    raw_cbor: bytes = b""  # CBOR-encoded core.types.Tx


@dataclass(frozen=True)
class GetTx:
    msg_id: MsgID = MsgID.GET_TX
    hashes: List[Hash32] = dc.field(default_factory=list)

    def __post_init__(self):
        for h in self.hashes:
            _ensure_len("tx hash", h, 32)


@dataclass(frozen=True)
class TxNotFound:
    msg_id: MsgID = MsgID.TX_NOTFOUND
    hashes: List[Hash32] = dc.field(default_factory=list)

    def __post_init__(self):
        for h in self.hashes:
            _ensure_len("tx hash", h, 32)


# ---------------------------
# 0x05xx — Useful-work Shares
# ---------------------------


@dataclass(frozen=True)
class Share:
    msg_id: MsgID = MsgID.SHARE
    envelope_cbor: bytes = b""  # CBOR proofs/envelope (see proofs/schemas)
    summary_hint: Optional[Dict[str, Any]] = None  # optional tiny metrics preview


@dataclass(frozen=True)
class GetShare:
    msg_id: MsgID = MsgID.GET_SHARE
    hashes: List[Hash32] = dc.field(default_factory=list)

    def __post_init__(self):
        for h in self.hashes:
            _ensure_len("share hash", h, 32)


@dataclass(frozen=True)
class ShareSummary:
    msg_id: MsgID = MsgID.SHARE_SUMMARY
    share_hash: Hash32 = b""
    metrics: Dict[str, float] = dc.field(
        default_factory=dict
    )  # e.g., {"d_ratio": 0.42, "ai_units": 123.0}

    def __post_init__(self):
        _ensure_len("share_hash", self.share_hash, 32)


# ---------------------------
# 0x06xx — Data Availability
# ---------------------------


@dataclass(frozen=True)
class DACommitment:
    commitment: Hash32  # NMT root
    namespace: NamespaceID
    size: int  # bytes

    def __post_init__(self):
        _ensure_len("commitment", self.commitment, 32)


@dataclass(frozen=True)
class DAInv:
    msg_id: MsgID = MsgID.DA_INV
    items: List[DACommitment] = dc.field(default_factory=list)


@dataclass(frozen=True)
class DAGet:
    msg_id: MsgID = MsgID.DA_GET
    commitment: Hash32 = b""
    want_proof: bool = True
    # Optional byte-range for chunk fetch (transport may segment anyway)
    offset: Optional[int] = None
    length: Optional[int] = None

    def __post_init__(self):
        _ensure_len("commitment", self.commitment, 32)


@dataclass(frozen=True)
class DAProof:
    msg_id: MsgID = MsgID.DA_PROOF
    commitment: Hash32 = b""
    proof_cbor: bytes = b""  # DAS/NMT proof object

    def __post_init__(self):
        _ensure_len("commitment", self.commitment, 32)


@dataclass(frozen=True)
class DAChunk:
    msg_id: MsgID = MsgID.DA_CHUNK
    commitment: Hash32 = b""
    offset: int = 0
    data: bytes = b""

    def __post_init__(self):
        _ensure_len("commitment", self.commitment, 32)


# ---------------------------
# 0x07xx — Randomness (beacon)
# ---------------------------


@dataclass(frozen=True)
class RandCommit:
    msg_id: MsgID = MsgID.RAND_COMMIT
    round: int = 0
    commit_hash: Hash32 = b""  # H(domain|addr|salt|payload)

    def __post_init__(self):
        _ensure_len("commit_hash", self.commit_hash, 32)


@dataclass(frozen=True)
class RandReveal:
    msg_id: MsgID = MsgID.RAND_REVEAL
    round: int = 0
    salt: bytes = b""
    payload: bytes = b""


@dataclass(frozen=True)
class RandVdfProof:
    msg_id: MsgID = MsgID.RAND_VDF_PROOF
    round: int = 0
    proof_bytes: bytes = b""  # Wesolowski proof blob


@dataclass(frozen=True)
class RandBeacon:
    msg_id: MsgID = MsgID.RAND_BEACON
    round: int = 0
    output: Hash32 = b""
    light_proof_cbor: bytes = b""

    def __post_init__(self):
        _ensure_len("output", self.output, 32)


# ---------------------------
# 0x08xx — Execution hints (optional)
# ---------------------------


@dataclass(frozen=True)
class ReceiptHint:
    msg_id: MsgID = MsgID.RECEIPT_HINT
    tx_hash: Hash32 = b""
    logs_root: Hash32 = b""

    def __post_init__(self):
        _ensure_len("tx_hash", self.tx_hash, 32)
        _ensure_len("logs_root", self.logs_root, 32)


# ---------------------------
# 0x0Exx — Experimental
# ---------------------------


@dataclass(frozen=True)
class ExpExample:
    msg_id: MsgID = MsgID.EXP_EXAMPLE
    payload: bytes = b""


# ---------------------------
# Schema fingerprint
# ---------------------------


def _schema_descriptor() -> str:
    """Build a stable textual descriptor of all message class fields."""

    def desc(cls) -> str:
        anns = getattr(cls, "__annotations__", {})
        items = sorted((k, str(v)) for k, v in anns.items())
        return f"{cls.__name__}(" + ",".join(f"{k}:{t}" for k, t in items) + ")"

    classes = [
        Hello,
        HelloAck,
        Ping,
        Pong,
        Disconnect,
        Error,
        Identify,
        IdentifyResp,
        GetPeers,
        Peers,
        AddressAnnounce,
        InvItem,
        Inv,
        GetData,
        NotFound,
        GetHeaders,
        HeaderCompact,
        Headers,
        GetBlocks,
        Blocks,
        BlockAnnounce,
        Tx,
        GetTx,
        TxNotFound,
        Share,
        GetShare,
        ShareSummary,
        DACommitment,
        DAInv,
        DAGet,
        DAProof,
        DAChunk,
        RandCommit,
        RandReveal,
        RandVdfProof,
        RandBeacon,
        ReceiptHint,
        ExpExample,
    ]
    return "|".join(desc(c) for c in classes)


FINGERPRINT: str = sha3_256(_schema_descriptor().encode("utf-8")).hexdigest()


__all__ = [
    # versioning
    "WIRE_SCHEMA_VERSION",
    "FINGERPRINT",
    # inventory enum
    "InvType",
    # messages
    "Hello",
    "HelloAck",
    "Ping",
    "Pong",
    "Disconnect",
    "Error",
    "Identify",
    "IdentifyResp",
    "GetPeers",
    "Peers",
    "AddressAnnounce",
    "InvItem",
    "Inv",
    "GetData",
    "NotFound",
    "GetHeaders",
    "HeaderCompact",
    "Headers",
    "GetBlocks",
    "Blocks",
    "BlockAnnounce",
    "Tx",
    "GetTx",
    "TxNotFound",
    "Share",
    "GetShare",
    "ShareSummary",
    "DACommitment",
    "DAInv",
    "DAGet",
    "DAProof",
    "DAChunk",
    "RandCommit",
    "RandReveal",
    "RandVdfProof",
    "RandBeacon",
    "ReceiptHint",
    "ExpExample",
    # aliases
    "Hash32",
    "Hash64",
    "PeerID",
    "Address",
    "NamespaceID",
    "Height",
    "ChainId",
]
