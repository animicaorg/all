from __future__ import annotations

"""
Deploy & Preflight Routers

Endpoints:
  - POST /deploy     : accept a signed CBOR tx and relay via node RPC
  - POST /preflight  : simulate a deploy/call locally (no state writes)

These endpoints are thin shims over the service layer in
`studio_services.services.deploy`. They purposefully avoid embedding business
logic; errors are raised as ApiError (mapped by middleware).
"""

import logging
from typing import Any, Callable, Optional, Sequence

from fastapi import APIRouter

# Models (request/response)
try:
    from studio_services.models.deploy import (DeployRequest, DeployResponse,
                                               PreflightRequest,
                                               PreflightResponse)
except Exception as e:  # pragma: no cover - defensive, keeps app importable
    raise RuntimeError(f"deploy router missing models: {e}")

log = logging.getLogger(__name__)
router = APIRouter(tags=["deploy"])


def _resolve(func_names: Sequence[str]) -> Callable[..., Any]:
    """
    Find a callable in studio_services.services.deploy by trying a list of names.
    This makes the router tolerant to small refactors in the service layer.
    """
    import importlib

    mod = importlib.import_module("studio_services.services.deploy")
    for name in func_names:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise RuntimeError(
        f"None of the expected service functions found in services.deploy: {', '.join(func_names)}"
    )


# Resolve service functions (prefer canonical names; accept aliases)
_submit_deploy = _resolve(
    ("submit_deploy", "relay_deploy", "handle_deploy", "deploy_submit")
)
_run_preflight = _resolve(("run_preflight", "preflight_simulate", "simulate_preflight"))


@router.post(
    "/deploy",
    summary="Relay a signed CBOR transaction",
    response_model=DeployResponse,
)
def post_deploy(req: DeployRequest) -> DeployResponse:
    """
    Accept a signed CBOR transaction and relay it to the node RPC.

    Returns:
        DeployResponse: includes txHash and optionally preliminary receipt info.

    Notes:
        - Authorization and rate limiting (if configured) are applied in middleware.
        - ChainId mismatches, invalid CBOR, or RPC errors are surfaced as ApiError
          and converted to JSON problem responses by the global error handler.
    """
    log.debug(
        "POST /deploy received (bytes=%s)",
        len(req.tx_cbor) if req and req.tx_cbor else 0,
    )
    res = _submit_deploy(req)
    # Strong typing guard: ensure service returned the correct model
    if not isinstance(res, DeployResponse):
        # Allow service to return dict-like payloads; Pydantic will coerce on return,
        # but we log unexpected shapes to aid debugging.
        log.warning("service returned non-DeployResponse type: %r", type(res))
    return res  # FastAPI handles model coercion


@router.post(
    "/preflight",
    summary="Simulate a deploy/call without state changes",
    response_model=PreflightResponse,
)
def post_preflight(req: PreflightRequest) -> PreflightResponse:
    """
    Run an offline simulation of a deploy or call, using the local VM.
    No state is modified; this is suitable for fee/gas estimation and sanity checks.

    Returns:
        PreflightResponse: gas estimate, logs/events, potential revert reason.
    """
    log.debug("POST /preflight received (kind=%s)", getattr(req, "kind", None))
    res = _run_preflight(req)
    if not isinstance(res, PreflightResponse):
        log.warning("service returned non-PreflightResponse type: %r", type(res))
    return res


def get_router() -> APIRouter:
    # For dynamic loading from routers.__init__.py
    return router
