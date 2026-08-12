"""Tier 2 — Bitcoin-Core-compatible wallet RPCs (node-wallet adapter).

Animica is account-model with post-quantum addresses and no UTXO set. To make
Bitcoin-Core wallet integrations (exchanges) work anyway, this module keeps a
small node-local *watch-address* registry plus a balance-delta deposit tracker
(see wallet_store.py), so the whole family of wallet RPCs behaves sensibly:

  * ``getbalance <address>`` returns that account's true balance and, crucially,
    **raises a descriptive error instead of a silent 0** when the node cannot
    read the account. The old shim did ``except: return 0.0``, so any read
    error looked like an empty (yet funded) account. It reads through the strict
    canonical reader (``state_service.get_balance``, which raises on a bad
    address or a state-read error) — no lenient fallback that could re-mask the
    error into a 0.
  * ``getbalance`` / ``"*"`` / ``"<label>"`` sum the imported (watched) addresses.
  * ``importaddress`` registers an address; ``getreceivedbyaddress``,
    ``listunspent``, ``listtransactions`` and ``listsinceblock`` then work for
    deposit detection, synthesised from watched-address balance deltas.

Confirmation depth for synthesised entries is derived from when a deposit was
first *observed* (``detect_height``), a lower bound on its true height, so
confirmations NEVER over-state — an exchange's ``minconf`` reorg gate stays
safe. Every list/received method honours ``minconf``/``maxconf``.

Everything here is a pure RPC-facade convenience. It never touches consensus,
state transition, block validation, or the mempool — it only *reads* balances
through the registered ``state.*`` natives and records what it saw.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from rpc.methods import method

from . import formatters as F
from . import wallet_store as WS
from .errors_btc import (RPC_INVALID_ADDRESS_OR_KEY, RPC_WALLET_ERROR,
                         rpc_error, to_btc_error)


# --------------------------------------------------------------------------- #
# Balance helpers                                                             #
# --------------------------------------------------------------------------- #
def _is_addr_token(tok: Any) -> bool:
    t = str(tok or "").strip().lower()
    return t.startswith("anim") or t.startswith("0x")


def _head_height() -> int:
    try:
        return F.int_from((F.native("chain.getHead") or {}).get("height"), 0)
    except Exception:
        return 0


def _head_hash() -> Optional[str]:
    try:
        return F.hx((F.native("chain.getHead") or {}).get("hash"))
    except Exception:
        return None


def _balance_nanos_strict(addr: str) -> int:
    """Read an address balance (nANM) from the strict canonical reader.

    This is the fix for the "getbalance returns 0 on a funded address" class of
    bug. ``rpc.state_service.get_balance`` raises on a bad address / missing ctx
    / state read error, and returns 0 ONLY for a genuinely absent account —
    unlike the RPC-facing ``_svc_balance`` (behind ``state.getBalance``) which
    swallows every error into a 0. We deliberately do NOT fall back to
    ``state.getAccount`` / ``state.getBalance`` here: those can re-mask a real
    read error as 0, which is precisely what we are eliminating. A caller that
    wants best-effort (polling, aggregate) wraps this in try/except itself.
    """
    a = str(addr or "").strip()
    if not a:
        raise rpc_error(RPC_INVALID_ADDRESS_OR_KEY, "address required")
    try:
        from rpc.state_service import get_balance as _strict_get_balance
        local = int(_strict_get_balance(a))
    except Exception as exc:
        # Surface the real reason — never a misleading 0.
        raise rpc_error(
            RPC_WALLET_ERROR,
            "balance unavailable for %s (node could not read account state): %r" % (a, exc),
        )
    # Read-through to a trusted upstream when the local node is behind, so a
    # not-yet-synced node returns the authoritative balance instead of a stale 0.
    try:
        from rpc.upstream_balance import authoritative
        h = F.native("chain.getHead") or {}
        return authoritative(a, "state.getBalance", local, h.get("height"),
                             h.get("chainId", h.get("chain_id")))
    except Exception:
        return local


def _try_balance_nanos(addr: str) -> Optional[int]:
    """Best-effort read for polling/aggregate loops — returns None on failure
    (so a per-address problem never poisons a whole-wallet total with a 0)."""
    try:
        return _balance_nanos_strict(addr)
    except Exception:
        return None


def _poll_watched(only: Optional[list] = None) -> list:
    """Reconcile watched-address balances against chain state; return new
    synthetic receive events (best-effort; never raises into the RPC)."""
    def _bal(a: str) -> int:
        v = _try_balance_nanos(a)
        if v is None:
            raise ValueError("unreadable")  # poll() skips this address
        return v
    try:
        return WS.poll(_bal, _head_height(), only=only)
    except Exception:
        return []


def _event_confs(detect_height: int, head: int) -> int:
    """Conservative confirmation depth for a tracked deposit: counted from first
    observation, so it never over-states. 0 when we have no height yet."""
    dh = F.int_from(detect_height, 0)
    if head and dh and head >= dh:
        return head - dh + 1
    return 1 if dh else 0


def _addr_confs(addr: str, head: int) -> int:
    """Confirmation depth of an address's balance = the FRESHEST tracked
    deposit's depth (fewest confirmations) — conservative for whole-balance
    'UTXO' reporting. 0 if nothing tracked yet."""
    evs = WS.events(limit=100000, address=addr)
    if not evs:
        return 0
    freshest = max(F.int_from(e.get("detect_height"), 0) for e in evs)
    return _event_confs(freshest, head)


def _received_with_minconf(addr: str, head: int, minconf: int) -> int:
    """Sum tracked receipts (nANM) for an address whose depth >= minconf."""
    total = 0
    for e in WS.events(limit=100000, address=addr):
        if _event_confs(e.get("detect_height"), head) >= minconf:
            total += F.int_from(e.get("amount_nanos"), 0)
    return total


def _event_to_btc(ev: dict, head: int) -> dict:
    dh = F.int_from(ev.get("detect_height"), 0)
    return {
        "address": ev.get("address"),
        "category": "receive",
        "amount": F.to_coin(ev.get("amount_nanos", 0)),
        "label": ev.get("label", ""),
        "vout": 0,
        "confirmations": _event_confs(dh, head),
        "txid": ev.get("txid"),
        "blockhash": None,
        "time": F.int_from(ev.get("time"), 0),
        "timereceived": F.int_from(ev.get("time"), 0),
        "bip125-replaceable": "no",
        "animica:detected_at_height": dh,
    }


# --------------------------------------------------------------------------- #
# Address / wallet management                                                 #
# --------------------------------------------------------------------------- #
@method("getnewaddress", desc="Bitcoin-compat: new Animica (PQ bech32m) address.")
def getnewaddress(label: str = "", address_type: Optional[str] = None, ctx=None) -> str:
    try:
        r = F.native("wallet.createAddress", label=(label or None), ctx=ctx)
    except Exception as exc:
        raise to_btc_error(exc, default_code=RPC_WALLET_ERROR)
    addr = (r or {}).get("address") or (r or {}).get("addr")
    if addr:
        WS.watch(addr, label=label, baseline_nanos=0)
    return addr


@method("importaddress",
        desc="Bitcoin-compat: watch an address so wallet-wide balance/deposit RPCs include it.")
def importaddress(address: str = "", label: str = "", rescan: bool = True,
                  p2sh: bool = False, ctx=None) -> None:
    addr = str(address or "").strip()
    if not _is_addr_token(addr):
        raise rpc_error(RPC_INVALID_ADDRESS_OR_KEY, "importaddress requires an anim1/0x address")
    # rescan=True (Bitcoin default) -> baseline 0, so the address's existing
    # balance surfaces as a deposit on the next poll. rescan=False -> baseline
    # current, so only *new* funds are reported going forward.
    baseline = None
    if not rescan:
        baseline = _try_balance_nanos(addr) or 0
    WS.watch(addr, label=label, baseline_nanos=baseline)
    _poll_watched(only=[addr])
    return None


@method("importpubkey", desc="Bitcoin-compat: watch by pubkey (advisory; account-model derives address elsewhere).")
def importpubkey(pubkey: str = "", label: str = "", rescan: bool = True, ctx=None) -> None:
    # Deriving the anim1 address from a raw pubkey needs the PQ address codec;
    # exchanges that manage their own keys import the address directly. Accept
    # and no-op so the call does not error out their setup.
    return None


@method("importprivkey", desc="Bitcoin-compat: no-op (node does not custody imported private keys).")
def importprivkey(privkey: str = "", label: str = "", rescan: bool = True, ctx=None) -> None:
    return None


@method("importmulti", desc="Bitcoin-compat: bulk-watch any anim1/0x addresses found in the request.")
def importmulti(requests: Optional[list] = None, options: Optional[dict] = None, ctx=None) -> list:
    out = []
    for req in (requests or []):
        watched = False
        try:
            scriptpub = (req or {}).get("scriptPubKey")
            addr = None
            if isinstance(scriptpub, dict):
                addr = scriptpub.get("address")
            elif isinstance(scriptpub, str) and _is_addr_token(scriptpub):
                addr = scriptpub
            for cand in (addr, (req or {}).get("address")):
                if _is_addr_token(cand):
                    WS.watch(str(cand), label=str((req or {}).get("label", "")), baseline_nanos=0)
                    watched = True
        except Exception:
            pass
        out.append({"success": watched,
                    "warnings": [] if watched else ["no anim1/0x address found in request"]})
    return out


@method("setlabel", desc="Bitcoin-compat: set the label on a watched address.")
def setlabel(address: str = "", label: str = "", ctx=None) -> None:
    if _is_addr_token(address):
        WS.watch(address, label=label)
    return None


@method("getaddressinfo", desc="Bitcoin-compat: address metadata (validity + watch status).")
def getaddressinfo(address: str = "", ctx=None) -> dict:
    addr = str(address or "").strip()
    valid = False
    try:
        vr = F.native("validateaddress", addr)
        if isinstance(vr, dict):
            valid = bool(vr.get("isvalid"))
    except Exception:
        valid = False
    if not valid and _is_addr_token(addr):
        valid = True
    lab = WS.label_of(addr)
    return {
        "address": addr,
        "scriptPubKey": F.hx(addr) if addr.startswith("0x") else "",
        "isvalid": valid,
        "ismine": False,
        "iswatchonly": WS.is_watched(addr),
        "solvable": WS.is_watched(addr),
        "iscompressed": False,
        "ischange": False,
        "labels": [lab] if lab else [],
        "animica:account_model": True,
    }


# --------------------------------------------------------------------------- #
# Balances                                                                    #
# --------------------------------------------------------------------------- #
@method("getbalance",
        desc="Bitcoin-compat: balance. Pass an anim1/0x address for that account; \"*\"/label sums imported (watched) addresses.")
def getbalance(dummy: str = "*", minconf: int = 0, include_watchonly: bool = True,
               avoid_reuse: bool = False, ctx=None) -> float:
    tok = str(dummy if dummy is not None else "*").strip()
    # Primary path: an explicit address -> that account's confirmed balance.
    # On an internal read failure this RAISES a descriptive error (never a
    # silent 0.0). It is a pure read: no watch-list write on this hot path.
    if _is_addr_token(tok):
        return F.to_coin(_balance_nanos_strict(tok))
    # Aggregate path: bare getbalance / "*" / a label -> sum watched addresses.
    label = None if tok in ("", "*") else tok
    total = 0
    for addr in WS.addresses(label=label):
        v = _try_balance_nanos(addr)
        if v is not None:
            total += v
        # else: skip an address we momentarily cannot read rather than
        # under-reporting the whole wallet as 0.
    return F.to_coin(total)


@method("getbalances", desc="Bitcoin-compat: structured balances (summed over watched addresses).")
def getbalances(ctx=None) -> dict:
    total = 0
    for addr in WS.addresses():
        v = _try_balance_nanos(addr)
        if v is not None:
            total += v
    coin = F.to_coin(total)
    return {
        "mine": {"trusted": coin, "untrusted_pending": 0.0, "immature": 0.0},
        "watchonly": {"trusted": coin, "untrusted_pending": 0.0, "immature": 0.0},
    }


@method("getreceivedbyaddress",
        desc="Bitcoin-compat: total received by an address with >= minconf confirmations.")
def getreceivedbyaddress(address: str, minconf: int = 1, include_watchonly: bool = True, ctx=None) -> float:
    if not _is_addr_token(address):
        raise rpc_error(RPC_INVALID_ADDRESS_OR_KEY, "getreceivedbyaddress requires an anim1/0x address")
    WS.watch(address, baseline_nanos=0)
    _poll_watched(only=[address])
    head = _head_height()
    total = _received_with_minconf(address, head, minconf)
    # If nothing is tracked yet (e.g. a poll hiccup) and the caller accepts
    # shallow confirmations, fall back to the strict current balance so a real
    # deposit is never under-reported as 0.
    if total == 0 and minconf <= 1:
        v = _try_balance_nanos(address)
        if v is not None:
            total = max(v, WS.received(address))
    return F.to_coin(total)


@method("getreceivedbylabel", desc="Bitcoin-compat: total received across addresses with a label (>= minconf).")
def getreceivedbylabel(label: str = "", minconf: int = 1, ctx=None) -> float:
    _poll_watched()
    head = _head_height()
    total = 0
    for addr in WS.addresses(label=label):
        total += _received_with_minconf(addr, head, minconf)
    return F.to_coin(total)


@method("listreceivedbyaddress", desc="Bitcoin-compat: per-address received across watched addresses (>= minconf).")
def listreceivedbyaddress(minconf: int = 1, include_empty: bool = False,
                          include_watchonly: bool = True, address_filter: str = "", ctx=None) -> list:
    _poll_watched()
    head = _head_height()
    addrs = [address_filter] if _is_addr_token(address_filter) else WS.addresses()
    out = []
    for addr in addrs:
        recv = _received_with_minconf(addr, head, minconf)
        confs = _addr_confs(addr, head)
        if recv <= 0 and not include_empty:
            continue
        evs = WS.events(limit=100000, address=addr)
        out.append({
            "address": addr,
            "amount": F.to_coin(recv),
            "confirmations": max(0, confs),
            "label": WS.label_of(addr),
            "txids": [e.get("txid") for e in evs],
        })
    return out


@method("getwalletinfo", desc="Bitcoin-compat: node wallet summary (balance summed over watched addresses).")
def getwalletinfo(ctx=None) -> dict:
    total = 0
    for addr in WS.addresses():
        v = _try_balance_nanos(addr)
        if v is not None:
            total += v
    return {
        "walletname": "",
        "walletversion": 169900,
        "format": "account-model",
        "balance": F.to_coin(total),
        "unconfirmed_balance": 0.0,
        "immature_balance": 0.0,
        "txcount": len(WS.events(limit=1_000_000)),
        "keypoolsize": 0,
        "keypoololdest": 0,
        "paytxfee": 0.0,
        "private_keys_enabled": False,
        "avoid_reuse": False,
        "scanning": False,
        "descriptors": True,
        "watchonly_addresses": len(WS.addresses()),
        "warning": ("Animica is account-model; wallet-wide balances are summed over imported "
                    "(watched) addresses. Use getbalance <address> for a single account."),
    }


# --------------------------------------------------------------------------- #
# UTXO / history (synthesised from balances + deltas)                         #
# --------------------------------------------------------------------------- #
@method("listunspent", desc="Bitcoin-compat: synthetic UTXOs from account balances (watched addresses if none given).")
def listunspent(minconf: int = 1, maxconf: int = 9999999, addresses: Optional[list] = None,
                include_unsafe: bool = True, query_options: Optional[dict] = None, ctx=None) -> list:
    explicit = [str(a) for a in (addresses or []) if a]
    addrs = explicit or WS.addresses()
    # Track explicitly-listed addresses so their deposits get a confirmation
    # depth; then refresh from chain before computing depths.
    for a in explicit:
        WS.watch(a, baseline_nanos=0)
    _poll_watched(only=(explicit or None))
    head = _head_height()
    out = []
    for addr in addrs:
        v = _try_balance_nanos(addr)
        if v is None or v <= 0:
            continue
        # Conservative confirmation depth (from the freshest tracked deposit).
        confs = _addr_confs(addr, head)
        if confs < minconf or confs > maxconf:
            continue
        # Deterministic, unique, NON-zero synthetic outpoint. The old shim used
        # the all-zero txid, which many wallets reject as an invalid placeholder.
        # This account model is spent via sendtoaddress/wallet.send, not by
        # consuming this outpoint.
        txid = hashlib.sha256(("utxo|%s|%d" % (addr, v)).encode()).hexdigest()
        out.append({
            "txid": txid,
            "vout": 0,
            "address": addr,
            "label": WS.label_of(addr),
            "scriptPubKey": F.hx(addr) if addr.startswith("0x") else "",
            "amount": F.to_coin(v),
            "confirmations": confs,
            "spendable": True,
            "solvable": True,
            "safe": True,
            "animica:account_model": True,
        })
    return out


@method("listtransactions",
        desc="Bitcoin-compat: recent wallet receive events (synthesised from watched-address balance deltas).")
def listtransactions(label: str = "*", count: int = 10, skip: int = 0,
                     include_watchonly: bool = True, ctx=None) -> list:
    _poll_watched()
    lab = None if label in ("*", "", None) else label
    evs = WS.events(limit=1_000_000, label=lab)
    if skip:
        evs = evs[:max(0, len(evs) - skip)]
    if count and len(evs) > count:
        evs = evs[-count:]
    head = _head_height()
    return [_event_to_btc(ev, head) for ev in evs]


@method("listsinceblock",
        desc="Bitcoin-compat: deposits since a block (synthesised from watched-address balance deltas).")
def listsinceblock(blockhash: str = "", target_confirmations: int = 1,
                   include_watchonly: bool = True, include_removed: bool = True, ctx=None) -> dict:
    _poll_watched()
    head = _head_height()
    since_h = None
    if blockhash:
        for m in ("chain.getBlockByHash", "chain.getBlock"):
            try:
                blk = F.native(m, F.px(blockhash)) or {}
                since_h = F.int_from(blk.get("height", blk.get("number")), None)
                if since_h is not None:
                    break
            except Exception:
                continue
    evs = WS.events(limit=1_000_000,
                    min_height=(since_h + 1) if since_h is not None else None)
    return {
        "transactions": [_event_to_btc(ev, head) for ev in evs],
        "removed": [],
        "lastblock": _head_hash() or F.ZERO_HASH,
    }


# --------------------------------------------------------------------------- #
# Sending (account-model)                                                     #
# --------------------------------------------------------------------------- #
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
    if amounts is None:
        amounts = {}
    if not isinstance(amounts, dict):
        raise rpc_error(RPC_WALLET_ERROR, "sendmany: 'amounts' must be an object mapping address -> amount")
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


@method("gettransaction", desc="Bitcoin-compat: wallet transaction detail.")
def gettransaction(txid: str, include_watchonly: bool = False, verbose: bool = False, ctx=None) -> dict:
    try:
        tx = F.native("tx.getTransactionByHash", F.px(txid))
    except Exception as exc:
        raise to_btc_error(exc, default_code=RPC_WALLET_ERROR)
    if not tx:
        raise rpc_error(RPC_INVALID_ADDRESS_OR_KEY, "Invalid or non-wallet transaction id")
    try:
        head_h = F.int_from((F.native("chain.getHead") or {}).get("height"), 0)
    except Exception:
        head_h = 0
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


# --------------------------------------------------------------------------- #
# Wallet lifecycle (single node wallet; advisory)                             #
# --------------------------------------------------------------------------- #
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
    # SECURITY (incident 2026-07-09): this copies the plaintext node key store
    # (wallets.json — secret_key_hex for EVERY node wallet) to an arbitrary,
    # caller-supplied path. Left unauthenticated it is a remote private-key
    # exfiltration + arbitrary-file-write oracle (destination into a snapshot dir
    # then snapshot.downloadChunk reads it back) — the same class as the
    # /wallet-export leak the public-edge gate closed. Gate it exactly like
    # wallet.send: a public-edge (nginx-proxied) caller is denied; only a direct
    # localhost / private-network / admin-token caller may back up.
    from rpc.methods.wallet import _authorize_wallet_rpc
    _authorize_wallet_rpc(ctx)
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
