# -*- coding: utf-8 -*-
"""
Integration: deploy the Counter contract, then inc/get.

This test is flexible so it can run against different Animica devnet setups.
It **skips by default** unless RUN_INTEGRATION_TESTS=1 (package gate in
tests/integration/__init__.py). It tries to:

  1) Submit a *signed* CBOR deploy transaction for the Counter example.
  2) Wait for inclusion and extract the created contract address.
  3) Read the counter value (attempt several RPC spellings).
  4) Optionally submit a signed *inc* call transaction (if provided).
  5) Read again and expect the value to increase.

Because nodes may expose different surfaces, we try multiple variants:

  - JSON-RPC method names:
      * tx.sendRawTransaction / sendRawTransaction / eth_sendRawTransaction
      * tx.getTransactionReceipt / getTransactionReceipt / eth_getTransactionReceipt
      * state.call / contract.call / vm.call / eth_call (best-effort)
  - "state.call" payload shapes:
      * {"to": "...", "method": "get", "args": []}
      * {"to": "...", "abi": <ABI>, "func": "get", "args": []}
      * {"to": "...", "data": "0x..."}  (if ABI-encoded data provided via env)

Environment:
  RUN_INTEGRATION_TESTS=1            — enable the test package

  ANIMICA_RPC_URL                    — JSON-RPC URL (default: http://127.0.0.1:8545)
  ANIMICA_HTTP_TIMEOUT               — per-call timeout seconds (default: 5)

  ANIMICA_DEPLOY_CBOR_PATH           — REQUIRED unless a default fixture exists.
                                        Path to a *signed* deploy CBOR tx. Fallbacks:
                                          - studio-services/fixtures/counter/deploy_signed_tx.cbor
  ANIMICA_INC_CBOR_PATH              — OPTIONAL signed CBOR tx that calls "inc()" on the
                                        deployed contract. If not provided, the increment step
                                        is skipped and we only read the initial value.

  ANIMICA_COUNTER_GET_DATA_HEX       — OPTIONAL pre-encoded call data for "get" (0x…).
                                        If set, we will try eth_call/state.call with this data.
  ANIMICA_COUNTER_ABI_JSON           — OPTIONAL path to ABI JSON (for richer call shapes).
                                        Fallbacks to vm_py/examples/counter/manifest.json's "abi".

Notes:
  * This test does NOT sign or build transactions; it consumes signed CBOR files.
  * If your node does not expose a call/simulate method, the test will still pass
    if the deploy succeeds and (if provided) the inc tx is included successfully.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

from tests.integration import env  # package-level gate & env helper

# ------------------------------- RPC helpers ---------------------------------


def _http_timeout() -> float:
    try:
        return float(env("ANIMICA_HTTP_TIMEOUT", "5"))
    except Exception:
        return 5.0


def _rpc_call(
    rpc_url: str,
    method: str,
    params: Optional[Sequence[Any] | Dict[str, Any]] = None,
    *,
    req_id: int = 1,
) -> Any:
    if params is None:
        params = []
    if isinstance(params, dict):
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    else:
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": list(params),
        }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
        raw = resp.read()
    msg = json.loads(raw.decode("utf-8"))
    if "error" in msg and msg["error"]:
        raise AssertionError(f"JSON-RPC error from {method}: {msg['error']}")
    if "result" not in msg:
        raise AssertionError(f"JSON-RPC response missing 'result' for {method}: {msg}")
    return msg["result"]


def _rpc_try(
    rpc_url: str,
    methods: Sequence[str],
    params: Optional[Sequence[Any] | Dict[str, Any]] = None,
) -> Tuple[str, Any]:
    last_exc: Optional[Exception] = None
    for i, m in enumerate(methods, start=1):
        try:
            return m, _rpc_call(rpc_url, m, params, req_id=i)
        except Exception as exc:
            last_exc = exc
            continue
    raise AssertionError(
        f"All RPC spellings failed ({methods}). Last error: {last_exc}"
    )


# ------------------------------ File helpers ---------------------------------


def _load_bytes_from_candidates(name: str, candidates: Sequence[Path]) -> bytes:
    for p in candidates:
        if p.is_file():
            return p.read_bytes()
    pytest.skip(
        f"{name} not provided and no fallback fixture found: {', '.join(map(str, candidates))}"
    )
    raise AssertionError("unreachable")


def _load_signed_deploy_cbor() -> bytes:
    env_path = env("ANIMICA_DEPLOY_CBOR_PATH")
    cands = []
    if env_path:
        cands.append(Path(env_path))
    cands.append(Path("studio-services/fixtures/counter/deploy_signed_tx.cbor"))
    return _load_bytes_from_candidates("signed deploy CBOR", cands)


def _load_signed_inc_cbor_opt() -> Optional[bytes]:
    env_path = env("ANIMICA_INC_CBOR_PATH")
    if not env_path:
        return None
    p = Path(env_path)
    if not p.is_file():
        pytest.skip(f"ANIMICA_INC_CBOR_PATH set but file not found: {env_path}")
    return p.read_bytes()


def _load_counter_abi_opt() -> Optional[dict]:
    abi_path_env = env("ANIMICA_COUNTER_ABI_JSON")
    cands: list[Path] = []
    if abi_path_env:
        cands.append(Path(abi_path_env))
    cands.append(Path("vm_py/examples/counter/manifest.json"))
    for p in cands:
        if p.is_file():
            try:
                js = json.loads(p.read_text())
                # manifest.json usually has {"abi": {...}} or a list; normalize
                if isinstance(js, dict) and "abi" in js:
                    return js["abi"]
                return js
            except Exception:
                continue
    return None


# ---------------------------- Call / decode utils ----------------------------


def _as_hex(b: bytes) -> str:
    return "0x" + b.hex()


def _is_hex_hash(val: Any) -> bool:
    return isinstance(val, str) and val.startswith("0x") and len(val) >= 10


def _parse_address(val: Any) -> Optional[str]:
    if isinstance(val, str) and val.startswith("0x") and len(val) >= 12:
        return val
    return None


def _extract_contract_address_from_receipt(rcpt: Dict[str, Any]) -> Optional[str]:
    # Common fields
    for k in ("contractAddress", "createdAddress", "address"):
        addr = _parse_address(rcpt.get(k))
        if addr:
            return addr
    # Some nodes put it into "result" or "output"
    for k in ("result", "output"):
        v = rcpt.get(k)
        if isinstance(v, dict):
            addr = _parse_address(v.get("contractAddress") or v.get("address"))
            if addr:
                return addr
    return None


def _call_get_best_effort(rpc_url: str, addr: str) -> Optional[int]:
    """
    Try multiple 'state.call' shapes and spellings to retrieve the current counter value.
    Returns the integer value on success, or None if the node doesn't support a call surface.
    """
    # If user provides raw data for "get", try eth_call-like first
    data_hex = env("ANIMICA_COUNTER_GET_DATA_HEX")
    if data_hex and data_hex.startswith("0x"):
        for method in ("eth_call", "state.call", "vm.call", "contract.call"):
            try:
                # Try object form (eth_call): {"to": addr, "data": "0x.."}
                _, res = _rpc_try(rpc_url, (method,), [{"to": addr, "data": data_hex}])
                val = _decode_get_result(res)
                if val is not None:
                    return val
            except Exception:
                continue

    # Try friendly method-name shapes
    for payload in (
        {"to": addr, "method": "get", "args": []},
        {"to": addr, "func": "get", "args": []},
    ):
        for method in ("state.call", "contract.call", "vm.call"):
            try:
                _, res = _rpc_try(rpc_url, (method,), [payload])
                val = _decode_get_result(res)
                if val is not None:
                    return val
            except Exception:
                continue

    # If ABI is available, include it in the payload; some nodes accept {"abi": …}
    abi = _load_counter_abi_opt()
    if abi:
        payload = {"to": addr, "abi": abi, "func": "get", "args": []}
        for method in ("state.call", "contract.call", "vm.call"):
            try:
                _, res = _rpc_try(rpc_url, (method,), [payload])
                val = _decode_get_result(res)
                if val is not None:
                    return val
            except Exception:
                continue

    return None


def _decode_get_result(res: Any) -> Optional[int]:
    """
    Accept a few output shapes for a read call:
      - {"ok": true, "return": 1} / {"result": {"return": 1}}
      - {"value": 1} / {"return": "0x01"} / {"return": "1"}
      - raw scalar 1
    """
    if isinstance(res, int):
        return res
    if isinstance(res, str):
        try:
            return int(res, 0)
        except Exception:
            return None
    if isinstance(res, dict):
        # Unwrap nested result
        if "result" in res and isinstance(res["result"], dict):
            res = res["result"]
        for k in ("return", "value", "result"):
            v = res.get(k)
            if v is None:
                continue
            if isinstance(v, int):
                return v
            if isinstance(v, str):
                try:
                    return int(v, 0)
                except Exception:
                    return None
    return None


# ----------------------------------- Test ------------------------------------


@pytest.mark.timeout(300)
def test_deploy_counter_then_inc_get():
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")

    # 1) Submit deploy tx
    deploy_cbor = _load_signed_deploy_cbor()
    raw_hex = _as_hex(deploy_cbor)
    send_method, send_res = _rpc_try(
        rpc_url,
        methods=(
            "tx.sendRawTransaction",
            "sendRawTransaction",
            "eth_sendRawTransaction",
        ),
        params=[raw_hex],
    )
    # Extract tx hash
    tx_hash: Optional[str] = None
    if _is_hex_hash(send_res):
        tx_hash = send_res  # type: ignore[assignment]
    elif isinstance(send_res, dict):
        for k in ("txHash", "hash", "transactionHash"):
            v = send_res.get(k)
            if _is_hex_hash(v):
                tx_hash = v
                break
    assert tx_hash, f"{send_method} returned unexpected shape: {send_res!r}"

    # Wait for inclusion
    include_timeout = 180.0
    poll = 1.0
    deadline = time.time() + include_timeout
    receipt: Optional[Dict[str, Any]] = None
    while time.time() < deadline:
        try:
            _, rcpt = _rpc_try(
                rpc_url,
                methods=(
                    "tx.getTransactionReceipt",
                    "getTransactionReceipt",
                    "eth_getTransactionReceipt",
                ),
                params=[tx_hash],
            )
            if isinstance(rcpt, dict) and rcpt.get("blockHash"):
                receipt = rcpt
                break
        except Exception:
            pass
        time.sleep(poll)
    assert (
        receipt is not None
    ), f"Deploy tx {tx_hash} not included within {include_timeout:.1f}s"

    # Extract created contract address
    addr = _extract_contract_address_from_receipt(receipt)
    assert (
        addr
    ), f"Could not determine deployed contract address from receipt: {receipt!r}"

    # 2) Read initial value (best-effort; if not supported we still pass the deploy step)
    initial = _call_get_best_effort(rpc_url, addr)
    if initial is not None:
        assert (
            isinstance(initial, int) and initial >= 0
        ), f"Unexpected initial counter value: {initial}"

    # 3) Optionally submit an 'inc' tx if provided
    inc_cbor = _load_signed_inc_cbor_opt()
    if inc_cbor:
        inc_hex = _as_hex(inc_cbor)
        _, inc_res = _rpc_try(
            rpc_url,
            methods=(
                "tx.sendRawTransaction",
                "sendRawTransaction",
                "eth_sendRawTransaction",
            ),
            params=[inc_hex],
        )
        inc_hash: Optional[str] = None
        if _is_hex_hash(inc_res):
            inc_hash = inc_res  # type: ignore[assignment]
        elif isinstance(inc_res, dict):
            for k in ("txHash", "hash", "transactionHash"):
                v = inc_res.get(k)
                if _is_hex_hash(v):
                    inc_hash = v
                    break
        assert inc_hash, f"send inc returned unexpected shape: {inc_res!r}"

        # Wait for inclusion
        deadline2 = time.time() + 120.0
        ok = False
        while time.time() < deadline2:
            try:
                _, rcpt2 = _rpc_try(
                    rpc_url,
                    methods=(
                        "tx.getTransactionReceipt",
                        "getTransactionReceipt",
                        "eth_getTransactionReceipt",
                    ),
                    params=[inc_hash],
                )
                if isinstance(rcpt2, dict) and rcpt2.get("blockHash"):
                    status = rcpt2.get("status")
                    if status in (True, 1, "0x1", "success", "ok", "1"):
                        ok = True
                    else:
                        # permit hex or str ints
                        try:
                            ok = int(status, 0) != 0  # type: ignore[arg-type]
                        except Exception:
                            ok = False
                    break
            except Exception:
                pass
            time.sleep(1.0)
        assert ok, f"inc() transaction {inc_hash} failed or not included"

        # 4) Read again and expect value increased (if call is available)
        after = _call_get_best_effort(rpc_url, addr)
        if initial is not None and after is not None:
            assert (
                after >= initial + 1
            ), f"Counter did not increase as expected: initial={initial}, after_inc={after}"
        else:
            # No call surface available — we still consider deploy+inc successful.
            pytest.skip(
                "Node does not expose a contract call surface; deploy+inc succeeded, skipping value check"
            )
    else:
        # No inc tx provided; assert we at least could read the initial value if the node supports it.
        if initial is None:
            pytest.skip(
                "Deployed successfully but node does not expose a call surface and no inc tx provided"
            )
