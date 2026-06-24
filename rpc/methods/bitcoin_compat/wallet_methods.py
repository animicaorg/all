"""Tier 2 — Bitcoin-Core-compatible wallet RPCs (node-wallet adapter).

Animica is account-model with post-quantum addresses and no UTXO set or
address-indexed history, so several Bitcoin wallet calls are structurally
degraded (documented inline). The ones that map cleanly (getnewaddress,
sendtoaddress, gettransaction) are faithful; the rest return safe shapes.
"""

from __future__ import annotations

from typing import Any, Optional

from rpc.methods import method

from . import formatters as F
from .errors_btc import (RPC_INVALID_ADDRESS_OR_KEY, RPC_WALLET_ERROR,
                         rpc_error, to_btc_error)


@method("getnewaddress", desc="Bitcoin-compat: new Animica (PQ bech32m) address.")
def getnewaddress(label: str = "", address_type: Optional[str] = None, ctx=None) -> str:
    try:
        r = F.native("wallet.createAddress", label=(label or None), ctx=ctx)
    except Exception as exc:
        raise to_btc_error(exc, default_code=RPC_WALLET_ERROR)
    return (r or {}).get("address") or (r or {}).get("addr")


@method("getbalance", desc="Bitcoin-compat: balance (account-model; pass an anim1 address).")
def getbalance(dummy: str = "*", minconf: int = 0, include_watchonly: bool = False,
               avoid_reuse: bool = False, ctx=None) -> float:
    # Account model: there is no aggregate node-wallet sum without an address
    # registry. As an Animica-friendly extension, if the first arg is an address,
    # return its balance; otherwise return 0.0 (use state.getBalance for exact).
    addr = str(dummy or "")
    if addr.startswith("anim1") or addr.startswith("0x"):
        try:
            return F.to_coin(F.native("state.getBalance", addr))
        except Exception:
            return 0.0
    return 0.0


@method("listunspent", desc="Bitcoin-compat: synthetic UTXOs from account balances.")
def listunspent(minconf: int = 1, maxconf: int = 9999999, addresses: Optional[list] = None,
                include_unsafe: bool = True, query_options: Optional[dict] = None, ctx=None) -> list:
    out = []
    for addr in (addresses or []):
        try:
            bal = F.to_coin(F.native("state.getBalance", str(addr)))
        except Exception:
            bal = 0.0
        if bal <= 0:
            continue
        out.append({
            "txid": F.ZERO_HASH, "vout": 0, "address": addr,
            "scriptPubKey": F.hx(addr) if str(addr).startswith("0x") else "",
            "amount": bal, "confirmations": 1,
            "spendable": True, "solvable": True, "safe": True,
        })
    return out


@method("sendtoaddress", desc="Bitcoin-compat: send ANM to an address.")
def sendtoaddress(address: str, amount: float, comment: str = "", comment_to: str = "",
                  subtractfeefromamount: bool = False, replaceable: Optional[bool] = None,
                  conf_target: Optional[int] = None, estimate_mode: Optional[str] = None,
                  avoid_reuse: bool = False, fee_rate: Optional[float] = None, ctx=None) -> str:
    try:
        r = F.native("wallet.send", to=address, amount=F.to_nanos(amount),
                     label=(comment or None), ctx=ctx)
    except Exception as exc:
        raise to_btc_error(exc, default_code=RPC_WALLET_ERROR)
    return F.hx((r or {}).get("txid") or (r or {}).get("hash"))


@method("sendmany", desc="Bitcoin-compat: send to many (fans out to one tx per recipient).")
def sendmany(dummy: str = "", amounts: Optional[dict] = None, minconf: int = 1,
             comment: str = "", subtractfeefrom: Optional[list] = None,
             replaceable: Optional[bool] = None, conf_target: Optional[int] = None,
             estimate_mode: Optional[str] = None, fee_rate: Optional[float] = None, ctx=None) -> str:
    amounts = amounts or {}
    txids = []
    for addr, amt in amounts.items():
        try:
            r = F.native("wallet.send", to=addr, amount=F.to_nanos(amt),
                         label=(comment or None), ctx=ctx)
            txids.append(F.hx((r or {}).get("txid") or (r or {}).get("hash")))
        except Exception as exc:
            raise to_btc_error(exc, default_code=RPC_WALLET_ERROR)
    if not txids:
        raise rpc_error(RPC_WALLET_ERROR, "sendmany: no recipients")
    # Bitcoin returns a single txid; account model produced one per recipient.
    return txids[-1]


@method("listtransactions", desc="Bitcoin-compat: wallet history (no address index; empty).")
def listtransactions(label: str = "*", count: int = 10, skip: int = 0,
                     include_watchonly: bool = False, ctx=None) -> list:
    # No address-indexed history RPC on the node; an explorer index would back
    # this. Return empty (Bitcoin clients tolerate it).
    return []


@method("gettransaction", desc="Bitcoin-compat: wallet transaction detail.")
def gettransaction(txid: str, include_watchonly: bool = False, verbose: bool = False, ctx=None) -> dict:
    tx = F.native("tx.getTransactionByHash", F.px(txid))
    if not tx:
        raise rpc_error(RPC_INVALID_ADDRESS_OR_KEY, "Invalid or non-wallet transaction id")
    head_h = F.int_from((F.native("chain.getHead") or {}).get("height"), 0)
    bnum = F.int_from(tx.get("blockNumber", tx.get("blockHeight")), -1)
    body = tx.get("body") or tx
    value = F.to_coin(body.get("value", body.get("amount", 0)))
    fee = F.to_coin(body.get("fee", 0))
    raw = None
    try:
        raw = (F.native("mempool.getRawTx", F.px(txid)) or {}).get("raw")
    except Exception:
        pass
    return {
        "amount": value,
        "fee": -fee,
        "confirmations": (head_h - bnum + 1) if bnum >= 0 else 0,
        "blockhash": F.hx(tx.get("blockHash")),
        "blockheight": bnum if bnum >= 0 else None,
        "blockindex": F.int_from(tx.get("index"), 0),
        "blocktime": F.int_from(tx.get("timestamp"), 0),
        "txid": F.hx(txid),
        "wtxid": F.hx(txid),
        "time": F.int_from(tx.get("timestamp"), 0),
        "timereceived": F.int_from(tx.get("timestamp"), 0),
        "bip125-replaceable": "no",
        "details": [{
            "address": body.get("to"),
            "category": "send",
            "amount": -value,
            "vout": 0,
        }],
        "hex": F.hx(raw),
    }


@method("createwallet", desc="Bitcoin-compat: single node wallet (advisory name).")
def createwallet(wallet_name: str, disable_private_keys: bool = False, blank: bool = False,
                 passphrase: str = "", avoid_reuse: bool = False, descriptors: bool = True,
                 load_on_startup: Optional[bool] = None, external_signer: bool = False, ctx=None) -> dict:
    return {"name": wallet_name,
            "warning": "Animica uses a single node wallet; wallet_name is advisory only."}


@method("loadwallet", desc="Bitcoin-compat: load wallet (no-op; single node wallet).")
def loadwallet(filename: str, load_on_startup: Optional[bool] = None, ctx=None) -> dict:
    return {"name": filename, "warning": "single node wallet; already loaded"}


@method("listwallets", desc="Bitcoin-compat: loaded wallets.")
def listwallets(ctx=None) -> list:
    return [""]


@method("backupwallet", desc="Bitcoin-compat: back up the node wallet file.")
def backupwallet(destination: str, ctx=None):
    import shutil
    from pathlib import Path
    src = None
    for env in ("ANIMICA_WALLET_FILE", "ANIMICA_WALLET_PATH"):
        import os
        if os.environ.get(env):
            src = os.environ[env]
            break
    if not src:
        src = str(Path.home() / ".animica" / "wallets.json")
    try:
        shutil.copy2(src, destination)
    except Exception as exc:
        raise to_btc_error(exc, default_code=RPC_WALLET_ERROR)
    return None
