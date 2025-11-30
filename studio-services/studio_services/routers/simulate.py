from __future__ import annotations

"""
Simulate Router

Endpoint:
  - POST /simulate : compile+execute a single call or deploy against an ephemeral state
                     (no writes), returning logs, return data, and gas usage.

This router is a thin shim over `studio_services.services.simulate`.
It resolves the underlying service function with a tolerant name search.
"""

import logging
from typing import Any, Callable, Sequence

from fastapi import APIRouter, Depends

log = logging.getLogger(__name__)

# Request/Response models
try:
    from studio_services.models.simulate import SimulateCall, SimulateResult
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"simulate router missing models: {e}")


# Optional API-key guard for this endpoint (often useful in hosted Studio)
def _maybe_guard():
    try:  # pragma: no cover - optional import
        from studio_services.security.auth import \
            require_api_key  # type: ignore

        return [Depends(require_api_key)]
    except Exception:
        return []


router = APIRouter(tags=["simulate"], dependencies=_maybe_guard())


def _resolve(func_names: Sequence[str]) -> Callable[..., Any]:
    """
    Resolve a callable in `studio_services.services.simulate` by trying
    a small set of expected names, making the router resilient to refactors.
    """
    import importlib

    mod = importlib.import_module("studio_services.services.simulate")
    for name in func_names:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise RuntimeError(
        f"None of the expected service functions found in services.simulate: {', '.join(func_names)}"
    )


# Prefer primary name; allow common aliases
_simulate = _resolve(("simulate_call", "simulate", "run_simulation", "exec_simulation"))


@router.post(
    "/simulate",
    summary="Compile + run a contract call/deploy in an isolated VM (no state writes)",
    response_model=SimulateResult,
)
def post_simulate(req: SimulateCall) -> SimulateResult:
    """
    Execute a single simulation using an offline VM:

      • If `req.kind == "deploy"`, compiles the provided source/manifest and returns
        the deployment result (code hash, events, gas), without persisting state.

      • If `req.kind == "call"`, compiles/loads the target, decodes arguments per ABI,
        runs the call, and returns return-data, logs, and gas used.

    The underlying service guarantees:
      - No writes to chain state (pure simulation).
      - Deterministic gas metering and event ordering.
      - Strict resource limits and timeouts.

    Raises:
      ApiError-based exceptions on validation or execution failure; mapped by middleware.
    """
    log.debug(
        "POST /simulate kind=%s address=%s",
        getattr(req, "kind", None),
        getattr(req, "address", None),
    )
    result = _simulate(req)
    if not isinstance(result, SimulateResult):
        log.warning("service returned unexpected type for simulate: %r", type(result))
    return result


def get_router() -> APIRouter:
    return router
