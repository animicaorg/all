"""Tier 3 — Bitcoin-Core-compatible mining RPCs (PoIES adapter).

Animica's PoIES/useful-work consensus is not PoW/nBits, so templates carry an
``animica:*`` extension with the real consensus fields (thetaMicro, templateId,
stateRoot) inside an otherwise Bitcoin-shaped response. Useful for pool/miner
tooling that speaks getblocktemplate/submitblock.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from rpc.methods import method

from . import formatters as F
from .errors_btc import rpc_error, to_btc_error, RPC_INVALID_PARAMETER


@method("getmininginfo", desc="Bitcoin-compat: mining status.")
def getmininginfo() -> dict:
    head = F.native("chain.getHead") or {}
    height = F.int_from(head.get("height", head.get("number")), 0)
    try:
        hr = (F.native("chain.getNetworkHashrate", 120) or {}).get("hashrate_hsps", 0.0)
    except Exception:
        hr = 0.0
    try:
        pooled = F.int_from((F.native("mempool.getStats") or {}).get("count"), 0)
    except Exception:
        pooled = 0
    status = {}
    try:
        status = F.native("miner.status") or F.native("miner.getStatus") or {}
    except Exception:
        pass
    return {
        "blocks": height,
        "currentblockweight": None,
        "currentblocktx": None,
        "difficulty": F.theta_to_difficulty(head.get("thetaMicro")),
        "networkhashps": hr,
        "pooledtx": pooled,
        "chain": F.chain_name_from_id(head.get("chainId")),
        "warnings": [],
        # Animica extension
        "animica:consensus": "poies",
        "animica:thetaMicro": F.int_from(head.get("thetaMicro"), 0),
        "animica:hashrate_unit": "HShare/s",
        "animica:minerEnabled": bool(status.get("enabled", status.get("running", False))),
    }


@method("getblocktemplate", desc="Bitcoin-compat: PoIES block template (Bitcoin-shaped).")
def getblocktemplate(template_request: Optional[dict] = None, ctx=None) -> dict:
    req = template_request or {}
    addr = req.get("coinbaseaddress") or req.get("address")
    try:
        tpl = F.native("miner.getBlockTemplate", **({"address": addr} if addr else {}))
    except Exception as exc:
        raise to_btc_error(exc)
    tpl = tpl or {}
    height = F.int_from(tpl.get("height", tpl.get("blockNumber")), 0)
    theta = tpl.get("thetaMicro", tpl.get("difficulty"))
    diff = F.theta_to_difficulty(theta)
    txs = tpl.get("transactions") or []
    btc_txs = []
    for t in txs:
        size = F.int_from(t.get("size", t.get("len")), 0)
        btc_txs.append({
            "data": F.hx(t.get("raw", t.get("data"))),
            "txid": F.hx(t.get("hash", t.get("txid"))),
            "hash": F.hx(t.get("hash", t.get("txid"))),
            "depends": [], "fee": F.int_from(t.get("fee"), 0), "sigops": 0,
            "weight": size * 4,
        })
    ts = F.int_from(tpl.get("timestamp"), 0)
    return {
        "version": F.COMPAT_VERSION,
        "rules": [], "vbavailable": {}, "vbrequired": 0,
        "previousblockhash": F.hx(tpl.get("parentHash")),
        "transactions": btc_txs,
        "coinbaseaux": {},
        "coinbasevalue": F.int_from(tpl.get("coinbaseValue", tpl.get("reward")), 0),
        "longpollid": str(tpl.get("template_id", "")),
        "target": F.difficulty_to_target_hex(diff),
        "mintime": ts,
        "mutable": ["time", "transactions", "prevblock"],
        "noncerange": "00000000ffffffff",
        "sigoplimit": 80000, "sizelimit": 4000000, "weightlimit": 4000000,
        "curtime": ts, "bits": F.COMPAT_BITS, "height": height,
        # Animica extension — the real consensus inputs
        "animica:consensus": "poies",
        "animica:thetaMicro": F.int_from(theta, 0),
        "animica:templateId": tpl.get("template_id"),
        "animica:stateRoot": tpl.get("stateRoot"),
        "animica:coinbase": tpl.get("coinbase"),
        "animica:expiresAt": tpl.get("template_expires_at"),
    }


@method("submitblock", desc="Bitcoin-compat: submit a solved block (Animica payload).")
def submitblock(hexdata: str, dummy: str = "", ctx=None):
    # Animica blocks are not Bitcoin-serialized; accept our json-hex form
    # (as produced by getblock verbosity=0) or a raw payload dict in hex.
    payload = None
    try:
        raw = bytes.fromhex(str(hexdata).replace("0x", ""))
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        return "bad-block-deserialize"
    try:
        r = F.native("miner.submitBlock", payload=payload)
    except Exception as exc:
        _, reason = __import__("rpc.methods.bitcoin_compat.errors_btc",
                               fromlist=["reason_for"]).reason_for(str(exc))
        return reason or "rejected"
    if isinstance(r, dict) and r.get("success"):
        return None
    msg = (r or {}).get("message", "rejected") if isinstance(r, dict) else "rejected"
    return msg


@method("prioritisetransaction", desc="Bitcoin-compat: fee priority (no-op).")
def prioritisetransaction(txid: str, dummy: int = 0, fee_delta: int = 0, ctx=None) -> bool:
    # Animica selects by envelope fee_rate; post-hoc priority is advisory only.
    return True


@method("generatetoaddress", desc="Bitcoin-compat: mine N blocks to an address (dev/regtest).")
def generatetoaddress(nblocks: int, address: str, maxtries: int = 1000000, ctx=None) -> list:
    try:
        r = F.native("miner.mine", count=int(nblocks), address=address)
    except Exception as exc:
        raise to_btc_error(exc)
    blocks = (r or {}).get("blocks") or []
    out = []
    for b in blocks:
        out.append(F.hx(b.get("hash") if isinstance(b, dict) else b))
    return out
