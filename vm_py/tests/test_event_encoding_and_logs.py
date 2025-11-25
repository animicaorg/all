from __future__ import annotations

from typing import Any, Dict

import pytest

from vm_py.runtime.events_api import (
    VmError,
    clear_events,
    emit,
    events_for_receipt,
    get_events,
)


def _args_to_dict(args: Any) -> Dict[str, Any]:
    """
    Normalize the internal args representation into a simple
    {name: value} mapping so tests don't depend on the exact
    container type (tuple/list/dataclass/dict).
    """
    if isinstance(args, dict):
        return dict(args)

    out: Dict[str, Any] = {}

    # Common shapes: list/tuple of (k, v) or small objects/dicts
    # with "k"/"v" attributes/keys.
    if isinstance(args, (list, tuple)):
        for item in args:
            # (key, value) pairs
            if isinstance(item, tuple) and len(item) == 2:
                k, v = item
                out[k] = v
                continue

            # Dict-like with "k" / "v"
            if isinstance(item, dict) and "k" in item and "v" in item:
                out[item["k"]] = item["v"]
                continue

            # Object-like with .k / .v
            if hasattr(item, "k") and hasattr(item, "v"):
                out[getattr(item, "k")] = getattr(item, "v")
                continue

    return out


def test_emit_and_get_events_preserve_order_and_args() -> None:
    """
    Basic sanity check: events are stored in emit order and args round-trip.
    """
    clear_events()

    emit(b"Alpha", {"x": 1})
    emit(b"Beta", {"x": 3, "y": 2})

    events = get_events()
    # Order must be preserved
    assert [e.name for e in events] == [b"Alpha", b"Beta"]

    first_args = _args_to_dict(events[0].args)
    second_args = _args_to_dict(events[1].args)

    assert first_args == {"x": 1}
    assert second_args == {"x": 3, "y": 2}


def test_events_for_receipt_encodes_types_and_hex() -> None:
    """
    events_for_receipt should return CanonicalEvent objects with:
      - name: 0x-prefixed hex string of the event name bytes
      - args: iterable of {"k","t","v"} dicts where:
          t="b" => bytes encoded as 0x-prefixed hex
          t="i" => integer
          t="z" => boolean
    """
    clear_events()

    emit(
        b"Demo",
        {
            "bin": b"\x01\x02",
            "n": 42,
            "flag": True,
        },
    )

    receipt = events_for_receipt()
    assert len(receipt) == 1
    ev = receipt[0]

    # Name is 0x-prefixed hex of the original bytes
    assert ev.name == "0x" + b"Demo".hex()

    # args is a list/tuple of {"k","t","v"} dicts
    args_by_key = {a["k"]: a for a in ev.args}

    bin_arg = args_by_key["bin"]
    assert bin_arg["t"] == "b"
    assert bin_arg["v"] == "0x0102"

    n_arg = args_by_key["n"]
    assert n_arg["t"] == "i"
    assert n_arg["v"] == 42

    flag_arg = args_by_key["flag"]
    assert flag_arg["t"] == "z"
    assert flag_arg["v"] is True


def test_invalid_event_names_and_args_raise_error() -> None:
    """
    Guard rails:

      - name must be non-empty bytes
      - keys must be str
      - values must be bytes/int/bool and within limits
    """
    clear_events()

    # Empty name
    with pytest.raises(VmError):
        emit(b"", {"ok": True})

    # Name must be bytes, not str
    with pytest.raises(VmError):
        emit("NotBytes", {"ok": True})  # type: ignore[arg-type]

    # Keys must be str
    with pytest.raises(VmError):
        emit(b"BadKey", {b"k": 1})  # type: ignore[arg-type]

    # Values must be of an allowed type (float should fail)
    with pytest.raises(VmError):
        emit(b"BadVal", {"x": 1.234})  # type: ignore[arg-type]


def test_clear_events_resets_sink_and_receipt_view() -> None:
    """
    clear_events() should wipe both the in-memory sink and the
    receipt-oriented view.
    """
    clear_events()

    emit(b"A", {"n": 1})
    assert len(get_events()) == 1
    assert len(events_for_receipt()) == 1

    clear_events()
    assert get_events() == []
    assert events_for_receipt() == []
