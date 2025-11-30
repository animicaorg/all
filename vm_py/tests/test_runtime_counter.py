from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import pytest

# --- paths & fixtures ---------------------------------------------------------

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
COUNTER_DIR = ROOT / "examples" / "counter"
MANIFEST_PATH = COUNTER_DIR / "manifest.json"
SOURCE_PATH = COUNTER_DIR / "contract.py"


@pytest.fixture(scope="module")
def have_examples() -> bool:
    return MANIFEST_PATH.exists() and SOURCE_PATH.exists()


# --- resilient loader/engine helpers -----------------------------------------


def _load_module_from_manifest(manifest_path: Path, source_path: Path) -> Any:
    """
    Try hard to compile/link the example contract into a runnable module/IR bytes.
    Works across slightly different loader/encoder APIs.
    """
    # 1) Preferred: loader helpers
    try:
        from vm_py.runtime import loader as ldr  # type: ignore

        # common loader entrypoints (first that works wins)
        candidates = [
            ("load_and_link", {"manifest_path": str(manifest_path)}),
            ("load", {"manifest_path": str(manifest_path)}),
            ("load_manifest", {"path": str(manifest_path)}),
            ("compile_from_manifest", {"manifest_path": str(manifest_path)}),
            ("compile_manifest", {"manifest_path": str(manifest_path)}),
            ("build_from_manifest", {"manifest_path": str(manifest_path)}),
        ]
        for name, kwargs in candidates:
            fn = getattr(ldr, name, None)
            if callable(fn):
                try:
                    return fn(**kwargs)  # type: ignore[misc]
                except Exception:
                    continue

        # 2) Fallback: loader pieces (read files → compile → link)
        mf = json.loads(manifest_path.read_text())
        src = source_path.read_text()

        for name in ("compile_source_and_link", "compile_source", "compile"):
            fn = getattr(ldr, name, None)
            if callable(fn):
                try:
                    mod = fn(src, manifest=mf)  # type: ignore[call-arg]
                    if mod is not None:
                        return mod
                except TypeError:
                    # maybe signature without manifest
                    try:
                        mod = fn(src)  # type: ignore[call-arg]
                        if mod is not None:
                            return mod
                    except Exception:
                        pass
                except Exception:
                    pass
    except Exception:
        pass

    # 3) Last resort: go through compiler.* directly
    try:
        from vm_py.compiler import ast_lower as lower  # type: ignore
        from vm_py.compiler import encode as enc  # type: ignore

        src = source_path.read_text()
        lower_fns = [
            ("lower_source", {"src": src}),
            ("lower", {"src": src}),
            ("compile", {"source": src}),
        ]
        ir_mod = None
        for name, kwargs in lower_fns:
            fn = getattr(lower, name, None)
            if callable(fn):
                try:
                    ir_mod = fn(**kwargs)  # type: ignore[misc]
                    if ir_mod is not None:
                        break
                except Exception:
                    continue
        if ir_mod is None:
            raise RuntimeError("Could not lower source to IR")

        # Return encoded bytes if encoder prefers bytes
        for name in ("encode_module", "dumps", "encode"):
            fn = getattr(enc, name, None)
            if callable(fn):
                try:
                    return fn(ir_mod)  # type: ignore[misc]
                except Exception:
                    continue
        return ir_mod
    except Exception as e:
        raise RuntimeError(f"Failed to compile/load module: {e}") from e


def _engine_new_session(module_or_bytes: Any) -> Tuple[Any, Dict[str, Any]]:
    """
    Create a reusable 'session' for repeated calls so contract state persists.
    Returns (runner, default_kwargs). 'runner' may be a callable or an object with call methods.
    """
    # Prepare a shared state object if the engine supports it
    state_candidates: Iterable[Tuple[str, Dict[str, Any]]] = [
        ("State", {}),
        ("InMemoryState", {}),
        ("Storage", {}),
    ]
    shared_state: Optional[Any] = None
    try:
        from vm_py.runtime import engine as eng  # type: ignore
    except Exception as e:
        raise pytest.skip(f"VM engine is not available: {e}")

    # Try to instantiate an engine instance bound to the module
    # Common constructors across variations
    constructors = [
        ("Instance", {}),
        ("Engine", {}),
        ("Runtime", {}),
        ("VM", {}),
    ]

    # Build or pick a state if engine exposes one
    for cls_name, kwargs in state_candidates:
        st_cls = getattr(eng, cls_name, None)
        if st_cls:
            try:
                shared_state = st_cls(**kwargs)  # type: ignore[call-arg]
                break
            except Exception:
                continue

    # Construct engine instance if possible
    for cls_name, kwargs in constructors:
        cls = getattr(eng, cls_name, None)
        if cls:
            try:
                inst = cls(module_or_bytes, **kwargs)  # type: ignore[misc]
                # Prefer instance methods (call/invoke/run)
                return inst, {"state": shared_state} if shared_state is not None else {}
            except Exception:
                continue

    # If no instance API, fall back to module-level call helpers
    return eng, (
        {"module": module_or_bytes, "state": shared_state}
        if shared_state is not None
        else {"module": module_or_bytes}
    )


def _engine_call(
    runner: Any,
    fn_name: str,
    *,
    args: Optional[list] = None,
    gas_limit: int = 1_000_000,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Execute a contract function and normalize the result into a dict with:
      {'return': Any, 'logs': list, 'gas_used': int}
    Attempts multiple likely method names/signatures.
    """
    args = args or []
    extra = extra or {}

    # Possible invocation APIs on an instance
    call_variants = [
        ("call", {"name": fn_name, "args": args}),
        ("invoke", {"name": fn_name, "args": args}),
        ("run_call", {"func": fn_name, "args": args}),
        ("call_method", {"method": fn_name, "args": args}),
        ("execute", {"entry": fn_name, "args": args}),
        ("run", {"entry": fn_name, "args": args}),
    ]

    # If runner is a module-level engine, provide the module in kwargs
    if "module" in extra:
        mod = extra["module"]
        with_mod = []
        for meth, kwargs in call_variants:
            kw = kwargs.copy()
            # try different param names for the module/program
            for key in ("module", "program", "ir", "code", "contract", "artifact"):
                kw_with = kw.copy()
                kw_with[key] = mod
                with_mod.append((meth, kw_with))
        call_variants = with_mod

    # Add shared state if supported
    if "state" in extra and extra["state"] is not None:
        call_variants = [
            (meth, dict(kwargs, state=extra["state"])) for meth, kwargs in call_variants
        ]

    # Add gas limit parameter variants
    enriched: list[Tuple[str, Dict[str, Any]]] = []
    for meth, kwargs in call_variants:
        for gas_key in ("gas_limit", "gas", "limit"):
            kw = kwargs.copy()
            kw[gas_key] = gas_limit
            enriched.append((meth, kw))
        enriched.append((meth, kwargs))
    call_variants = enriched

    # Try calling
    last_err: Optional[BaseException] = None
    for meth_name, kwargs in call_variants:
        fn = getattr(runner, meth_name, None)
        if not callable(fn):
            continue
        try:
            res = fn(**kwargs)  # type: ignore[misc]
            return _normalize_result(res)
        except TypeError:
            # try positional style (name, args, ...)
            try:
                if "name" in kwargs:
                    name = kwargs.pop("name")
                elif "method" in kwargs:
                    name = kwargs.pop("method")
                elif "func" in kwargs:
                    name = kwargs.pop("func")
                elif "entry" in kwargs:
                    name = kwargs.pop("entry")
                else:
                    raise
                res = fn(name, **kwargs)  # type: ignore[misc]
                return _normalize_result(res)
            except Exception as e:
                last_err = e
                continue
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(
        f"Could not execute function '{fn_name}' via engine API variants; last error: {last_err}"
    )


def _normalize_result(res: Any) -> Dict[str, Any]:
    """
    Normalize various engine return shapes into a dict with 'return', 'logs', 'gas_used'.
    """
    # If already a dict, try to extract fields
    if isinstance(res, dict):
        out = {
            "return": res.get("return")
            or res.get("ret")
            or res.get("value")
            or res.get("result"),
            "logs": res.get("logs") or res.get("events") or [],
            "gas_used": res.get("gas_used")
            or res.get("gas")
            or res.get("used_gas")
            or 0,
        }
        # sometimes nested 'stats'/'metrics' hold gas
        if not out["gas_used"]:
            stats = res.get("stats") or res.get("metrics")
            if isinstance(stats, dict):
                out["gas_used"] = (
                    stats.get("gas_used") or stats.get("gas") or stats.get("used") or 0
                )
        return out

    # Object with attributes
    for getter in (
        lambda o: {
            "return": getattr(o, "return_", None),
            "logs": getattr(o, "logs", None),
            "gas_used": getattr(o, "gas_used", None),
        },
        lambda o: {
            "return": getattr(o, "result", None),
            "logs": getattr(o, "events", None),
            "gas_used": getattr(o, "gas", None),
        },
    ):
        try:
            cand = getter(res)
            if (
                cand["return"] is not None
                or cand["logs"] is not None
                or cand["gas_used"] is not None
            ):
                return {
                    "return": cand["return"],
                    "logs": cand["logs"] or [],
                    "gas_used": cand["gas_used"] or 0,
                }
        except Exception:
            pass

    # Primitive: treat as return value
    return {"return": res, "logs": [], "gas_used": 0}


def _logs_count(obj: Any) -> int:
    if obj is None:
        return 0
    if isinstance(obj, (list, tuple)):
        return len(obj)
    # maybe dict with 'items'
    if isinstance(obj, dict):
        return len(obj.get("items", []))
    return 1


# --- the actual test ----------------------------------------------------------


@pytest.mark.usefixtures("have_examples")
def test_counter_inc_get_roundtrip(have_examples: bool) -> None:
    if not have_examples:
        pytest.skip("Counter example files not present")

    module = _load_module_from_manifest(MANIFEST_PATH, SOURCE_PATH)
    runner, extra = _engine_new_session(module)

    # Baseline get
    r0 = _engine_call(runner, "get", args=[], extra=extra)
    v0 = r0["return"]
    assert isinstance(v0, int), f"expected integer return from get(), got: {v0!r}"
    gas_get0 = int(r0["gas_used"]) if r0["gas_used"] is not None else 0
    assert gas_get0 >= 0

    # First inc
    l0 = _logs_count(r0["logs"])
    ri = _engine_call(runner, "inc", args=[], extra=extra)
    gas_inc = int(ri["gas_used"]) if ri["gas_used"] is not None else 0
    assert gas_inc >= 0
    # after inc, logs may have increased (contract emits), so allow >=
    li = _logs_count(ri["logs"])
    assert li >= l0

    # get again; value should advance by +1
    r1 = _engine_call(runner, "get", args=[], extra=extra)
    v1 = r1["return"]
    assert isinstance(v1, int)
    assert v1 == v0 + 1, f"counter should increment by 1: got {v0} -> {v1}"

    # gas shape sanity: inc should generally cost >= get
    gas_get1 = int(r1["gas_used"]) if r1["gas_used"] is not None else 0
    if gas_get0 and gas_inc:
        assert (
            gas_inc >= gas_get1
        ), f"inc gas ({gas_inc}) should be >= get gas ({gas_get1}) in typical implementations"
