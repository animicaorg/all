import json
import os
import copy
import inspect
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import pytest


# --- Helpers to discover runtime/adapters without locking exact APIs -------------

def _import(mod: str):
    try:
        return __import__(mod, fromlist=["*"])
    except Exception as e:
        pytest.skip(f"Cannot import {mod}: {e}")


def _find_apply_block():
    rt_exec = _import("execution.runtime.executor")
    # Accept a few common names
    for name in ("apply_block", "run_block", "apply_block_txs", "execute_block"):
        fn = getattr(rt_exec, name, None)
        if callable(fn):
            return fn
    pytest.skip("No apply_block-style function exported from execution.runtime.executor")


def _find_apply_tx_optional():
    rt_exec = _import("execution.runtime.executor")
    for name in ("apply_tx", "execute_tx", "apply_transaction"):
        fn = getattr(rt_exec, name, None)
        if callable(fn):
            return fn
    return None


def _load_chain_params_optional():
    try:
        mod = __import__("execution.adapters.params", fromlist=["*"])
        for name in ("load_chain_params", "get_chain_params", "ChainParams", "default_params"):
            v = getattr(mod, name, None)
            if callable(v):
                try:
                    return v()
                except TypeError:
                    # Maybe a dataclass/const
                    return v
        return {}
    except Exception:
        return {}


def _mk_state_from_genesis(genesis: Dict[str, Any]):
    """
    Best-effort constructor for a writable state DB backed by the execution adapters.
    We try multiple shapes to stay compatible with minor refactors.
    """
    s_mod = _import("execution.adapters.state_db")
    candidates = [
        "StateDB", "StateDb", "InMemoryState", "State", "StateAdapter", "StateDBAdapter"
    ]
    cls = None
    for name in candidates:
        c = getattr(s_mod, name, None)
        if isinstance(c, type):
            cls = c
            break
    if cls is None:
        pytest.skip("No usable state class found in execution.adapters.state_db")
    try:
        state = cls()
    except TypeError:
        # Some adapters require a path/uri; try in-memory defaults
        try:
            state = cls(":memory:")
        except Exception as e:
            pytest.skip(f"Cannot instantiate state class {cls}: {e}")

    # Populate balances/nonces from genesis dict (fixture format: {"accounts":[{"address":..,"balance":..,"nonce":..}]})
    accts = genesis.get("accounts") or genesis.get("Accounts") or []
    # Try a few APIs on the state object
    set_balance = getattr(state, "set_balance", None)
    set_nonce = getattr(state, "set_nonce", None)
    create_account = getattr(state, "create_account", None)
    upsert = getattr(state, "upsert_account", None)

    for entry in accts:
        addr = _addr_hex(entry["address"])
        bal = int(entry.get("balance", 0))
        nonce = int(entry.get("nonce", 0))
        if upsert:
            upsert(addr, balance=bal, nonce=nonce)
        else:
            if create_account:
                create_account(addr, balance=bal, nonce=nonce)
            else:
                if set_balance:
                    set_balance(addr, bal)
                if set_nonce:
                    set_nonce(addr, nonce)
    return state


def _genesis_fixture_path() -> str:
    here = os.path.dirname(__file__)
    # ../fixtures/genesis_state.json
    return os.path.normpath(os.path.join(here, "..", "fixtures", "genesis_state.json"))


def _load_genesis_dict() -> Dict[str, Any]:
    path = _genesis_fixture_path()
    if not os.path.exists(path):
        pytest.skip(f"Genesis fixture missing at {path}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _addr_hex(x: Union[str, bytes, bytearray]) -> str:
    if isinstance(x, (bytes, bytearray)):
        raw = bytes(x)
    else:
        s = str(x)
        s = s[2:] if s.lower().startswith("0x") else s
        if len(s) % 2:
            s = "0" + s
        raw = bytes.fromhex(s)
    if len(raw) < 20:
        raw = b"\x00" * (20 - len(raw)) + raw
    elif len(raw) > 20:
        raw = raw[-20:]
    return "0x" + raw.hex()


def _mk_tx_transfer(sender_hex: str, to_hex: str, value: int, nonce: int) -> Any:
    """
    Construct a minimal 'transfer' tx object that common executors can understand.
    We prefer a dict, but also try to adapt to dataclasses if available.
    Fields chosen to be broadly compatible with earlier tests:
      - kind: "transfer"
      - from, to: 0x-hex 20-byte
      - value: int
      - nonce: int
      - gas_limit/gas_price present with safe defaults
    """
    tx = {
        "kind": "transfer",
        "from": _addr_hex(sender_hex),
        "to": _addr_hex(to_hex),
        "value": int(value),
        "nonce": int(nonce),
        "gas_limit": 100_000,
        "gas_price": 1,
    }
    return tx


def _mk_block_envelope(txs: List[Any], height: int = 1, timestamp: int = 1) -> Any:
    """
    Create a very light block envelope the executor is likely to accept.
    If the executor expects a dataclass with .txs or .transactions, we provide both.
    """
    return SimpleNamespace(
        height=height,
        timestamp=timestamp,
        txs=txs,
        transactions=txs,
        header=SimpleNamespace(height=height, timestamp=timestamp),
    )


def _call_apply_block(fn, state, block, params):
    """
    Call apply_block with flexible signature handling:
      - (state, block)
      - (state, block, params)
      - (block, state)
      - (block, state, params)
    Returns the function's raw result.
    """
    sig = inspect.signature(fn)
    args = []
    if len(sig.parameters) == 2:
        # Detect order by parameter names
        names = list(sig.parameters.keys())
        if names[0].lower().startswith("state"):
            args = [state, block]
        else:
            args = [block, state]
    elif len(sig.parameters) == 3:
        names = list(sig.parameters.keys())
        if names[0].lower().startswith("state"):
            args = [state, block, params]
        else:
            args = [block, state, params]
    else:
        # Fallback to most common
        try:
            args = [state, block, params]
        except Exception:
            args = [state, block]
    return fn(*args)


def _extract_root(result) -> Optional[str]:
    """
    Try common ways to obtain the resulting state root:
      - result.state_root / result.new_state_root / result.root
      - tuple returns where last element looks like hex root
      - dict-like with 'stateRoot'/'state_root'
    """
    # direct attributes
    for name in ("state_root", "new_state_root", "root", "stateRoot"):
        if hasattr(result, name):
            val = getattr(result, name)
            if isinstance(val, str):
                return val
    # mapping
    if isinstance(result, dict):
        for k in ("stateRoot", "state_root", "root"):
            if k in result and isinstance(result[k], str):
                return result[k]
    # tuple endings
    if isinstance(result, (tuple, list)) and result:
        tail = result[-1]
        if isinstance(tail, str) and tail.lower().startswith("0x") and len(tail) >= 66:
            return tail
        # Sometimes the root is nested
        if hasattr(tail, "state_root") and isinstance(tail.state_root, str):
            return tail.state_root
    return None


# --- The test: apply a tiny bundle and assert determinism -----------------------

def test_apply_block_roundtrip_stable_root():
    apply_block = _find_apply_block()
    params = _load_chain_params_optional()

    # Load genesis and build a fresh state
    genesis = _load_genesis_dict()
    state = _mk_state_from_genesis(genesis)

    # Prepare two simple transfers within funded genesis accounts.
    # If the fixture lacks these exact addresses, balances may be 0—so we choose small values.
    A = "0x" + "11" * 20
    B = "0x" + "22" * 20
    C = "0x" + "33" * 20
    txs = [
        _mk_tx_transfer(A, B, value=123, nonce=0),
        _mk_tx_transfer(B, C, value=7, nonce=0),
    ]
    block = _mk_block_envelope(txs, height=1, timestamp=1)

    # Run apply_block twice over deep-copied inputs to catch purity issues.
    res1 = _call_apply_block(apply_block, state, block, params)
    # Rebuild state for the second run (fresh instance)
    state2 = _mk_state_from_genesis(genesis)
    block2 = _mk_block_envelope(copy.deepcopy(txs), height=1, timestamp=1)
    res2 = _call_apply_block(apply_block, state2, block2, params)

    root1 = _extract_root(res1)
    root2 = _extract_root(res2)

    assert isinstance(root1, str) and root1.lower().startswith("0x"), "first run did not return a hex root"
    assert isinstance(root2, str) and root2.lower().startswith("0x"), "second run did not return a hex root"
    assert len(bytes.fromhex(root1[2:])) == len(bytes.fromhex(root2[2:])), "root lengths mismatch"
    assert root1 == root2, "state root must be stable/deterministic across identical runs"

    # Optional: if apply_tx is available, applying txs one-by-one should yield the same root.
    apply_tx = _find_apply_tx_optional()
    if apply_tx:
        state3 = _mk_state_from_genesis(genesis)
        # Allow flexible signatures for apply_tx as well: (state, tx, params?) or (tx, state, params?)
        def call_apply_tx(tx):
            sig = inspect.signature(apply_tx)
            if len(sig.parameters) == 2:
                names = list(sig.parameters.keys())
                if names[0].lower().startswith("state"):
                    return apply_tx(state3, tx)
                else:
                    return apply_tx(tx, state3)
            elif len(sig.parameters) == 3:
                names = list(sig.parameters.keys())
                if names[0].lower().startswith("state"):
                    return apply_tx(state3, tx, params)
                else:
                    return apply_tx(tx, state3, params)
            else:
                return apply_tx(state3, tx, params)

        for t in txs:
            call_apply_tx(t)

        # After sequential txs, a helper on state may expose current root; try a few names.
        for attr in ("state_root", "root", "get_state_root"):
            if hasattr(state3, attr):
                v = getattr(state3, attr)
                sr = v() if callable(v) else v
                if isinstance(sr, str) and sr.lower().startswith("0x"):
                    assert sr == root1, "sequential tx apply must arrive at the same final root"
                    break


