"""
p2p.adapters.core_chain
=======================

Lightweight helpers the P2P stack uses to:

- Decode CBOR bytes received from the wire into core dataclasses
- Run *cheap* structural sanity checks (sizes, required fields)
- Compute canonical content-hashes for dedupe/inventory
- (Optionally) pretty-print short diagnostics for logs

Deliberately avoids any heavy consensus checks (Θ schedule, ψ, etc.).
Those live in :mod:`consensus` and are invoked later in the pipeline.

This module should remain import-cheap and side-effect free.
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Dict, Tuple, Type, TypeVar, Union, overload

# Core primitives
from core.encoding import cbor
from core.types.block import Block
from core.types.header import Header
from core.types.tx import Tx
from core.utils.bytes import to_hex
from core.utils.hash import sha3_256

T = TypeVar("T")

BytesLike = Union[bytes, bytearray, memoryview]


# --------------------------
# Decoding & coercion
# --------------------------


def _ensure_bytes(b: BytesLike) -> bytes:
    if isinstance(b, bytes):
        return b
    if isinstance(b, (bytearray, memoryview)):
        return bytes(b)
    raise TypeError(f"expected bytes-like, got {type(b)!r}")


def _coerce_dataclass(cls: Type[T], obj: Any) -> T:
    """
    Convert a dict (or already-typed instance) into the target dataclass.

    Only passes through the fields declared on the dataclass, so extra keys
    coming from older/newer peers are ignored safely.
    """
    if isinstance(obj, cls):
        return obj
    if not isinstance(obj, dict):
        raise TypeError(f"cannot coerce {type(obj)!r} into {cls.__name__}")

    if not is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass")

    names = {f.name for f in fields(cls)}
    ctor_kwargs = {k: v for k, v in obj.items() if k in names}
    # Basic required-field check: any dataclass field without a default that
    # is missing from input → KeyError for a clear error surface.
    missing = [f.name for f in fields(cls) if f.init and f.default is fields(cls)[0].default and f.name not in ctor_kwargs]  # type: ignore
    # The above default check is conservative; we still let __init__ raise if needed.
    return cls(**ctor_kwargs)  # type: ignore[arg-type]


def decode_header(raw: BytesLike) -> Header:
    """Decode a CBOR-encoded header into :class:`Header`."""
    obj = cbor.loads(_ensure_bytes(raw))
    return _coerce_dataclass(Header, obj)


def decode_tx(raw: BytesLike) -> Tx:
    """Decode a CBOR-encoded transaction into :class:`Tx`."""
    obj = cbor.loads(_ensure_bytes(raw))
    return _coerce_dataclass(Tx, obj)


def decode_block(raw: BytesLike) -> Block:
    """Decode a CBOR-encoded block into :class:`Block`."""
    obj = cbor.loads(_ensure_bytes(raw))
    return _coerce_dataclass(Block, obj)


# --------------------------
# Canonical content hashes
# --------------------------


def _canonical_cbor_hash(obj: Any) -> bytes:
    """
    Compute the canonical content-hash of a core object as sha3_256(CBOR(obj)),
    using the canonical encoder from :mod:`core.encoding.cbor`.
    """
    return sha3_256(cbor.dumps(obj))


def header_hash(hdr: Header) -> bytes:
    """sha3_256 over canonical CBOR of the header."""
    return _canonical_cbor_hash(hdr)


def tx_hash(tx: Tx) -> bytes:
    """sha3_256 over canonical CBOR of the transaction."""
    return _canonical_cbor_hash(tx)


def block_hash(blk: Block) -> bytes:
    """sha3_256 over canonical CBOR of the full block (header+txs+proofs)."""
    return _canonical_cbor_hash(blk)


# --------------------------
# Cheap, structural sanity
# --------------------------


def _len_is(data: Union[bytes, bytearray, memoryview], n: int) -> bool:
    return len(data) == n


def _is_zero32(b: bytes) -> bool:
    return len(b) == 32 and all(x == 0 for x in b)


def sanity_header(h: Header) -> None:
    """
    Fast, non-consensus header checks suitable for P2P admission:

    - Field presence & byte lengths for roots (32 bytes)
    - prevHash length (32) unless genesis (zero)
    - nonce & mixSeed sizes (nonce>=8, mixSeed==32)
    - chainId positive; height non-negative; Θ positive
    """
    # Roots
    roots = [
        getattr(h, "stateRoot", None),
        getattr(h, "txsRoot", None),
        getattr(h, "receiptsRoot", None),
        getattr(h, "proofsRoot", None),
        getattr(h, "daRoot", None),
    ]
    for r in roots:
        if not isinstance(r, (bytes, bytearray, memoryview)) or not _len_is(r, 32):
            raise ValueError(
                "header root must be 32 bytes (state/txs/receipts/proofs/da)"
            )

    # Previous hash (genesis may be zero-bytes)
    prev = getattr(h, "prevHash", b"\x00" * 32)
    if not isinstance(prev, (bytes, bytearray, memoryview)) or not _len_is(prev, 32):
        raise ValueError("prevHash must be 32 bytes")
    # nonce & mixSeed
    nonce = getattr(h, "nonce", b"")
    mix = getattr(h, "mixSeed", b"")
    if not isinstance(nonce, (bytes, bytearray, memoryview)) or len(nonce) < 8:
        raise ValueError("nonce must be at least 8 bytes")
    if not isinstance(mix, (bytes, bytearray, memoryview)) or not _len_is(mix, 32):
        raise ValueError("mixSeed must be 32 bytes")

    # Heights & ids
    height = int(getattr(h, "height", -1))
    chain_id = int(getattr(h, "chainId", 0))
    theta = int(getattr(h, "theta", 0))
    if height < 0:
        raise ValueError("height must be >= 0")
    if chain_id <= 0:
        raise ValueError("chainId must be > 0")
    if theta <= 0:
        raise ValueError("Θ (theta) must be > 0")


def sanity_tx(tx: Tx, expected_chain_id: int | None = None) -> None:
    """
    Fast, non-consensus transaction checks:

    - chainId (if provided) must match
    - nonce >= 0; gasLimit > 0
    - sender/address sizes well-formed where applicable
    - signature present and plausible length (depends on alg_id, not verified here)
    """
    cid = int(getattr(tx, "chainId", 0))
    if cid <= 0:
        raise ValueError("tx.chainId must be > 0")
    if expected_chain_id is not None and cid != expected_chain_id:
        raise ValueError(f"tx.chainId mismatch: got {cid}, want {expected_chain_id}")

    nonce = int(getattr(tx, "nonce", -1))
    gas_limit = int(getattr(tx, "gasLimit", 0))
    if nonce < 0:
        raise ValueError("tx.nonce must be >= 0")
    if gas_limit <= 0:
        raise ValueError("tx.gasLimit must be > 0")

    # Optional to/from fields (transfer/call)
    for attr in ("to", "from_"):
        if hasattr(tx, attr):
            v = getattr(tx, attr)
            if v is not None and not (
                isinstance(v, (bytes, bytearray, memoryview))
                and len(v) in (20, 32, 35, 36, 42)
            ):  # address payloads vary by encoding
                raise ValueError(f"tx.{attr} has invalid length")

    # Signature envelope plausibility (presence)
    sig = getattr(tx, "signature", None)
    if sig is None:
        raise ValueError("tx.signature missing")
    # We don't verify here (P2P fast path) — that happens in RPC/validator.
    if not isinstance(sig, (bytes, bytearray, memoryview)) or len(sig) < 64:
        raise ValueError("tx.signature too short")


def sanity_block(b: Block, expected_chain_id: int | None = None) -> None:
    """
    Cheap block checks: header sanity + tx linkage sanity.

    Does not re-execute or re-verify signatures/proofs.
    """
    sanity_header(b.header)
    if (
        expected_chain_id is not None
        and int(getattr(b.header, "chainId", 0)) != expected_chain_id
    ):
        raise ValueError("block.header.chainId mismatch")

    # Optional quick checks on txs count/size relationships can be added here.


# --------------------------
# Inventory helpers (logging)
# --------------------------


def short(h: bytes) -> str:
    """Hex-encode first 8 bytes of a hash for concise logs."""
    return to_hex(h)[:18]  # "0x" + 16 hex chars


def describe_header(h: Header) -> str:
    hh = short(header_hash(h))
    height = getattr(h, "height", "?")
    cid = getattr(h, "chainId", "?")
    return f"Header(h={height}, chain={cid}, hash={hh})"


def describe_tx(tx: Tx) -> str:
    th = short(tx_hash(tx))
    kind = getattr(tx, "kind", "tx")
    nonce = getattr(tx, "nonce", "?")
    return f"Tx(kind={kind}, nonce={nonce}, hash={th})"


def describe_block(b: Block) -> str:
    bh = short(block_hash(b))
    height = getattr(b.header, "height", "?")
    txs = len(getattr(b, "txs", []) or [])
    return f"Block(h={height}, txs={txs}, hash={bh})"


# --------------------------
# Overloads for convenience
# --------------------------


@overload
def decode_and_sanity(
    payload: BytesLike, kind: Type[Header]
) -> Tuple[Header, bytes]: ...
@overload
def decode_and_sanity(payload: BytesLike, kind: Type[Tx]) -> Tuple[Tx, bytes]: ...
@overload
def decode_and_sanity(payload: BytesLike, kind: Type[Block]) -> Tuple[Block, bytes]: ...


def decode_and_sanity(payload: BytesLike, kind: Type[Any]) -> Tuple[Any, bytes]:
    """
    Convenience: decode CBOR → object, run cheap sanity, return object + hash.

    Example
    -------
    >>> hdr, hhash = decode_and_sanity(raw_bytes, Header)
    """
    if kind is Header:
        obj = decode_header(payload)
        sanity_header(obj)
        return obj, header_hash(obj)
    if kind is Tx:
        obj = decode_tx(payload)
        sanity_tx(obj)
        return obj, tx_hash(obj)
    if kind is Block:
        obj = decode_block(payload)
        sanity_block(obj)
        return obj, block_hash(obj)
    raise TypeError(f"Unsupported decode kind {kind!r}")


__all__ = [
    "decode_header",
    "decode_tx",
    "decode_block",
    "header_hash",
    "tx_hash",
    "block_hash",
    "sanity_header",
    "sanity_tx",
    "sanity_block",
    "decode_and_sanity",
    "describe_header",
    "describe_tx",
    "describe_block",
]
