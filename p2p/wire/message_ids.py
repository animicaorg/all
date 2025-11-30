"""
Animica P2P message id registry.

Design goals
------------
- Small, stable uint16 namespace with clear ranges per sub-protocol.
- Deterministic mapping (enum) + helpers for request/response pairing.
- Backwards-compatible evolution via reserved gaps.

Numbering (0x0000..0xFFFF)
--------------------------
0x0000–0x00FF  Core control / handshake
0x0100–0x01FF  Peer management / identify / lists
0x0200–0x02FF  Inventory (generic) & light requests
0x0300–0x03FF  Header/Block sync
0x0400–0x04FF  Transaction relay & queries
0x0500–0x05FF  Useful-work shares (HashShare/AI/Quantum)
0x0600–0x06FF  Data Availability (NMT/Blob) messages
0x0700–0x07FF  Randomness (commit/reveal/VDF) gossip
0x0800–0x08FF  Execution / receipts (compact announce)
0x0E00–0x0EFF  Experimental (subject to change)
0x0F00–0x0FFF  Vendor / private extensions

Changing this table requires bumping WIRE_SCHEMA_VERSION.
"""

from __future__ import annotations

from enum import IntEnum
from typing import Dict, Optional

# Bump when adding/removing/renaming message ids.
WIRE_SCHEMA_VERSION: int = 1


class MsgID(IntEnum):
    # ---------------------------
    # 0x00xx — Core control
    # ---------------------------
    HELLO = 0x0001  # handshake: versions, chainId, alg-policy root, peer-id
    HELLO_ACK = 0x0002  # optional ack with negotiated params
    PING = 0x0003  # keepalive
    PONG = 0x0004  # keepalive reply
    DISCONNECT = 0x0005  # reason code, optional text
    ERROR = 0x0006  # structured protocol error

    # ---------------------------
    # 0x01xx — Peer management
    # ---------------------------
    IDENTIFY = 0x0100  # request peer's meta (caps, head, addrs)
    IDENTIFY_RESP = 0x0101
    GET_PEERS = 0x0102  # ask for known peers (scored)
    PEERS = 0x0103  # list of peer addresses/ids
    ADDRESS_ANNOUNCE = 0x0104  # this node's reachable addresses

    # ---------------------------
    # 0x02xx — Inventory (generic)
    # ---------------------------
    INV = 0x0200  # generic inventory (typed hashes: tx/block/share/blob)
    GETDATA = 0x0201  # request objects by (type, hash)
    NOTFOUND = 0x0202  # objects not found for request

    # ---------------------------
    # 0x03xx — Headers & Blocks sync
    # ---------------------------
    GET_HEADERS = 0x0300  # header locator → sequence of headers
    HEADERS = 0x0301
    GET_BLOCKS = 0x0302  # ask for blocks by hash/height range
    BLOCKS = 0x0303
    BLOCK_ANNOUNCE = 0x0304  # compact announce (hash, height, hints)

    # ---------------------------
    # 0x04xx — Transactions
    # ---------------------------
    TX = 0x0400  # full tx (CBOR) relay
    GET_TX = 0x0401
    TX_NOTFOUND = 0x0402

    # ---------------------------
    # 0x05xx — Useful-work Shares
    # ---------------------------
    SHARE = 0x0500  # HashShare / AI / Quantum / Storage / VDF envelopes
    GET_SHARE = 0x0501
    SHARE_SUMMARY = 0x0502  # compact metrics summary (for preview/scoring)

    # ---------------------------
    # 0x06xx — Data Availability
    # ---------------------------
    DA_INV = 0x0600  # announce blob commitments / namespaces
    DA_GET = 0x0601  # request proof/shards by commitment
    DA_PROOF = 0x0602  # NMT proof + indices
    DA_CHUNK = 0x0603  # optional chunk transfer for retrieval

    # ---------------------------
    # 0x07xx — Randomness (beacon)
    # ---------------------------
    RAND_COMMIT = 0x0700  # commit payloads
    RAND_REVEAL = 0x0701  # reveals
    RAND_VDF_PROOF = 0x0702  # VDF proofs for the round
    RAND_BEACON = 0x0703  # finalized beacon broadcast

    # ---------------------------
    # 0x08xx — Execution hints (optional)
    # ---------------------------
    RECEIPT_HINT = 0x0800  # compact receipt bloom/logs root announce (optional)

    # ---------------------------
    # 0x0Exx — Experimental
    # ---------------------------
    EXP_EXAMPLE = 0x0E00


# Request → Response mapping (used by router/flow control)
_REQUEST_RESPONSE: Dict[MsgID, MsgID] = {
    MsgID.HELLO: MsgID.HELLO_ACK,
    MsgID.PING: MsgID.PONG,
    MsgID.IDENTIFY: MsgID.IDENTIFY_RESP,
    MsgID.GET_PEERS: MsgID.PEERS,
    MsgID.GETDATA: MsgID.NOTFOUND,  # may also be answered by TX/BLOCKS/SHARE/DA_* depending on type
    MsgID.GET_HEADERS: MsgID.HEADERS,
    MsgID.GET_BLOCKS: MsgID.BLOCKS,
    MsgID.GET_TX: MsgID.TX,
    MsgID.GET_SHARE: MsgID.SHARE,
    MsgID.DA_GET: MsgID.DA_PROOF,
}


def is_request(mid: MsgID) -> bool:
    """Return True if `mid` expects a reply."""
    return mid in _REQUEST_RESPONSE


def response_for(mid: MsgID) -> Optional[MsgID]:
    """Return the canonical response id for a request, if any."""
    return _REQUEST_RESPONSE.get(mid)


def category(mid: MsgID) -> str:
    """Human-friendly category string based on numeric range."""
    v = int(mid)
    if 0x0000 <= v <= 0x00FF:
        return "control"
    if 0x0100 <= v <= 0x01FF:
        return "peer"
    if 0x0200 <= v <= 0x02FF:
        return "inventory"
    if 0x0300 <= v <= 0x03FF:
        return "sync"
    if 0x0400 <= v <= 0x04FF:
        return "tx"
    if 0x0500 <= v <= 0x05FF:
        return "share"
    if 0x0600 <= v <= 0x06FF:
        return "da"
    if 0x0700 <= v <= 0x07FF:
        return "randomness"
    if 0x0800 <= v <= 0x08FF:
        return "execution"
    if 0x0E00 <= v <= 0x0EFF:
        return "experimental"
    if 0x0F00 <= v <= 0x0FFF:
        return "vendor"
    return "unknown"


__all__ = [
    "WIRE_SCHEMA_VERSION",
    "MsgID",
    "is_request",
    "response_for",
    "category",
]
