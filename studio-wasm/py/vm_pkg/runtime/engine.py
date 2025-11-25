from __future__ import annotations

"""
Deterministic interpreter core (browser-safe)
=============================================

A tiny, stack-based interpreter for the Animica Python-VM IR (browser subset).
This runtime is designed to execute the *minimal, safe* IR emitted by the
compiler subset bundled with `vm_pkg` and used by @animica/studio-wasm. It is:

- Deterministic: no wall-clock, filesystem, or network access.
- Pure-Python: compatible with Pyodide (WASM).
- Gas-metered: every step debits gas via the provided GasMeter.
- Sandboxed: external effects limited to in-memory storage/events/hash APIs.

IR Compatibility
----------------
We support a small, pragmatic instruction set that the browser compiler emits:

Stack & locals:
  - PUSH_I {int}           : push integer
  - PUSH_B {bytes}         : push bytes
  - PUSH_BOOL {bool}       : push boolean
  - DUP                    : duplicate top of stack
  - DROP                   : pop and discard
  - LOAD_LOCAL {idx:int}   : push locals[idx]
  - STORE_LOCAL {idx:int}  : locals[idx] = pop()

Arithmetic/logic (ints/bools only):
  - BIN {op:str}           : op in {ADD,SUB,MUL,DIV,MOD,AND,OR,XOR,EQ,NE,LT,LE,GT,GE}
  - UN {op:str}            : op in {NEG,NOT}

Control flow:
  - JUMP {pc:int}
  - JUMP_IF_FALSE {pc:int} : jump if pop() is falsy
  - RETURN                 : return top-of-stack (or None if stack empty)

Interop:
  - CALL_EXTERN {name:str, argc:int}
    name is a dotted string like "storage.get", "events.emit", "hash.keccak256".
    Pops argc args (rightmost argument is on top), calls the external, and if it
    returns a non-None value, pushes it.

Function unit:
  A function is a sequence of instructions with a fixed locals size.

We accept both dataclass-style IR (with attributes) and dict-style IR
({'op': 'PUSH_I', 'value': 1}). The upstream vm_pkg.compiler.ir defines these
types; this interpreter is flexible enough to run either representation.

Gas
---
Each instruction and external call has a small fixed base cost; some externs
add a size-dependent component (e.g., hashing per byte).

Errors
------
- VmError               : generic execution failure
- ValidationError       : malformed IR or wrong operand types
- OOG                   : out of gas
- Revert                : user-triggered revert (returning bytes message)

These come from vm_pkg.errors.

"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple, Union

# Local runtime/stdlib
from .gasmeter import GasMeter
from . import storage_api, events_api, hash_api, random_api, abi
from .context import BlockEnv, TxEnv

# Errors
from ..errors import VmError, ValidationError, OOG, Revert


# ---------- Public result type ----------

@dataclass
class ExecResult:
    status: str                 # "SUCCESS" | "REVERT" | "OOG" | "ERROR"
    return_value: Any           # typically bytes/int/bool or None
    gas_used: int
    events: List[events_api.Event]  # concrete event records from events_api
    # Reserved for future (e.g., logs bloom, storage diff)


# ---------- Helpers for flexible IR intake ----------

def _get_op(ins: Any) -> str:
    # Prefer dict-style
    if isinstance(ins, dict):
        op = ins.get("op") or ins.get("opcode")
        if not isinstance(op, str):
            raise ValidationError(f"Instruction missing 'op': {ins!r}")
        return op.upper()
    # dataclass / object style: look for 'op' or class name
    op = getattr(ins, "op", None) or getattr(ins, "opcode", None)
    if isinstance(op, str):
        return op.upper()
    return ins.__class__.__name__.upper()


def _get_attr(ins: Any, name: str, default: Any = None) -> Any:
    if isinstance(ins, dict):
        return ins.get(name, default)
    return getattr(ins, name, default)


# ---------- Engine ----------

class Engine:
    """
    A small deterministic interpreter with gas metering and a pluggable extern map.
    """

    def __init__(
        self,
        *,
        gas_meter: Optional[GasMeter] = None,
        externs: Optional[Dict[str, Callable[..., Any]]] = None,
        step_limit: int = 1_000_000,
    ) -> None:
        self.gas = gas_meter or GasMeter(limit=10_000_000)  # generous default for sims
        self.externs = externs or _default_externs()
        self.step_limit = int(step_limit)

        # Base gas per op; tuned conservatively for browser sims
        self._gas_per_op: Dict[str, int] = {
            "PUSH_I": 2,
            "PUSH_B": 2,
            "PUSH_BOOL": 2,
            "DUP": 1,
            "DROP": 1,
            "LOAD_LOCAL": 2,
            "STORE_LOCAL": 3,
            "BIN": 5,
            "UN": 3,
            "JUMP": 2,
            "JUMP_IF_FALSE": 3,
            "CALL_EXTERN": 15,   # plus dynamic per-external overhead
            "RETURN": 1,
        }

    # ----- Public API -----

    def run_function(
        self,
        *,
        module: Any,
        fn_name: str,
        args: Sequence[Any],
        block_env: Optional[BlockEnv] = None,
        tx_env: Optional[TxEnv] = None,
        storage: Optional[storage_api.Storage] = None,
        event_sink: Optional[events_api.EventSink] = None,
    ) -> ExecResult:
        """
        Execute function `fn_name` in `module` with the given arguments.

        `module` is expected to have:
          - module.funcs: Dict[str, Function] where Function has fields:
              - code: Sequence[instruction]
              - n_locals: int
        """
        # Prepare runtime services (ephemeral by default)
        storage = storage or storage_api.Storage()
        event_sink = event_sink or events_api.EventSink()
        block_env = block_env or BlockEnv.dummy()
        tx_env = tx_env or TxEnv.dummy()

        # Scope for externs to access deterministic services
        rt = _RuntimeView(storage=storage, events=event_sink, block=block_env, tx=tx_env)

        try:
            fn = _resolve_function(module, fn_name)
        except Exception as e:
            raise ValidationError(f"Function '{fn_name}' not found or invalid: {e}") from e

        stack: List[Any] = []
        locals_: List[Any] = [None] * int(_get_attr(fn, "n_locals", 0))
        code: Sequence[Any] = _get_attr(fn, "code") or []
        pc = 0
        steps = 0
        ret_value: Any = None

        try:
            while pc < len(code):
                if steps >= self.step_limit:
                    raise VmError(f"step limit exceeded ({self.step_limit})")
                ins = code[pc]
                opcode = _get_op(ins)

                gas_cost = self._gas_per_op.get(opcode, 5)
                self.gas.debit(gas_cost)

                if opcode == "PUSH_I":
                    stack.append(_coerce_int(_get_attr(ins, "value")))
                    pc += 1

                elif opcode == "PUSH_B":
                    stack.append(_coerce_bytes(_get_attr(ins, "value")))
                    pc += 1

                elif opcode == "PUSH_BOOL":
                    val = _get_attr(ins, "value")
                    if not isinstance(val, bool):
                        raise ValidationError(f"PUSH_BOOL expects bool, got {type(val)}")
                    stack.append(val)
                    pc += 1

                elif opcode == "DUP":
                    if not stack:
                        raise ValidationError("DUP on empty stack")
                    stack.append(stack[-1])
                    pc += 1

                elif opcode == "DROP":
                    if not stack:
                        raise ValidationError("DROP on empty stack")
                    stack.pop()
                    pc += 1

                elif opcode == "LOAD_LOCAL":
                    idx = int(_get_attr(ins, "idx"))
                    stack.append(locals_[idx])
                    pc += 1

                elif opcode == "STORE_LOCAL":
                    idx = int(_get_attr(ins, "idx"))
                    if not stack:
                        raise ValidationError("STORE_LOCAL with empty stack")
                    locals_[idx] = stack.pop()
                    pc += 1

                elif opcode == "BIN":
                    op = str(_get_attr(ins, "op")).upper()
                    b = stack.pop()
                    a = stack.pop()
                    stack.append(_bin_op(op, a, b))
                    pc += 1

                elif opcode == "UN":
                    op = str(_get_attr(ins, "op")).upper()
                    a = stack.pop()
                    stack.append(_un_op(op, a))
                    pc += 1

                elif opcode == "JUMP":
                    target = int(_get_attr(ins, "pc"))
                    _bounds_check(target, len(code))
                    pc = target

                elif opcode == "JUMP_IF_FALSE":
                    target = int(_get_attr(ins, "pc"))
                    cond = stack.pop()
                    if not cond:
                        _bounds_check(target, len(code))
                        pc = target
                    else:
                        pc += 1

                elif opcode == "CALL_EXTERN":
                    name = str(_get_attr(ins, "name"))
                    argc = int(_get_attr(ins, "argc"))
                    if argc < 0 or argc > len(stack):
                        raise ValidationError(f"CALL_EXTERN argc invalid: {argc}")
                    # Pop args (rightmost on top)
                    call_args = [stack.pop() for _ in range(argc)][::-1]
                    # Dynamic gas component for some externs
                    self._debit_extern_dynamic_cost(name, call_args)
                    fn_ext = self.externs.get(name)
                    if fn_ext is None:
                        raise ValidationError(f"unknown extern '{name}'")
                    rv = fn_ext(rt, *call_args)
                    if rv is not None:
                        stack.append(rv)
                    pc += 1

                elif opcode == "RETURN":
                    ret_value = stack.pop() if stack else None
                    break

                else:
                    raise ValidationError(f"unknown opcode '{opcode}'")

                steps += 1

            status = "SUCCESS"
            return ExecResult(status=status, return_value=ret_value, gas_used=self.gas.used, events=event_sink.events)

        except Revert as r:
            return ExecResult(status="REVERT", return_value=bytes(r.args[0]) if r.args else b"", gas_used=self.gas.used, events=event_sink.events)
        except OOG:
            return ExecResult(status="OOG", return_value=None, gas_used=self.gas.used, events=event_sink.events)
        except ValidationError:
            # Propagate validation separately (often a user/dev error)
            raise
        except VmError:
            raise
        except Exception as e:
            raise VmError(f"unhandled error: {e!r}") from e

    # ----- Internals -----

    def _debit_extern_dynamic_cost(self, name: str, args: Sequence[Any]) -> None:
        """Charge a small dynamic gas component for certain externs."""
        # Hashing: per-byte cost
        if name.startswith("hash."):
            total_len = sum(len(x) for x in args if isinstance(x, (bytes, bytearray)))
            self.gas.debit(1 + total_len // 64)  # ~1 per 64 bytes
        # Event: small overhead per topic/data size
        elif name == "events.emit":
            payload_bytes = 0
            for a in args:
                if isinstance(a, (bytes, bytearray)):
                    payload_bytes += len(a)
                elif isinstance(a, dict):
                    payload_bytes += sum(len(v) for v in a.values() if isinstance(v, (bytes, bytearray)))
            self.gas.debit(5 + payload_bytes // 128)
        # Storage set/get: charge on key/value sizes
        elif name.startswith("storage."):
            sizes = [len(a) for a in args if isinstance(a, (bytes, bytearray))]
            self.gas.debit(3 + sum(s // 128 for s in sizes))
        # random.bytes: per-byte
        elif name == "random.bytes":
            n = int(args[0]) if args else 0
            self.gas.debit(1 + max(0, n) // 64)


# ---------- Runtime view passed to externs ----------

@dataclass
class _RuntimeView:
    storage: storage_api.Storage
    events: events_api.EventSink
    block: BlockEnv
    tx: TxEnv


# ---------- Externs mapping ----------

def _default_externs() -> Dict[str, Callable[..., Any]]:
    return {
        # ABI helpers (raises Revert)
        "abi.revert": lambda rt, data=b"": abi.revert(_coerce_bytes(data)),

        # Storage (bytes-in, bytes-out)
        "storage.get": lambda rt, key: rt.storage.get(_coerce_bytes(key)),
        "storage.set": lambda rt, key, value: rt.storage.set(_coerce_bytes(key), _coerce_bytes(value)),

        # Events
        "events.emit": lambda rt, name, args: rt.events.emit(_coerce_bytes(name), _coerce_event_args(args)),

        # Hash
        "hash.keccak256": lambda rt, data: hash_api.keccak256(_coerce_bytes(data)),
        "hash.sha3_256": lambda rt, data: hash_api.sha3_256(_coerce_bytes(data)),
        "hash.sha3_512": lambda rt, data: hash_api.sha3_512(_coerce_bytes(data)),

        # Randomness (deterministic, PRNG seeded from tx hash within the worker)
        "random.bytes": lambda rt, n: random_api.random_bytes(int(n)),

        # Treasury (no-ops/inert in browser sim; kept for compatibility)
        "treasury.balance": lambda rt: 0,
        "treasury.transfer": lambda rt, to, amount: None,
    }


def _coerce_event_args(obj: Any) -> Dict[bytes, bytes]:
    """
    Accepts a mapping-like of {name->value} where keys/values may be str/bytes/int/bool and
    returns a bytes->bytes dict for deterministic event encoding.
    """
    if obj is None:
        return {}
    if not isinstance(obj, dict):
        raise ValidationError("events.emit expects dict for args")
    out: Dict[bytes, bytes] = {}
    for k, v in obj.items():
        bk = _to_bytes_key(k)
        bv = _to_bytes_value(v)
        out[bk] = bv
    return out


def _to_bytes_key(k: Any) -> bytes:
    if isinstance(k, (bytes, bytearray)):
        return bytes(k)
    if isinstance(k, str):
        return k.encode("utf-8")
    raise ValidationError(f"event arg key must be str/bytes, got {type(k)}")


def _to_bytes_value(v: Any) -> bytes:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, str):
        return v.encode("utf-8")
    if isinstance(v, bool):
        return b"\x01" if v else b"\x00"
    if isinstance(v, int):
        # minimal big-endian encoding (0 -> b'\x00')
        if v == 0:
            return b"\x00"
        neg = v < 0
        if neg:
            raise ValidationError("negative ints not supported in event values")
        nbytes = (v.bit_length() + 7) // 8
        return v.to_bytes(nbytes, "big")
    raise ValidationError(f"unsupported event arg value type {type(v)}")


# ---------- Operators ----------

def _bin_op(op: str, a: Any, b: Any) -> Any:
    if op in {"ADD", "SUB", "MUL", "DIV", "MOD"}:
        ai, bi = _coerce_int(a), _coerce_int(b)
        if op == "ADD":
            return ai + bi
        if op == "SUB":
            return ai - bi
        if op == "MUL":
            return ai * bi
        if op == "DIV":
            if bi == 0:
                raise ValidationError("DIV by zero")
            return ai // bi
        if op == "MOD":
            if bi == 0:
                raise ValidationError("MOD by zero")
            return ai % bi

    if op in {"AND", "OR", "XOR"}:
        ai, bi = _coerce_int(a), _coerce_int(b)
        if op == "AND":
            return ai & bi
        if op == "OR":
            return ai | bi
        if op == "XOR":
            return ai ^ bi

    if op in {"EQ", "NE", "LT", "LE", "GT", "GE"}:
        # Support ints/bools/bytes with Python semantics where sensible
        if op == "EQ":
            return a == b
        if op == "NE":
            return a != b
        # Order comparisons require same-type ints
        ai, bi = _coerce_int(a), _coerce_int(b)
        if op == "LT":
            return ai < bi
        if op == "LE":
            return ai <= bi
        if op == "GT":
            return ai > bi
        if op == "GE":
            return ai >= bi

    raise ValidationError(f"unknown BIN op '{op}'")


def _un_op(op: str, a: Any) -> Any:
    if op == "NEG":
        return -_coerce_int(a)
    if op == "NOT":
        # Logical NOT for bool-like
        if isinstance(a, bool):
            return not a
        return not bool(_coerce_int(a))
    raise ValidationError(f"unknown UN op '{op}'")


# ---------- Coercions & guards ----------

def _coerce_int(x: Any) -> int:
    if isinstance(x, bool):
        return 1 if x else 0
    if isinstance(x, int):
        return x
    if isinstance(x, (bytes, bytearray)):
        if len(x) == 0:
            return 0
        return int.from_bytes(x, "big")
    raise ValidationError(f"expected int-like, got {type(x)}")


def _coerce_bytes(x: Any) -> bytes:
    if isinstance(x, (bytes, bytearray)):
        return bytes(x)
    if isinstance(x, str):
        return x.encode("utf-8")
    if isinstance(x, bool):
        return b"\x01" if x else b"\x00"
    if isinstance(x, int):
        if x == 0:
            return b"\x00"
        n = (x.bit_length() + 7) // 8
        return x.to_bytes(n, "big")
    raise ValidationError(f"expected bytes-like, got {type(x)}")


def _bounds_check(pc: int, length: int) -> None:
    if pc < 0 or pc >= length:
        raise ValidationError(f"pc out of bounds: {pc} / {length}")


# ---------- Function resolver ----------

def _resolve_function(module: Any, fn_name: str) -> Any:
    """
    module.funcs may be:
      - dict[str, Function-like], or
      - object with attribute 'funcs' like above
    """
    funcs = getattr(module, "funcs", None)
    if funcs is None and isinstance(module, dict):
        funcs = module.get("funcs")
    if not isinstance(funcs, dict):
        raise ValidationError("module.funcs must be a dict[str, Function]")
    fn = funcs.get(fn_name)
    if fn is None:
        raise ValidationError(f"function '{fn_name}' not found")
    return fn
