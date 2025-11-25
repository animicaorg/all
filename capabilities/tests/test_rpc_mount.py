import sys
import types
from typing import Any, Dict, List, Optional

import pytest

try:
    from fastapi import FastAPI
    from httpx import AsyncClient
except Exception as e:  # pragma: no cover - helpful error if deps missing
    pytest.skip(f"FastAPI/httpx not available for tests: {e}")


# ---------- helpers: create a fake methods module the router will use ----------

@pytest.fixture(autouse=True)
def fake_methods(monkeypatch):
    """
    Provide a stand-in for capabilities.rpc.methods so the router (re)loads
    against predictable, in-memory list/get functions.
    """
    JOBS: Dict[str, Dict[str, Any]] = {
        "task_1": {
            "task_id": "task_1",
            "kind": "AI",
            "status": "queued",
            "caller": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq7u8y6n",
            "height": 42,
        },
        "task_2": {
            "task_id": "task_2",
            "kind": "Quantum",
            "status": "completed",
            "caller": "anim1zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz2r9rk",
            "height": 43,
        },
    }

    RESULTS: Dict[str, Dict[str, Any]] = {
        "task_2": {
            "task_id": "task_2",
            "status": "completed",
            "units": {"quantum_units": 123},
            "output_digest": "0x" + "ab" * 32,
        }
    }

    mod = types.ModuleType("capabilities.rpc.methods")

    def list_jobs(offset: Optional[str] = None, limit: int = 50) -> Dict[str, Any]:
        items: List[Dict[str, Any]] = list(JOBS.values())[:limit]
        return {"items": items, "next": None}

    def get_job(task_id: str) -> Dict[str, Any]:
        if task_id not in JOBS:
            raise KeyError(task_id)
        return JOBS[task_id]

    def get_result(task_id: str) -> Dict[str, Any]:
        if task_id not in RESULTS:
            raise KeyError(task_id)
        return RESULTS[task_id]

    # attach API surface
    mod.list_jobs = list_jobs
    mod.get_job = get_job
    mod.get_result = get_result

    # install into sys.modules so the router imports this
    monkeypatch.setitem(sys.modules, "capabilities.rpc.methods", mod)
    yield


# ---------- helpers: build app and mount the router under a known prefix ----------

async def _build_app(prefix: str = "/cap"):
    import importlib

    # Reload mount after monkeypatching methods, so it binds to our fake module.
    mount_mod = importlib.import_module("capabilities.rpc.mount")
    try:
        mount_mod = importlib.reload(mount_mod)
    except Exception:
        pass

    app = FastAPI()

    # Try a few common entrypoints for mounting/creating the router.
    mounted = False
    for fn_name in (
        "mount_capabilities_rpc",
        "mount_capabilities",
        "mount",
        "mount_router",
        "create_router",
        "router",
    ):
        fn = getattr(mount_mod, fn_name, None)
        if fn is None:
            continue

        # If it's a function that mounts into the app:
        try:
            if fn_name in {"mount_capabilities_rpc", "mount_capabilities", "mount"}:
                fn(app, prefix=prefix)  # type: ignore[misc,arg-type]
                mounted = True
                break
        except TypeError:
            # maybe signature is (app) only
            try:
                fn(app)  # type: ignore[misc]
                mounted = True
                break
            except Exception:
                pass

        # If it's a factory returning an APIRouter:
        try:
            router = fn(prefix=prefix)  # type: ignore[misc]
            # FastAPI includes APIRouter via include_router
            try:
                app.include_router(router, prefix="")  # router likely already has prefix
            except Exception:
                # If router is a FastAPI app itself:
                app.mount(prefix, router)  # type: ignore[arg-type]
            mounted = True
            break
        except TypeError:
            try:
                router = fn()  # type: ignore[misc]
                app.include_router(router, prefix=prefix)  # type: ignore[arg-type]
                mounted = True
                break
            except Exception:
                pass

    if not mounted:
        pytest.skip("capabilities.rpc.mount does not expose a known router mount function")

    client = AsyncClient(app=app, base_url="http://testserver")
    return app, client


# ---------- tests: list/get job, get result (read-only) ----------

@pytest.mark.anyio
async def test_list_jobs_ok():
    _, client = await _build_app("/cap")
    r = await client.get("/cap/jobs")
    assert r.status_code == 200
    data = r.json()
    assert "items" in data
    assert isinstance(data["items"], list)
    # our fake exposes two jobs
    assert {it["task_id"] for it in data["items"]} >= {"task_1", "task_2"}


@pytest.mark.anyio
async def test_get_job_found_and_not_found():
    _, client = await _build_app("/cap")
    ok = await client.get("/cap/jobs/task_1")
    assert ok.status_code == 200
    assert ok.json()["task_id"] == "task_1"

    miss = await client.get("/cap/jobs/does_not_exist")
    # Implementation may return 404 or 400 for unknown id; accept either.
    assert miss.status_code in (404, 400)


@pytest.mark.anyio
async def test_get_result_found_and_not_found():
    _, client = await _build_app("/cap")
    ok = await client.get("/cap/results/task_2")
    assert ok.status_code == 200
    body = ok.json()
    assert body["task_id"] == "task_2"
    assert "output_digest" in body or "units" in body

    miss = await client.get("/cap/results/unknown_task")
    assert miss.status_code in (404, 400)


@pytest.mark.anyio
async def test_read_only_no_post_on_list():
    """
    Ensure the surface is read-only; posting to list should not be allowed.
    """
    _, client = await _build_app("/cap")
    r = await client.post("/cap/jobs", json={})
    assert r.status_code in (404, 405)
