"""Mining-related JSON-RPC methods used by the Stratum pool."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
import uuid
from dataclasses import asdict
from typing import Any, Dict, Tuple

from core.types.block import Block
from core.types.header import Header
from core.types.tx import Tx
from core.utils.merkle import merkle_root
from mining.adapters.core_chain import CoreChainAdapter
from rpc import deps
from rpc.methods import method

try:  # Optional helper to compute share target from Θ
    from consensus.difficulty import share_microtarget
except Exception:  # pragma: no cover
    share_microtarget = None  # type: ignore[assignment]

try:  # canonical zero constant
    from core.types.hash import ZERO32
except Exception:  # pragma: no cover
    ZERO32 = b"\x00" * 32  # type: ignore[assignment]

# Fallback Θ (µ-nats) if nothing else is available
_DEFAULT_THETA_MICRO = int(os.getenv("ANIMICA_DEFAULT_THETA_MICRO", "3000000"))
_DEFAULT_SHARE_TARGET = float(os.getenv("ANIMICA_DEFAULT_SHARE_TARGET", "0.01"))
_DEFAULT_SHA256_BITS = os.getenv("ANIMICA_SHA256_NBITS", "1d00ffff")

log = logging.getLogger("animica.rpc.miner")

# Constants for address and gas calculations
ADDRESS_LEN = 32  # Animica address length (32-byte digest, matches core/types/tx.py)
RECEIPT_ADDRESS_LEN = 32  # Receipt log address length (bytes)
TOPIC_LEN = 32  # Receipt log topic length (bytes)
INTRINSIC_GAS_TRANSFER = 21_000  # Intrinsic gas cost for simple transfers

# Logging display constants
MAX_DISPLAYED_TX_HASHES = 3  # Maximum number of transaction hashes to display in logs

# Mempool drain limits for block building
DEFAULT_BLOCK_GAS_LIMIT = 100_000_000_000  # 100 billion gas (very high limit for devnet)
DEFAULT_BLOCK_BYTE_LIMIT = 1_000_000_000  # 1GB block size limit

# Default gas limit for transactions when not specified (same as INTRINSIC_GAS_TRANSFER)
DEFAULT_TX_GAS_LIMIT = INTRINSIC_GAS_TRANSFER  # 21,000 gas for simple transfers

# In-memory job cache for miner.getWork / miner.submitWork flows
_JOB_CACHE: dict[str, dict[str, Any]] = {}
_LOCAL_HEAD: dict[str, Any] = {}
_AUTO_MINE: bool = False
_AUTO_TASK: asyncio.Task | None = None


def _to_hex(b: bytes | None) -> str | None:
    return None if b is None else "0x" + b.hex()


def _bytes32(val: Any) -> bytes:
    if isinstance(val, (bytes, bytearray)):
        b = bytes(val)
    elif isinstance(val, str):
        s = val[2:] if val.startswith("0x") else val
        if len(s) % 2:
            s = "0" + s
        b = bytes.fromhex(s)
    else:
        return ZERO32
    if len(b) < 32:
        b = b.rjust(32, b"\x00")
    return b[:32]


def txid_bytes(tx: Tx | dict | bytes, raw: bytes | None = None) -> bytes:
    """
    Canonical txid helper used everywhere in mining/block assembly.
    
    Computes transaction hash (txid) from various tx representations:
    - Core `Tx` objects (uses `.hash()` or `.txid()` method)
    - Decoded dict/envelope objects (uses hash field or computes from raw)
    - Raw tx bytes (computes sha3_256 directly)
    
    The canonical rule is: TxID = sha3_256(raw_cbor_bytes) for the signed envelope.
    This matches the rule used by `rpc/methods/tx.py` in `tx.sendRawTransaction`.
    
    Args:
        tx: Transaction object (Tx instance, dict, or bytes)
        raw: Optional raw CBOR bytes (used to compute hash if tx is dict without hash field)
        
    Returns:
        32-byte transaction hash
        
    Raises:
        ValueError: If hash cannot be computed from any available source
    """
    # Try 1: tx.hash() method (Tx dataclass)
    if hasattr(tx, "hash") and callable(getattr(tx, "hash")):
        try:
            h = tx.hash()
            if isinstance(h, bytes) and len(h) == 32:
                return h
        except Exception as e:
            log.debug(f"txid_bytes: tx.hash() failed: {e}")
    
    # Try 2: tx.txid() method (alternative Tx method name)
    if hasattr(tx, "txid") and callable(getattr(tx, "txid")):
        try:
            h = tx.txid()
            if isinstance(h, bytes) and len(h) == 32:
                return h
        except Exception as e:
            log.debug(f"txid_bytes: tx.txid() failed: {e}")
    
    # Try 3: Attributes (tx.tx_hash, tx.txid, tx.hash as bytes or hex)
    for attr_name in ("tx_hash", "txid", "hash"):
        if hasattr(tx, attr_name):
            val = getattr(tx, attr_name)
            if isinstance(val, bytes) and len(val) == 32:
                return val
            elif isinstance(val, str):
                # Try to decode hex string
                try:
                    hex_str = val[2:] if val.startswith("0x") else val
                    h = bytes.fromhex(hex_str)
                    if len(h) == 32:
                        return h
                except Exception:
                    pass
    
    # Try 4: Dict keys (for envelope objects)
    if isinstance(tx, dict):
        for key_name in ("hash", "tx_hash", "txid"):
            if key_name in tx:
                val = tx[key_name]
                if isinstance(val, bytes) and len(val) == 32:
                    return val
                elif isinstance(val, str):
                    try:
                        hex_str = val[2:] if val.startswith("0x") else val
                        h = bytes.fromhex(hex_str)
                        if len(h) == 32:
                            return h
                    except Exception:
                        pass
    
    # Try 5: Compute from raw bytes if available
    if raw is not None and isinstance(raw, (bytes, bytearray)):
        return hashlib.sha3_256(bytes(raw)).digest()
    
    # Try 6: If tx is raw bytes, compute directly
    if isinstance(tx, (bytes, bytearray)):
        return hashlib.sha3_256(bytes(tx)).digest()
    
    # Try 7: If tx is a Tx dataclass, serialize to CBOR and hash
    if hasattr(tx, "to_cbor") and callable(getattr(tx, "to_cbor")):
        try:
            cbor_bytes = tx.to_cbor()
            return hashlib.sha3_256(cbor_bytes).digest()
        except Exception as e:
            log.debug(f"txid_bytes: tx.to_cbor() failed: {e}")
    
    # Failed to compute hash from any source
    raise ValueError(
        f"Cannot compute txid from tx: type={type(tx).__name__}, "
        f"has_hash={hasattr(tx, 'hash')}, has_txid={hasattr(tx, 'txid')}, "
        f"has_to_cbor={hasattr(tx, 'to_cbor')}, raw_provided={raw is not None}"
    )


def _resolve_theta() -> int:
    # Try the live consensus state if available
    try:
        from consensus.state import consensus_state  # type: ignore

        st = consensus_state()
        if st and getattr(st, "theta_micro", None):
            return int(st.theta_micro)
    except Exception:
        pass
    return _DEFAULT_THETA_MICRO


def _ctx():
    try:
        return deps.get_ctx()
    except Exception:
        # In tests the FastAPI lifecycle may not have run yet; fall back to a
        # one-off context.
        return deps.build_context()


def _head_info() -> Tuple[bytes, int, bytes, int, bytes]:
    ctx = _ctx()
    snap = ctx.get_head()
    if _LOCAL_HEAD and isinstance(_LOCAL_HEAD, dict):
        local_h = int(_LOCAL_HEAD.get("height", 0))
        snap_h = int(snap.get("height", 0)) if isinstance(snap, dict) else 0
        if local_h > snap_h:
            snap = _LOCAL_HEAD
    if (snap.get("height") is None or snap.get("hash") is None) and _LOCAL_HEAD:
        snap = _LOCAL_HEAD
    header = snap.get("header") if isinstance(snap, dict) else None
    height = int(snap.get("height") or 0)
    chain_id = int(getattr(header, "chain_id", None) or ctx.cfg.chain_id)

    parent_hash_hex = snap.get("hash") if isinstance(snap, dict) else None
    if parent_hash_hex and isinstance(parent_hash_hex, str):
        parent_hash = bytes.fromhex(
            parent_hash_hex[2:] if parent_hash_hex.startswith("0x") else parent_hash_hex
        )
    else:
        header_hash = getattr(header, "hash", None)
        parent_hash = header_hash() if callable(header_hash) else header_hash or ZERO32
    if len(parent_hash) < 32:
        parent_hash = parent_hash.rjust(32, b"\x00")
    parent_mix_seed = getattr(header, "mix_seed", None) or ZERO32
    parent_state_root = getattr(header, "state_root", None) or ZERO32
    return parent_hash, height or 0, parent_mix_seed, chain_id, parent_state_root


def _policy_roots() -> Tuple[bytes, bytes]:
    ctx = _ctx()
    pow_params = (ctx.params or {}).get("pow", {}) if hasattr(ctx, "params") else {}
    pq_root = pow_params.get("pqAlgPolicyRoot") or ZERO32
    poies_root = pow_params.get("poiesPolicyRoot") or ZERO32
    if isinstance(pq_root, str):
        pq_root = bytes.fromhex(pq_root[2:] if pq_root.startswith("0x") else pq_root)
    if isinstance(poies_root, str):
        poies_root = bytes.fromhex(
            poies_root[2:] if poies_root.startswith("0x") else poies_root
        )
    if not isinstance(pq_root, (bytes, bytearray)):
        pq_root = ZERO32
    if not isinstance(poies_root, (bytes, bytearray)):
        poies_root = ZERO32
    return bytes(pq_root), bytes(poies_root)


def _beacon() -> bytes:
    try:
        from randomness.beacon import get_beacon_bytes  # type: ignore

        return get_beacon_bytes() or b""
    except Exception:
        return b""


def _decode_bech32_address(address: str) -> bytes:
    """
    Decode a bech32 address to 32-byte raw address.
    
    Args:
        address: Bech32 address string (e.g., "anim1...")
        
    Returns:
        bytes: 32-byte address (digest padded to 32 bytes)
        
    Raises:
        Exception: If address cannot be decoded
    """
    from pq.py.address import decode_address  # type: ignore[import-not-found]
    
    addr_record = decode_address(address)
    digest = bytes(addr_record.digest) if isinstance(addr_record.digest, list) else addr_record.digest
    return digest[:32].ljust(32, b"\x00")


def _get_miner_address() -> bytes:
    """
    Determine the default miner address for block rewards.
    
    Priority:
    1. Environment variable ANIMICA_MINER_ADDRESS (bech32 address)
    2. Genesis premine address for the chain (if available)
    3. Zero address (fallback)
    
    Returns:
        bytes: 32-byte miner address
    """
    # Try environment variable first
    env_addr = os.getenv("ANIMICA_MINER_ADDRESS", "").strip()
    if env_addr:
        try:
            # Try to decode bech32 address to raw bytes
            return _decode_bech32_address(env_addr)
        except Exception as e:
            log.debug(f"Failed to decode ANIMICA_MINER_ADDRESS as bech32: {e}")
            # If bech32 decode fails, try hex
            try:
                if env_addr.startswith("0x"):
                    env_addr = env_addr[2:]
                addr_bytes = bytes.fromhex(env_addr)
                return addr_bytes[:32].ljust(32, b"\x00")
            except Exception as hex_err:
                log.warning(f"Failed to decode ANIMICA_MINER_ADDRESS as hex: {hex_err}")
    
    # Try to get premine address from consensus.rewards
    try:
        from consensus.rewards import MAINNET_PREMINE_DISTRIBUTION  # type: ignore[import-not-found]
        
        ctx = _ctx()
        chain_id = ctx.cfg.chain_id
        
        # For mainnet (chain_id=1) or devnet (chain_id=1337), use first premine address
        if chain_id in (1, 1337) and MAINNET_PREMINE_DISTRIBUTION:
            premine_addr = MAINNET_PREMINE_DISTRIBUTION[0][0]  # First address in distribution
            try:
                return _decode_bech32_address(premine_addr)
            except Exception as e:
                log.warning(f"Failed to decode premine address: {e}")
    except Exception as e:
        log.debug(f"Could not load premine address: {e}")
    
    # Fallback to zero address
    log.warning("No miner address configured; using zero address for block rewards")
    return ZERO32


def _apply_block_reward(ctx: Any, height: int, payout_address: bytes | None = None) -> int:
    """
    Apply block reward to the miner's address in state.
    
    Args:
        ctx: RPC context with state_db access
        height: Block height for reward calculation
        payout_address: Optional 32-byte payout address. If None, uses default miner address.
        
    Returns:
        int: Total miner reward amount (in nANM) credited to payout address, or 0 if none
    """
    try:
        # Get miner address (use custom payout address if provided)
        miner_address = payout_address if payout_address is not None else _get_miner_address()
        
        # Compute block reward (returns list of (address, amount) tuples)
        from consensus.rewards import compute_block_reward  # type: ignore[import-not-found]
        
        chain_id = ctx.cfg.chain_id
        params = getattr(ctx, "params", None) or {}
        rewards = compute_block_reward(chain_id=chain_id, height=height, params=params)
        
        # Log warning if rewards are empty when they shouldn't be (height >= 1)
        if not rewards and height >= 1:
            log.warning(
                f"Block reward at height {height} is empty. "
                f"This may indicate missing/invalid consensus params. "
                f"Check that spec/params.yaml defines proper emission schedule for chain_id={chain_id}."
            )
        
        # Track miner reward amount for return
        miner_reward_amount = 0
        
        # If rewards are specified, apply them
        if rewards:
            from execution.state.apply_balance import credit  # type: ignore[import-not-found]
            
            state_db = ctx.state_db
            # Apply block rewards to state (miner, aicf, treasury)
            # For the first reward (miner), use the provided payout address
            for idx, (reward_addr, amount) in enumerate(rewards):
                # Override first reward (miner) with payout address if provided
                # Do this BEFORE trying to decode, since the first address may be a placeholder
                if idx == 0 and payout_address is not None:
                    reward_addr_bytes = payout_address
                else:
                    # Convert bech32 address to bytes if needed
                    if isinstance(reward_addr, str):
                        try:
                            reward_addr_bytes = _decode_bech32_address(reward_addr)
                        except Exception:
                            log.warning(f"Could not decode reward address {reward_addr}; skipping")
                            continue
                    else:
                        reward_addr_bytes = reward_addr[:32].ljust(32, b"\x00")
                
                if amount > 0:
                    new_balance = credit(state_db, reward_addr_bytes, amount)
                    log.info(
                        f"Applied block reward: height={height}, "
                        f"address={reward_addr_bytes.hex()[:16]}..., "
                        f"amount={amount}, new_balance={new_balance}"
                    )
                    
                    # Track miner reward (first reward entry)
                    if idx == 0:
                        miner_reward_amount = amount
        
        return miner_reward_amount
    except Exception as e:
        # Don't fail mining if reward application has issues
        log.error(f"Failed to apply block reward at height {height}: {e}", exc_info=True)
        return 0


def _compute_state_root(state_db: Any) -> bytes:
    """Compute a deterministic state root from the current state snapshot."""

    if state_db is None:
        return ZERO32

    try:
        snap = state_db.snapshot()
        if hasattr(snap, "digest"):
            root = snap.digest()
            if isinstance(root, str):
                root = bytes.fromhex(root[2:] if root.startswith("0x") else root)
            return _bytes32(root)
    except Exception as e:
        log.warning("failed to compute state root; returning zero", extra={"err": str(e)})

    return ZERO32


def _bits_to_target(bits_hex: str) -> int:
    bits = int(bits_hex, 16)
    exponent = bits >> 24
    mantissa = bits & 0xFFFFFF
    return mantissa * (1 << (8 * (exponent - 3)))


def _theta_to_target(theta_micro: int) -> int:
    """Derive a loose block target from θ for lightweight validation."""

    # Keep the target reachable in tests and offline environments; default
    # share target is a 1% slice of the 256-bit space.
    max_target = (1 << 256) - 1
    base = int(max_target * _DEFAULT_SHARE_TARGET)
    if theta_micro <= 0:
        return base
    # Clamp so that higher θ lowers the target but never goes to zero.
    scaled = max(1, int(base / max(theta_micro / 1_000_000, 1)))
    return min(max_target, scaled)


def _hex_to_bytes(hex_str: str) -> bytes:
    """
    Convert a hex string to bytes, handling both "0x" prefixed and unprefixed formats.
    
    Args:
        hex_str: Hex string (e.g., "0x6e23..." or "6e23...")
        
    Returns:
        bytes: The decoded bytes
    """
    s = hex_str[2:] if hex_str.startswith("0x") else hex_str
    # Pad with leading zero if odd length (ensures valid hex pairs)
    if len(s) % 2:
        s = "0" + s
    return bytes.fromhex(s)


def _parse_nonce(nonce: Any) -> bytes:
    if isinstance(nonce, (bytes, bytearray)):
        return bytes(nonce)
    if isinstance(nonce, int):
        if nonce < 0:
            raise ValueError("nonce must be non-negative")
        return nonce.to_bytes(8, "big")
    if isinstance(nonce, str):
        return _hex_to_bytes(nonce)
    raise ValueError("nonce must be hex string, int, or bytes")


def _record_local_block(
    height: int, block_hash: str, header: dict[str, Any] | None = None
) -> None:
    _LOCAL_HEAD.update({"height": height, "hash": block_hash, "header": header})


def auto_mine_enabled() -> bool:
    return _AUTO_MINE


def _get_tx_gas_limit(tx_obj: Any) -> int:
    """
    Extract gas_limit from a Tx object.
    
    Handles both flat and nested structures:
    - Flat: tx.gas_limit or tx.gas
    - Nested: tx.unsigned.gas_limit (Tx dataclass structure)
    
    Args:
        tx_obj: Transaction object (Tx instance or dict-like)
        
    Returns:
        Gas limit as integer, defaults to DEFAULT_TX_GAS_LIMIT (21,000) if not found
    """
    # Try flat gas_limit attribute
    tx_gas = getattr(tx_obj, "gas_limit", None)
    if tx_gas is not None:
        return int(tx_gas)
    
    # Try nested unsigned.gas_limit (Tx dataclass structure)
    if hasattr(tx_obj, "unsigned"):
        tx_gas = getattr(tx_obj.unsigned, "gas_limit", None)
        if tx_gas is not None:
            return int(tx_gas)
    
    # Try flat gas attribute (alternative naming)
    tx_gas = getattr(tx_obj, "gas", None)
    if tx_gas is not None:
        return int(tx_gas)
    
    # Default to intrinsic gas for simple transfers
    return DEFAULT_TX_GAS_LIMIT


def _get_tx_sender_and_nonce(tx: Tx) -> tuple[bytes | None, int | None]:
    """
    Extract sender address and nonce from a Tx object.
    
    Returns:
        tuple[bytes | None, int | None]: (sender_bytes, nonce) or (None, None) if extraction fails
    """
    try:
        # Try to get sender from unsigned field (Tx dataclass)
        sender = None
        nonce = None
        
        if hasattr(tx, "unsigned"):
            sender = getattr(tx.unsigned, "sender", None)
            nonce = getattr(tx.unsigned, "nonce", None)
        
        # Fallback to direct attributes
        if sender is None:
            sender = getattr(tx, "sender", getattr(tx, "from", getattr(tx, "frm", None)))
        if nonce is None:
            nonce = getattr(tx, "nonce", None)
        
        # Convert to bytes if string
        if isinstance(sender, str):
            if sender.startswith("0x"):
                sender = bytes.fromhex(sender[2:])
            elif sender.startswith("anim1"):
                # Decode bech32
                sender = _decode_bech32_address(sender)
            else:
                # Try hex without 0x prefix
                try:
                    sender = bytes.fromhex(sender)
                except ValueError:
                    sender = None
        
        # Ensure sender is bytes
        if sender is not None and not isinstance(sender, (bytes, bytearray)):
            sender = None
        
        # Ensure nonce is int
        if nonce is not None:
            nonce = int(nonce)
        
        return (bytes(sender) if sender else None, nonce)
    except Exception as e:
        log.debug(f"_get_tx_sender_and_nonce: Failed to extract sender/nonce: {e}")
        return (None, None)


def _adapter() -> CoreChainAdapter:
    """
    Create a CoreChainAdapter with mempool feed for block building.
    
    This adapter connects the miner to both the chain state (via block_db/state_db)
    and the mempool (via miner_feed) so that pending transactions can be included
    in newly mined blocks.
    """
    ctx = _ctx()
    
    # Try to attach a miner_feed that drains from the RPC fallback pending cache
    # This allows get_mempool_snapshot() to return pending transactions
    miner_feed = None
    try:
        from mempool.adapters.miner_feed import MinerFeed
        
        # Import tx_methods to access _FALLBACK_PENDING
        # This is where transactions are stored when submitted via RPC
        try:
            from rpc.methods import tx as tx_methods
        except ImportError:
            tx_methods = None  # type: ignore[assignment]
        
        if tx_methods is not None:
            def drain_fn(max_gas: int, max_bytes: int):
                """
                Drain function that selects transactions from the fallback pending cache.
                
                This reads from rpc.methods.tx._FALLBACK_PENDING which is where transactions
                are stored when submitted via the RPC tx.sendRawTransaction method.
                """
                txs = []
                try:
                    pending_map = getattr(tx_methods, "_FALLBACK_PENDING", {}) or {}
                    log.info(f"drain_fn called with max_gas={max_gas}, max_bytes={max_bytes}, pending_count={len(pending_map)}")
                    if not pending_map:
                        log.info("drain_fn: No pending transactions in _FALLBACK_PENDING")
                        return []
                    
                    total_gas = 0
                    total_bytes = 0
                    
                    for tx_hash_hex, raw in pending_map.items():
                        try:
                            log.debug(f"drain_fn: Processing tx {tx_hash_hex}, raw_len={len(raw)}")
                            # Decode the raw CBOR transaction
                            decoded, obj = tx_methods._decode_tx(raw)  # type: ignore[attr-defined]
                            log.debug(f"drain_fn: Decoded tx {tx_hash_hex}, type={type(decoded).__name__}")
                            
                            # Try to construct a Tx instance
                            tx_obj = None
                            if isinstance(decoded, Tx):
                                tx_obj = decoded
                                log.debug(f"drain_fn: tx {tx_hash_hex} is already Tx instance")
                            elif isinstance(decoded, dict):
                                # Normalize envelope format and try to construct Tx
                                normalized = _normalize_tx_envelope(decoded)
                                log.debug(f"drain_fn: Normalized tx {tx_hash_hex}, keys={list(normalized.keys())}")
                                tx_obj = _construct_tx_from_dict(normalized)
                                if tx_obj:
                                    log.debug(f"drain_fn: Successfully constructed Tx from dict for {tx_hash_hex}")
                                else:
                                    log.warning(f"drain_fn: Failed to construct Tx from dict for {tx_hash_hex}")
                            
                            if tx_obj is None:
                                log.warning(f"drain_fn: Skipping tx {tx_hash_hex} - could not construct Tx instance")
                                continue
                            
                            # Check gas and byte limits
                            tx_gas = _get_tx_gas_limit(tx_obj)
                            tx_bytes = len(raw)
                            
                            if total_gas + tx_gas > max_gas or total_bytes + tx_bytes > max_bytes:
                                # Skip this tx if it would exceed limits
                                log.debug(f"drain_fn: Skipping tx {tx_hash_hex} - would exceed limits (gas: {total_gas + tx_gas} > {max_gas} or bytes: {total_bytes + tx_bytes} > {max_bytes})")
                                continue
                            
                            txs.append(tx_obj)
                            total_gas += tx_gas
                            total_bytes += tx_bytes
                            log.debug(f"drain_fn: Added tx {tx_hash_hex} to batch (total: {len(txs)}, gas: {total_gas}, bytes: {total_bytes})")
                            
                        except Exception as e:
                            log.warning(f"drain_fn: Failed to decode tx {tx_hash_hex}: {e}", exc_info=True)
                            continue
                    
                    if txs:
                        log.info(f"drain_fn returning {len(txs)} transactions from fallback pending cache (total_gas={total_gas}, total_bytes={total_bytes})")
                    else:
                        log.warning(f"drain_fn: Processed {len(pending_map)} pending txs but returned 0 (all failed or exceeded limits)")
                    return txs
                    
                except Exception as e:
                    log.error(f"drain_fn failed with exception: {e}", exc_info=True)
                    return []
            
            # Create the MinerFeed with the drain function
            base_feed = MinerFeed(drain=drain_fn, notifier=None)
            
            # Wrap the MinerFeed to provide the peek_ready interface expected by CoreChainAdapter
            # CoreChainAdapter expects: peek_ready(limit, gas_limit) -> Iterable[Tx]
            class MinerFeedAdapter:
                """Adapter that provides peek_ready interface for CoreChainAdapter."""
                def __init__(self, feed: "MinerFeed"):
                    self._feed = feed
                
                def peek_ready(self, limit: int = 1000, gas_limit: int | None = None):
                    """Return an iterable of ready transactions (without removing them from pool)."""
                    log.info(f"MinerFeedAdapter.peek_ready called with limit={limit}, gas_limit={gas_limit}")
                    # Convert gas_limit to max_gas (use constant if None)
                    max_gas = gas_limit if gas_limit is not None else DEFAULT_BLOCK_GAS_LIMIT
                    max_bytes = DEFAULT_BLOCK_BYTE_LIMIT
                    
                    # Use next_batch() instead of private _drain() method
                    # next_batch returns a MinerTxBatch with a txs attribute
                    log.debug(f"MinerFeedAdapter.peek_ready calling next_batch(max_gas={max_gas}, max_bytes={max_bytes})")
                    batch = self._feed.next_batch(max_gas, max_bytes, wait_s=0)
                    txs = batch.txs if hasattr(batch, 'txs') else []
                    log.info(f"MinerFeedAdapter.peek_ready: next_batch returned {len(txs)} transactions")
                    
                    # Return up to limit transactions
                    result = txs[:limit] if limit else txs
                    log.info(f"MinerFeedAdapter.peek_ready returning {len(result)} transactions")
                    return result
            
            miner_feed = MinerFeedAdapter(base_feed)
            log.info("Created MinerFeed connected to RPC fallback pending cache")
    except Exception as e:
        # If anything fails, continue without a miner_feed
        # The adapter will still work but will use the inline fallback in _mine_once
        log.warning(f"Failed to attach miner_feed to adapter: {e}")
        miner_feed = None
    
    return CoreChainAdapter(
        kv=ctx.kv,
        block_db=ctx.block_db,
        state_db=getattr(ctx, "state_db", None),
        miner_feed=miner_feed,
    )


def _build_child_header(
    parent_height: int, parent_hash: bytes, parent_header: Any
) -> Header:
    theta = getattr(
        parent_header, "thetaMicro", getattr(parent_header, "theta_micro", None)
    )
    mix_seed = getattr(
        parent_header, "mixSeed", getattr(parent_header, "mix_seed", None)
    )
    state_root = getattr(
        parent_header, "stateRoot", getattr(parent_header, "state_root", None)
    )
    pq_root, poies_root = _policy_roots()
    return Header(
        v=1,
        chainId=_ctx().cfg.chain_id,
        height=parent_height + 1,
        parentHash=_bytes32(parent_hash),
        timestamp=int(time.time()),
        stateRoot=_bytes32(state_root or ZERO32),
        txsRoot=ZERO32,
        receiptsRoot=ZERO32,
        proofsRoot=ZERO32,
        daRoot=ZERO32,
        mixSeed=_bytes32(mix_seed or ZERO32),
        poiesPolicyRoot=poies_root,
        pqAlgPolicyRoot=pq_root,
        thetaMicro=int(theta or _resolve_theta()),
        nonce=0,
        extra=b"",
    )


def _execute_transactions(
    ctx: Any, txs: list[Tx], header: Header, coinbase_address: bytes | None = None
) -> list[Any]:
    """
    Execute transactions and generate receipts.
    
    Args:
        ctx: RPC context with state_db access
        txs: List of transactions to execute
        header: Block header (provides height, timestamp context)
        coinbase_address: Optional coinbase address for tips; defaults to zero if None
        
    Returns:
        list: Transaction receipts (one per tx, in order)
    """
    if not txs:
        return []
    
    receipts = []
    state_db = ctx.state_db
    if state_db is None:
        log.warning("No state_db available; skipping tx execution")
        return []
    
    # Use coinbase address for tips (or payout address if mining with custom address)
    coinbase = coinbase_address if coinbase_address is not None else ZERO32
    
    try:
        # Try to use execution runtime for transfers
        from execution.runtime.transfers import apply_transfer
        from execution.runtime.env import BlockEnv, TxEnv
        from core.types.receipt import Receipt, ReceiptStatus, Log
        
        # Build block environment
        block_env = BlockEnv(
            height=header.height,
            timestamp=header.timestamp,
            coinbase=coinbase,
            chain_id=header.chainId,
        )
        
        for tx in txs:
            # Extract sender from tx (may be in different fields)
            sender = getattr(tx, "sender", getattr(tx, "from", getattr(tx, "frm", None)))
            if sender is None:
                log.warning(f"Transaction missing sender; skipping")
                # Add failed receipt
                receipts.append(Receipt(
                    status=ReceiptStatus.REVERT,
                    gas_used=0,
                    logs=tuple()
                ))
                continue
            
            # Ensure sender is bytes (may be hex string)
            if isinstance(sender, str):
                sender_hex = sender[2:] if sender.startswith("0x") else sender
                sender_bytes = bytes.fromhex(sender_hex)
            else:
                sender_bytes = bytes(sender) if isinstance(sender, (bytes, bytearray)) else sender
            
            # Pad/truncate to Animica address length (32 bytes, matches core/types/tx.py ADDRESS_LEN)
            if len(sender_bytes) > ADDRESS_LEN:
                sender_bytes = sender_bytes[:ADDRESS_LEN]
            elif len(sender_bytes) < ADDRESS_LEN:
                sender_bytes = sender_bytes.rjust(ADDRESS_LEN, b"\x00")
            
            # Build tx environment
            gas_price = getattr(tx, "gas_price", getattr(tx, "tip", 1))
            tx_env = TxEnv(
                sender=sender_bytes,
                gas_price=int(gas_price) if gas_price is not None else 1,
                base_price=0,  # No base fee in simple model
            )
            
            try:
                # Apply transfer (handles balance updates, nonce increment, fees)
                result = apply_transfer(tx, state_db, block_env, tx_env, emit_event=True)
                
                # Convert ApplyResult to Receipt
                # Map status codes
                if hasattr(result, "status"):
                    status_val = result.status
                    # Convert string status to ReceiptStatus enum
                    if isinstance(status_val, str):
                        status_map = {
                            "SUCCESS": ReceiptStatus.SUCCESS,
                            "REVERT": ReceiptStatus.REVERT,
                            "OOG": ReceiptStatus.OOG,
                        }
                        status = status_map.get(status_val.upper(), ReceiptStatus.REVERT)
                    elif isinstance(status_val, int):
                        status = ReceiptStatus(status_val)
                    else:
                        status = ReceiptStatus.SUCCESS if status_val else ReceiptStatus.REVERT
                else:
                    status = ReceiptStatus.SUCCESS
                
                gas_used = int(result.gas_used) if hasattr(result, "gas_used") else INTRINSIC_GAS_TRANSFER
                
                # Convert logs to Receipt Log format
                logs_out = []
                if hasattr(result, "logs") and result.logs:
                    for log_event in result.logs:
                        # Ensure address is RECEIPT_ADDRESS_LEN bytes (Receipt Log format)
                        addr = getattr(log_event, "address", b"\x00" * RECEIPT_ADDRESS_LEN)
                        if isinstance(addr, (bytes, bytearray)):
                            addr_bytes = bytes(addr)
                        else:
                            addr_bytes = b"\x00" * RECEIPT_ADDRESS_LEN
                        # Pad to RECEIPT_ADDRESS_LEN bytes
                        if len(addr_bytes) < RECEIPT_ADDRESS_LEN:
                            addr_bytes = addr_bytes.ljust(RECEIPT_ADDRESS_LEN, b"\x00")
                        elif len(addr_bytes) > RECEIPT_ADDRESS_LEN:
                            addr_bytes = addr_bytes[:RECEIPT_ADDRESS_LEN]
                        
                        topics = getattr(log_event, "topics", [])
                        topics_tuple = tuple(
                            bytes(t)[:TOPIC_LEN].ljust(TOPIC_LEN, b"\x00") if isinstance(t, (bytes, bytearray)) else b"\x00" * TOPIC_LEN
                            for t in topics
                        )
                        data = bytes(getattr(log_event, "data", b""))
                        
                        logs_out.append(Log(address=addr_bytes, topics=topics_tuple, data=data))
                
                receipt = Receipt(
                    status=status,
                    gas_used=gas_used,
                    logs=tuple(logs_out)
                )
                receipts.append(receipt)
                
            except Exception as e:
                log.warning(f"Transaction execution failed: {e}")
                # Add revert receipt
                receipts.append(Receipt(
                    status=ReceiptStatus.REVERT,
                    gas_used=INTRINSIC_GAS_TRANSFER,  # Charge intrinsic gas on revert
                    logs=tuple()
                ))
        
    except ImportError:
        log.warning("execution.runtime not available; generating stub receipts")
        # Fallback: generate stub receipts without execution
        from core.types.receipt import Receipt, ReceiptStatus
        for _ in txs:
            receipts.append(Receipt(
                status=ReceiptStatus.SUCCESS,
                gas_used=INTRINSIC_GAS_TRANSFER,
                logs=tuple()
            ))
    
    return receipts


def _normalize_tx_envelope(decoded: dict) -> dict:
    """
    Normalize transaction envelope format to core format.
    
    Handles three formats:
    1. Core canonical: {"tx": {...canonical unsigned tx...}, "sigs": [...]}  (no change needed)
    2. RPC/CLI simplified: {"body": {...flat fields...}, "sig": {...}} 
       where body has: {chainId, from, to, nonce, value, gasLimit, maxFee, data}
    3. Flat format (pass through)
    
    Returns dict in core canonical format expected by Tx.from_obj().
    """
    if "body" in decoded:
        # RPC/CLI simplified envelope: convert to canonical core format
        body = decoded["body"]
        
        # Check if body is already in canonical format (has "v", "gas", "payload" keys)
        if "v" in body and "gas" in body and "payload" in body:
            # Body is already canonical, just wrap it
            normalized = {"tx": body}
        else:
            # Body is in simplified format, need to convert to canonical
            # Simplified format: {chainId, from, to, nonce, value, gasLimit, maxFee, data}
            # Canonical format: {v, chainId, from, nonce, gas: {price, limit}, payload: {t, v}, accessList}
            
            # Extract fields with defaults
            chain_id = body.get("chainId", body.get("chain_id", 1))
            from_addr = body.get("from", body.get("sender"))
            to_addr = body.get("to")
            nonce = body.get("nonce", 0)
            value = body.get("value", body.get("amount", 0))
            gas_limit = body.get("gasLimit", body.get("gas_limit", body.get("gas", 21000)))
            gas_price = body.get("maxFee", body.get("max_fee", body.get("gasPrice", body.get("gas_price", body.get("tip", 1)))))
            data = body.get("data", b"")
            
            # Ensure data is bytes
            if isinstance(data, str):
                # Handle hex strings
                if data.startswith("0x"):
                    data = bytes.fromhex(data[2:])
                else:
                    data = data.encode("utf-8")
            elif isinstance(data, (list, tuple)):
                data = bytes(data)
            elif not isinstance(data, (bytes, bytearray)):
                data = b""
            
            # Convert addresses (bech32 strings → raw bytes if needed)
            # Core expects 32-byte raw addresses
            def _addr_to_bytes(addr) -> bytes:
                """Convert address to 32-byte raw format."""
                if addr is None:
                    raise ValueError("Address cannot be None")
                
                if isinstance(addr, (bytes, bytearray)):
                    addr_bytes = bytes(addr)
                elif isinstance(addr, str):
                    # Try to decode bech32 first
                    if addr.startswith("anim1"):
                        try:
                            addr_bytes = _decode_bech32_address(addr)
                        except Exception as e:
                            # Bech32 decode failed - fall back to UTF-8 hash
                            # We can't assume hex format since bech32 addresses use base32 encoding
                            import hashlib
                            addr_bytes = hashlib.sha3_256(addr.encode("utf-8")).digest()
                            log.warning(f"Could not decode address '{addr}' as bech32 ({e}), using hash fallback")
                    elif addr.startswith("0x"):
                        addr_bytes = bytes.fromhex(addr[2:])
                    else:
                        # Try bare hex, fall back to UTF-8 hash
                        try:
                            addr_bytes = bytes.fromhex(addr)
                        except ValueError:
                            import hashlib
                            addr_bytes = hashlib.sha3_256(addr.encode("utf-8")).digest()
                            log.warning(f"Could not decode address '{addr}' as hex, using hash")
                else:
                    raise TypeError(f"Unsupported address type: {type(addr).__name__} (expected str or bytes)")
                
                # Pad or truncate to 32 bytes
                if len(addr_bytes) < ADDRESS_LEN:
                    addr_bytes = addr_bytes.ljust(ADDRESS_LEN, b"\x00")
                elif len(addr_bytes) > ADDRESS_LEN:
                    addr_bytes = addr_bytes[:ADDRESS_LEN]
                
                return addr_bytes
            
            # Build canonical unsigned tx structure
            canonical_tx = {
                "v": 1,
                "chainId": int(chain_id),
                "from": _addr_to_bytes(from_addr) if from_addr else b"\x00" * ADDRESS_LEN,
                "nonce": int(nonce),
                "gas": {
                    "price": int(gas_price),
                    "limit": int(gas_limit),
                },
                "payload": {
                    "t": 0,  # TxKind.TRANSFER
                    "v": {
                        "to": _addr_to_bytes(to_addr) if to_addr else b"\x00" * ADDRESS_LEN,
                        "amount": int(value),
                        "data": bytes(data),
                    },
                },
                "accessList": [],
            }
            
            normalized = {"tx": canonical_tx}
        
        # Handle signatures
        if "sigs" in decoded:
            normalized["sigs"] = decoded["sigs"]
        elif "sig" in decoded:
            # Single sig: wrap in array and normalize field names
            sig = decoded["sig"]
            # Normalize signature field names (algId/alg_id → alg, pk → pubkey)
            normalized_sig = {
                "alg": sig.get("algId", sig.get("alg_id", sig.get("alg", 0))),
                "pubkey": sig.get("pk", sig.get("pubkey", sig.get("pub", b""))),
                "sig": sig.get("sig", sig.get("signature", b"")),
            }
            normalized["sigs"] = [normalized_sig]
        else:
            normalized["sigs"] = []
        
        return normalized
    else:
        # Already in core format or flat format
        return decoded


def _construct_tx_from_dict(normalized: dict) -> Tx | None:
    """
    Try to construct a Tx instance from a normalized dict.
    
    Tries multiple constructor methods in order:
    1. Tx.from_obj() (preferred)
    2. Tx.from_dict() (fallback)
    
    Returns Tx instance or None if construction fails or no constructor available.
    """
    if hasattr(Tx, "from_obj"):
        try:
            return Tx.from_obj(normalized)  # type: ignore[attr-defined]
        except Exception as e:
            log.warning(f"_construct_tx_from_dict: Tx.from_obj failed: {e}", exc_info=True)
            return None
    elif hasattr(Tx, "from_dict"):
        try:
            return Tx.from_dict(normalized)  # type: ignore[attr-defined]
        except Exception as e:
            log.warning(f"_construct_tx_from_dict: Tx.from_dict failed: {e}", exc_info=True)
            return None
    else:
        log.warning("_construct_tx_from_dict: Tx class has no from_obj or from_dict method")
        return None


def _mine_once(payout_address: bytes | None = None) -> tuple[bool, int]:
    """
    Mine a single block with proof-of-work.
    
    This function performs actual mining by iterating through nonces until a valid
    block hash is found that meets the difficulty target. The target is derived from
    the current theta (acceptance threshold) parameter.
    
    Key operations:
    1. Select pending transactions from mempool snapshot
    2. Build candidate block header with txs merkle root
    3. Find nonce that satisfies PoW target (hash <= target)
    4. Execute transactions to update state (balances, nonces)
    5. Generate transaction receipts (status, gasUsed, logs)
    6. Apply block reward to coinbase/payout address
    7. Persist block with receipts and update canonical head
    
    Base units: 1 ANM = 1_000_000_000 nANM (nano-ANM)
    Block numbering: 0-based (genesis at height 0)
    Mempool selection: Snapshot at time of mining (non-deterministic ordering OK)
    
    Args:
        payout_address: Optional 32-byte payout address. If None, uses default miner address.
        
    Returns:
        tuple[bool, int]: (success, reward_amount) where:
            - success: True if block was mined and accepted, False otherwise
            - reward_amount: Miner reward in nANM (0 if mining failed or no reward)
    """
    ctx = _ctx()
    adapter = _adapter()
    txs: list[Tx] = []
    included_hashes: list[str] = []
    
    # Track per-sender nonces to enforce sequencing within this block
    # Maps sender_bytes -> next_expected_nonce
    # NOTE: This is intentionally reset per mining call - each block starts fresh
    # from current state, ensuring nonce consistency even if transactions fail
    sender_nonces: dict[bytes, int] = {}

    # Collect pending transactions from the best available source (mempool → fallback cache)
    log.info("_mine_once: Starting transaction collection from mempool adapter")
    try:
        txs = list(adapter.get_mempool_snapshot(limit=1000))
        log.info(f"_mine_once: adapter.get_mempool_snapshot returned {len(txs)} transactions")
        if txs:
            log.info(f"_mine_once: Sample tx types from adapter: {[type(tx).__name__ for tx in txs[:3]]}")
        # Track hashes of transactions from adapter for eviction later
        # Use canonical txid_bytes() helper to support multiple tx representations
        for tx in txs:
            try:
                tx_hash_bytes = txid_bytes(tx)
                tx_hash_hex = "0x" + tx_hash_bytes.hex()
                included_hashes.append(tx_hash_hex)
                log.debug(f"Tracked tx hash from adapter: {tx_hash_hex}")
            except Exception as e:
                # txid_bytes() may fail for malformed tx; log and skip this tx for eviction tracking
                log.debug(f"Could not get hash for tx from adapter; skipping eviction tracking: {e}")
        if txs:
            log.info(f"Retrieved {len(txs)} transactions from mempool adapter for mining (tracked {len(included_hashes)} hashes)")
    except Exception as e:
        log.warning(f"mempool snapshot unavailable; falling back to in-process cache: {e}", exc_info=True)
    if not txs:
        log.info("_mine_once: No transactions from adapter, trying fallback pending cache")
        try:
            from rpc.methods import tx as tx_methods

            pending_map = getattr(tx_methods, "_FALLBACK_PENDING", {}) or {}
            pending_count = len(pending_map)
            log.info(f"_mine_once: Found {pending_count} transactions in fallback pending cache")
            if pending_count > 0:
                log.info(f"Attempting to retrieve {pending_count} transactions from fallback pending cache")
            for tx_hash_hex, raw in pending_map.items():
                try:
                    log.debug(f"_mine_once: Decoding tx {tx_hash_hex} from fallback")
                    decoded, obj = tx_methods._decode_tx(raw)  # type: ignore[attr-defined]
                    log.debug(f"_mine_once: Decoded tx {tx_hash_hex}, type={type(decoded).__name__}")
                    # Accept both Tx instances and dict/obj that can be used as Tx
                    # _decode_tx returns (Tx, dict) when Tx.from_obj succeeds, or (dict, dict) when falling back to dict
                    if isinstance(decoded, Tx):
                        # Verify chainId matches this node's chainId before including
                        tx_chain_id = getattr(decoded, "chain_id", getattr(decoded, "chainId", None))
                        if hasattr(decoded, "unsigned"):
                            tx_chain_id = getattr(decoded.unsigned, "chain_id", tx_chain_id)
                        
                        node_chain_id = ctx.cfg.chain_id
                        if tx_chain_id is not None and int(tx_chain_id) != int(node_chain_id):
                            log.warning(
                                f"_mine_once: Skipping tx {tx_hash_hex} - chainId mismatch "
                                f"(tx={tx_chain_id}, node={node_chain_id})"
                            )
                            continue
                        
                        # Check nonce sequencing (optional, allows gaps but enforces order for same sender)
                        sender, tx_nonce = _get_tx_sender_and_nonce(decoded)
                        if sender is not None and tx_nonce is not None:
                            # Get expected nonce for this sender (from state or tracked nonces)
                            if sender not in sender_nonces:
                                # First tx from this sender - get current nonce from state
                                try:
                                    state_nonce = ctx.state_db.get_nonce(sender) if ctx.state_db else 0
                                    sender_nonces[sender] = int(state_nonce)
                                except Exception:
                                    sender_nonces[sender] = 0
                            
                            expected_nonce = sender_nonces[sender]
                            if tx_nonce < expected_nonce:
                                log.warning(
                                    f"_mine_once: Skipping tx {tx_hash_hex} - nonce too low "
                                    f"(tx_nonce={tx_nonce}, expected={expected_nonce})"
                                )
                                continue
                            elif tx_nonce > expected_nonce:
                                # Skip transactions with nonce gaps within this block
                                # NOTE: This prevents out-of-order execution but means txs
                                # with gaps stay in mempool until gap is filled. This is
                                # standard Ethereum-style behavior and prevents stuck transactions.
                                log.debug(
                                    f"_mine_once: Skipping tx {tx_hash_hex} - nonce gap "
                                    f"(tx_nonce={tx_nonce}, expected={expected_nonce})"
                                )
                                continue
                            
                            # Nonce matches - accept and increment expected nonce
                            sender_nonces[sender] = expected_nonce + 1
                        
                        txs.append(decoded)
                        included_hashes.append(tx_hash_hex)
                        log.debug(f"_mine_once: Added Tx instance {tx_hash_hex} to txs list")
                    elif decoded is not None and isinstance(decoded, dict):
                        # Try to construct Tx from the decoded dict
                        # The dict may be in one of two formats:
                        # 1. Core format: {"tx": {...}, "sigs": [...]}
                        # 2. RPC envelope format: {"body": {...}, "sig": {...}} or {"body": {...}, "sigs": [...]}
                        try:
                            # Normalize RPC envelope format to core format if needed
                            normalized = _normalize_tx_envelope(decoded)
                            log.debug(f"_mine_once: Normalized tx {tx_hash_hex}, keys={list(normalized.keys())}")
                            
                            # Try to construct Tx using available constructor methods
                            tx_obj = _construct_tx_from_dict(normalized)
                            if tx_obj is not None:
                                # Verify chainId matches this node's chainId before including
                                tx_chain_id = getattr(tx_obj, "chain_id", getattr(tx_obj, "chainId", None))
                                if hasattr(tx_obj, "unsigned"):
                                    tx_chain_id = getattr(tx_obj.unsigned, "chain_id", tx_chain_id)
                                
                                # Also check the dict body if Tx attributes not available
                                if tx_chain_id is None and isinstance(obj, dict):
                                    body = obj.get("body", {}) if "body" in obj else obj.get("tx", {})
                                    tx_chain_id = body.get("chainId", body.get("chain_id"))
                                
                                node_chain_id = ctx.cfg.chain_id
                                if tx_chain_id is not None and int(tx_chain_id) != int(node_chain_id):
                                    log.warning(
                                        f"_mine_once: Skipping tx {tx_hash_hex} - chainId mismatch "
                                        f"(tx={tx_chain_id}, node={node_chain_id})"
                                    )
                                    continue
                                
                                # Check nonce sequencing (optional, allows gaps but enforces order for same sender)
                                sender, tx_nonce = _get_tx_sender_and_nonce(tx_obj)
                                if sender is not None and tx_nonce is not None:
                                    # Get expected nonce for this sender (from state or tracked nonces)
                                    if sender not in sender_nonces:
                                        # First tx from this sender - get current nonce from state
                                        try:
                                            state_nonce = ctx.state_db.get_nonce(sender) if ctx.state_db else 0
                                            sender_nonces[sender] = int(state_nonce)
                                        except Exception:
                                            sender_nonces[sender] = 0
                                    
                                    expected_nonce = sender_nonces[sender]
                                    if tx_nonce < expected_nonce:
                                        log.warning(
                                            f"_mine_once: Skipping tx {tx_hash_hex} - nonce too low "
                                            f"(tx_nonce={tx_nonce}, expected={expected_nonce})"
                                        )
                                        continue
                                    elif tx_nonce > expected_nonce:
                                        log.debug(
                                            f"_mine_once: Skipping tx {tx_hash_hex} - nonce gap "
                                            f"(tx_nonce={tx_nonce}, expected={expected_nonce})"
                                        )
                                        continue
                                    
                                    # Nonce matches - accept and increment expected nonce
                                    sender_nonces[sender] = expected_nonce + 1
                                
                                txs.append(tx_obj)
                                included_hashes.append(tx_hash_hex)
                                log.debug(f"_mine_once: Successfully constructed and added Tx from dict for {tx_hash_hex}")
                            else:
                                log.warning(
                                    "Tx class has no from_obj/from_dict method; skipping tx from fallback cache",
                                    extra={"hash": tx_hash_hex},
                                )
                        except Exception as e:
                            log.warning(
                                "Could not convert decoded tx to Tx instance; skipping from fallback cache",
                                extra={"hash": tx_hash_hex, "err": str(e), "keys": list(decoded.keys() if isinstance(decoded, dict) else [])},
                                exc_info=True,
                            )
                except Exception as e:
                    log.warning(
                        "Failed to decode pending tx from fallback cache; skipping",
                        extra={"hash": tx_hash_hex, "err": str(e)},
                        exc_info=True,
                    )
            if txs:
                log.info(f"Retrieved {len(txs)}/{pending_count} transactions from fallback pending cache for mining")
            elif pending_count > 0:
                log.warning(f"Failed to retrieve any of {pending_count} transactions from fallback pending cache")
        except Exception as e:
            log.error("Fallback pending pool unavailable", extra={"err": str(e)}, exc_info=True)
    head = adapter.get_head()
    parent_height = int(head.get("height") or 0)
    parent_hash_val = head.get("hash") or head.get("hash_hex")
    parent_header = head.get("obj") or head.get("header")

    if parent_header is None:
        # If the DB is empty, force bootstrap and retry once
        _maybe_bootstrap = getattr(deps, "startup", None)
        if callable(_maybe_bootstrap):
            try:
                # reinitialize context to pick up genesis
                deps.ensure_started(ctx.cfg)
                head = adapter.get_head()
                parent_height = int(head.get("height") or 0)
                parent_hash_val = head.get("hash") or head.get("hash_hex")
                parent_header = head.get("obj") or head.get("header")
            except Exception:
                parent_header = None

    parent_hash_bytes = _bytes32(parent_hash_val or ZERO32)
    if parent_header is None:
        # Build a minimal synthetic parent header so hashes/roots have sane defaults
        pq_root, poies_root = _policy_roots()
        parent_header = Header(
            v=1,
            chainId=_ctx().cfg.chain_id,
            height=parent_height,
            parentHash=parent_hash_bytes,
            timestamp=int(time.time()),
            stateRoot=ZERO32,
            txsRoot=ZERO32,
            receiptsRoot=ZERO32,
            proofsRoot=ZERO32,
            daRoot=ZERO32,
            mixSeed=ZERO32,
            poiesPolicyRoot=poies_root,
            pqAlgPolicyRoot=pq_root,
            thetaMicro=_resolve_theta(),
            nonce=0,
            extra=b"",
        )

    # Build child header template (nonce will be updated in mining loop). Update the
    # txsRoot to reflect any pending transactions we plan to include.
    header_template = _build_child_header(parent_height, parent_hash_bytes, parent_header)

    if txs:
        # Build merkle root from tx hashes using safe txid_bytes() helper
        # Drop individual malformed txs instead of failing the whole batch
        leaves = []
        valid_txs = []
        valid_hashes = []
        
        for i, tx in enumerate(txs):
            try:
                # Use canonical txid_bytes() helper that supports multiple tx formats
                tx_hash = txid_bytes(tx)
                leaves.append(tx_hash)
                valid_txs.append(tx)
                # Keep corresponding hash from included_hashes if available
                if i < len(included_hashes):
                    valid_hashes.append(included_hashes[i])
            except Exception as e:
                log.debug(
                    f"Skipping malformed tx {i+1}/{len(txs)}: {e}",
                    extra={"tx_type": type(tx).__name__, "err": str(e)}
                )
        
        # Calculate counts before reassigning
        original_count = len(txs)
        valid_count = len(valid_txs)
        skipped_total = original_count - valid_count
        
        # Update txs list and included_hashes to only include valid transactions
        txs = valid_txs
        included_hashes = valid_hashes
        
        # Log summary of tx selection
        if valid_count > 0 or skipped_total > 0:
            log.info(
                f"Selected {valid_count} valid transactions for block (skipped {skipped_total} malformed)",
                extra={"pending_total": original_count, "valid": valid_count, "skipped": skipped_total}
            )
        
        # Compute merkle root and update header
        if leaves:
            try:
                txs_root = merkle_root(leaves)
                from dataclasses import replace
                header_template = replace(header_template, txsRoot=txs_root)
                log.debug(f"Computed txsRoot from {len(leaves)} tx hashes: {txs_root.hex()[:16]}...")
            except Exception as e:
                log.error(f"Failed to compute merkle root from {len(leaves)} leaves: {e}", exc_info=True)
                # Fall back to empty block if merkle root computation fails
                txs = []
                included_hashes = []
    
    # Compute target from theta
    theta_micro = header_template.thetaMicro
    target = _theta_to_target(theta_micro)
    
    # Mining loop: iterate through nonces until we find one that meets the target
    # Cap iterations to avoid infinite loops in tests or misconfigured environments
    DEFAULT_MAX_NONCE = 100_000
    max_nonce = int(os.getenv("ANIMICA_MINER_MAX_NONCE", str(DEFAULT_MAX_NONCE)))
    
    reward_amount = 0

    for nonce_val in range(max_nonce):
        # Update header with new nonce using dataclasses.replace for efficiency
        try:
            from dataclasses import replace
            header = replace(header_template, nonce=nonce_val)
        except Exception:
            # Fallback if replace not available or Header is not a dataclass
            header = Header(
                v=header_template.v,
                chainId=header_template.chainId,
                height=header_template.height,
                parentHash=header_template.parentHash,
                timestamp=header_template.timestamp,
                stateRoot=header_template.stateRoot,
                txsRoot=header_template.txsRoot,
                receiptsRoot=header_template.receiptsRoot,
                proofsRoot=header_template.proofsRoot,
                daRoot=header_template.daRoot,
                mixSeed=header_template.mixSeed,
                poiesPolicyRoot=header_template.poiesPolicyRoot,
                pqAlgPolicyRoot=header_template.pqAlgPolicyRoot,
                thetaMicro=header_template.thetaMicro,
                nonce=nonce_val,
                extra=header_template.extra,
            )
        
        # Compute block hash
        block_hash_bytes = header.hash()
        block_hash_int = int.from_bytes(block_hash_bytes, "big")
        
        # Check if hash meets target
        if block_hash_int <= target:
            # Found a valid block! Now execute txs and generate receipts before persisting.
            receipts = _execute_transactions(ctx, txs, header, payout_address)

            # Apply block reward before finalizing header/roots
            reward_amount = _apply_block_reward(ctx, header.height, payout_address)

            # Compute receipts root (if any receipts) and ensure txs root matches tx set
            receipts_root = ZERO32
            if receipts:
                try:
                    leaves = [rcpt.hash() for rcpt in receipts]
                    receipts_root = merkle_root(leaves) if leaves else ZERO32
                except Exception as e:
                    log.warning(
                        "failed to compute receipts root; defaulting to zero", extra={"err": str(e)}
                    )

            # Recompute txsRoot using safe txid_bytes() helper
            try:
                if txs:
                    leaves = []
                    for tx in txs:
                        try:
                            leaves.append(txid_bytes(tx))
                        except Exception as e:
                            log.warning(f"Failed to compute hash for tx in final root: {e}")
                    txs_root = merkle_root(leaves) if leaves else ZERO32
                else:
                    txs_root = ZERO32
            except Exception as e:
                log.warning(f"Failed to recompute txsRoot after execution: {e}")
                txs_root = header.txsRoot

            state_root = _compute_state_root(getattr(ctx, "state_db", None))

            try:
                from dataclasses import replace

                header = replace(
                    header,
                    stateRoot=state_root,
                    txsRoot=txs_root,
                    receiptsRoot=receipts_root,
                )
            except Exception:
                header = Header(
                    v=header.v,
                    chainId=header.chainId,
                    height=header.height,
                    parentHash=header.parentHash,
                    timestamp=header.timestamp,
                    stateRoot=state_root,
                    txsRoot=txs_root,
                    receiptsRoot=receipts_root,
                    proofsRoot=header.proofsRoot,
                    daRoot=header.daRoot,
                    mixSeed=header.mixSeed,
                    poiesPolicyRoot=header.poiesPolicyRoot,
                    pqAlgPolicyRoot=header.pqAlgPolicyRoot,
                    thetaMicro=header.thetaMicro,
                    nonce=header.nonce,
                    extra=header.extra,
                )

            # Build block with updated header and receipts
            block = Block.from_components(
                header=header, txs=txs, proofs=(), receipts=receipts, verify=True
            )
            
            # Persist block directly using block_db's atomic method
            # This ensures the block is stored and marked canonical in one transaction
            try:
                block_db = ctx.block_db
                if hasattr(block_db, "append_canonical_block"):
                    block_db.append_canonical_block(header.height, block)
                    accepted = True
                    log.info(f"Block persisted via append_canonical_block at height {header.height}")
                else:
                    # Fallback to adapter
                    accepted = adapter.submit_block(block)
                    log.info(f"Block submitted via adapter: accepted={accepted}")
            except Exception as e:
                log.error(f"Block persistence failed: {e}", exc_info=True)
                accepted = False
            
            if accepted:
                _record_local_block(header.height, "0x" + block_hash_bytes.hex(), header)

                # Evict successfully mined txs from both mempool adapter and fallback cache
                # to prevent re-mining them in subsequent blocks
                if included_hashes:
                    # 1. Evict from adapter mempool (if available)
                    try:
                        # Convert hex hashes to bytes for adapter eviction
                        hashes_bytes = [_hex_to_bytes(h) for h in included_hashes]
                        
                        # Call adapter to evict from mempool pool
                        if hasattr(adapter, "remove_included"):
                            adapter.remove_included(hashes_bytes)
                            log.info(f"Evicted {len(hashes_bytes)} included transactions from mempool adapter")
                    except Exception as e:
                        log.warning(f"Failed to evict from mempool adapter: {e}")
                    
                    # 2. Evict from fallback pending cache (backward compatibility)
                    try:
                        from rpc.methods import tx as tx_methods

                        cache = getattr(tx_methods, "_FALLBACK_PENDING", {}) or {}
                        ts_cache = getattr(tx_methods, "_FALLBACK_PENDING_TS", {}) or {}
                        evicted_count = 0
                        for h in included_hashes:
                            # pop() returns the removed value or None if key doesn't exist
                            # We count eviction only if the tx was actually in the cache
                            if cache.pop(h, None) is not None:
                                # Also remove timestamp (may not exist, that's ok)
                                ts_cache.pop(h, None)
                                evicted_count += 1
                        if evicted_count > 0:
                            log.info(f"Evicted {evicted_count} included transactions from fallback cache")
                    except Exception as e:
                        log.warning(f"Failed to evict from fallback cache: {e}")

                log.info(
                    f"Mined block at height {header.height} with nonce {nonce_val} "
                    f"(hash {block_hash_int} <= target {target}), reward={reward_amount} nANM, "
                    f"txs={len(txs)}, receipts={len(receipts) if receipts else 0}, "
                    f"included_tx_hashes={included_hashes[:MAX_DISPLAYED_TX_HASHES]}"
                    f"{' ...' if len(included_hashes) > MAX_DISPLAYED_TX_HASHES else ''}"
                )
                return (True, reward_amount)
            return (False, 0)
    
    # Failed to mine a valid block within max_nonce iterations
    log.warning(
        f"Failed to mine block at height {parent_height + 1} after {max_nonce} attempts "
        f"(target: {target}, theta: {theta_micro})"
    )
    return (False, 0)


async def _auto_mine_loop(interval: float = 1.0) -> None:
    global _AUTO_MINE
    while _AUTO_MINE:
        try:
            _mine_once()
        except Exception:
            pass
        await asyncio.sleep(interval)


def _start_auto_task() -> bool:
    global _AUTO_TASK
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    if _AUTO_TASK is None or _AUTO_TASK.done():
        _AUTO_TASK = loop.create_task(_auto_mine_loop())
    return True


@method(
    "miner.getWork",
    desc="Return a mining work template for Stratum/CPU miners",
    aliases=("miner_getWork",),
)
def miner_get_work(params: Any | None = None) -> Dict[str, Any]:
    from mining.templates import TemplateBuilder

    algo_hint: str | None = None
    if params is None:
        payload: dict[str, Any] | None = None
    elif isinstance(params, dict):
        payload = (
            params.get("payload")
            if len(params) == 1 and "payload" in params
            else params
        )
    elif isinstance(params, (list, tuple)):
        params_list = list(params)
        if len(params_list) == 0:
            payload = None
        elif len(params_list) == 1 and isinstance(params_list[0], dict):
            payload = params_list[0]
        elif len(params_list) == 1:
            payload = None
            algo_hint = str(params_list[0])
        else:
            raise ValueError("expected at most one param: optional algo hint")
    elif isinstance(params, str):
        payload = None
        algo_hint = params
    else:
        raise ValueError("params must be array or object")

    if payload:
        algo_hint = str(
            payload.get("algo")
            or payload.get("algorithm")
            or algo_hint
            or "asic_sha256"
        )
    elif algo_hint is None:
        algo_hint = "asic_sha256"

    tb = TemplateBuilder(
        get_head_info=_head_info,
        get_theta=_resolve_theta,
        get_policy_roots=_policy_roots,
        get_beacon=_beacon,
    )
    tpl = tb.current_template(force=True)

    theta = tpl.theta_target_micro
    block_target = _theta_to_target(theta)
    share_target = _DEFAULT_SHARE_TARGET
    if share_microtarget is not None:
        try:
            share_target = float(share_microtarget(theta, shares_per_block=1)) / float(
                theta or 1
            )
        except Exception:
            share_target = _DEFAULT_SHARE_TARGET

    header_dict = asdict(tpl.header)
    # asdict preserves bytes; coerce to hex for JSON clients
    header_view = {
        k: (_to_hex(v) if isinstance(v, (bytes, bytearray)) else v)
        for k, v in header_dict.items()
    }

    try:
        sign_bytes = tpl.header.to_sign_bytes()
    except Exception:
        # msgspec may not be available in lightweight environments; fall back
        # to a deterministic JSON encoding with hex-encoded bytes.
        import json

        body = {
            k: (v if not isinstance(v, (bytes, bytearray)) else v.hex())
            for k, v in header_dict.items()
        }
        sign_bytes = json.dumps(body, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    job_id = uuid.uuid4().hex
    _JOB_CACHE[job_id] = {
        "template": tpl,
        "sign_bytes": sign_bytes,
        "block_target": block_target,
        "share_target": share_target,
        "height": int(tpl.height),
        "created_at": time.time(),
    }

    return {
        "jobId": job_id,
        "header": header_view,
        "thetaMicro": int(theta),
        "shareTarget": float(share_target),
        "target": hex(block_target),
        "height": int(tpl.height),
        "hints": {"mixSeed": _to_hex(tpl.mix_seed)},
        "signBytes": _to_hex(sign_bytes),
        "algo": algo_hint,
    }


@method(
    "miner.submitWork",
    desc="Validate and accept a mined solution",
    aliases=("miner_submitWork", "miner.submit_work"),
)
def miner_submit_work(*args: Any, **payload: Any) -> Dict[str, Any]:
    positional = list(args)
    if (
        not positional
        and "args" in payload
        and isinstance(payload["args"], (list, tuple))
    ):
        positional = list(payload.pop("args"))

    if (
        payload
        and len(payload) == 1
        and "payload" in payload
        and isinstance(payload["payload"], dict)
    ):
        payload = payload["payload"]
    elif payload:
        payload = payload
    elif positional:
        if len(positional) == 1 and isinstance(positional[0], dict):
            payload = positional[0]
        elif len(positional) in (2, 3):
            payload = {"jobId": positional[0], "nonce": positional[1]}
            if len(positional) == 3:
                payload["digest"] = positional[2]
        else:
            raise ValueError("params must be an object or [jobId, nonce, digest]")
    else:
        payload = {}

    if not isinstance(payload, dict):
        raise ValueError("params must be an object or array")

    job_id = payload.get("jobId") or payload.get("job_id")
    nonce_val = payload.get("nonce")
    if not job_id or nonce_val is None:
        raise ValueError("jobId and nonce are required")

    job = _JOB_CACHE.get(str(job_id))
    if job is None:
        raise ValueError("unknown or stale jobId")

    # Guard against stale work: if the head advanced to this height or beyond,
    # reject and evict the job.
    _parent_hash, head_height, _mix, _chain_id, _state_root = _head_info()
    if head_height >= int(job.get("height", 0)):
        _JOB_CACHE.pop(str(job_id), None)
        raise ValueError("stale work for current head")

    nonce = _parse_nonce(nonce_val)
    sign_bytes: bytes = job["sign_bytes"]
    digest = hashlib.sha3_256(sign_bytes + nonce).digest()
    digest_int = int.from_bytes(digest, "big")

    accepted = digest_int <= int(job["block_target"])
    block_hash = "0x" + digest.hex()
    res: Dict[str, Any] = {
        "accepted": bool(accepted),
        "jobId": job_id,
        "hash": block_hash,
        "target": hex(int(job["block_target"])),
    }

    if not accepted:
        res["reason"] = "target-not-met"
        return res

    # Record the new head locally for lightweight test chains.
    try:
        header_obj = job["template"].header  # type: ignore[index]
        header_view = asdict(header_obj)
    except Exception:
        header_obj = None
        header_view = None

    _JOB_CACHE.pop(str(job_id), None)
    _record_local_block(
        int(job.get("height", 0)), block_hash, header_view or header_obj
    )
    res.update(
        {
            "reason": None,
            "height": int(job.get("height", 0)),
            "newHead": {"height": int(job.get("height", 0)), "hash": block_hash},
        }
    )
    return res


@method("miner.mine", desc="Mine up to N blocks locally")
def miner_mine(count: int | None = None, address: str | None = None) -> dict[str, int | list[dict[str, int]]]:
    """
    Mine N blocks locally.
    
    Args:
        count: Number of blocks to mine (default: 1)
        address: Optional payout address (bech32 or hex). If omitted, uses default miner address.
        
    Returns:
        dict: {
            "mined": int,           # Number of blocks successfully mined
            "height": int,          # Final chain height after mining
            "totalReward": int,     # Total miner reward in nANM across all blocks
            "rewards": [            # Per-block reward details
                {"height": int, "reward": int},
                ...
            ]
        }
    """
    ctx = _ctx()
    try:
        head_before = ctx.get_head()
    except Exception:
        head_before = {"height": None, "hash": None}
    
    # Parse payout address if provided
    payout_address_bytes: bytes | None = None
    if address:
        try:
            # Try to decode as bech32 first
            payout_address_bytes = _decode_bech32_address(address)
            log.info(f"Using custom payout address: {address}")
        except Exception as bech32_err:
            # Try hex fallback (validate length before conversion)
            try:
                addr_str = address[2:] if address.startswith("0x") else address
                # Validate hex string is exactly 64 characters (32 bytes)
                if len(addr_str) != 64:
                    raise ValueError(f"Hex address must be exactly 64 hex characters (32 bytes), got {len(addr_str)}")
                payout_address_bytes = bytes.fromhex(addr_str)
                # No need for second validation: 64 hex chars always => exactly 32 bytes
                log.info(f"Using custom payout address (hex): {address}")
            except Exception as hex_err:
                log.warning(
                    f"Failed to decode payout address '{address}': bech32={bech32_err}, hex={hex_err}. "
                    f"Using default miner address."
                )
                payout_address_bytes = None
    
    log.info(
        "miner.mine request",
        extra={
            "db_uri": getattr(ctx, "cfg", None) and getattr(ctx.cfg, "db_uri", None),
            "chain_id": getattr(ctx, "cfg", None)
            and getattr(ctx.cfg, "chain_id", None),
            "count": count,
            "address": address,
            "head_height": head_before.get("height"),
            "head_hash": head_before.get("hash"),
        },
    )
    target = max(1, int(count or 1))
    mined = 0
    total_reward = 0
    rewards_list: list[dict[str, int]] = []
    
    for _ in range(target):
        success, reward_amount = _mine_once(payout_address=payout_address_bytes)
        if success:
            mined += 1
            total_reward += reward_amount
            # Get current head to record the height of this block
            head_current = ctx.get_head()
            current_height = int(head_current.get("height") or 0) if isinstance(head_current, dict) else 0
            rewards_list.append({"height": current_height, "reward": reward_amount})
        else:
            break
    
    head = ctx.get_head()
    height = int(head.get("height") or 0) if isinstance(head, dict) else 0
    log.info(
        "miner.mine completed",
        extra={
            "mined": mined,
            "height": height,
            "total_reward": total_reward,
            "head_hash": head.get("hash") if isinstance(head, dict) else None,
        },
    )
    return {
        "mined": mined,
        "height": height,
        "totalReward": total_reward,
        "rewards": rewards_list,
    }


@method(
    "miner.start", aliases=("miner_start", "miner.setAutoMine", "animica_setAutoMine")
)
def miner_start(enable: bool | None = None) -> bool:
    global _AUTO_MINE
    _AUTO_MINE = True if enable is None else bool(enable)
    _start_auto_task()
    return _AUTO_MINE


@method("miner.stop", aliases=("miner_stop", "animica_stopAutoMine"))
def miner_stop() -> bool:
    global _AUTO_MINE
    _AUTO_MINE = False
    if _AUTO_TASK is not None:
        _AUTO_TASK.cancel()
    return False


@method(
    "miner.submitShare",
    desc="Accept a submitted share from the mining pool",
    aliases=("miner_submitShare",),
)
def miner_submit_share(**payload: Any) -> Dict[str, Any]:
    # TODO: wire into real PoW validation once available. For now accept and echo.
    share = (
        payload.get("payload")
        if len(payload) == 1 and "payload" in payload
        else payload
    )
    return {"accepted": True, "reason": None, "share": share}


@method("miner.get_sha256_job", desc="Return a Bitcoin-style Stratum v1 job template")
def miner_get_sha256_job(params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Provide a lightweight SHA-256 template for ASIC-oriented Stratum clients."""

    params = params or {}
    address = params.get("address") or params.get("poolAddress") or ""
    parent_hash, height, _mix_seed, chain_id, _state_root = _head_info()

    prevhash = parent_hash[::-1].hex()  # Stratum v1 expects little-endian hex
    coinb1 = (
        "01000000"  # version
        + f"{height:08x}"  # fake height marker
        + f"{chain_id:08x}"  # chain id marker
    )
    coinb2 = (address or "").replace("0x", "") + "00"
    merkle_branch: list[str] = []

    bits = _DEFAULT_SHA256_BITS
    nbits = bits if isinstance(bits, str) else str(bits)
    ntime = f"{int(time.time()):08x}"
    version = "20000000"

    block_target = _bits_to_target(nbits)
    share_target = _DEFAULT_SHARE_TARGET
    if share_microtarget is not None:
        try:
            share_target = float(
                share_microtarget(_resolve_theta(), shares_per_block=1)
            ) / float(_resolve_theta() or 1)
        except Exception:
            share_target = _DEFAULT_SHARE_TARGET

    return {
        "jobId": uuid.uuid4().hex,
        "prevhash": prevhash,
        "coinb1": coinb1,
        "coinb2": coinb2,
        "merkle_branch": merkle_branch,
        "version": version,
        "nbits": nbits,
        "ntime": ntime,
        "clean_jobs": True,
        "target": hex(block_target),
        "difficulty": share_target,
        "height": height,
    }


@method(
    "miner.submit_sha256_block", desc="Accept a candidate SHA-256 block from the pool"
)
def miner_submit_sha256_block(**payload: Any) -> Dict[str, Any]:
    # Stub for integration with the Animica orchestrator. For now we simply echo success.
    block = (
        payload.get("payload")
        if len(payload) == 1 and "payload" in payload
        else payload
    )
    return {"accepted": True, "payload": block}
