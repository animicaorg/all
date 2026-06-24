"""Formatters + helpers for the Ethereum/EVM-compatible RPC facade.

Maps Animica's native shapes to Ethereum JSON-RPC shapes (eth_*/net_*/web3_*),
targeting the ethers.js/web3.js/MetaMask wire format. This is an
RPC-COMPATIBLE FACADE, not EVM execution: Animica stays a post-quantum PoIES
useful-work L1; only the RPC surface mimics Ethereum.

Conventions (strict Ethereum JSON-RPC):
  - QUANTITY: 0x-prefixed compact hex, no leading zeros, "0x0" for zero -> q()
  - DATA: 0x-prefixed, even number of hex digits -> d()
  - Addresses: 20-byte 0x (Ethereum) bridged to anim1 (Animica) via bridge.py.
  - Hashes: 32-byte 0x (Animica SHA3-256 hashes are already 0x+64hex; reused).
  - Units: Animica balances are nANM; Ethereum wei-style is returned as the raw
    nANM integer (1 ANM = 1e9 nANM, exposed as the smallest unit, like wei).
"""

from __future__ import annotations

import inspect
import os
from typing import Any, Optional

CHAIN_UNKNOWN = -1

# EVM-facade chain id. Animica's NATIVE chain id is 1 (and stays 1 — the chain is
# never reset for EVM compatibility). But chain id 1 is Ethereum mainnet, so the
# EVM facade advertises its OWN dedicated id for EIP-155 replay protection and a
# clean MetaMask "add network". 149 is the lowest unregistered EIP-155 chain id
# (everything 1..148 is taken on chainid.network). Override: ANIMICA_EVM_CHAIN_ID.
ANIMICA_EVM_DEFAULT_CHAIN_ID = 149
ZERO32 = "0x" + "00" * 32
ZERO20 = "0x" + "00" * 20
EMPTY_BLOOM = "0x" + "00" * 256
# keccak256("") — Ethereum's empty-uncles hash; harmless constant for clients.
SHA3_UNCLES = "0x1dcc4de8dec75d7aab85b567b6ccd41ad312451b948a7413f0a142fd40d49347"
GAS_LIMIT = 30_000_000
DEFAULT_GAS = 21_000


# ---- hex quantity / data ------------------------------------------------------
def q(n: Any) -> str:
    """QUANTITY: 0x-prefixed compact hex (no leading zeros)."""
    if n is None:
        return "0x0"
    if isinstance(n, str):
        if n.startswith(("0x", "0X")):
            try:
                n = int(n, 16)
            except ValueError:
                return "0x0"
        else:
            try:
                n = int(n)
            except ValueError:
                return "0x0"
    return "0x" + format(int(n), "x")


def d(b: Any) -> str:
    """DATA: 0x-prefixed, even hex length."""
    if b is None:
        return "0x"
    s = str(b)
    if s.startswith(("0x", "0X")):
        s = s[2:]
    if len(s) % 2:
        s = "0" + s
    return "0x" + s


def from_q(v: Any, default: int = 0) -> int:
    try:
        if isinstance(v, str):
            return int(v, 16) if v.startswith(("0x", "0X")) else int(v)
        return int(v)
    except (TypeError, ValueError):
        return default


def evm_chain_id() -> int:
    """The EVM facade's dedicated chain id (default 149).

    Decoupled from Animica's native chain id (1) on purpose: id 1 is Ethereum
    mainnet, so advertising it to EVM wallets collides in chain-lists and weakens
    EIP-155 replay protection. This changes ONLY what the facade reports — the
    underlying chain, its blocks, and its native chain id are untouched.
    Override with the ANIMICA_EVM_CHAIN_ID env var.
    """
    ov = os.environ.get("ANIMICA_EVM_CHAIN_ID")
    if ov:
        return from_q(ov, ANIMICA_EVM_DEFAULT_CHAIN_ID)
    return ANIMICA_EVM_DEFAULT_CHAIN_ID


def hexhash(s: Any) -> Optional[str]:
    """Animica 0x-hash passthrough (already 0x+64hex)."""
    if s is None:
        return None
    s = str(s)
    return s if s.startswith(("0x", "0X")) else ("0x" + s)


# ---- keccak (Ethereum address derivation, web3_sha3) --------------------------
def keccak256(data: bytes) -> bytes:
    try:
        from eth_utils import keccak  # ships with web3/eth-account (base dep)
        return keccak(data)
    except Exception:
        # Fallback: pysha3 / hashlib keccak if available (NOT NIST sha3).
        import hashlib
        try:
            h = hashlib.new("keccak_256")
            h.update(data)
            return h.digest()
        except Exception:
            # last resort: NIST sha3_256 (wrong for Ethereum, but never crash)
            return hashlib.sha3_256(data).digest()


# ---- native-handler invocation (by registered JSON-RPC name) ------------------
_REG = None


def _registry():
    global _REG
    if _REG is None:
        from rpc import methods as _m
        _m.ensure_loaded()
        _REG = _m.get_methods()
    return _REG


def native(name: str, *args, **kwargs) -> Any:
    spec = _registry().get(name)
    if spec is None:
        raise KeyError(f"native method not registered: {name}")
    result = spec.func(*args, **kwargs)
    if inspect.isawaitable(result):
        import asyncio
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(result)
        finally:
            loop.close()
    return result


async def anative(name: str, *args, **kwargs) -> Any:
    spec = _registry().get(name)
    if spec is None:
        raise KeyError(f"native method not registered: {name}")
    result = spec.func(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


# ---- block tag resolution -----------------------------------------------------
def head_height() -> int:
    return from_q((native("chain.getHead") or {}).get("height"), 0)


def resolve_block_tag(tag: Any) -> Optional[int]:
    """latest/earliest/pending/safe/finalized/0xhex -> height int (None=unknown)."""
    if tag is None:
        return head_height()
    if isinstance(tag, dict):
        tag = tag.get("blockNumber") or tag.get("blockHash")
    s = str(tag).lower()
    if s in ("latest", "pending", "safe", "finalized"):
        return head_height()
    if s == "earliest":
        return 0
    return from_q(s, None)  # type: ignore[arg-type]


# ---- block / tx / receipt formatting -----------------------------------------
def _roots(view: dict) -> dict:
    r = view.get("roots") or {}
    return {k: (r.get(k) or view.get(k)) for k in
            ("stateRoot", "txsRoot", "receiptsRoot", "proofsRoot", "daRoot")}


def block_to_eth(view: dict, height: int, full_txs: bool, miner_addr: str = ZERO20) -> dict:
    roots = _roots(view)
    txs = view.get("txs") or view.get("transactions") or []
    size = from_q(view.get("size"), 0) or (len(txs) * 256 + 256)
    out = {
        "number": q(height),
        "hash": hexhash(view.get("hash")),
        "parentHash": hexhash(view.get("parentHash")) or ZERO32,
        "nonce": "0x" + format(from_q(view.get("nonce"), 0), "016x"),
        "sha3Uncles": SHA3_UNCLES,
        "logsBloom": EMPTY_BLOOM,
        "transactionsRoot": hexhash(roots.get("txsRoot")) or ZERO32,
        "stateRoot": hexhash(roots.get("stateRoot")) or ZERO32,
        "receiptsRoot": hexhash(roots.get("receiptsRoot")) or ZERO32,
        "miner": miner_addr,
        "difficulty": q(from_q(view.get("thetaMicro"), 0)),
        "totalDifficulty": q(0),
        "extraData": "0x",
        "size": q(size),
        "gasLimit": q(GAS_LIMIT),
        "gasUsed": q(0),
        "timestamp": q(view.get("timestamp")),
        "uncles": [],
        "baseFeePerGas": q(0),
        "mixHash": hexhash(view.get("mixSeed")) or ZERO32,
    }
    if full_txs:
        out["transactions"] = [tx_to_eth(t, view.get("hash"), height, i)
                               for i, t in enumerate(txs) if isinstance(t, dict)]
        if not out["transactions"]:
            out["transactions"] = [hexhash(t.get("hash") if isinstance(t, dict) else t) for t in txs]
    else:
        out["transactions"] = [hexhash(t.get("hash") if isinstance(t, dict) else t) for t in txs]
    return out


def tx_to_eth(tx: dict, block_hash: Any = None, block_num: Any = None, index: int = 0) -> dict:
    body = tx.get("body") or tx.get("tx", {}).get("body") or tx
    txid = tx.get("hash") or tx.get("txid") or tx.get("txHash")
    frm = body.get("from") or body.get("sender")
    to = body.get("to") or body.get("recipient")
    # Bridge Animica addresses -> 20-byte 0x EVM aliases for display.
    from .bridge import anim_to_evm
    return {
        "blockHash": hexhash(tx.get("blockHash") or block_hash),
        "blockNumber": q(tx.get("blockNumber", block_num)) if (tx.get("blockNumber") is not None or block_num is not None) else None,
        "from": anim_to_evm(frm) if frm else ZERO20,
        "gas": q(DEFAULT_GAS),
        "gasPrice": q(1),
        "maxFeePerGas": q(1),
        "maxPriorityFeePerGas": q(0),
        "hash": hexhash(txid),
        "input": d(body.get("data") or "0x"),
        "nonce": q(body.get("nonce")),
        "to": anim_to_evm(to) if to else None,
        "transactionIndex": q(from_q(tx.get("index", index), index)),
        "value": q(body.get("value", body.get("amount", 0))),
        "type": "0x0",
        "chainId": q(body.get("chain_id", body.get("chainId", 0))),
        "v": "0x0", "r": ZERO32, "s": ZERO32,
        # Animica extension (clients ignore unknown keys)
        "animica:from": frm,
        "animica:to": to,
        "animica:kind": body.get("kind"),
    }


def receipt_to_eth(tx: dict, status: dict, head_h: int) -> dict:
    body = tx.get("body") or tx
    txid = tx.get("hash") or tx.get("txHash")
    from .bridge import anim_to_evm
    frm = body.get("from")
    to = body.get("to")
    bnum = from_q(tx.get("blockNumber", tx.get("blockHeight")), -1)
    ok = str(status.get("status", "")).lower() in ("confirmed", "included", "success", "ok") or bnum >= 0
    return {
        "transactionHash": hexhash(txid),
        "transactionIndex": q(from_q(tx.get("index"), 0)),
        "blockHash": hexhash(tx.get("blockHash")) or ZERO32,
        "blockNumber": q(bnum if bnum >= 0 else 0),
        "from": anim_to_evm(frm) if frm else ZERO20,
        "to": anim_to_evm(to) if to else None,
        "cumulativeGasUsed": q(DEFAULT_GAS),
        "gasUsed": q(DEFAULT_GAS),
        "contractAddress": None,
        "logs": [],
        "logsBloom": EMPTY_BLOOM,
        "status": "0x1" if ok else "0x0",
        "effectiveGasPrice": q(1),
        "type": "0x0",
    }
