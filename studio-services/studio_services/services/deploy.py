"""
Deploy service: accept signed CBOR tx → relay via node RPC; preflight simulation.

This module contains two primary entrypoints that routers can call:

- relay_signed_tx(...) : submit a signed, CBOR-encoded transaction to the node,
  optionally wait for the receipt, and return a structured response.

- preflight_simulate(...) : compile & sanity-check a contract package locally
  (no state writes), returning code-hash and optional gas estimates.

Both functions delegate IO to adapters under studio_services.adapters and only
perform light validation and orchestration here in the service layer.
"""

from __future__ import annotations

import binascii
import logging
import time
from typing import Any, Dict, Optional

from studio_services.adapters.node_rpc import NodeRPC, from_env
from studio_services.adapters.vm_compile import (code_hash_bytes,
                                                 compile_package,
                                                 estimate_gas_for_deploy,
                                                 simulate_deploy_locally)
from studio_services.errors import ApiError, BadRequest, ChainMismatch
from studio_services.models.common import ChainId
from studio_services.models.deploy import (DeployRequest, DeployResponse,
                                           PreflightRequest, PreflightResponse)

log = logging.getLogger(__name__)


# ---------- Helpers ----------


def _decode_hex(data: str) -> bytes:
    """Decode 0x-prefixed or plain hex into bytes."""
    s = data.strip()
    if s.startswith("0x") or s.startswith("0X"):
        s = s[2:]
    try:
        return binascii.unhexlify(s)
    except binascii.Error as e:  # pragma: no cover - mapped as BadRequest above
        raise BadRequest(f"Invalid hex payload: {e}") from e


def _pick_first(*func_names: str):
    """
    Return a callable attribute accessor that tries several method names on an object.

    Usage:
        call = _pick_first("send_raw_transaction", "send_raw_tx")
        tx_hash = call(node_rpc)(signed_bytes)
    """

    def _resolve(obj):
        for name in func_names:
            if hasattr(obj, name):
                return getattr(obj, name)
        raise AttributeError(f"None of {func_names} found on {type(obj).__name__}")

    return _resolve


# ---------- Public API ----------


def relay_signed_tx(
    node: NodeRPC,
    req: DeployRequest,
    *,
    expected_chain_id: Optional[ChainId] = None,
) -> DeployResponse:
    """
    Submit a signed CBOR transaction to the node; optionally await receipt.

    Parameters
    ----------
    node : NodeRPC
        Configured RPC adapter (HTTP+WS capable).
    req : DeployRequest
        Pydantic model containing the signed tx and options.
    expected_chain_id : Optional[int]
        If provided, reject when req.chain_id is set and mismatches.

    Returns
    -------
    DeployResponse
    """
    # Validate chain id if provided
    if expected_chain_id is not None and req.chain_id is not None:
        if int(req.chain_id) != int(expected_chain_id):
            raise ChainMismatch(
                f"Provided chainId={req.chain_id} does not match server chainId={expected_chain_id}"
            )

    signed_tx_bytes = _decode_hex(req.signed_tx_hex)

    # Submit via NodeRPC (support a couple of method name variants)
    send_fn = _pick_first("send_raw_transaction", "send_raw_tx", "tx_send_raw")(node)
    tx_hash = send_fn(signed_tx_bytes)

    receipt: Optional[Dict[str, Any]] = None
    if req.await_receipt:
        # Prefer explicit wait method if available; else poll get_receipt
        if hasattr(node, "wait_for_receipt"):
            receipt = node.wait_for_receipt(
                tx_hash,
                timeout_s=max(1.0, req.timeout_ms / 1000.0),
                poll_interval_s=max(0.1, req.poll_interval_ms / 1000.0),
            )
        else:
            get_receipt = _pick_first("get_transaction_receipt", "get_receipt")(node)
            deadline = time.time() + max(1.0, req.timeout_ms / 1000.0)
            while time.time() < deadline:
                r = get_receipt(tx_hash)
                if r:
                    receipt = r
                    break
                time.sleep(max(0.1, req.poll_interval_ms / 1000.0))
            if receipt is None:
                raise ApiError(
                    f"Timed out waiting for receipt (tx={tx_hash}, timeout_ms={req.timeout_ms})"
                )

    return DeployResponse(tx_hash=tx_hash, receipt=receipt)


def submit_deploy(req: DeployRequest) -> DeployResponse:
    """Compatibility wrapper used by the router.

    Builds a NodeRPC from the environment/config and delegates to
    :func:`relay_signed_tx`.
    """

    try:
        from studio_services.config import load_config

        cfg = load_config()
        expected_chain = getattr(cfg, "CHAIN_ID", None)
    except Exception:
        expected_chain = None

    node = from_env()
    return relay_signed_tx(node, req, expected_chain_id=expected_chain)


def preflight_simulate(req: PreflightRequest) -> PreflightResponse:
    """
    Offline compile & simulate a contract package locally (no state writes).

    - Compiles the provided (manifest, source/code) using vm_py.
    - Computes a deterministic code hash (content-address-like).
    - Optionally simulates a dry-run deploy to estimate intrinsic gas bounds.

    Notes
    -----
    This does not submit *any* transaction to the network and does not mutate
    node state. It is intended for quick UX feedback before signing.
    """
    # Compile package (raises ValidationError/BadRequest from adapter on errors)
    build = compile_package(
        manifest=req.manifest,
        source=req.source,
        code_bytes=req.code_bytes,
    )

    # Compute canonical code-hash (bytes → 0x-hex)
    ch_bytes = code_hash_bytes(build)
    code_hash_hex = "0x" + ch_bytes.hex()

    # Estimate gas — adapter may return None if unavailable for this artifact
    gas_estimate = estimate_gas_for_deploy(build)

    # Optional dry-run deploy (pure local), returns logs/diagnostics if requested
    sim_result: Optional[Dict[str, Any]] = None
    if req.simulate:
        sim_result = simulate_deploy_locally(
            build, call_data=req.constructor_args or {}
        )

    return PreflightResponse(
        code_hash=code_hash_hex,
        gas_estimate=gas_estimate,
        diagnostics=build.diagnostics or None,
        simulate_result=sim_result,
    )


# Router compatibility aliases
run_preflight = preflight_simulate


__all__ = [
    "relay_signed_tx",
    "submit_deploy",
    "preflight_simulate",
    "run_preflight",
]
