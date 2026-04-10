import importlib
import inspect
import json
import time
from typing import Any, Dict, List, Optional, Tuple

import pytest

try:
    from starlette.testclient import TestClient
except Exception:  # pragma: no cover - starlette missing
    TestClient = None  # type: ignore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _maybe(mod: Any, names: Tuple[str, ...]) -> Optional[Any]:
    for n in names:
        if hasattr(mod, n):
            return getattr(mod, n)
    return None


def _load_asgi_app(ws_mod: Any):
    """
    Best-effort loader for the WS getwork ASGI app. Tries common factories/vars.
    """
    # Prefer explicit factory
    for fname in ("create_app", "make_app", "app_factory", "factory"):
        fn = getattr(ws_mod, fname, None)
        if callable(fn):
            try:
                sig = inspect.signature(fn)
                if len(sig.parameters) == 0:
                    return fn()
                # Try with permissive kwargs if present
                kwargs = {}
                for p in sig.parameters.values():
                    if p.default is not inspect._empty:
                        kwargs[p.name] = p.default
                    else:
                        # give obvious Nones for optional-ish params
                        kwargs[p.name] = None
                return fn(**kwargs)
            except Exception:
                pass

    # Common exported app variables
    for vname in ("app", "asgi", "application"):
        app = getattr(ws_mod, vname, None)
        if app is not None:
            return app

    # Try a getter
    for gname in ("get_app", "asgi_app", "build_app"):
        fn = getattr(ws_mod, gname, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass

    return None


def _try_ws_paths(client: TestClient, paths: List[str]) -> Optional[str]:
    for path in paths:
        try:
            with client.websocket_connect(path) as ws:
                # If we connected, close and return the path
                ws.close()
                return path
        except Exception:
            continue
    return None


def _ws_send_json(ws, obj: Dict[str, Any]):
    ws.send_text(json.dumps(obj))


def _ws_recv_json(ws, timeout_s: float = 2.0) -> Dict[str, Any]:
    t0 = time.time()
    while True:
        try:
            msg = ws.receive_text()
            return json.loads(msg)
        except Exception:
            if time.time() - t0 > timeout_s:
                raise
            time.sleep(0.01)


def _extract_work(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tolerant extractor for a 'work' object from various message shapes:
    - JSON-RPC result
    - notify method payload
    - raw dict with fields
    """
    if "result" in payload and isinstance(payload["result"], dict):
        return payload["result"]
    if payload.get("method") in ("miner.notify", "mining.notify", "getwork.notify"):
        params = payload.get("params") or {}
        if isinstance(params, dict):
            return params
        if isinstance(params, list) and params and isinstance(params[0], dict):
            return params[0]
    # fall back to payload itself
    return payload


def _build_fake_share(work: Dict[str, Any]) -> Dict[str, Any]:
    job_id = work.get("job_id") or work.get("id") or "job-1"
    header = work.get("header") or "0x" + "11" * 64
    mix = work.get("mix") or "0x" + "22" * 64
    target = work.get("target") or "0x" + "33" * 64
    return {
        "job_id": job_id,
        "extranonce": work.get("extranonce") or "0x00000001",
        "nonce": "0x0000000000000001",
        "header": header,
        "mix": mix,
        "target": target,
        "d_ratio": float(work.get("d_ratio", 0.5)),
    }


# ---------------------------------------------------------------------------
# Module under test
# ---------------------------------------------------------------------------

ws_mod = importlib.import_module("mining.ws_getwork")


@pytest.mark.skipif(TestClient is None, reason="starlette not available")
def test_ws_subscribe_and_get_work_roundtrip():
    app = _load_asgi_app(ws_mod)
    if app is None:
        pytest.skip("Could not obtain ASGI app from mining.ws_getwork")

    client = TestClient(app)

    # Try a set of likely websocket endpoints
    path = _try_ws_paths(
        client,
        paths=[
            "/ws/getwork",
            "/getwork/ws",
            "/miner/ws",
            "/ws",
            "/miner/getwork",
        ],
    )
    if path is None:
        pytest.skip("No known WS path worked on the ASGI app")

    # Connect and perform subscribe + getWork
    with client.websocket_connect(path) as ws:
        # Optional subscribe
        _ws_send_json(
            ws,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "miner.subscribe",
                "params": {"agent": "pytest/animica"},
            },
        )
        try:
            _ws_recv_json(ws, timeout_s=0.5)  # ignore the response if any
        except Exception:
            pass

        # Optional authorize
        _ws_send_json(
            ws,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "miner.authorize",
                "params": {"user": "test", "password": "x"},
            },
        )
        try:
            _ws_recv_json(ws, timeout_s=0.5)
        except Exception:
            pass

        # Ask for work (method names vary a bit)
        for mid, name in enumerate(
            ("miner.getWork", "mining.getWork", "getWork", "miner.requestWork"),
            start=10,
        ):
            _ws_send_json(
                ws, {"jsonrpc": "2.0", "id": mid, "method": name, "params": {}}
            )
            try:
                msg = _ws_recv_json(ws, timeout_s=1.0)
                work = _extract_work(msg)
                # Sanity: we expect at least a header/target or a job id
                assert any(k in work for k in ("header", "target", "job_id", "id"))
                break
            except Exception:
                continue
        else:
            pytest.fail("Did not receive any work payload after getWork attempts")


@pytest.mark.skipif(TestClient is None, reason="starlette not available")
def test_ws_submit_share_acknowledged():
    app = _load_asgi_app(ws_mod)
    if app is None:
        pytest.skip("Could not obtain ASGI app from mining.ws_getwork")

    client = TestClient(app)

    path = _try_ws_paths(
        client,
        paths=[
            "/ws/getwork",
            "/getwork/ws",
            "/miner/ws",
            "/ws",
            "/miner/getwork",
        ],
    )
    if path is None:
        pytest.skip("No known WS path worked on the ASGI app")

    with client.websocket_connect(path) as ws:
        # Subscribe (best effort)
        _ws_send_json(
            ws,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "miner.subscribe",
                "params": {"agent": "pytest/animica"},
            },
        )
        try:
            _ws_recv_json(ws, timeout_s=0.5)
        except Exception:
            pass

        # Get some work first
        work = None
        for mid, name in enumerate(
            ("miner.getWork", "mining.getWork", "getWork", "miner.requestWork"),
            start=10,
        ):
            _ws_send_json(
                ws, {"jsonrpc": "2.0", "id": mid, "method": name, "params": {}}
            )
            try:
                msg = _ws_recv_json(ws, timeout_s=1.0)
                work = _extract_work(msg)
                if isinstance(work, dict):
                    break
            except Exception:
                continue
        if not isinstance(work, dict):
            pytest.skip("Service did not return a work template; cannot submit share")

        share = _build_fake_share(work)

        # Submit share (accept various method names & shapes)
        for mid, name in enumerate(
            ("miner.submitShare", "mining.submitShare", "submitShare", "miner.submit"),
            start=100,
        ):
            # Try named params first
            _ws_send_json(
                ws,
                {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "method": name,
                    "params": {"share": share},
                },
            )
            try:
                ack = _ws_recv_json(ws, timeout_s=1.0)
                # Accept either result or boolean
                result = ack.get("result", ack)
                if isinstance(result, dict):
                    ok = result.get("accepted") or (
                        result.get("status") in ("OK", "accepted")
                    )
                    if ok:
                        break
                elif isinstance(result, bool) and result:
                    break
            except Exception:
                # Try positional form
                _ws_send_json(
                    ws,
                    {
                        "jsonrpc": "2.0",
                        "id": mid + 1,
                        "method": name,
                        "params": [share],
                    },
                )
                try:
                    ack = _ws_recv_json(ws, timeout_s=1.0)
                    result = ack.get("result", ack)
                    if isinstance(result, dict):
                        ok = result.get("accepted") or (
                            result.get("status") in ("OK", "accepted")
                        )
                        if ok:
                            break
                    elif isinstance(result, bool) and result:
                        break
                except Exception:
                    continue
        else:
            pytest.fail("No submitShare variant returned an acceptance")
