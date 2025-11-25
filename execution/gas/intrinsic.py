"""
execution.gas.intrinsic — intrinsic gas for tx kinds (transfer/deploy/call/blob)

Overview
--------
Computes the *intrinsic* gas (base cost independent of state execution) for
Animica transactions. Intrinsic gas covers:

* A base cost by tx kind: `transfer`, `deploy`, `call` (and an extra `blob`
  base if blob bytes are attached).
* Calldata size (bytes in the envelope; deterministic CBOR payload).
* Access list entries (addresses and storage keys) if present.
* Optional blob attachment bytes (data-availability payload size proxy).

The exact numbers are network-parameterized. This module ships safe defaults and
allows overrides via `resolve_params(...)`.

Notes
-----
* All arithmetic is non-negative and u256-capped.
* This module is standalone and *does not* import DA or VM packages to avoid
  heavy dependency chains during admission checks.
* For canonical values, wire your node to load from `spec/params.yaml` and pass
  them in via `resolve_params(path=...)` or direct overrides.

API
---
    params = resolve_params()  # defaults or loaded from file + overrides
    gas = intrinsic_gas("transfer", calldata_len=12, params=params)
    gas.total  # -> integer (u256), ready to feed into fee checks

"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Literal, Mapping, Optional, Tuple

import json

# Optional YAML (graceful if missing)
try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # type: ignore

from execution.types.gas import (
    Gas,
    U256_MAX,
    is_u256,
    saturating_add,
    mul_price_gas,  # reused for generic multiply w/ cap semantics
)


# --------------------------- parameter model ---------------------------------


@dataclass(frozen=True)
class IntrinsicGasParams:
    """
    Parameter set driving intrinsic gas calculation.

    The defaults below are conservative placeholders suitable for devnets.
    Tune via spec/params.yaml for your network.
    """
    # Base costs by tx kind
    base_transfer: int = 21_000
    base_deploy: int = 53_000
    base_call: int = 21_000

    # Extra base charged when a tx carries blob bytes (DA attachment)
    base_blob: int = 5_000

    # Linear components
    calldata_per_byte: int = 16
    access_list_address: int = 2_400
    access_list_storage_key: int = 1_900

    # DA/Blob component (linear per byte; *not* the full DA fee market)
    blob_per_byte: int = 1

    # Global arithmetic cap
    cap: int = U256_MAX

    def validate(self) -> "IntrinsicGasParams":
        for field, value in self.__dict__.items():
            if not isinstance(value, int):
                raise TypeError(f"{field} must be int (got {type(value).__name__})")
            if value < 0:
                raise ValueError(f"{field} must be non-negative")
            if not is_u256(value):
                raise OverflowError(f"{field} exceeds u256")
        return self


def _merge_maps(*maps: Mapping[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for m in maps:
        for k, v in m.items():
            out[str(k)] = v
    return out


def _load_params_file(path: Path) -> Mapping[str, Any]:
    txt = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"} and yaml is not None:
        data = yaml.safe_load(txt)  # type: ignore
    else:
        try:
            data = json.loads(txt)
        except Exception:
            if yaml is None:
                raise
            data = yaml.safe_load(txt)  # type: ignore
    if not isinstance(data, dict):
        raise ValueError(f"{path}: expected mapping at root")
    return data


def resolve_params(
    path: Optional[str | Path] = None,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
) -> IntrinsicGasParams:
    """
    Build a validated `IntrinsicGasParams` from defaults, optional file, then overrides.

    `path` may point to `spec/params.yaml` or a minimal JSON/YAML containing keys
    that match the IntrinsicGasParams fields under:
        gas:
          base_transfer: ...
          base_deploy: ...
          ...
    If the shape differs, only keys that match are applied.
    """
    base = IntrinsicGasParams()
    merged: Dict[str, Any] = {}

    if path is not None:
        p = Path(path)
        if p.exists():
            data = _load_params_file(p)
            # Accept either flat keys under "gas" or top-level keys
            src = {}
            if "gas" in data and isinstance(data["gas"], dict):
                src = data["gas"]  # type: ignore[assignment]
            else:
                src = data  # type: ignore[assignment]
            merged = _merge_maps(merged, src)

    if overrides:
        merged = _merge_maps(merged, overrides)

    if not merged:
        return base.validate()

    # Filter only known fields
    known = {k: v for k, v in merged.items() if hasattr(base, k)}
    obj = IntrinsicGasParams(**{**base.__dict__, **known})  # type: ignore[arg-type]
    return obj.validate()


# --------------------------- computation model -------------------------------


@dataclass(frozen=True)
class IntrinsicGas:
    """
    Result of an intrinsic gas computation with a debuggable breakdown.
    """
    kind: Literal["transfer", "deploy", "call", "blob"]
    base: int
    calldata: int
    access_list: int
    blob: int

    @property
    def total(self) -> Gas:
        return Gas(
            saturating_add(
                saturating_add(self.base, self.calldata, cap=U256_MAX),
                saturating_add(self.access_list, self.blob, cap=U256_MAX),
                cap=U256_MAX,
            )
        )

    def breakdown(self) -> Dict[str, int]:
        return {
            "base": self.base,
            "calldata": self.calldata,
            "access_list": self.access_list,
            "blob": self.blob,
            "total": int(self.total),
        }


def _mul(a: int, b: int, cap: int) -> int:
    # Reuse mul_price_gas for u256-capped multiply; semantics match what we need.
    return mul_price_gas(a, b, saturating=True, cap=cap)


def intrinsic_gas(
    kind: Literal["transfer", "deploy", "call", "blob"],
    *,
    calldata: Optional[bytes | bytearray | memoryview] = None,
    calldata_len: Optional[int] = None,
    access_list_addrs: int = 0,
    access_list_keys: int = 0,
    blob_bytes: int = 0,
    params: Optional[IntrinsicGasParams] = None,
) -> IntrinsicGas:
    """
    Compute intrinsic gas for a transaction envelope.

    Parameters
    ----------
    kind:
        "transfer", "deploy", "call". A special "blob" kind computes blob base
        only (useful for unit tests or DA-only envelopes), but typical txs are
        one of transfer/deploy/call with optional blob_bytes > 0.
    calldata, calldata_len:
        Provide either `calldata` (bytes-like) or an explicit `calldata_len`.
    access_list_addrs, access_list_keys:
        Number of unique addresses and storage keys in the access list.
    blob_bytes:
        Size (bytes) of an attached blob (if any). This is a linear proxy; the
        full DA fee market may add more costs elsewhere.
    params:
        Optional `IntrinsicGasParams`. If omitted, safe defaults are used.

    Returns
    -------
    IntrinsicGas (with `.total` as a Gas newtype and `.breakdown()` for debugging)
    """
    p = (params or IntrinsicGasParams()).validate()

    if calldata is not None and calldata_len is not None:
        raise ValueError("Provide either calldata or calldata_len, not both")

    cd_len = len(calldata) if calldata is not None else int(calldata_len or 0)
    if cd_len < 0:
        raise ValueError("calldata_len must be non-negative")
    if access_list_addrs < 0 or access_list_keys < 0:
        raise ValueError("access list counts must be non-negative")
    if blob_bytes < 0:
        raise ValueError("blob_bytes must be non-negative")

    # Base component
    if kind == "transfer":
        base = p.base_transfer
    elif kind == "deploy":
        base = p.base_deploy
    elif kind == "call":
        base = p.base_call
    elif kind == "blob":
        base = p.base_blob
    else:  # pragma: no cover (type checker guards literals)
        raise ValueError(f"unknown tx kind: {kind!r}")

    # Calldata
    calldata_cost = _mul(cd_len, p.calldata_per_byte, p.cap)

    # Access list
    al_addr = _mul(access_list_addrs, p.access_list_address, p.cap)
    al_keys = _mul(access_list_keys, p.access_list_storage_key, p.cap)
    access_list_cost = saturating_add(al_addr, al_keys, cap=p.cap)

    # Blob component
    blob_cost = 0
    if blob_bytes > 0:
        blob_cost = saturating_add(p.base_blob, _mul(blob_bytes, p.blob_per_byte, p.cap), cap=p.cap)

    return IntrinsicGas(
        kind=kind,
        base=base,
        calldata=calldata_cost,
        access_list=access_list_cost,
        blob=blob_cost,
    )


__all__ = ["IntrinsicGasParams", "IntrinsicGas", "resolve_params", "intrinsic_gas"]
