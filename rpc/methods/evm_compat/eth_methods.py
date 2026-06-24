"""eth_* read methods (Phase 1 + wallet-connect): chain, blocks, accounts, txs."""

from __future__ import annotations

from typing import Any, Optional

from rpc.methods import method

from . import formatters as F
from .bridge import anim_to_evm, evm_to_anim
from .errors_evm import RPC_INVALID_PARAMS, rpc_error


def _chain_id() -> int:
    # The EVM facade advertises its OWN dedicated chain id (default 149), NOT
    # Animica's native chain id (1 = Ethereum mainnet). The native chain is
    # never reset for EVM compat; only what we report here changes.
    return F.evm_chain_id()


def _resolve_addr(addr: Any) -> Optional[str]:
    """0x EVM alias / relayer-managed account / anim1 -> Animica address (or None)."""
    a = str(addr or "")
    if a.startswith("anim1"):
        return a
    if a.startswith(("0x", "0X")):
        bound = evm_to_anim(a)
        if bound:
            return bound
        # With the relayer enabled, an EVM address may be a managed account whose
        # home anim1 holds its balance.
        try:
            from .relayer import is_enabled, get_account
            if is_enabled():
                acct = get_account(a.lower())
                if acct:
                    return acct.anim1
        except Exception:
            pass
        return None
    return None


# ---- chain / block ----------------------------------------------------------
@method("eth_chainId", desc="EVM-compat: chain id (hex). Override: ANIMICA_EVM_CHAIN_ID.")
def eth_chainId() -> str:
    return F.q(_chain_id())


@method("eth_blockNumber", desc="EVM-compat: latest block number (hex).")
def eth_blockNumber() -> str:
    return F.q(F.head_height())


@method("eth_getBlockByNumber", desc="EVM-compat: block by number/tag.")
def eth_getBlockByNumber(tag: Any, full_transactions: bool = False) -> Optional[dict]:
    h = F.resolve_block_tag(tag)
    if h is None:
        return None
    blk = F.native("chain.getBlockByHeight", int(h), bool(full_transactions), False)
    if not blk:
        return None
    return F.block_to_eth(blk, int(h), bool(full_transactions))


@method("eth_getBlockByHash", desc="EVM-compat: block by hash.")
def eth_getBlockByHash(block_hash: str, full_transactions: bool = False) -> Optional[dict]:
    blk = F.native("chain.getBlockByHash", F.hexhash(block_hash), bool(full_transactions), False)
    if not blk:
        return None
    height = F.from_q(blk.get("height", blk.get("number")), None)
    if height is None:
        head = F.native("chain.getHead") or {}
        if F.hexhash(head.get("hash")) == F.hexhash(block_hash):
            height = F.from_q(head.get("height"), 0)
        else:
            try:
                from rpc.methods.block import _resolve_block_by_hash
                rh, _ = _resolve_block_by_hash(F.hexhash(block_hash))
                height = rh if rh is not None else 0
            except Exception:
                height = 0
    return F.block_to_eth(blk, int(height), bool(full_transactions))


@method("eth_getBlockTransactionCountByNumber", desc="EVM-compat: tx count in a block.")
def eth_getBlockTransactionCountByNumber(tag: Any) -> Optional[str]:
    h = F.resolve_block_tag(tag)
    if h is None:
        return None
    blk = F.native("chain.getBlockByHeight", int(h), False, False) or {}
    txs = blk.get("txs") or blk.get("transactions") or []
    return F.q(len(txs))


@method("eth_getBlockTransactionCountByHash", desc="EVM-compat: tx count in a block by hash.")
def eth_getBlockTransactionCountByHash(block_hash: str) -> Optional[str]:
    blk = F.native("chain.getBlockByHash", F.hexhash(block_hash), False, False) or {}
    txs = blk.get("txs") or blk.get("transactions") or []
    return F.q(len(txs))


# ---- accounts ---------------------------------------------------------------
@method("eth_getBalance", desc="EVM-compat: account balance in nANM (wei-style hex).")
def eth_getBalance(address: str, tag: Any = "latest") -> str:
    anim = _resolve_addr(address)
    if not anim:
        return F.q(0)
    try:
        return F.q(F.from_q(F.native("state.getBalance", anim), 0))
    except Exception:
        return F.q(0)


@method("eth_getTransactionCount", desc="EVM-compat: account nonce (hex).")
def eth_getTransactionCount(address: str, tag: Any = "latest") -> str:
    # Relayer-managed EVM accounts use a relayer-maintained nonce (native v2 txs
    # have no nonce; this is the EVM-facing sequence wallets need to sign).
    a = str(address or "")
    if a.startswith(("0x", "0X")):
        try:
            from .relayer import is_enabled, get_account, get_nonce
            if is_enabled() and get_account(a.lower()):
                return F.q(get_nonce(a.lower()))
        except Exception:
            pass
    anim = _resolve_addr(address)
    if not anim:
        return F.q(0)
    pending = str(tag).lower() == "pending"
    for m in (["state.getPendingNonce"] if pending else []) + ["state.getNonce", "state.getNextNonce"]:
        try:
            return F.q(F.from_q(F.native(m, anim), 0))
        except Exception:
            continue
    return F.q(0)


@method("eth_getCode", desc="EVM-compat: contract code at address (Animica uses Python-VM, not EVM bytecode).")
def eth_getCode(address: str, tag: Any = "latest") -> str:
    return "0x"


@method("eth_getStorageAt", desc="EVM-compat: storage slot (not EVM-addressable; 0x0).")
def eth_getStorageAt(address: str, position: str, tag: Any = "latest") -> str:
    return F.ZERO32


@method("eth_accounts", desc="EVM-compat: node-managed EVM accounts (none; use a wallet).")
def eth_accounts() -> list:
    return []


# ---- transactions (read) ----------------------------------------------------
def _relayed_record(tx_hash: Any):
    """If tx_hash is an EVM hash the relayer produced, return its map row + native tx."""
    try:
        from .relayer import is_enabled, lookup_by_evm_hash
        if not is_enabled():
            return None, None
        rec = lookup_by_evm_hash(F.hexhash(tx_hash))
        if not rec:
            return None, None
        native = F.native("tx.getTransactionByHash", rec["native_txid"])
        return rec, native
    except Exception:
        return None, None


def _apply_evm_identity(view: dict, rec: dict) -> dict:
    """Substitute the EVM-facing identity fields onto a native-derived tx/receipt."""
    view = dict(view)
    view["hash"] = F.hexhash(rec["evm_hash"])
    view["from"] = rec["evm_from"]
    view["to"] = rec.get("evm_to")
    view["nonce"] = F.q(rec["evm_nonce"])
    view["value"] = F.q(rec["value"])
    view["chainId"] = F.q(rec["chain_id"])
    return view


@method("eth_getTransactionByHash", desc="EVM-compat: transaction by hash.")
def eth_getTransactionByHash(tx_hash: str) -> Optional[dict]:
    rec, native = _relayed_record(tx_hash)
    if rec is not None:
        base = F.tx_to_eth(native) if native else {}
        return _apply_evm_identity(base, rec)
    tx = F.native("tx.getTransactionByHash", F.hexhash(tx_hash))
    if not tx:
        return None
    return F.tx_to_eth(tx)


@method("eth_getTransactionReceipt", desc="EVM-compat: transaction receipt.")
def eth_getTransactionReceipt(tx_hash: str) -> Optional[dict]:
    rec, native = _relayed_record(tx_hash)
    if rec is not None:
        if not native:
            return None  # relayer recorded it but the native tx isn't mined yet
        try:
            status = F.native("tx.getTransactionStatus", rec["native_txid"]) or {}
        except Exception:
            status = {}
        bnum = F.from_q(native.get("blockNumber", native.get("blockHeight")), -1)
        if bnum < 0:
            return None
        receipt = F.receipt_to_eth(native, status, F.head_height())
        return _apply_evm_identity(receipt, rec)
    tx = F.native("tx.getTransactionByHash", F.hexhash(tx_hash))
    if not tx:
        return None
    try:
        status = F.native("tx.getTransactionStatus", F.hexhash(tx_hash)) or {}
    except Exception:
        status = {}
    head_h = F.head_height()
    bnum = F.from_q(tx.get("blockNumber", tx.get("blockHeight")), -1)
    if bnum < 0:
        return None  # Ethereum returns null for not-yet-mined txs
    return F.receipt_to_eth(tx, status, head_h)


@method("eth_getTransactionByBlockNumberAndIndex", desc="EVM-compat: tx by block number + index.")
def eth_getTransactionByBlockNumberAndIndex(tag: Any, index: str) -> Optional[dict]:
    h = F.resolve_block_tag(tag)
    if h is None:
        return None
    blk = F.native("chain.getBlockByHeight", int(h), True, False) or {}
    txs = blk.get("txs") or blk.get("transactions") or []
    i = F.from_q(index, 0)
    if i >= len(txs) or not isinstance(txs[i], dict):
        return None
    return F.tx_to_eth(txs[i], blk.get("hash"), int(h), i)


# ---- node status ------------------------------------------------------------
@method("eth_syncing", desc="EVM-compat: sync status (false or progress object).")
def eth_syncing():
    h = F.native("node.health") or {}
    if not h.get("syncing"):
        return False
    cur = F.from_q(h.get("head_height"), 0)
    return {"startingBlock": F.q(0), "currentBlock": F.q(cur), "highestBlock": F.q(cur)}


@method("eth_mining", desc="EVM-compat: whether the node is mining.")
def eth_mining() -> bool:
    try:
        st = F.native("miner.status") or {}
        return bool(st.get("enabled", st.get("running", False)))
    except Exception:
        return False


@method("eth_hashrate", desc="EVM-compat: node hashrate (hex).")
def eth_hashrate() -> str:
    return F.q(0)


@method("eth_coinbase", desc="EVM-compat: coinbase address (none surfaced).")
def eth_coinbase() -> str:
    return F.ZERO20


@method("eth_protocolVersion", desc="EVM-compat: protocol version.")
def eth_protocolVersion() -> str:
    return "0x41"
