# -*- coding: utf-8 -*-
"""
vm_counter_runtime.py
=====================

Interpreter throughput for a tiny "Counter" contract:
- Primary metrics: calls/sec, gas/sec
- Secondary (if available): steps/sec (interpreter steps)

If the real Animica Python VM (vm_py) is available, this script will attempt to
load the examples/counter contract and issue repeated `inc()` calls, reading the
gas used per call from the VM. If the VM isn't available (or the adapter fails),
it falls back to a deterministic micro-engine that simulates work and gas.

Output: a single JSON object (one line) suitable for tests/bench/runner.py.

Examples:
    # Default: 20k calls per measured iteration, 1 warmup, 5 repeats
    python tests/bench/vm_counter_runtime.py

    # Heavier run
    python tests/bench/vm_counter_runtime.py --calls 50000 --repeat 7

    # Force fallback engine
    python tests/bench/vm_counter_runtime.py --mode fallback
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from dataclasses import dataclass
from typing import Optional, Tuple


# --------------------------------------------------------------------------- #
# Engine Adapters
# --------------------------------------------------------------------------- #

@dataclass
class CallResult:
    ok: bool
    gas_used: int
    steps: Optional[int]  # None if not available
    ret: Optional[object] = None


class EngineAdapter:
    """Minimal interface the benchmark needs."""

    name: str = "unknown"

    def reset_state(self) -> None:
        """Reset contract state to a clean slate (per measured iteration)."""
        raise NotImplementedError

    def call_inc(self) -> CallResult:
        """Perform one `inc()` call and return gas/steps if available."""
        raise NotImplementedError


# ------------------------------ Fallback engine ------------------------------ #

class FallbackCounter(EngineAdapter):
    """
    Deterministic micro-engine used when vm_py isn't available.

    It simulates a small, fixed amount of work per `inc()`:
      - gas_used per call: configurable (default 150)
      - steps per call: configurable (default 60)
    """
    name = "fallback"

    def __init__(self, gas_per_call: int = 150, steps_per_call: int = 60):
        self._gas = gas_per_call
        self._steps = steps_per_call
        self._value = 0

    def reset_state(self) -> None:
        self._value = 0

    def call_inc(self) -> CallResult:
        # A tiny deterministic loop to consume some CPU in fallback mode
        # (kept constant to make results comparable over time).
        x = self._value
        # 5 tiny operations * ~12 iterations ~= 60 "steps"
        for _ in range(12):
            x = (x + 1) ^ 0x5A
            x = (x * 3) & 0xFFFFFFFF
            x = (x ^ 0xA5) + 7
            x = (x << 1) & 0xFFFFFFFF
            x = (x >> 1)
        self._value = (self._value + 1) & 0x7FFFFFFF
        return CallResult(ok=True, gas_used=self._gas, steps=self._steps, ret=None)


# ------------------------------- vm_py adapter ------------------------------- #

class VmPyCounter(EngineAdapter):
    """
    Best-effort adapter for vm_py. It tries a few call shapes so it can work
    across minor API variations while keeping the benchmark simple.

    Strategy:
      - Use importlib.resources to load examples/counter/{contract.py,manifest.json}
      - Try vm_py.runtime.loader.* helpers to compile/link
      - Attempt to call `inc()` and read gas from a result/gasmeter field
    """
    name = "vm_py"

    def __init__(self):
        self._setup_done = False
        self._env = None
        self._engine = None
        self._abi_call = None
        self._load_counter()

    def _load_counter(self) -> None:
        try:
            import importlib
            import importlib.resources as resources

            # Try to load source + manifest from the packaged examples
            pkg = "vm_py.examples.counter"
            contract_src = resources.files(pkg).joinpath("contract.py").read_text(encoding="utf-8")  # type: ignore[attr-defined]
            manifest_json = resources.files(pkg).joinpath("manifest.json").read_text(encoding="utf-8")  # type: ignore[attr-defined]

            # Loader variants
            loader = importlib.import_module("vm_py.runtime.loader")
            engine_mod = importlib.import_module("vm_py.runtime.engine")
            abi_mod = importlib.import_module("vm_py.runtime.abi")

            # Construct an engine. Try a few helper names on loader:
            # - load(manifest_json:str, source:str) -> engine/env
            # - load_manifest(manifest_json:str, source:str)
            # - load_contract(...)
            eng = None
            env = None

            for fn_name in ("load", "load_manifest", "load_contract", "from_source"):
                fn = getattr(loader, fn_name, None)
                if callable(fn):
                    try:
                        out = fn(manifest_json, contract_src)  # type: ignore[misc]
                        # popular options: (engine, env) or engine with env attr
                        if isinstance(out, tuple) and len(out) >= 2:
                            eng, env = out[0], out[1]
                        else:
                            eng = out
                            env = getattr(out, "env", None)
                        break
                    except Exception:
                        continue

            # If loader helpers aren't present, fall back to a more manual path
            if eng is None:
                # In some designs Engine() can be created with manifest/src directly
                if hasattr(engine_mod, "Engine"):
                    Engine = getattr(engine_mod, "Engine")
                    try:
                        eng = Engine(manifest_json=manifest_json, source=contract_src)  # type: ignore[call-arg]
                    except TypeError:
                        # Give up; rely on fallback engine
                        raise RuntimeError("Engine(manifest_json, source) path not supported")
                else:
                    raise RuntimeError("vm_py.runtime.engine.Engine not found")

            # ABI call dispatcher (function to invoke methods)
            abi_call = None
            for fn_name in ("call", "dispatch", "invoke", "run_call"):
                f = getattr(abi_mod, fn_name, None)
                if callable(f):
                    abi_call = f
                    break

            # If ABI module lacks helpers, try method on engine instance
            if abi_call is None:
                for meth in ("call", "invoke", "run_call"):
                    m = getattr(eng, meth, None)
                    if callable(m):
                        # Bind engine method
                        def _bound(name: str, args: dict) -> object:
                            return m(name=name, args=args)  # type: ignore[misc]
                        abi_call = _bound
                        break

            if abi_call is None:
                raise RuntimeError("No ABI call helper available")

            self._engine = eng
            self._env = env
            self._abi_call = abi_call
            self._setup_done = True

        except Exception:
            # Any failure bubbles up to let the factory choose fallback.
            raise

    def reset_state(self) -> None:
        # Best-effort: engines often have reset/clear or reload helpers.
        eng = self._engine
        if eng is None:
            return
        for meth in ("reset_state", "reset", "clear", "reload"):
            m = getattr(eng, meth, None)
            if callable(m):
                try:
                    m()
                    return
                except Exception:
                    continue
        # As a last resort, do nothing; the counter starting value doesn't
        # materially affect throughput metrics.

    def _read_gas_from_result(self, result: object) -> int:
        # Try a few fields
        if isinstance(result, dict):
            for k in ("gasUsed", "gas_used", "gas", "GasUsed"):
                v = result.get(k)  # type: ignore[union-attr]
                if isinstance(v, int):
                    return v
        # Object attributes
        for k in ("gas_used", "gasUsed", "gas"):
            v = getattr(result, k, None)
            if isinstance(v, int):
                return v
        # Some engines attach a gasmeter
        for attr in ("gasmeter", "gas_meter", "meter"):
            gm = getattr(self._engine, attr, None)
            if gm is not None:
                for gk in ("used", "gas_used", "value"):
                    gv = getattr(gm, gk, None)
                    if isinstance(gv, int):
                        return gv
        # Unknown → 0 (we still measure calls/sec)
        return 0

    def _read_steps_from_result(self, result: object) -> Optional[int]:
        # Optional, many engines don't expose this.
        if isinstance(result, dict):
            for k in ("steps", "instr", "instructions"):
                v = result.get(k)  # type: ignore[union-attr]
                if isinstance(v, int):
                    return v
        for k in ("steps", "instr", "instructions", "insn"):
            v = getattr(result, k, None)
            if isinstance(v, int):
                return v
        return None

    def call_inc(self) -> CallResult:
        if not self._setup_done:
            raise RuntimeError("vm_py adapter not initialized")
        # Most ABIs expect a method name and args (often empty for inc())
        try:
            result = self._abi_call("inc", {})  # type: ignore[misc]
        except TypeError:
            # Some call helpers use positional args
            result = self._abi_call("inc")  # type: ignore[misc]

        gas = self._read_gas_from_result(result)
        steps = self._read_steps_from_result(result)
        return CallResult(ok=True, gas_used=int(gas), steps=steps, ret=None)


def _build_engine(mode: str) -> EngineAdapter:
    if mode in ("auto", "vm"):
        try:
            return VmPyCounter()
        except Exception:
            # fall through to fallback
            pass
    return FallbackCounter()


# --------------------------------------------------------------------------- #
# Benchmark Core
# --------------------------------------------------------------------------- #

def _time_calls(engine: EngineAdapter, calls: int) -> Tuple[float, int, Optional[int]]:
    """
    Execute `calls` times `inc()` and return (seconds, total_gas, total_steps|None)
    """
    engine.reset_state()
    total_gas = 0
    total_steps: Optional[int] = 0

    t0 = time.perf_counter()
    for _ in range(calls):
        res = engine.call_inc()
        total_gas += int(res.gas_used)
        if res.steps is None:
            total_steps = None
        elif total_steps is not None:
            total_steps += int(res.steps)
    t1 = time.perf_counter()
    return (t1 - t0), total_gas, total_steps


def run_bench(calls: int, warmup: int, repeat: int, mode: str) -> dict:
    eng = _build_engine(mode)
    label = eng.name

    # Warmup
    for _ in range(max(0, warmup)):
        _time_calls(eng, calls)

    # Measure
    timings = []
    gases = []
    steps_list = []
    for _ in range(repeat):
        dt, gas, steps = _time_calls(eng, calls)
        timings.append(dt)
        gases.append(gas)
        steps_list.append(steps)

    median_s = statistics.median(timings)
    p90_s = statistics.quantiles(timings, n=10)[8] if len(timings) >= 10 else max(timings)

    calls_per_s = (calls / median_s) if median_s > 0 else float("inf")
    gas_median = statistics.median(gases)
    gas_per_s = (gas_median / median_s) if median_s > 0 else float("inf")

    steps_per_s = None
    if all(s is not None for s in steps_list):
        steps_median = statistics.median([int(s) for s in steps_list if s is not None])
        steps_per_s = (steps_median / median_s) if median_s > 0 else float("inf")

    payload = {
        "case": f"vm.counter_runtime(calls={calls})",
        "params": {
            "calls": calls,
            "warmup": warmup,
            "repeat": repeat,
            "mode": label,
        },
        "result": {
            "calls_per_s": calls_per_s,
            "gas_per_s": gas_per_s,
            "median_s": median_s,
            "p90_s": p90_s,
        },
    }
    if steps_per_s is not None:
        payload["result"]["steps_per_s"] = steps_per_s
    return payload


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="VM Counter runtime throughput (calls/sec, gas/sec).")
    ap.add_argument("--calls", type=int, default=20_000, help="inc() calls per measured iteration (default: 20000)")
    ap.add_argument("--warmup", type=int, default=1, help="Warmup iterations (default: 1)")
    ap.add_argument("--repeat", type=int, default=5, help="Measured iterations (default: 5)")
    ap.add_argument("--mode", choices=("auto", "vm", "fallback"), default="auto",
                    help="Use vm_py if available (auto/vm), else fallback (default: auto)")
    args = ap.parse_args(argv)

    out = run_bench(calls=args.calls, warmup=args.warmup, repeat=args.repeat, mode=args.mode)
    print(json.dumps(out, separators=(",", ":"), sort_keys=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
