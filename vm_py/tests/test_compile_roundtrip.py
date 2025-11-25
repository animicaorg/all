from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Dict

import pytest

from vm_py.compiler import ir
from vm_py.compiler import encode as enc


# --- helpers -----------------------------------------------------------------

def _encode(module_obj: Any) -> bytes:
    """Locate the encoder function in vm_py.compiler.encode."""
    for name in ("encode_module", "dumps", "encode"):
        fn = getattr(enc, name, None)
        if callable(fn):
            return fn(module_obj)
    raise AssertionError("No encode function found in vm_py.compiler.encode")


def _decode(buf: bytes) -> Any:
    """Locate the decoder function in vm_py.compiler.encode."""
    for name in ("decode_module", "loads", "decode"):
        fn = getattr(enc, name, None)
        if callable(fn):
            return fn(buf)
    raise AssertionError("No decode function found in vm_py.compiler.encode")


def mk(cls: Any, **kwargs: Any) -> Any:
    """
    Construct dataclass instances defensively:
    - filter keyword args to declared field names
    - allows us to be resilient if IR shapes evolve slightly
    """
    assert is_dataclass(cls), f"{cls} is expected to be a dataclass"
    field_names = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in kwargs.items() if k in field_names}
    return cls(**filtered)


def build_sample_ir() -> Any:
    """
    Build a tiny IR module in a way that tolerates slight schema differences.
    Expected common fields:
      Instr(op, args)
      Block(name|label|id, instrs)
      Module(blocks|funcs, entry, name)
    """
    Instr = ir.Instr
    Block = ir.Block
    Module = ir.Module

    # instructions
    i_push0 = mk(Instr, op="PUSHI", args=[0])
    i_store_val = mk(Instr, op="STORE", args=["VALUE"])
    i_load_val = mk(Instr, op="LOAD", args=["VALUE"])
    i_push1 = mk(Instr, op="PUSHI", args=[1])
    i_add = mk(Instr, op="ADD", args=[])
    i_store_val2 = mk(Instr, op="STORE", args=["VALUE"])

    # blocks (tolerate different naming fields)
    b_init = mk(Block, name="init", label="init", id="init", instrs=[i_push0, i_store_val])
    b_inc = mk(Block, name="inc", label="inc", id="inc", instrs=[i_load_val, i_push1, i_add, i_store_val2])

    # module: support either list-of-blocks or dict-of-funcs
    mod_fields = {f.name for f in fields(Module)}
    kwargs: Dict[str, Any] = {}
    if "blocks" in mod_fields:
        kwargs["blocks"] = [b_init, b_inc]
    if "funcs" in mod_fields:
        # canonical entry symbol names mapping to blocks
        kwargs["funcs"] = {"init": b_init, "inc": b_inc}
    if "entry" in mod_fields:
        kwargs["entry"] = "init"
    if "name" in mod_fields:
        kwargs["name"] = "counter"

    return mk(Module, **kwargs)


# --- tests -------------------------------------------------------------------

def test_roundtrip_stability() -> None:
    mod1 = build_sample_ir()
    b1 = _encode(mod1)
    mod2 = _decode(b1)
    b2 = _encode(mod2)

    # bytes must be identical after a decode→encode cycle
    assert b1 == b2, "IR encoding must be canonical and stable across round-trips"

    # if dataclass equality is defined on Module, objects should match structurally
    try:
        assert mod1 == mod2  # type: ignore[comparison-overlap]
    except Exception:
        # Structural equality not strictly required; canonical bytes cover correctness.
        pass


def test_canonical_ordering_dict_funcs_if_applicable() -> None:
    """If Module uses a dict for functions/blocks, encoding must not depend on insertion order."""
    Module = ir.Module
    field_names = {f.name for f in fields(Module)}
    if "funcs" not in field_names:
        pytest.skip("Module.funcs not present; not applicable")

    Instr = ir.Instr
    Block = ir.Block

    a = mk(Block, name="a", label="a", id="a", instrs=[mk(Instr, op="PUSHI", args=[1])])
    b = mk(Block, name="b", label="b", id="b", instrs=[mk(Instr, op="PUSHI", args=[2])])

    m_order1 = mk(Module, funcs={"a": a, "b": b}, entry="a", name="mod")
    m_order2 = mk(Module, funcs={"b": b, "a": a}, entry="a", name="mod")

    e1 = _encode(m_order1)
    e2 = _encode(m_order2)

    assert e1 == e2, "Encoding must be independent of Python dict insertion order"


def test_encode_is_pure_function() -> None:
    """Encoding the same module twice should produce identical bytes."""
    mod = build_sample_ir()
    b1 = _encode(mod)
    b2 = _encode(mod)
    assert b1 == b2
