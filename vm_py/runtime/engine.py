"""
vm_py.runtime.engine — deterministic interpreter for Animica's Python-VM IR.

Design goals
------------
- Deterministic, gas-first execution (no sys I/O, no time, no randomness).
- Small, explicit instruction set; arithmetic is modulo a fixed bit-width.
- Pluggable stdlib surface (storage/events/hash/abi/treasury/syscalls).
- Defensive parsing: accepts IR objects or plain dicts produced by the compiler.

IR expectations (minimal)
-------------------------
The interpreter expects a "program" shaped like:
    {
        "blocks": {
            "entry": [ Instr, Instr, ... ],
            "L1":    [ ... ],
            ...
        },
        "entry": "entry",      # name of the entry block
        "consts": {...},       # optional constant pool
        "meta": {...},         # optional metadata
    }

Each Instr can be either:
    - an object with attributes: op: str, args: tuple/list, label: Optional[str]
    - or a dict with keys: {"op": str, "args": [...], "label": Optional[str]}

Supported ops (gas charged for each):
    - "PUSH value"            : push immediate (int/bytes/bool) onto the stack
    - "POP"                   : pop and discard
    - "DUP"                   : duplicate TOS
    - "SWAP"                  : swap TOS with second item
    - "ADD/SUB/MUL/DIV/MOD"   : arithmetic (ints), modulo NUMERIC_BIT_WIDTH
    - "AND/OR/XOR/NOT"        : bitwise on ints (mod width); NOT is bitwise ~ & mask
    - "EQ/LT/GT"              : comparisons → 0/1
    - "ISZERO"                : 1 if TOS==0 or empty bytes; else 0
    - "BYTESLEN"              : len(x) for bytes; raises if not bytes
    - "CAT"                   : concat bytes a+b
    - "SLOAD key"             : storage.get(key: bytes) → bytes
    - "SSTORE key"            : storage.set(key: bytes, value: bytes)
    - "CALL module.func n"    : pop n args (right-most first), call stdlib, push result (if not None)
    - "JUMP label"            : jump to label (in current function/block space)
    - "JUMPI label"           : conditional jump; consumes TOS as condition
    - "RETURN"                : return TOS (or None if stack empty)
    - "NOP"                   : no-op

Gas is charged *before* executing each instruction to ensure OOG is deterministic.

This interpreter is intentionally strict and small. Extend via the compiler and
the stdlib surface rather than adding ad-hoc Python features.

NOTE: Contract authors never import this directly; the VM injects a `stdlib` API.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import (Any, Callable, Dict, Iterable, List, Mapping,
                    MutableMapping, Optional, Sequence, Tuple, Union, cast)

# Errors & config
try:
    from ..errors import OOG, Revert, ValidationError, VmError  # type: ignore
except Exception:  # pragma: no cover

    class VmError(Exception): ...

    class ValidationError(VmError): ...

    class OOG(VmError): ...

    class Revert(VmError): ...


try:
    from .. import config as _cfg  # type: ignore

    NUMERIC_BIT_WIDTH: int = getattr(_cfg, "NUMERIC_BIT_WIDTH", 256)
    STEP_LIMIT: int = getattr(_cfg, "STEP_LIMIT", 1_000_000)
    GAS_TABLE: Mapping[str, int] = getattr(_cfg, "GAS_TABLE_RUNTIME", {})
except Exception:  # pragma: no cover
    NUMERIC_BIT_WIDTH = 256
    STEP_LIMIT = 1_000_000
    GAS_TABLE = {}

from . import abi as _abi  # type: ignore
from . import events_api as _events  # type: ignore
from . import hash_api as _hash  # type: ignore
from . import storage_api as _storage  # type: ignore
from . import syscalls_api as _syscalls  # type: ignore
from . import treasury_api as _treasury  # type: ignore
from .gasmeter import GasMeter  # type: ignore

# ------------------------------- utilities -------------------------------- #

MASK = (1 << NUMERIC_BIT_WIDTH) - 1


def _to_int(x: Any) -> int:
    if isinstance(x, bool):
        return 1 if x else 0
    if isinstance(x, int):
        return x & MASK
    raise ValidationError(f"Expected int, got {type(x).__name__}")


def _to_bytes(x: Any) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    raise ValidationError(f"Expected bytes, got {type(x).__name__}")


def _bool(x: Any) -> int:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return 0 if len(x) == 0 else 1
    if isinstance(x, bool):
        return 1 if x else 0
    if isinstance(x, int):
        return 0 if (x & MASK) == 0 else 1
    return 1 if x else 0


def _charge(gm: GasMeter, op: str, gas_table: Mapping[str, int]) -> None:
    cost = gas_table.get(op, 1)  # small but non-zero default
    gm.consume(cost)


def _read_op(instr: Any) -> Tuple[str, Tuple[Any, ...], Optional[str]]:
    """Extract (op, args, label) from either dict or object."""
    if isinstance(instr, dict):
        op = instr.get("op")
        if not isinstance(op, str):
            raise ValidationError("Instruction missing 'op' string")
        args = instr.get("args", ())
        if isinstance(args, list):
            args_t = tuple(args)
        elif isinstance(args, tuple):
            args_t = args
        elif args is None:
            args_t = ()
        else:
            raise ValidationError("Instruction 'args' must be list/tuple")
        label = instr.get("label")
        if label is not None and not isinstance(label, str):
            raise ValidationError("Instruction 'label' must be str or None")
        return op, args_t, label
    # object path
    try:
        op = cast(str, getattr(instr, "op"))
        args = getattr(instr, "args", ())
        label = getattr(instr, "label", None)
        if args is None:
            args = ()
        if not isinstance(op, str):
            raise TypeError
        if not isinstance(args, (list, tuple)):
            raise TypeError
        if label is not None and not isinstance(label, str):
            raise TypeError
        return op, tuple(args), label  # type: ignore
    except Exception:
        raise ValidationError(f"Bad instruction form: {instr!r}")


# ------------------------------- Engine ----------------------------------- #


@dataclass
class ExecResult:
    return_value: Optional[Any]
    gas_used: int
    steps: int
    logs: Tuple[bytes, ...]  # opaque events/logs, if any (VM-local)


class Engine:
    """Deterministic, gas-first interpreter for simple stack-based IR."""

    def __init__(
        self,
        *,
        gas_meter: Optional[GasMeter] = None,
        stdlib: Optional[Mapping[str, Any]] = None,
        gas_table: Optional[Mapping[str, int]] = None,
        step_limit: int = STEP_LIMIT,
        numeric_bits: int = NUMERIC_BIT_WIDTH,
    ) -> None:
        self.gas = gas_meter or GasMeter(limit=10_000_000)
        self.step_limit = int(step_limit)
        self.mask = (1 << int(numeric_bits)) - 1
        self.gas_table = gas_table or GAS_TABLE or {}
        # Bind stdlib surface
        self.stdlib: Dict[str, Any] = {
            "storage": _storage,
            "events": _events,
            "hash": _hash,
            "abi": _abi,
            "treasury": _treasury,
            "syscalls": _syscalls,
        }
        if stdlib:
            # Allow overrides/injection for tests
            self.stdlib.update(dict(stdlib))

    # ---------- execution entrypoints ---------- #

    def run(
        self, program: Mapping[str, Any], *, entry: Optional[str] = None
    ) -> ExecResult:
        """Run a program starting at its entry block. Returns ExecResult."""
        blocks = program.get("blocks")
        if not isinstance(blocks, Mapping):
            raise ValidationError("Program must have a 'blocks' mapping")
        entry_label = entry or program.get("entry") or "entry"
        if entry_label not in blocks:
            raise ValidationError(f"Entry block '{entry_label}' not found")

        # Build label->instructions map; each block is a list of instructions
        block_instrs: Dict[str, Sequence[Any]] = {}
        for k, v in blocks.items():
            if not isinstance(v, (list, tuple)):
                raise ValidationError(f"Block '{k}' must be a list of instructions")
            block_instrs[str(k)] = list(v)

        # Control state
        cur_label = str(entry_label)
        ip = 0
        stack: List[Any] = []
        logs: List[bytes] = []
        steps = 0

        # Fetch current block instructions
        cur_block = list(block_instrs[cur_label])

        while True:
            if steps >= self.step_limit:
                raise VmError(f"Step limit exceeded ({self.step_limit})")
            if ip >= len(cur_block):
                # Implicit return if we fall off the end of a block
                return ExecResult(
                    return_value=stack[-1] if stack else None,
                    gas_used=self.gas.used,
                    steps=steps,
                    logs=tuple(logs),
                )

            instr = cur_block[ip]
            op, args, _label = _read_op(instr)
            _charge(self.gas, op, self.gas_table)
            steps += 1

            # Execute op
            if op == "NOP":
                ip += 1
                continue

            if op == "PUSH":
                if len(args) != 1:
                    raise ValidationError("PUSH expects 1 arg (immediate)")
                stack.append(args[0])
                ip += 1
                continue

            if op == "POP":
                _require_len(stack, 1, "POP")
                stack.pop()
                ip += 1
                continue

            if op == "DUP":
                _require_len(stack, 1, "DUP")
                stack.append(stack[-1])
                ip += 1
                continue

            if op == "SWAP":
                _require_len(stack, 2, "SWAP")
                stack[-1], stack[-2] = stack[-2], stack[-1]
                ip += 1
                continue

            # Arithmetic (ints only)
            if op in ("ADD", "SUB", "MUL", "DIV", "MOD"):
                _require_len(stack, 2, op)
                b = _to_int(stack.pop())
                a = _to_int(stack.pop())
                if op == "ADD":
                    res = (a + b) & self.mask
                elif op == "SUB":
                    res = (a - b) & self.mask
                elif op == "MUL":
                    res = (a * b) & self.mask
                elif op == "DIV":
                    res = 0 if b == 0 else (a // b) & self.mask
                else:  # MOD
                    res = 0 if b == 0 else (a % b) & self.mask
                stack.append(res)
                ip += 1
                continue

            # Bitwise (ints)
            if op in ("AND", "OR", "XOR", "NOT"):
                if op == "NOT":
                    _require_len(stack, 1, "NOT")
                    a = _to_int(stack.pop())
                    stack.append((~a) & self.mask)
                else:
                    _require_len(stack, 2, op)
                    b = _to_int(stack.pop())
                    a = _to_int(stack.pop())
                    if op == "AND":
                        stack.append((a & b) & self.mask)
                    elif op == "OR":
                        stack.append((a | b) & self.mask)
                    else:
                        stack.append((a ^ b) & self.mask)
                ip += 1
                continue

            # Comparisons
            if op in ("EQ", "LT", "GT", "ISZERO"):
                if op == "ISZERO":
                    _require_len(stack, 1, "ISZERO")
                    a = stack.pop()
                    stack.append(1 if _bool(a) == 0 else 0)
                else:
                    _require_len(stack, 2, op)
                    b = _to_int(stack.pop())
                    a = _to_int(stack.pop())
                    if op == "EQ":
                        stack.append(1 if a == b else 0)
                    elif op == "LT":
                        stack.append(1 if a < b else 0)
                    else:
                        stack.append(1 if a > b else 0)
                ip += 1
                continue

            # Bytes helpers
            if op == "BYTESLEN":
                _require_len(stack, 1, "BYTESLEN")
                data = _to_bytes(stack.pop())
                stack.append(len(data))
                ip += 1
                continue

            if op == "CAT":
                _require_len(stack, 2, "CAT")
                b = _to_bytes(stack.pop())
                a = _to_bytes(stack.pop())
                stack.append(a + b)
                ip += 1
                continue

            # Storage (deterministic host)
            if op == "SLOAD":
                if len(args) != 1:
                    raise ValidationError(
                        "SLOAD expects 1 immediate arg: key source ('stack'|'imm')"
                    )
                mode = str(args[0])
                if mode == "stack":
                    _require_len(stack, 1, "SLOAD")
                    key = _to_bytes(stack.pop())
                elif mode == "imm":
                    # Next arg in instr provides the key literal (compiler emitted)
                    key = _literal_arg(instr, idx=1, desc="SLOAD key")
                    key = _to_bytes(key)
                else:
                    raise ValidationError("SLOAD mode must be 'stack' or 'imm'")
                val = _storage.get(key)
                stack.append(val)
                ip += 1
                continue

            if op == "SSTORE":
                if len(args) != 1:
                    raise ValidationError(
                        "SSTORE expects 1 immediate arg: key source ('stack'|'imm')"
                    )
                mode = str(args[0])
                _require_len(stack, 1, "SSTORE value")
                value = _to_bytes(stack.pop())
                if mode == "stack":
                    _require_len(stack, 1, "SSTORE key")
                    key = _to_bytes(stack.pop())
                elif mode == "imm":
                    key = _literal_arg(instr, idx=1, desc="SSTORE key")
                    key = _to_bytes(key)
                else:
                    raise ValidationError("SSTORE mode must be 'stack' or 'imm'")
                _storage.set(key, value)
                ip += 1
                continue

            # CALL into stdlib: args = (module.func, argc)
            if op == "CALL":
                if len(args) != 2:
                    raise ValidationError(
                        "CALL expects (target: 'module.func', argc: int)"
                    )
                target = str(args[0])
                argc = int(args[1])
                if argc < 0:
                    raise ValidationError("CALL argc must be >= 0")
                _require_len(stack, argc, "CALL")
                call_args = [stack.pop() for _ in range(argc)]
                call_args.reverse()  # stack is LIFO; args were pushed left→right
                mod_name, func_name = _split_target(target)
                module = self.stdlib.get(mod_name)
                if module is None:
                    raise ValidationError(f"Unknown stdlib module '{mod_name}'")
                fn = getattr(module, func_name, None)
                if not callable(fn):
                    raise ValidationError(
                        f"Unknown stdlib function '{mod_name}.{func_name}'"
                    )
                # Execute host call deterministically
                res = fn(*call_args)  # type: ignore
                if res is not None:
                    stack.append(res)
                ip += 1
                continue

            # Control flow
            if op == "JUMP":
                if len(args) != 1:
                    raise ValidationError("JUMP expects 1 arg: label")
                target_label = str(args[0])
                if target_label not in block_instrs:
                    raise ValidationError(f"Unknown jump label '{target_label}'")
                cur_label = target_label
                cur_block = list(block_instrs[cur_label])
                ip = 0
                continue

            if op == "JUMPI":
                if len(args) != 1:
                    raise ValidationError("JUMPI expects 1 arg: label")
                _require_len(stack, 1, "JUMPI condition")
                cond = _bool(stack.pop())
                if cond == 0:
                    ip += 1
                else:
                    target_label = str(args[0])
                    if target_label not in block_instrs:
                        raise ValidationError(f"Unknown jump label '{target_label}'")
                    cur_label = target_label
                    cur_block = list(block_instrs[cur_label])
                    ip = 0
                continue

            if op == "RETURN":
                return ExecResult(
                    return_value=stack[-1] if stack else None,
                    gas_used=self.gas.used,
                    steps=steps,
                    logs=tuple(logs),
                )

            # Unknown op
            raise ValidationError(f"Unknown opcode '{op}'")

    # ---------- helpers ---------- #


def _require_len(stack: Sequence[Any], n: int, ctx: str) -> None:
    if len(stack) < n:
        raise ValidationError(f"Stack underflow in {ctx}: need {n}, have {len(stack)}")


def _split_target(target: str) -> Tuple[str, str]:
    parts = target.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValidationError("CALL target must be 'module.func'")
    return parts[0], parts[1]


def _literal_arg(instr: Any, *, idx: int, desc: str) -> Any:
    """Fetch a literal argument by position for dict or object style instrs."""
    if isinstance(instr, dict):
        args = instr.get("args", ())
        try:
            return args[idx]
        except Exception:
            raise ValidationError(f"Missing literal {desc}")
    args = getattr(instr, "args", None)
    if not isinstance(args, (list, tuple)) or len(args) <= idx:
        raise ValidationError(f"Missing literal {desc}")
    return args[idx]


__all__ = ["Engine", "ExecResult"]
