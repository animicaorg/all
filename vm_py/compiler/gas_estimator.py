"""
gas_estimator.py — Static upper-bound gas estimator for the Python-VM IR.

What this provides
------------------
- `estimate_prog_gas(prog, ...)`:
    Computes a *deterministic upper bound* for gas usage by walking the
    instruction-level IR control-flow graph (CFG) with bounded loop unrolling.

- Pluggable gas table:
    Loads `vm_py/gas_table.json` if present. Falls back to conservative defaults.

- Clear, structured result:
    Returns a `GasEstimate` (dataclass) containing:
      * total_upper_bound
      * per_block_costs (sum of instruction costs in each block)
      * config (loop_unroll, table source, missing-keys fallback notes)

Assumptions & bounds
--------------------
- Branches: takes the *max* cost among successors.
- Loops: bounded by `loop_unroll` visits per block (default 8). This yields a
  finite, conservative upper bound and prevents state-space explosion.
- Instruction costs: resolved from the gas table by opcode/category; unknown
  ops fall back to category defaults.

This module only depends on the *instruction IR* (`Prog`, `Block`, `Instr`, …).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import (Any, Dict, Iterable, List, Mapping, MutableMapping,
                    Optional, Sequence, Set, Tuple)

from .ir import (Block, IAttrGet, IBinOp, ICall, ICompare, IDup, IJump,
                 IJumpIfFalse, IJumpIfTrue, ILoadConst, ILoadName, INop, Instr,
                 IPop, IReturn, IStoreName, ISubscriptGet, IUnaryOp, Prog)

# ----------------------------- Gas Table Loading ----------------------------- #

_DEFAULT_GAS_TABLE: Dict[str, int] = {
    # stack & name ops
    "load_const": 2,
    "load_name": 3,
    "store_name": 4,
    "attr_get": 5,
    "subscript_get": 6,
    "dup": 1,
    "pop": 1,
    "return": 2,
    # arithmetic / logic
    "binop_add": 5,
    "binop_sub": 5,
    "binop_mul": 8,
    "binop_div": 12,
    "binop_mod": 13,
    "binop_pow": 24,
    "binop_and": 3,
    "binop_or": 3,
    "binop_xor": 4,
    "binop_shl": 6,
    "binop_shr": 6,
    "binop_other": 8,  # fallback
    # unary
    "unary_neg": 3,
    "unary_not": 2,
    "unary_invert": 3,
    "unary_other": 3,
    # compares
    "cmp_eq": 3,
    "cmp_ne": 3,
    "cmp_lt": 4,
    "cmp_le": 4,
    "cmp_gt": 4,
    "cmp_ge": 4,
    "cmp_other": 4,
    # control flow
    "jump": 1,
    "jump_if": 2,
    "nop": 0,
    # calls
    "call_base": 12,  # setup/dispatch baseline
    "call_arg": 2,  # per positional arg marshalling
    "call_kwarg": 3,  # per keyword arg marshalling
}

# Mapping from IR operator strings → gas-table keys
_BINOP_KEY = {
    "+": "binop_add",
    "-": "binop_sub",
    "*": "binop_mul",
    "/": "binop_div",
    "%": "binop_mod",
    "**": "binop_pow",
    "&": "binop_and",
    "|": "binop_or",
    "^": "binop_xor",
    "<<": "binop_shl",
    ">>": "binop_shr",
}
_UNARY_KEY = {
    "-": "unary_neg",
    "not": "unary_not",
    "~": "unary_invert",
}
_CMP_KEY = {
    "==": "cmp_eq",
    "!=": "cmp_ne",
    "<": "cmp_lt",
    "<=": "cmp_le",
    ">": "cmp_gt",
    ">=": "cmp_ge",
}


def _load_gas_table(
    explicit_path: Optional[str] = None,
) -> Tuple[Dict[str, int], str, List[str]]:
    """
    Load gas table JSON.

    Returns:
        (table, source_desc, notes)
    """
    notes: List[str] = []
    vm_root = Path(__file__).resolve().parent.parent  # vm_py/
    candidates: List[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    candidates.append(vm_root / "gas_table.json")

    for p in candidates:
        try:
            if p.is_file():
                import json

                table = json.loads(p.read_text(encoding="utf-8"))
                # ensure int values
                norm = {str(k): int(v) for k, v in table.items()}
                # fill missing with defaults but record a note
                missing = [k for k in _DEFAULT_GAS_TABLE if k not in norm]
                if missing:
                    notes.append(
                        f"missing keys filled with defaults: {', '.join(sorted(missing))}"
                    )
                    for k in missing:
                        norm[k] = _DEFAULT_GAS_TABLE[k]
                return norm, f"json:{p}", notes
        except Exception as e:  # pragma: no cover
            notes.append(f"failed to load {p}: {e!r}")

    notes.append("using built-in DEFAULT table")
    return dict(_DEFAULT_GAS_TABLE), "builtin:default", notes


# ------------------------------- Estimation Core ------------------------------ #


@dataclass(frozen=True)
class GasEstimate:
    total_upper_bound: int
    per_block_costs: Dict[str, int]
    config: Dict[str, Any]


def _instr_cost(i: Instr, table: Mapping[str, int]) -> int:
    """Map an instruction to its gas cost using the resolved table."""
    if isinstance(i, ILoadConst):
        return table["load_const"]
    if isinstance(i, ILoadName):
        return table["load_name"]
    if isinstance(i, IStoreName):
        return table["store_name"]
    if isinstance(i, IAttrGet):
        return table["attr_get"]
    if isinstance(i, ISubscriptGet):
        return table["subscript_get"]
    if isinstance(i, IBinOp):
        key = _BINOP_KEY.get(i.op, "binop_other")
        return table[key]
    if isinstance(i, IUnaryOp):
        key = _UNARY_KEY.get(i.op, "unary_other")
        return table[key]
    if isinstance(i, ICompare):
        key = _CMP_KEY.get(i.op, "cmp_other")
        return table[key]
    if isinstance(i, ICall):
        return (
            table["call_base"]
            + i.n_pos * table["call_arg"]
            + len(i.kw_names) * table["call_kwarg"]
        )
    if isinstance(i, IPop):
        return table["pop"]
    if isinstance(i, IDup):
        return table["dup"]
    if isinstance(i, IReturn):
        return table["return"]
    if isinstance(i, IJump):
        return table["jump"]
    if isinstance(i, (IJumpIfTrue, IJumpIfFalse)):
        return table["jump_if"]
    if isinstance(i, INop):
        return table["nop"]
    # Safety: unknown instructions get a conservative bump
    return max(table.get("nop", 0), 1) + table.get("binop_other", 8)


def _block_cost(b: Block, table: Mapping[str, int]) -> int:
    return sum(_instr_cost(i, table) for i in b.instrs)


def _build_cfg(prog: Prog) -> Dict[str, Set[str]]:
    """
    Construct a successor map for each block label.

    Rules:
      - Unconditional jump → only target.
      - Conditional jump → target + fallthrough (if present).
      - Explicit fallthrough field is always considered when present.
      - Return → no successors.
    """
    succ: Dict[str, Set[str]] = {lbl: set() for lbl in prog.blocks}
    for lbl, blk in prog.blocks.items():
        # If the block ends with IReturn, it has no successors.
        ends_with_return = bool(blk.instrs and isinstance(blk.instrs[-1], IReturn))
        if ends_with_return:
            continue

        # Check for terminal jumps
        term = blk.instrs[-1] if blk.instrs else None
        if isinstance(term, IJump):
            succ[lbl].add(term.target)
        elif isinstance(term, (IJumpIfTrue, IJumpIfFalse)):
            succ[lbl].add(term.target)
            if blk.fallthrough is not None:
                succ[lbl].add(blk.fallthrough)
        else:
            # Normal fallthrough if present
            if blk.fallthrough is not None:
                succ[lbl].add(blk.fallthrough)
    return succ


def estimate_prog_gas(
    prog: Prog,
    *,
    loop_unroll: int = 8,
    gas_table_path: Optional[str] = None,
    max_states: int = 200_000,
) -> GasEstimate:
    """
    Compute a conservative upper bound on gas for an instruction-level program.

    Args:
        prog: IR program (`Prog`) with labeled blocks and an entry label.
        loop_unroll: Maximum *visits per block* during exploration. Higher is
            more conservative (larger bound) and more expensive to compute.
        gas_table_path: Optional explicit path to a JSON gas table.
        max_states: Hard cap on explored (block, visits-vector) states to
            protect against path explosion in malformed IR.

    Returns:
        GasEstimate with total_upper_bound and per-block tallies (instruction
        costs only; excludes transitive successor costs).
    """
    if loop_unroll < 1:
        raise ValueError("loop_unroll must be >= 1")

    table, source_desc, load_notes = _load_gas_table(gas_table_path)
    per_block = {lbl: _block_cost(b, table) for lbl, b in prog.blocks.items()}
    succ = _build_cfg(prog)
    labels = sorted(prog.blocks.keys())
    idx = {lbl: i for i, lbl in enumerate(labels)}

    # Visits vector is a small tuple[int] with one slot per label, bounded by loop_unroll
    zero_visits = tuple(0 for _ in labels)

    # We memoize on (label_index, visits_tuple) → worst_cost_from_here
    cache: Dict[Tuple[int, Tuple[int, ...]], int] = {}
    visited_states = 0

    def capped_inc(v: Tuple[int, ...], i: int) -> Tuple[int, ...]:
        lst = list(v)
        lst[i] = min(loop_unroll, lst[i] + 1)
        return tuple(lst)

    def worst_from(label: str, visits: Tuple[int, ...]) -> int:
        nonlocal visited_states
        key = (idx[label], visits)
        if key in cache:
            return cache[key]
        visited_states += 1
        if visited_states > max_states:
            # Degenerate safeguard: bail out by assuming the biggest single-block cost times loop_unroll
            conservative = max(per_block.values() or [0]) * loop_unroll * len(per_block)
            cache[key] = conservative
            return conservative

        li = idx[label]
        if visits[li] >= loop_unroll:
            # Stop exploring beyond the cap.
            cost = per_block[label]
            cache[key] = cost
            return cost

        # Local cost plus the worst successor path (if any).
        local = per_block[label]
        vnext = capped_inc(visits, li)
        succs = succ.get(label, set())
        if not succs:
            total = local
            cache[key] = total
            return total

        worst_succ = 0
        for s in succs:
            worst_succ = max(worst_succ, worst_from(s, vnext))
        total = local + worst_succ
        cache[key] = total
        return total

    total = worst_from(prog.entry, zero_visits)
    config = {
        "loop_unroll": loop_unroll,
        "gas_table_source": source_desc,
        "notes": load_notes,
    }
    return GasEstimate(
        total_upper_bound=total, per_block_costs=per_block, config=config
    )


# ------------------------------- Pretty Helpers ------------------------------ #


def format_estimate(est: GasEstimate) -> str:
    """Human-friendly single-line + per-block breakdown."""
    lines = [
        f"Gas upper bound: {est.total_upper_bound}",
        f"Config: loop_unroll={est.config.get('loop_unroll')} source={est.config.get('gas_table_source')}",
    ]
    notes = est.config.get("notes") or []
    if notes:
        lines.append("Notes: " + "; ".join(notes))
    if est.per_block_costs:
        width = max(len(lbl) for lbl in est.per_block_costs)
        lines.append("Per-block instruction costs:")
        for lbl, cost in sorted(est.per_block_costs.items()):
            lines.append(f"  {lbl.rjust(width)} : {cost}")
    return "\n".join(lines)


__all__ = [
    "GasEstimate",
    "estimate_prog_gas",
    "format_estimate",
]
