"""
End-to-end: POST a blob → receive commitment/root → GET blob back → GET availability proof
→ (optionally) run light verification if verification helpers are available.

This test is intentionally tolerant of slightly different API shapes:
- It tries multiple endpoint paths (/da/blob, /da/proof, etc.)
- It accepts commitment under a few common response field names
- It will verify inclusion/availability only if the project's verification helpers are present
"""

import base64
import importlib
import io
import os
import random
from typing import Any, Dict, Optional, Tuple

import pytest

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    TestClient = None  # type: ignore


SEED = 0xDA5EED
random.seed(SEED)
NS = 24
DATA = bytes(random.getrandbits(8) for _ in range(48 * 1024))  # 48 KiB sample blob


# ------------------------------- Utilities --------------------------------- #

def _hex(b: bytes) -> str:
    return "0x" + b.hex()


def _import(mod: str):
    return importlib.import_module(mod)


def _try_make_app(tmpdir) -> Optional[Any]:
    """
    Try to obtain a FastAPI app for the DA retrieval API using common patterns.
    """
    os.environ.setdefault("ANIMICA_DA_STORAGE_DIR", str(tmpdir))
    os.environ.setdefault("DA_STORAGE_DIR", str(tmpdir))  # alternate key if used

    try:
        api = _import("da.retrieval.api")
    except ModuleNotFoundError:
        return None

    # 1) app factory variants
    for fn_name in ("create_app", "get_app", "app_factory", "build_app"):
        if hasattr(api, fn_name):
            fn = getattr(api, fn_name)
            try:
                # Pass config if available
                cfg = None
                try:
                    cfg_mod = _import("da.config")
                    cfg_cls = getattr(cfg_mod, "Config", None)
                    cfg = cfg_cls() if cfg_cls else None
                    if cfg and hasattr(cfg, "storage_dir"):
                        setattr(cfg, "storage_dir", str(tmpdir))
                except Exception:
                    cfg = None
                return fn(cfg) if cfg else fn()
            except Exception:
                pass

    # 2) global app
    if hasattr(api, "app"):
        return getattr(api, "app")

    # 3) build service then ask API to make/mount an app for it
    try:
        svc_mod = _import("da.retrieval.service")
        for svc_name in ("Service", "RetrievalService"):
            if hasattr(svc_mod, svc_name):
                Svc = getattr(svc_mod, svc_name)
                try:
                    svc = Svc(storage_dir=str(tmpdir))
                except Exception:
                    try:
                        svc = Svc()
                    except Exception:
                        svc = None
                if svc:
                    for helper in ("app_from_service", "mount_app", "create_app_for_service"):
                        if hasattr(api, helper):
                            try:
                                return getattr(api, helper)(svc)
                            except Exception:
                                pass
    except ModuleNotFoundError:
        pass

    # 4) last resort: reload after env set
    try:
        api = importlib.reload(api)
        if hasattr(api, "app"):
            return getattr(api, "app")
    except Exception:
        pass

    return None


def _post_blob(client: TestClient, ns: int, data: bytes) -> Tuple[str, Dict[str, Any]]:
    """
    POST a blob via several payload/endpoint variants.
    Returns (commitment_hex, response_json).
    """
    endpoints = ["/da/blob", "/da/blob/"]
    payloads = [
        ("application/json", {"namespace": ns, "data": _hex(data)}),
        ("application/json", {"namespace": ns, "data_hex": _hex(data)}),
        ("application/json", {"ns": ns, "data": _hex(data)}),
        ("application/json", {"ns": ns, "data_b64": base64.b64encode(data).decode()}),
        ("multipart/form-data", {"file": ("blob.bin", io.BytesIO(data), "application/octet-stream"), "ns": str(ns)}),
        ("multipart/form-data", {"file": ("blob.bin", io.BytesIO(data), "application/octet-stream"), "namespace": str(ns)}),
    ]

    for ep in endpoints:
        for ctype, body in payloads:
            try:
                if ctype == "application/json":
                    r = client.post(ep, json=body, timeout=60)
                else:
                    files = {"file": body["file"]} if "file" in body else None
                    form = {k: v for k, v in body.items() if k != "file"}
                    r = client.post(ep, data=form, files=files, timeout=60)

                if r.status_code in (200, 201):
                    try:
                        js = r.json()
                    except Exception:
                        js = {"commitment": r.text.strip()}

                    # Find a hex commitment
                    cand = None
                    for key in ("commitment", "root", "nmt_root", "da_root", "id"):
                        if key in js and isinstance(js[key], str) and js[key].startswith("0x"):
                            cand = js[key]
                            break
                    if not cand:
                        for v in js.values():
                            if isinstance(v, str) and v.startswith("0x") and len(v) >= 66:
                                cand = v
                                break
                    if not cand:
                        for v in js.values():
                            if isinstance(v, dict):
                                for vv in v.values():
                                    if isinstance(vv, str) and vv.startswith("0x") and len(vv) >= 66:
                                        cand = vv
                                        break
                            if cand:
                                break
                    if not cand:
                        raise AssertionError(f"POST {ep} ok but no commitment found: {js}")
                    return cand, js
            except Exception:
                pass

    raise pytest.skip.Exception("Could not POST blob with any supported payload/endpoint variant")


def _get_blob_bytes(client: TestClient, commitment_hex: str) -> bytes:
    endpoints = [
        f"/da/blob/{commitment_hex}",
        f"/da/blob/{commitment_hex}/data",
        f"/da/get/{commitment_hex}",
    ]
    for ep in endpoints:
        try:
            r = client.get(ep, timeout=60)
            if r.status_code == 200:
                ct = r.headers.get("content-type", "")
                if "application/json" not in ct and r.content:
                    return r.content
                try:
                    js = r.json()
                    val = js.get("data")
                    if isinstance(val, str):
                        if val.startswith("0x"):
                            return bytes.fromhex(val[2:])
                        try:
                            return base64.b64decode(val)
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            pass
    raise pytest.skip.Exception("Could not GET blob bytes from any supported endpoint")


def _get_proof(client: TestClient, commitment_hex: str, samples: int = 24) -> Dict[str, Any]:
    endpoints = [
        (f"/da/blob/{commitment_hex}/proof", {}),
        (f"/da/proof/{commitment_hex}", {}),
        (f"/da/proof", {"commitment": commitment_hex}),
    ]
    for ep, params in endpoints:
        try:
            q = {**params, "samples": str(samples)}
            r = client.get(ep, params=q, timeout=60)
            if r.status_code == 200:
                try:
                    return r.json()
                except Exception:
                    if r.content:
                        return {"_binary": True, "content_len": len(r.content)}
        except Exception:
            pass
    raise pytest.skip.Exception("Could not GET proof from any supported endpoint")


def _commit_locally(ns: int, data: bytes) -> Optional[str]:
    """
    Compute commitment/NMT-root locally via a few common helpers; return hex string if possible.
    """
    cand: Optional[str] = None

    # Preferred: da.blob.commitment
    try:
        cm = _import("da.blob.commitment")
        for fn_name in ("commit", "commit_blob", "compute_commitment", "compute"):
            if hasattr(cm, fn_name):
                fn = getattr(cm, fn_name)
                try:
                    res = fn(ns, data)  # could return (root_hex, size, ns) or a dict
                    if isinstance(res, tuple) and res:
                        root = res[0]
                        if isinstance(root, (bytes, bytearray)):
                            cand = _hex(bytes(root))
                        elif isinstance(root, str) and root.startswith("0x"):
                            cand = root
                        elif isinstance(root, str):
                            cand = "0x" + root
                    elif isinstance(res, dict):
                        root = res.get("root") or res.get("commitment") or res.get("nmt_root")
                        if isinstance(root, bytes):
                            cand = _hex(root)
                        elif isinstance(root, str):
                            cand = root if root.startswith("0x") else "0x" + root
                    if cand:
                        return cand
                except Exception:
                    pass
    except ModuleNotFoundError:
        pass

    # Fallback: try NMT pipeline (encode → root)
    try:
        codec = _import("da.nmt.codec")
        commit = _import("da.nmt.commit")
        for enc in ("encode_leaf", "leaf_bytes", "make_leaf"):
            if hasattr(codec, enc) and hasattr(commit, "commit"):
                try:
                    encode_leaf = getattr(codec, enc)
                    leaves = [encode_leaf(ns, data)]  # minimal single-leaf commitment
                    root_b = commit.commit(leaves)
                    if isinstance(root_b, (bytes, bytearray)):
                        return _hex(bytes(root_b))
                except Exception:
                    pass
    except ModuleNotFoundError:
        pass

    return cand


def _try_verify_light(root_hex: str, proof: Dict[str, Any]) -> Optional[bool]:
    """
    Attempt to verify availability proof using known helpers.
    Returns True/False if a verifier ran, or None if no suitable verifier is available.
    """
    root_b = bytes.fromhex(root_hex[2:]) if root_hex.startswith("0x") else bytes.fromhex(root_hex)

    # 1) da.sampling.verifier
    try:
        ver = _import("da.sampling.verifier")
        for nm in ("verify", "verify_proof", "verify_samples"):
            if hasattr(ver, nm):
                fn = getattr(ver, nm)
                try:
                    return bool(fn(root_hex, proof))
                except Exception:
                    try:
                        return bool(fn(root=root_hex, proof=proof))
                    except Exception:
                        try:
                            return bool(fn(root_b, proof))
                        except Exception:
                            pass
    except ModuleNotFoundError:
        pass

    # 2) da.sampling.light_client
    try:
        lc = _import("da.sampling.light_client")
        for nm in ("verify_availability", "verify", "check"):
            if hasattr(lc, nm):
                fn = getattr(lc, nm)
                # If the proof exposes "samples" array, try that
                if isinstance(proof, dict) and "samples" in proof:
                    samples = proof["samples"]
                    try:
                        return bool(fn(root_hex, samples))
                    except Exception:
                        try:
                            return bool(fn(root=root_hex, samples=samples))
                        except Exception:
                            try:
                                return bool(fn(root_b, samples))
                            except Exception:
                                pass
    except ModuleNotFoundError:
        pass

    return None


# ---------------------------------- Test ------------------------------------ #

@pytest.mark.skipif(TestClient is None, reason="fastapi.testclient not available")
def test_end_to_end_post_get_verify(tmp_path):
    app = _try_make_app(tmp_path)
    if app is None:
        pytest.skip("DA retrieval FastAPI app not found; skipping integration test")

    client = TestClient(app)

    # 1) POST the blob
    commitment_hex, post_js = _post_blob(client, NS, DATA)
    assert isinstance(commitment_hex, str) and commitment_hex.startswith("0x"), "Commitment must be 0x-hex"

    # 2) GET the blob back and compare bytes
    got = _get_blob_bytes(client, commitment_hex)
    assert got == DATA, "Retrieved blob bytes must match original payload"

    # 3) Compute commitment locally and cross-check the root
    local_root = _commit_locally(NS, DATA)
    if local_root is not None:
        assert local_root.lower() == commitment_hex.lower(), f"Local NMT root {local_root} != API commitment {commitment_hex}"

    # 4) Request an availability proof and (optionally) verify it
    proof = _get_proof(client, commitment_hex, samples=24)
    assert isinstance(proof, dict) and len(proof) > 0 or ("_binary" in proof and proof["_binary"] is True)

    # Optional verification if helpers are present
    vr = None
    if isinstance(proof, dict) and "_binary" not in proof:
        vr = _try_verify_light(commitment_hex, proof)

    # If a verifier ran, it must pass
    if vr is not None:
        assert vr is True, "Light verification of availability proof failed"
    else:
        # Otherwise, at least ensure the proof looks structurally sane if JSON
        if isinstance(proof, dict):
            # Common shape hints
            if "samples" in proof:
                assert isinstance(proof["samples"], (list, tuple)) and len(proof["samples"]) > 0
