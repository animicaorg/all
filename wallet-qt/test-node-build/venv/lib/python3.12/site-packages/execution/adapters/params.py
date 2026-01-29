"""
execution.adapters.params — loader for ChainParams defined in core/types/params.py

This adapter turns a YAML/JSON mapping (e.g. spec/params.yaml) into a strongly
typed `ChainParams` dataclass instance, with light validation and convenient
defaults. Unknown keys in the config are ignored (forward compatibility).

Typical usage
-------------
    from execution.adapters.params import load_chain_params

    params = load_chain_params()  # finds spec/params.yaml or ANIMICA_PARAMS
    # or:
    params = load_chain_params("networks/dev/spec/params.yaml",
                               overrides={"consensus": {"theta_initial": 1_000_000}},
                               expected_chain_id=1337)

Design notes
------------
• Zero dependency on the rest of execution/ (besides the ChainParams type).
• YAML is preferred; JSON works too. If PyYAML is not installed, JSON is tried.
• Gracefully handles nested dataclasses and simple collections (list/dict/tuple/set).
• Optional minimal validation (expected_chain_id).
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import (Any, Dict, Mapping, Optional, Sequence, Tuple, Type,
                    TypeVar, get_args, get_origin)

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - fallback to JSON only
    yaml = None  # type: ignore

# ---- public errors -----------------------------------------------------------


class ParamsError(Exception):
    """Base error for params adapter."""


class ParamsFileNotFound(ParamsError):
    """Raised when a params file cannot be located."""


class ParamsValidationError(ParamsError):
    """Raised when input fails validation (e.g., chainId mismatch)."""


# ---- import ChainParams from core -------------------------------------------

try:
    # Expected to be a dataclass
    from core.types.params import ChainParams  # type: ignore
except Exception as e:  # pragma: no cover
    raise ImportError(
        "Failed to import core.types.params.ChainParams — ensure core/ is on PYTHONPATH."
    ) from e


T = TypeVar("T")


# ---- public API --------------------------------------------------------------


def load_chain_params(
    source: Optional[os.PathLike | str | Mapping[str, Any]] = None,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
    expected_chain_id: Optional[int] = None,
) -> ChainParams:
    """
    Load and parse ChainParams.

    Parameters
    ----------
    source:
        • Path to YAML/JSON file, OR
        • Mapping already loaded in-memory, OR
        • None → search order:
            1) $ANIMICA_PARAMS (file path)
            2) ./spec/params.yaml (relative to CWD)
    overrides:
        Mapping to deep-merge on top of the loaded mapping (last-wins).
    expected_chain_id:
        If provided, assert that resulting params.chain_id equals this value.

    Returns
    -------
    ChainParams
    """
    data: Mapping[str, Any]
    if source is None:
        path = _default_params_path()
        data = _load_mapping_file(path)
    elif isinstance(source, (str, Path)):
        data = _load_mapping_file(Path(source))
    else:
        # already a mapping
        data = source

    if overrides:
        data = _deep_merge(dict(data), dict(overrides))

    params: ChainParams = _from_mapping(ChainParams, data)

    # Optional sanity check
    chain_id = getattr(params, "chain_id", None) or getattr(params, "chainId", None)
    if (
        expected_chain_id is not None
        and chain_id is not None
        and chain_id != expected_chain_id
    ):
        raise ParamsValidationError(
            f"expected chain_id={expected_chain_id}, got {chain_id}"
        )

    return params


def params_to_dict(params: ChainParams) -> Dict[str, Any]:
    """
    Serialize ChainParams → dict using dataclasses.asdict (stable, JSON-friendly).
    """
    if not is_dataclass(params):
        raise ParamsError("params_to_dict expects a dataclass instance")
    return dataclasses.asdict(params)


# ---- internal: locating & parsing files -------------------------------------


def _default_params_path() -> Path:
    env = os.getenv("ANIMICA_PARAMS")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p
        raise ParamsFileNotFound(f"$ANIMICA_PARAMS points to a non-existent file: {p}")

    p = Path("spec/params.yaml")
    if p.exists():
        return p

    raise ParamsFileNotFound(
        "Could not locate params file. Set $ANIMICA_PARAMS or create ./spec/params.yaml"
    )


def _load_mapping_file(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        raise ParamsFileNotFound(f"Params file not found: {path}")

    text = path.read_text(encoding="utf-8")

    # Try YAML first if available; fall back to JSON
    if yaml is not None:
        try:
            loaded = yaml.safe_load(text)  # type: ignore
            if isinstance(loaded, Mapping):
                return loaded
            raise ParamsError(
                f"Top-level YAML must be a mapping (got {type(loaded).__name__})"
            )
        except Exception as e:
            raise ParamsError(f"Failed to parse YAML in {path}: {e}") from e
    else:
        try:
            loaded = json.loads(text)
            if isinstance(loaded, Mapping):
                return loaded
            raise ParamsError(
                f"Top-level JSON must be a mapping (got {type(loaded).__name__})"
            )
        except Exception as e:
            raise ParamsError(f"Failed to parse JSON in {path}: {e}") from e


# ---- internal: mapping → dataclass ------------------------------------------


def _from_mapping(cls: Type[T], data: Mapping[str, Any]) -> T:
    """
    Construct dataclass `cls` from a (possibly nested) mapping.

    Unknown keys are ignored. For nested dataclasses and common collection types,
    conversion is recursive. Optional[...] is handled by accepting None.
    """
    if not is_dataclass(cls):
        raise ParamsError(f"_from_mapping expects a dataclass type (got {cls!r})")

    kwargs: Dict[str, Any] = {}
    for f in fields(cls):
        name = f.name
        if name not in data:
            continue
        value = data[name]
        kwargs[name] = _coerce_value(f.type, value)
    try:
        return cls(**kwargs)  # type: ignore[arg-type]
    except TypeError as e:
        # Include a hint with the bad key names if possible
        bad_keys = ", ".join(sorted(set(data.keys()) - {f.name for f in fields(cls)}))
        hint = f" (unknown keys ignored: {bad_keys})" if bad_keys else ""
        raise ParamsError(f"Unable to construct {cls.__name__}: {e}{hint}") from e


def _coerce_value(t: Any, v: Any) -> Any:
    # Handle Optional[T] / Union[T, None]
    origin = get_origin(t)
    args = get_args(t)

    # Optional[...] → pick inner type when value is not None
    if origin is not None and type(None) in args:
        inner = next((a for a in args if a is not type(None)), Any)  # noqa: E721
        return None if v is None else _coerce_value(inner, v)

    # Dataclass type
    if isinstance(t, type) and is_dataclass(t) and isinstance(v, Mapping):
        return _from_mapping(t, v)

    # Collections
    if (
        origin in (list, tuple, set)
        and isinstance(v, Sequence)
        and not isinstance(v, (str, bytes, bytearray))
    ):
        inner = args[0] if args else Any
        seq = [_coerce_value(inner, x) for x in v]
        if origin is list:
            return list(seq)
        if origin is tuple:
            return tuple(seq)
        if origin is set:
            return set(seq)

    # Dict[K, V]
    if origin in (dict, Mapping) and isinstance(v, Mapping):
        kt = args[0] if args else Any
        vt = args[1] if len(args) > 1 else Any
        return {_coerce_value(kt, k): _coerce_value(vt, val) for k, val in v.items()}

    # Primitives: int/str/float/bool/bytes
    if t in (int, float, str, bool):
        try:
            return t(v)  # type: ignore[misc,call-arg]
        except Exception:
            return v  # best-effort; dataclass ctor may still reject

    if t is bytes:
        if isinstance(v, (bytes, bytearray)):
            return bytes(v)
        if isinstance(v, str):
            s = v.strip().lower()
            if s.startswith("0x"):
                s = s[2:]
            try:
                return bytes.fromhex(s)
            except Exception:
                # fall back to utf-8 bytes
                return v.encode("utf-8")
        return v

    # Path
    if t is Path and isinstance(v, (str, os.PathLike)):
        return Path(v)

    # If annotation is typing.Any or something exotic, pass through
    return v


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """
    Deep-merge mapping `b` into `a` (mutates and returns `a`).
    """
    for k, v in b.items():
        if k in a and isinstance(a[k], dict) and isinstance(v, Mapping):
            _deep_merge(a[k], dict(v))
        else:
            a[k] = v
    return a


__all__ = [
    "load_chain_params",
    "params_to_dict",
    "ParamsError",
    "ParamsFileNotFound",
    "ParamsValidationError",
]
