from __future__ import annotations

import pytest

errors_mod = pytest.importorskip("vm_py.errors")
abi_mod = pytest.importorskip("vm_py.stdlib.abi")

VmError = errors_mod.VmError
Revert = errors_mod.Revert


def test_abi_revert_string_message_maps_to_revert_problem() -> None:
    """
    ABI-facing revert(msg) should surface as vm_py.errors.Revert with a
    stable VM_REVERT code and a problem+json payload.

    Flow under test:
        abi.revert("INT256_OUT_OF_RANGE")
          → Revert(...)
          → Revert.to_problem()

    We don't over-specify the exact human detail string, but we do assert:
      - type == "animica://vm/vm_revert"
      - title == "VM_REVERT"
      - deterministic == True
      - context["reason"] carries the ABI message (bytes or str)
    """
    with pytest.raises(Revert) as excinfo:
        abi_mod.revert("INT256_OUT_OF_RANGE")

    err = excinfo.value
    assert isinstance(err, Revert)

    # Core VM error code is symbolic, not numeric, at this layer.
    assert err.code == "VM_REVERT"

    prob = err.to_problem()
    assert prob["type"] == "animica://vm/vm_revert"
    assert prob["title"] == "VM_REVERT"
    assert prob["deterministic"] is True

    # Don't over-assume representation; accept bytes or str.
    ctx = prob.get("context", {})
    assert "reason" in ctx
    reason = ctx["reason"]
    if isinstance(reason, bytes):
        assert reason == b"INT256_OUT_OF_RANGE"
    else:
        assert isinstance(reason, str)
        assert "INT256_OUT_OF_RANGE" in reason


def test_revert_with_data_includes_data_hex_and_roundtrips_via_problem() -> None:
    """
    A Revert instantiated with (reason, data) should:

      - keep code == "VM_REVERT"
      - expose reason and data_hex in context
      - round-trip through VmError.from_problem() and remain a Revert
    """
    reason = "AccessDenied"
    data = b"\xde\xad\xbe\xef\x00"

    err = Revert(reason=reason, data=data)
    assert err.code == "VM_REVERT"

    prob = err.to_problem()
    assert prob["type"] == "animica://vm/vm_revert"
    assert prob["title"] == "VM_REVERT"
    assert prob["deterministic"] is True

    ctx = prob.get("context", {})
    assert ctx.get("reason") == reason
    # data_hex must be 0x-prefixed hex string.
    data_hex = ctx.get("data_hex")
    assert isinstance(data_hex, str)
    assert data_hex.startswith("0x")
    assert data_hex == "0x" + data.hex()

    # Now go back from wire-format to a Python exception.
    err2 = VmError.from_problem(prob)
    assert isinstance(err2, Revert)
    assert err2.code == err.code
    assert err2.message == err.message
    assert err2.context == err.context
    assert err2.deterministic == err.deterministic


def test_unknown_problem_code_maps_back_to_base_vm_error() -> None:
    """
    If a problem+json comes back from some remote node with an unknown VM
    code, VmError.from_problem() should safely map it to the base VmError,
    not crash or mis-classify.

    This covers the "ABI-defined error that this node doesn't recognize"
    scenario: it should still be representable and inspectable.
    """
    problem = {
        "type": "animica://vm/vm_custom_abi_error",
        "title": "VM_CUSTOM_ABI_ERROR",  # not in _CODE_TO_SUBCLASS
        "detail": "Custom ABI error from remote node",
        "deterministic": True,
        "context": {"selector": "0xdeadbeef", "payload": "0x1234"},
    }

    err = VmError.from_problem(problem)
    # Unknown titles fall back to the base VmError type.
    assert isinstance(err, VmError)
    assert not isinstance(err, Revert)

    assert err.code == "VM_CUSTOM_ABI_ERROR"
    assert err.message == "Custom ABI error from remote node"
    assert err.deterministic is True
    assert err.context == problem["context"]
