from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Optional

from animica.config import load_network_config


@dataclass
class RpcAdapter:
    url: str
    timeout: float = 30.0

    def __post_init__(self) -> None:
        self._client: Any = None
        try:
            from omni_sdk.rpc.http import RpcClient

            self._client = RpcClient(self.url, timeout=self.timeout)
        except Exception:
            self._client = None

    def request(self, method: str, params: Any | None = None) -> Any:
        payload_params = [] if params is None else params

        if self._client is not None:
            request_fn = getattr(self._client, "request", None) or getattr(self._client, "call", None)
            if callable(request_fn):
                return request_fn(method, payload_params)

        try:
            import requests
        except Exception as exc:  # pragma: no cover - dependency gate
            raise RuntimeError(
                "RPC client unavailable: install requests or omni_sdk.rpc.http"
            ) from exc

        req = {
            "jsonrpc": "2.0",
            "id": int(time.time() * 1000) % 1_000_000,
            "method": method,
            "params": payload_params,
        }
        response = requests.post(self.url, json=req, timeout=self.timeout)
        response.raise_for_status()
        body = response.json()
        if body.get("error"):
            err = body["error"]
            message = err.get("message") if isinstance(err, dict) else str(err)
            code = err.get("code") if isinstance(err, dict) else "unknown"
            raise RuntimeError(f"rpc {method} failed ({code}): {message}")
        return body.get("result")


def resolve_rpc_url(override: Optional[str]) -> str:
    if override and override.strip():
        return override.strip()

    for key in ("ANIMICA_RPC_URL", "OMNI_RPC_URL", "OMNI_SDK_RPC_URL"):
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()

    return load_network_config().rpc_url


def resolve_network_hint() -> Optional[str]:
    for key in ("ANIMICA_NETWORK",):
        value = os.environ.get(key)
        if value and value.strip():
            return value.strip()
    try:
        cfg = load_network_config()
        if cfg.name:
            return cfg.name
    except Exception:
        pass
    return None


def _coerce_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text, 16) if text.startswith(("0x", "0X")) else int(text)
        except Exception:
            return None
    try:
        return int(value)
    except Exception:
        return None


def resolve_chain_id(rpc: RpcAdapter, override: Optional[int]) -> int:
    if override is not None:
        return int(override)

    env_value = os.environ.get("ANIMICA_CHAIN_ID") or os.environ.get("OMNI_CHAIN_ID")
    if env_value:
        parsed = _coerce_int(env_value)
        if parsed is not None:
            return parsed

    for method in (
        "chain.getChainId",
        "chain_id",
        "net_version",
        "eth_chainId",
        "chainId",
    ):
        try:
            result = rpc.request(method, [])
        except Exception:
            continue
        parsed = _coerce_int(result)
        if parsed is not None:
            return parsed

    try:
        return int(load_network_config().chain_id)
    except Exception:
        return 1


def wait_for_receipt(
    rpc: RpcAdapter,
    tx_hash: str,
    *,
    timeout_s: float = 120.0,
    poll_interval_s: float = 0.5,
) -> dict[str, Any]:
    # Preferred SDK helper.
    try:
        from omni_sdk.tx.send import wait_for_receipt as sdk_wait_for_receipt

        receipt = sdk_wait_for_receipt(
            rpc, tx_hash, timeout_s=timeout_s, poll_interval_s=poll_interval_s
        )
        if isinstance(receipt, dict):
            return receipt
    except Exception:
        pass

    deadline = time.monotonic() + float(timeout_s)
    while True:
        for method in ("tx.getTransactionReceipt", "tx.getReceipt", "tx.getInstantReceipt"):
            try:
                result = rpc.request(method, [tx_hash])
            except Exception:
                continue
            if isinstance(result, dict) and result:
                return result

        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"timeout waiting for receipt for {tx_hash} after {timeout_s:.1f}s"
            )
        time.sleep(max(0.1, poll_interval_s))


def get_tx_status(rpc: RpcAdapter, tx_hash: str) -> Optional[dict[str, Any]]:
    for method in ("tx.getStatus", "tx.getTransactionStatus"):
        try:
            result = rpc.request(method, [tx_hash])
        except Exception:
            continue
        if isinstance(result, dict):
            return result
    return None


def pretty_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False)
