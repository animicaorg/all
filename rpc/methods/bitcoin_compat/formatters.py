"""Formatters + helpers for the Bitcoin-Core-compatible RPC facade.

Everything that translates Animica's native shapes into Bitcoin-Core 30.x JSON
shapes lives here: hex/unit conversion, the synthetic theta->difficulty mapping,
the COMPAT-only constants, and the helpers that invoke Animica's native
JSON-RPC handlers by their *registered name* (so this facade rides the canonical
implementations and their error handling, not fragile internal imports).

Design notes:
  - Animica hashes are 0x + 64 hex (SHA3-256); Bitcoin hashes are bare 64 hex.
    hx() strips 0x for responses; px() adds it when feeding Animica.
  - Units: nANM (1 ANM = 1e9 nANM) <-> Bitcoin coin-unit float via /1e9.
  - difficulty is synthetic: exp(thetaMicro/1e6) = expected PoIES trials/block,
    a monotonic Bitcoin-difficulty analogue (PoIES has no compact nBits target).
"""

from __future__ import annotations

import inspect
import math
from typing import Any, Optional

# ---- COMPAT-only constants (Bitcoin fields with no Animica analogue) ----------
ZERO_HASH = "0" * 64
CHAINWORK = "0" * 64
COMPAT_BITS = "1d00ffff"          # Bitcoin difficulty-1 bits; clients rarely validate
COMPAT_VERSION = 536870912        # 0x20000000
COMPAT_VERSION_HEX = "20000000"
PROTOCOL_VERSION = 70016
SERVICES = "0000000000000409"     # NETWORK | WITNESS
NANOS_PER_COIN = 1_000_000_000    # nANM per ANM (display-mapped to BTC coin unit)


# ---- hex / unit helpers -------------------------------------------------------
def hx(s: Any) -> Optional[str]:
    """Animica 0x-hash -> bare Bitcoin hex (no 0x)."""
    if s is None:
        return None
    s = str(s)
    return s[2:] if s.startswith(("0x", "0X")) else s


def px(s: Any) -> str:
    """Bitcoin bare hex -> Animica 0x-prefixed."""
    s = str(s or "")
    return s if s.startswith(("0x", "0X")) else ("0x" + s)


def to_coin(nanos: Any) -> float:
    """nANM (int or 0x-hex) -> Bitcoin coin-unit float."""
    if nanos is None:
        return 0.0
    if isinstance(nanos, str):
        try:
            nanos = int(nanos, 16) if nanos.startswith(("0x", "0X")) else int(nanos)
        except ValueError:
            return 0.0
    try:
        return int(nanos) / NANOS_PER_COIN
    except (TypeError, ValueError):
        return 0.0


def to_nanos(coin: Any) -> int:
    """Bitcoin coin-unit float -> nANM int."""
    try:
        return int(round(float(coin) * NANOS_PER_COIN))
    except (TypeError, ValueError):
        return 0


def int_from(v: Any, default: int = 0) -> int:
    try:
        if isinstance(v, str):
            return int(v, 16) if v.startswith(("0x", "0X")) else int(v)
        return int(v)
    except (TypeError, ValueError):
        return default


# ---- synthetic difficulty / target -------------------------------------------
def theta_to_difficulty(theta_micro: Any) -> float:
    """PoIES thetaMicro -> Bitcoin-difficulty-analogue (expected trials/block).

    thetaMicro is micro-nats; exp(theta/1e6) is the expected number of PoIES
    trials to find a block, which is the natural monotonic "difficulty". Clamped
    so a huge theta never overflows to inf in JSON.
    """
    t = int_from(theta_micro, 0)
    if t <= 0:
        return 0.0
    try:
        d = math.exp(min(t / 1e6, 700.0))  # exp(700) ~ 1e304, still finite
    except OverflowError:
        d = 1e308
    return round(d, 8)


def difficulty_to_target_hex(difficulty: float) -> str:
    """Synthetic 256-bit target so target<->difficulty are internally consistent."""
    d = max(difficulty, 1e-12)
    target = int((2 ** 256 - 1) / d)
    return format(min(target, 2 ** 256 - 1), "064x")


def chain_name_from_id(chain_id: Any) -> str:
    cid = int_from(chain_id, 0)
    if cid == 1:
        return "main"
    if cid in (1337, 31337):
        return "regtest"
    if cid in (2, 11):
        return "test"
    return f"animica-{cid}"


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
    """Call a *synchronous* native method by its registered name.

    If the native happens to be a coroutine, run it to completion on a private
    loop (rare on the sync read paths the facade uses).
    """
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
    """Await a native method by registered name (for async natives like p2p.*)."""
    spec = _registry().get(name)
    if spec is None:
        raise KeyError(f"native method not registered: {name}")
    result = spec.func(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def has_native(name: str) -> bool:
    return _registry().get(name) is not None


# ---- block / header formatting -----------------------------------------------
def _roots(view: dict) -> dict:
    r = view.get("roots") or {}
    # roots may be nested or flattened on the view
    return {
        "stateRoot": r.get("stateRoot") or view.get("stateRoot"),
        "txsRoot": r.get("txsRoot") or view.get("txsRoot"),
        "receiptsRoot": r.get("receiptsRoot") or view.get("receiptsRoot"),
        "proofsRoot": r.get("proofsRoot") or view.get("proofsRoot"),
        "daRoot": r.get("daRoot") or view.get("daRoot"),
    }


def header_to_btc(view: dict, head_height: int, next_hash: Optional[str] = None) -> dict:
    """Animica block/header view -> Bitcoin getblockheader(verbose=True) shape."""
    height = int_from(view.get("height", view.get("number")), 0)
    roots = _roots(view)
    theta = view.get("thetaMicro", view.get("theta_micro"))
    diff = theta_to_difficulty(theta)
    ts = int_from(view.get("timestamp"), 0)
    out = {
        "hash": hx(view.get("hash")),
        "confirmations": (head_height - height + 1) if head_height >= height else -1,
        "height": height,
        "version": COMPAT_VERSION,
        "versionHex": COMPAT_VERSION_HEX,
        "merkleroot": hx(roots.get("txsRoot")) or ZERO_HASH,
        "time": ts,
        "mediantime": ts,           # no rolling median maintained
        "nonce": int_from(view.get("nonce"), 0),
        "bits": COMPAT_BITS,
        "difficulty": diff,
        "chainwork": CHAINWORK,
        "nTx": _ntx(view),
        # Animica-specific passthrough (Bitcoin clients ignore unknown keys)
        "thetaMicro": int_from(theta, 0),
        "mixSeed": view.get("mixSeed"),
        "stateRoot": roots.get("stateRoot"),
        "proofsRoot": roots.get("proofsRoot"),
        "daRoot": roots.get("daRoot"),
    }
    parent = view.get("parentHash") or view.get("parent_hash")
    if parent and int_from(parent, -1) != 0 and height > 0:
        out["previousblockhash"] = hx(parent)
    if next_hash:
        out["nextblockhash"] = hx(next_hash)
    return out


def _ntx(view: dict) -> int:
    txs = view.get("txs") or view.get("transactions")
    if isinstance(txs, list):
        return len(txs)
    return int_from(view.get("txCount", view.get("nTx", 0)), 0)


def block_to_btc(view: dict, head_height: int, verbosity: int,
                 next_hash: Optional[str] = None) -> dict:
    """Animica block view -> Bitcoin getblock(verbosity>=1) shape."""
    out = header_to_btc(view, head_height, next_hash=next_hash)
    txs = view.get("txs") or view.get("transactions") or []
    # rough serialized size: prefer a reported size, else estimate
    size = int_from(view.get("size"), 0) or (len(txs) * 256 + 200)
    out.update({
        "size": size,
        "strippedsize": size,
        "weight": size * 4,
    })
    if verbosity >= 2 and txs and isinstance(txs[0], dict):
        out["tx"] = [btc_tx_view(t) for t in txs]
    else:
        out["tx"] = [hx(t.get("hash") if isinstance(t, dict) else t) for t in txs]
    return out


def btc_tx_view(tx: dict) -> dict:
    """Animica tx (decoded or record) -> Bitcoin decoderawtransaction shape.

    Account model => one synthetic vin (sender) + one synthetic vout (recipient).
    """
    body = tx.get("body") or tx.get("tx", {}).get("body") or tx
    txid = tx.get("hash") or tx.get("txid") or tx.get("txHash")
    frm = body.get("from") or body.get("sender")
    to = body.get("to") or body.get("recipient")
    value = body.get("value", body.get("amount", 0))
    kind = body.get("kind", body.get("type"))
    data = body.get("data") or ""
    size = int_from(tx.get("len", tx.get("size")), 0)
    return {
        "txid": hx(txid),
        "hash": hx(txid),
        "version": 2,
        "size": size,
        "vsize": size,
        "weight": size * 4,
        "locktime": 0,
        "vin": [{
            "txid": None,
            "vout": 0,
            "scriptSig": {"asm": "", "hex": ""},
            "sequence": 0xffffffff,
            "animica:from": frm,
        }],
        "vout": [{
            "value": to_coin(value),
            "n": 0,
            "scriptPubKey": {
                "asm": "",
                "hex": hx(data) or "",
                "type": _kind_to_script_type(kind),
                "address": to,
                "addresses": [to] if to else [],
            },
        }],
        # Animica-specific extension
        "animica:kind": kind,
        "animica:nonce": body.get("nonce"),
        "animica:fee": body.get("fee"),
        "animica:chainId": body.get("chain_id", body.get("chainId")),
    }


def _kind_to_script_type(kind: Any) -> str:
    k = str(kind or "").lower()
    if k in ("transfer", "0", "value"):
        return "pubkeyhash"
    return "nonstandard"
