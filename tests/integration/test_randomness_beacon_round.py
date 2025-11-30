# -*- coding: utf-8 -*-
"""
Integration: randomness beacon round — commit → reveal → (VDF) → beacon out.

This test aims to be robust across devnet configurations. It:
  1) Waits for the commit phase (or next round) and submits a commit(salt,payload).
  2) Waits for the reveal phase and submits the matching reveal.
  3) Optionally attempts to run the reference VDF prover CLI (if available).
  4) Waits for the round to finalize and asserts a beacon output exists.

The suite **skips by default** unless RUN_INTEGRATION_TESTS=1
(see tests/integration/__init__.py). It also skips gracefully if the randomness
RPC surface isn't available or the phase never arrives within the timeout.

Environment (optional unless noted):
  ANIMICA_RPC_URL            — JSON-RPC URL (default: http://127.0.0.1:8545)
  ANIMICA_HTTP_TIMEOUT       — Per-request timeout seconds (default: 5)
  ANIMICA_BEACON_WAIT_SECS   — Max seconds to wait for beacon finalize (default: 300)
  ANIMICA_PHASE_WAIT_SECS    — Max seconds to wait for commit/reveal window (default: 180)
  ANIMICA_TEST_ADDRESS       — Optional bech32/hex address if the RPC requires an address on commit/reveal
  ANIMICA_PROVE_VDF          — If "1", try to run `python -m randomness.cli.prove_vdf` after reveal
  ANIMICA_CLI_PYTHON         — Python executable for CLI path (default: sys.executable)
"""
from __future__ import annotations

import binascii
import json
import os
import subprocess
import sys
import time
import urllib.request
from typing import Any, Dict, Optional, Sequence, Tuple

import pytest

from tests.integration import env  # gate + env helper

# -----------------------------------------------------------------------------


def _timeout_http() -> float:
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
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        rpc_url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=_timeout_http()) as resp:
        raw = resp.read()
    msg = json.loads(raw.decode("utf-8"))
    if "error" in msg and msg["error"]:
        raise AssertionError(f"RPC {method} error: {msg['error']}")
    if "result" not in msg:
        raise AssertionError(f"RPC {method} missing result: {msg}")
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
    raise AssertionError(f"All methods failed ({methods}); last error: {last_exc}")


def _hex(b: bytes) -> str:
    return "0x" + b.hex()


# ------------------------------- Chain helpers --------------------------------


def _get_head_height(rpc_url: str) -> int:
    # Prefer chain.getHead
    for m in ("chain.getHead", "chain_head", "eth_blockNumber"):
        try:
            _, res = _rpc_try(rpc_url, (m,), [])
            if isinstance(res, dict) and "height" in res:
                return int(res["height"])
            if isinstance(res, str) and res.startswith("0x"):
                return int(res, 16)
            if isinstance(res, int):
                return res
        except Exception:
            continue
    # Fallback: latest block
    try:
        _, res = _rpc_try(
            rpc_url, ("chain.getBlockByNumber",), ["latest", False, False]
        )
        if isinstance(res, dict):
            if "number" in res:
                return int(res["number"])
            if "height" in res:
                return int(res["height"])
    except Exception:
        pass
    pytest.skip("Could not determine head height")
    raise AssertionError("unreachable")


def _ensure_next_block(
    rpc_url: str, after_height: int, *, wait_secs: float = 120.0
) -> int:
    deadline = time.time() + wait_secs
    while time.time() < deadline:
        h = _get_head_height(rpc_url)
        if h > after_height:
            return h
        time.sleep(1.0)
    pytest.skip(
        f"No new block observed within timeout; last height={_get_head_height(rpc_url)}"
    )
    raise AssertionError("unreachable")


# ---------------------------- Randomness helpers ------------------------------


def _get_round(rpc_url: str) -> Dict[str, Any]:
    methods = ("rand.getRound", "randomness.getRound", "beacon.getRound")
    try:
        _, res = _rpc_try(rpc_url, methods, [])
        if not isinstance(res, dict):
            raise AssertionError(f"Unexpected getRound shape: {res}")
        return res
    except Exception as exc:
        pytest.skip(f"Randomness getRound not available: {exc}")
        raise AssertionError("unreachable")


def _get_beacon_latest(rpc_url: str) -> Optional[Dict[str, Any]]:
    # Try direct latest
    for m in ("rand.getBeacon", "randomness.getBeacon"):
        try:
            _, res = _rpc_try(rpc_url, (m,), [])
            if isinstance(res, dict):
                return res
        except Exception:
            continue
    # Fallback: history and take tail
    for m in ("rand.getHistory", "randomness.getHistory"):
        try:
            _, res = _rpc_try(rpc_url, (m,), [0, 1_000])
            if isinstance(res, list) and res:
                last = res[-1]
                if isinstance(last, dict):
                    return last
        except Exception:
            continue
    return None


def _round_id(r: Dict[str, Any]) -> int:
    for k in ("id", "round", "round_id", "index", "height"):
        v = r.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    raise AssertionError(f"Round id not found in {r}")


def _phase_of(r: Dict[str, Any]) -> str:
    v = (r.get("phase") or r.get("stage") or r.get("window") or "").lower()
    return str(v)


def _wait_for_phase(
    rpc_url: str, wants_prefix: str, *, timeout_secs: float
) -> Dict[str, Any]:
    deadline = time.time() + timeout_secs
    while time.time() < deadline:
        rd = _get_round(rpc_url)
        ph = _phase_of(rd)
        if ph.startswith(wants_prefix):
            return rd
        time.sleep(1.0)
    pytest.skip(
        f"Phase '{wants_prefix}' not observed within {timeout_secs}s (last={_phase_of(_get_round(rpc_url))})"
    )
    raise AssertionError("unreachable")


def _commit(
    rpc_url: str, salt_hex: str, payload_hex: str, *, address: Optional[str]
) -> Any:
    params_dict = {"salt": salt_hex, "payload": payload_hex}
    if address:
        params_dict["address"] = address
        # Try both 'address' and 'from' spellings
        params_dict_from = {"salt": salt_hex, "payload": payload_hex, "from": address}
    else:
        params_dict_from = None

    methods = ("rand.commit", "randomness.commit", "beacon.commit")
    # Try dict
    try:
        return _rpc_try(rpc_url, methods, [params_dict])[1]
    except Exception:
        pass
    if params_dict_from:
        try:
            return _rpc_try(rpc_url, methods, [params_dict_from])[1]
        except Exception:
            pass
    # Try positional [salt, payload]
    return _rpc_try(rpc_url, methods, [salt_hex, payload_hex])[1]


def _reveal(
    rpc_url: str, salt_hex: str, payload_hex: str, *, address: Optional[str]
) -> Any:
    params_dict = {"salt": salt_hex, "payload": payload_hex}
    if address:
        params_dict["address"] = address
        params_dict_from = {"salt": salt_hex, "payload": payload_hex, "from": address}
    else:
        params_dict_from = None

    methods = ("rand.reveal", "randomness.reveal", "beacon.reveal")
    try:
        return _rpc_try(rpc_url, methods, [params_dict])[1]
    except Exception:
        pass
    if params_dict_from:
        try:
            return _rpc_try(rpc_url, methods, [params_dict_from])[1]
        except Exception:
            pass
    return _rpc_try(rpc_url, methods, [salt_hex, payload_hex])[1]


def _maybe_run_vdf_cli(rpc_url: str) -> None:
    if env("ANIMICA_PROVE_VDF", "0") != "1":
        return
    py = env("ANIMICA_CLI_PYTHON", sys.executable)
    cmd = [py, "-m", "randomness.cli.prove_vdf", "--rpc", rpc_url]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        # Python not found or no module — ignore
        return
    except Exception:
        # Prover may not be packaged/enabled — ignore
        return


# ------------------------------------ Test ------------------------------------


@pytest.mark.timeout(540)
def test_beacon_round_commit_reveal_vdf_finalize():
    rpc_url = env("ANIMICA_RPC_URL", "http://127.0.0.1:8545")
    addr_opt = env("ANIMICA_TEST_ADDRESS", None)

    # Pick deterministic salt/payload so the test is repeatable across runs.
    salt = _hex(b"\x11" * 16)
    payload = _hex(binascii.unhexlify("deadbeef"))

    # Capture current context
    h0 = _get_head_height(rpc_url)
    round0 = _get_round(rpc_url)
    rid0 = _round_id(round0)

    # --- Commit phase ---
    phase_wait = float(env("ANIMICA_PHASE_WAIT_SECS", "180"))
    rd_commit = _wait_for_phase(rpc_url, "commit", timeout_secs=phase_wait)
    _commit(rpc_url, salt, payload, address=addr_opt)

    # Ensure the network is producing blocks
    _ensure_next_block(rpc_url, h0, wait_secs=120.0)

    # --- Reveal phase ---
    rd_reveal = _wait_for_phase(rpc_url, "reveal", timeout_secs=phase_wait)
    _reveal(rpc_url, salt, payload, address=addr_opt)

    # Optionally run the reference VDF prover (devnet helper)
    _maybe_run_vdf_cli(rpc_url)

    # --- Finalize / Beacon out ---
    # Wait for a beacon whose round id is >= rid0 (or rid0+1 depending on design),
    # and that contains a recognizable output/proof/mix.
    deadline = time.time() + float(env("ANIMICA_BEACON_WAIT_SECS", "300"))
    last_beacon: Optional[Dict[str, Any]] = None
    while time.time() < deadline:
        b = _get_beacon_latest(rpc_url)
        if isinstance(b, dict):
            last_beacon = b
            # Round id extraction (accept multiple field names)
            brid = None
            for k in ("round", "round_id", "id", "index", "height"):
                v = b.get(k)
                if isinstance(v, (int, float)):
                    brid = int(v)
                    break
            # Output/proof presence (accept a variety of keys)
            has_out = any(
                k in b
                for k in (
                    "output",
                    "mix",
                    "beacon",
                    "seed",
                    "vdf",
                    "vdf_proof",
                    "light_proof",
                )
            )
            if brid is not None and brid >= rid0 and has_out:
                break
        time.sleep(1.0)

    if not last_beacon:
        pytest.skip(
            "Beacon not available within timeout — devnet may lack VDF worker; skipping."
        )

    # Minimal sanity assertions
    #  - Round id advanced to (or past) the round we committed into
    brid = None
    for k in ("round", "round_id", "id", "index", "height"):
        v = last_beacon.get(k)
        if isinstance(v, (int, float)):
            brid = int(v)
            break
    assert (
        brid is not None and brid >= rid0
    ), f"Beacon round id did not advance (rid0={rid0}, beacon={last_beacon})"

    #  - It should carry some recognizable output field(s)
    assert any(
        k in last_beacon
        for k in ("output", "mix", "seed", "vdf", "vdf_output", "light_proof")
    ), f"Beacon lacks output/proof fields: {last_beacon}"

    # If present, the light proof or VDF section should be well-formed enough
    vdf = last_beacon.get("vdf") or last_beacon.get("vdf_proof")
    if isinstance(vdf, dict):
        # Accept a few common shapes
        assert "proof" in vdf or "y" in vdf or "pi" in vdf or "iterations" in vdf
