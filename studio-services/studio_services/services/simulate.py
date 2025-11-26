"""
Simulate service: compile + run a single contract call using vm_py locally (no state writes).

This service accepts a SimulateCall request (source+manifest or code+manifest),
compiles when needed, and executes the selected method in a deterministic in-memory
environment. It never mutates on-chain state and does not require a node.

Adapters used (duck-typed, with graceful fallbacks):
- studio_services.adapters.vm_compile: compile/run helpers against vm_py
- studio_services.adapters.vm_hash: optional code hash
- studio_services.config: chain/env defaults

Models:
- studio_services.models.simulate: SimulateCall, SimulateResult
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

from studio_services.errors import BadRequest, ApiError
from studio_services import config as cfg_mod
from studio_services.models.simulate import SimulateCall, SimulateResult

# Adapters (resolved at runtime; we only rely on call-shape)
from studio_services.adapters import vm_compile as vm_adapter  # type: ignore
from studio_services.adapters import vm_hash as vm_hash_adapter  # type: ignore

log = logging.getLogger(__name__)


# ------------------------ helpers ------------------------

def _cfg(name: str, default: Optional[str] = None) -> Optional[str]:
    getter = getattr(cfg_mod, "get", None)
    if callable(getter):
        try:
            v = getter(name)
            if v is not None:
                return str(v)
        except Exception:
            pass
    return os.getenv(name, default)


def _default_env(chain_id: Optional[int] = None) -> Dict[str, Any]:
    """
    Build a minimal local execution environment. execution/vm_py will accept a
    small set of fields; we keep it permissive and let the adapter trim/augment.
    """
    from time import time
    return {
        "chainId": chain_id or int(_cfg("CHAIN_ID", "1") or "1"),
        "block": {
            "timestamp": int(time()),
            "height": 0,
            "coinbase": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqey8k2v",  # null-ish coinbase for sim
        },
        "tx": {
            "gasPrice": 0,
            "caller": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqey8k2v",
            "value": 0,
            "nonce": 0,
        },
    }


def _compute_code_hash(code: Optional[bytes]) -> Optional[str]:
    if not code:
        return None
    for fn_name in ("compute_code_hash", "code_hash", "content_digest"):
        fn = getattr(vm_hash_adapter, fn_name, None)
        if callable(fn):
            try:
                return str(fn(code))
            except Exception:
                continue
    # Cheap fallback (SHA3-256 from hashlib might be used inside adapter too)
    try:
        import hashlib
        return hashlib.sha3_256(code).hexdigest()
    except Exception:  # pragma: no cover
        return None


def _normalize_run_result(res: Any) -> Tuple[Any, int, list]:
    """
    Accepts several result shapes and normalizes to (return_value, gas_used, logs[]).
    Common shapes:
      - {"return": <any>, "gasUsed": int, "logs": [..]}
      - (return, gas_used, logs)
      - {"ok": True, "result": <any>, "gas": int, "events": [..]}
    """
    if isinstance(res, dict):
        if "return" in res or "gasUsed" in res or "logs" in res:
            return res.get("return"), int(res.get("gasUsed") or 0), list(res.get("logs") or [])
        if "result" in res or "gas" in res or "events" in res:
            return res.get("result"), int(res.get("gas") or 0), list(res.get("events") or [])
        # last resort: look for first int and list in values
        rv = res.get("result") or res.get("return")
        gas = res.get("gasUsed") or res.get("gas") or 0
        logs = res.get("logs") or res.get("events") or []
        return rv, int(gas), list(logs)
    if isinstance(res, tuple) and len(res) >= 3:
        return res[0], int(res[1] or 0), list(res[2] or [])
    # Unknown; best effort
    return res, 0, []


# ------------------------ service ------------------------

class SimulateService:
    """
    One-shot compile & run for a single method call.
    """

    def __init__(self) -> None:
        self.vm = vm_adapter

    def simulate(self, req: SimulateCall) -> SimulateResult:
        """
        Accepts a SimulateCall and returns a SimulateResult.

        Rules:
        - Either (source + manifest) or (code + manifest) must be provided.
        - method must be present; args may be [], and will be validated by adapter.
        - gas_limit is optional; adapter/runtime enforces safety caps.
        - No state writes; environment uses in-memory storage.
        """
        # Validate basics
        if not req.method or not isinstance(req.method, str):
            raise BadRequest("method is required")
        if not req.manifest:
            raise BadRequest("manifest is required")

        has_source = bool(req.source)
        has_code = isinstance(getattr(req, "code", None), (bytes, bytearray))

        if not (has_source or has_code):
            raise BadRequest("provide either source or code along with manifest")

        # Build environment
        env = dict(req.env or {})
        if not env:
            env = _default_env(chain_id=req.chainId)

        # Compile if needed
        code: Optional[bytes] = bytes(req.code) if has_code else None
        diagnostics: Optional[Dict[str, Any]] = None
        ir_bytes: Optional[bytes] = None

        if has_source:
            compile_ok = False
            # Try flexible adapter entrypoints in order of preference.
            for fn_name in ("compile_source", "compile", "compile_and_link"):
                fn = getattr(self.vm, fn_name, None)
                if callable(fn):
                    try:
                        comp = fn(req.source, req.manifest)  # type: ignore[arg-type]
                        # Possible shapes: bytes, {"code": bytes, "ir": bytes, "diagnostics": {...}}
                        if isinstance(comp, (bytes, bytearray)):
                            code = bytes(comp)
                        elif isinstance(comp, dict):
                            if "code" in comp and isinstance(comp["code"], (bytes, bytearray)):
                                code = bytes(comp["code"])
                            irv = comp.get("ir")
                            if isinstance(irv, (bytes, bytearray)):
                                ir_bytes = bytes(irv)
                            diag = comp.get("diagnostics")
                            if isinstance(diag, dict):
                                diagnostics = diag
                        compile_ok = bool(code)
                        break
                    except Exception as e:
                        log.exception("compile failed: %s", e)
                        raise BadRequest(f"compile failed: {e}")
            if not compile_ok:
                raise BadRequest("no compile function available in vm adapter")

        if not code:
            raise BadRequest("no executable code produced or provided")

        # Optional gas estimate (best effort)
        gas_estimate: Optional[int] = None
        try:
            est = getattr(self.vm, "estimate_gas", None) or getattr(self.vm, "gas_estimate", None)
            if callable(est):
                ge = est(code, req.manifest, req.method, req.args or [], env=env)  # type: ignore[arg-type]
                if isinstance(ge, dict) and "gas" in ge:
                    gas_estimate = int(ge["gas"])
                elif isinstance(ge, int):
                    gas_estimate = ge
        except Exception:
            pass

        # Execute
        try:
            runner = (
                getattr(self.vm, "run_call", None)
                or getattr(self.vm, "simulate_call", None)
                or getattr(self.vm, "run", None)
            )
            if not callable(runner):
                raise ApiError("vm adapter has no runnable entrypoint (run_call/simulate_call/run)")

            run_res = runner(
                code,
                req.manifest,
                req.method,
                req.args or [],
                env=env,
                gas_limit=req.gasLimit,
                readonly=True,  # enforce no state writes if adapter supports it
            )
        except Exception as e:
            # Allow adapters to return structured errors; otherwise, map to BadRequest
            raise BadRequest(f"execution failed: {e}")

        ret, gas_used, logs = _normalize_run_result(run_res)
        code_hash = _compute_code_hash(code)

        return SimulateResult(
            ok=True,
            return_value=ret,
            gas_used=gas_used,
            gas_estimate=gas_estimate,
            logs=logs,
            code_hash=code_hash,
            ir=ir_bytes,
            diagnostics=diagnostics,
        )


_SERVICE = SimulateService()


def simulate_call(req: SimulateCall) -> SimulateResult:
    return _SERVICE.simulate(req)


# Backward-compatible aliases for router resolution
simulate = simulate_call
run_simulation = simulate_call
exec_simulation = simulate_call


__all__ = [
    "SimulateService",
    "simulate_call",
    "simulate",
    "run_simulation",
    "exec_simulation",
]
