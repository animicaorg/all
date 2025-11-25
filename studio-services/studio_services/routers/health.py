from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, Response, status

try:
    # Local package imports
    from studio_services import version as svc_version
    from studio_services.config import Config, load_config
except Exception:  # pragma: no cover - defensive in early boot
    svc_version = None  # type: ignore
    Config = object  # type: ignore

    def load_config() -> Any:  # type: ignore
        return None


log = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

_PROCESS_START = time.time()


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uptime_seconds() -> float:
    return max(0.0, time.time() - _PROCESS_START)


def _check_storage(cfg: Any) -> Tuple[bool, Dict[str, Any]]:
    """
    Storage liveness (filesystem) — verifies the configured STORAGE_DIR exists
    and is writable. Does not create the directory in readiness checks.
    """
    info: Dict[str, Any] = {}
    try:
        storage_dir = getattr(cfg, "STORAGE_DIR", None)
        if not storage_dir:
            info.update(error="STORAGE_DIR not configured")
            return False, info

        info["path"] = storage_dir
        if not os.path.isdir(storage_dir):
            info.update(error="directory missing")
            return False, info

        test_path = os.path.join(storage_dir, ".rw_probe")
        with open(test_path, "wb") as f:
            f.write(b"ok")
        os.remove(test_path)
        return True, info
    except Exception as e:  # pragma: no cover
        info.update(error=str(e))
        return False, info


def _check_rpc(cfg: Any) -> Tuple[bool, Dict[str, Any]]:
    """
    RPC readiness — attempts a very small call to the node to confirm
    connectivity and basic JSON-RPC viability.
    """
    info: Dict[str, Any] = {}
    rpc_url = getattr(cfg, "RPC_URL", None)
    if not rpc_url:
        info.update(error="RPC_URL not configured")
        return False, info
    info["url"] = rpc_url

    # Prefer the internal adapter if available; otherwise do a tiny raw POST.
    try:
        try:
            from studio_services.adapters.node_rpc import NodeRPC  # type: ignore

            client = NodeRPC(rpc_url, timeout=getattr(cfg, "RPC_TIMEOUT", 2.0))
            head = client.get_head()  # expected to be cheap
            # Minimal schema sanity (height/hash keys are typical; tolerate variants)
            if not isinstance(head, dict):
                info.update(error="unexpected head shape")
                return False, info
            info["head"] = {k: head.get(k) for k in ("height", "hash", "number") if k in head}
            return True, info
        except Exception as adapter_err:
            # Fallback to raw HTTP if requests is available
            import urllib.request

            req = urllib.request.Request(
                rpc_url,
                data=json.dumps(
                    {"jsonrpc": "2.0", "id": 1, "method": "chain.getHead", "params": []}
                ).encode("utf-8"),
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status != 200:
                    info.update(error=f"http {resp.status}")
                    return False, info
                payload = json.loads(resp.read().decode("utf-8"))
                if "result" not in payload:
                    info.update(error="no result in payload")
                    return False, info
                res = payload["result"]
                info["head"] = {k: res.get(k) for k in ("height", "hash", "number") if isinstance(res, dict)}
                return True, info
    except Exception as e:  # pragma: no cover
        info.update(error=str(e))
        return False, info


def _version_blob() -> Dict[str, Any]:
    ver = getattr(svc_version, "__version__", "0.0.0")
    git = None
    if svc_version and hasattr(svc_version, "git_describe"):
        try:
            git = svc_version.git_describe()  # type: ignore[attr-defined]
        except Exception:  # pragma: no cover
            git = None
    return {
        "service": "studio-services",
        "version": ver,
        "git": git,
        "python": {
            "version": "{}.{}.{}".format(*os.sys.version_info[:3]),
            "impl": os.sys.implementation.name,
        },
        "started_at": datetime.fromtimestamp(_PROCESS_START, tz=timezone.utc).isoformat(),
        "now": _utcnow_iso(),
        "uptime_seconds": round(_uptime_seconds(), 3),
    }


@router.get("/healthz", summary="Liveness probe", response_model=None)
def healthz() -> Dict[str, Any]:
    """
    Simple liveness probe: always returns 200 if the process is serving requests.
    """
    return {"status": "ok", **_version_blob()}


@router.get("/version", summary="Service version", response_model=None)
def version() -> Dict[str, Any]:
    """
    Returns service version metadata and runtime details.
    """
    meta = _version_blob()
    try:
        cfg = load_config()
        meta["chainId"] = getattr(cfg, "CHAIN_ID", None)
        meta["env"] = getattr(cfg, "ENV", None)
    except Exception:  # pragma: no cover
        pass
    return meta


@router.get("/readyz", summary="Readiness probe", response_model=None)
def readyz(response: Response) -> Dict[str, Any]:
    """
    Readiness probe: verifies essential downstreams (storage and RPC).
    Returns 200 when all checks pass; 503 otherwise.
    """
    checks: Dict[str, Dict[str, Any]] = {}
    ok_all = True

    cfg: Optional[Config] = None  # type: ignore[assignment]
    try:
        cfg = load_config()
    except Exception as e:  # pragma: no cover
        checks["config"] = {"ok": False, "error": f"load_config failed: {e}"}
        ok_all = False

    if cfg is not None:
        # Storage
        ok, info = _check_storage(cfg)
        checks["storage"] = {"ok": ok, **info}
        ok_all = ok_all and ok

        # RPC
        ok, info = _check_rpc(cfg)
        checks["rpc"] = {"ok": ok, **info}
        ok_all = ok_all and ok

        # Minimal config sanity
        chain_id = getattr(cfg, "CHAIN_ID", None)
        checks["config"] = {"ok": chain_id is not None, "chainId": chain_id}
        ok_all = ok_all and (chain_id is not None)

    status_code = status.HTTP_200_OK if ok_all else status.HTTP_503_SERVICE_UNAVAILABLE
    response.status_code = status_code
    return {
        "status": "ok" if ok_all else "degraded",
        "now": _utcnow_iso(),
        "uptime_seconds": round(_uptime_seconds(), 3),
        "checks": checks,
    }


def get_router() -> APIRouter:
    # Allow dynamic import via routers.__init__.py
    return router
