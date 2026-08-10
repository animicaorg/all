"""
vm_py.runtime.tree_engine — deterministic, gas-metered tree-walk interpreter for
the structured IR that vm_py.compiler.ast_lower produces (ir.Module/Function/
Stmt/Expr).

Why a tree interpreter and not the stack Engine
-----------------------------------------------
The stack Engine (engine.py) executes a `{blocks, entry}` machine with no locals,
no user-defined calls, no containers, and int-only 256-bit-masked arithmetic —
none of which the real standard contracts (token / dex_pair / dex_router /
dex_factory) can be expressed in, and nothing lowers the structured tree to that
machine. The compiler front-end (validate → ast_lower) already yields a faithful
structured tree for the whole contract subset; this module executes that tree
directly.

Determinism policy (consensus-critical)
---------------------------------------
- Integers are Python arbitrary-precision — NOT masked to any width. The DEX math
  (`amount_in_with_fee * reserve_out`, `reserve_in * amount_out * 10_000`,
  Newton `_sqrt`) intentionally exceeds 2**256 in intermediates; masking would
  silently corrupt prices. Bignum ops are gas-charged proportional to operand
  size so cost is itself deterministic and bounded.
- `+` is polymorphic: int+int OR bytes‖bytes, dispatched by operand type.
  `-`, `*`, `//` are int-only. No `%`, no bitwise, no true `/`.
- Comparisons work over ints and over bytes (bytes are lexicographic); mixed types
  revert deterministically.
- Reverts are exceptions: `abi.require`/`abi.revert` raise VmError, which
  propagates out of the call. Out-of-gas raises OOG. Any other host/Python error
  is normalized to VmError so execution never leaks a nondeterministic traceback.

The contract's stdlib surface (abi/storage/events/hash/treasury) is supplied by
the caller via `hosts` — the interpreter never imports host state itself, so the
same engine runs against dict-backed hosts in tests and chain-state-backed hosts
in consensus.
"""

from __future__ import annotations

import ast as _ast
import hashlib as _hashlib
from typing import Any, Dict, List, Mapping, Optional, Sequence

from ..compiler import ir as _ir

try:
    from ..errors import OOG, Revert, ValidationError, VmError
except Exception:  # pragma: no cover - bootstrap
    class VmError(Exception): ...
    class ValidationError(VmError): ...
    class OOG(VmError): ...
    class Revert(VmError): ...

from .gasmeter import GasMeter

# --------------------------------------------------------------------------- #
# Gas schedule (deterministic; bignum ops scale with operand byte-length)
# --------------------------------------------------------------------------- #

GAS_STEP = 1            # per statement / per expr node
GAS_CALL_USER = 10      # entering a user function
GAS_CALL_HOST = 20      # a stdlib/builtin host call
GAS_MUL_PER_BYTE = 1    # extra per operand byte for *, //, from_bytes, to_bytes
GAS_CONCAT_PER_BYTE = 1 # extra per output byte for bytes concat
GAS_STORAGE = 50        # storage.get/set/delete base (host adds nothing extra)

MAX_CALL_DEPTH = 256    # per-contract user-call recursion cap (belt-and-suspenders; gas bounds loops)

_BUILTIN_TYPES = {"int": int, "bytes": bytes, "bool": bool}
# Type names resolvable as VALUES (only ever as isinstance's 2nd arg / in a type
# tuple). bytearray/list/tuple never appear as constructors in the subset.
_TYPE_NAMES = {
    "int": int, "bytes": bytes, "bool": bool,
    "bytearray": bytearray, "list": list, "tuple": tuple,
}
_PSEUDO = {"__tuple__", "__list__", "__dict__", "__ternary__", "__setitem__"}


class _ReturnSignal(Exception):
    """Internal: unwinds a function body when a `return` executes."""

    __slots__ = ("value",)

    def __init__(self, value: Any) -> None:
        self.value = value


class ExecResult:
    __slots__ = ("return_value", "gas_used")

    def __init__(self, return_value: Any, gas_used: int) -> None:
        self.return_value = return_value
        self.gas_used = gas_used


class TreeInterpreter:
    """Executes one contract's structured IR against a bound host surface."""

    def __init__(
        self,
        functions: Mapping[str, "_ir.Function"],
        hosts: Mapping[str, Any],
        gas: GasMeter,
        *,
        module_globals: Optional[Mapping[str, Any]] = None,
        max_call_depth: int = MAX_CALL_DEPTH,
    ) -> None:
        self.functions: Dict[str, "_ir.Function"] = dict(functions)
        self.hosts = hosts
        self.gas = gas
        self.module_globals = dict(module_globals or {})
        self.max_call_depth = int(max_call_depth)
        self._depth = 0

    # ---------------- public entrypoint ---------------- #

    def call(self, method: str, args: Sequence[Any]) -> Any:
        fn = self.functions.get(method)
        if fn is None:
            raise VmError(f"method '{method}' not found in contract")
        return self._call_function(fn, list(args))

    # ---------------- function calls ---------------- #

    def _call_function(self, fn: "_ir.Function", args: List[Any]) -> Any:
        if len(args) != len(fn.params):
            raise VmError(
                f"{fn.name}() expects {len(fn.params)} arg(s), got {len(args)}"
            )
        if self._depth >= self.max_call_depth:
            raise VmError("maximum contract call depth exceeded")
        self.gas.consume(GAS_CALL_USER)
        frame: Dict[str, Any] = {}
        for name, value in zip(fn.params, args):
            frame[name] = value
        self._depth += 1
        try:
            self._exec_block(fn.body, frame)
            # Fell off the end with no explicit return → None.
            return None
        except _ReturnSignal as r:
            return r.value
        finally:
            self._depth -= 1

    # ---------------- statements ---------------- #

    def _exec_block(self, body: Sequence["_ir.Stmt"], frame: Dict[str, Any]) -> None:
        for st in body:
            self._exec_stmt(st, frame)

    def _exec_stmt(self, st: "_ir.Stmt", frame: Dict[str, Any]) -> None:
        self.gas.consume(GAS_STEP)
        if isinstance(st, _ir.Assign):
            value = self._eval(st.value, frame)
            self._bind_targets(st.targets, value, frame)
            return
        if isinstance(st, _ir.ExprStmt):
            self._eval(st.expr, frame)
            return
        if isinstance(st, _ir.Return):
            val = None if st.value is None else self._eval(st.value, frame)
            raise _ReturnSignal(val)
        if isinstance(st, _ir.If):
            if _truthy(self._eval(st.cond, frame)):
                self._exec_block(st.then, frame)
            else:
                self._exec_block(st.orelse, frame)
            return
        if isinstance(st, _ir.While):
            # Unbounded loops are bounded by gas: each iteration charges, so a
            # non-converging loop deterministically OOGs rather than hanging.
            while _truthy(self._eval(st.cond, frame)):
                self.gas.consume(GAS_STEP)
                self._exec_block(st.body, frame)
            return
        raise ValidationError(f"unsupported statement: {type(st).__name__}")

    def _bind_targets(self, targets: Any, value: Any, frame: Dict[str, Any]) -> None:
        # Assign.targets is either ["x", ...] (single/parallel names) or
        # [["a","b"]] (one tuple-unpack target).
        if targets and isinstance(targets[0], list):
            names = targets[0]
            seq = _as_sequence(value)
            if len(seq) != len(names):
                raise VmError(
                    f"cannot unpack {len(seq)} value(s) into {len(names)} target(s)"
                )
            for n, v in zip(names, seq):
                frame[n] = v
            return
        # One or more bare-name targets all bound to the same value.
        for n in targets:
            frame[n] = value

    # ---------------- expressions ---------------- #

    def _eval(self, e: "_ir.Expr", frame: Dict[str, Any]) -> Any:
        self.gas.consume(GAS_STEP)

        if isinstance(e, _ir.Const):
            return e.value

        if isinstance(e, _ir.Name):
            return self._resolve_name(e.name, frame)

        if isinstance(e, _ir.BinOp):
            return self._eval_binop(e, frame)

        if isinstance(e, _ir.BoolOp):
            # Short-circuit, returning the last-evaluated operand (Python
            # semantics), matching how the lowerer preserves and/or.
            if e.op == "and":
                result: Any = True
                for v in e.values:
                    result = self._eval(v, frame)
                    if not _truthy(result):
                        return result
                return result
            if e.op == "or":
                result = False
                for v in e.values:
                    result = self._eval(v, frame)
                    if _truthy(result):
                        return result
                return result
            raise ValidationError(f"unsupported BoolOp '{e.op}'")

        if isinstance(e, _ir.UnaryOp):
            if e.op == "not":
                return not _truthy(self._eval(e.operand, frame))
            if e.op == "neg":
                return -_as_int(self._eval(e.operand, frame))
            if e.op == "pos":
                return +_as_int(self._eval(e.operand, frame))
            raise ValidationError(f"unsupported unary op '{e.op}'")

        if isinstance(e, _ir.Compare):
            return self._eval_compare(e, frame)

        if isinstance(e, _ir.Subscript):
            container = self._eval(e.value, frame)
            index = self._eval(e.index, frame)
            return self._subscript(container, index)

        if isinstance(e, _ir.Call):
            return self._eval_call(e, frame)

        if isinstance(e, _ir.Attribute):
            # A bare attribute in value position (not a call target) — not used
            # by the contract subset, but resolve deterministically if it ever is.
            raise VmError(f"attribute '{e.attr}' is only valid as a call target")

        raise ValidationError(f"unsupported expression: {type(e).__name__}")

    def _resolve_name(self, name: str, frame: Dict[str, Any]) -> Any:
        if name in frame:
            return frame[name]
        if name in _TYPE_NAMES:  # a type name used as a value (isinstance / type tuple)
            return _TYPE_NAMES[name]
        if name in self.module_globals:
            return self.module_globals[name]
        # A reference to another contract function used as a value is not part of
        # the subset (calls resolve the Name directly in _eval_call).
        raise VmError(f"name '{name}' is not defined")

    def _eval_binop(self, e: "_ir.BinOp", frame: Dict[str, Any]) -> Any:
        left = self._eval(e.left, frame)
        right = self._eval(e.right, frame)
        op = e.op
        if op == "add":
            # Polymorphic: bytes concat OR int add.
            if isinstance(left, (bytes, bytearray)) and isinstance(right, (bytes, bytearray)):
                out = bytes(left) + bytes(right)
                self.gas.consume(GAS_CONCAT_PER_BYTE * len(out))
                return out
            a, b = _as_int(left), _as_int(right)
            return a + b
        a, b = _as_int(left), _as_int(right)
        if op == "sub":
            return a - b
        if op == "mul":
            self.gas.consume(GAS_MUL_PER_BYTE * (_bytelen(a) + _bytelen(b)))
            return a * b
        if op == "floordiv":
            if b == 0:
                raise VmError("division by zero")
            self.gas.consume(GAS_MUL_PER_BYTE * (_bytelen(a) + _bytelen(b)))
            return a // b
        raise ValidationError(f"unsupported binary op '{op}' (only add/sub/mul/floordiv)")

    def _eval_compare(self, e: "_ir.Compare", frame: Dict[str, Any]) -> bool:
        left = self._eval(e.left, frame)
        right = self._eval(e.right, frame)
        op = e.op
        try:
            if op == "eq":
                return _cmp_eq(left, right)
            if op == "ne":
                return not _cmp_eq(left, right)
            # Ordering: ints numerically, bytes lexicographically; same-type only.
            if not _same_orderable_type(left, right):
                raise VmError("cannot order values of different types")
            if op == "lt":
                return left < right
            if op == "le":
                return left <= right
            if op == "gt":
                return left > right
            if op == "ge":
                return left >= right
        except VmError:
            raise
        except Exception as exc:  # normalize any comparison error deterministically
            raise VmError(f"comparison failed: {exc}") from None
        raise ValidationError(f"unsupported comparison '{op}'")

    def _subscript(self, container: Any, index: Any) -> Any:
        if not isinstance(index, int) or isinstance(index, bool):
            raise VmError("subscript index must be an integer")
        if isinstance(container, (list, tuple)):
            if index < 0 or index >= len(container):
                raise VmError("subscript index out of range")
            return container[index]
        if isinstance(container, (bytes, bytearray)):
            if index < 0 or index >= len(container):
                raise VmError("subscript index out of range")
            return container[index]  # int
        raise VmError("value is not subscriptable")

    # ---------------- call dispatch ---------------- #

    def _eval_call(self, e: "_ir.Call", frame: Dict[str, Any]) -> Any:
        func = e.func

        # Name(...) — pseudo-callables, builtins, or user functions.
        if isinstance(func, _ir.Name):
            name = func.name

            # Ternary must NOT eval both arms.
            if name == "__ternary__":
                if len(e.args) != 3:
                    raise VmError("__ternary__ expects 3 args")
                cond = self._eval(e.args[0], frame)
                return self._eval(e.args[1] if _truthy(cond) else e.args[2], frame)

            pos = [self._eval(a, frame) for a in e.args]

            if name == "__tuple__":
                return tuple(pos)
            if name == "__list__":
                return list(pos)
            if name == "__dict__":
                if len(pos) % 2 != 0:
                    raise VmError("__dict__ expects flat key/value pairs")
                d: Dict[Any, Any] = {}
                for i in range(0, len(pos), 2):
                    d[pos[i]] = pos[i + 1]
                return d
            if name == "__setitem__":
                if len(pos) != 3:
                    raise VmError("__setitem__ expects (obj, key, value)")
                obj, key, val = pos
                if not isinstance(obj, dict):
                    raise VmError("__setitem__ target must be a dict")
                obj[key] = val
                return None

            if name in _BUILTIN_TYPES or name in ("len", "max", "min", "isinstance"):
                return self._call_builtin(name, pos, e.kwargs, frame)

            user = self.functions.get(name)
            if user is not None:
                if e.kwargs:
                    raise VmError(f"{name}() called with unsupported keyword args")
                return self._call_function(user, pos)

            raise VmError(f"call to unknown function '{name}'")

        # Attribute(...) — stdlib module call, or a bound method / classmethod.
        if isinstance(func, _ir.Attribute):
            return self._eval_attr_call(func, e.args, e.kwargs, frame)

        raise VmError("unsupported call target")

    def _call_builtin(self, name: str, pos: List[Any], kwargs, frame) -> Any:
        self.gas.consume(GAS_CALL_HOST)
        if kwargs:
            raise VmError(f"{name}() does not accept keyword args")
        if name == "int":
            if len(pos) != 1:
                raise VmError("int() expects 1 arg")
            v = pos[0]
            if isinstance(v, bool):
                return 1 if v else 0
            if isinstance(v, int):
                return v
            raise VmError("int() only accepts int/bool in this VM")
        if name == "bytes":
            if len(pos) != 1:
                raise VmError("bytes() expects 1 arg")
            v = pos[0]
            if isinstance(v, (bytes, bytearray)):
                return bytes(v)
            raise VmError("bytes() only accepts bytes in this VM")
        if name == "bool":
            if len(pos) != 1:
                raise VmError("bool() expects 1 arg")
            return _truthy(pos[0])
        if name == "len":
            if len(pos) != 1:
                raise VmError("len() expects 1 arg")
            v = pos[0]
            if isinstance(v, (bytes, bytearray, list, tuple, dict)):
                return len(v)
            raise VmError("len() unsupported for this type")
        if name in ("max", "min"):
            if len(pos) != 2:
                raise VmError(f"{name}() expects 2 args in this VM")
            a, b = _as_int(pos[0]), _as_int(pos[1])
            return (a if a >= b else b) if name == "max" else (a if a <= b else b)
        if name == "isinstance":
            if len(pos) != 2:
                raise VmError("isinstance() expects 2 args")
            obj, typ = pos
            types = typ if isinstance(typ, tuple) else (typ,)
            return any(_isinstance_one(obj, t) for t in types)
        raise VmError(f"unknown builtin '{name}'")

    def _eval_attr_call(self, attr: "_ir.Attribute", arg_exprs, kwargs, frame) -> Any:
        value_node = attr.value
        method = attr.attr

        # `int.from_bytes(raw, "big")` — classmethod on the int type name.
        if isinstance(value_node, _ir.Name) and value_node.name == "int":
            pos = [self._eval(a, frame) for a in arg_exprs]
            if method == "from_bytes":
                return self._int_from_bytes(pos)
            raise VmError(f"unsupported int classmethod '{method}'")

        # `abi.x(...)`, `storage.x(...)`, etc. — a stdlib module by its imported name.
        if isinstance(value_node, _ir.Name) and value_node.name in self.hosts:
            return self._stdlib_call(value_node.name, method, arg_exprs, kwargs, frame)

        # Bound method on a computed value: `x.to_bytes(n, "big")`, `x.bit_length()`.
        recv = self._eval(value_node, frame)
        pos = [self._eval(a, frame) for a in arg_exprs]
        self.gas.consume(GAS_CALL_HOST)
        if method == "to_bytes":
            return self._int_to_bytes(recv, pos)
        if method == "bit_length":
            if pos:
                raise VmError("bit_length() takes no args")
            return _as_int(recv).bit_length()
        if method == "from_bytes":
            return self._int_from_bytes([recv] + pos if False else pos)
        raise VmError(f"unsupported method '{method}'")

    def _stdlib_call(self, mod: str, method: str, arg_exprs, kwargs, frame) -> Any:
        host = self.hosts.get(mod)
        if host is None:
            raise VmError(f"stdlib module '{mod}' is not available")
        fn = getattr(host, method, None)
        if not callable(fn):
            raise VmError(f"unknown stdlib function '{mod}.{method}'")
        pos = [self._eval(a, frame) for a in arg_exprs]
        kw = {k: self._eval(v, frame) for k, v in kwargs}
        base = GAS_STORAGE if mod == "storage" else GAS_CALL_HOST
        self.gas.consume(base)
        try:
            return fn(*pos, **kw)
        except (VmError, OOG, Revert):
            raise
        except Exception as exc:  # normalize host errors deterministically
            raise VmError(f"{mod}.{method} failed: {exc}") from None

    # ---------------- int/bytes bridge (deterministic) ---------------- #

    def _int_from_bytes(self, pos: List[Any]) -> int:
        if len(pos) < 1:
            raise VmError("int.from_bytes expects (bytes, byteorder)")
        raw = pos[0]
        order = pos[1] if len(pos) >= 2 else "big"
        if not isinstance(raw, (bytes, bytearray)):
            raise VmError("int.from_bytes expects bytes")
        if order != "big":
            raise VmError("only big-endian byteorder is supported")
        self.gas.consume(GAS_MUL_PER_BYTE * (len(raw) + 1))
        return int.from_bytes(bytes(raw), "big")

    def _int_to_bytes(self, recv: Any, pos: List[Any]) -> bytes:
        if len(pos) < 1:
            raise VmError("to_bytes expects (length, byteorder)")
        length = pos[0]
        order = pos[1] if len(pos) >= 2 else "big"
        n = _as_int(recv)
        if not isinstance(length, int) or isinstance(length, bool) or length < 0:
            raise VmError("to_bytes length must be a non-negative int")
        if order != "big":
            raise VmError("only big-endian byteorder is supported")
        if n < 0:
            raise VmError("to_bytes cannot encode a negative int")
        self.gas.consume(GAS_MUL_PER_BYTE * (length + 1))
        try:
            return n.to_bytes(length, "big")
        except OverflowError:
            raise VmError("integer too big to convert to bytes") from None


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v != 0
    if isinstance(v, (bytes, bytearray, list, tuple, dict, str)):
        return len(v) != 0
    if v is None:
        return False
    return bool(v)


def _as_int(v: Any) -> int:
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, int):
        return v
    raise VmError(f"expected int, got {type(v).__name__}")


def _as_sequence(v: Any) -> Sequence[Any]:
    if isinstance(v, (list, tuple)):
        return v
    raise VmError("cannot unpack a non-sequence value")


def _bytelen(n: int) -> int:
    return (n.bit_length() + 7) // 8 + 1


def _cmp_eq(a: Any, b: Any) -> bool:
    # bool is a subtype of int in Python; keep True==1 semantics the contracts rely on.
    if isinstance(a, (bytes, bytearray)) and isinstance(b, (bytes, bytearray)):
        return bytes(a) == bytes(b)
    a_num = isinstance(a, (int, bool))
    b_num = isinstance(b, (int, bool))
    if a_num and b_num:
        return _as_int(a) == _as_int(b)
    # Distinct categories (bytes vs int, or None) are never equal.
    if a is None or b is None:
        return a is b
    return False


def _isinstance_one(obj: Any, t: Any) -> bool:
    if t is int:
        return isinstance(obj, int) and not isinstance(obj, bool)
    if t is bool:
        return isinstance(obj, bool)
    if t is bytes or t is bytearray:
        return isinstance(obj, (bytes, bytearray))
    if t is list:
        return isinstance(obj, list)
    if t is tuple:
        return isinstance(obj, tuple)
    raise VmError("isinstance() types must be int/bytes/bytearray/bool/list/tuple")


def _same_orderable_type(a: Any, b: Any) -> bool:
    if isinstance(a, (bytes, bytearray)) and isinstance(b, (bytes, bytearray)):
        return True
    if isinstance(a, (int, bool)) and isinstance(b, (int, bool)):
        return True
    return False


def run_module(
    functions: Mapping[str, "_ir.Function"],
    method: str,
    args: Sequence[Any],
    *,
    hosts: Mapping[str, Any],
    gas: GasMeter,
    module_globals: Optional[Mapping[str, Any]] = None,
) -> ExecResult:
    """Convenience wrapper: run `method(args)` and return an ExecResult."""
    vm = TreeInterpreter(functions, hosts, gas, module_globals=module_globals)
    value = vm.call(method, args)
    return ExecResult(return_value=value, gas_used=gas.used)


# --------------------------------------------------------------------------- #
# Compile + cache: contract source (raw contract.py) → executable functions.
# The lowered IR is deterministic in the source bytes, so caching by a content
# hash across calls/blocks within a process is safe.
# --------------------------------------------------------------------------- #

_COMPILE_CACHE: "Dict[bytes, Dict[str, _ir.Function]]" = {}
_COMPILE_CACHE_MAX = 512


def compile_contract(source: str) -> "Dict[str, _ir.Function]":
    """Validate (closed subset) + lower to executable functions, cached by hash.

    Raises ValidationError for anything outside the executable subset (imports,
    `**`, comprehensions, …) — the DoS / early-rejection gate layered on top of
    the interpreter's structural safety.
    """
    from .exec_validate import validate_for_execution
    from ..compiler import ast_lower as _ast_lower

    raw = source.encode("utf-8") if isinstance(source, str) else bytes(source)
    key = _hashlib.sha3_256(raw).digest()
    cached = _COMPILE_CACHE.get(key)
    if cached is not None:
        return cached

    validate_for_execution(source)
    module = _ast_lower.lower_to_ir(_ast.parse(source), filename="<contract>")
    functions = module.functions
    if not isinstance(functions, dict):
        functions = {f.name: f for f in functions}

    if len(_COMPILE_CACHE) >= _COMPILE_CACHE_MAX:
        _COMPILE_CACHE.clear()
    _COMPILE_CACHE[key] = functions
    return functions


__all__ = ["TreeInterpreter", "ExecResult", "run_module", "compile_contract"]
