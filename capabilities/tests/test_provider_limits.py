import math
import types
from typing import Callable, Optional

import pytest

# These exceptions are part of the public surface for capability errors.
try:
    from capabilities.errors import LimitExceeded, NotDeterministic
except Exception as e:  # pragma: no cover
    pytest.skip(f"capabilities.errors not available: {e}")


# Try to import the determinism helpers; if the module isn't present in this
# environment (or has different names), we adapt and/or skip selectively.
try:
    import capabilities.runtime.determinism as det  # type: ignore
except Exception as e:  # pragma: no cover
    det = None  # type: ignore
    DET_IMPORT_ERR = e  # type: ignore
else:
    DET_IMPORT_ERR = None  # type: ignore


def _find_enforcer_bytes() -> Optional[Callable[..., object]]:
    """
    Find a function in capabilities.runtime.determinism that enforces a maximum
    number of bytes, raising LimitExceeded on overflow.

    Common names we support:
      - require_max_bytes(data: bytes, max_bytes: int, label: str = ...)
      - enforce_max_bytes(...)
      - ensure_max_bytes(...)
      - guard_input_size(...)
    """
    if det is None:  # pragma: no cover
        return None

    for name in (
        "require_max_bytes",
        "enforce_max_bytes",
        "ensure_max_bytes",
        "guard_input_size",
    ):
        fn = getattr(det, name, None)
        if callable(fn):
            return fn
    return None


def _find_enforcer_text() -> Optional[Callable[..., object]]:
    """
    Find a function that enforces a maximum *UTF-8* size for text inputs, either
    by raising LimitExceeded or returning a clamped/sanitized string.
      - clamp_text(text: str, max_bytes: int, label: str = ...)
      - enforce_text_bytes(...)
      - require_text_max_bytes(...)
    """
    if det is None:  # pragma: no cover
        return None

    for name in ("clamp_text", "enforce_text_bytes", "require_text_max_bytes"):
        fn = getattr(det, name, None)
        if callable(fn):
            return fn
    return None


def _find_json_sanitizer() -> Optional[Callable[..., object]]:
    """
    Find a function that rejects non-deterministic JSON (NaN, +/-Inf, decimals w/o canonical form).
      - sanitize_json(obj, *, reject_nondet=True)
      - ensure_deterministic_json(obj)
      - validate_json_determinism(obj)
    """
    if det is None:  # pragma: no cover
        return None

    for name in ("sanitize_json", "ensure_deterministic_json", "validate_json_determinism"):
        fn = getattr(det, name, None)
        if callable(fn):
            return fn
    return None


@pytest.mark.skipif(det is None, reason=lambda: f"determinism module not importable: {DET_IMPORT_ERR}")
def test_bytes_enforcer_raises_over_cap():
    fn = _find_enforcer_bytes()
    if fn is None:
        pytest.skip("No byte-size enforcer found in capabilities.runtime.determinism")

    data = b"x" * 33
    with pytest.raises(LimitExceeded):
        # Most enforcers follow (data, max_bytes, label=...) but accept extra kwargs.
        fn(data, 32, "prompt")  # type: ignore[misc]


@pytest.mark.skipif(det is None, reason=lambda: f"determinism module not importable: {DET_IMPORT_ERR}")
def test_text_enforcer_under_and_over_limits():
    fn = _find_enforcer_text()
    if fn is None:
        pytest.skip("No text-size enforcer/clamp found in capabilities.runtime.determinism")

    # Exactly at the limit (in bytes) should pass.
    text_at = "a" * 32
    try:
        out = fn(text_at, 32, "model_prompt")  # type: ignore[misc]
        # If it returns the string, ensure it preserved content/size.
        if isinstance(out, str):
            assert out == text_at
            assert len(out.encode("utf-8")) == 32
    except LimitExceeded:
        pytest.fail("Text enforcer raised at limit boundary (should allow exactly-on-cap inputs)")

    # Over the limit should either raise or clamp strictly within the budget.
    text_over = "a" * 40
    try:
        out = fn(text_over, 32, "model_prompt")  # type: ignore[misc]
        # If the API clamps rather than raises, the clamped output must obey the budget.
        assert isinstance(out, str)
        assert len(out.encode("utf-8")) <= 32
    except LimitExceeded:
        # Raising is also acceptable; that's the stronger contract.
        pass


@pytest.mark.skipif(det is None, reason=lambda: f"determinism module not importable: {DET_IMPORT_ERR}")
def test_json_sanitizer_rejects_nondeterministic_numbers():
    sanitizer = _find_json_sanitizer()
    if sanitizer is None:
        pytest.skip("No JSON determinism sanitizer found")

    bads = [
        {"v": float("nan")},
        {"v": float("inf")},
        {"v": -float("inf")},
    ]
    for obj in bads:
        with pytest.raises(NotDeterministic):
            # Support both positional and keyword-only signatures.
            try:
                sanitizer(obj, reject_nondet=True)  # type: ignore[misc]
            except TypeError:
                sanitizer(obj)  # type: ignore[misc]

    # A good, canonical payload should pass without exception.
    ok = {"model": "tiny", "prompt": "hello", "params": {"temperature": 0, "top_k": 0}}
    try:
        sanitizer(ok, reject_nondet=True)  # type: ignore[misc]
    except TypeError:
        sanitizer(ok)  # type: ignore[misc]


# ------- Optional integration: ensure host.compute integrates the enforcers -------

@pytest.mark.skipif(det is None, reason=lambda: f"determinism module not importable: {DET_IMPORT_ERR}")
def test_ai_enqueue_respects_prompt_cap(monkeypatch):
    """
    If capabilities.host.compute.ai_enqueue exists and uses the determinism
    helper, a long prompt should cause LimitExceeded. If the function is not
    present (or signature differs), we skip gracefully.
    """
    try:
        from capabilities.host import compute as host_compute  # type: ignore
    except Exception:
        pytest.skip("capabilities.host.compute not available")

    ai_enqueue = getattr(host_compute, "ai_enqueue", None)
    if not callable(ai_enqueue):  # pragma: no cover
        pytest.skip("host.compute.ai_enqueue not implemented")

    # Wrap whatever byte-size enforcer exists to force a small cap (=16 bytes)
    # regardless of underlying defaults, so we can deterministically trigger.
    enforcer = _find_enforcer_bytes()
    if enforcer is None:
        pytest.skip("No byte-size enforcer to hook; cannot validate integration")

    def strict_enforcer(data: bytes, max_bytes: int = 1 << 30, label: str = "prompt", **_: object) -> None:
        # Ignore provided max_bytes and enforce 16 for this test.
        if len(data) > 16:
            raise LimitExceeded(f"{label} exceeds 16 bytes in test-hook")

    # Monkeypatch the enforcer into the determinism module used by compute.
    monkeypatch.setattr(det, enforcer.__name__, strict_enforcer, raising=True)

    # Now a long prompt must fail through the integration.
    with pytest.raises(LimitExceeded):
        ai_enqueue(model="dev/tiny", prompt="X" * 64)  # type: ignore[misc]

    # A short prompt should succeed and produce a receipt-like object.
    ok = ai_enqueue(model="dev/tiny", prompt="short")  # type: ignore[misc]
    assert isinstance(ok, (dict, types.SimpleNamespace))
    # Best-effort shape checks:
    if isinstance(ok, dict):
        assert "task_id" in ok or "receipt" in ok
    else:
        assert hasattr(ok, "task_id") or hasattr(ok, "receipt")


@pytest.mark.skipif(det is None, reason=lambda: f"determinism module not importable: {DET_IMPORT_ERR}")
def test_quantum_enqueue_respects_circuit_cap(monkeypatch):
    """
    Same idea as the AI prompt, but for a quantum circuit payload.
    """
    try:
        from capabilities.host import compute as host_compute  # type: ignore
    except Exception:
        pytest.skip("capabilities.host.compute not available")

    q_enqueue = getattr(host_compute, "quantum_enqueue", None)
    if not callable(q_enqueue):  # pragma: no cover
        pytest.skip("host.compute.quantum_enqueue not implemented")

    enforcer = _find_enforcer_bytes()
    if enforcer is None:
        pytest.skip("No byte-size enforcer to hook; cannot validate integration")

    def strict_enforcer(data: bytes, max_bytes: int = 1 << 30, label: str = "circuit", **_: object) -> None:
        if len(data) > 64:
            raise LimitExceeded(f"{label} exceeds 64 bytes in test-hook")

    monkeypatch.setattr(det, enforcer.__name__, strict_enforcer, raising=True)

    # A tiny “circuit” (as JSON/bytes) should pass.
    ok = q_enqueue(circuit={"ops": [{"h": 0}, {"cx": [0, 1]}]}, shots=8)  # type: ignore[misc]
    assert isinstance(ok, (dict, types.SimpleNamespace))

    # A very large circuit blob should fail.
    big_blob = {"ops": [{"u": "x" * 1024}]}
    with pytest.raises(LimitExceeded):
        q_enqueue(circuit=big_blob, shots=1)  # type: ignore[misc]
