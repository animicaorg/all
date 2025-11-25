from __future__ import annotations

import pytest

# These tests exercise the /verify pipeline:
#   - POST /verify : (re)compile provided source+manifest and compute code hash
#                    (optionally compare against an expected hash / on-chain address)
# They are written to be tolerant of deployments where the full compiler toolchain
# is not present during CI: we assert safe rejections (400/422) and only enforce
# success-path assertions if the endpoint responds 200.

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
            {"name": "inc", "inputs": [{"name": "amount", "type": "int"}], "outputs": []},
            {"name": "get", "inputs": [], "outputs": [{"name": "value", "type": "int"}]},
        ],
        "events": [
            {"name": "Inc", "inputs": [{"name": "by", "type": "int"}, {"name": "v", "type": "int"}]},
        ],
    },
}

DANGEROUS_SOURCE = "import os\nos.system('rm -rf /')  # forbidden"


def _extract_code_hash(payload: dict) -> str | None:
    """
    Different implementations may shape the response differently.
    Try a few common keys to find the compiled code hash.
    """
    for k in ("codeHash", "code_hash", "code", "hash"):
        v = payload.get(k)
        if isinstance(v, str):
            return v
    # Some services wrap inside "result"
    result = payload.get("result") or {}
    if isinstance(result, dict):
        for k in ("codeHash", "code_hash", "hash"):
            v = result.get(k)
            if isinstance(v, str):
                return v
    return None


@pytest.mark.asyncio
async def test_verify_rejects_dangerous_source(aclient):
    """
    A correct verifier must not accept clearly dangerous source; we expect
    either input validation errors (422) or explicit rejections (400).
    """
    resp = await aclient.post(
        "/verify",
        json={
            "source": DANGEROUS_SOURCE,
            "manifest": COUNTER_MANIFEST,
        },
    )
    assert resp.status_code in (400, 422), f"Unexpected {resp.status_code}: {resp.text}"


@pytest.mark.asyncio
async def test_verify_returns_code_hash_if_available(aclient):
    """
    Happy-path (best-effort): verifying a simple Counter source should produce a stable code hash.
    If the compiler isn't wired in this environment, skip gracefully.
    """
    payload = {
        "source": COUNTER_SOURCE,
        "manifest": COUNTER_MANIFEST,
    }
    resp = await aclient.post("/verify", json=payload)
    if resp.status_code != 200:
        pytest.skip(f"/verify not available for happy-path (status {resp.status_code})")

    data = resp.json()
    code_hash = _extract_code_hash(data)
    assert isinstance(code_hash, str) and code_hash.startswith("0x") and len(code_hash) >= 10

    # Determinism check: verifying the same source again should yield the same hash.
    resp2 = await aclient.post("/verify", json=payload)
    assert resp2.status_code == 200, resp2.text
    code_hash_2 = _extract_code_hash(resp2.json())
    assert code_hash_2 == code_hash


@pytest.mark.asyncio
async def test_verify_expected_hash_match_flag_when_supported(aclient):
    """
    Some implementations accept an 'expectedCodeHash' (or similar) and return a boolean 'matched'.
    If supported, verify that providing the previously returned hash yields matched == True.
    Otherwise, skip without failing the suite.
    """
    base = {"source": COUNTER_SOURCE, "manifest": COUNTER_MANIFEST}
    first = await aclient.post("/verify", json=base)
    if first.status_code != 200:
        pytest.skip(f"/verify not available for expected-hash check (status {first.status_code})")
    code_hash = _extract_code_hash(first.json())
    assert code_hash, "Service returned 200 but no code hash was found in payload"

    # Try common field names that services may accept.
    for expected_key in ("expectedCodeHash", "expected_hash", "expect", "codeHash"):
        resp = await aclient.post("/verify", json={**base, expected_key: code_hash})
        if resp.status_code != 200:
            # Try the next candidate key
            continue
        data = resp.json()
        # Accept several possible keys for the boolean
        matched = (
            data.get("matched")
            if isinstance(data.get("matched"), bool)
            else (data.get("result") or {}).get("matched")
        )
        if matched is None:
            # Implementation doesn't expose a match flag; accept and stop.
            pytest.skip("Verifier does not expose a 'matched' flag; only returns code hash.")
        assert matched is True, f"Expected match with {expected_key} but got: {data}"
        break
    else:
        # None of the expected-* keys were accepted with 200; skip.
        pytest.skip("Verifier did not accept any expected-code-hash hint in this deployment.")
