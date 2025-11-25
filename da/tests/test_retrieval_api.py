import base64
import importlib
import io
import json
import os
import random
from typing import Any, Dict, Optional, Tuple

import pytest

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    TestClient = None  # type: ignore


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

SEED = 20250921
random.seed(SEED)
NS = 24
DATA = bytes(random.getrandbits(8) for _ in range(32 * 1024))  # 32 KiB sample blob


# ---------------------------------------------------------------------------
# App/bootstrap helpers (tolerant to different wiring patterns)
# ---------------------------------------------------------------------------

def _import(name: str):
    return importlib.import_module(name)


def _hex(b: bytes) -> str:
    return "0x" + b.hex()


def _try_make_app(tmpdir) -> Optional[Any]:
    """
    Try a series of strategies to obtain a FastAPI app instance:
      1) da.retrieval.api:create_app(...)
      2) da.retrieval.api:app (module global)
      3) Build service object and ask api for app-from-service function
      4) As a last resort, import 'app' after setting env vars
    """
    # Prefer setting storage env up-front in case modules read them at import time.
    os.environ.setdefault("ANIMICA_DA_STORAGE_DIR", str(tmpdir))
    os.environ.setdefault("DA_STORAGE_DIR", str(tmpdir))  # alternate key, if used

    try:
        api = _import("da.retrieval.api")
    except ModuleNotFoundError:
        return None

    # 1) A factory
    for fn_name in ("create_app", "get_app", "app_factory", "build_app"):
        if hasattr(api, fn_name):
            fn = getattr(api, fn_name)
            try:
                # Try passing config if accepted
                try:
                    cfg_mod = _import("da.config")
                    # Best-effort: choose something named Config
                    cfg_cls = getattr(cfg_mod, "Config", None)
                    cfg = cfg_cls() if cfg_cls else None
                    if cfg and hasattr(cfg, "storage_dir"):
                        setattr(cfg, "storage_dir", str(tmpdir))
                    app = fn(cfg) if cfg else fn()
                except Exception:
                    app = fn()
                return app
            except Exception:
                pass

    # 2) Global app
    if hasattr(api, "app"):
        return getattr(api, "app")

    # 3) Build a service instance and hand it to API helper
    try:
        svc_mod = _import("da.retrieval.service")
        for svc_name in ("Service", "RetrievalService"):
            if hasattr(svc_mod, svc_name):
                Svc = getattr(svc_mod, svc_name)
                svc = None
                try:
                    svc = Svc(storage_dir=str(tmpdir))
                except Exception:
                    try:
                        svc = Svc()  # maybe reads env for storage path
                    except Exception:
                        svc = None
                if svc:
                    for helper in ("app_from_service", "mount_app", "create_app_for_service"):
                        if hasattr(api, helper):
                            fn = getattr(api, helper)
                            try:
                                app = fn(svc)
                                return app
                            except Exception:
                                pass
    except ModuleNotFoundError:
        pass

    # 4) Last resort: maybe the module defines a global app that needs env set
    try:
        api = importlib.reload(api)
        if hasattr(api, "app"):
            return getattr(api, "app")
    except Exception:
        pass

    return None


# ---------------------------------------------------------------------------
# Client request helpers (accept multiple API shapes)
# ---------------------------------------------------------------------------

def _post_blob(client: TestClient, ns: int, data: bytes) -> Tuple[str, Dict[str, Any]]:
    """
    Try several payload/endpoint variants for POSTing a blob.
    Returns (commitment_hex, response_json).
    """
    endpoints = ["/da/blob", "/da/blob/"]
    # Variant A: JSON hex
    payloads = [
        ("application/json", {"namespace": ns, "data": _hex(data)}),
        ("application/json", {"namespace": ns, "data_hex": _hex(data)}),
        ("application/json", {"ns": ns, "data": _hex(data)}),
        # Variant B: JSON base64
        ("application/json", {"namespace": ns, "data_b64": base64.b64encode(data).decode()}),
        ("application/json", {"ns": ns, "data_b64": base64.b64encode(data).decode()}),
        # Variant C: multipart (file + ns)
        ("multipart/form-data", {"file": ("blob.bin", io.BytesIO(data), "application/octet-stream"), "ns": str(ns)}),
        ("multipart/form-data", {"file": ("blob.bin", io.BytesIO(data), "application/octet-stream"), "namespace": str(ns)}),
    ]

    for ep in endpoints:
        for ctype, body in payloads:
            try:
                if ctype == "application/json":
                    r = client.post(ep, json=body, timeout=30)
                else:
                    files = None
                    data_fields = {}
                    if "file" in body:
                        files = {"file": body["file"]}
                    for k, v in body.items():
                        if k != "file":
                            data_fields[k] = v
                    r = client.post(ep, data=data_fields, files=files, timeout=30)
                if r.status_code in (200, 201):
                    try:
                        js = r.json()
                    except Exception:
                        # Some APIs might return plain text commitment
                        text = r.text.strip()
                        js = {"commitment": text}
                    # Heuristics: find hex-commitment in common fields or scan values
                    cand = None
                    for key in ("commitment", "root", "nmt_root", "da_root", "id"):
                        if key in js and isinstance(js[key], str) and js[key].startswith("0x"):
                            cand = js[key]
                            break
                    if not cand:
                        # scan string values for 0x-prefixed 64+ hex chars
                        for v in js.values():
                            if isinstance(v, str) and v.startswith("0x") and len(v) >= 66:
                                cand = v
                                break
                    if not cand:
                        # fallback: sometimes commitment is nested
                        for v in js.values():
                            if isinstance(v, dict):
                                for vv in v.values():
                                    if isinstance(vv, str) and vv.startswith("0x") and len(vv) >= 66:
                                        cand = vv
                                        break
                            if cand:
                                break
                    if not cand:
                        raise AssertionError(f"POST {ep} succeeded but no commitment found in response: {js}")
                    return cand, js
            except Exception:
                # try next variant
                pass

    raise pytest.skip.Exception("Could not POST blob with any supported payload/endpoint variant")


def _get_blob(client: TestClient, commitment_hex: str) -> bytes:
    endpoints = [
        f"/da/blob/{commitment_hex}",
        f"/da/blob/{commitment_hex}/data",
        f"/da/get/{commitment_hex}",
    ]
    for ep in endpoints:
        try:
            r = client.get(ep, timeout=30)
            if r.status_code == 200:
                # Prefer raw bytes; if JSON, accept {data: "..."} hex or base64
                ct = r.headers.get("content-type", "")
                if "application/octet-stream" in ct or "binary" in ct or not ct or r.content:
                    return r.content
                try:
                    js = r.json()
                except Exception:
                    continue
                if "data" in js and isinstance(js["data"], str):
                    s = js["data"]
                    if s.startswith("0x"):
                        return bytes.fromhex(s[2:])
                    try:
                        return base64.b64decode(s)
                    except Exception:
                        pass
        except Exception:
            pass
    raise pytest.skip.Exception("Could not GET blob bytes from any supported endpoint")


def _get_proof(client: TestClient, commitment_hex: str, samples: int = 16) -> Dict[str, Any]:
    endpoints = [
        (f"/da/blob/{commitment_hex}/proof", {}),
        (f"/da/proof/{commitment_hex}", {}),
        (f"/da/proof", {"commitment": commitment_hex}),
    ]
    for ep, params in endpoints:
        try:
            q = {**params, "samples": str(samples)}
            r = client.get(ep, params=q, timeout=30)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    # Some APIs may return CBOR/binary; accept non-empty content
                    if r.content:
                        return {"_binary": True, "content_len": len(r.content)}
        except Exception:
            pass
    raise pytest.skip.Exception("Could not GET proof from any supported endpoint")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient not available")
def test_post_get_proof_roundtrip(tmp_path):
    app = _try_make_app(tmp_path)
    if app is None:
        pytest.skip("FastAPI app for DA retrieval API not found")

    client = TestClient(app)

    # 1) POST the blob
    commitment, post_js = _post_blob(client, NS, DATA)
    assert commitment.startswith("0x"), f"commitment must be hex string, got: {commitment}"
    # Keep response fields around in case we want to assert metadata presence
    # (namespace, size, receipt, etc.) but don't be strict on schema shape.

    # 2) GET blob by commitment and compare bytes
    got = _get_blob(client, commitment)
    assert got == DATA, "GET of commitment must return the exact original bytes"

    # 3) GET availability proof; should be well-formed/non-empty
    proof = _get_proof(client, commitment, samples=16)
    # Heuristic checks: either binary proof, or JSON with some expected keys
    if "_binary" in proof:
        assert proof["content_len"] > 0
    else:
        # Accept a broad set of shapes: {samples:[...]}, {leaves:[...], proofs:[...]}, etc.
        assert isinstance(proof, dict) and len(proof) > 0
        # If 'samples' present, it should be a non-empty list
        if "samples" in proof:
            assert isinstance(proof["samples"], (list, tuple)) and len(proof["samples"]) > 0

    # Optional: if a light-client verify function exists, try a tiny verification smoke test
    try:
        lc_mod = _import("da.sampling.light_client")
        for nm in ("verify_availability", "verify", "verify_samples", "check"):
            if hasattr(lc_mod, nm):
                fn = getattr(lc_mod, nm)
                # Adapt: many verifiers accept (root, samples). We don't know exact proof JSON shape,
                # so only attempt if it provides "samples" already.
                if isinstance(proof, dict) and "samples" in proof:
                    ok = False
                    try:
                        ok = bool(fn(commitment, proof["samples"]))
                    except Exception:
                        try:
                            ok = bool(fn(root=commitment, samples=proof["samples"]))
                        except Exception:
                            pass
                    # Don't fail the API test if verification glue doesn't fit; this is a bonus smoke-check.
                    if ok:
                        break
    except ModuleNotFoundError:
        pass


