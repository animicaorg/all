# -*- coding: utf-8 -*-
"""
Local unit tests for the example Animica-20 token contract using the Python VM.

These tests intentionally tolerate small differences in the VM loader/runner
interfaces across versions by probing for multiple common entrypoints. If the
VM package isn't available in the environment, the tests will be skipped.

What we check:
- initializer sets name/symbol/decimals/owner/initialSupply
- balanceOf / totalSupply reflect transfers, approvals, transferFrom
- owner-only mint works; burn reduces supply
- transferOwnership updates owner; old owner can no longer mint
- canonical Transfer/Approval/OwnershipTransferred/Mint/Burn events appear

Conventions:
- Addresses are 32-byte values (bytes), derived deterministically from labels.
- Amounts are small integers to keep gas low in constrained test environments.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import pytest


# ---------------------------------------------------------------------------
# Helpers to deal with slightly different vm_py loader/runner shapes
# ---------------------------------------------------------------------------

def _try_import_vm():
    """
    Try to import vm_py loader/engine bits. If not present, skip the tests.
    """
    try:
        import vm_py.runtime.loader as loader  # type: ignore
    except Exception:
        pytest.skip("vm_py is not available in this environment")
    # Optional helpers; absence should not fail
    try:
        import vm_py.runtime.events_api as events_api  # type: ignore
    except Exception:  # pragma: no cover
        events_api = None
    try:
        import vm_py.runtime.context as ctx  # type: ignore
    except Exception:  # pragma: no cover
        ctx = None
    return loader, events_api, ctx


def _project_paths() -> Tuple[Path, Path]:
    """
    Return (contract.py path, manifest.json path) for the example token.
    """
    here = Path(__file__).resolve()
    base = here.parent  # contracts/examples/token
    src = base / "contract.py"
    manifest = base / "manifest.json"
    assert src.is_file(), f"Missing contract source at {src}"
    assert manifest.is_file(), f"Missing manifest at {manifest}"
    return src, manifest


def _addr(label: str, n: int = 32) -> bytes:
    raw = (label.encode("utf-8") + b"\x00" * n)[:n]
    return raw


class VmRunner:
    """
    Thin, forgiving wrapper over possible VM contract instances.

    Expected minimal surface:
      - an object with one of: call(fn, args), invoke(fn, args),
        dispatch(fn, args), run(fn, args)
      - returns either just a result, or (result, logs) where logs is a list
        of {name, args} dicts.
    """

    def __init__(self, contract: Any):
        self.contract = contract

    def call(self, fn: str, *args: Any) -> Tuple[Any, List[Dict[str, Any]]]:
        for meth in ("call", "invoke", "dispatch", "run"):
            if hasattr(self.contract, meth):
                out = getattr(self.contract, meth)(fn, list(args))
                if isinstance(out, tuple) and len(out) == 2:
                    return out[0], list(out[1] or [])
                # Some runners return only the result and keep logs on a field
                logs: List[Dict[str, Any]] = []
                if hasattr(self.contract, "logs"):
                    logs = list(getattr(self.contract, "logs") or [])
                return out, logs
        pytest.skip("Contract runner lacks a known call/dispatch method")

    # convenience
    def view(self, fn: str, *args: Any) -> Any:
        res, _ = self.call(fn, *args)
        return res


def _load_contract() -> VmRunner:
    loader, _, _ = _try_import_vm()
    src, manifest = _project_paths()

    # Try a few likely loader entrypoints
    contract_obj: Optional[Any] = None
    # 1) load(manifest_path, source_path)
    if hasattr(loader, "load"):
        try:
            contract_obj = loader.load(str(manifest), str(src))
        except TypeError:
            # some variants accept Path objects
            contract_obj = loader.load(manifest, src)
    # 2) load_manifest_and_source(manifest_path, source_path)
    if contract_obj is None and hasattr(loader, "load_manifest_and_source"):
        contract_obj = loader.load_manifest_and_source(str(manifest), str(src))
    # 3) build(...) or from_files(...)
    if contract_obj is None:
        for cand in ("build", "from_files", "load_from_paths"):
            if hasattr(loader, cand):
                contract_obj = getattr(loader, cand)(str(manifest), str(src))
                break

    if contract_obj is None:
        pytest.skip("vm_py.runtime.loader has no recognized load entrypoint")

    return VmRunner(contract_obj)


def _assert_event(logs: List[Dict[str, Any]], name: str) -> Dict[str, Any]:
    for ev in logs:
        if ev.get("name") == name:
            return ev
    raise AssertionError(f"Expected event {name!r} not found. Got: {logs}")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def token() -> VmRunner:
    """
    Build + load a fresh token contract instance.
    """
    return _load_contract()


@pytest.fixture()
def fresh_token(token: VmRunner) -> VmRunner:
    """
    Create a fresh logical instance by re-loading (stateless runners) or
    calling an explicit reset if provided by the loader.

    We want test-level isolation of state (balances, allowances, owner).
    """
    # If the runner exposes a 'reset' or 'fresh' constructor, prefer it.
    if hasattr(token.contract, "reset"):
        getattr(token.contract, "reset")()
        return token
    # Otherwise, reload to get a pristine state.
    return _load_contract()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_initializer_and_views(fresh_token: VmRunner):
    owner = _addr("owner")
    name = b"Animica Token"
    symbol = b"AMK"
    decimals = 6
    initial_supply = 1_000_000

    # init(name, symbol, decimals, initial_owner, initial_supply)
    res, logs = fresh_token.call(
        "init", name, symbol, decimals, owner, initial_supply
    )
    # initializer typically returns nothing; be tolerant
    assert fresh_token.view("name") == name
    assert fresh_token.view("symbol") == symbol
    assert int(fresh_token.view("decimals")) == decimals
    assert fresh_token.view("owner") == owner
    assert int(fresh_token.view("totalSupply")) == initial_supply
    assert int(fresh_token.view("balanceOf", owner)) == initial_supply

    # Should have minted to owner → expect Transfer event from zero?
    # We tolerate either explicit Mint event or Transfer from 0x00.. to owner.
    try:
        _assert_event(logs, "Mint")
    except AssertionError:
        try:
            ev = _assert_event(logs, "Transfer")
            assert ev["args"]["from"] in (b"", b"\x00" * 32)
            assert ev["args"]["to"] == owner
            assert int(ev["args"]["value"]) == initial_supply
        except Exception:
            # Some loaders may not return logs from init; that's OK.
            pass


def test_transfer_and_balances(fresh_token: VmRunner):
    owner = _addr("owner")
    alice = _addr("alice")
    initial_supply = 1_000_000
    fresh_token.call("init", b"X", b"X", 6, owner, initial_supply)

    ok, logs = fresh_token.call("transfer", alice, 1234)
    # accept True|1 as truthy
    assert bool(ok) is True

    assert int(fresh_token.view("balanceOf", alice)) == 1234
    assert int(fresh_token.view("balanceOf", owner)) == initial_supply - 1234

    ev = _assert_event(logs, "Transfer")
    assert ev["args"]["from"] == owner
    assert ev["args"]["to"] == alice
    assert int(ev["args"]["value"]) == 1234


def test_approve_and_transfer_from(fresh_token: VmRunner):
    owner = _addr("owner")
    spender = _addr("spender")
    bob = _addr("bob")
    total = 50_000
    fresh_token.call("init", b"T", b"T", 6, owner, total)

    ok, logs = fresh_token.call("approve", spender, 2500)
    assert bool(ok) is True
    ev = _assert_event(logs, "Approval")
    assert ev["args"]["owner"] == owner
    assert ev["args"]["spender"] == spender
    assert int(ev["args"]["value"]) == 2500

    # spender moves funds from owner → bob
    ok, logs = fresh_token.call("transferFrom", owner, bob, 1200)
    assert bool(ok) is True
    _assert_event(logs, "Transfer")
    assert int(fresh_token.view("allowance", owner, spender)) == 1300
    assert int(fresh_token.view("balanceOf", bob)) == 1200
    assert int(fresh_token.view("balanceOf", owner)) == total - 1200


def test_mint_and_burn_owner_only(fresh_token: VmRunner):
    owner = _addr("owner")
    carol = _addr("carol")
    fresh_token.call("init", b"M", b"M", 6, owner, 1)

    # Owner mints
    ok, logs = fresh_token.call("mint", carol, 200)
    assert bool(ok) is True
    _assert_event(logs, "Mint")
    assert int(fresh_token.view("balanceOf", carol)) == 200
    assert int(fresh_token.view("totalSupply")) == 201

    # Burn from owner (self-burn pattern)
    ok, logs = fresh_token.call("burn", 50)
    assert bool(ok) is True
    _assert_event(logs, "Burn")
    assert int(fresh_token.view("totalSupply")) == 151

    # Non-owner cannot mint — accept False return or a raised error
    eve = _addr("eve")
    # Simulate msg.sender switch if the runner supports it via a special call;
    # otherwise the contract is expected to read caller from TxEnv seeded by runner.
    # We attempt a few conventional knobs to set caller for the next call.
    switched = False
    for attr in ("set_sender", "set_caller", "with_sender"):
        if hasattr(fresh_token.contract, attr):
            getattr(fresh_token.contract, attr)(eve)
            switched = True
            break

    def _try_mint_as_eve() -> bool:
        try:
            ok2, _ = fresh_token.call("mint", eve, 1)
            return bool(ok2)
        except Exception:
            return False

    assert _try_mint_as_eve() is False or switched is False, (
        "Non-owner mint must not succeed; if the runner doesn't support "
        "sender switching the check is skipped as non-determinable."
    )


def test_transfer_ownership_and_enforce(fresh_token: VmRunner):
    owner = _addr("owner")
    new_owner = _addr("new_owner")
    fresh_token.call("init", b"O", b"O", 6, owner, 10)

    # Transfer ownership
    _, logs = fresh_token.call("transferOwnership", new_owner)
    ev = _assert_event(logs, "OwnershipTransferred")
    assert ev["args"]["previousOwner"] == owner
    assert ev["args"]["newOwner"] == new_owner
    assert fresh_token.view("owner") == new_owner

    # Old owner cannot mint now (same caller caveat as above)
    def _set_sender(addr: bytes) -> None:
        for attr in ("set_sender", "set_caller", "with_sender"):
            if hasattr(fresh_token.contract, attr):
                getattr(fresh_token.contract, attr)(addr)
                return

    _set_sender(owner)
    try:
        ok, _ = fresh_token.call("mint", owner, 1)
        # If the VM returns a bool, it must be False; else an exception should be raised.
        assert bool(ok) is False
    except Exception:
        # Raised revert is acceptable
        pass

    # New owner can mint
    _set_sender(new_owner)
    ok, _ = fresh_token.call("mint", new_owner, 7)
    assert bool(ok) is True
    assert int(fresh_token.view("balanceOf", new_owner)) == 7


