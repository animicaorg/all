from __future__ import annotations

"""
Tiny IR checker for the in-browser simulator.

Scope
-----
- Sanity-check module/func shapes.
- Validate instruction operands and referenced symbols.
- Do a conservative linear stack-effect analysis (no underflow).
- Check CALL/EXTCALL arity and callee parameter agreement.
- Require functions to end with RET or REVERT.

This is intentionally lightweight: it does not attempt whole-program control
flow joins for IF. We conservatively consume the condition and proceed.
"""

from dataclasses import is_dataclass
from typing import Optional, Tuple

from ..errors import ValidationError
from . import ir


def validate_module(m: ir.Module) -> None:
    if not isinstance(m, ir.Module):
        raise ValidationError("expected ir.Module")

    if not isinstance(m.entry, str) or not m.entry:
        raise ValidationError("entry must be a non-empty string")

    if not isinstance(m.functions, dict) or not m.functions:
        raise ValidationError("module must have at least one function")

    # Names must be unique, non-empty, and simple ASCII-ish.
    for name, fn in m.functions.items():
        if not isinstance(name, str) or not name:
            raise ValidationError("function name must be a non-empty string")
        if not _is_simple_ident(name):
            raise ValidationError(f"invalid function name: {name!r}")
        if not isinstance(fn, ir.Function):
            raise ValidationError(f"function {name!r} is not ir.Function")
        if fn.name != name:
            raise ValidationError(
                f"function name mismatch: key {name!r} != fn.name {fn.name!r}"
            )

    if m.entry not in m.functions:
        raise ValidationError(f"entry function {m.entry!r} not found")

    # Validate each function.
    for fn in m.functions.values():
        _validate_function(fn, m)


def _validate_function(fn: ir.Function, m: ir.Module) -> None:
    if not isinstance(fn.params, int) or fn.params < 0:
        raise ValidationError(f"{fn.name}: params must be a non-negative int")

    if not isinstance(fn.body, list):
        raise ValidationError(f"{fn.name}: body must be a list")

    if not fn.body:
        raise ValidationError(f"{fn.name}: body cannot be empty")

    stack = fn.params
    for idx, ins in enumerate(fn.body):
        if not isinstance(ins, ir.Instr):
            raise ValidationError(f"{fn.name}[{idx}]: expected ir.Instr")

        # Validate opcode
        op = ins.op
        if op not in ir.ALLOWED_OPS:
            raise ValidationError(f"{fn.name}[{idx}]: unknown opcode {op!r}")

        # Validate operand shapes & compute dynamic stack effect when needed.
        _validate_args_shape(fn.name, idx, ins, m)
        cin, cout = _stack_effect(fn.name, idx, ins, m)

        # Apply stack effect, protect against underflow.
        if stack < cin:
            raise ValidationError(
                f"{fn.name}[{idx}] {op}: stack underflow (need {cin}, have {stack})"
            )
        stack = stack - cin + cout

    # Last instruction must be terminal.
    last_op = fn.body[-1].op
    if last_op not in ("RET", "REVERT"):
        raise ValidationError(f"{fn.name}: must end with RET or REVERT")


def _is_simple_ident(s: str) -> bool:
    # allow [A-Za-z_][A-Za-z0-9_]{0,63}
    if not s:
        return False
    if not (s[0].isalpha() or s[0] == "_"):
        return False
    if len(s) > 64:
        return False
    for ch in s:
        if not (ch.isalnum() or ch == "_"):
            return False
    return True


# -------- Operand validation & stack effect helpers --------


def _validate_args_shape(fn_name: str, idx: int, ins: ir.Instr, m: ir.Module) -> None:
    op, args = ins.op, ins.args

    def err(msg: str) -> ValidationError:
        return ValidationError(f"{fn_name}[{idx}] {op}: {msg}")

    # Opcodes with zero immediates
    if op in {
        "POP",
        "DUP",
        "SWAP",
        "ADD",
        "SUB",
        "MUL",
        "DIV",
        "MOD",
        "EQ",
        "LT",
        "GT",
        "NOT",
        "AND",
        "OR",
        "RET",
        "REVERT",
        "SHA3_256",
        "SHA3_512",
        "KECCAK256",
        "SLOAD",
        "SSTORE",
        "EMIT",
    }:
        if len(args) != 0:
            raise err("takes no immediate args")

    elif op == "PUSH":
        if len(args) != 1:
            raise err("expects 1 immediate (scalar)")
        if not isinstance(args[0], (int, bytes, str)):
            raise err("PUSH value must be int|bytes|string")

    elif op == "IF":
        if not (1 <= len(args) <= 2):
            raise err("expects (then_fn: str, else_fn?: Optional[str])")
        then_fn = args[0]
        else_fn = args[1] if len(args) == 2 else None
        if not isinstance(then_fn, str):
            raise err("then_fn must be a string")
        if then_fn not in m.functions:
            raise err(f"then_fn {then_fn!r} not found")
        if else_fn is not None:
            if not isinstance(else_fn, str):
                raise err("else_fn must be a string when provided")
            if else_fn not in m.functions:
                raise err(f"else_fn {else_fn!r} not found")

    elif op == "CALL":
        if len(args) != 2:
            raise err("expects (fn_name: str, argc: int)")
        callee, argc = args
        if not isinstance(callee, str):
            raise err("callee must be a string")
        if callee not in m.functions:
            raise err(f"callee {callee!r} not found")
        if not isinstance(argc, int) or argc < 0:
            raise err("argc must be a non-negative int")
        # Callee arity agreement
        expected = m.functions[callee].params
        if argc != expected:
            raise err(f"argc {argc} does not match callee params {expected}")

    elif op == "EXTCALL":
        if len(args) != 2:
            raise err("expects (symbol: str, argc: int)")
        sym, argc = args
        if not isinstance(sym, str) or not sym:
            raise err("symbol must be a non-empty string")
        if not isinstance(argc, int) or argc < 0:
            raise err("argc must be a non-negative int")

    else:
        # Should not happen (covered by ALLOWED_OPS), but keep future-proof.
        raise err("unsupported opcode")


def _stack_effect(
    fn_name: str, idx: int, ins: ir.Instr, m: ir.Module
) -> Tuple[int, int]:
    """
    Return (consume, produce) for a single instruction.
    Uses OP_SPECS as baseline, with special handling for CALL/EXTCALL.
    """
    op, args = ins.op, ins.args

    if op == "CALL":
        # (argc popped) -> push 1 result
        argc = int(args[1])
        return argc, 1

    if op == "EXTCALL":
        argc = int(args[1])
        # External calls return a single scalar deterministically in this model.
        return argc, 1

    if op == "IF":
        # Consume the condition, no stack production in linear typing.
        return 1, 0

    # Baseline from spec
    spec = ir.OP_SPECS[op]
    return int(spec["in_"]), int(spec["out"])


# Backwards-compatible alias
typecheck = validate_module

__all__ = ["validate_module", "typecheck"]
