from __future__ import annotations

"""
Simulation models

These helpers define a small, stable contract-call simulation API used by
studio-services to (optionally) compile a Python contract and then execute a
single function call in a side-effect-free environment.

Two mutually exclusive modes are supported:

1) Local source mode:
   - Provide `source` (Python) and `manifest` (ABI JSON).
   - The service compiles the code and runs the call against an ephemeral state.

2) On-chain contract mode:
   - Provide `address` of a deployed contract (ABI may be fetched or provided).
   - The service loads code/ABI by address and runs the call read-only.

Exactly one of the modes must be used.
"""

from typing import Any, Dict, List, Optional

try:
    # Pydantic v2
    from pydantic import BaseModel, Field, PositiveInt, conint, model_validator

    _IS_PYDANTIC_V2 = True
except Exception:  # pragma: no cover - v1 fallback
    from pydantic.v1 import BaseModel, Field, PositiveInt  # type: ignore
    from pydantic.v1.types import conint  # type: ignore

    _IS_PYDANTIC_V2 = False

from .common import Address, ChainId, Hash, Hex


class SimulateCall(BaseModel):
    """
    Request to simulate a single contract function call.

    Fields
    ------
    chain_id: ChainId
        Target chain id (used for domain separation and defaults).
    # Mode A (local source):
    source: Optional[str]
        Python contract source (mutually exclusive with `address`).
    manifest: Optional[Dict[str, Any]]
        Manifest/ABI JSON required when `source` is provided.
    # Mode B (on-chain):
    address: Optional[Address]
        Target contract address (mutually exclusive with `source`).
    abi: Optional[Dict[str, Any]]
        ABI to use when calling a deployed address (optional convenience).
    # Call shape:
    function: str
        Function name to invoke.
    args: Dict[str, Any]
        Arguments keyed by ABI parameter names (or positional via "_args": []).
    sender: Optional[Address]
        Simulated sender (used for access control / events); defaults to a zero address.
    value: Optional[conint(ge=0)]
        Value (native token) to transfer alongside the call; defaults to 0.
    gas_limit: Optional[conint(ge=0)]
        Upper bound for gas; simulator may clamp to internal maximums.
    gas_price: Optional[conint(ge=0)]
        Simulated gas price (only affects accounting in reports).
    seed: Optional[Hex]
        Deterministic PRNG seed for the simulator (if the VM exposes a PRNG).
    context: Optional[Dict[str, Any]]
        Optional block/tx context overrides (e.g., {"height": 123, "timestamp": 1700000000}).
    """

    chain_id: ChainId = Field(..., description="Target chain id.")
    # Mode A
    source: Optional[str] = Field(
        default=None, description="Python contract source (exclusive with `address`)."
    )
    manifest: Optional[Dict[str, Any]] = Field(
        default=None, description="Manifest/ABI JSON when `source` is provided."
    )
    # Mode B
    address: Optional[Address] = Field(
        default=None, description="Deployed contract address (exclusive with `source`)."
    )
    abi: Optional[Dict[str, Any]] = Field(
        default=None, description="Optional ABI to use with `address`."
    )

    # Call
    function: str = Field(..., min_length=1, description="Function name to invoke.")
    args: Dict[str, Any] = Field(
        default_factory=dict,
        description="Arguments keyed by name (or '_args' for positional).",
    )
    sender: Optional[Address] = Field(
        default=None, description="Simulated sender address."
    )
    value: Optional[conint(ge=0)] = Field(default=0, description="Value in base units (default 0).")  # type: ignore[misc]
    gas_limit: Optional[conint(ge=0)] = Field(default=None, description="Gas upper bound (simulated).")  # type: ignore[misc]
    gas_price: Optional[conint(ge=0)] = Field(default=None, description="Gas price (simulated).")  # type: ignore[misc]
    seed: Optional[Hex] = Field(
        default=None, description="Deterministic PRNG seed for the VM."
    )
    context: Optional[Dict[str, Any]] = Field(
        default=None, description="Optional block/tx context overrides."
    )

    class Config:  # type: ignore[override]
        extra = "forbid"
        anystr_strip_whitespace = True
        populate_by_name = True

    if _IS_PYDANTIC_V2:

        @model_validator(mode="after")
        def _mode_exclusivity(cls, v: "SimulateCall") -> "SimulateCall":  # type: ignore[override]
            has_src = bool(v.source)
            has_addr = bool(v.address)
            if has_src == has_addr:
                raise ValueError(
                    "Provide exactly one of {source+manifest} OR {address}."
                )
            if has_src and v.manifest is None:
                raise ValueError("`manifest` is required when `source` is provided.")
            if has_addr and v.abi is not None and not isinstance(v.abi, dict):
                raise ValueError("`abi` must be an object when provided.")
            return v

    else:  # pragma: no cover - v1 compatibility

        @classmethod
        def validate(cls, value):  # type: ignore[override]
            obj = super().validate(value)
            has_src = bool(obj.source)
            has_addr = bool(obj.address)
            if has_src == has_addr:
                raise ValueError(
                    "Provide exactly one of {source+manifest} OR {address}."
                )
            if has_src and obj.manifest is None:
                raise ValueError("`manifest` is required when `source` is provided.")
            if has_addr and obj.abi is not None and not isinstance(obj.abi, dict):
                raise ValueError("`abi` must be an object when provided.")
            return obj


class SimulatedEvent(BaseModel):
    """
    Event emitted during simulation (VM stdlib/events format).
    """

    name: str = Field(..., description="Event name.")
    args: Dict[str, Any] = Field(
        default_factory=dict, description="Event arguments (decoded)."
    )

    class Config:  # type: ignore[override]
        extra = "forbid"


class SimulateResult(BaseModel):
    """
    Simulation result envelope.

    The simulator is side-effect-free. Any `state_diff` provided is an
    *informational* preview of writes that would have occurred.
    """

    ok: bool = Field(..., description="True if the call executed without VM error.")
    return_value: Any = Field(None, description="Decoded return value (per ABI).")
    events: List[SimulatedEvent] = Field(
        default_factory=list, description="Emitted events (decoded)."
    )
    gas_used: PositiveInt = Field(
        ..., description="Gas units consumed by the simulated call."
    )
    logs_text: List[str] = Field(
        default_factory=list, description="Optional textual logs/trace."
    )
    error: Optional[str] = Field(default=None, description="Error string if ok=False.")
    code_hash: Optional[Hash] = Field(
        default=None, description="Computed code hash (if compiled or resolved)."
    )
    abi: Optional[Dict[str, Any]] = Field(
        default=None, description="Normalized ABI used for encoding/decoding."
    )
    state_diff: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Preview of state mutations (for UX only; simulator does not persist).",
    )

    class Config:  # type: ignore[override]
        extra = "forbid"


__all__ = ["SimulateCall", "SimulatedEvent", "SimulateResult"]
