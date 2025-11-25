from __future__ import annotations

"""
Minimal IR types for the in-browser VM engine.

Design goals
------------
- **Tiny & stable**: a very small surface that is easy to serialize.
- **Deterministic**: explicit operands; no implicit environment reads.
- **Portable**: pure data (no code objects) so we can (de)serialize with
  either `msgspec` or `cbor2` in `encode.py`.

Core model
----------
- `Module` — collection of named `Function`s plus an `entry` function name.
- `Function` — `(name, params, body)`; `params` is the number of stack
  parameters the function expects on entry (rightmost arg is top-of-stack).
- `Instr` — `(op, args)` where `op` is a string opcode and `args` is a small
  tuple of scalars (`int | bytes | str`).

Stack discipline
----------------
The engine is stack-based. Each opcode's stack effect is described in
`OP_SPECS`. This module only defines metadata; enforcement happens in the
validator and engine.

Conventions
-----------
- Integers are mathematical integers; runtime will clamp/validate where needed.
- Bytes are raw buffers.
- Strings are small identifiers (e.g., extern/stdlib names, event keys).

This is intentionally minimal and geared to support the simulator examples
and tests (counter, escrow) without encoding the full production VM.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Union, Optional, Mapping, Final, TypedDict

# ---------------- Scalar types ----------------

Operand = Union[int, bytes, str]


# ---------------- IR nodes ----------------

@dataclass(slots=True)
class Instr:
    """
    A single instruction.

    Attributes:
        op: Opcode name (ASCII string).
        args: Positional operands; semantics depend on `op`.
    """
    op: str
    args: Tuple[Operand, ...] = field(default_factory=tuple)


@dataclass(slots=True)
class Function:
    """
    A function consisting of a parameter count and a linear sequence of Instr.

    `params` indicates how many arguments the function expects on entry.
    """
    name: str
    params: int
    body: List[Instr] = field(default_factory=list)


@dataclass(slots=True)
class Module:
    """
    A module is a mapping of function name -> Function and the designated entry.

    The engine will look up `entry` and start execution there. Additional
    helper functions may be referenced via CALL.
    """
    functions: Dict[str, Function]
    entry: str


# ---------------- Opcode specs ----------------

class OpSpec(TypedDict):
    in_: int      # stack items consumed
    out: int      # stack items produced
    gas: int      # rough base cost for static estimator


# Minimal set used by the simulator and examples.
# NOTE: These are *metadata only*. Semantics live in the engine.
OP_SPECS: Final[Mapping[str, OpSpec]] = {
    # Stack / data movement
    "PUSH": {"in_": 0, "out": 1, "gas": 1},     # args: (value: int|bytes|str)
    "POP":  {"in_": 1, "out": 0, "gas": 1},
    "DUP":  {"in_": 1, "out": 2, "gas": 1},     # duplicate TOS
    "SWAP": {"in_": 2, "out": 2, "gas": 1},     # swap top two

    # Arithmetic (integers; deterministically bounded by engine)
    "ADD": {"in_": 2, "out": 1, "gas": 3},
    "SUB": {"in_": 2, "out": 1, "gas": 3},
    "MUL": {"in_": 2, "out": 1, "gas": 5},
    "DIV": {"in_": 2, "out": 1, "gas": 8},
    "MOD": {"in_": 2, "out": 1, "gas": 8},

    # Comparisons → bool (0/1 as bytes or int; engine defines exact encoding)
    "EQ": {"in_": 2, "out": 1, "gas": 2},
    "LT": {"in_": 2, "out": 1, "gas": 2},
    "GT": {"in_": 2, "out": 1, "gas": 2},
    "NOT": {"in_": 1, "out": 1, "gas": 1},
    "AND": {"in_": 2, "out": 1, "gas": 1},
    "OR":  {"in_": 2, "out": 1, "gas": 1},

    # Control flow (structured; no unstructured jumps in this minimal IR)
    "IF":     {"in_": 1, "out": 0, "gas": 1},   # args: (then_fn: str, else_fn: str|None)
    "CALL":   {"in_": 0, "out": 0, "gas": 10},  # args: (fn_name: str, argc: int)
    "RET":    {"in_": 1, "out": 0, "gas": 1},   # return one value
    "REVERT": {"in_": 1, "out": 0, "gas": 1},   # revert with bytes reason

    # Hashing
    "SHA3_256":  {"in_": 1, "out": 1, "gas": 15},
    "SHA3_512":  {"in_": 1, "out": 1, "gas": 25},
    "KECCAK256": {"in_": 1, "out": 1, "gas": 15},

    # Storage (via stdlib bridge in engine)
    "SLOAD":  {"in_": 1, "out": 1, "gas": 30},  # pop key -> push value
    "SSTORE": {"in_": 2, "out": 0, "gas": 50},  # pop key, value

    # Events (name, args_blob)
    "EMIT": {"in_": 2, "out": 0, "gas": 10},

    # Extern/syscall style: engine routes to stdlib (storage/events/hash/treasury/…)
    # args: (symbol: "module.func", argc: int)
    "EXTCALL": {"in_": 0, "out": 1, "gas": 20},
}


# ---------------- Utilities ----------------

ALLOWED_OPS: Final[frozenset[str]] = frozenset(OP_SPECS.keys())


def instr(op: str, *args: Operand) -> Instr:
    """
    Convenience constructor with validation of opcode name (not stack effect).
    """
    if op not in ALLOWED_OPS:
        raise ValueError(f"unknown opcode: {op}")
    return Instr(op=op, args=tuple(args))


__all__ = [
    "Operand",
    "Instr",
    "Function",
    "Module",
    "OP_SPECS",
    "ALLOWED_OPS",
    "instr",
]
