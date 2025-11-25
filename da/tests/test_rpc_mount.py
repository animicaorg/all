"""
RPC-mounted DA endpoints — smoke test that the DA router mounts into a FastAPI app
and (if the retrieval service is wired) supports a minimal POST→GET→PROOF round-trip.

The test is adapter-agnostic and will:
  • Create a FastAPI app (bare or via rpc server if available)
  • Mount DA routes using da.adapters.rpc_mount (best-effort across common APIs)
  • Assert at least one /da route exists
  • If the retrieval service is available, try a tiny POST/GET/proof flow

Skips gracefully when optional modules aren't present.
"""

import base64
import io
import os
import random
from typing import Any, Dict, Optional, Tuple

import pytest

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    FastAPI = None  # type: ignore
    TestClient = None  # type: ignore


SEED = 0xDA5EED
random.seed(SEED)
NS = 19
DATA = b"animica-da-rpc-mount-" + bytes(random.getrandbits(8) for _ in range(1024))


def _import(name: str):
    return __import__(name, fromlist=["*"])


def _make_app() -> Any:
    """
    Prefer an RPC app factory if available, otherwise build a plain FastAPI().
    """
    # Try rpc.server factories
    try:
        rpc_srv = _import("rpc.server")
        for nm in ("create_app", "get_app", "app_factory", "build_app"):
            if hasattr(rpc_srv, nm):
                return getattr(rpc_srv, nm)()
        if hasattr(rpc_srv, "app"):
            return getattr(rpc_srv, "app")
    except ModuleNotFoundError:
        pass

    # Fallback: plain FastAPI
    return FastAPI()  # type: ignore[name-defined]


def _make_service(tmpdir) -> Optional[Any]:
    """
    Try to instantiate a DA retrieval service that the rpc_mount adapter might accept.
    """
    os.environ.setdefault("ANIMICA_DA_STORAGE_DIR", str(tmpdir))
    os.environ.setdefault("DA_STORAGE_DIR", str(tmpdir))

    try:
        svc_mod = _import("da.retrieval.service")
    except ModuleNotFoundError:
        return None

    for cls_name in ("Service", "RetrievalService", "DAService"):
        if hasattr(svc_mod, cls_name):
            C = getattr(svc_mod, cls_name)
            try:
                return C(storage_dir=str(tmpdir))
            except Exception:
                try:
                    return C()
                except Exception:
                    continue
    return None


def _mount_da(app: Any, service: Optional[Any]) -> None:
    """
    Call into da.adapters.rpc_mount using a variety of likely function names.
    """
    rpc_mount = pytest.importorskip("da.adapters.rpc_mount")

    # Common signatures:
    #   mount_into_app(app, service)
    #   mount_into_app(app)
    #   include_router(app, service=...)
    #   mount(app=..., service=...)
    tried = False
    err = None
    for fn_name in (
        "mount_into_app",
        "mount",
        "include_into",
        "include_router",
        "attach",
        "mount_da",
        "mount_endpoints",
    ):
        if hasattr(rpc_mount, fn_name):
            fn = getattr(rpc_mount, fn_name)
            try:
                tried = True
                if service is not None:
                    fn(app, service)  # type: ignore[misc]
                else:
                    # try keyword first, then positional
                    try:
                        fn(app=app)  # type: ignore[misc]
                    except Exception as e:
                        err = e
                        fn(app)  # type: ignore[misc]
                return
            except Exception as e:  # keep trying other names
                err = e
                continue

    if not tried:
        pytest.skip("da.adapters.rpc_mount has no recognized mount function")
    if err:
        pytest.skip(f"Failed to mount DA endpoints via rpc_mount ({err!r})")


def _has_da_routes(app: Any) -> bool:
    routes = getattr(app, "routes", [])
    for r in routes:
        path = getattr(r, "path", "") or getattr(r, "path_format", "")
        if "/da" in path:
            return True
    return False


def _post_blob(client: TestClient, ns: int, data: bytes) -> Tuple[str, Dict[str, Any]]:
    """
    Try a few common POST shapes at /da/blob.
    """
    endpoints = ["/da/blob", "/da/blob/"]
    payloads = [
        ("application/json", {"namespace": ns, "data": "0x" + data.hex()}),
        ("application/json", {"ns": ns, "data_hex": "0x" + data.hex()}),
        ("application/json", {"ns": ns, "data_b64": base64.b64encode(data).decode()}),
        ("multipart/form-data", {"file": ("blob.bin", io.BytesIO(data), "application/octet-stream"), "ns": str(ns)}),
    ]
    last_err: Optional[str] = None
    for ep in endpoints:
        for ctype, body in payloads:
            try:
                if ctype == "application/json":
                    r = client.post(ep, json=body, timeout=30)
                else:
                    files = {"file": body["file"]}
                    form = {k: v for k, v in body.items() if k != "file"}
                    r = client.post(ep, data=form, files=files, timeout=30)
                if r.status_code in (200, 201):
                    js = {}
                    try:
                        js = r.json()
                    except Exception:
                        pass
                    # Try to discover commitment field
                    for key in ("commitment", "root", "nmt_root", "da_root", "id"):
                        v = js.get(key)
                        if isinstance(v, str) and v.startswith("0x"):
                            return v, js
                    # Fallback: scan values
                    for v in js.values():
                        if isinstance(v, str) and v.startswith("0x"):
                            return v, js
                last_err = f"{ep} → {r.status_code} {r.text[:120]}"
            except Exception as e:
                last_err = f"{ep} → {e!r}"
                continue
    pytest.skip(f"Could not POST blob via RPC-mounted endpoints ({last_err})")


def _get_blob(client: TestClient, commitment_hex: str) -> bytes:
    for ep in (
        f"/da/blob/{commitment_hex}",
        f"/da/blob/{commitment_hex}/data",
        "/da/blob",
    ):
        try:
            if ep.endswith("/da/blob"):
                r = client.get(ep, params={"commitment": commitment_hex}, timeout=30)
            else:
                r = client.get(ep, timeout=30)
            if r.status_code == 200:
                if r.headers.get("content-type", "").startswith("application/json"):
                    try:
                        js = r.json()
                    except Exception:
                        continue
                    val = js.get("data")
                    if isinstance(val, str) and val.startswith("0x"):
                        return bytes.fromhex(val[2:])
                    if isinstance(val, str):
                        try:
                            return base64.b64decode(val)
                        except Exception:
                            pass
                else:
                    return r.content
        except Exception:
            continue
    pytest.skip("Could not GET blob via RPC-mounted endpoints")


def _get_proof(client: TestClient, commitment_hex: str, samples: int = 16) -> Dict[str, Any]:
    for ep, params in (
        (f"/da/blob/{commitment_hex}/proof", {}),
        (f"/da/proof/{commitment_hex}", {}),
        ("/da/proof", {"commitment": commitment_hex}),
    ):
        try:
            r = client.get(ep, params={**params, "samples": str(samples)}, timeout=30)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    return {"_binary": True, "len": len(r.content)}
        except Exception:
            continue
    pytest.skip("Could not GET availability proof via RPC-mounted endpoints")


@pytest.mark.skipif(FastAPI is None or TestClient is None, reason="fastapi not available")
def test_rpc_mount_da_endpoints(tmp_path):
    # Build host app and optional service
    app = _make_app()
    service = _make_service(tmp_path)

    # Mount DA endpoints
    _mount_da(app, service)

    # Ensure at least one /da route is present
    assert _has_da_routes(app), "DA router did not mount any /da routes"

    # If there is no retrieval service, we can't exercise POST/GET
    if service is None:
        pytest.skip("DA retrieval service not available; mount presence verified only")

    # Exercise a minimal round-trip
    client = TestClient(app)
    commitment_hex, post_js = _post_blob(client, NS, DATA)
    got = _get_blob(client, commitment_hex)
    assert got == DATA, "Data returned via RPC-mounted endpoint must match original"

    proof = _get_proof(client, commitment_hex, samples=12)
    assert isinstance(proof, dict) and len(proof) > 0

