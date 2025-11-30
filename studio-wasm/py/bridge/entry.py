from __future__ import annotations

"""
Pyodide/WASM bridge entrypoints.

Exports simple, JSON/bytes-friendly helpers used by the JS library:

- version() -> str
- compile_bytes(manifest_json, *, source_bytes=None, ir_bytes=None, ir_obj_json=None) -> dict
- run_call(code_b64|code_bytes, fn, args, *, gas_limit=500_000, ctx=None, state=None) -> dict
- simulate_tx(manifest_json, code_b64|code_bytes, fn, args, *, gas_limit=500_000, ctx=None, state=None) -> dict

Notes
-----
- All inputs are friendly to browser messaging: bytes/str/JSON-like objects.
- Results are plain dicts/JSON-serializable (code is base64 when returned).
- `ctx` is optional execution context. When absent, a deterministic default is used.
- `state` defaults to a simple in-memory key/value store (python dict). The VM
  runtime adapts to this mapping (only used by the stdlib.storage helpers).
"""

import base64
import hashlib
import json
from typing import Any, Dict, Optional, Sequence, Tuple, Union, cast

from ..vm_pkg.compiler import ir as irmod
from ..vm_pkg.compiler.encode import decode_ir, encode_ir
from ..vm_pkg.compiler.gas_estimator import estimate_entry
from ..vm_pkg.errors import ValidationError, VmError
# vm_pkg (trimmed in-browser VM)
from ..vm_pkg.loader import build_from_ir, build_from_source, load_manifest
from ..vm_pkg.runtime import abi as rt_abi  # type: ignore
from ..vm_pkg.runtime import context as rt_ctx  # type: ignore

# ----------------------------- Public API -----------------------------


def version() -> str:
    """Return a simple version banner from vm_pkg (falls back to a static string)."""
    try:
        from ..vm_pkg import __version__  # type: ignore

        return str(__version__)
    except Exception:
        return "vm-pkg/0.1.0"


def compile_bytes(
    manifest_json: Union[str, bytes, bytearray],
    *,
    source_bytes: Optional[Union[bytes, bytearray]] = None,
    ir_bytes: Optional[Union[bytes, bytearray]] = None,
    ir_obj_json: Optional[Union[str, bytes, bytearray]] = None,
) -> Dict[str, Any]:
    """
    Build a contract bundle from either Python source or IR input.

    Returns
    -------
    {
      "ok": true,
      "manifest": {...},
      "abi": {...},
      "entry": "main",
      "code_b64": "...",
      "code_hash": "sha3_256(hex)",
      "gas_upper_bound": 12345,
      "ir_summary": {"functions": 2, "instructions": 17}
    }
    """
    manifest = load_manifest(manifest_json)

    # Prefer explicit IR bytes/object when provided; else compile source.
    bundle = None
    if ir_bytes is not None:
        bundle = build_from_ir(manifest, ir_bytes)
    elif ir_obj_json is not None:
        try:
            obj = (
                json.loads(ir_obj_json)
                if isinstance(ir_obj_json, (str, bytes, bytearray))
                else ir_obj_json
            )
        except Exception as e:
            raise ValidationError(f"ir_obj_json parse error: {e}") from e
        bundle = build_from_ir(manifest, cast(Dict[str, Any], obj))
    elif source_bytes is not None:
        src = source_bytes.decode("utf-8")
        bundle = build_from_source(manifest, src)
    else:
        raise ValidationError(
            "one of source_bytes, ir_bytes, ir_obj_json must be provided"
        )

    # Compute a conservative upper bound for entry function.
    gas_upper = _safe_estimate_entry(bundle.ir)

    # Summarize IR for the UI
    fn_cnt, ins_cnt = _ir_stats(bundle.ir)

    return {
        "ok": True,
        "manifest": bundle.manifest,
        "abi": bundle.abi,
        "entry": bundle.ir.entry,
        "code_b64": base64.b64encode(bundle.code).decode("ascii"),
        "code_hash": bundle.code_hash_hex,
        "gas_upper_bound": gas_upper,
        "ir_summary": {"functions": fn_cnt, "instructions": ins_cnt},
    }


def run_call(
    code: Union[str, bytes, bytearray],
    fn: str,
    args: Sequence[Any],
    *,
    gas_limit: int = 500_000,
    ctx: Optional[Dict[str, Any]] = None,
    state: Optional[Dict[str, bytes]] = None,
) -> Dict[str, Any]:
    """
    Execute a single function call on an IR module.

    Parameters
    ----------
    code : base64 string or raw bytes of encoded IR
    fn   : function name (entry) to invoke
    args : list/sequence of ABI-friendly values
    gas_limit : maximum gas to spend
    ctx  : optional context overrides (block/tx timestamps/coinbase, etc.)
    state: optional dict-like storage (bytes→bytes)

    Returns
    -------
    {
      "ok": true,
      "return": <value>,
      "events": [{"name": "...", "args": {...}}, ...],
      "gas_used": 123,
    }
    """
    ir_mod = _decode_ir_any(code)
    block_env, tx_env = _build_context(ctx)
    storage = state if state is not None else {}

    ret, events, gas_used = _abi_run(
        ir_mod, fn, list(args), gas_limit, block_env, tx_env, storage
    )

    return {
        "ok": True,
        "return": ret,
        "events": events,
        "gas_used": gas_used,
    }


def simulate_tx(
    manifest_json: Union[str, bytes, bytearray],
    code: Union[str, bytes, bytearray],
    fn: str,
    args: Sequence[Any],
    *,
    gas_limit: int = 500_000,
    ctx: Optional[Dict[str, Any]] = None,
    state: Optional[Dict[str, bytes]] = None,
) -> Dict[str, Any]:
    """
    Like run_call, but returns additional manifest/code metadata for UI wiring.
    """
    manifest = load_manifest(manifest_json)
    out = run_call(code, fn, args, gas_limit=gas_limit, ctx=ctx, state=state)
    # Add lightweight identity so the UI can show what ran.
    code_b = _ensure_bytes(code)
    return {
        **out,
        "manifest": manifest,
        "entry": fn,
        "code_hash": _sha3_256_hex(code_b),
    }


# ----------------------------- Internals -----------------------------


def _safe_estimate_entry(m: irmod.Module) -> int:
    try:
        return int(estimate_entry(m))
    except Exception:
        # Be conservative when estimator fails
        # Sum base costs as a crude fallback
        fn = m.functions.get(m.entry)
        if not fn:
            return 0
        base = 0
        for ins in fn.body:
            base += 10  # tiny safe default
        return base


def _ir_stats(m: irmod.Module) -> Tuple[int, int]:
    fn_cnt = len(m.functions)
    ins_cnt = sum(len(f.body) for f in m.functions.values())
    return fn_cnt, ins_cnt


def _decode_ir_any(code: Union[str, bytes, bytearray]) -> irmod.Module:
    b = _ensure_bytes(code)
    try:
        return decode_ir(b)
    except Exception as e:
        raise ValidationError(f"failed to decode IR: {e}") from e


def _ensure_bytes(code: Union[str, bytes, bytearray]) -> bytes:
    if isinstance(code, (bytes, bytearray)):
        return bytes(code)
    if isinstance(code, str):
        # Try base64 first, else assume hex
        try:
            return base64.b64decode(code, validate=True)
        except Exception:
            try:
                return bytes.fromhex(code.removeprefix("0x"))
            except Exception:
                # As a last resort, take the utf-8 bytes (likely wrong but explicit)
                return code.encode("utf-8")
    raise TypeError("code must be base64 string or bytes")


def _sha3_256_hex(b: bytes) -> str:
    return hashlib.sha3_256(b).hexdigest()


def _build_context(ctx: Optional[Dict[str, Any]]) -> Tuple[Any, Any]:
    """
    Create BlockEnv/TxEnv for the runtime. `ctx` may override a subset:
      {
        "block": {"height": 1, "timestamp": 1700000000, "coinbase": "0x..."},
        "tx": {"sender": "anim1...", "value": 0, "gas_price": 0}
      }
    """
    # Provide deterministic but realistic defaults.
    block_defaults = dict(height=1, timestamp=1_700_000_000, coinbase=b"\x00" * 20)
    tx_defaults = dict(sender=b"\x01" * 20, value=0, gas_price=0)

    if ctx:
        block_over = ctx.get("block", {})
        tx_over = ctx.get("tx", {})
        if isinstance(block_over, dict):
            block_defaults.update(block_over)
        if isinstance(tx_over, dict):
            tx_defaults.update(tx_over)

    # The minimal context classes are provided by vm_pkg.runtime.context
    try:
        block_env = rt_ctx.BlockEnv(**block_defaults)  # type: ignore[arg-type]
    except Exception:
        # Fallback to a simple dict if dataclass signature does not match
        block_env = block_defaults

    try:
        tx_env = rt_ctx.TxEnv(**tx_defaults)  # type: ignore[arg-type]
    except Exception:
        tx_env = tx_defaults

    return block_env, tx_env


def _abi_run(
    mod: irmod.Module,
    fn: str,
    args: Sequence[Any],
    gas_limit: int,
    block_env: Any,
    tx_env: Any,
    storage: Dict[bytes, bytes],
) -> Tuple[Any, Sequence[Dict[str, Any]], int]:
    """
    Call into runtime.abi with a resilient adapter, trying a few common
    signatures to maintain compatibility with minor API drift.
    Returns (result, events, gas_used).
    """
    # Try the most explicit signature first
    # run_call(mod, fn, args, *, gas_limit, block_env, tx_env, state)
    try:
        result = rt_abi.run_call(  # type: ignore[attr-defined,call-arg]
            mod,
            fn,
            args,
            gas_limit=gas_limit,
            block_env=block_env,
            tx_env=tx_env,
            state=storage,
        )
        # Expect a dict-like result
        if isinstance(result, dict):
            return (
                result.get("return"),
                result.get("events", []),
                int(result.get("gas_used", 0)),
            )
        # Or a tuple (ret, events, gas_used)
        if isinstance(result, tuple) and len(result) == 3:
            return result  # type: ignore[return-value]
    except TypeError:
        pass

    # Try a simpler variant:
    # run_call(mod, fn, args, gas_limit, block_env, tx_env, storage)
    try:
        ret, events, gas_used = rt_abi.run_call(  # type: ignore[attr-defined]
            mod, fn, args, gas_limit, block_env, tx_env, storage
        )
        return ret, events, int(gas_used)
    except TypeError:
        pass

    # Try a barebones `call` API: call(mod, fn, args) -> ret
    if hasattr(rt_abi, "call"):
        ret = rt_abi.call(mod, fn, args)  # type: ignore[attr-defined]
        return ret, [], 0

    raise ValidationError(
        "runtime ABI does not expose a compatible run_call/call function"
    )


__all__ = ["version", "compile_bytes", "run_call", "simulate_tx"]
