"""
Deploy service: submit signed tx payloads and run preflight checks.

This module keeps compatibility with both legacy and current request payloads.
"""

from __future__ import annotations

import asyncio
import binascii
import inspect
import logging
import os
import time
from typing import Any, Dict, Optional

from studio_services.adapters import node_rpc as node_rpc_mod
from studio_services.adapters.vm_compile import (code_hash_bytes,
                                                 compile_package,
                                                 estimate_gas_for_deploy,
                                                 simulate_deploy_locally)
from studio_services.errors import ApiError, BadRequest, ChainMismatch
from studio_services.models.common import ChainId
from studio_services.models.deploy import (DeployRequest, DeployResponse,
                                           PreflightRequest, PreflightResponse)

log = logging.getLogger(__name__)


def _decode_hex(data: str) -> bytes:
    s = (data or "").strip()
    if s.startswith("0x") or s.startswith("0X"):
        s = s[2:]
    try:
        return binascii.unhexlify(s)
    except binascii.Error as e:
        raise BadRequest(f"Invalid hex payload: {e}") from e


def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return asyncio.run(value)
    return value


def _pick_method(obj: Any, *names: str):
    for name in names:
        fn = getattr(obj, name, None)
        if callable(fn):
            return fn
    raise AttributeError(f"None of {names!r} found on {type(obj).__name__}")


def _node_from_env() -> Any:
    """
    Build a node client in a way that works for:
    - real NodeRPC (async client)
    - test monkeypatches replacing `node_rpc.NodeRPC`
    """
    rpc_url = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
    chain_id_env = os.environ.get("CHAIN_ID")
    chain_id = int(chain_id_env) if chain_id_env and chain_id_env.isdigit() else 1

    NodeRPC = getattr(node_rpc_mod, "NodeRPC", None)
    NodeRpcConfig = getattr(node_rpc_mod, "NodeRpcConfig", None)

    if callable(NodeRPC):
        # Test doubles commonly accept kwargs (rpc_url, chain_id)
        try:
            return NodeRPC(rpc_url=rpc_url, chain_id=chain_id)
        except TypeError:
            pass

        # Real adapter expects NodeRpcConfig
        if NodeRpcConfig is not None:
            try:
                return NodeRPC(NodeRpcConfig(url=rpc_url))
            except Exception:
                pass

    # Last resort: module factory
    from_env = getattr(node_rpc_mod, "from_env", None)
    if callable(from_env):
        return from_env()

    raise ApiError("Unable to construct NodeRPC client")


def relay_signed_tx(
    node: Any,
    req: DeployRequest,
    *,
    expected_chain_id: Optional[ChainId] = None,
) -> DeployResponse:
    if expected_chain_id is not None and req.chain_id is not None:
        if int(req.chain_id) != int(expected_chain_id):
            raise ChainMismatch(expected=int(expected_chain_id), got=int(req.chain_id))

    signed_tx_bytes = _decode_hex(req.raw_tx)

    send_fn = _pick_method(node, "send_raw_tx", "send_raw_transaction", "tx_send_raw")
    tx_hash = _maybe_await(send_fn(signed_tx_bytes))
    if not isinstance(tx_hash, str):
        tx_hash = str(tx_hash)

    receipt: Optional[Dict[str, Any]] = None
    if req.await_receipt:
        # 1) Prefer explicit wait helper if available
        wait_fn = getattr(node, "wait_for_receipt", None)
        if callable(wait_fn):
            receipt = _maybe_await(
                wait_fn(
                    tx_hash,
                    timeout_s=max(1.0, req.timeout_ms / 1000.0),
                    poll_interval_s=max(0.01, req.poll_interval_ms / 1000.0),
                )
            )
        else:
            # 2) Poll receipt getter
            get_receipt = _pick_method(node, "get_transaction_receipt", "get_receipt")
            deadline = time.time() + max(1.0, req.timeout_ms / 1000.0)
            poll_s = max(0.01, req.poll_interval_ms / 1000.0)
            while time.time() < deadline:
                r = _maybe_await(get_receipt(tx_hash))
                if r:
                    receipt = r
                    break
                time.sleep(poll_s)

            if receipt is None:
                raise ApiError(
                    f"Timed out waiting for receipt (tx={tx_hash}, timeout_ms={req.timeout_ms})",
                    status_code=504,
                    code="rpc_error",
                )

    contract_address = None
    block_hash = None
    block_number = None
    if isinstance(receipt, dict):
        contract_address = receipt.get("contractAddress") or receipt.get("contract_address")
        block_hash = receipt.get("blockHash") or receipt.get("block_hash")
        bn = receipt.get("blockNumber") or receipt.get("block_number")
        if isinstance(bn, str):
            try:
                block_number = int(bn, 16) if bn.startswith("0x") else int(bn)
            except Exception:
                block_number = None
        elif isinstance(bn, int) and bn > 0:
            block_number = bn

    return DeployResponse(
        tx_hash=tx_hash,
        receipt=receipt,
        contract_address=contract_address,
        block_hash=block_hash,
        block_number=block_number,
    )


def submit_deploy(req: DeployRequest) -> DeployResponse:
    try:
        from studio_services.config import load_config

        cfg = load_config()
        expected_chain = getattr(cfg, "CHAIN_ID", None)
    except Exception:
        expected_chain = None

    node = _node_from_env()
    return relay_signed_tx(node, req, expected_chain_id=expected_chain)


def _decode_optional_hex(data: Optional[str]) -> Optional[bytes]:
    if not data:
        return None
    s = data.strip()
    if s.startswith("0x") or s.startswith("0X"):
        s = s[2:]
    try:
        return bytes.fromhex(s)
    except Exception as e:
        raise BadRequest(f"Invalid code_bytes hex: {e}") from e


def preflight_simulate(req: PreflightRequest) -> PreflightResponse:
    # Require at least one source of code
    if not req.source and not req.code_bytes:
        raise BadRequest("Provide source or code bytes for preflight")

    code_bytes = _decode_optional_hex(req.code_bytes)

    try:
        build = compile_package(
            manifest=req.manifest,
            source=req.source or None,
            code_bytes=code_bytes,
        )
    except Exception as e:
        raise BadRequest(f"Preflight compile failed: {e}") from e

    code_hash_hex: Optional[str] = None
    try:
        ch = code_hash_bytes(build)
        code_hash_hex = "0x" + bytes(ch).hex()
    except Exception:
        code_hash_hex = None

    gas_estimate = None
    try:
        est = estimate_gas_for_deploy(build)
        if isinstance(est, int) and est > 0:
            gas_estimate = est
    except Exception:
        gas_estimate = None

    diagnostics: list[str] = []
    raw_diag = getattr(build, "diagnostics", None)
    if isinstance(raw_diag, list):
        diagnostics = [str(x) for x in raw_diag]

    if req.simulate:
        try:
            simulate_deploy_locally(build, call_data=req.constructor_args or {})
        except Exception as e:
            diagnostics.append(f"simulate warning: {e}")

    return PreflightResponse(
        code_hash=code_hash_hex,
        gas_estimate=gas_estimate,
        abi=getattr(build, "abi", {}) or {},
        diagnostics=diagnostics,
        ok=True,
    )


# Router compatibility aliases
run_preflight = preflight_simulate


__all__ = [
    "relay_signed_tx",
    "submit_deploy",
    "preflight_simulate",
    "run_preflight",
]
