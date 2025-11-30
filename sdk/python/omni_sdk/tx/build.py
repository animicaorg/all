"""
omni_sdk.tx.build
=================

Builders for Animica transactions (transfer / call / deploy) plus lightweight
gas estimation helpers you can use before signing & sending.

The builders return the ergonomic dataclass `omni_sdk.types.core.Tx`. You can then
feed that into `omni_sdk.tx.encode` to produce sign-bytes / CBOR, and into
`omni_sdk.tx.send` to submit via JSON-RPC.

Design notes
------------
- `transfer`: value transfer with empty data.
- `call`: contract call with raw `data` payload (ABI-encoded by higher layers).
- `deploy`: contract creation with raw `data` payload (manifest/code packed by higher layers).
- Gas helpers here mirror the defaults used by node-side logic and allow overrides.
  For authoritative estimates, prefer an RPC method if your node exposes one.

Examples
--------
    from omni_sdk.tx.build import transfer, call, deploy, intrinsic_gas, suggest_gas_limit, suggest_max_fee

    tx = transfer(
        from_addr="anim1...", to_addr="anim1...",
        amount=12345, nonce=7,
        chain_id=1, max_fee=2_000_000_000_000,
        gas_limit=suggest_gas_limit("transfer"),
    )

    # Later:
    # sign_bytes = encode.sign_bytes(tx)
    # sig = signer.sign(sign_bytes)
    # raw = encode.pack_signed(tx, signature=sig, alg_id=signer.alg_id, public_key=signer.public_key)
    # receipt = send.submit_and_wait(rpc, raw)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional, Sequence, Tuple

# Types/dataclasses used on the client
from omni_sdk.types.core import Address, ChainId, Tx  # type: ignore

# -----------------------------------------------------------------------------
# Gas estimation primitives
# -----------------------------------------------------------------------------

TxKind = Literal["transfer", "call", "deploy"]


@dataclass(frozen=True)
class GasParams:
    """
    Parameters for intrinsic-gas estimation. Defaults are conservative and match
    the node's reference values (see execution/gas/intrinsic.py).
    """

    # Base costs by tx kind
    base_transfer: int = 21_000
    base_deploy: int = 53_000
    base_call: int = 21_000

    # Extra base charged when a tx carries blob bytes (if/when used)
    base_blob: int = 5_000

    # Linear components
    calldata_per_byte: int = 16
    access_list_address: int = 2_400
    access_list_storage_key: int = 1_900

    # Blob/DA size proxy (optional)
    blob_per_kib: int = 24  # cost units per KiB of blob attachment (illustrative)


def intrinsic_gas(
    kind: TxKind,
    *,
    calldata_len: int = 0,
    access_list_addrs: int = 0,
    access_list_storage_keys: int = 0,
    blob_bytes: int = 0,
    params: Optional[GasParams] = None,
) -> int:
    """
    Compute a *rough* intrinsic gas for a transaction, independent of state execution.

    This is the minimum you must budget just to carry the envelope over the wire.
    Execution may require more (e.g., storage writes), so consider adding a safety
    multiplier via `suggest_gas_limit`.

    Args:
        kind: "transfer" | "call" | "deploy"
        calldata_len: number of bytes in the tx data payload
        access_list_addrs: count of addresses in access list (if used)
        access_list_storage_keys: count of storage keys in access list (if used)
        blob_bytes: size of any attached blob/DA bytes (0 if unused)
        params: optional override of gas parameters

    Returns:
        Integer gas amount (>= 0)
    """
    p = params or GasParams()

    if kind == "transfer":
        base = p.base_transfer
    elif kind == "call":
        base = p.base_call
    elif kind == "deploy":
        base = p.base_deploy
    else:
        raise ValueError(f"unknown tx kind: {kind}")

    g = int(base)
    if blob_bytes > 0:
        g += p.base_blob
        # simple size proxy (rounded up KiB)
        kib = (int(blob_bytes) + 1023) // 1024
        g += kib * p.blob_per_kib

    g += int(calldata_len) * p.calldata_per_byte
    g += int(access_list_addrs) * p.access_list_address
    g += int(access_list_storage_keys) * p.access_list_storage_key
    return max(g, 0)


def suggest_gas_limit(
    kind: TxKind,
    *,
    calldata_len: int = 0,
    access_list_addrs: int = 0,
    access_list_storage_keys: int = 0,
    blob_bytes: int = 0,
    params: Optional[GasParams] = None,
    safety_multiplier: float = 1.10,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    """
    Suggest a gasLimit by applying a safety multiplier over `intrinsic_gas`.

    Bounds:
      - If `minimum` is provided, the result is at least that.
      - If `maximum` is provided, the result is at most that.
    """
    base = intrinsic_gas(
        kind,
        calldata_len=calldata_len,
        access_list_addrs=access_list_addrs,
        access_list_storage_keys=access_list_storage_keys,
        blob_bytes=blob_bytes,
        params=params,
    )
    est = int(round(base * float(safety_multiplier)))
    if minimum is not None:
        est = max(est, int(minimum))
    if maximum is not None:
        est = min(est, int(maximum))
    return max(est, 0)


def suggest_max_fee(
    *,
    base_fee: int,
    tip: int = 0,
    surge_multiplier: float = 1.0,
    floor: Optional[int] = None,
    cap: Optional[int] = None,
) -> int:
    """
    Suggest a `max_fee` for the tx given a base fee and optional tip.

    The result is: round((base_fee + tip) * surge_multiplier), bounded by [floor, cap] if provided.
    """
    bf = max(int(base_fee), 0)
    tp = max(int(tip), 0)
    fee = int(round((bf + tp) * float(surge_multiplier)))
    if floor is not None:
        fee = max(fee, int(floor))
    if cap is not None:
        fee = min(fee, int(cap))
    return fee


# -----------------------------------------------------------------------------
# Core builders
# -----------------------------------------------------------------------------


def _require_non_negative(name: str, value: int) -> None:
    if int(value) < 0:
        raise ValueError(f"{name} must be non-negative")


def _ensure_bytes(data: bytes | bytearray | memoryview | None) -> bytes:
    if data is None:
        return b""
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, memoryview):
        return bytes(data)
    raise TypeError("data must be bytes-like")


def make_tx(
    *,
    from_addr: Address,
    to: Optional[Address],
    nonce: int,
    value: int,
    data: bytes,
    gas_limit: int,
    max_fee: int,
    chain_id: ChainId,
) -> Tx:
    """
    Construct a `Tx` dataclass. This performs local validation only.

    Note: no signing or hashing is performed here.
    """
    # Basic validation
    if not isinstance(from_addr, str) or len(from_addr) == 0:
        raise ValueError("from_addr must be a non-empty address string")
    if to is not None and (not isinstance(to, str) or len(to) == 0):
        raise ValueError("to must be None or a non-empty address string")
    _require_non_negative("nonce", nonce)
    _require_non_negative("value", value)
    _require_non_negative("gas_limit", gas_limit)
    _require_non_negative("max_fee", max_fee)
    _require_non_negative("chain_id", chain_id)

    return Tx(
        from_addr=from_addr,
        to=to,
        nonce=int(nonce),
        value=int(value),
        data=_ensure_bytes(data),
        gas_limit=int(gas_limit),
        max_fee=int(max_fee),
        chain_id=int(chain_id),
    )


def transfer(
    *,
    from_addr: Address,
    to_addr: Address,
    amount: int,
    nonce: int,
    gas_limit: Optional[int],
    max_fee: int,
    chain_id: ChainId,
) -> Tx:
    """
    Build a value-transfer transaction (no calldata).

    If `gas_limit` is None, we suggest one based on `intrinsic_gas("transfer")`.
    """
    if not isinstance(to_addr, str) or len(to_addr) == 0:
        raise ValueError("to_addr must be a non-empty address string")

    gl = (
        int(gas_limit)
        if gas_limit is not None
        else suggest_gas_limit("transfer", calldata_len=0)
    )
    return make_tx(
        from_addr=from_addr,
        to=to_addr,
        nonce=int(nonce),
        value=int(amount),
        data=b"",
        gas_limit=gl,
        max_fee=int(max_fee),
        chain_id=int(chain_id),
    )


def call(
    *,
    from_addr: Address,
    to_addr: Address,
    data: bytes,
    nonce: int,
    gas_limit: Optional[int],
    max_fee: int,
    chain_id: ChainId,
    value: int = 0,
) -> Tx:
    """
    Build a contract-call transaction.

    `data` should already be ABI-encoded by higher-level helpers (e.g., codegen client).
    If `gas_limit` is None, we suggest one from intrinsic + 10% safety.
    """
    payload = _ensure_bytes(data)
    gl = (
        int(gas_limit)
        if gas_limit is not None
        else suggest_gas_limit("call", calldata_len=len(payload))
    )
    return make_tx(
        from_addr=from_addr,
        to=to_addr,
        nonce=int(nonce),
        value=int(value),
        data=payload,
        gas_limit=gl,
        max_fee=int(max_fee),
        chain_id=int(chain_id),
    )


def deploy(
    *,
    from_addr: Address,
    deploy_data: bytes,
    nonce: int,
    gas_limit: Optional[int],
    max_fee: int,
    chain_id: ChainId,
    value: int = 0,
) -> Tx:
    """
    Build a contract-deploy transaction.

    `deploy_data` should be the already-prepared creation payload (e.g., manifest+code bytes).
    `to` is set to `None` to indicate contract creation. If `gas_limit` is None, we suggest one.
    """
    payload = _ensure_bytes(deploy_data)
    gl = (
        int(gas_limit)
        if gas_limit is not None
        else suggest_gas_limit("deploy", calldata_len=len(payload))
    )
    return make_tx(
        from_addr=from_addr,
        to=None,
        nonce=int(nonce),
        value=int(value),
        data=payload,
        gas_limit=gl,
        max_fee=int(max_fee),
        chain_id=int(chain_id),
    )


__all__ = [
    "GasParams",
    "TxKind",
    "intrinsic_gas",
    "suggest_gas_limit",
    "suggest_max_fee",
    "make_tx",
    "transfer",
    "call",
    "deploy",
]
