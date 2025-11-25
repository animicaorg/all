from __future__ import annotations

"""
Block import (skeleton)
=======================

Responsibilities
---------------
- Decode a block from CBOR bytes or a Python dict into core.types.Block.
- Perform *basic* stateless and linkage checks:
    * chainId matches local params
    * header height monotonic (== parent.height + 1 for non-genesis)
    * parent exists (unless genesis)
    * header hash length sanity, roots length sanity
- Persist header + block to the block DB and update tx index (if available).
- Feed candidate into fork choice and, if selected, update canonical head.

This module intentionally avoids expensive consensus checks (PoIES scoring,
proof verification, DA sampling, etc.). Those live in `consensus/validator.py`
and `proofs/` and can be integrated later. Here we just make the node *able to
boot from genesis* and append well-formed linked blocks.

Public API
----------
- BlockImporter.import_block(raw) -> ImportResult
- BlockImporter.head() -> (height, hash) | None
- BlockImporter.decode_block(raw) -> Block

Where `raw` can be:
- `core.types.block.Block`
- `bytes` (CBOR, matching spec/header_format.cddl + tx_format.cddl)
- `dict` (already-decoded mapping)

Storage interfaces expected (from core/db/block_db.py):
- get_block_by_hash(h) -> Optional[Block]
- get_header_by_hash(h) -> Optional[Header]
- put_header(height, h, header) -> None
- put_block(h, block) -> None
- get_canonical_head() -> Optional[tuple[int, bytes]]
- set_canonical_head(height, h) -> None

Fork choice (from core/chain/fork_choice.py):
- ForkChoice.consider(height=..., block_hash=...) -> bool
- ForkChoice.best() -> Optional[tuple[int, bytes]]
"""

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Optional, Tuple, Union, List, NamedTuple

from core.errors import AnimicaError
from core.types.block import Block
from core.types.header import Header
from core.types.tx import Tx
from core.types.receipt import Receipt  # imported for type completeness; not used here
from core.encoding.cbor import loads as cbor_loads
from core.encoding.cbor import dumps as cbor_dumps
from core.encoding.canonical import header_signing_bytes  # canonical SignBytes for header hashing
from core.utils.hash import sha3_256
from core.chain.fork_choice import ForkChoice
from core.types.params import ChainParams


class ImportErrorCode(str):
    INVALID = "invalid"
    ORPHAN = "orphan"
    DUPLICATE = "duplicate"
    ACCEPTED = "accepted"


class ImportResult(NamedTuple):
    code: str                      # see ImportErrorCode
    height: Optional[int]
    block_hash: Optional[bytes]
    head_changed: bool
    reason: Optional[str] = None


class BlockImportError(AnimicaError):
    pass


def _as_bytes(x: Any, *, name: str) -> bytes:
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    if isinstance(x, str):
        # accept 0x… hex or raw string; prefer hex with even length
        s = x[2:] if x.startswith("0x") else x
        try:
            return bytes.fromhex(s)
        except ValueError:
            raise BlockImportError(f"{name}: expected hex/bytes, got str not hex-decodable")
    raise BlockImportError(f"{name}: expected bytes-like/hex str, got {type(x).__name__}")


def _parent_hash_of(header: Header, payload: Optional[Dict[str, Any]] = None) -> bytes:
    """
    Be tolerant to naming: allow parent_hash / prev_hash / parentHash
    if the Header dataclass doesn't define a single canonical attribute yet.
    """
    for attr in ("parent_hash", "prev_hash", "parentHash", "prevHash"):
        if hasattr(header, attr):
            val = getattr(header, attr)
            return _as_bytes(val, name=f"header.{attr}")
    # fallback to decoded mapping if provided
    if payload:
        for key in ("parent_hash", "prev_hash", "parentHash", "prevHash"):
            if key in payload:
                return _as_bytes(payload[key], name=f"header.{key}")
    raise BlockImportError("header missing parent hash field (parent_hash/prev_hash)")


def _chain_id_of(header: Header, payload: Optional[Dict[str, Any]] = None) -> int:
    if hasattr(header, "chain_id"):
        return int(getattr(header, "chain_id"))
    if hasattr(header, "chainId"):
        return int(getattr(header, "chainId"))
    if payload:
        if "chain_id" in payload:
            return int(payload["chain_id"])
        if "chainId" in payload:
            return int(payload["chainId"])
    raise BlockImportError("header missing chain id (chain_id/chainId)")


def _height_of(header: Header, payload: Optional[Dict[str, Any]] = None) -> int:
    if hasattr(header, "height"):
        return int(getattr(header, "height"))
    if payload and "height" in payload:
        return int(payload["height"])
    raise BlockImportError("header missing height")


def compute_header_hash(header: Header) -> bytes:
    """
    Canonical header hash (sha3_256 over header SignBytes).
    """
    sb = header_signing_bytes(header)
    return sha3_256(sb)


def _dataclass_from_dict(dc_type, data: Dict[str, Any]):
    # Best-effort constructor: pass through only fields known to the dataclass
    # so loose CBOR maps don't break construction.
    if not is_dataclass(dc_type):
        # For NamedTuple-like or other typed classes, try direct ** mapping
        return dc_type(**data)
    field_names = {f.name for f in dc_type.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    filtered = {k: v for k, v in data.items() if k in field_names}
    return dc_type(**filtered)  # type: ignore[call-arg]


def block_from_mapping(m: Dict[str, Any]) -> Block:
    """
    Construct a Block dataclass from a (already CBOR-decoded) mapping.

    Expected keys: "header", "txs", optionally "proofs", "receipts".
    """
    if "header" not in m or "txs" not in m:
        raise BlockImportError("block mapping missing required keys (header, txs)")

    hdr_payload = m["header"]
    if not isinstance(hdr_payload, dict):
        raise BlockImportError("header must decode to a map")
    header = _dataclass_from_dict(Header, hdr_payload)

    txs_payload = m.get("txs", [])
    if not isinstance(txs_payload, list):
        raise BlockImportError("txs must decode to a list")
    txs: List[Tx] = []
    for t in txs_payload:
        if isinstance(t, dict):
            txs.append(_dataclass_from_dict(Tx, t))
        else:
            raise BlockImportError("each tx must decode to a map")

    block_payload = {"header": header, "txs": txs}

    # Optional fields (pass through if your Block dataclass has them)
    for opt_key in ("proofs", "receipts"):
        if opt_key in m:
            block_payload[opt_key] = m[opt_key]

    return _dataclass_from_dict(Block, block_payload)  # type: ignore[return-value]


def decode_block(raw: Union[Block, bytes, Dict[str, Any]]) -> Tuple[Block, Dict[str, Any]]:
    """
    Decode `raw` into a Block and return `(block, raw_mapping_for_header_fallbacks)`.

    The second element preserves the original mapping to help extract fields
    (e.g., chainId or parentHash) if the dataclass field names differ.
    """
    if isinstance(raw, Block):
        # For fallback extraction, synthesize a minimal mapping from the dataclass.
        mapping = asdict(raw.header) if hasattr(raw, "header") else {}
        return raw, {"header": mapping}
    if isinstance(raw, (bytes, bytearray)):
        m = cbor_loads(bytes(raw))
        if not isinstance(m, dict):
            raise BlockImportError("CBOR block must decode to a map")
        return block_from_mapping(m), m
    if isinstance(raw, dict):
        return block_from_mapping(raw), raw
    raise BlockImportError(f"unsupported block input type: {type(raw).__name__}")


class BlockImporter:
    """
    Block importer that knows how to decode, sanity-check, link, persist, and
    update fork choice & canonical head.
    """

    __slots__ = ("params", "block_db", "tx_index", "fork_choice")

    def __init__(self, *, params: ChainParams, block_db, tx_index=None, fork_choice: Optional[ForkChoice] = None):
        self.params = params
        self.block_db = block_db
        self.tx_index = tx_index
        self.fork_choice = fork_choice or ForkChoice()

    # --- Basics -------------------------------------------------------------

    def head(self) -> Optional[Tuple[int, bytes]]:
        return self.block_db.get_canonical_head()

    # --- Import -------------------------------------------------------------

    def import_block(self, raw: Union[Block, bytes, Dict[str, Any]]) -> ImportResult:
        try:
            block, mapping = decode_block(raw)
            header: Header = block.header
            hdr_map = mapping.get("header", {}) if isinstance(mapping, dict) else {}

            # Compute hash
            h = compute_header_hash(header)

            # Duplicate?
            if self.block_db.get_header_by_hash(h) is not None:
                # already persisted
                head_changed = self.fork_choice.consider(height=_height_of(header, hdr_map), block_hash=h)
                self._maybe_update_canonical_head()
                return ImportResult(ImportErrorCode.DUPLICATE, _height_of(header, hdr_map), h, head_changed, "duplicate")

            # chainId check
            chain_id = _chain_id_of(header, hdr_map)
            if chain_id != self.params.chain_id:
                return ImportResult(
                    ImportErrorCode.INVALID, None, None, False,
                    f"chainId mismatch: got {chain_id}, expected {self.params.chain_id}"
                )

            height = _height_of(header, hdr_map)
            parent_hash = _parent_hash_of(header, hdr_map)

            # Genesis vs non-genesis
            if height == 0:
                # Must match configured genesis in DB (or DB empty)
                current_head = self.block_db.get_canonical_head()
                if current_head is not None:
                    return ImportResult(ImportErrorCode.DUPLICATE, 0, h, False, "genesis already exists")
                # Minimal header sanity
                self._sanity_header(header)
                # Persist
                self.block_db.put_header(0, h, header)
                self.block_db.put_block(h, block)
                # Update head
                self.block_db.set_canonical_head(0, h)
                self.fork_choice.reset()
                self.fork_choice.consider(height=0, block_hash=h)
                return ImportResult(ImportErrorCode.ACCEPTED, 0, h, True, None)

            # Non-genesis needs parent
            parent_header = self.block_db.get_header_by_hash(parent_hash)
            if parent_header is None:
                return ImportResult(ImportErrorCode.ORPHAN, height, h, False, "missing parent")

            # Height continuity
            parent_height = _height_of(parent_header)  # type: ignore[arg-type]
            if height != parent_height + 1:
                return ImportResult(
                    ImportErrorCode.INVALID, height, h, False,
                    f"height continuity failed: got {height}, parent at {parent_height}"
                )

            # Basic header sanity
            self._sanity_header(header)

            # Persist header & block
            self.block_db.put_header(height, h, header)
            self.block_db.put_block(h, block)

            # Optional: index txs
            if self.tx_index is not None and getattr(block, "txs", None):
                for idx, tx in enumerate(block.txs):
                    try:
                        tx_hash = self._tx_hash(tx)
                        self.tx_index.put(tx_hash, height, idx)
                    except Exception:  # pragma: no cover - non-critical
                        pass

            # Fork choice & canonical head update
            head_changed = self.fork_choice.consider(height=height, block_hash=h)
            self._maybe_update_canonical_head()

            return ImportResult(ImportErrorCode.ACCEPTED, height, h, head_changed, None)

        except BlockImportError as e:
            return ImportResult(ImportErrorCode.INVALID, None, None, False, str(e))

    # --- Helpers ------------------------------------------------------------

    def _maybe_update_canonical_head(self) -> None:
        best = self.fork_choice.best()
        if best is None:
            return
        cur = self.block_db.get_canonical_head()
        if cur != best:
            self.block_db.set_canonical_head(best[0], best[1])

    def _sanity_header(self, header: Header) -> None:
        """
        Minimal structural checks that don't require heavy state/consensus:
        - hash/roots lengths if present are sane (e.g., 32 bytes)
        - Θ (theta) domain sanity if present (non-negative, bounded)
        - mixSeed/nonce length sanity
        """
        # Tolerate differing attribute names (snake/camel)
        def has(name: str) -> bool:
            return hasattr(header, name)

        def get(name: str, alt: Optional[str] = None) -> Any:
            if hasattr(header, name):
                return getattr(header, name)
            if alt and hasattr(header, alt):
                return getattr(header, alt)
            return None

        def ensure_len(b: Optional[bytes], want: int, field: str):
            if b is None:
                return
            bb = _as_bytes(b, name=field)
            if len(bb) != want:
                raise BlockImportError(f"{field}: expected {want} bytes, got {len(bb)}")

        # 32-byte roots if present
        for fld, alt in [
            ("state_root", "stateRoot"),
            ("txs_root", "txsRoot"),
            ("receipts_root", "receiptsRoot"),
            ("proofs_root", "proofsRoot"),
            ("da_root", "daRoot"),
        ]:
            ensure_len(get(fld, alt), 32, fld)

        # nonce / mixSeed (length-free but keep under 64 bytes for now)
        for fld, alt in [("nonce", None), ("mix_seed", "mixSeed")]:
            v = get(fld, alt)
            if v is None:
                continue
            bb = _as_bytes(v, name=fld)
            if len(bb) > 64:
                raise BlockImportError(f"{fld}: too long ({len(bb)} bytes)")

        # Θ (theta) sanity (if present)
        theta = get("theta", "Θ")
        if theta is not None:
            t = int(theta)
            if t < 0:
                raise BlockImportError("theta must be non-negative")
            # upper bound guard (µ-nats scale) — policy will clamp tighter
            if t > 10**12:
                raise BlockImportError("theta unreasonably large")

    def _tx_hash(self, tx: Tx) -> bytes:
        # Canonical: sha3_256 over the tx SignBytes (encoding/ canonical domain).
        from core.encoding.canonical import tx_signing_bytes
        return sha3_256(tx_signing_bytes(tx))


# Convenience: tiny CLI for manual testing
if __name__ == "__main__":  # pragma: no cover
    import argparse
    from core.db.sqlite import SQLiteKV
    from core.db.block_db import BlockDB
    from core.genesis.loader import load_genesis
    from core.config import load_config

    ap = argparse.ArgumentParser(description="Import a CBOR-encoded block into the local DB")
    ap.add_argument("--db", default="sqlite:///animica.db")
    ap.add_argument("--genesis", default="core/genesis/genesis.json")
    ap.add_argument("--block", required=True, help="path to block.cbor")
    args = ap.parse_args()

    cfg = load_config()
    kv = SQLiteKV.from_dsn(args.db)
    bdb = BlockDB(kv)
    params, _genesis_header = load_genesis(args.genesis, kv, bdb)

    with open(args.block, "rb") as f:
        blob = f.read()

    importer = BlockImporter(params=params, block_db=bdb)
    res = importer.import_block(blob)
    print("Import result:", res)
    print("Head:", importer.head())
