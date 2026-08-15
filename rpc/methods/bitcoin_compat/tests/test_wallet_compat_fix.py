"""Mocked unit tests for the 5.2.2 Bitcoin-compat wallet fix.

Runs without a live node: state_service.get_balance and F.native are stubbed so
we can drive balances + chain height deterministically and assert the fixed
behaviour (no silent-0, conservative confirmations, minconf/maxconf honoured,
delta-based deposit detection).
"""
import os
import tempfile

# Point the watch-store at a throwaway file BEFORE anything resolves its path.
_TMP = tempfile.mkdtemp(prefix="btcw_test_")
os.environ["ANIMICA_BTC_COMPAT_WALLET_FILE"] = os.path.join(_TMP, "wallet.json")

import rpc.methods.bitcoin_compat.formatters as F
import rpc.methods.bitcoin_compat.wallet_store as WS
import rpc.methods.bitcoin_compat.wallet_methods as WM
import rpc.state_service as SS
from rpc.methods.bitcoin_compat.errors_btc import rpc_error  # noqa

NAN = 1_000_000_000  # nANM per ANM

STATE = {"balances": {}, "raise_for": set(), "head": {"height": 0, "hash": "0x" + "ab" * 32}}


def _fake_get_balance(addr):
    if addr in STATE["raise_for"]:
        raise RuntimeError("simulated state read error")
    return int(STATE["balances"].get(addr, 0))  # 0 = genuinely absent account


def _fake_native(name, *a, **k):
    if name == "chain.getHead":
        return dict(STATE["head"])
    if name == "validateaddress":
        return {"isvalid": True}
    if name in ("chain.getBlockByHash", "chain.getBlock"):
        return {"height": 0}
    raise KeyError("unexpected native in test: " + name)


SS.get_balance = _fake_get_balance
F.native = _fake_native


def _reset():
    WS.reset_for_tests()
    STATE["balances"].clear()
    STATE["raise_for"].clear()
    STATE["head"] = {"height": 0, "hash": "0x" + "ab" * 32}


PASS = 0


def check(cond, msg):
    global PASS
    assert cond, "FAIL: " + msg
    PASS += 1
    print("  ok:", msg)


# 1) getbalance <address> returns the true balance.
_reset()
A = "anim1zqpjp7test0000000000000000000000000000000000000000000000000"
STATE["balances"][A] = 1_224_000 * NAN
check(WM.getbalance(A) == 1_224_000.0, "getbalance <addr> returns true balance 1,224,000")

# 2) getbalance <address> RAISES on a read error — never a silent 0.
_reset()
B = "anim1raise00000000000000000000000000000000000000000000000000000000"
STATE["balances"][B] = 500 * NAN
STATE["raise_for"].add(B)
raised = False
try:
    WM.getbalance(B)
except Exception as e:
    raised = True
    check("balance unavailable" in str(e), "getbalance error message names the real failure")
check(raised, "getbalance <addr> RAISES on read error (no silent 0.0)")

# 3) genuinely-empty account -> 0.0 (valid address, no funds), not an error.
_reset()
C = "anim1empty00000000000000000000000000000000000000000000000000000000"
check(WM.getbalance(C) == 0.0, "getbalance <addr> of absent account returns 0.0 (no false error)")

# 4) importaddress(rescan) + getreceivedbyaddress honour minconf.
_reset()
STATE["head"]["height"] = 1000
STATE["balances"][A] = 1_224_000 * NAN
WM.importaddress(A, rescan=True)          # baseline 0 -> poll detects full balance at h=1000
check(WM.getreceivedbyaddress(A, 1) == 1_224_000.0, "getreceivedbyaddress minconf=1 sees the deposit")
check(WM.getreceivedbyaddress(A, 6) == 0.0, "getreceivedbyaddress minconf=6 hides a 1-conf deposit (safe)")
STATE["head"]["height"] = 1005            # deposit now 6 confs deep
check(WM.getreceivedbyaddress(A, 6) == 1_224_000.0, "getreceivedbyaddress minconf=6 credits once deep enough")

# 5) A follow-up deposit is detected as a separate receive event.
STATE["balances"][A] += 100 * NAN
sinceb = WM.listsinceblock("", 1)
evs = [t for t in sinceb["transactions"] if t["address"] == A]
check(len(evs) == 2, "listsinceblock shows two receive events (initial + follow-up)")
check(abs(sum(t["amount"] for t in evs) - 1_224_100.0) < 1e-6, "receive events sum to total received")

# 6) confirmations are conservative — derived from detect_height, never head.
_reset()
STATE["head"]["height"] = 850000
STATE["balances"][A] = 10 * NAN
WM.importaddress(A, rescan=True)          # detected at height 850000 -> 1 conf
u = WM.listunspent(1, 9999999, [A])
check(len(u) == 1, "listunspent returns the account UTXO")
check(u[0]["confirmations"] == 1, "listunspent confirmations = 1 (NOT the 850000 chain height)")
check(u[0]["txid"] != "0" * 64, "listunspent txid is a non-zero synthetic outpoint")

# 7) minconf/maxconf actually filter.
check(WM.listunspent(10, 9999999, [A]) == [], "listunspent minconf=10 filters out a 1-conf UTXO")
STATE["head"]["height"] = 850010          # now 11 confs
check(len(WM.listunspent(10, 9999999, [A])) == 1, "listunspent minconf=10 passes once deep enough")
check(WM.listunspent(1, 5, [A]) == [], "listunspent maxconf=5 filters out an 11-conf UTXO")

# 8) sendmany rejects a non-object 'amounts' cleanly (no internal crash).
raised = False
try:
    WM.sendmany("", ["not", "a", "dict"])
except Exception as e:
    raised = True
check(raised, "sendmany raises a clean error on non-object amounts")

print("\nALL %d CHECKS PASSED" % PASS)
