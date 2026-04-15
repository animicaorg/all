"""
Compatibility adapter for calling AST/source lowering helpers safely.

Why this exists:
- historical vm_py call-sites passed different argument shapes to lowerers
- lowerer signatures also evolved (`ast` only, `ast + name`, kw `filename`, etc.)
- naive retry loops caused misleading TypeErrors ("unexpected keyword 'name'")

This adapter uses function signature introspection to build one deterministic
call shape per helper instead of probing with mismatched argument lists.
"""

from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from typing import Any, Iterable


class LoweringAdapterError(RuntimeError):
    """Raised when no compatible lowering helper can be invoked."""


_FILENAME_PARAM_NAMES = {
    "filename",
    "name",
    "name_hint",
    "path",
    "file",
    "module_name",
    "contract_name",
}

_PRIMARY_SOURCE_NAMES = {"source", "src", "text", "code", "program", "input"}
_PRIMARY_AST_NAMES = {"ast", "tree", "module", "node"}


@dataclass(frozen=True)
class LoweringCallMeta:
    helper_name: str
    input_mode: str  # "ast" | "source"
    signature: str
    call_repr: str


def _looks_like_filename_param(name: str) -> bool:
    lowered = name.strip().lower()
    if lowered in _FILENAME_PARAM_NAMES:
        return True
    return any(token in lowered for token in ("file", "path", "name"))


def _safe_signature(fn: Any) -> inspect.Signature:
    try:
        return inspect.signature(fn)
    except Exception as exc:  # noqa: BLE001
        raise LoweringAdapterError(f"signature inspection failed: {exc}") from exc


def _pick_primary_arg(param: inspect.Parameter, *, source: str, tree: ast.AST, preferred: str) -> Any:
    name = param.name.lower()
    ann = param.annotation
    if name in _PRIMARY_SOURCE_NAMES:
        return source
    if name in _PRIMARY_AST_NAMES:
        return tree
    if ann is not inspect._empty:
        ann_name = getattr(ann, "__name__", str(ann)).lower()
        if "ast" in ann_name or "module" in ann_name:
            return tree
        if "str" in ann_name or "source" in ann_name or "text" in ann_name:
            return source
    return tree if preferred == "ast" else source


def _build_call(
    fn: Any,
    *,
    source: str,
    tree: ast.AST,
    filename: str,
    preferred_input: str,
) -> tuple[list[Any], dict[str, Any], str]:
    sig = _safe_signature(fn)
    params = list(sig.parameters.values())
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    consumed: set[int] = set()

    positional_slots = [
        (idx, p)
        for idx, p in enumerate(params)
        if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    ]
    has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)

    # Primary contract input (AST or source text).
    if positional_slots:
        idx0, p0 = positional_slots[0]
        args.append(_pick_primary_arg(p0, source=source, tree=tree, preferred=preferred_input))
        consumed.add(idx0)
    elif has_varargs:
        args.append(tree if preferred_input == "ast" else source)
    else:
        assigned_primary = False
        for idx, p in enumerate(params):
            if p.kind not in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
                continue
            if p.name.lower() in _PRIMARY_AST_NAMES | _PRIMARY_SOURCE_NAMES:
                kwargs[p.name] = _pick_primary_arg(
                    p,
                    source=source,
                    tree=tree,
                    preferred=preferred_input,
                )
                consumed.add(idx)
                assigned_primary = True
                break
        if not assigned_primary:
            raise LoweringAdapterError("no parameter accepts AST/source input")

    # Preferred path: explicit filename-like parameter.
    assigned_filename = False
    for idx, p in enumerate(params):
        if idx in consumed:
            continue
        if not _looks_like_filename_param(p.name):
            continue
        if p.kind == inspect.Parameter.POSITIONAL_ONLY:
            args.append(filename)
            consumed.add(idx)
            assigned_filename = True
            break
        if p.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
            kwargs[p.name] = filename
            consumed.add(idx)
            assigned_filename = True
            break

    # Fill required parameters if they are clearly filename-like.
    missing_required: list[str] = []
    for idx, p in enumerate(params):
        if idx in consumed:
            continue
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if p.default is not inspect._empty:
            continue
        if _looks_like_filename_param(p.name):
            if p.kind == inspect.Parameter.POSITIONAL_ONLY:
                args.append(filename)
            else:
                kwargs[p.name] = filename
            consumed.add(idx)
            assigned_filename = True
            continue
        missing_required.append(p.name)

    if missing_required:
        raise LoweringAdapterError(
            "unsupported required parameter(s): " + ", ".join(missing_required)
        )

    call_repr = f"args={len(args)}, kwargs={sorted(kwargs.keys())}, filename={'yes' if assigned_filename else 'no'}"
    return args, kwargs, call_repr


def invoke_lowering_helper(
    fn: Any,
    *,
    helper_name: str,
    input_mode: str,
    source: str,
    tree: ast.AST,
    filename: str,
) -> tuple[Any, LoweringCallMeta]:
    args, kwargs, call_repr = _build_call(
        fn,
        source=source,
        tree=tree,
        filename=filename,
        preferred_input=input_mode,
    )
    result = fn(*args, **kwargs)
    return result, LoweringCallMeta(
        helper_name=helper_name,
        input_mode=input_mode,
        signature=str(_safe_signature(fn)),
        call_repr=call_repr,
    )


def lower_source_with_compat(
    lower_module: Any,
    *,
    source: str,
    filename: str,
    candidate_order: Iterable[tuple[str, str]] | None = None,
) -> tuple[Any, LoweringCallMeta]:
    """
    Lower source text to IR module using a signature-safe compatibility matrix.

    `candidate_order` entries are `(helper_name, input_mode)` where input_mode is
    `"ast"` or `"source"`.
    """
    tree = ast.parse(source, filename=filename)
    candidates = list(
        candidate_order
        or (
            ("lower_to_ir", "ast"),
            ("lower_from_ast", "ast"),
            ("lower_ast", "ast"),
            ("lower_module", "ast"),
            ("lower_from_source", "source"),
            ("lower_source", "source"),
            ("compile_source", "source"),
            ("lower", "ast"),
            ("compile", "ast"),
            ("lower", "source"),
            ("compile", "source"),
        )
    )
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for helper_name, input_mode in candidates:
        key = (helper_name, input_mode)
        if key in seen:
            continue
        seen.add(key)
        fn = getattr(lower_module, helper_name, None)
        if not callable(fn):
            continue
        try:
            out, meta = invoke_lowering_helper(
                fn,
                helper_name=helper_name,
                input_mode=input_mode,
                source=source,
                tree=tree,
                filename=filename,
            )
            return out, meta
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{helper_name}[{input_mode}]: {exc}")
            continue

    raise LoweringAdapterError(
        "no compatible lowering helper found; attempts: "
        + "; ".join(errors[:12] or ["<none>"])
    )


__all__ = [
    "LoweringAdapterError",
    "LoweringCallMeta",
    "invoke_lowering_helper",
    "lower_source_with_compat",
]

