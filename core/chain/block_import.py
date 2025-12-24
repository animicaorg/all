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
- Track and update difficulty (Θ) based on block intervals using EMA retargeting.

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

from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, is_dataclass
from functools import lru_cache
import logging
import os
import time
from typing import Any, Deque, Dict, Iterable, List, NamedTuple, Optional, Tuple, Union

from core.db.block_db import k_hix
from core.encoding.canonical import \
    header_signing_bytes  # canonical SignBytes for header hashing
from core.encoding.cbor import dumps as cbor_dumps
from core.encoding.cbor import loads as cbor_loads
from core.errors import AnimicaError
from core.genesis.genesis_loader import get_genesis
from core.genesis.loader import load_chain_params_from_genesis
from core.types.block import Block
from core.types.header import Header
from core.types.params import ChainParams
from core.types.receipt import \
    Receipt  # imported for type completeness; not used here
from core.types.tx import Tx
from core.utils.hash import sha3_256
from core.utils.pow import micro_threshold_to_target256

# Import difficulty adjustment functions
try:
    from consensus import difficulty as diff
    DIFFICULTY_AVAILABLE = True
except ImportError:
    DIFFICULTY_AVAILABLE = False
    diff = None  # type: ignore[assignment]

try:
    from consensus.fork_choice import ForkChoice as WeightForkChoice
except Exception:  # pragma: no cover - consensus optional
    WeightForkChoice = None  # type: ignore[assignment]

log = logging.getLogger("animica.chain.block_import")


class ImportErrorCode(str):
    INVALID = "invalid"
    ORPHAN = "orphan"
    DUPLICATE = "duplicate"
    ACCEPTED = "accepted"


class ImportResult(NamedTuple):
    code: str  # see ImportErrorCode
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
            raise BlockImportError(
                f"{name}: expected hex/bytes, got str not hex-decodable"
            )
    raise BlockImportError(
        f"{name}: expected bytes-like/hex str, got {type(x).__name__}"
    )


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


def _timestamp_of(header: Header, payload: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Extract timestamp from header (returns None if not present)."""
    if hasattr(header, "timestamp"):
        ts = getattr(header, "timestamp")
        if ts is not None:
            return int(ts)
    if payload:
        if "timestamp" in payload and payload["timestamp"] is not None:
            return int(payload["timestamp"])
        if "time" in payload and payload["time"] is not None:
            return int(payload["time"])
    return None


def compute_header_hash(header: Header) -> bytes:
    """
    Canonical header hash. Prefer header.hash() to match BlockDB storage.
    """
    if hasattr(header, "hash") and callable(getattr(header, "hash")):
        return bytes(header.hash())  # type: ignore[no-any-return]
    sb = header_signing_bytes(header)
    return sha3_256(sb)


def _weight_micro_of(
    header: Header, payload: Optional[Dict[str, Any]], params: ChainParams
) -> int:
    for attr in ("thetaMicro", "theta_micro", "theta", "Θ"):
        if hasattr(header, attr):
            try:
                return int(getattr(header, attr))
            except Exception:
                pass
    if payload:
        for key in ("thetaMicro", "theta_micro", "theta", "Θ"):
            if key in payload:
                try:
                    return int(payload[key])
                except Exception:
                    pass
    return int(params.theta_initial)


def _theta_to_target(theta_micro: int) -> int:
    """Derive a block target from θ for lightweight validation."""
    return micro_threshold_to_target256(theta_micro)


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

    block_payload = {"header": header, "txs": txs, "proofs": []}

    # Optional fields (pass through if your Block dataclass has them)
    if "proofs" in m:
        block_payload["proofs"] = m["proofs"]
    if "receipts" in m:
        block_payload["receipts"] = m["receipts"]

    return _dataclass_from_dict(Block, block_payload)  # type: ignore[return-value]


def decode_block(
    raw: Union[Block, bytes, Dict[str, Any]],
) -> Tuple[Block, Dict[str, Any]]:
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
    update fork choice & canonical head. Tracks difficulty (Θ) adjustments.
    """

    __slots__ = (
        "params",
        "block_db",
        "tx_index",
        "fork_choice",
        "difficulty_state",
        "_last_block_time",
        "_orphan_pool",
        "_orphan_parents",
        "_max_orphans",
        "_max_future_seconds",
        "_min_block_spacing_ms",
    )

    def __init__(
        self,
        *,
        params: ChainParams,
        block_db,
        tx_index=None,
        fork_choice: Optional[Any] = None,
    ):
        self.params = params
        self.block_db = block_db
        self.tx_index = tx_index
        self.fork_choice = fork_choice
        
        # Initialize difficulty adjustment state
        self.difficulty_state = None
        self._last_block_time: Optional[int] = None
        if DIFFICULTY_AVAILABLE:
            self._init_difficulty_state()
        self._orphan_pool: "OrderedDict[bytes, _OrphanBlock]" = OrderedDict()
        self._orphan_parents: Dict[bytes, Deque[bytes]] = {}
        self._max_orphans = int(os.getenv("ANIMICA_ORPHAN_POOL_MAX", "1000"))
        self._max_future_seconds = int(os.getenv("ANIMICA_MAX_FUTURE_SECONDS", "5"))
        self._min_block_spacing_ms = int(os.getenv("ANIMICA_MIN_BLOCK_SPACING_MS", "0"))
        self._init_fork_choice_from_db()

    # --- Basics -------------------------------------------------------------

    def head(self) -> Optional[Tuple[int, bytes]]:
        return self.block_db.get_canonical_head()

    # --- Difficulty adjustment ----------------------------------------------

    def _init_difficulty_state(self) -> None:
        """
        Initialize difficulty state from params. Called once at startup.
        Maps ChainParams retarget config to consensus.difficulty RetargetParams.
        """
        if not DIFFICULTY_AVAILABLE or diff is None:
            return
        
        try:
            # Map from ChainParams to consensus.difficulty.RetargetParams
            # ChainParams uses: retarget.window, retarget.ema_alpha, retarget.bounds.{min,max}
            # consensus.difficulty uses: target_block_time_s, half_life_blocks, gain_beta, 
            #                            step_clamp_micro, theta_min_micro, theta_max_micro
            
            # Convert window to half_life_blocks (approximation: use window as half-life)
            half_life_blocks = float(self.params.retarget.window)
            
            # Use ema_alpha as gain_beta (proportional gain)
            gain_beta = float(self.params.retarget.ema_alpha)
            
            # Compute step clamp from bounds: convert multiplicative ratio to additive µ-nats
            # For a typical initial theta, compute a reasonable step clamp
            # bounds.max = 2.0 means we can double; that's ln(2) ≈ 0.693 nats
            # Convert to µ-nats: ~693,000 µ-nats per retarget window
            # Per-block step: divide by window size
            import math
            max_change_nats = math.log(self.params.retarget.bounds.max)  # ln(2) ≈ 0.693 for 2x
            step_clamp_micro = int(max_change_nats * 1_000_000 / max(1, half_life_blocks))
            step_clamp_micro = max(100_000, min(1_000_000, step_clamp_micro))  # reasonable bounds
            
            retarget_params = diff.RetargetParams(
                target_block_time_s=float(self.params.block.target_seconds),
                half_life_blocks=half_life_blocks,
                gain_beta=gain_beta,
                step_clamp_micro=step_clamp_micro,
                theta_min_micro=500_000,  # 0.5 nats - very easy
                theta_max_micro=30_000_000,  # 30 nats - very hard
            )
            
            # Initialize state with genesis theta
            theta_init = int(self.params.theta_initial)
            self.difficulty_state = diff.init_state(retarget_params, theta_init_micro=theta_init)
            
        except Exception as e:  # pragma: no cover
            # If difficulty module is unavailable or initialization fails, log and continue
            # The node can still import blocks without difficulty adjustment
            import logging
            logging.warning(f"Failed to initialize difficulty state: {e}")
            self.difficulty_state = None

    def _update_difficulty(self, block_timestamp: int) -> None:
        """
        Update difficulty state based on the time since the last block.
        
        Args:
            block_timestamp: Unix timestamp (seconds) of the current block
        """
        if not DIFFICULTY_AVAILABLE or diff is None or self.difficulty_state is None:
            return
        
        try:
            # Calculate time delta since last block
            if self._last_block_time is not None:
                dt_seconds = float(block_timestamp - self._last_block_time)
                
                # Sanity check: ensure positive, non-zero time delta
                if dt_seconds > 0:
                    # Update theta using the consensus.difficulty mechanism
                    self.difficulty_state = diff.update_theta(
                        self.difficulty_state,
                        dt_seconds=dt_seconds,
                        blocks_skipped=1,
                    )
            
            # Update last block time for next iteration
            self._last_block_time = block_timestamp
            
        except Exception as e:  # pragma: no cover
            import logging
            logging.warning(f"Failed to update difficulty: {e}")

    def get_current_difficulty(self) -> int:
        """
        Get the current difficulty threshold (Θ) in micro-nats.
        
        Returns:
            Current theta_micro value, or genesis theta_initial if difficulty state not available.
        """
        if self.difficulty_state is not None:
            return int(self.difficulty_state.theta_micro)
        return int(self.params.theta_initial)

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
                parent_hash = _parent_hash_of(header, hdr_map)
                self._ensure_fork_choice_parent(parent_hash)
                if self.fork_choice is None:
                    self._init_fork_choice_from_db()
                if self.fork_choice is not None and not self.fork_choice.has(h):
                    result = self.fork_choice.add_block(
                        h=h,
                        parent=parent_hash,
                        height=_height_of(header, hdr_map),
                        weight_micro=_weight_micro_of(header, hdr_map, self.params),
                    )
                    if result.became_best:
                        self._apply_reorg(result.detached, result.attached, result.best)
                return ImportResult(
                    ImportErrorCode.DUPLICATE,
                    _height_of(header, hdr_map),
                    h,
                    False,
                    "duplicate",
                )

            # chainId check
            chain_id = _chain_id_of(header, hdr_map)
            if chain_id != self.params.chain_id:
                return ImportResult(
                    ImportErrorCode.INVALID,
                    None,
                    None,
                    False,
                    f"chainId mismatch: got {chain_id}, expected {self.params.chain_id}",
                )

            height = _height_of(header, hdr_map)
            parent_hash = _parent_hash_of(header, hdr_map)

            # Genesis vs non-genesis
            if height == 0:
                # Must match configured genesis in DB (or DB empty)
                current_head = self.block_db.get_canonical_head()
                if current_head is not None:
                    return ImportResult(
                        ImportErrorCode.DUPLICATE, 0, h, False, "genesis already exists"
                    )
                # Minimal header sanity
                self._sanity_header(header)
                # Persist
                self._store_header(0, h, header)
                self._store_block(h, block)
                # Update head
                self.block_db.set_canonical_head(0, h)
                self._init_fork_choice(genesis_hash=h, header=header, payload=hdr_map)
                
                # Initialize difficulty tracking with genesis timestamp
                timestamp = _timestamp_of(header, hdr_map)
                if timestamp is not None:
                    self._last_block_time = timestamp
                
                # Index canonical txs if any
                self._index_block_if_canonical(height=0, block_hash=h, block=block)

                return ImportResult(ImportErrorCode.ACCEPTED, 0, h, True, None)

            # Non-genesis needs parent
            parent_header = self.block_db.get_header_by_hash(parent_hash)
            if parent_header is None:
                self._remember_orphan(h, block, mapping, parent_hash, height)
                return ImportResult(
                    ImportErrorCode.ORPHAN, height, h, False, "missing parent"
                )

            # Height continuity
            parent_height = _height_of(parent_header)  # type: ignore[arg-type]
            if height != parent_height + 1:
                return ImportResult(
                    ImportErrorCode.INVALID,
                    height,
                    h,
                    False,
                    f"height continuity failed: got {height}, parent at {parent_height}",
                )

            timestamp_error = self._timestamp_sanity(header, parent_header, hdr_map)
            if timestamp_error is not None:
                return ImportResult(ImportErrorCode.INVALID, height, h, False, timestamp_error)

            # Basic header sanity
            self._sanity_header(header)

            pow_error = self._pow_sanity(
                header=header, header_hash=h, payload=hdr_map
            )
            if pow_error is not None:
                return ImportResult(ImportErrorCode.INVALID, height, h, False, pow_error)

            # Persist header & block
            self._store_header(height, h, header)
            self._store_block(h, block)

            # Fork choice & canonical head update
            head_changed = self._apply_fork_choice(
                header=header,
                header_hash=h,
                parent_hash=parent_hash,
                payload=hdr_map,
                block=block,
            )

            self._process_orphans(parent_hash=h)

            return ImportResult(ImportErrorCode.ACCEPTED, height, h, head_changed, None)

        except BlockImportError as e:
            return ImportResult(ImportErrorCode.INVALID, None, None, False, str(e))

    # --- Helpers ------------------------------------------------------------

    def _maybe_update_canonical_head(self) -> None:
        return

    def _store_header(self, height: int, h: bytes, header: Header) -> None:
        """
        Persist header using whichever BlockDB interface is available.

        Legacy mock DBs expose put_header(height, hash, header); modern BlockDB
        exposes put_header(header) and derives the hash internally.
        """
        if hasattr(self.block_db, "put_header"):
            try:
                self.block_db.put_header(header)
                return
            except TypeError:
                pass
            try:
                self.block_db.put_header(header, None)
                return
            except TypeError:
                pass
            try:
                self.block_db.put_header(height, h, header)
                return
            except TypeError:
                pass
        if hasattr(self.block_db, "write_header"):
            try:
                self.block_db.write_header(height, header)
                return
            except TypeError:
                try:
                    self.block_db.write_header(height, h, header)
                    return
                except TypeError:
                    pass
        raise BlockImportError("block_db missing header writer")

    def _store_block(self, h: bytes, block: Block) -> None:
        """
        Persist block using whichever BlockDB interface is available.

        Legacy mock DBs expose put_block(hash, block); modern BlockDB exposes
        put_block(block) and derives the hash internally.
        """
        if hasattr(self.block_db, "put_block"):
            try:
                self.block_db.put_block(block)
                return
            except TypeError:
                pass
            try:
                self.block_db.put_block(block, None)
                return
            except TypeError:
                pass
            try:
                self.block_db.put_block(h, block)
                return
            except TypeError:
                pass
        raise BlockImportError("block_db missing block writer")

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

        # nonce: can be int or bytes depending on header version
        nonce_val = get("nonce", None)
        if nonce_val is not None:
            if isinstance(nonce_val, int):
                # int nonce is valid (uint type in CBOR/CDDL)
                if nonce_val < 0:
                    raise BlockImportError(f"nonce must be non-negative, got {nonce_val}")
            else:
                # bytes nonce (legacy): check length
                bb = _as_bytes(nonce_val, name="nonce")
                if len(bb) > 64:
                    raise BlockImportError(f"nonce: too long ({len(bb)} bytes)")
        
        # mixSeed (length-free but keep under 64 bytes for now)
        for fld, alt in [("mix_seed", "mixSeed")]:
            v = get(fld, alt)
            if v is None:
                continue
            bb = _as_bytes(v, name=fld)
            if len(bb) > 64:
                raise BlockImportError(f"{fld}: too long ({len(bb)} bytes)")

        # Θ (theta) sanity (if present)
        theta = get("thetaMicro", "theta_micro")
        if theta is None:
            theta = get("theta", "Θ")
        if theta is not None:
            t = int(theta)
            if t < 0:
                raise BlockImportError("theta must be non-negative")
            # upper bound guard (µ-nats scale) — policy will clamp tighter
            if t > 10**12:
                raise BlockImportError("theta unreasonably large")

    def _pow_sanity(
        self,
        *,
        header: Header,
        header_hash: bytes,
        payload: Dict[str, Any],
    ) -> Optional[str]:
        """
        Lightweight PoW threshold check aligned with miner target rules.
        """
        try:
            theta_micro = _weight_micro_of(header, payload, self.params)
            target = _theta_to_target(int(theta_micro))
            pow_hash_int = int.from_bytes(header_hash, "big")
            if pow_hash_int > target:
                if os.getenv("ANIMICA_SYNC_DEBUG") == "1":
                    log.debug(
                        "PoW target mismatch",
                        extra={
                            "height": _height_of(header, payload),
                            "header_hash": header_hash.hex(),
                            "theta_micro": int(theta_micro),
                            "pow_hash_int": pow_hash_int,
                            "target": target,
                        },
                    )
                return "pow target not met"
        except Exception as e:
            if os.getenv("ANIMICA_SYNC_DEBUG") == "1":
                log.debug(
                    "PoW check failed",
                    extra={
                        "header_hash": header_hash.hex(),
                        "reason": str(e),
                    },
                )
            return f"pow check failed: {e}"
        return None

    def _tx_hash(self, tx: Tx) -> bytes:
        # Canonical: sha3_256 over the tx SignBytes (encoding/ canonical domain).
        from core.encoding.canonical import tx_signing_bytes

        return sha3_256(tx_signing_bytes(tx))

    # --- Fork choice & reorg ------------------------------------------------

    def _init_fork_choice(
        self,
        *,
        genesis_hash: bytes,
        header: Optional[Header] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        if self.fork_choice is not None or WeightForkChoice is None:
            return
        genesis_weight = (
            _weight_micro_of(header, payload, self.params) if header is not None else 0
        )
        self.fork_choice = WeightForkChoice(
            genesis_hash=genesis_hash,
            genesis_weight_micro=genesis_weight,
            genesis_height=0,
        )

    def _init_fork_choice_from_db(self) -> None:
        if self.fork_choice is not None or WeightForkChoice is None:
            return
        head = self.block_db.get_canonical_head()
        genesis_hash = None
        if head is not None and hasattr(self.block_db, "get_canonical_hash"):
            genesis_hash = self.block_db.get_canonical_hash(0)
        if genesis_hash is None and hasattr(self.block_db, "get_genesis_hash"):
            genesis_hash = self.block_db.get_genesis_hash()
        if genesis_hash is None:
            genesis_hash = self.params.genesis_hash
        if not genesis_hash:
            return
        genesis_header = self.block_db.get_header_by_hash(genesis_hash)
        if genesis_header is None and head is None:
            return
        genesis_weight = (
            _weight_micro_of(genesis_header, None, self.params)
            if genesis_header is not None
            else int(self.params.theta_initial)
        )
        self.fork_choice = WeightForkChoice(
            genesis_hash=genesis_hash,
            genesis_weight_micro=genesis_weight,
            genesis_height=0,
        )
        self._seed_fork_choice_from_canonical()

    def _seed_fork_choice_from_canonical(self) -> None:
        if self.fork_choice is None:
            return
        head = self.block_db.get_canonical_head()
        if not head or head[0] <= 0:
            return
        head_hash = head[1]
        chain: List[Tuple[Header, bytes]] = []
        cursor = head_hash
        while True:
            header = self.block_db.get_header_by_hash(cursor)
            if header is None:
                break
            if _height_of(header) == 0:
                break
            chain.append((header, cursor))
            cursor = _parent_hash_of(header)
        for header, h in reversed(chain):
            self.fork_choice.add_block(
                h=h,
                parent=_parent_hash_of(header),
                height=_height_of(header),
                weight_micro=_weight_micro_of(header, None, self.params),
            )

    def _ensure_fork_choice_parent(self, parent_hash: bytes) -> None:
        if self.fork_choice is None or WeightForkChoice is None:
            return
        if self.fork_choice.has(parent_hash):
            return
        chain: List[Tuple[Header, bytes]] = []
        cursor = parent_hash
        while True:
            header = self.block_db.get_header_by_hash(cursor)
            if header is None:
                break
            chain.append((header, cursor))
            if _height_of(header) == 0:
                break
            cursor = _parent_hash_of(header)
        for header, h in reversed(chain):
            if self.fork_choice.has(h):
                continue
            self.fork_choice.add_block(
                h=h,
                parent=_parent_hash_of(header),
                height=_height_of(header),
                weight_micro=_weight_micro_of(header, None, self.params),
            )

    def _apply_fork_choice(
        self,
        *,
        header: Header,
        header_hash: bytes,
        parent_hash: bytes,
        payload: Dict[str, Any],
        block: Block,
    ) -> bool:
        if self.fork_choice is None:
            self._init_fork_choice_from_db()
        if self.fork_choice is None:
            return False
        self._ensure_fork_choice_parent(parent_hash)
        weight = _weight_micro_of(header, payload, self.params)
        result = self.fork_choice.add_block(
            h=header_hash,
            parent=parent_hash,
            height=_height_of(header, payload),
            weight_micro=weight,
        )
        if not result.became_best:
            return False
        self._apply_reorg(result.detached, result.attached, result.best)
        return True

    def _apply_reorg(
        self,
        detached: Iterable[bytes],
        attached: Iterable[bytes],
        best,
    ) -> None:
        old_head = self.block_db.get_canonical_head()
        old_height = old_head[0] if old_head else None
        old_hash = old_head[1] if old_head else None

        detached_list = list(detached)
        attached_list = list(attached)
        if detached_list or attached_list:
            log.info(
                "reorg",
                extra={
                    "depth": len(detached_list),
                    "old_head": old_hash.hex() if old_hash else None,
                    "new_head": best.h.hex(),
                    "new_height": best.height,
                },
            )

        # Reset difficulty anchor to the LCA timestamp if possible.
        if attached_list:
            first_header = self.block_db.get_header_by_hash(attached_list[0])
            if first_header is not None:
                parent_header = self.block_db.get_header_by_hash(
                    _parent_hash_of(first_header)
                )
                parent_ts = _timestamp_of(parent_header) if parent_header else None
                if parent_ts is not None:
                    self._last_block_time = parent_ts

        # Remove canonical indices for detached blocks
        if self.tx_index is not None:
            for h in detached_list:
                header = self.block_db.get_header_by_hash(h)
                if header is None:
                    continue
                height = _height_of(header)
                self._remove_block_index(height)

        # Apply new canonical blocks
        for h in attached_list:
            header = self.block_db.get_header_by_hash(h)
            if header is None:
                continue
            height = _height_of(header)
            if hasattr(self.block_db, "set_canonical"):
                self.block_db.set_canonical(height, h)
            if self.tx_index is not None:
                block = self.block_db.get_block_by_hash(h)
                if block is not None:
                    self._index_block_if_canonical(height=height, block_hash=h, block=block)

            ts = _timestamp_of(header)
            if ts is not None:
                self._update_difficulty(ts)

        if old_height is not None and best.height < old_height:
            self._delete_canonical_range(best.height + 1, old_height)

        if hasattr(self.block_db, "set_head"):
            self.block_db.set_head(best.height, best.h)
        else:
            self.block_db.set_canonical_head(best.height, best.h)

    def fork_tips(self, limit: int = 5) -> List[Dict[str, Any]]:
        if self.fork_choice is None:
            return []
        tips = []
        best = self.fork_choice.best_tip
        for h in self.fork_choice.tip_set():
            node = self.fork_choice.nodes.get(h)
            if node is None:
                continue
            tips.append(
                {
                    "hash": "0x" + node.h.hex(),
                    "height": int(node.height),
                    "total_work": int(node.cum_weight_micro),
                    "is_best": node.h == best.h,
                }
            )
        tips.sort(key=lambda item: (-item["total_work"], item["hash"]))
        return tips[: max(1, int(limit))]

    def _delete_canonical_range(self, start: int, end: int) -> None:
        if start > end:
            return
        kv = getattr(self.block_db, "kv", None)
        if kv is None or not hasattr(kv, "delete"):
            return
        for height in range(start, end + 1):
            try:
                kv.delete(k_hix(height))
            except Exception:
                pass
            self._remove_block_index(height)

    def _index_block_if_canonical(
        self, *, height: int, block_hash: bytes, block: Block
    ) -> None:
        if self.tx_index is None or not getattr(block, "txs", None):
            return
        tx_hashes = []
        for tx in block.txs:
            try:
                tx_hashes.append(self._tx_hash(tx))
            except Exception:  # pragma: no cover
                return
        if hasattr(self.tx_index, "index_block"):
            try:
                self.tx_index.index_block(height, block_hash, tx_hashes)
                return
            except Exception:
                pass
        if hasattr(self.tx_index, "put"):
            for idx, tx_hash in enumerate(tx_hashes):
                try:
                    self.tx_index.put(tx_hash, height, idx)
                except Exception:  # pragma: no cover
                    continue

    def _remove_block_index(self, height: int) -> None:
        if self.tx_index is None:
            return
        if hasattr(self.tx_index, "remove_block"):
            try:
                self.tx_index.remove_block(height)
                return
            except Exception:
                return

    # --- Orphan handling ----------------------------------------------------

    def _remember_orphan(
        self,
        header_hash: bytes,
        block: Block,
        mapping: Dict[str, Any],
        parent_hash: bytes,
        height: int,
    ) -> None:
        if header_hash in self._orphan_pool:
            return
        entry = _OrphanBlock(
            header_hash=header_hash,
            parent_hash=parent_hash,
            height=height,
            block=block,
            mapping=mapping,
            received_at=time.time(),
        )
        self._orphan_pool[header_hash] = entry
        self._orphan_parents.setdefault(parent_hash, deque()).append(header_hash)
        while len(self._orphan_pool) > self._max_orphans:
            old_hash, old_entry = self._orphan_pool.popitem(last=False)
            parent_q = self._orphan_parents.get(old_entry.parent_hash)
            if parent_q and old_hash in parent_q:
                parent_q.remove(old_hash)
            if parent_q and not parent_q:
                self._orphan_parents.pop(old_entry.parent_hash, None)
        log.debug(
            "orphan stored",
            extra={"hash": header_hash.hex(), "parent": parent_hash.hex(), "height": height},
        )

    def _process_orphans(self, parent_hash: bytes) -> None:
        queue = self._orphan_parents.pop(parent_hash, deque())
        while queue:
            child_hash = queue.popleft()
            entry = self._orphan_pool.pop(child_hash, None)
            if entry is None:
                continue
            self.import_block(entry.block)

    # --- Timestamp guardrails ----------------------------------------------

    def _timestamp_sanity(
        self, header: Header, parent_header: Header, payload: Dict[str, Any]
    ) -> Optional[str]:
        ts = _timestamp_of(header, payload)
        if ts is None:
            return None
        parent_ts = _timestamp_of(parent_header)
        if parent_ts is not None and ts < parent_ts:
            return "timestamp regression"
        if self._max_future_seconds > 0:
            now = int(time.time())
            if ts > now + self._max_future_seconds:
                return "timestamp too far in future"
        if self._min_block_spacing_ms > 0 and parent_ts is not None:
            delta_ms = (ts - parent_ts) * 1000
            if delta_ms < self._min_block_spacing_ms:
                return "timestamp spacing too short"
        return None


@dataclass(frozen=True)
class _OrphanBlock:
    header_hash: bytes
    parent_hash: bytes
    height: int
    block: Block
    mapping: Dict[str, Any]
    received_at: float


_IMPORTER_CACHE: Dict[int, BlockImporter] = {}


@lru_cache(maxsize=4)
def _load_chain_params_for_import(genesis_path: Optional[str]) -> ChainParams:
    bundle = get_genesis(genesis_path)
    return load_chain_params_from_genesis(bundle.genesis, base_dir=bundle.base_dir)


def _get_importer(
    block_db,
    tx_index,
    params: ChainParams,
) -> BlockImporter:
    cached = _IMPORTER_CACHE.get(id(block_db))
    if cached is not None and cached.params.chain_id == params.chain_id:
        return cached
    importer = BlockImporter(params=params, block_db=block_db, tx_index=tx_index)
    _IMPORTER_CACHE[id(block_db)] = importer
    return importer


def import_block(
    block_db,
    state_db,  # unused but kept for signature compatibility
    tx_index,
    raw_block,
    genesis_path: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Module-level adapter for P2P/RPC that mirrors the legacy signature expected
    by p2p.deps._lazy_core. It instantiates (and caches) a BlockImporter using
    chain params loaded from the configured genesis file, then imports the
    provided block.
    """
    try:
        params = _load_chain_params_for_import(genesis_path)
        importer = _get_importer(block_db, tx_index, params)
        result = importer.import_block(raw_block)
        accepted = result.code in (
            ImportErrorCode.ACCEPTED,
            ImportErrorCode.DUPLICATE,
        )
        return accepted, result.reason or result.code
    except Exception as e:
        return False, str(e)


def fork_choice_snapshot(
    block_db,
    tx_index=None,
    *,
    genesis_path: Optional[str] = None,
    limit: int = 5,
) -> Dict[str, Any]:
    try:
        params = _load_chain_params_for_import(genesis_path)
        importer = _get_importer(block_db, tx_index, params)
        return {
            "tips": importer.fork_tips(limit=limit),
        }
    except Exception as e:
        return {"tips": [], "error": str(e)}


# Convenience: tiny CLI for manual testing
if __name__ == "__main__":  # pragma: no cover
    import argparse

    from core.config import load_config
    from core.db.block_db import BlockDB
    from core.db.sqlite import SQLiteKV
    from core.genesis.loader import load_genesis

    ap = argparse.ArgumentParser(
        description="Import a CBOR-encoded block into the local DB"
    )
    ap.add_argument("--db", default="sqlite:///animica.db")
    ap.add_argument("--genesis", default=None)
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
