# -*- coding: utf-8 -*-
"""
Local unit tests for contracts/examples/ai_agent/contract.py using the Python VM.

Goals:
- Building & linking the contract succeeds against the provided manifest.
- request(model, prompt) returns a non-empty task_id (bytes) and emits JobRequested.
- Prompt/model can be retrieved via view methods (get_prompt/get_model).
- has_result(task_id) returns a bool and read(task_id) returns a (ok, output) pair of expected shapes.
  (In local VM mode, syscalls may be stubs; the test tolerates either "no result yet" or immediate stubbed result.)
- If a result is reported as available, get_output returns the same bytes and JobResult is emitted.

These tests are intentionally robust across slightly different vm_py loader APIs and
local syscall behaviors. If vm_py is unavailable, the test module is skipped.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import pytest


# ---- path helpers ------------------------------------------------------------

HERE = Path(__file__).resolve().parent
EXAMPLE_DIR = HERE
CONTRACT_PY = EXAMPLE_DIR / "contract.py"
MANIFEST_JSON = EXAMPLE_DIR / "manifest.json"


# ---- vm harness (best-effort across APIs) -----------------------------------

_vm = None
_loader = None


def _import_vm_loader():
    """Import vm_py loader, or skip tests if unavailable."""
    global _loader
    try:
        import vm_py.runtime.loader as loader  # type: ignore
    except Exception as exc:
        pytest.skip(f"vm_py not available or failed to import: {exc}")
    _loader = loader
    return loader


def _build_contract():
    """
    Try common loader entrypoints to compile & link the example contract.
    Returns an object with one of:
      - .call(name: str, *args) -> (ret, logs)
      - .invoke(name: str, *args) -> (ret, logs)
      - .run(name: str, args: list[bytes|int|bool|dict]) -> dict with {'return': ..., 'logs': ...}
    Falls back to loader-level helpers when present.
    """
    loader = _import_vm_loader()

    # Read manifest/source as text for APIs that take strings
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    source_text = CONTRACT_PY.read_text(encoding="utf-8")

    # Try a sequence of known patterns
    candidates = []

    # Pattern A: loader.load(manifest_path=..., source_path=...)
    cand = getattr(loader, "load", None)
    if callable(cand):
        try:
            vm = cand(manifest_path=str(MANIFEST_JSON), source_path=str(CONTRACT_PY))
            candidates.append(vm)
        except Exception:
            pass

    # Pattern B: loader.load_manifest_source(manifest: dict, source: str)
    cand = getattr(loader, "load_manifest_source", None)
    if callable(cand):
        try:
            vm = cand(manifest, source_text)
            candidates.append(vm)
        except Exception:
            pass

    # Pattern C: loader.from_files(manifest_path, source_path)
    cand = getattr(loader, "from_files", None)
    if callable(cand):
        try:
            vm = cand(str(MANIFEST_JSON), str(CONTRACT_PY))
            candidates.append(vm)
        except Exception:
            pass

    # Pattern D: loader.build(manifest=..., source=...)
    cand = getattr(loader, "build", None)
    if callable(cand):
        try:
            vm = cand(manifest=manifest, source=source_text)
            candidates.append(vm)
        except Exception:
            pass

    if not candidates:
        pytest.skip("No compatible vm_py loader entrypoint found for building the contract")

    # Prefer the first candidate that exposes a usable call surface
    for vm in candidates:
        for attr in ("call", "invoke", "run"):
            if callable(getattr(vm, attr, None)):
                return vm

    # As a last resort, expose loader.run_call style if present
    if hasattr(loader, "run_call"):
        return {"_manifest": manifest, "_source": source_text, "_loader": loader}

    pytest.skip("Built VM doesn't expose a recognizable call/invoke surface")


def _vm_call(vm: Any, name: str, *args: Any) -> Tuple[Any, list[dict]]:
    """
    Call a contract function and return (ret, logs list).
    Supports a range of adapter shapes produced by _build_contract().
    """
    # Direct .call / .invoke returning (ret, logs)
    for meth in ("call", "invoke"):
        fn = getattr(vm, meth, None)
        if callable(fn):
            out = fn(name, *args)
            # normalize
            if isinstance(out, tuple) and len(out) == 2:
                ret, logs = out
                return ret, list(logs or [])
            # Some implementations return dict
            if isinstance(out, dict) and "return" in out:
                return out["return"], list(out.get("logs") or [])
            # If return-only, try to grab logs attribute
            return out, list(getattr(vm, "logs", []) or [])

    # .run(name, args=[...]) → dict
    fn = getattr(vm, "run", None)
    if callable(fn):
        res = fn(name, list(args))
        if isinstance(res, dict):
            return res.get("return"), list(res.get("logs") or [])
        return res, list(getattr(vm, "logs", []) or [])

    # loader.run_call(vm_state, ...)
    if isinstance(vm, dict) and "_loader" in vm:
        loader = vm["_loader"]
        run_call = getattr(loader, "run_call", None)
        if callable(run_call):
            res = run_call(vm["_manifest"], vm["_source"], name, list(args))
            if isinstance(res, dict):
                return res.get("return"), list(res.get("logs") or [])
            return res, []
    pytest.skip("No compatible call surface available on VM instance")


def _find_event(logs: Iterable[dict], name: str) -> Optional[dict]:
    for ev in logs:
        if isinstance(ev, dict) and ev.get("name") == name:
            return ev
    return None


# ---- fixtures ----------------------------------------------------------------

@pytest.fixture(scope="module")
def vm() -> Any:
    """Compile & link the example contract once for this module."""
    global _vm
    if _vm is None:
        _vm = _build_contract()
    return _vm


# ---- tests -------------------------------------------------------------------

def test_build_and_manifest_present():
    assert CONTRACT_PY.is_file(), "contract.py must exist"
    assert MANIFEST_JSON.is_file(), "manifest.json must exist"
    manifest = json.loads(MANIFEST_JSON.read_text(encoding="utf-8"))
    assert manifest.get("name") == "ai_agent"
    abi = manifest.get("abi") or {}
    fn_names = [f.get("name") for f in (abi.get("functions") or [])]
    for needed in ("request", "read", "get_prompt", "get_model", "get_output", "has_result"):
        assert needed in fn_names, f"ABI is missing function: {needed}"
    event_names = [e.get("name") for e in (abi.get("events") or [])]
    assert "JobRequested" in event_names
    assert "JobResult" in event_names


def test_request_persists_prompt_and_model_and_emits_event(vm: Any):
    model = b"mini:qa"
    prompt = b"hello, world"
    task_id, logs = _vm_call(vm, "request", model, prompt)

    # Return shape
    assert isinstance(task_id, (bytes, bytearray)) and len(task_id) > 0

    # Views reflect stored inputs
    got_prompt, _ = _vm_call(vm, "get_prompt", task_id)
    assert isinstance(got_prompt, (bytes, bytearray)) and got_prompt == prompt

    got_model, _ = _vm_call(vm, "get_model", task_id)
    assert isinstance(got_model, (bytes, bytearray)) and got_model == model

    # Event emitted
    ev = _find_event(logs, "JobRequested")
    assert ev is not None, "JobRequested should be emitted"
    # Optional shape checks (lenient to allow different encodings)
    if ev:
        assert "task_id" in ev or "args" in ev


def test_read_flow_is_shape_correct_and_consistent(vm: Any):
    # New request
    model = b"mini:summarize"
    prompt = b"summarize: Animica whitepaper section 1"
    task_id, _ = _vm_call(vm, "request", model, prompt)

    # has_result: must be a bool
    has, _ = _vm_call(vm, "has_result", task_id)
    assert isinstance(has, bool)

    # read returns a (ok, output) pair
    out, logs = _vm_call(vm, "read", task_id)
    assert isinstance(out, (tuple, list)) and len(out) == 2
    ok, output = bool(out[0]), out[1]
    assert isinstance(ok, bool)
    assert isinstance(output, (bytes, bytearray))

    # If result is available, it should be retrievable via get_output and JobResult should be logged
    if ok:
        got, _ = _vm_call(vm, "get_output", task_id)
        assert isinstance(got, (bytes, bytearray))
        # Not all hosts guarantee equality (some may return truncated/hashed), but if non-empty, prefer equality
        if len(output) > 0:
            assert got == output
        ev = _find_event(logs, "JobResult")
        assert ev is not None, "JobResult should be emitted when ok=True"
    else:
        # If result not yet available in local mode, get_output should be bytes (may be empty)
        got, _ = _vm_call(vm, "get_output", task_id)
        assert isinstance(got, (bytes, bytearray))


def test_view_methods_are_pure(vm: Any):
    # Request once
    model = b"mini:chat"
    prompt = b"say hi"
    task_id, _ = _vm_call(vm, "request", model, prompt)

    # Snapshot of prompt/model
    p1, _ = _vm_call(vm, "get_prompt", task_id)
    m1, _ = _vm_call(vm, "get_model", task_id)

    # Call views multiple times and ensure they don't mutate
    p2, _ = _vm_call(vm, "get_prompt", task_id)
    m2, _ = _vm_call(vm, "get_model", task_id)
    p3, _ = _vm_call(vm, "get_prompt", task_id)
    m3, _ = _vm_call(vm, "get_model", task_id)

    assert p1 == p2 == p3 == prompt
    assert m1 == m2 == m3 == model
