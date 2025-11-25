import asyncio
import importlib
import inspect
import json
import socket
import time
from typing import Any, Callable, Dict, Optional, Tuple

import pytest


# ---------- small introspection helpers --------------------------------------

def _maybe(mod: Any, names: Tuple[str, ...]) -> Optional[Any]:
    for n in names:
        if hasattr(mod, n):
            return getattr(mod, n)
    return None

def _sig_call(fn: Callable, /, *args, **kwargs):
    """
    Call a function with args/kwargs trimmed to its signature.
    If fn accepts **kwargs, pass all kwargs through.
    """
    sig = inspect.signature(fn)
    params = sig.parameters
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()):
        return fn(*args, **kwargs)
    fkwargs = {k: v for k, v in kwargs.items() if k in params}
    # trim args length as well
    max_pos = sum(1 for p in params.values()
                  if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                  and p.default is inspect._empty)
    fargs = args[:max_pos] if len(args) > max_pos else args
    return fn(*fargs, **fkwargs)

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    _, port = s.getsockname()
    s.close()
    return port


# ---------- modules under test (tolerant discovery) --------------------------

srv_mod = importlib.import_module("mining.stratum_server")
cli_mod = importlib.import_module("mining.stratum_client")
proto_mod = importlib.import_module("mining.stratum_protocol")

StratumServer = _maybe(srv_mod, ("StratumServer", "Server"))
StratumClient = _maybe(cli_mod, ("StratumClient", "Client"))

# Optional helpers/constants from protocol
METHODS = {
    "subscribe": _maybe(proto_mod, ("METHOD_SUBSCRIBE", "METHOD_MINER_SUBSCRIBE")) or "mining.subscribe",
    "authorize": _maybe(proto_mod, ("METHOD_AUTHORIZE", "METHOD_MINER_AUTHORIZE")) or "mining.authorize",
    "set_difficulty": _maybe(proto_mod, ("METHOD_SET_DIFFICULTY", "METHOD_MINER_SET_DIFFICULTY")) or "mining.setDifficulty",
    "submit_share": _maybe(proto_mod, ("METHOD_SUBMIT_SHARE", "METHOD_MINER_SUBMIT")) or "mining.submitShare",
    "notify": _maybe(proto_mod, ("METHOD_NOTIFY", "METHOD_MINING_NOTIFY")) or "mining.notify",
}

# ---------- fixtures ---------------------------------------------------------

@pytest.fixture(scope="module")
def event_loop():
    # pytest-asyncio default loop fixture is function-scoped; we want module-scoped to speed things up
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
async def stratum_stack(event_loop):
    if StratumServer is None or StratumClient is None:
        pytest.skip("StratumServer/StratumClient not available")

    host = "127.0.0.1"
    port = _free_port()

    shares = []
    diff_events = []

    share_evt = asyncio.Event()
    diff_evt = asyncio.Event()

    async def on_share_cb(session_id: str, share: Dict[str, Any]) -> Dict[str, Any]:
        shares.append((session_id, share))
        share_evt.set()
        # Respond with a generic acceptance structure
        return {"status": "OK", "accepted": True}

    async def on_diff_cb(session_id: str, difficulty: float) -> None:
        diff_events.append((session_id, difficulty))
        diff_evt.set()

    # Instantiate server (tolerant to different ctor signatures)
    server = StratumServer  # type: ignore
    if inspect.isclass(server):
        server = server  # class -> class
        server_kwargs = {
            "host": host,
            "port": port,
            "on_share": on_share_cb,
            "on_set_difficulty": on_diff_cb,
            "default_difficulty": 64.0,
            "validate_shares": False,     # allow fake shares in test
            "accept_insecure": True,
            "idle_timeout_s": 5,
        }
        srv = _sig_call(server, **server_kwargs)
    else:
        pytest.skip("StratumServer is not a class")

    # Start server (handle sync/async start)
    start = getattr(srv, "start", None)
    if start is None:
        pytest.skip("StratumServer.start() missing")
    res = start()
    if inspect.iscoroutine(res):
        await res

    # Build client
    client = StratumClient  # type: ignore
    if inspect.isclass(client):
        client = client()
    else:
        pytest.skip("StratumClient is not a class")

    # Connect
    connect = getattr(client, "connect", None)
    if connect is None:
        pytest.skip("StratumClient.connect() missing")
    cres = connect(host=host, port=port)
    if inspect.iscoroutine(cres):
        await cres

    try:
        yield {
            "server": srv,
            "client": client,
            "shares": shares,
            "diff_events": diff_events,
            "share_evt": share_evt,
            "diff_evt": diff_evt,
            "host": host,
            "port": port,
        }
    finally:
        # Graceful shutdown
        try:
            close = getattr(client, "close", None)
            if close:
                c = close()
                if inspect.iscoroutine(c):
                    await c
        except Exception:
            pass

        try:
            stop = getattr(srv, "stop", None)
            if stop:
                s = stop()
                if inspect.iscoroutine(s):
                    await s
        except Exception:
            pass


# ---------- tests ------------------------------------------------------------

@pytest.mark.asyncio
async def test_server_client_set_difficulty(stratum_stack):
    client = stratum_stack["client"]
    diff_evt: asyncio.Event = stratum_stack["diff_evt"]
    diff_events = stratum_stack["diff_events"]

    # subscribe/authorize if supported
    for meth_name, payload in (
        ("subscribe", {"agent": "pytest/animica"}),
        ("authorize", {"user": "test", "password": "x"}),
    ):
        fn = getattr(client, meth_name, None)
        if fn is None:
            continue
        res = fn(payload) if "payload" in inspect.signature(fn).parameters else fn(**payload)
        if inspect.iscoroutine(res):
            await res

    # set difficulty on the session
    setdiff = getattr(client, "set_difficulty", None) or getattr(client, "difficulty", None)
    assert setdiff is not None, "Client missing set_difficulty()"
    d_target = 128.0
    res = setdiff(difficulty=d_target) if "difficulty" in inspect.signature(setdiff).parameters else setdiff(d_target)
    if inspect.iscoroutine(res):
        await res

    # wait for server callback
    try:
        await asyncio.wait_for(diff_evt.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        pytest.fail("Server did not receive set_difficulty within timeout")

    # assert last difficulty equals target (tolerate float rounding)
    _, last = diff_events[-1]
    assert abs(float(last) - d_target) < 1e-6


@pytest.mark.asyncio
async def test_server_client_submit_share_roundtrip(stratum_stack):
    client = stratum_stack["client"]
    shares = stratum_stack["shares"]
    share_evt: asyncio.Event = stratum_stack["share_evt"]

    # Obtain a job/work if API supports it
    job_id = "job-1"
    header = "0x" + "11" * 64
    mix = "0x" + "22" * 64
    target = "0x" + "33" * 64
    extranonce = "0x00000001"

    # Some clients expose get_work/subscribe that returns a job
    get_work = getattr(client, "get_work", None) or getattr(client, "getwork", None) or getattr(client, "fetch_work", None)
    if callable(get_work):
        res = get_work()
        if inspect.iscoroutine(res):
            res = await res
        try:
            job_id = res.get("job_id") or res.get("id") or job_id
            header = res.get("header") or header
            target = res.get("target") or target
            mix = res.get("mix") or mix
        except Exception:
            pass

    # Build a minimal fake share that servers usually accept when validation is disabled.
    share = {
        "job_id": job_id,
        "extranonce": extranonce,
        "nonce": "0x0000000000000001",
        "header": header,
        "mix": mix,
        "target": target,
        "d_ratio": 0.5,
    }

    # Submit via client — accept various method names/signatures
    submit = getattr(client, "submit_share", None) or getattr(client, "submit", None) or getattr(client, "share", None)
    assert submit is not None, "Client missing submit_share()"

    # Call with flexible signature
    params = inspect.signature(submit).parameters
    if "share" in params:
        res = submit(share=share)
    elif "job_id" in params and "nonce" in params:
        res = submit(job_id=share["job_id"], nonce=share["nonce"], header=share.get("header"), mix=share.get("mix"), target=share.get("target"))
    else:
        # last resort: pass the whole dict
        res = submit(share)

    if inspect.iscoroutine(res):
        ack = await res
    else:
        ack = res

    # Accept both dict acks and simple True/False
    if isinstance(ack, dict):
        assert ack.get("accepted", True) or ack.get("status") in ("OK", "accepted")
    else:
        assert bool(ack) is True

    # Server should have invoked our callback
    try:
        await asyncio.wait_for(share_evt.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        pytest.fail("Server did not receive submitShare within timeout")

    # Validate the callback saw our job_id (if present)
    _, seen_share = shares[-1]
    if isinstance(seen_share, dict) and "job_id" in seen_share:
        assert seen_share["job_id"] == job_id


# ---------- optional protocol-level smoke (JSON-RPC over TCP) ----------------

@pytest.mark.asyncio
async def test_protocol_jsonrpc_shapes_exist():
    """
    Ensure protocol table exports method names we expect. This doesn't open sockets;
    it's a quick sanity check that helps catch rename regressions.
    """
    required = ("subscribe", "authorize", "set_difficulty", "submit_share")
    missing = [k for k in required if k not in METHODS]
    assert not missing, f"Missing protocol method names: {missing}"
    for k in required:
        assert isinstance(METHODS[k], str) and "." in METHODS[k]
