"""
Tree-engine execution tests for the real standard contracts.

Lowers each contract with the actual compiler front-end (ast_lower) and runs it
through the deterministic tree interpreter against dict-backed hosts — proving
the VM build-out executes the production token/DEX contracts without the raw
exec path. Runs standalone (no pytest needed): `python -m vm_py.tests.test_tree_engine_contracts`.
"""

from __future__ import annotations

import ast
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from vm_py.compiler import ast_lower  # noqa: E402
from vm_py.errors import VmError  # noqa: E402
from vm_py.runtime.gasmeter import GasMeter  # noqa: E402
from vm_py.runtime.tree_engine import TreeInterpreter  # noqa: E402

CONTRACTS = os.path.join(_ROOT, "contracts", "standards")


def _lower(name: str):
    src = open(os.path.join(CONTRACTS, name, "contract.py")).read()
    return ast_lower.lower_to_ir(ast.parse(src), filename=name).functions


class DictStorage:
    def __init__(self):
        self.d = {}

    def get(self, key, default=b""):
        return self.d.get(bytes(key), default)

    def set(self, key, value):
        self.d[bytes(key)] = bytes(value)

    def delete(self, key):
        self.d.pop(bytes(key), None)


class Abi:
    def __init__(self, self_addr=b"\x11" * 32):
        self.caller_addr = b"\x00" * 32
        self.self_addr = self_addr
        self.call_value = 0

    def caller(self):
        return self.caller_addr

    def sender(self):
        return self.caller_addr

    def tx_origin(self):
        return self.caller_addr

    def contract_address(self):
        return self.self_addr

    def self_address(self):
        return self.self_addr

    def value(self):
        return self.call_value

    def is_read_only(self):
        return False

    def require(self, cond, msg=b"require_failed"):
        if not cond:
            raise VmError(_msg(msg))

    def revert(self, msg=b"revert"):
        raise VmError(_msg(msg))


class Events:
    def __init__(self):
        self.log = []

    def emit(self, name, payload):
        self.log.append((bytes(name), dict(payload)))


def _msg(m):
    return m.decode("utf-8", "replace") if isinstance(m, (bytes, bytearray)) else str(m)


def _run(fns, storage, abi, events, method, args, caller=None):
    if caller is not None:
        abi.caller_addr = caller
    gas = GasMeter(limit=200_000_000)
    hosts = {"storage": storage, "abi": abi, "events": events}
    return TreeInterpreter(fns, hosts, gas).call(method, list(args))


def test_token_lifecycle():
    fns = _lower("animica_token")
    st, abi, ev = DictStorage(), Abi(), Events()

    def call(method, args, caller=None):
        return _run(fns, st, abi, ev, method, args, caller)

    OWNER = bytes.fromhex("aa" * 32)
    A = bytes.fromhex("bb" * 32)
    B = bytes.fromhex("cc" * 32)

    # init: name, symbol, decimals, owner, initial_supply, max_supply, mintable, uri, freeze_auth
    call("init", [b"MyToken", b"MTK", 9, OWNER, 1000, 10**9, True, b"ipfs://x", b""], caller=OWNER)
    assert call("total_supply", []) == 1000
    assert call("totalSupply", []) == 1000  # alias delegates to total_supply
    assert call("balance_of", [OWNER]) == 1000
    assert call("name", []) == b"MyToken"
    assert call("symbol", []) == b"MTK"
    assert call("decimals", []) == 9
    assert call("owner", []) == OWNER
    assert call("mintable", []) is True

    # re-init reverts
    try:
        call("init", [b"X", b"X", 0, OWNER, 1, 2, False, b"", b""], caller=OWNER)
        raise AssertionError("re-init should revert")
    except VmError as e:
        assert "already_initialized" in str(e)

    # mint (owner is minter)
    call("mint", [A, 500], caller=OWNER)
    assert call("total_supply", []) == 1500 and call("balance_of", [A]) == 500

    # transfer
    call("transfer", [B, 200], caller=OWNER)
    assert call("balance_of", [OWNER]) == 800 and call("balance_of", [B]) == 200

    # approve + transfer_from
    call("approve", [A, 300], caller=OWNER)
    assert call("allowance", [OWNER, A]) == 300
    call("transfer_from", [OWNER, B, 300], caller=A)
    assert call("balance_of", [OWNER]) == 500
    assert call("balance_of", [B]) == 500
    assert call("allowance", [OWNER, A]) == 0

    # burn
    call("burn", [100], caller=A)
    assert call("balance_of", [A]) == 400 and call("total_supply", []) == 1400

    # over-transfer reverts
    try:
        call("transfer", [B, 999999], caller=A)
        raise AssertionError("over-transfer should revert")
    except VmError:
        pass

    # events were emitted (Transfer/Approval/Mint/Burn)
    names = {n.decode() for n, _ in ev.log}
    assert {"Transfer", "Approval", "Mint", "Burn"} <= names, names
    return "token lifecycle OK (%d events)" % len(ev.log)


def test_determinism_repeatable():
    """Same calldata + same seed → identical return AND identical gas, twice."""
    fns = _lower("animica_token")

    def fresh():
        st, abi, ev = DictStorage(), Abi(), Events()
        OWNER = bytes.fromhex("aa" * 32)
        abi.caller_addr = OWNER
        gas = GasMeter(limit=200_000_000)
        hosts = {"storage": st, "abi": abi, "events": ev}
        vm = TreeInterpreter(fns, hosts, gas)
        vm.call("init", [b"T", b"T", 9, OWNER, 1000, 10**9, True, b"", b""])
        g0 = gas.used
        vm2 = TreeInterpreter(fns, hosts, GasMeter(limit=200_000_000))
        r = vm2.call("balance_of", [OWNER])
        return r, g0, dict(st.d)

    r1, g1, s1 = fresh()
    r2, g2, s2 = fresh()
    assert r1 == r2 == 1000, (r1, r2)
    assert g1 == g2, (g1, g2)
    assert s1 == s2
    return "determinism OK (init gas=%d, reproducible)" % g1


def test_bignum_no_masking():
    """The DEX price math overflows 2**256; the engine must use arbitrary
    precision, not the stack engine's 256-bit mask. Prove it via the pair's real
    `_get_amount_out(amount_in, reserve_in, reserve_out, fee_bps)` with values
    whose intermediate product exceeds 2**256."""
    fns = _lower("animica_dex_pair")
    st, abi, ev = DictStorage(), Abi(), Events()
    gas = GasMeter(limit=200_000_000)
    hosts = {"storage": st, "abi": abi, "events": ev}
    vm = TreeInterpreter(fns, hosts, gas)

    amount_in = 10**30
    reserve_in = 5 * 10**30
    reserve_out = 7 * 10**30
    fee_bps = 30
    # numerator = amount_in*(10000-fee) * reserve_out  →  ~10^64, far above 2**256 (~1.16e77 is 2^256; use bigger)
    amount_in = 2**200
    reserve_in = 3 * 2**200
    reserve_out = 5 * 2**200  # numerator ~ 2^200 * 9970 * 5*2^200 ≈ 2^417 » 2^256
    got = vm.call("_get_amount_out", [amount_in, reserve_in, reserve_out, fee_bps])

    fee_factor = 10_000 - fee_bps
    amount_in_with_fee = amount_in * fee_factor
    numerator = amount_in_with_fee * reserve_out
    denominator = reserve_in * 10_000 + amount_in_with_fee
    expected = numerator // denominator
    assert numerator > (1 << 256), "test must exercise a >2**256 intermediate"
    assert got == expected, f"masking detected: {got} != {expected}"
    return "bignum OK (numerator ~2^%d, no masking)" % numerator.bit_length()


if __name__ == "__main__":
    for fn in (test_token_lifecycle, test_determinism_repeatable, test_bignum_no_masking):
        print(f"{fn.__name__}: {fn()}")
    print("ALL TREE-ENGINE CONTRACT TESTS PASSED")
