"""
typecheck.py — tiny type checker (ints/bytes/bool/address)

This pass validates a lowered IR (or IR-like) stream with a minimal, duck-typed
interface. It focuses on practical safety for Animica's Python-VM:

  • Scalar kinds: int, bytes, bool, address, void.
  • Op rules for arithmetic/logic/concat/load/store/call/return.
  • Function parameter/return arity/type checks.
  • Method calls validated against a SymbolTable's dispatch registry.
  • Helpful error messages with best-effort source/IR locations.

It intentionally avoids complex control-flow/data-flow inference—this is a small,
deterministic pass to catch obvious mismatches before execution. If your IR adds
new opcodes, extend OP_RULES or add handlers in TypeChecker._check_instr.

Duck-typing expectations for the IR
-----------------------------------
We accept either:
  • instr.op or instr.opcode  → string like "ADD", "LOAD", "CALL", …
  • instr.args or instr.operands → list of argument *values* or var names
  • instr.dest (optional) → name of destination variable
  • instr.loc  (optional) → location tuple/obj for nicer error messages

For CALL/MCALL we support a few shapes:
  • CALL: target in instr.target or args[0] as function name (str)
  • MCALL: owner/type in instr.owner_type and name in instr.method_name,
           or args[0] = ("ContractType", "methodName")

Symbols & methods
-----------------
If provided, a vm_py.compiler.symbols.SymbolTable lets the checker:
  • Look up function signatures by name.
  • Validate method dispatch via (owner_type, method_name).
  • Optionally discover storage var types if you encode them in your table.

You may also pass your own `extern_signatures` mapping for functions/methods
not present in the SymbolTable: { "foo": (["int","bytes"], "bool") } or
{ ("MyType","bar"): (["address"], "void") }.

Usage
-----
    checker = TypeChecker(symbols=maybe_symtab, extern_signatures={...})
    checker.check_module(module)    # raises TypeCheckError on failure

    # or a single function (with .params, .returns, .blocks or .instrs)
    checker.check_function(fn)

The checker stores inferred output types as `instr.out_type` where applicable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import (Any, Dict, Iterable, List, Mapping, MutableMapping,
                    Optional, Sequence, Tuple, Union)

try:
    # Optional import: if present, we leverage the symbol table for method/function types.
    from .symbols import SymbolTable  # type: ignore
except Exception:  # pragma: no cover - optional for very small builds
    SymbolTable = None  # type: ignore


# ------------------------------ Type model ---------------------------------- #


class BaseType(str, Enum):
    INT = "int"
    BYTES = "bytes"
    BOOL = "bool"
    ADDRESS = "address"
    VOID = "void"


Scalar = BaseType  # alias


def normalize_type(t: Optional[Union[str, BaseType]]) -> BaseType:
    if t is None:
        return BaseType.VOID
    if isinstance(t, BaseType):
        return t
    s = str(t).strip().lower()
    if s in ("int", "uint", "i", "u"):
        return BaseType.INT
    if s in ("bytes", "bytearray", "b"):
        return BaseType.BYTES
    if s in ("bool", "boolean"):
        return BaseType.BOOL
    if s in ("address", "addr"):
        return BaseType.ADDRESS
    if s in ("void", "none", "nil"):
        return BaseType.VOID
    raise ValueError(f"Unknown type: {t}")


# ------------------------------ Errors -------------------------------------- #


class TypeCheckError(Exception):
    def __init__(self, message: str, loc: Any = None):
        self.loc = loc
        if loc is not None:
            msg = f"{message} @ {loc}"
        else:
            msg = message
        super().__init__(msg)


# ------------------------------ Helpers ------------------------------------- #


def _op_name(instr: Any) -> str:
    return getattr(instr, "op", getattr(instr, "opcode", "")).upper()


def _args(instr: Any) -> List[Any]:
    xs = getattr(instr, "args", getattr(instr, "operands", None))
    if xs is None:
        return []
    return list(xs)


def _dest(instr: Any) -> Optional[str]:
    d = getattr(instr, "dest", None)
    return d if isinstance(d, str) else None


def _loc(obj: Any) -> Any:
    return getattr(obj, "loc", None)


def _is_address_like(v: Any) -> bool:
    # Strings like bech32m "anim1..." or hex 0x...; bytes length 20/32 also acceptable.
    if isinstance(v, str):
        s = v.lower()
        return s.startswith("anim1") or s.startswith("0x")
    if isinstance(v, (bytes, bytearray)):
        return len(v) in (20, 32)
    # If upstream attaches a '.kind' tag:
    k = getattr(v, "kind", None)
    return k == "address"


def _literal_type(v: Any) -> Optional[BaseType]:
    if isinstance(v, bool):
        return BaseType.BOOL
    if isinstance(v, int) and not isinstance(v, bool):
        return BaseType.INT
    if isinstance(v, (bytes, bytearray)):
        return BaseType.BYTES
    if _is_address_like(v):
        return BaseType.ADDRESS
    # Duck-typed pre-typed value
    t = getattr(v, "out_type", getattr(v, "type", None))
    if t is not None:
        try:
            return normalize_type(t)
        except Exception:
            return None
    return None


def _same(a: BaseType, b: BaseType) -> bool:
    return a == b


# ------------------------------ Signatures ---------------------------------- #

Signature = Tuple[List[BaseType], BaseType]  # (param_types, return_type)


def _sig_from_tuple(
    sig: Tuple[Sequence[Union[str, BaseType]], Union[str, BaseType]],
) -> Signature:
    params, ret = sig
    return ([normalize_type(p) for p in params], normalize_type(ret))


# --------------------------- Operation rules -------------------------------- #


# Each rule returns the output type (or raises TypeCheckError). It may also
# validate arity and input types.
def _rule_unary(expected: BaseType, out: BaseType):
    def f(instr: Any, itypes: List[BaseType]) -> BaseType:
        if len(itypes) != 1:
            raise TypeCheckError(
                f"{_op_name(instr)} expects 1 arg, got {len(itypes)}", _loc(instr)
            )
        if itypes[0] != expected:
            raise TypeCheckError(
                f"{_op_name(instr)} expects {expected}, got {itypes[0]}", _loc(instr)
            )
        return out

    return f


def _rule_binary(expected_pair: Tuple[BaseType, BaseType], out: BaseType):
    def f(instr: Any, itypes: List[BaseType]) -> BaseType:
        if len(itypes) != 2:
            raise TypeCheckError(
                f"{_op_name(instr)} expects 2 args, got {len(itypes)}", _loc(instr)
            )
        a, b = itypes
        ea, eb = expected_pair
        if a != ea or b != eb:
            raise TypeCheckError(
                f"{_op_name(instr)} expects ({ea},{eb}), got ({a},{b})", _loc(instr)
            )
        return out

    return f


def _rule_binary_same(out: BaseType, allowed: Tuple[BaseType, ...]):
    def f(instr: Any, itypes: List[BaseType]) -> BaseType:
        if len(itypes) != 2:
            raise TypeCheckError(
                f"{_op_name(instr)} expects 2 args, got {len(itypes)}", _loc(instr)
            )
        a, b = itypes
        if a != b or a not in allowed:
            raise TypeCheckError(
                f"{_op_name(instr)} expects same-type in {allowed}, got ({a},{b})",
                _loc(instr),
            )
        return out

    return f


OP_RULES: Dict[str, Any] = {
    # Constants (arity checked in _infer_arg_types)
    "CONST_INT": lambda instr, itypes: BaseType.INT,
    "CONST_BYTES": lambda instr, itypes: BaseType.BYTES,
    "CONST_BOOL": lambda instr, itypes: BaseType.BOOL,
    "CONST_ADDRESS": lambda instr, itypes: BaseType.ADDRESS,
    # Arithmetic
    "ADD": _rule_binary((BaseType.INT, BaseType.INT), BaseType.INT),
    "SUB": _rule_binary((BaseType.INT, BaseType.INT), BaseType.INT),
    "MUL": _rule_binary((BaseType.INT, BaseType.INT), BaseType.INT),
    # Logic
    "AND": _rule_binary((BaseType.BOOL, BaseType.BOOL), BaseType.BOOL),
    "OR": _rule_binary((BaseType.BOOL, BaseType.BOOL), BaseType.BOOL),
    "NOT": _rule_unary(BaseType.BOOL, BaseType.BOOL),
    # Bytes
    "CONCAT": _rule_binary((BaseType.BYTES, BaseType.BYTES), BaseType.BYTES),
    # Comparisons
    "EQ": _rule_binary_same(
        BaseType.BOOL, (BaseType.INT, BaseType.BOOL, BaseType.BYTES, BaseType.ADDRESS)
    ),
    "NEQ": _rule_binary_same(
        BaseType.BOOL, (BaseType.INT, BaseType.BOOL, BaseType.BYTES, BaseType.ADDRESS)
    ),
}


# ------------------------------ TypeChecker --------------------------------- #


@dataclass
class _FnLike:
    name: str
    params: List[Tuple[str, BaseType]]  # (name, type)
    returns: BaseType


class TypeChecker:
    def __init__(
        self,
        *,
        symbols: Optional["SymbolTable"] = None,
        extern_signatures: Optional[
            Mapping[
                Union[str, Tuple[str, str]],
                Tuple[Sequence[Union[str, BaseType]], Union[str, BaseType]],
            ]
        ] = None,
    ) -> None:
        """
        :param symbols: Optional SymbolTable for function/method signatures.
        :param extern_signatures: Mapping for functions/methods missing in symbols.
               Keys: "fn_name" or ("OwnerType","method") → ( [param_types], return_type )
        """
        self.symbols = symbols
        self.extern_sigs: Dict[Union[str, Tuple[str, str]], Signature] = {}
        if extern_signatures:
            for k, sig in extern_signatures.items():
                self.extern_sigs[k] = _sig_from_tuple(sig)

    # ---- Public API -------------------------------------------------------- #

    def check_module(self, module: Any) -> None:
        """Duck-typed: module.functions or .funcs is an iterable of fn-like objects."""
        fns = getattr(module, "functions", getattr(module, "funcs", None))
        if fns is None:
            # Accept a single-function "module" that just has .blocks/.instrs
            if hasattr(module, "blocks") or hasattr(module, "instrs"):
                self.check_function(module)
                return
            raise TypeCheckError("Module has no functions")
        for fn in fns:
            self.check_function(fn)

    def check_function(self, fn: Any) -> None:
        """Check a function-like object with .name, .params, .returns, and .blocks/.instrs."""
        flike = self._fn_like(fn)
        env: Dict[str, BaseType] = {pname: ptype for (pname, ptype) in flike.params}
        # Allow predeclared locals via fn.locals: Dict[name,type]
        for lname, ltype in getattr(
            fn, "locals", getattr(fn, "locals_types", {})
        ).items():
            env[str(lname)] = normalize_type(ltype)

        blocks = getattr(fn, "blocks", None)
        instrs: Iterable[Any]
        if blocks is not None:
            # Flatten blocks in order
            instrs = (
                instr
                for b in blocks
                for instr in getattr(b, "instrs", getattr(b, "instructions", []))
            )
        else:
            instrs = getattr(fn, "instrs", getattr(fn, "instructions", []))

        saw_return = False
        for instr in instrs:
            self._check_instr(instr, env, fn_context=flike)
            if _op_name(instr) == "RETURN":
                saw_return = True

        if flike.returns != BaseType.VOID and not saw_return:
            raise TypeCheckError(
                f"Function '{flike.name}' missing a return of {flike.returns}"
            )

    # ---- Internals --------------------------------------------------------- #

    def _fn_like(self, fn: Any) -> _FnLike:
        name = getattr(fn, "name", None) or "<fn>"
        # params: either list of (name, type) or a structure with .name/.type
        raw_params = getattr(fn, "params", getattr(fn, "parameters", []))
        params: List[Tuple[str, BaseType]] = []
        for p in raw_params:
            if isinstance(p, (tuple, list)) and len(p) >= 2:
                pname, ptype = p[0], p[1]
            else:
                pname = getattr(p, "name", None) or str(p)
                ptype = getattr(p, "type", BaseType.INT)
            params.append((str(pname), normalize_type(ptype)))
        returns = normalize_type(
            getattr(fn, "returns", getattr(fn, "ret", BaseType.VOID))
        )
        return _FnLike(name=name, params=params, returns=returns)

    def _infer_arg_types(
        self, args: Sequence[Any], env: Mapping[str, BaseType], instr: Any
    ) -> List[BaseType]:
        types: List[BaseType] = []
        for a in args:
            # Variable reference
            if isinstance(a, str) and a in env:
                types.append(env[a])
                continue
            # Literal or previously-typed temp
            lt = _literal_type(a)
            if lt is not None:
                types.append(lt)
                continue
            # Unknown reference: if it's a tuple that encodes ("Owner","method") for MCALL target,
            # let _check_instr handle it. For regular args it's an error.
            if (
                isinstance(a, tuple)
                and len(a) == 2
                and all(isinstance(x, str) for x in a)
            ):
                # caller probably passed ("Owner","method") as the first arg for MCALL
                types.append(BaseType.VOID)  # placeholder; handled in MCALL logic
                continue
            raise TypeCheckError(
                f"Unbound or untyped value in argument list: {a!r}", _loc(instr)
            )
        return types

    def _check_instr(
        self, instr: Any, env: MutableMapping[str, BaseType], *, fn_context: _FnLike
    ) -> None:
        op = _op_name(instr)
        loc = _loc(instr)
        args = _args(instr)
        dest = _dest(instr)

        # Declarations: DECLARE <name> <type>
        if op == "DECLARE":
            if len(args) != 2 or not isinstance(args[0], str):
                raise TypeCheckError("DECLARE expects (name: str, type: str)", loc)
            name, t = args[0], normalize_type(args[1])
            if name in env:
                raise TypeCheckError(f"Variable already declared: {name}", loc)
            env[name] = t
            setattr(instr, "out_type", BaseType.VOID)
            return

        # Load/Store (typed variables)
        if op == "LOAD":
            if len(args) != 1 or not isinstance(args[0], str):
                raise TypeCheckError("LOAD expects (name: str)", loc)
            name = args[0]
            if name not in env:
                raise TypeCheckError(f"LOAD of undeclared variable '{name}'", loc)
            t = env[name]
            setattr(instr, "out_type", t)
            if dest:
                env[dest] = t
            return

        if op == "STORE":
            if len(args) != 2 or not isinstance(args[0], str):
                raise TypeCheckError("STORE expects (name: str, value)", loc)
            name, value = args[0], args[1]
            if name not in env:
                raise TypeCheckError(f"STORE to undeclared variable '{name}'", loc)
            vt = env[name]
            at = self._infer_arg_types([value], env, instr)[0]
            if vt != at:
                raise TypeCheckError(
                    f"STORE type mismatch for '{name}': {vt} <- {at}", loc
                )
            setattr(instr, "out_type", BaseType.VOID)
            return

        # Control: RETURN (optional value)
        if op == "RETURN":
            if fn_context.returns == BaseType.VOID:
                if len(args) != 0:
                    raise TypeCheckError("RETURN of value in void function", loc)
                setattr(instr, "out_type", BaseType.VOID)
                return
            if len(args) != 1:
                raise TypeCheckError("RETURN expects single value", loc)
            rt = self._infer_arg_types(args, env, instr)[0]
            if rt != fn_context.returns:
                raise TypeCheckError(
                    f"RETURN type mismatch: expected {fn_context.returns}, got {rt}",
                    loc,
                )
            setattr(instr, "out_type", rt)
            return

        # Calls: CALL (free function) or MCALL (method on owner_type)
        if op in ("CALL", "MCALL"):
            sig: Optional[Signature] = None
            call_args = list(args)

            if op == "CALL":
                # Target as instr.target or first arg (string)
                target = getattr(instr, "target", None) or (
                    call_args.pop(0)
                    if call_args and isinstance(call_args[0], str)
                    else None
                )
                if not isinstance(target, str):
                    raise TypeCheckError(
                        "CALL requires target function name (str) as .target or args[0]",
                        loc,
                    )
                sig = self._resolve_fn_sig(target)
                if sig is None:
                    raise TypeCheckError(f"Unknown function: {target}", loc)
            else:  # MCALL
                owner = getattr(instr, "owner_type", None)
                mname = getattr(instr, "method_name", None)
                if owner is None or mname is None:
                    # Allow ("Owner","method") as first arg
                    if (
                        call_args
                        and isinstance(call_args[0], tuple)
                        and len(call_args[0]) == 2
                    ):
                        owner, mname = call_args.pop(0)
                if not (isinstance(owner, str) and isinstance(mname, str)):
                    raise TypeCheckError(
                        "MCALL requires owner_type/method_name or args[0]=('Owner','method')",
                        loc,
                    )
                sig = self._resolve_method_sig(owner, mname)
                if sig is None:
                    raise TypeCheckError(f"Unknown method: {owner}.{mname}", loc)

            param_types, ret_type = sig
            arg_types = self._infer_arg_types(call_args, env, instr)
            if len(arg_types) != len(param_types):
                raise TypeCheckError(
                    f"Call arity mismatch: expected {len(param_types)} arg(s), got {len(arg_types)}",
                    loc,
                )
            for i, (got, exp) in enumerate(zip(arg_types, param_types)):
                if got != exp:
                    raise TypeCheckError(
                        f"Call arg {i} type mismatch: expected {exp}, got {got}", loc
                    )
            setattr(instr, "out_type", ret_type)
            if dest and ret_type != BaseType.VOID:
                env[dest] = ret_type
            return

        # Generic ops covered by OP_RULES
        if op in OP_RULES:
            rule = OP_RULES[op]
            in_types = self._infer_arg_types(args, env, instr)
            out_t = rule(instr, in_types)
            setattr(instr, "out_type", out_t)
            if dest and out_t != BaseType.VOID:
                env[dest] = out_t
            return

        # If we reach here, it's an unknown op; accept as no-op but warn with strictness if desired.
        # For strict mode you may raise:
        # raise TypeCheckError(f"Unknown opcode: {op}", loc)
        setattr(instr, "out_type", BaseType.VOID)

    # ---- Signature resolution --------------------------------------------- #

    def _resolve_fn_sig(self, name: str) -> Optional[Signature]:
        # 1) external override
        if name in self.extern_sigs:
            return self.extern_sigs[name]
        # 2) symbol table (if available): look for a function symbol
        if self.symbols is not None:
            sym = self.symbols.resolve(name)  # type: ignore[attr-defined]
            if sym and getattr(sym, "kind", None).__class__.__name__ in ("SymbolKind",):
                # Prefer FunctionSymbol/MethodSymbol shape if present
                kname = getattr(sym.kind, "name", "")
                if kname in ("FUNC", "METHOD"):
                    params = list(getattr(sym, "params", ()))
                    returns = getattr(sym, "returns", BaseType.VOID)
                    return (
                        [normalize_type(p) for p in params],
                        normalize_type(returns),
                    )
        return None

    def _resolve_method_sig(
        self, owner_type: str, method_name: str
    ) -> Optional[Signature]:
        key = (owner_type, method_name)
        if key in self.extern_sigs:
            return self.extern_sigs[key]
        if self.symbols is not None:
            ms = self.symbols.resolve_method(owner_type, method_name)  # type: ignore[attr-defined]
            if ms:
                params = list(getattr(ms, "params", ()))
                returns = getattr(ms, "returns", BaseType.VOID)
                return ([normalize_type(p) for p in params], normalize_type(returns))
        return None


# ------------------------------ Convenience API ------------------------------ #


def typecheck(
    module_or_function: Any,
    *,
    symbols: Optional["SymbolTable"] = None,
    extern_signatures: Optional[
        Mapping[
            Union[str, Tuple[str, str]],
            Tuple[Sequence[Union[str, BaseType]], Union[str, BaseType]],
        ]
    ] = None,
) -> None:
    """
    One-shot helper. Raises TypeCheckError on the first mismatch.
    """
    TypeChecker(symbols=symbols, extern_signatures=extern_signatures).check_module(
        module_or_function
    )


__all__ = [
    "BaseType",
    "Scalar",
    "normalize_type",
    "Signature",
    "TypeCheckError",
    "TypeChecker",
    "typecheck",
]
