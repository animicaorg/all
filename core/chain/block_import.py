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
from core.types.header import Header, serialize_header
from core.types.params import ChainParams
from core.types.receipt import \
    Receipt  # imported for type completeness; not used here
from core.types.tx import PqSignature, Tx, TxKind, UnsignedTx
from core.utils.tx import normalize_tx_envelope
from core.utils.hash import sha3_256
from core.utils.pow import micro_threshold_to_target256
from execution.runtime.env import make_block_env
from execution.runtime.executor import apply_block
from execution.state.apply_balance import (
    assert_block_apply_deltas,
    begin_apply_block,
    credit as state_credit,
    end_apply_block,
)

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
_POW_LOG_MIN_S = float(os.getenv("ANIMICA_POW_LOG_MIN_S", "5.0") or 5.0)
_POW_LOG_AT: dict[str, float] = {}
_BLOCK_COINBASE_CREDIT_TOTAL = 0

# Fork choice reorg depth limit
# This prevents excessive chain switching by limiting how deep a reorganization can be.
# The default of 96 blocks matches the P2P sync configuration (p2p/sync/__init__.py)
# and provides a good balance between allowing legitimate short reorgs and preventing
# malicious or accidental deep chain switches that destabilize the network.
DEFAULT_MAX_REORG_DEPTH = 96

# Snapshot creation interval
# Create on-disk snapshots every N blocks for fast sync
# Can be overridden via ANIMICA_SNAPSHOT_INTERVAL environment variable
DEFAULT_SNAPSHOT_INTERVAL = int(os.getenv("ANIMICA_SNAPSHOT_INTERVAL", "2000"))

# Enable/disable automatic snapshot creation
SNAPSHOT_AUTO_CREATE = os.getenv("ANIMICA_SNAPSHOT_AUTO_CREATE", "true").lower() in ("true", "1", "yes", "on")


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


def _is_instant_block(header: Header, payload: Optional[Dict[str, Any]] = None) -> bool:
    """
    Check if a block is an instant block by examining the extra field.
    Instant blocks are marked with {instant_block: true} in the extra CBOR data.
    
    Returns:
        True if the block is an instant block, False otherwise.
    """
    extra = None
    if hasattr(header, "extra"):
        extra = getattr(header, "extra")
    elif payload and "extra" in payload:
        extra = payload["extra"]
    
    if not extra:
        return False
    
    # Try to decode the extra field as CBOR
    try:
        import cbor2
        extra_dict = cbor2.loads(extra)
        if isinstance(extra_dict, dict):
            return bool(extra_dict.get("instant_block", False))
    except Exception:
        pass
    
    return False


def compute_header_hash(header: Header) -> bytes:
    """
    Canonical header hash. Prefer header.hash() to match BlockDB storage.
    """
    try:
        return sha3_256(serialize_header(header))
    except Exception:
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


def _decode_address_bytes(addr: Any) -> bytes:
    if isinstance(addr, (bytes, bytearray)):
        return bytes(addr)
    if isinstance(addr, str):
        if addr.startswith("anim1"):
            try:
                from pq.py.address import decode_address  # type: ignore

                record = decode_address(addr)
                digest = bytes(record.digest) if isinstance(record.digest, list) else record.digest
                return digest[:32].ljust(32, b"\x00")
            except Exception:
                pass
        hex_str = addr[2:] if addr.startswith("0x") else addr
        try:
            raw = bytes.fromhex(hex_str)
            return raw[:32].ljust(32, b"\x00")
        except Exception:
            return sha3_256(addr.encode("utf-8"))
    raise BlockImportError("unsupported address type in tx")


def _normalize_tx_envelope(decoded: Dict[str, Any]) -> Dict[str, Any]:
    if "unsigned" in decoded:
        unsigned = decoded["unsigned"]
        if isinstance(unsigned, UnsignedTx):
            tx_body = unsigned.to_obj()
        elif isinstance(unsigned, dict):
            tx_body = unsigned
        else:
            raise BlockImportError("invalid tx format: unsupported unsigned tx payload")
        sigs = decoded.get("sigs")
        if sigs is None and "sig" in decoded:
            sigs = [decoded.get("sig")]
        return normalize_tx_envelope({"tx": tx_body, "sigs": sigs or []})

    return normalize_tx_envelope(decoded)


def _tx_unsigned(tx: Any) -> Any:
    if hasattr(tx, "unsigned"):
        return getattr(tx, "unsigned")
    if isinstance(tx, dict):
        return tx.get("unsigned")
    return None




def _is_zero_address(addr: Any) -> bool:
    try:
        if isinstance(addr, (bytes, bytearray)):
            return bytes(addr) == b"\x00" * 32
        if isinstance(addr, str):
            v = addr.lower().strip()
            if v.startswith("0x"):
                v = v[2:]
            return v in {"0" * 64, "0" * 40}
    except Exception:
        return False
    return False


def _validate_coinbase_outputs_nonzero(block: Block) -> None:
    """Consensus rule: no coinbase output may target zero address."""
    for tx in getattr(block, "txs", ()):
        unsigned = getattr(tx, "unsigned", None)
        if unsigned is None:
            continue
        kind = getattr(unsigned, "kind", None)
        if int(kind) != 3:
            continue
        payload = getattr(unsigned, "payload", None)
        to_addr = getattr(payload, "to", None) if payload is not None else None
        if to_addr is not None and _is_zero_address(to_addr):
            raise BlockImportError("CORE/BLOCK_IMPORT", "invalid coinbase: zero-address output is forbidden")
def _is_coinbase_tx(tx: Any) -> bool:
    unsigned = _tx_unsigned(tx)
    if unsigned is None:
        return False
    kind = getattr(unsigned, "kind", None)
    if kind is None and isinstance(unsigned, dict):
        kind = unsigned.get("kind")
    try:
        return int(kind) == int(TxKind.COINBASE)
    except Exception:
        return False


def _compute_block_fees_total(block: Block) -> int:
    """
    Compute total explicit transaction fees in a block.

    Coinbase transactions are excluded by definition. Fees in this codebase are
    transferred during tx execution (tip to coinbase / base to treasury or burn),
    so this helper intentionally returns only the explicit fee fields to keep
    deterministic accounting.
    """
    total = 0
    for tx in block.txs:
        if _is_coinbase_tx(tx):
            continue
        unsigned = _tx_unsigned(tx)
        if unsigned is None:
            continue
        fee = getattr(unsigned, "fee", None)
        if fee is None and isinstance(unsigned, dict):
            fee = unsigned.get("fee")
        if fee is None:
            continue
        try:
            fee_i = int(fee)
        except Exception:
            continue
        if fee_i > 0:
            total += fee_i
    return int(total)


def _compute_block_reward_amount(
    *,
    chain_id: int,
    height: int,
    params: Mapping[str, Any],
    header: Header,
) -> int:
    if height <= 0:
        return 0
    try:
        from consensus.rewards import compute_block_reward

        canonical_height = None
        if not _is_instant_block(header):
            canonical_height = max(1, height)
        rewards = compute_block_reward(
            chain_id=chain_id,
            height=height,
            params=params,
            instant_block=_is_instant_block(header),
            canonical_height=canonical_height,
        )
    except Exception:
        return 0
    if not rewards:
        return 0
    try:
        return max(0, int(rewards[0][1]))
    except Exception:
        return 0


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
            normalized = _normalize_tx_envelope(t)
            try:
                txs.append(Tx.from_obj(normalized))
            except Exception:
                if "unsigned" in t:
                    txs.append(_dataclass_from_dict(Tx, t))
                else:
                    raise BlockImportError("invalid tx format: missing unsigned tx payload")
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
        "full_params_dict",
        "block_db",
        "tx_index",
        "state_db",
        "fork_choice",
        "difficulty_state",
        "_last_block_time",
        "_difficulty_samples",
        "_difficulty_anchor_hash",
        "_timestamp_window",
        "_window_size",
        "_difficulty_epoch_start_height",
        "_difficulty_epoch_theta",
        "_orphan_pool",
        "_orphan_parents",
        "_max_orphans",
        "_max_future_seconds",
        "_min_block_spacing_ms",
        "_state_snapshots",
        "_state_snapshot_limit",
        "_max_reorg_depth",
        "_snapshot_interval",
        "_snapshot_auto_create",
        "_created_snapshots",
        "_pending_snapshots",
        "_data_dir",
    )

    def __init__(
        self,
        *,
        params: ChainParams,
        block_db,
        state_db=None,
        tx_index=None,
        fork_choice: Optional[Any] = None,
        full_params_dict: Optional[Dict[str, Any]] = None,
        max_reorg_depth: Optional[int] = None,
        data_dir: Optional[str] = None,
    ):
        self.params = params
        self.block_db = block_db
        self.tx_index = tx_index
        self.state_db = state_db
        self.fork_choice = fork_choice
        # Store full params dict for reward calculation (includes monetary.issuance)
        # If not provided, try to load from spec/params.yaml
        self.full_params_dict = full_params_dict
        if self.full_params_dict is None:
            self.full_params_dict = _load_full_params_dict(params.chain_id)

        # Data directory for snapshots and other persistent data
        # If not provided, uses ANIMICA_DATA_DIR or default (~/.animica)
        self._data_dir = data_dir

        # Fork choice reorg depth limit (prevents excessive chain switching)
        # Default to DEFAULT_MAX_REORG_DEPTH or allow override via environment
        self._max_reorg_depth = max_reorg_depth
        if self._max_reorg_depth is None:
            env_val = os.getenv("ANIMICA_MAX_REORG_DEPTH")
            if env_val is not None:
                try:
                    self._max_reorg_depth = int(env_val)
                except ValueError:
                    log.warning(
                        f"Invalid ANIMICA_MAX_REORG_DEPTH value: '{env_val}', using default {DEFAULT_MAX_REORG_DEPTH}"
                    )
                    self._max_reorg_depth = DEFAULT_MAX_REORG_DEPTH
            else:
                self._max_reorg_depth = DEFAULT_MAX_REORG_DEPTH

        # Validate max_reorg_depth is non-negative (handles direct constructor param)
        if self._max_reorg_depth is not None and self._max_reorg_depth < 0:
            log.warning(
                f"max_reorg_depth must be non-negative, got {self._max_reorg_depth}, using default {DEFAULT_MAX_REORG_DEPTH}"
            )
            self._max_reorg_depth = DEFAULT_MAX_REORG_DEPTH

        # Initialize difficulty adjustment state
        self.difficulty_state = None
        self._last_block_time: Optional[int] = None
        self._difficulty_samples = 0
        # Canonical head hash the current difficulty_state is anchored to.
        self._difficulty_anchor_hash: Optional[bytes] = None
        # Window of recent timestamps for anti-gaming difficulty adjustment
        # Use retarget window size from params, defaulting to 10 blocks minimum
        self._window_size = max(10, int(getattr(params.retarget, 'window', 10)))
        self._timestamp_window: Deque[int] = deque(maxlen=self._window_size)
        # Track the difficulty epoch for static difficulty windows
        self._difficulty_epoch_start_height: int = 0
        self._difficulty_epoch_theta: Optional[int] = None
        if DIFFICULTY_AVAILABLE:
            self._init_difficulty_state()
        self._orphan_pool: "OrderedDict[bytes, _OrphanBlock]" = OrderedDict()
        self._orphan_parents: Dict[bytes, Deque[bytes]] = {}
        self._max_orphans = int(os.getenv("ANIMICA_ORPHAN_POOL_MAX", "1000"))
        self._max_future_seconds = int(os.getenv("ANIMICA_MAX_FUTURE_SECONDS", "5"))
        
        # Read min_block_spacing_ms from params if available, with env var override
        default_spacing = 0
        if self.full_params_dict:
            try:
                # Try to read from network-specific params
                network_key = f"animica:{params.chain_id}"
                if "networks" in self.full_params_dict and network_key in self.full_params_dict["networks"]:
                    network_params = self.full_params_dict["networks"][network_key]
                    if "monetary" in network_params and "issuance" in network_params["monetary"]:
                        default_spacing = int(network_params["monetary"]["issuance"].get("min_block_spacing_ms", 0))
                # Fall back to defaults if not in network-specific config
                if default_spacing == 0 and "defaults" in self.full_params_dict:
                    defaults = self.full_params_dict["defaults"]
                    if "issuance" in defaults:
                        default_spacing = int(defaults["issuance"].get("min_block_spacing_ms", 0))
            except (KeyError, ValueError, TypeError) as e:
                log.warning(f"Failed to read min_block_spacing_ms from params: {e}, using default 0")
                default_spacing = 0
        
        # Environment variable can override config file
        self._min_block_spacing_ms = int(os.getenv("ANIMICA_MIN_BLOCK_SPACING_MS", str(default_spacing)))
        
        # Validate min_block_spacing_ms is non-negative
        if self._min_block_spacing_ms < 0:
            log.warning(
                f"min_block_spacing_ms must be non-negative, got {self._min_block_spacing_ms}, using 0"
            )
            self._min_block_spacing_ms = 0
        
        if self._min_block_spacing_ms > 0:
            log.info(f"Minimum block spacing enforced: {self._min_block_spacing_ms} ms ({self._min_block_spacing_ms / 1000:.1f} seconds)")
        
        self._state_snapshots: Dict[int, Any] = {}
        self._state_snapshot_limit = int(
            os.getenv("ANIMICA_STATE_SNAPSHOT_CACHE", "2048") or 2048
        )
        
        # Disk snapshot tracking for fast sync
        self._snapshot_interval = DEFAULT_SNAPSHOT_INTERVAL
        self._snapshot_auto_create = SNAPSHOT_AUTO_CREATE
        self._created_snapshots: set[int] = set()  # Track which snapshots we've created
        self._pending_snapshots: set[int] = set()  # Track snapshots in progress
        
        self._init_fork_choice_from_db()

    # --- Basics -------------------------------------------------------------

    def head(self) -> Optional[Tuple[int, bytes]]:
        return self.block_db.get_canonical_head()

    # --- Difficulty adjustment ----------------------------------------------

    def _build_retarget_params(self) -> Optional["diff.RetargetParams"]:
        if not DIFFICULTY_AVAILABLE or diff is None:
            return None
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

            max_change_nats = math.log(self.params.retarget.bounds.max)
            step_clamp_micro = int(max_change_nats * 1_000_000 / max(1, half_life_blocks))
            step_clamp_micro = max(100_000, min(1_000_000, step_clamp_micro))

            # Read max_block_time_s from params if available
            max_block_time_s = None
            try:
                if self.full_params_dict:
                    network_key = f"animica:{self.params.chain_id}"
                    network_params = self.full_params_dict.get("networks", {}).get(network_key, {})
                    if network_params:
                        max_block_time_s = network_params.get("monetary", {}).get("issuance", {}).get("max_block_time_s")
                    if max_block_time_s is None:
                        defaults = self.full_params_dict.get("defaults", {})
                        max_block_time_s = defaults.get("issuance", {}).get("max_block_time_s")
            except Exception as e:
                log.warning(f"Failed to read max_block_time_s from params: {e}")

            return diff.RetargetParams(
                target_block_time_s=float(self.params.block.target_seconds),
                half_life_blocks=half_life_blocks,
                gain_beta=gain_beta,
                step_clamp_micro=step_clamp_micro,
                theta_min_micro=int(self.params.theta_min),
                theta_max_micro=int(self.params.theta_max),
                max_block_time_s=float(max_block_time_s) if max_block_time_s is not None else None,
            )
        except Exception as e:  # pragma: no cover
            import logging

            logging.warning(f"Failed to build retarget params: {e}")
            return None

    def _init_difficulty_state(self) -> None:
        """
        Initialize difficulty state from params. Called once at startup.
        Maps ChainParams retarget config to consensus.difficulty RetargetParams.
        """
        if not DIFFICULTY_AVAILABLE or diff is None:
            return

        try:
            retarget_params = self._build_retarget_params()
            if retarget_params is None:
                self.difficulty_state = None
                self._difficulty_samples = 0
                return

            # Initialize state with genesis theta
            theta_init = int(self.params.theta_initial)
            self.difficulty_state = diff.init_state(retarget_params, theta_init_micro=theta_init)
            self._difficulty_samples = 0
            
            # Initialize epoch tracking metadata (kept for diagnostics).
            self._difficulty_epoch_start_height = 0
            self._difficulty_epoch_theta = theta_init

        except Exception as e:  # pragma: no cover
            # If difficulty module is unavailable or initialization fails, log and continue
            # The node can still import blocks without difficulty adjustment
            import logging
            logging.warning(f"Failed to initialize difficulty state: {e}")
            self.difficulty_state = None
            self._difficulty_samples = 0

    def _update_difficulty(
        self,
        block_timestamp: int,
        block_height: int,
        block_hash: bytes | None = None,
    ) -> None:
        """
        Update difficulty state for every accepted canonical block.

        The state tracks the expected theta for the *next* canonical block.
        Once a block at `block_height` is accepted, we use its timestamp delta
        against the previous canonical block timestamp to evolve theta.

        Args:
            block_timestamp: Unix timestamp (seconds) of the current block
            block_height: Height of the current block
            block_hash: Canonical hash of the current block
        """
        if not DIFFICULTY_AVAILABLE or diff is None or self.difficulty_state is None:
            return

        try:
            # Track recent timestamps for observability/debugging.
            self._timestamp_window.append(block_timestamp)

            # First observed canonical block after init/reanchor: establish the
            # timestamp anchor; the next block will produce the first dt sample.
            if self._last_block_time is None:
                self._last_block_time = block_timestamp
                if block_hash is not None:
                    self._difficulty_anchor_hash = bytes(block_hash)
                return

            dt_seconds = int(block_timestamp) - int(self._last_block_time)
            if dt_seconds <= 0:
                self._last_block_time = block_timestamp
                if block_hash is not None:
                    self._difficulty_anchor_hash = bytes(block_hash)
                return

            # Update theta for the next block.
            self.difficulty_state = diff.update_theta(
                self.difficulty_state,
                dt_seconds=float(dt_seconds),
                blocks_skipped=1,
            )
            self._difficulty_samples += 1
            self._difficulty_epoch_theta = int(self.difficulty_state.theta_micro)
            self._difficulty_epoch_start_height = block_height + 1
            self._last_block_time = block_timestamp
            if block_hash is not None:
                self._difficulty_anchor_hash = bytes(block_hash)

            log.info(
                "Difficulty update at canonical height %d: theta=%.6f nats (dt=%.2fs, target=%.2fs)",
                block_height,
                self._difficulty_epoch_theta / 1e6,
                float(dt_seconds),
                float(self.difficulty_state.params.target_block_time_s),
            )

        except Exception as e:  # pragma: no cover
            import logging
            logging.warning(f"Failed to update difficulty: {e}")

    def _reanchor_difficulty_state(
        self,
        parent_header: Header,
        *,
        parent_hash: bytes | None = None,
    ) -> None:
        if not DIFFICULTY_AVAILABLE or diff is None:
            return
        parent_ts = _timestamp_of(parent_header)
        if parent_ts is None:
            return
        parent_theta = _weight_micro_of(parent_header, None, self.params)
        if parent_hash is None:
            try:
                parent_hash = compute_header_hash(parent_header)
            except Exception:
                parent_hash = None
        needs_reset = (
            self._last_block_time is None
            or int(self._last_block_time) != int(parent_ts)
            or self.difficulty_state is None
            or (
                parent_hash is not None
                and self._difficulty_anchor_hash != bytes(parent_hash)
            )
        )
        if not needs_reset:
            return
        retarget_params = (
            self.difficulty_state.params
            if self.difficulty_state is not None
            else self._build_retarget_params()
        )
        if retarget_params is None:
            self._last_block_time = int(parent_ts)
            return
        try:
            self.difficulty_state = diff.init_state(
                retarget_params, theta_init_micro=int(parent_theta)
            )
            self._last_block_time = int(parent_ts)
            self._difficulty_samples = 0
            self._difficulty_anchor_hash = bytes(parent_hash) if parent_hash is not None else None
            # Clear the timestamp window when reanchoring to start fresh
            self._timestamp_window.clear()
            # Add the parent timestamp as the first entry
            self._timestamp_window.append(int(parent_ts))
            # Reset epoch tracking
            parent_height = _height_of(parent_header)
            self._difficulty_epoch_start_height = parent_height
            self._difficulty_epoch_theta = int(parent_theta)
        except Exception as e:  # pragma: no cover
            import logging

            logging.warning(f"Failed to reanchor difficulty state: {e}")
            self._difficulty_samples = 0

    def sync_difficulty_to_canonical_head(self) -> None:
        """
        Ensure difficulty tracking is anchored to the current canonical head.

        This keeps theta resolution deterministic even when canonical blocks are
        committed through alternative paths that bypass BlockImporter internals.
        """
        if not DIFFICULTY_AVAILABLE or diff is None:
            return
        if self.difficulty_state is None:
            return
        head = self.block_db.get_canonical_head()
        if head is None:
            return
        head_hash = bytes(head[1])
        if self._difficulty_anchor_hash == head_hash and self._last_block_time is not None:
            return
        header = self.block_db.get_header_by_hash(head_hash)
        if header is None:
            return
        self._reanchor_difficulty_state(header, parent_hash=head_hash)

    def get_current_difficulty(self) -> int:
        """
        Get the current difficulty threshold (Θ) in micro-nats.
        
        Returns:
            Current theta_micro value, or genesis theta_initial if difficulty state not available.
        """
        if self.difficulty_state is not None:
            self.sync_difficulty_to_canonical_head()
            return int(self.difficulty_state.theta_micro)
        return int(self.params.theta_initial)

    def _expected_theta_for_timestamp(self, block_timestamp: int) -> Optional[int]:
        """
        Return the expected Θ for the next canonical block.

        Theta is updated per canonical block acceptance and anchored to the
        canonical head. `block_timestamp` is accepted for compatibility with
        older callers but not currently required by the stateful retarget path.

        Returns None if difficulty state is unavailable.
        """
        if not DIFFICULTY_AVAILABLE or diff is None or self.difficulty_state is None:
            return None
        
        self.sync_difficulty_to_canonical_head()
        return int(self.difficulty_state.theta_micro)

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

            tx_hashes = self._canonical_tx_hashes(block)
            duplicate_hash = self._first_duplicate_hash(tx_hashes)
            if duplicate_hash is not None:
                log.warning(
                    "duplicate tx hash detected inside block",
                    extra={
                        "height": height,
                        "block_hash": h.hex(),
                        "tx_hash": duplicate_hash.hex(),
                        "caller": "BlockImporter.import_block",
                    },
                )
                return ImportResult(
                    ImportErrorCode.INVALID,
                    height,
                    h,
                    False,
                    f"duplicate tx hash in block: 0x{duplicate_hash.hex()}",
                )

            if self.tx_index is not None:
                for tx_hash in tx_hashes:
                    try:
                        already_applied = bool(self.tx_index.exists(tx_hash))
                    except Exception:
                        already_applied = False
                    if already_applied:
                        log.warning(
                            "duplicate tx apply attempt rejected",
                            extra={
                                "height": height,
                                "block_hash": h.hex(),
                                "tx_hash": tx_hash.hex(),
                                "caller": "BlockImporter.import_block",
                            },
                        )
                        return ImportResult(
                            ImportErrorCode.INVALID,
                            height,
                            h,
                            False,
                            f"tx already applied on canonical chain: 0x{tx_hash.hex()}",
                        )

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
                
                # Initialize canonical height (genesis is always canonical height 0)
                self.block_db.set_canonical_height(0)
                
                self._init_fork_choice(genesis_hash=h, header=header, payload=hdr_map)
                
                # Initialize difficulty tracking with genesis timestamp
                timestamp = _timestamp_of(header, hdr_map)
                if timestamp is not None:
                    self._last_block_time = timestamp
                self._difficulty_anchor_hash = h
                
                # Index canonical txs if any
                self._index_block_if_canonical(height=0, block_hash=h, block=block)
                self._capture_state_snapshot(0)

                # Notify all miners that genesis block was accepted
                try:
                    from mining.orchestrator import notify_all_template_feeders_block_found
                    notify_all_template_feeders_block_found()
                except Exception as e:
                    # Best effort notification, don't fail block import
                    import logging
                    logging.getLogger("core.chain.block_import").debug(
                        "Failed to notify template feeders: %s", e, exc_info=True
                    )

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

            theta_error = self._theta_sanity(header, parent_hash, hdr_map)
            if theta_error is not None:
                return ImportResult(ImportErrorCode.INVALID, height, h, False, theta_error)

            # Basic header sanity
            self._sanity_header(header)

            # Consensus rule: coinbase outputs may never target zero address.
            _validate_coinbase_outputs_nonzero(block)

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

            # Notify all miners that a block was found so they can move to next block
            try:
                from mining.orchestrator import notify_all_template_feeders_block_found
                notify_all_template_feeders_block_found()
            except Exception as e:
                # Best effort notification, don't fail block import
                # Use a local logger since we don't have one in scope
                import logging
                logging.getLogger("core.chain.block_import").debug(
                    "Failed to notify template feeders: %s", e, exc_info=True
                )

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
        # Normal block PoW validation
        try:
            theta_micro = _weight_micro_of(header, payload, self.params)
            target = _theta_to_target(int(theta_micro))
            pow_hash_int = int.from_bytes(header_hash, "big")
            if pow_hash_int > target:
                height = None
                parent_hash = None
                try:
                    height = _height_of(header, payload)
                except Exception:
                    height = None
                try:
                    parent_hash = _parent_hash_of(header, payload).hex()
                except Exception:
                    parent_hash = None
                claimed_bits = None
                for key in ("bits", "target", "targetBits", "target_bits"):
                    if key in payload:
                        claimed_bits = payload.get(key)
                        break
                adaptive_pow = None
                if height is not None:
                    adaptive_pow = int(self.params.chain_id) == 1 and int(height) >= 1
                log_key = header_hash.hex()
                now = time.time()
                last = _POW_LOG_AT.get(log_key, 0.0)
                if now - last >= _POW_LOG_MIN_S:
                    _POW_LOG_AT[log_key] = now
                    log.warning(
                        "PoW target mismatch",
                        extra={
                            "block_hash": header_hash.hex(),
                            "header_hash": header_hash.hex(),
                            "height": height,
                            "prev_hash": parent_hash,
                            "claimed_bits": claimed_bits,
                            "claimed_theta_micro": int(theta_micro),
                            "computed_target": target,
                            "computed_target_hex": hex(int(target)),
                            "computed_work_hash": header_hash.hex(),
                            "pow_hash_int": pow_hash_int,
                            "pow_rule": "header_hash<=target",
                            "hash_endianness": "big",
                            "adaptive_pow": adaptive_pow,
                            "chain_id": int(self.params.chain_id),
                            "chain_name": self.params.chain_name,
                            "genesis_hash": self.params.genesis_hash.hex(),
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
            max_reorg_depth=self._max_reorg_depth,
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
            max_reorg_depth=self._max_reorg_depth,
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
                if parent_header is not None:
                    self._reanchor_difficulty_state(
                        parent_header,
                        parent_hash=_parent_hash_of(first_header),
                    )

        # Remove canonical indices for detached blocks
        if self.tx_index is not None:
            for h in detached_list:
                header = self.block_db.get_header_by_hash(h)
                if header is None:
                    continue
                height = _height_of(header)
                self._remove_block_index(height)

        # Apply new canonical blocks and track canonical height
        canonical_height = self.block_db.get_canonical_height()
        if canonical_height is None:
            canonical_height = 0

        # Remove canonical height contributions for detached non-instant blocks
        for h in detached_list:
            header = self.block_db.get_header_by_hash(h)
            if header is None:
                continue
            if not _is_instant_block(header):
                canonical_height = max(0, canonical_height - 1)
        self.block_db.set_canonical_height(canonical_height)
        
        for h in attached_list:
            header = self.block_db.get_header_by_hash(h)
            if header is None:
                continue
            height = _height_of(header)
            
            if hasattr(self.block_db, "set_canonical"):
                self.block_db.set_canonical(height, h, allow_overwrite=True)
            if self.tx_index is not None:
                block = self.block_db.get_block_by_hash(h)
                if block is not None:
                    self._index_block_if_canonical(height=height, block_hash=h, block=block)

            # Update canonical height only for non-instant blocks (mining blocks)
            # Instant blocks are created by tx.sendRawTransaction with instant_block=True
            # and should not count towards the halving schedule
            if not _is_instant_block(header):
                canonical_height += 1
                self.block_db.set_canonical_height(canonical_height)
                
                # Check if we should create a disk snapshot at this height
                if self._should_create_disk_snapshot(height):
                    self._create_disk_snapshot(height)
            
            ts = _timestamp_of(header)
            if ts is not None:
                self._update_difficulty(ts, height, h)

        if old_height is not None and best.height < old_height:
            self._delete_canonical_range(best.height + 1, old_height)

        if hasattr(self.block_db, "set_head"):
            self.block_db.set_head(best.height, best.h, allow_reorg=True)
        else:
            self.block_db.set_canonical_head(best.height, best.h, allow_overwrite=True, allow_reorg=True)

        if self.state_db is not None:
            if old_height is not None and old_height not in self._state_snapshots:
                self._capture_state_snapshot(old_height)
            self._apply_state_reorg(detached_list, attached_list, best)
        
        # Auto-confirm local mempool transactions when canonical height increases
        # This ensures transactions in local mempools get confirmed without needing
        # to propagate to miners' nodes
        if attached_list:
            self._confirm_mempool_transactions(attached_list)
        
        # Check for and create missing snapshots (run periodically, not on every block)
        # Only check every 100 blocks to avoid overhead
        if best.height % 100 == 0:
            self._check_and_create_missing_snapshots(best.height)

    def _confirm_mempool_transactions(self, attached_blocks: list[bytes]) -> None:
        """
        Automatically confirm local mempool transactions when blocks are attached.
        
        This ensures that when a block increases the canonical height, transactions
        in the local mempool are confirmed/evicted without needing to propagate to
        miners' nodes.
        
        Args:
            attached_blocks: List of block hashes (bytes) that were attached to the canonical chain
        """
        if not attached_blocks:
            return
        
        # Validate input types
        if not all(isinstance(h, (bytes, bytearray)) for h in attached_blocks):
            log.warning(
                "Invalid block hash type in attached_blocks",
                extra={"expected": "bytes", "got": [type(h).__name__ for h in attached_blocks]},
            )
            return
        
        try:
            # Try to get mempool service from context (may not be available in all environments)
            from rpc import deps
            ctx = deps.get_ctx()
            mempool_service = getattr(ctx, "mempool", None)
        except Exception:
            # Context not available (e.g., during testing or minimal node setup)
            # This is expected and safe to ignore
            mempool_service = None
        
        if mempool_service is None:
            # No mempool service available, nothing to do
            return
        
        # Extract transaction hashes from all attached blocks
        # Cache blocks and their transaction hashes to avoid redundant database lookups
        blocks_cache: dict[bytes, Block] = {}
        block_tx_hashes: dict[bytes, list[str]] = {}  # Map block_hash -> tx_hashes in that block
        
        for block_hash in attached_blocks:
            try:
                # Fetch and cache block
                block = self.block_db.get_block_by_hash(block_hash)
                if block is None:
                    continue
                blocks_cache[block_hash] = block
                
                # Extract transaction hashes from this block
                tx_hashes: list[str] = []
                if hasattr(block, "txs") and block.txs:
                    for tx in block.txs:
                        # Try to get canonical transaction hash
                        tx_hash_hex = self._extract_tx_hash(tx)
                        if tx_hash_hex:
                            tx_hashes.append(tx_hash_hex)
                block_tx_hashes[block_hash] = tx_hashes
            except Exception as e:
                log.debug(
                    "Failed to extract transactions from block for mempool confirmation",
                    extra={"block_hash": block_hash.hex() if isinstance(block_hash, bytes) else str(block_hash), "error": str(e)},
                )
                continue
        
        # Collect all transaction hashes for bulk removal
        all_tx_hashes = []
        for hashes in block_tx_hashes.values():
            all_tx_hashes.extend(hashes)
        
        # Remove confirmed transactions from mempool
        if all_tx_hashes:
            try:
                removed = mempool_service.remove_included(all_tx_hashes)
                log.debug(
                    "Auto-confirmed local mempool transactions on height increase",
                    extra={"removed": removed, "tx_count": len(all_tx_hashes)},
                )
            except Exception as e:
                log.warning(
                    "Failed to remove confirmed transactions from mempool",
                    extra={"error": str(e), "tx_count": len(all_tx_hashes)},
                )
            try:
                from rpc.instant_tx import get_instant_tx_service_singleton

                instant_svc = get_instant_tx_service_singleton()
                if instant_svc is not None:
                    instant_svc.mark_finalized(all_tx_hashes)
            except Exception:
                log.debug("Failed to mark instant tx confirmations finalized", exc_info=True)
        
        # Trigger mempool reconciliation for conflict resolution
        # Use cached blocks and per-block tx hashes to avoid redundant lookups
        try:
            from mempool import on_block_accepted
            
            # Call on_block_accepted for each attached block with its specific tx hashes
            for block_hash in attached_blocks:
                block = blocks_cache.get(block_hash)
                if block is not None:
                    # Get transaction hashes for this specific block
                    tx_hashes = block_tx_hashes.get(block_hash, [])
                    reconcile_result = on_block_accepted(
                        block, 
                        self.state_db,
                        tx_hashes=tx_hashes
                    )
                    if reconcile_result and (reconcile_result.get("evicted", 0) > 0 or reconcile_result.get("conflicts", 0) > 0):
                        log.debug(
                            "Reconciled mempool after block attachment",
                            extra=reconcile_result,
                        )
        except Exception as e:
            log.debug(
                "Failed to reconcile mempool after block attachment",
                extra={"error": str(e)},
            )
    
    def _extract_tx_hash(self, tx: Union[Tx, Dict[str, Any]]) -> Optional[str]:
        """
        Extract canonical transaction hash from a transaction object.
        
        Args:
            tx: Transaction object (can be Tx, dict, or bytes)
            
        Returns:
            Transaction hash as hex string with 0x prefix, or None if extraction fails
        """
        try:
            # Try raw CBOR method first (most canonical)
            raw = getattr(tx, "raw_cbor", None)
            if raw is None and hasattr(tx, "to_cbor") and callable(getattr(tx, "to_cbor")):
                try:
                    raw = tx.to_cbor()
                except Exception:
                    raw = None
            
            if raw:
                from mempool.tx_hash import tx_hash_hex as _tx_hash_hex
                return _tx_hash_hex(raw)
            
            # Try hash() method
            if hasattr(tx, "hash") and callable(getattr(tx, "hash")):
                try:
                    h = tx.hash()
                    if isinstance(h, bytes) and len(h) == 32:
                        return "0x" + h.hex()
                except Exception:
                    pass
            
            # Try hash attribute
            if hasattr(tx, "hash"):
                h = getattr(tx, "hash")
                if isinstance(h, bytes) and len(h) == 32:
                    return "0x" + h.hex()
                elif isinstance(h, str):
                    return h if h.startswith("0x") else f"0x{h}"
            
            # Try tx_hash attribute
            if hasattr(tx, "tx_hash"):
                h = getattr(tx, "tx_hash")
                if isinstance(h, bytes) and len(h) == 32:
                    return "0x" + h.hex()
                elif isinstance(h, str):
                    return h if h.startswith("0x") else f"0x{h}"
        except Exception as e:
            log.debug(
                "Failed to extract transaction hash",
                extra={"error": str(e), "tx_type": type(tx).__name__},
            )
        
        return None

    def _apply_state_reorg(
        self,
        detached: list[bytes],
        attached: list[bytes],
        best,
    ) -> None:
        if self.state_db is None:
            return
        if not detached and not attached:
            return

        lca_height = self._reorg_lca_height(detached, attached, best)
        snap = self._state_snapshots.get(lca_height)
        if snap is None:
            log.warning(
                "state: missing snapshot for reorg; rebuilding from canonical chain",
                extra={"lca_height": lca_height, "best_height": best.height},
            )
            self._rebuild_state_from_canonical(best.height)
            return

        try:
            self.state_db.revert(snap)
        except Exception as exc:
            log.error(
                "state: failed to revert snapshot; rebuilding",
                extra={"error": str(exc), "lca_height": lca_height},
            )
            self._rebuild_state_from_canonical(best.height)
            return

        applied = 0
        for h in sorted(attached, key=self._block_height_for_hash):
            block = self.block_db.get_block_by_hash(h)
            if block is None:
                continue
            if not self._apply_block_state(block):
                log.warning(
                    "state: block execution failed during reorg",
                    extra={"height": getattr(block.header, "height", None), "hash": h.hex()},
                )
                continue
            height = _height_of(block.header)
            self._capture_state_snapshot(height)
            applied += 1

        if applied:
            log.info(
                "state: reorg applied",
                extra={
                    "lca_height": lca_height,
                    "applied_blocks": applied,
                    "best_height": best.height,
                },
            )

    def _reorg_lca_height(
        self,
        detached: list[bytes],
        attached: list[bytes],
        best,
    ) -> int:
        if attached:
            heights = [self._block_height_for_hash(h) for h in attached]
            heights = [h for h in heights if h is not None]
            if heights:
                return max(0, min(heights) - 1)
        try:
            return int(best.height) if best is not None else 0
        except Exception:
            return 0

    def _block_height_for_hash(self, h: bytes) -> Optional[int]:
        header = self.block_db.get_header_by_hash(h)
        if header is None:
            return None
        try:
            return _height_of(header)
        except Exception:
            return None

    def _apply_block_state(self, block: Block) -> bool:
        if self.state_db is None:
            return False

        try:
            block_env = make_block_env(block.header, self.params)
            non_coinbase_txs = [tx for tx in block.txs if not _is_coinbase_tx(tx)]
            height = int(getattr(block.header, "height", 0) or 0)
            block_hash_hex = "0x" + block.header.hash().hex()
            begin_apply_block(height, block_hash_hex)

            block_result = apply_block(non_coinbase_txs, self.state_db, block_env, params=self.params)
            apply_events = end_apply_block()
            tx_expectations: list[dict[str, Any]] = []
            for i, tx in enumerate(non_coinbase_txs):
                tx_hash_hex = self._extract_tx_hash(tx)
                if tx_hash_hex is None:
                    continue

                unsigned = getattr(tx, "unsigned", tx)
                payload = getattr(unsigned, "payload", None)
                sender = getattr(unsigned, "sender", None)
                to = getattr(payload, "to", None) if payload is not None else None
                amount = int(getattr(payload, "amount", 0) or 0) if payload is not None else 0
                gas_price = int(getattr(unsigned, "gas_price", 0) or 0)

                tx_result = block_result.tx_results[i] if i < len(block_result.tx_results) else None
                status_obj = getattr(tx_result, "status", None) if tx_result is not None else None
                status_name = (
                    getattr(status_obj, "name", None)
                    or str(status_obj or "")
                ).upper()
                gas_used = int(getattr(tx_result, "gas_used", 0) or 0) if tx_result is not None else 0
                fee_charged = 0 if status_name == "OOG" else int(gas_used) * int(gas_price)

                transfer_applied = status_name == "SUCCESS"
                sender_hex = bytes(sender).hex() if isinstance(sender, (bytes, bytearray)) else ""
                to_hex = bytes(to).hex() if isinstance(to, (bytes, bytearray)) else ""
                value_component = (
                    int(amount)
                    if transfer_applied and sender_hex and to_hex and to_hex != sender_hex
                    else 0
                )
                sender_delta = -(value_component + int(fee_charged)) if sender_hex else 0
                recipient_delta = value_component if to_hex and to_hex != sender_hex else 0

                tx_expectations.append(
                    {
                        "tx_hash": tx_hash_hex,
                        "sender": sender_hex,
                        "recipient": to_hex,
                        "sender_delta": sender_delta,
                        "recipient_delta": recipient_delta,
                    }
                )
            assert_block_apply_deltas(tx_expectations=tx_expectations, events=apply_events)

            # Compute block rewards with AICF slicing
            height = int(getattr(block.header, "height", 0) or 0)
            chain_id = int(self.params.chain_id)
            
            # Get all reward outputs (miner, AICF, treasury)
            from consensus.rewards import compute_block_reward
            try:
                reward_outputs = compute_block_reward(
                    chain_id=chain_id,
                    height=height,
                    params=self.full_params_dict or {},
                    instant_block=_is_instant_block(block.header),
                    canonical_height=max(1, height) if not _is_instant_block(block.header) else None,
                )
            except Exception as e:
                log.warning(f"Failed to compute block reward: {e}")
                reward_outputs = []
            
            # Parse reward outputs
            miner_reward = 0
            aicf_reward = 0
            treasury_reward = 0
            
            if reward_outputs:
                # Get system addresses to identify reward types
                system_addresses = (self.full_params_dict or {}).get("system_addresses", {})
                aicf_addr = system_addresses.get("aicf_treasury", "anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
                treasury_addr = system_addresses.get("treasury", "anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
                
                for addr, amount in reward_outputs:
                    if addr == aicf_addr:
                        aicf_reward += amount
                    elif addr == treasury_addr:
                        treasury_reward += amount
                    else:
                        # Miner reward (default)
                        miner_reward += amount
            
            # Compute fee split for AICF
            fee_aicf_amount = 0
            try:
                from execution.runtime.aicf_integration import compute_fee_aicf_amount
                fee_aicf_amount = compute_fee_aicf_amount(getattr(block_result, 'tx_results', []))
            except Exception as e:
                log.debug(f"Failed to compute AICF fee amount: {e}")
            
            # Total fees (for miner - already includes AICF portion split)
            fee_amount = _compute_block_fees_total(block)
            miner_fee_amount = fee_amount - fee_aicf_amount
            
            # Credit miner with their portion (reward + fees after AICF slice)
            miner_total = int(miner_reward) + int(miner_fee_amount)
            if miner_total > 0:
                new_balance = state_credit(
                    self.state_db,
                    bytes(block_env.coinbase),
                    miner_total,
                    reason="BLOCK_APPLY_MINER_REWARD_FEES",
                    tx_hash=None,
                    height=height,
                    callsite="core.chain.block_import._apply_block_state",
                )
                global _BLOCK_COINBASE_CREDIT_TOTAL
                _BLOCK_COINBASE_CREDIT_TOTAL += miner_total
                log.info(
                    "STATE_CREDIT miner=%s reward=%d fees=%d total=%d height=%d new_balance=%d",
                    block_env.coinbase.hex(),
                    miner_reward,
                    miner_fee_amount,
                    miner_total,
                    height,
                    new_balance,
                )
            
            # Credit AICF pool with their portion (reward slice + fee slice)
            aicf_total = int(aicf_reward) + int(fee_aicf_amount)
            if aicf_total > 0:
                system_addresses = (self.full_params_dict or {}).get("system_addresses", {})
                aicf_pool_addr_str = system_addresses.get("aicf_treasury", "anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
                
                # Convert bech32 address to bytes
                try:
                    from pq.address import bech32_decode
                    _, aicf_pool_addr_bytes = bech32_decode(aicf_pool_addr_str)
                except Exception:
                    # Fallback: use a deterministic placeholder address
                    aicf_pool_addr_bytes = b"\x00" * 32
                
                aicf_balance = state_credit(
                    self.state_db,
                    aicf_pool_addr_bytes,
                    aicf_total,
                    reason="BLOCK_APPLY_AICF_POOL_INFLOW",
                    tx_hash=None,
                    height=height,
                    callsite="core.chain.block_import._apply_block_state",
                )
                log.info(
                    "STATE_CREDIT aicf_pool=%s reward=%d fees=%d total=%d height=%d new_balance=%d",
                    aicf_pool_addr_bytes.hex(),
                    aicf_reward,
                    fee_aicf_amount,
                    aicf_total,
                    height,
                    aicf_balance,
                )
            
            # Process AICF accounting (credits, epochs, etc.)
            try:
                from execution.runtime.aicf_integration import process_block_for_aicf
                process_block_for_aicf(
                    state=self.state_db,
                    block_env=block_env,
                    miner_address=bytes(block_env.coinbase),
                    block_reward_aicf_amount=int(aicf_reward),
                    fee_aicf_amount=int(fee_aicf_amount),
                    params=self.full_params_dict,
                )
                log.debug(
                    f"AICF: Processed block {height}, "
                    f"miner credits awarded, "
                    f"inflows tracked (reward={aicf_reward}, fees={fee_aicf_amount})"
                )
            except Exception as e:
                log.error(f"AICF: Failed to process block {height}: {e}", exc_info=True)
            
            # Log total coinbase credit metric
            total_credited = miner_total + aicf_total
            if total_credited > 0:
                log.info(
                    "metric block_coinbase_credit_total=%d",
                    _BLOCK_COINBASE_CREDIT_TOTAL,
                )

            return True
        except Exception as exc:
            try:
                end_apply_block()
            except Exception:
                pass
            tx_hashes_sample: list[str] = []
            for tx in list(getattr(block, "txs", ())[:10]):
                tx_hash = self._extract_tx_hash(tx)
                if tx_hash:
                    tx_hashes_sample.append(tx_hash)
            log.error(
                "state: block execution failed",
                extra={
                    "stage": "block_apply",
                    "reason": "invalid_state_transition",
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                    "height": getattr(block.header, "height", None),
                    "tx_hashes_sample": tx_hashes_sample,
                },
            )
            return False

    def _capture_state_snapshot(self, height: int) -> None:
        if self.state_db is None:
            return
        snap = getattr(self.state_db, "snapshot", None)
        if not callable(snap):
            return
        try:
            self._state_snapshots[int(height)] = snap()
            self._prune_state_snapshots()
        except Exception:
            return

    def _prune_state_snapshots(self) -> None:
        if self._state_snapshot_limit <= 0:
            return
        if len(self._state_snapshots) <= self._state_snapshot_limit:
            return
        for height in sorted(self._state_snapshots.keys())[
            : max(0, len(self._state_snapshots) - self._state_snapshot_limit)
        ]:
            self._state_snapshots.pop(height, None)

    def _should_create_disk_snapshot(self, height: int) -> bool:
        """
        Check if a disk snapshot should be created for the given height.
        
        Snapshots are created every SNAPSHOT_INTERVAL blocks (default 2000)
        to enable fast sync for new nodes.
        """
        if not self._snapshot_auto_create:
            return False
        if height <= 0:
            return False
        if self._snapshot_interval <= 0:
            return False
        if height % self._snapshot_interval != 0:
            return False
        # Don't create if already created or in progress
        if height in self._created_snapshots or height in self._pending_snapshots:
            return False
        return True
    
    def _get_snapshots_base_dir(self) -> "Path":
        """
        Get the base directory for snapshots.
        
        Returns the directory where snapshot subdirectories should be created.
        Handles chain-specific data directories by using the parent.
        """
        from pathlib import Path
        
        # Use data_dir if provided, otherwise use environment or default
        if self._data_dir:
            base_dir = Path(self._data_dir)
        else:
            base_dir = Path(os.environ.get("ANIMICA_DATA_DIR", "~/.animica")).expanduser()
        
        # If base_dir ends with chain-{id}, use parent for global snapshots
        if base_dir.name.startswith("chain-"):
            base_dir = base_dir.parent
        
        return base_dir

    def _create_disk_snapshot(self, height: int) -> None:
        """
        Create a disk snapshot at the given height asynchronously.
        
        This snapshot can be used by new nodes to fast sync without
        downloading all blocks from genesis.
        """
        if height in self._pending_snapshots or height in self._created_snapshots:
            return
        
        # Mark as pending to prevent duplicate creation
        self._pending_snapshots.add(height)
        
        try:
            # Lazy import to avoid circular dependencies
            from core.db.snapshot import export_snapshot
            from pathlib import Path
            import threading
            
            def create_snapshot_async():
                try:
                    # Get snapshot directory using helper method
                    chain_id = self.params.chain_id
                    base_dir = self._get_snapshots_base_dir()
                    
                    snapshots_dir = base_dir / "snapshots"
                    snapshots_dir.mkdir(parents=True, exist_ok=True)
                    
                    snapshot_dir = snapshots_dir / f"chain-{chain_id}-height-{height}"
                    
                    # Skip if snapshot already exists
                    if snapshot_dir.exists():
                        log.info(
                            f"Snapshot already exists at height {height}, skipping creation",
                            extra={"height": height, "path": str(snapshot_dir)}
                        )
                        self._created_snapshots.add(height)
                        self._pending_snapshots.discard(height)
                        return
                    
                    log.info(
                        f"Creating disk snapshot at height {height}",
                        extra={"height": height, "path": str(snapshot_dir)}
                    )
                    
                    start_time = time.time()
                    
                    # Create the snapshot
                    manifest = export_snapshot(
                        block_db=self.block_db,
                        state_db=self.state_db if self.state_db else None,
                        checkpoint_height=height,
                        output_dir=snapshot_dir,
                        compress=True,
                    )
                    
                    elapsed = time.time() - start_time
                    
                    log.info(
                        f"Snapshot created successfully at height {height}",
                        extra={
                            "height": height,
                            "blocks": manifest.blocks_count,
                            "accounts": manifest.accounts_count,
                            "elapsed_seconds": round(elapsed, 2),
                            "path": str(snapshot_dir),
                        }
                    )
                    
                    self._created_snapshots.add(height)
                    
                except Exception as e:
                    log.warning(
                        f"Failed to create snapshot at height {height}: {e}",
                        extra={"height": height, "error": str(e)},
                        exc_info=True
                    )
                finally:
                    self._pending_snapshots.discard(height)
            
            # Create snapshot in background thread to avoid blocking block import
            thread = threading.Thread(
                target=create_snapshot_async,
                name=f"snapshot-{height}",
                daemon=True
            )
            thread.start()
            
        except Exception as e:
            log.warning(
                f"Failed to initiate snapshot creation at height {height}: {e}",
                extra={"height": height, "error": str(e)}
            )
            self._pending_snapshots.discard(height)

    def _check_and_create_missing_snapshots(self, current_height: int) -> None:
        """
        Check for missing snapshots and create them if needed.
        
        When a node is past snapshot intervals (e.g., 2000, 4000, 6000),
        this ensures those snapshots exist for future sync operations.
        """
        if not self._snapshot_auto_create:
            return
        if self._snapshot_interval <= 0:
            return
        if current_height <= self._snapshot_interval:
            return
        
        # Find all snapshot heights we should have
        missing_heights = []
        for h in range(self._snapshot_interval, current_height, self._snapshot_interval):
            if h not in self._created_snapshots and h not in self._pending_snapshots:
                # Quick check: does snapshot already exist on disk?
                try:
                    from pathlib import Path
                    chain_id = self.params.chain_id
                    
                    # Use helper method for consistent directory resolution
                    base_dir = self._get_snapshots_base_dir()
                    snapshots_dir = base_dir / "snapshots"
                    snapshot_dir = snapshots_dir / f"chain-{chain_id}-height-{h}"
                    
                    if snapshot_dir.exists():
                        self._created_snapshots.add(h)
                    else:
                        missing_heights.append(h)
                except Exception:
                    missing_heights.append(h)
        
        if missing_heights:
            log.info(
                f"Found {len(missing_heights)} missing snapshots, will create in background",
                extra={
                    "missing_count": len(missing_heights),
                    "heights": missing_heights[:10],  # Log first 10
                    "current_height": current_height,
                }
            )
            
            # Create missing snapshots (oldest first, but limited to avoid overwhelming)
            for height in sorted(missing_heights)[:3]:  # Create max 3 at a time
                self._create_disk_snapshot(height)

    def _rebuild_state_from_canonical(self, target_height: int) -> None:
        if self.state_db is None:
            return
        genesis_snap = self._state_snapshots.get(0)
        if genesis_snap is not None:
            try:
                self.state_db.revert(genesis_snap)
            except Exception:
                return
        applied = 0
        for height in range(1, max(0, int(target_height)) + 1):
            try:
                h = self.block_db.get_canonical_hash(height)
            except Exception:
                h = None
            if not h:
                break
            block = self.block_db.get_block_by_hash(h)
            if block is None:
                break
            if not self._apply_block_state(block):
                break
            self._capture_state_snapshot(height)
            applied += 1
        log.info(
            "state: rebuilt from canonical chain",
            extra={"target_height": target_height, "applied": applied},
        )

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
        tx_hashes = self._canonical_tx_hashes(block)
        if not tx_hashes and block.txs:
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

    def _canonical_tx_hashes(self, block: Block) -> list[bytes]:
        tx_hashes: list[bytes] = []
        for tx in getattr(block, "txs", ()):
            try:
                tx_hashes.append(self._tx_hash(tx))
            except Exception:  # pragma: no cover
                return []
        return tx_hashes

    @staticmethod
    def _first_duplicate_hash(tx_hashes: list[bytes]) -> Optional[bytes]:
        seen: set[bytes] = set()
        for tx_hash in tx_hashes:
            if tx_hash in seen:
                return tx_hash
            seen.add(tx_hash)
        return None

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
            future_cap = now + self._max_future_seconds
            if parent_ts is not None and parent_ts > future_cap:
                future_cap = parent_ts
            if ts > future_cap:
                return "timestamp too far in future"
        if self._min_block_spacing_ms > 0 and parent_ts is not None:
            delta_ms = (ts - parent_ts) * 1000
            if delta_ms < self._min_block_spacing_ms:
                return "timestamp spacing too short"
        return None

    def _theta_sanity(
        self,
        header: Header,
        parent_hash: bytes,
        payload: Dict[str, Any],
    ) -> Optional[str]:
        head = self.block_db.get_canonical_head()
        if head is None or head[1] != parent_hash:
            return None
        parent_header = self.block_db.get_header_by_hash(parent_hash)
        if parent_header is not None:
            self._reanchor_difficulty_state(parent_header, parent_hash=parent_hash)
        ts = _timestamp_of(header, payload)
        if ts is None:
            return None
        expected_theta = self._expected_theta_for_timestamp(ts)
        if expected_theta is None:
            return None
        claimed_theta = _weight_micro_of(header, payload, self.params)
        if int(claimed_theta) != int(expected_theta):
            warmup_blocks = max(1, int(self.params.retarget.window))
            if self._difficulty_samples < warmup_blocks:
                log.warning(
                    "theta mismatch during difficulty warmup",
                    extra={
                        "claimed_theta_micro": int(claimed_theta),
                        "expected_theta_micro": int(expected_theta),
                        "samples": self._difficulty_samples,
                        "warmup_blocks": warmup_blocks,
                    },
                )
                return None
            return (
                "theta mismatch"
                f": got {int(claimed_theta)}, expected {int(expected_theta)}"
            )
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

# Network key prefix for params.yaml lookup (e.g., "animica:1" for mainnet)
# This matches the network key format in spec/params.yaml under the "networks" section:
#   networks:
#     "animica:1":    # mainnet
#     "animica:2":    # testnet
#     "animica:1337": # devnet
_NETWORK_KEY_PREFIX = "animica"


@lru_cache(maxsize=4)
def _load_full_params_dict(chain_id: int) -> Dict[str, Any]:
    """
    Load full params dict from spec/params.yaml for reward calculation.
    
    This includes the monetary.issuance configuration needed by compute_block_reward().
    Returns a network-specific dict with all parameters, or empty dict if file not found.
    
    Args:
        chain_id: Chain identifier (1=mainnet, 2=testnet, 1337=devnet, etc.)
        
    Returns:
        Dict with full network configuration including monetary.issuance
    """
    from pathlib import Path
    
    try:
        import yaml
    except ImportError:
        log.warning("PyYAML not available; rewards will not be calculated")
        return {}
    
    # Find spec/params.yaml relative to this file (core/chain/block_import.py)
    # Repository root is two levels up
    repo_root = Path(__file__).resolve().parents[2]
    params_path = repo_root / "spec" / "params.yaml"
    
    if not params_path.exists():
        log.warning(
            f"spec/params.yaml not found at {params_path}; rewards will not be calculated"
        )
        return {}
    
    try:
        with params_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        
        # Look for network-specific config under networks.<network_key>
        networks = raw.get("networks", {})
        network_key = f"{_NETWORK_KEY_PREFIX}:{chain_id}"
        
        if network_key in networks:
            network_config = dict(networks[network_key])
            # Ensure chain_id is set
            network_config["chain_id"] = chain_id
            network_config["chainId"] = chain_id
            return network_config
        
        log.warning(
            f"No network config found for chain_id={chain_id} (key={network_key}) "
            f"in {params_path}; rewards will not be calculated"
        )
        return {}
    except Exception as e:
        log.warning(
            f"Failed to load {params_path}: {e}; rewards will not be calculated"
        )
        return {}


@lru_cache(maxsize=4)
def _load_chain_params_for_import(genesis_path: Optional[str]) -> ChainParams:
    bundle = get_genesis(genesis_path)
    return load_chain_params_from_genesis(bundle.genesis, base_dir=bundle.base_dir)


def _get_importer(
    block_db,
    state_db,
    tx_index,
    params: ChainParams,
) -> BlockImporter:
    cached = _IMPORTER_CACHE.get(id(block_db))
    if cached is not None and cached.params.chain_id == params.chain_id:
        if cached.state_db is None and state_db is not None:
            cached.state_db = state_db
        return cached
    importer = BlockImporter(
        params=params, block_db=block_db, state_db=state_db, tx_index=tx_index
    )
    _IMPORTER_CACHE[id(block_db)] = importer
    return importer


def reset_importer_cache(block_db=None) -> None:
    if block_db is None:
        _IMPORTER_CACHE.clear()
        return
    _IMPORTER_CACHE.pop(id(block_db), None)


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
        importer = _get_importer(block_db, state_db, tx_index, params)
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
        importer = _get_importer(block_db, None, tx_index, params)
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
