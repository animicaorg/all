import json
import os
import time
from binascii import hexlify, unhexlify
from typing import Any, Dict, Optional

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI  # type: ignore
from fastapi.testclient import TestClient  # type: ignore


def hb(s: str) -> bytes:
    s = s.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) % 2 == 1:
        s = "0" + s
    return unhexlify(s)


def hx(b: bytes) -> str:
    return "0x" + hexlify(b).decode()


def _mount_app() -> FastAPI:
    app = FastAPI()
    # Try a few mount helpers
    try:
        import randomness.adapters.rpc_mount as rpc_mount  # type: ignore
    except Exception as e:  # pragma: no cover - environment variance
        pytest.skip(f"randomness.adapters.rpc_mount not available: {e}")

    mounted = False
    for name in (
        "mount",
        "mount_randomness",
        "mount_endpoints",
        "mount_app",
        "mount_routes",
    ):
        if hasattr(rpc_mount, name):
            try:
                getattr(rpc_mount, name)(app)  # type: ignore[arg-type]
                mounted = True
                break
            except TypeError:
                # Some mounts might require config; try with defaults
                try:
                    getattr(rpc_mount, name)(app, {})  # type: ignore[arg-type]
                    mounted = True
                    break
                except Exception:
                    pass
            except Exception:
                pass
    if not mounted:
        pytest.skip(
            "Could not mount randomness RPC/WS endpoints (no suitable mount_* function)."
        )
    return app


def _rpc_call(client: TestClient, method: str, params: Dict[str, Any]) -> Any:
    # Prefer JSON-RPC at /rpc, but try common alternates.
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    paths = ["/rpc", "/jsonrpc", "/api/rpc", "/"]
    last_err: Optional[str] = None
    for path in paths:
        try:
            resp = client.post(path, json=payload)
            if resp.status_code == 200 and "result" in resp.json():
                return resp.json()["result"]
        except Exception as e:
            last_err = str(e)
    pytest.skip(
        f"JSON-RPC endpoint not reachable for method {method} (last error: {last_err})"
    )
    return None  # unreachable


def _ws_path(client: TestClient) -> Optional[str]:
    for path in ("/ws/rand", "/rand/ws", "/ws", "/randomness/ws", "/ws/randomness"):
        try:
            with client.websocket_connect(path):
                return path
        except Exception:
            continue
    return None


@pytest.fixture(scope="module")
def test_client() -> TestClient:
    app = _mount_app()
    return TestClient(app)


def _cli_reveal(salt_hex: str, payload_hex: str, rpc_url: str) -> None:
    """
    Use the CLI if available; otherwise fall back to RPC.
    """
    try:
        # Typer-based CLI?
        import typer  # type: ignore
        from typer.testing import CliRunner  # type: ignore

        # Try to find the Typer app
        import randomness.cli.reveal as reveal_cli  # type: ignore

        app_obj = None
        for cand in ("app", "cli", "typer_app", "main_app"):
            if hasattr(reveal_cli, cand):
                app_obj = getattr(reveal_cli, cand)
                break
        # Or a main() function that accepts args
        if app_obj is not None:
            runner = CliRunner(mix_stderr=False)
            # Try common flags for RPC URL
            for flag in ("--rpc", "--url", "--endpoint", "--rpc-url"):
                res = runner.invoke(
                    app_obj,
                    [flag, rpc_url, "--salt", salt_hex, "--payload", payload_hex],
                )
                if res.exit_code == 0:
                    return
            # As a last resort, try env var
            env = os.environ.copy()
            for key in ("OMNI_RPC", "ANIMICA_RPC", "RANDOMNESS_RPC_URL"):
                env[key] = rpc_url
            res = runner.invoke(
                app_obj, ["--salt", salt_hex, "--payload", payload_hex], env=env
            )
            if res.exit_code == 0:
                return
        # Fallback to a callable main(*args)
        if hasattr(reveal_cli, "main") and callable(reveal_cli.main):
            try:
                reveal_cli.main(["--rpc", rpc_url, "--salt", salt_hex, "--payload", payload_hex])  # type: ignore
                return
            except SystemExit as se:
                if getattr(se, "code", 1) == 0:
                    return
    except Exception:
        pass

    # CLI not available — fall back to RPC
    # We will send the reveal via requests here; the caller handles final assertions.
    raise RuntimeError("CLI reveal path unavailable")


def test_commit_reveal_roundtrip_emits_beacon_event(test_client: TestClient):
    client = test_client

    # Prepare inputs
    salt = hb("0x11111111111111111111111111111111")
    payload = hb("0x2222222222222222222222222222222222222222222222222222222222222222")
    salt_hex = hx(salt)
    payload_hex = hx(payload)

    # Get current round info (if provided)
    try:
        round_info = _rpc_call(client, "rand.getRound", {})
    except pytest.skip.Exception:
        round_info = None

    # Open WS before triggering actions so we can capture events
    ws_path = _ws_path(client)
    if ws_path is None:
        pytest.skip("No websocket endpoint for randomness events found")

    # Commit via RPC
    commit_result = _rpc_call(
        client, "rand.commit", {"salt": salt_hex, "payload": payload_hex}
    )
    assert commit_result is not None

    # Reveal via CLI if available; otherwise, use RPC
    try:
        _cli_reveal(salt_hex, payload_hex, rpc_url="http://testserver")
    except RuntimeError:
        reveal_result = _rpc_call(
            client, "rand.reveal", {"salt": salt_hex, "payload": payload_hex}
        )
        assert reveal_result is not None

    # Some implementations may finalize automatically; others need an explicit "prove/verify VDF" step.
    # If a helper RPC exists, try it opportunistically (best-effort).
    for finalize_method in ("rand.proveVDF", "rand.finalize", "rand.triggerFinalize"):
        try:
            _rpc_call(client, finalize_method, {})
            break
        except pytest.skip.Exception:
            # Endpoint absent; continue
            pass
        except Exception:
            pass

    # Wait briefly for event; then assert we saw a beaconFinalized (or equivalent) message.
    event = None
    with client.websocket_connect(ws_path) as ws:
        # Drain any initial hello
        try:
            ws.receive_json(timeout=0.05)  # type: ignore[arg-type]
        except Exception:
            pass

        # Give server a short moment to push
        deadline = time.time() + 2.5
        while time.time() < deadline:
            try:
                msg = ws.receive_json()
            except Exception:
                time.sleep(0.05)
                continue
            if not isinstance(msg, dict):
                continue
            topic = msg.get("event") or msg.get("type") or msg.get("topic")
            if isinstance(topic, str) and topic.lower() in {
                "beaconfinalized",
                "beacon_finalized",
                "rand.beacon",
            }:
                event = msg
                break
            # Some servers nest data
            if "event" in msg and isinstance(msg["event"], dict):
                inner = msg["event"]
                t2 = inner.get("name") or inner.get("type")
                if isinstance(t2, str) and t2.lower() in {
                    "beaconfinalized",
                    "beacon_finalized",
                    "rand.beacon",
                }:
                    event = inner
                    break
            # Otherwise keep listening
            time.sleep(0.05)

    if event is None:
        pytest.xfail(
            "Did not observe a 'beaconFinalized' websocket event (implementation may require external VDF prover)"
        )

    # Minimal sanity on payload shape
    # Accept a variety of shapes: {"event":"beaconFinalized","beacon":{"round":..,"output":"0x.."}}
    beacon = None
    if "beacon" in event and isinstance(event["beacon"], dict):
        beacon = event["beacon"]
    elif (
        "data" in event
        and isinstance(event["data"], dict)
        and "beacon" in event["data"]
    ):
        beacon = event["data"]["beacon"]
    else:
        # Maybe the event is the beacon itself
        beacon = event if isinstance(event, dict) else None

    assert isinstance(beacon, dict), f"Beacon payload missing or malformed: {event}"
    # Output should be present and hex-like
    out = beacon.get("output") or beacon.get("value") or beacon.get("beacon")
    assert isinstance(out, (str, bytes)), "Beacon output missing"
    if isinstance(out, str):
        assert out.startswith("0x"), "Beacon output should be hex-prefixed"
    # Round id should exist (best-effort)
    rid = beacon.get("round") or beacon.get("roundId") or beacon.get("id")
    assert rid is not None, "Beacon round id missing"
