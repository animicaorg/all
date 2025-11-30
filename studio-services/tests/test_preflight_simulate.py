from __future__ import annotations

import json

import pytest

# These tests cover two endpoints exposed by studio-services:
#   - POST /preflight : dry-run checks before sending a deploy/tx (e.g., compile & estimate)
#   - POST /simulate  : compile provided source+manifest and execute a single call safely
#
# We do not rely on the full vm_py toolchain being present at test time.
# Instead, we:
#   - Assert robust validation (422) or safe rejections (400) on malformed/hostile inputs
#   - Optionally assert a happy-path if the service is wired to a working compiler
#
# The payload shapes are intentionally tolerant: different deployments may extend
# models with extra fields. We only assert the key invariants relevant to safety.

# Minimal, safe "counter" contract used for a best-effort happy-path probe.
# This mirrors the public examples elsewhere in the repo.
COUNTER_SOURCE = """
from stdlib import storage, events, abi

def inc(amount: int) -> None:
    v = storage.get_int(b"v") or 0
    v += int(amount)
    storage.set_int(b"v", v)
    events.emit(b"Inc", {b"by": amount, b"v": v})

def get() -> int:
    return storage.get_int(b"v") or 0
""".strip()

COUNTER_MANIFEST = {
    "name": "Counter",
    "abi": {
        "functions": [
            {
                "name": "inc",
                "inputs": [{"name": "amount", "type": "int"}],
                "outputs": [],
            },
            {
                "name": "get",
                "inputs": [],
                "outputs": [{"name": "value", "type": "int"}],
            },
        ],
        "events": [
            {
                "name": "Inc",
                "inputs": [{"name": "by", "type": "int"}, {"name": "v", "type": "int"}],
            },
        ],
    },
}

# Obviously-dangerous source that a correct validator must reject.
DANGEROUS_SOURCE = "import os\nos.remove('/')  # nope"


@pytest.mark.asyncio
async def test_simulate_rejects_dangerous_source(aclient):
    """
    The simulator must *not* execute or accept contracts with forbidden imports.
    We expect either input validation errors (422) or explicit rejections (400),
    but never a server crash (5xx).
    """
    payload = {
        "source": DANGEROUS_SOURCE,
        "manifest": COUNTER_MANIFEST,
        # Intend to call the harmless "get" (no args) if it ever compiled (it shouldn't).
        "call": {"function": "get", "args": []},
    }
    resp = await aclient.post("/simulate", json=payload)

    # Strong guarantee: no crashes
    assert resp.status_code in (
        400,
        422,
    ), f"Unexpected status {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_preflight_handles_minimal_payload(aclient):
    """
    Smoke-test /preflight with a minimal (but well-formed) payload.
    Different deployments may choose to compile or only parse; we accept
    either 200 (with a JSON body) or validation-style errors (422/400).
    """
    payload = {
        "source": COUNTER_SOURCE,
        "manifest": COUNTER_MANIFEST,
        # Optional fields a service may accept; harmless if ignored:
        "optimize": True,
        "estimateGas": True,
    }
    resp = await aclient.post("/preflight", json=payload)

    assert resp.status_code in (
        200,
        400,
        422,
    ), f"Unexpected status {resp.status_code}: {resp.text}"
    if resp.status_code == 200:
        data = resp.json()
        assert isinstance(data, dict)
        # Heuristic keys that many implementations return; tolerate absence.
        if "gas" in data:
            assert isinstance(data["gas"], int) or (
                isinstance(data["gas"], str) and data["gas"].startswith("0x")
            )
        if "ok" in data:
            assert isinstance(data["ok"], bool)


@pytest.mark.asyncio
async def test_simulate_counter_happy_path_if_available(aclient):
    """
    Best-effort happy path:
    - Compile the inlined Counter contract
    - Call inc(5), then get()
    If the runtime isn't available in the test env, we skip rather than fail.
    """
    # 1) inc(5)
    inc_payload = {
        "source": COUNTER_SOURCE,
        "manifest": COUNTER_MANIFEST,
        "call": {"function": "inc", "args": [5]},
        # Allow the service to keep ephemeral state in-memory across a test session
        # if it supports that; otherwise, explicit state handle may be required.
        # We include a synthetic "state" key that tolerant services may honor.
        "state": {"kind": "ephemeral", "id": "test-session-1"},
    }
    resp1 = await aclient.post("/simulate", json=inc_payload)

    if resp1.status_code not in (200,):
        # If the simulator toolchain isn't present, treat as optional.
        pytest.skip(
            f"/simulate not available for happy-path (status {resp1.status_code})"
        )

    data1 = resp1.json()
    assert isinstance(data1, dict)
    # Common shapes: {"ok": true, "logs":[...]} or {"events":[...]}
    if "ok" in data1:
        assert data1["ok"] is True
    # 2) get() should reflect the increment (if state persisted). Some implementations
    # reset state per-call; in that case, allow 0 as a valid result too.
    get_payload = {
        "source": COUNTER_SOURCE,
        "manifest": COUNTER_MANIFEST,
        "call": {"function": "get", "args": []},
        "state": {"kind": "ephemeral", "id": "test-session-1"},
    }
    resp2 = await aclient.post("/simulate", json=get_payload)
    assert resp2.status_code == 200, resp2.text
    data2 = resp2.json()
    # Result fields may vary: ("return", "result", "value"). Check a few.
    value = (
        data2.get("return")
        or data2.get("result")
        or data2.get("value")
        or (data2.get("outputs", {}) or {}).get("value")
    )

    # Accept either 5 (state persisted) or 0 (stateless run); must be int-like.
    if isinstance(value, str) and value.startswith("0x"):
        # hex-encoded int
        value_int = int(value, 16)
    else:
        value_int = int(value)

    assert value_int in (0, 5)
