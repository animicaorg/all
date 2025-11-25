"""
randomness.rpc.methods
----------------------

JSON-RPC method shims for the randomness/beacon module.

These are intentionally thin: they validate/normalize inputs,
then delegate to a `RandomnessService` (see
`randomness.adapters.rpc_mount`) that owns persistence and
core logic.

Exposed methods:

- rand.getParams()
- rand.getRound()
- rand.commit(address, payload, salt, round_id?)
- rand.reveal(address, payload, salt, round_id?)
- rand.getBeacon(round_id?)
- rand.getHistory(start?, limit?)

All hex-typed inputs/outputs are 0x-prefixed.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Mapping, Callable
from pydantic import BaseModel, Field, validator

# Import only for typing; the concrete implementation lives in adapters.rpc_mount.
try:
    from ..adapters.rpc_mount import RandomnessService  # type: ignore
except Exception:  # pragma: no cover - typing fallback if import side-effects
    class RandomnessService:  # type: ignore
        pass


# ---------- helpers ----------

def _strip_0x(s: str) -> str:
    return s[2:] if s.startswith("0x") or s.startswith("0X") else s


def _hex_to_bytes(s: str) -> bytes:
    return bytes.fromhex(_strip_0x(s))


def _bytes_to_hex(b: bytes) -> str:
    return "0x" + b.hex()


# ---------- request models ----------

class _RoundArg(BaseModel):
    round_id: Optional[int] = Field(
        default=None, description="Optional explicit round. Defaults to current round."
    )


class CommitParams(_RoundArg):
    address: str = Field(..., description="Sender/account address (string).")
    payload: str = Field(..., description="0x-hex payload committed to.")
    salt: str = Field(..., description="0x-hex salt (private until reveal).")

    @validator("payload")
    def _payload_hex(cls, v: str) -> str:
        _ = _hex_to_bytes(v)
        return v

    @validator("salt")
    def _salt_hex(cls, v: str) -> str:
        _ = _hex_to_bytes(v)
        return v


class RevealParams(_RoundArg):
    address: str = Field(..., description="Sender/account address (string).")
    payload: str = Field(..., description="0x-hex payload (revealed).")
    salt: str = Field(..., description="0x-hex salt (revealed).")

    @validator("payload")
    def _payload_hex(cls, v: str) -> str:
        _ = _hex_to_bytes(v)
        return v

    @validator("salt")
    def _salt_hex(cls, v: str) -> str:
        _ = _hex_to_bytes(v)
        return v


class BeaconQuery(_RoundArg):
    pass


class HistoryQuery(BaseModel):
    start: Optional[int] = Field(
        default=None, description="Optional starting round id (inclusive)."
    )
    limit: int = Field(
        default=20, ge=1, le=500, description="Max number of records to return."
    )


# ---------- method handlers ----------

def rand_get_params(service: RandomnessService, _args: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """
    Returns beacon/round/VDF parameters and config.
    """
    return service.get_params()


def rand_get_round(service: RandomnessService, _args: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """
    Returns current round info: round_id, phase, deadlines, now.
    """
    return service.get_round()


def rand_commit(service: RandomnessService, args: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Submit a commitment for (address, salt, payload) for a given or current round.

    Returns implementation-defined record; typical keys:
      - round_id
      - address
      - commitment (0x-hex)
      - accepted (bool)
      - reason (optional)
      - deadline_commit / deadline_reveal (optional)
    """
    params = CommitParams(**args)
    payload = _hex_to_bytes(params.payload)
    salt = _hex_to_bytes(params.salt)
    return service.commit(
        address=params.address,
        salt=salt,
        payload=payload,
        round_id=params.round_id,
    )


def rand_reveal(service: RandomnessService, args: Mapping[str, Any]) -> Dict[str, Any]:
    """
    Reveal the preimage (salt, payload) corresponding to a prior commitment.

    Returns implementation-defined record; typical keys:
      - round_id
      - address
      - ok (bool)
      - included (bool)
      - reason (optional)
    """
    params = RevealParams(**args)
    payload = _hex_to_bytes(params.payload)
    salt = _hex_to_bytes(params.salt)
    return service.reveal(
        address=params.address,
        salt=salt,
        payload=payload,
        round_id=params.round_id,
    )


def rand_get_beacon(service: RandomnessService, args: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """
    Get the finalized beacon output for the given or latest round.

    Returns implementation-defined beacon record; typical keys:
      - round_id
      - beacon (0x-hex)
      - aggregate (0x-hex)    # pre-VDF aggregate
      - vdf_input (0x-hex)
      - vdf_proof (0x-hex)
      - mixed_with_qrng (bool)
      - timestamp
    """
    q = BeaconQuery(**(args or {}))
    return service.get_beacon(round_id=q.round_id)


def rand_get_history(service: RandomnessService, args: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """
    Paginated history of recent beacons. Returns:
      - items: [beacon_record, ...]
      - next_start: Optional[int]
    """
    q = HistoryQuery(**(args or {}))
    return service.get_history(start=q.start, limit=q.limit)


# Public registry mapping JSON-RPC method names to callables.
# Each callable has signature: (service, args_dict) -> result
RPC_METHODS: Dict[str, Callable[..., Any]] = {
    "rand.getParams": rand_get_params,
    "rand.getRound": rand_get_round,
    "rand.commit": rand_commit,
    "rand.reveal": rand_reveal,
    "rand.getBeacon": rand_get_beacon,
    "rand.getHistory": rand_get_history,
}

__all__ = [
    "CommitParams",
    "RevealParams",
    "BeaconQuery",
    "HistoryQuery",
    "rand_get_params",
    "rand_get_round",
    "rand_commit",
    "rand_reveal",
    "rand_get_beacon",
    "rand_get_history",
    "RPC_METHODS",
]
