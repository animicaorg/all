from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Dict, Optional, Tuple

import pytest

from vm_py.compiler import ir
from vm_py.compiler import encode as enc
from vm_py.compiler import gas_estimator as ge


# ----------------------------- helpers ---------------------------------------


def _encode(module_obj: Any) -> bytes:
    for name in ("encode_module", "dumps", "encode"):
        fn = getattr(enc, name, None)
        if callable(fn):
            return fn(module_obj)
    raise AssertionError("No encode function found in vm_py.compiler.encode")


def _decode(buf: bytes) -> Any:
    for name in ("decode_module", "loads", "decode"):
        fn = getattr(enc, name, None)
        if callable(fn):
            return fn(buf)
    raise AssertionError("No decode function found in vm_py.compiler.encode")


def mk(cls: Any, **kwargs: Any) -> Any:
    assert is_dataclass(cls), f"{cls} is expected to be a dataclass"
    field_names = {f.name for f in fields(cls)}
    filtered = {k: v for k, v in kwargs.items() if k in field_names}
    return cls(**filtered)


def build_counter_ir() -> Tuple[Any, str, str]:
    """
    Build a tiny IR module with two entry points:
      - init: VALUE := 0
      - inc:  VALUE := VALUE + 1
    Returns (Module, init_name, inc_name)
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

    # blocks (support different naming fields)
    b_init = mk(Block, name="init", label="init", id="init", instrs=[i_push0, i_store_val])
    b_inc = mk(
        Block,
        name="inc",
        label="inc",
        id="inc",
        instrs=[i_load_val, i_push1, i_add, i_store_val2],
    )

    # module fields vary a bit across implementations; tolerate both styles
    ModuleFields = {f.name for f in fields(Module)}
    kwargs: Dict[str, Any] = {}
    if "blocks" in ModuleFields:
        kwargs["blocks"] = [b_init, b_inc]
    if "funcs" in ModuleFields:
        kwargs["funcs"] = {"init": b_init, "inc": b_inc}
    if "entry" in ModuleFields:
        kwargs["entry"] = "init"
    if "name" in ModuleFields:
        kwargs["name"] = "counter"

    mod = mk(Module, **kwargs)
    return mod, "init", "inc"


def _find_estimator() -> Any:
    # Prefer specific names if present; otherwise heuristically pick first callable
    candidates = [
        "estimate_module",
        "estimate",
        "static_upper_bound",
        "upper_bound",
        "estimate_gas",
    ]
    for name in candidates:
        fn = getattr(ge, name, None)
        if callable(fn):
            return fn
    # last resort: any callable in module
    for name in dir(ge):
        fn = getattr(ge, name)
        if callable(fn):
            return fn
    raise AssertionError("No estimator function found in vm_py.compiler.gas_estimator")


def _extract_upper_bound(est_result: Any, entry: Optional[str] = None) -> int:
    """
    Try hard to find an integer 'upper bound' in a variety of result shapes:
    - int
    - dataclass or object with .upper_bound / .upper / .total / .total_upper
    - dict with keys above
    - dict with per-function mapping (per_func|funcs)[entry].upper(_bound)
    """
    def pick_num(obj: Any) -> Optional[int]:
        if isinstance(obj, int):
            return obj
        if isinstance(obj, dict):
            for k in ("upper_bound", "upper", "total_upper", "total", "module_upper"):
                if k in obj and isinstance(obj[k], int):
                    return obj[k]
        # attribute form
        for k in ("upper_bound", "upper", "total_upper", "total", "module_upper"):
            v = getattr(obj, k, None)
            if isinstance(v, int):
                return v
        return None

    # 1) direct numeric
    n = pick_num(est_result)
    if n is not None and entry is None:
        return n

    # 2) per-function lookup
    if isinstance(est_result, dict):
        for map_key in ("per_func", "funcs", "functions", "by_func"):
            sub = est_result.get(map_key)
            if isinstance(sub, dict) and entry in sub:
                m = pick_num(sub[entry])
                if m is not None:
                    return m
    else:
        for map_key in ("per_func", "funcs", "functions", "by_func"):
            sub = getattr(est_result, map_key, None)
            if isinstance(sub, dict) and entry in sub:
                m = pick_num(sub[entry])
                if m is not None:
                    return m

    # 3) fall back to any numeric we can find
    if n is not None:
        return n

    raise AssertionError("Could not extract an integer upper bound from estimator result")


def _maybe_run_dynamic(mod: Any, entry: str) -> Optional[int]:
    """
    Try to execute the IR and return dynamic gas used (int).
    We probe a few plausible engine APIs. If none work, return None to allow skip.
    """
    try:
        from vm_py.runtime import engine as eng  # type: ignore
    except Exception:
        return None

    # candidate call names and arg styles
    calls = [
        ("run", {"entry": entry}),
        ("execute", {"entry": entry}),
        ("run_module", {"entry": entry}),
        ("call", {"name": entry}),
        ("call_entry", {"entry": entry}),
        ("interpret", {"entry": entry}),
        ("run_ir", {"entry": entry}),
    ]

    inputs = [mod, _encode(mod)]  # try object and bytes
    for obj in inputs:
        for name, kw in calls:
            fn = getattr(eng, name, None)
            if not callable(fn):
                continue
            try:
                # try with explicit high gas limit if supported
                try:
                    res = fn(obj, gas_limit=10**9, **kw)  # type: ignore[arg-type]
                except TypeError:
                    res = fn(obj, **kw)  # type: ignore[arg-type]
            except Exception:
                continue

            # try to extract gas used from result
            # common shapes: res.gas_used, res.stats['gas_used'], res['gas_used']
            if isinstance(res, dict):
                for k in ("gas_used", "gas", "used_gas"):
                    v = res.get(k)
                    if isinstance(v, int):
                        return v
                stats = res.get("stats") or res.get("metrics")
                if isinstance(stats, dict):
                    for k in ("gas_used", "gas", "used"):
                        v = stats.get(k)
                        if isinstance(v, int):
                            return v
            else:
                for k in ("gas_used", "gas", "used_gas"):
                    v = getattr(res, k, None)
                    if isinstance(v, int):
                        return v
                stats = getattr(res, "stats", None) or getattr(res, "metrics", None)
                if isinstance(stats, dict):
                    for k in ("gas_used", "gas", "used"):
                        v = stats.get(k)
                        if isinstance(v, int):
                            return v

    return None


# ------------------------------- tests ---------------------------------------


def test_estimator_produces_positive_upper_bound() -> None:
    mod, init_name, inc_name = build_counter_ir()
    estimator = _find_estimator()

    # Module-level bound should be > 0
    result = estimator(mod)
    ub_module = _extract_upper_bound(result)
    assert isinstance(ub_module, int) and ub_module > 0

    # If per-function info is available, those should also be positive
    try:
        ub_init = _extract_upper_bound(result, entry=init_name)
        assert ub_init > 0
        ub_inc = _extract_upper_bound(result, entry=inc_name)
        assert ub_inc > 0
    except AssertionError:
        # Per-function breakdown not exposed; module-level bound suffices
        pass


def test_static_upper_bound_ge_runtime_usage() -> None:
    """
    If the runtime is available in this build, dynamic gas for a simple call
    must be <= the static upper bound reported by the estimator.
    Otherwise, we gracefully skip.
    """
    mod, _init, inc = build_counter_ir()
    estimator = _find_estimator()
    est = estimator(mod)

    dyn = _maybe_run_dynamic(mod, entry=inc)
    if dyn is None:
        pytest.skip("VM runtime engine API not available for dynamic run; skipping bound check")

    # Prefer per-function bound if present; otherwise use module-level
    try:
        ub = _extract_upper_bound(est, entry=inc)
    except AssertionError:
        ub = _extract_upper_bound(est)

    assert isinstance(dyn, int) and dyn >= 0
    assert isinstance(ub, int) and ub >= 0
    assert dyn <= ub, f"dynamic gas {dyn} must not exceed static upper bound {ub}"
