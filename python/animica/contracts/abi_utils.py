from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import typer

CONSTRUCTOR_CANDIDATES = ("constructor", "__init__", "init")


def _require_abi_codec():
    try:
        from omni_sdk.types.abi import decode_return, encode_call, normalize_abi
    except Exception as exc:  # pragma: no cover - dependency gate
        raise RuntimeError(
            "Contract ABI tools require omni_sdk.types.abi; ensure the Animica SDK is available."
        ) from exc
    return normalize_abi, encode_call, decode_return


def _coerce_abi_shape(abi_value: Any) -> Any:
    if not isinstance(abi_value, dict):
        return abi_value
    if {"entries", "functions", "events"} <= set(abi_value.keys()):
        return abi_value

    funcs = abi_value.get("functions")
    events = abi_value.get("events")
    if not isinstance(funcs, list) or not isinstance(events, list):
        return abi_value

    entries: list[dict[str, Any]] = []
    for fn in funcs:
        if not isinstance(fn, dict):
            continue
        item = dict(fn)
        item.setdefault("type", "function")
        entries.append(item)
    for ev in events:
        if not isinstance(ev, dict):
            continue
        item = dict(ev)
        item.setdefault("type", "event")
        entries.append(item)
    return entries


def coerce_abi(abi_value: Any) -> Any:
    return _coerce_abi_shape(abi_value)


def load_abi_document(path: Path) -> Any:
    try:
        data = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"ABI file not found: {path}") from exc
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"invalid ABI JSON at {path}: {exc}") from exc

    if isinstance(data, dict) and "abi" in data:
        return _coerce_abi_shape(data["abi"])
    return _coerce_abi_shape(data)


def parse_args_json(raw: Optional[str], *, option_name: str = "--args") -> list[Any] | dict[str, Any]:
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"{option_name} must be valid JSON: {exc}") from exc
    if not isinstance(parsed, (list, dict)):
        raise typer.BadParameter(f"{option_name} must be a JSON array or object")
    return parsed


def to_positional_args(abi_obj: Any, method: str, args_obj: Any) -> list[Any]:
    normalize_abi, _, _ = _require_abi_codec()

    if args_obj is None:
        return []
    if isinstance(args_obj, list):
        return args_obj
    if not isinstance(args_obj, dict):
        raise typer.BadParameter("arguments must decode to a JSON array or object")

    normalized = normalize_abi(abi_obj)
    functions = normalized.get("functions", {})
    if not isinstance(functions, dict) or method not in functions:
        raise typer.BadParameter(f"method {method!r} is not present in ABI")

    fn = functions[method]
    inputs = fn.get("inputs", []) if isinstance(fn, dict) else []
    if not isinstance(inputs, list):
        raise typer.BadParameter(f"ABI function metadata for {method!r} is malformed")

    positional: list[Any] = []
    for param in inputs:
        if not isinstance(param, dict):
            raise typer.BadParameter(f"ABI function metadata for {method!r} is malformed")
        name = param.get("name")
        if not isinstance(name, str) or not name:
            raise typer.BadParameter(
                f"ABI for {method!r} includes unnamed parameters; use positional JSON array args"
            )
        if name not in args_obj:
            raise typer.BadParameter(f"missing named argument: {name}")
        positional.append(args_obj[name])
    return positional


def encode_calldata(abi_obj: Any, method: str, args: list[Any]) -> bytes:
    _, encode_call, _ = _require_abi_codec()
    try:
        return encode_call(abi_obj, method, args)
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"failed to encode calldata for {method!r}: {exc}") from exc


def decode_method_result(abi_obj: Any, method: str, raw: bytes) -> Any:
    _, _, decode_return = _require_abi_codec()
    try:
        return decode_return(abi_obj, method, raw)
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"failed to decode result for {method!r}: {exc}") from exc


def normalize_abi(abi_obj: Any) -> dict[str, Any]:
    normalize_abi_fn, _, _ = _require_abi_codec()
    return normalize_abi_fn(abi_obj)


def method_info(abi_obj: Any, method: str) -> dict[str, Any]:
    normalized = normalize_abi(abi_obj)
    functions = normalized.get("functions", {})
    if not isinstance(functions, dict) or method not in functions:
        raise typer.BadParameter(f"method {method!r} is not present in ABI")
    info = functions[method]
    if not isinstance(info, dict):
        raise typer.BadParameter(f"ABI metadata for {method!r} is malformed")
    return info


def state_mutability(abi_obj: Any, method: str) -> Optional[str]:
    info = method_info(abi_obj, method)
    mut = info.get("stateMutability") or info.get("state_mutability")
    return str(mut) if isinstance(mut, str) else None


def is_read_only_method(abi_obj: Any, method: str) -> bool:
    mut = (state_mutability(abi_obj, method) or "").strip().lower()
    return mut in {"view", "pure", "readonly", "read"}


def pick_constructor_method(abi_obj: Any) -> Optional[str]:
    normalized = normalize_abi(abi_obj)
    functions = normalized.get("functions", {})
    if not isinstance(functions, dict):
        return None
    lowered = {name.lower(): name for name in functions.keys() if isinstance(name, str)}
    for candidate in CONSTRUCTOR_CANDIDATES:
        if candidate in lowered:
            return lowered[candidate]
    return None


def encode_constructor_args(abi_obj: Any, args: list[Any]) -> tuple[Optional[str], Optional[bytes]]:
    if not args:
        return None, None
    constructor_method = pick_constructor_method(abi_obj)
    if constructor_method is None:
        raise typer.BadParameter(
            "constructor args were provided, but ABI has no constructor-like method "
            "(expected one of: constructor, __init__, init)"
        )
    calldata = encode_calldata(abi_obj, constructor_method, args)
    return constructor_method, calldata


def parse_hex_bytes(raw: str, *, field: str = "value") -> bytes:
    text = raw.strip()
    if text.startswith(("0x", "0X")):
        text = text[2:]
    if not text:
        raise typer.BadParameter(f"{field} cannot be empty")
    if len(text) % 2 != 0:
        text = "0" + text
    try:
        return bytes.fromhex(text)
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"{field} must be valid hex bytes") from exc
