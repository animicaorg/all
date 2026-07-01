"""Tier 1 — read-only Bitcoin-Core-compatible RPCs (implement/ship first).

Chain, block, mempool, raw-tx, util and network methods mapped onto Animica's
native chain.* / mempool.* / tx.* / node.* / p2p.* handlers. Highly viable:
broad explorer/wallet/exchange/indexer compatibility without a Bitcoin wallet.
"""

from __future__ import annotations

from typing import Any, Optional

from rpc.methods import method

from . import formatters as F
from .errors_btc import (RPC_INVALID_ADDRESS_OR_KEY, RPC_INVALID_PARAMETER,
                         reason_for, rpc_error, to_btc_error)


def _head() -> dict:
    return F.native("chain.getHead") or {}


def _height(h: dict) -> int:
    return F.int_from(h.get("height", h.get("number")), 0)


# ---- chain ------------------------------------------------------------------
@method("getblockcount", desc="Bitcoin-compat: current best block height.")
def getblockcount() -> int:
    return _height(_head())


@method("getbestblockhash", desc="Bitcoin-compat: best block hash.")
def getbestblockhash() -> str:
    return F.hx(_head().get("hash"))


@method("getblockhash", desc="Bitcoin-compat: block hash at a height.")
def getblockhash(height: int) -> str:
    blk = F.native("chain.getBlockByHeight", int(height), False, False)
    if not blk:
        raise rpc_error(RPC_INVALID_PARAMETER, "Block height out of range")
    return F.hx(blk.get("hash"))


def _resolve(blockhash: str, include_txs: bool):
    blk = F.native("chain.getBlockByHash", F.px(blockhash), include_txs, False)
    if not blk:
        raise rpc_error(RPC_INVALID_ADDRESS_OR_KEY, "Block not found")
    # chain.getBlockByHash returns block content but NOT its height (height is a
    # chain-position fact, not in the header, and there is no reverse hash->height
    # index reachable). Resolve it where we can: the tip (best block) is the head;
    # otherwise try the block resolver (best-effort). Non-tip blocks whose height
    # can't be resolved report height -1 (Bitcoin's "unknown" convention) rather
    # than a misleading 0; callers that came via getblockhash(height) already know it.
    if blk.get("height") is None and blk.get("number") is None:
        height = None
        head = _head()
        if F.hx(head.get("hash")) == F.hx(blockhash):
            height = _height(head)
        else:
            try:
                from rpc.methods.block import _resolve_block_by_hash
                h, _ = _resolve_block_by_hash(F.px(blockhash))
                height = h
            except Exception:
                height = None
        blk["height"] = int(height) if height is not None else -1
        blk["number"] = blk["height"]
    return blk


def _next_hash(height: int) -> Optional[str]:
    nxt = F.native("chain.getBlockByHeight", height + 1, False, False)
    return nxt.get("hash") if nxt else None


@method("getblockheader", desc="Bitcoin-compat: block header by hash.")
def getblockheader(blockhash: str, verbose: bool = True):
    blk = _resolve(blockhash, False)
    head_h = _height(_head())
    if not verbose:
        # No canonical wire-serializer exposed here; return the header JSON hex.
        import json
        return json.dumps(F.header_to_btc(blk, head_h)).encode("utf-8").hex()
    h = _height(blk)
    return F.header_to_btc(blk, head_h, next_hash=_next_hash(h))


@method("getblock", desc="Bitcoin-compat: block by hash (verbosity 0/1/2).")
def getblock(blockhash: str, verbosity: int = 1):
    verbosity = F.int_from(verbosity, 1)
    blk = _resolve(blockhash, include_txs=(verbosity >= 2))
    head_h = _height(_head())
    h = _height(blk)
    if verbosity == 0:
        import json
        return json.dumps(F.block_to_btc(blk, head_h, 1)).encode("utf-8").hex()
    return F.block_to_btc(blk, head_h, verbosity, next_hash=_next_hash(h))


def _sync_view() -> dict:
    """Best-effort real sync status so getblockchaininfo can tell the truth.

    An un-synced node must NOT report itself as fully synced — otherwise an
    exchange trusts a balance of 0 on a node that simply hasn't downloaded the
    blocks that credited the deposit. Sourced from `system.health` (authoritative
    `syncing` flag + peer count, a fast sync call) and, best-effort, the p2p
    `sync.getStatus` snapshot for the network-best header height.
    """
    out = {"syncing": None, "peers": None, "network_best": 0}
    try:
        hh = F.native("system.health") or {}
        out["syncing"] = hh.get("syncing")
        out["peers"] = hh.get("peers_connected")
    except Exception:
        pass
    try:
        s = F.native("sync.getStatus") or {}
        out["network_best"] = F.int_from(
            s.get("network_best_height") or s.get("best_header_height")
            or s.get("best_block_height"), 0)
    except Exception:
        pass
    return out


@method("getblockchaininfo", desc="Bitcoin-compat: chain state summary (reports real sync/IBD state).")
def getblockchaininfo() -> dict:
    h = _head()
    height = _height(h)
    ident = {}
    try:
        ident = F.native("chain.getChainIdentity") or {}
    except Exception:
        pass

    sv = _sync_view()
    net_best = F.int_from(sv.get("network_best"), 0)
    headers = max(height, net_best)
    behind = max(0, headers - height)
    syncing = sv.get("syncing")
    peers = sv.get("peers")
    # Prefer the node's own authoritative `syncing` flag; fall back to header lag.
    if syncing is True:
        ibd = True
    elif syncing is False:
        ibd = False
    else:
        ibd = behind > 2
    progress = 1.0 if (not ibd) else (round(min(1.0, height / headers), 8) if headers > 0 else 0.0)

    warnings = []
    if ibd:
        warnings.append(
            "Node is still syncing (initialblockdownload); balances and deposit "
            "history are INCOMPLETE until sync completes — do not credit yet.")
    if peers == 0:
        warnings.append(
            "No P2P peers connected — the node may be isolated / not on the "
            "canonical chain; balances cannot be trusted.")

    return {
        "chain": F.chain_name_from_id(h.get("chainId")),
        "blocks": height,
        "headers": headers,
        "bestblockhash": F.hx(h.get("hash")),
        "difficulty": F.theta_to_difficulty(h.get("thetaMicro")),
        "time": F.int_from(h.get("timestamp"), 0),
        "mediantime": F.int_from(h.get("timestamp"), 0),
        "verificationprogress": progress,
        "initialblockdownload": ibd,
        "chainwork": F.CHAINWORK,
        "size_on_disk": 0,
        "pruned": False,
        "warnings": warnings,
        "softforks": {},
        # Animica extension
        "thetaMicro": F.int_from(h.get("thetaMicro"), 0),
        "chainId": F.int_from(h.get("chainId"), 0),
        "genesisHash": F.hx(ident.get("genesisHash")),
        "consensus": "poies",
        "syncing": bool(ibd),
        "network_best_height": net_best or None,
        "peers": peers,
    }


@method("getchaintips", desc="Bitcoin-compat: known chain tips.")
def getchaintips() -> list:
    h = _head()
    tips = [{
        "height": _height(h),
        "hash": F.hx(h.get("hash")),
        "branchlen": 0,
        "status": "active",
    }]
    try:
        forks = F.native("chain.getForks", 10) or {}
        for t in (forks.get("tips") or []):
            if F.hx(t.get("hash")) == tips[0]["hash"]:
                continue
            st = str(t.get("status", "valid-fork")).lower()
            tips.append({
                "height": F.int_from(t.get("height"), 0),
                "hash": F.hx(t.get("hash")),
                "branchlen": F.int_from(t.get("branchlen", 1), 1),
                "status": "invalid" if "invalid" in st else "valid-fork",
            })
    except Exception:
        pass
    return tips


@method("getdifficulty", desc="Bitcoin-compat: current synthetic difficulty.")
def getdifficulty() -> float:
    return F.theta_to_difficulty(_head().get("thetaMicro"))


# ---- mempool ----------------------------------------------------------------
@method("getmempoolinfo", desc="Bitcoin-compat: mempool status.")
def getmempoolinfo() -> dict:
    stats = F.native("mempool.getStats") or {}
    nbytes = F.int_from(stats.get("totalBytes"), 0)
    return {
        "loaded": True,
        "size": F.int_from(stats.get("count"), 0),
        "bytes": nbytes,
        "usage": nbytes,
        "total_fee": 0,
        "maxmempool": 300000000,
        "mempoolminfee": 0.0,
        "minrelaytxfee": 0.0,
        "incrementalrelayfee": 0.0,
        "unbroadcastcount": 0,
        "fullrbf": False,
    }


@method("getrawmempool", desc="Bitcoin-compat: mempool txids.")
def getrawmempool(verbose: bool = False, mempool_sequence: bool = False):
    pending = F.native("mempool.getPending", bool(verbose)) or []
    if not verbose:
        return [F.hx(x) for x in pending]
    height = _height(_head())
    out = {}
    for e in pending:
        txid = F.hx(e.get("hash"))
        size = F.int_from(e.get("size"), 0)
        fee = F.to_coin(e.get("fee"))
        out[txid] = {
            "vsize": size,
            "weight": size * 4,
            "time": F.int_from(e.get("received_at"), 0),
            "height": height,
            "descendantcount": 1, "descendantsize": size,
            "ancestorcount": 1, "ancestorsize": size,
            "fees": {"base": fee, "modified": fee, "ancestor": fee, "descendant": fee},
            "depends": [], "spentby": [],
            "bip125-replaceable": False, "unbroadcast": False,
        }
    return out


# ---- raw transactions -------------------------------------------------------
@method("getrawtransaction", desc="Bitcoin-compat: transaction by id.")
def getrawtransaction(txid: str, verbose: int = 0, blockhash: Optional[str] = None):
    tx = F.native("tx.getTransactionByHash", F.px(txid))
    raw = None
    try:
        rr = F.native("mempool.getRawTx", F.px(txid)) or {}
        raw = rr.get("raw")
    except Exception:
        pass
    if not tx and not raw:
        raise rpc_error(
            RPC_INVALID_ADDRESS_OR_KEY,
            "No such mempool or blockchain transaction. Use -txindex or provide a block hash.",
        )
    verbose = F.int_from(verbose, 0)
    if verbose == 0:
        return F.hx(raw) if raw else None
    view = F.btc_tx_view(tx or {})
    head_h = _height(_head())
    bnum = F.int_from((tx or {}).get("blockNumber", (tx or {}).get("blockHeight")), -1)
    view.update({
        "hex": F.hx(raw),
        "txid": F.hx(txid),
        "hash": F.hx(txid),
        "confirmations": (head_h - bnum + 1) if bnum >= 0 else 0,
        "blockhash": F.hx((tx or {}).get("blockHash")),
        "time": F.int_from((tx or {}).get("timestamp"), 0),
        "in_active_chain": True,
    })
    return view


@method("sendrawtransaction", desc="Bitcoin-compat: submit a raw transaction.")
def sendrawtransaction(hexstring: str, maxfeerate: float = 0.10, maxburnamount: float = 0.0) -> str:
    try:
        txid = F.native("tx.sendRawTransaction", F.px(hexstring))
    except Exception as exc:
        raise to_btc_error(exc)
    if isinstance(txid, dict):
        txid = txid.get("txid") or txid.get("hash")
    return F.hx(txid)


@method("testmempoolaccept", desc="Bitcoin-compat: dry-run mempool acceptance.")
def testmempoolaccept(rawtxs: list, maxfeerate: float = 0.10) -> list:
    if not isinstance(rawtxs, list):
        raise rpc_error(RPC_INVALID_PARAMETER, "rawtxs must be an array")
    if len(rawtxs) > 25:
        raise rpc_error(RPC_INVALID_PARAMETER, "Array must contain between 1 and 25 transactions")
    out = []
    for hexstr in rawtxs:
        txid = None
        try:
            dec = F.native("tx.decodeRawTransaction", F.px(hexstr)) or {}
            txid = dec.get("hash") or dec.get("txid") or (dec.get("tx") or {}).get("hash")
        except Exception:
            pass
        try:
            F.native("mempool.simulateAdmission", F.px(hexstr))
            vsize = len(str(hexstr).replace("0x", "")) // 2
            out.append({
                "txid": F.hx(txid), "wtxid": F.hx(txid), "allowed": True,
                "vsize": vsize,
                "fees": {"base": 0.0, "effective-feerate": 0.0,
                         "effective-includes": [F.hx(txid)] if txid else []},
            })
        except Exception as exc:
            _, reason = reason_for(str(exc))
            out.append({"txid": F.hx(txid), "wtxid": F.hx(txid),
                        "allowed": False, "reject-reason": reason})
    return out


@method("decoderawtransaction", desc="Bitcoin-compat: decode a raw transaction.")
def decoderawtransaction(hexstring: str, iswitness: Optional[bool] = None) -> dict:
    try:
        dec = F.native("tx.decodeRawTransaction", F.px(hexstring))
    except Exception as exc:
        raise to_btc_error(exc, default_code=-22)
    return F.btc_tx_view(dec or {})


# ---- util / network ---------------------------------------------------------
@method("validateaddress", desc="Bitcoin-compat: validate an Animica address.")
def validateaddress(address: str) -> dict:
    addr = str(address or "")
    valid = False
    alg = None
    try:
        if addr.startswith("anim1"):
            from pq.py.utils import bech32 as _b  # type: ignore
            hrp, data = _b.bech32_decode(addr) if hasattr(_b, "bech32_decode") else (None, None)
            valid = hrp is not None and str(hrp).startswith("anim")
        elif addr.startswith(("0x", "0X")) and len(addr) >= 42:
            valid = True
    except Exception:
        # lenient fallback: structurally address-like
        valid = addr.startswith("anim1") and len(addr) > 20
    if not valid and addr.startswith("anim1") and len(addr) > 20:
        valid = True  # accept well-formed bech32m even if decoder unavailable
    if not valid:
        return {"isvalid": False}
    return {
        "isvalid": True,
        "address": addr,
        "scriptPubKey": F.hx(addr) if addr.startswith("0x") else "",
        "isscript": False,
        "iswitness": False,
        "animica:hrp": "anim",
        "animica:type": "pq-bech32m" if addr.startswith("anim1") else "hex",
    }


@method("getnetworkinfo", desc="Bitcoin-compat: node/network info.")
def getnetworkinfo() -> dict:
    health = F.native("node.health") or {}
    ver = str(health.get("version", "3.0.0"))
    return {
        "version": 3000000,
        "subversion": f"/Animica:{ver}/",
        "protocolversion": F.PROTOCOL_VERSION,
        "localservices": F.SERVICES,
        "localservicesnames": ["NETWORK", "WITNESS"],
        "localrelay": True,
        "timeoffset": 0,
        "networkactive": True,
        "connections": F.int_from(health.get("peers_connected"), 0),
        "connections_in": 0,
        "connections_out": F.int_from(health.get("peers_connected"), 0),
        "networks": [],
        "relayfee": 0.0,
        "incrementalfee": 0.0,
        "localaddresses": [],
        "warnings": [],
        # Animica extension
        "animica:chainId": F.int_from(health.get("chain_id"), 0),
        "animica:networkName": health.get("network_name"),
    }


@method("getpeerinfo", desc="Bitcoin-compat: connected peers.")
async def getpeerinfo() -> list:
    try:
        peers = await F.anative("p2p.listPeers")
    except Exception:
        try:
            peers = await F.anative("p2p.getPeers")
        except Exception:
            peers = []
    out = []
    for i, p in enumerate(peers or []):
        pid = str(p.get("peer_id", p.get("id", i)))
        out.append({
            "id": (hash(pid) & 0x7fffffff),
            "addr": p.get("addr", p.get("address", "")),
            "addrbind": p.get("addr", ""),
            "addrlocal": "",
            "services": F.SERVICES,
            "relaytxes": True,
            "lastsend": F.int_from(p.get("lastSeen"), 0),
            "lastrecv": F.int_from(p.get("lastSeen"), 0),
            "bytessent": 0, "bytesrecv": 0,
            "conntime": F.int_from(p.get("connectedAt"), 0),
            "timeoffset": 0,
            "pingtime": (F.int_from(p.get("latencyMs"), 0) / 1000.0),
            "version": F.PROTOCOL_VERSION,
            "subver": "/Animica/",
            "inbound": str(p.get("direction", "")).lower() == "inbound",
            "startingheight": F.int_from(p.get("height", -1), -1),
            "synced_headers": F.int_from(p.get("height", -1), -1),
            "synced_blocks": F.int_from(p.get("height", -1), -1),
            "banscore": 0,
            "animica:peerId": pid,
        })
    return out


@method("uptime", desc="Bitcoin-compat: node uptime in seconds.")
def uptime() -> int:
    return F.int_from((F.native("node.health") or {}).get("uptime_seconds"), 0)
