from __future__ import annotations

"""
Static (conservative) gas upper-bound estimator for the in-browser VM IR.

Approach
--------
- Sum per-instruction base costs from `ir.OP_SPECS`.
- For `CALL`, add the (memoized) cost of the callee function.
- For `IF(then, else?)`, add the maximum of the referenced branch functions'
  costs (when present). This is a safe upper bound.
- For `EXTCALL`, add a small symbol-specific surcharge (configurable) on top
  of the base opcode cost. Unknown symbols fall back to a generic default.

Limitations
-----------
- Input-size-dependent costs (e.g., hashing a large buffer) are modeled only by
  a base constant, since sizes are unknown statically in this minimal IR.
- Recursion is rejected (raises ValidationError). If you need to support
  structurally decreasing recursion in the future, wire an explicit bound.
"""

from dataclasses import dataclass
from typing import Dict, Optional

from ..errors import ValidationError
from . import ir


# ---------- Configuration ----------

@dataclass(frozen=True)
class ExtcallSurcharges:
    """
    Additional cost by symbol for EXTCALL (on top of OP_SPECS["EXTCALL"]["gas"]).
    The keys are simple string prefixes or exact names used by the stdlib bridge.
    """
    default: int = 10
    storage_get: int = 30
    storage_set: int = 50
    events_emit: int = 10
    hash_sha3_256: int = 15
    hash_sha3_512: int = 25
    hash_keccak256: int = 15
    treasury_transfer: int = 40

    def for_symbol(self, sym: str) -> int:
        s = sym.lower()
        # Prefix/equality matching to keep things flexible
        if s in ("storage.get", "stdlib.storage.get"):
            return self.storage_get
        if s in ("storage.set", "stdlib.storage.set"):
            return self.storage_set
        if s in ("events.emit", "stdlib.events.emit"):
            return self.events_emit
        if s in ("hash.sha3_256", "stdlib.hash.sha3_256"):
            return self.hash_sha3_256
        if s in ("hash.sha3_512", "stdlib.hash.sha3_512"):
            return self.hash_sha3_512
        if s in ("hash.keccak256", "stdlib.hash.keccak256"):
            return self.hash_keccak256
        if s in ("treasury.transfer", "stdlib.treasury.transfer"):
            return self.treasury_transfer
        return self.default


# ---------- Estimator ----------

@dataclass(frozen=True)
class GasEstimate:
    per_function: Dict[str, int]
    entry: str

    @property
    def entry_cost(self) -> int:
        return self.per_function[self.entry]


def estimate_module(m: ir.Module, *, extcall_surcharges: Optional[ExtcallSurcharges] = None) -> GasEstimate:
    """
    Compute a conservative upper-bound gas estimate for each function in `m`,
    including transitive `CALL`s and `IF` branches.
    """
    if extcall_surcharges is None:
        extcall_surcharges = ExtcallSurcharges()

    # Prepare base costs from OP_SPECS once.
    base_cost: Dict[str, int] = {op: int(spec["gas"]) for op, spec in ir.OP_SPECS.items()}

    visiting: set[str] = set()
    memo: Dict[str, int] = {}

    def cost_of(fn_name: str) -> int:
        if fn_name in memo:
            return memo[fn_name]
        if fn_name in visiting:
            # Direct or mutual recursion detected.
            raise ValidationError(f"recursive call detected at {fn_name!r}; static bound not supported")
        if fn_name not in m.functions:
            raise ValidationError(f"unknown function referenced: {fn_name!r}")

        visiting.add(fn_name)
        fn = m.functions[fn_name]
        total = 0

        for idx, ins in enumerate(fn.body):
            op = ins.op
            if op not in base_cost:
                raise ValidationError(f"{fn_name}[{idx}]: unknown opcode {op!r}")
            total += base_cost[op]

            if op == "CALL":
                callee = str(ins.args[0])
                total += cost_of(callee)

            elif op == "IF":
                then_fn = str(ins.args[0]) if len(ins.args) >= 1 else None
                else_fn = str(ins.args[1]) if len(ins.args) >= 2 and ins.args[1] is not None else None
                then_cost = cost_of(then_fn) if then_fn else 0
                else_cost = cost_of(else_fn) if else_fn else 0
                total += max(then_cost, else_cost)

            elif op == "EXTCALL":
                sym = str(ins.args[0])
                total += extcall_surcharges.for_symbol(sym)

            # Other opcodes are accounted by base cost only.

        visiting.remove(fn_name)
        memo[fn_name] = total
        return total

    per_fn = {name: cost_of(name) for name in m.functions.keys()}
    return GasEstimate(per_function=per_fn, entry=m.entry)


def estimate_entry(m: ir.Module, *, extcall_surcharges: Optional[ExtcallSurcharges] = None) -> int:
    """
    Convenience: estimate only the module entry function.
    """
    return estimate_module(m, extcall_surcharges=extcall_surcharges).entry_cost


def estimate_function(m: ir.Module, fn_name: str, *, extcall_surcharges: Optional[ExtcallSurcharges] = None) -> int:
    """
    Convenience: estimate a single named function.
    """
    est = estimate_module(m, extcall_surcharges=extcall_surcharges)
    if fn_name not in est.per_function:
        raise ValidationError(f"function not found: {fn_name!r}")
    return est.per_function[fn_name]


__all__ = [
    "GasEstimate",
    "ExtcallSurcharges",
    "estimate_module",
    "estimate_entry",
    "estimate_function",
]
