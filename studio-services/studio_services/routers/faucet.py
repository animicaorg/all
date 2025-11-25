from __future__ import annotations

"""
Faucet Router

Endpoint:
  - POST /faucet/drip  : request a controlled testnet drip to an address.

Notes:
  - This route is typically guarded by API-key and rate limits configured in
    security middleware. We add an optional dependency on `require_api_key`
    if present, but the service layer MUST still enforce faucet enablement.
"""

import logging
from typing import Any, Callable, Sequence

from fastapi import APIRouter

log = logging.getLogger(__name__)

# Optional API-key guard (if the project provides it)
_dependencies = []
try:  # pragma: no cover - optional import
    from fastapi import Depends
    from studio_services.security.auth import require_api_key  # type: ignore
    _dependencies = [Depends(require_api_key)]
except Exception:  # pragma: no cover
    # No-op: faucet can still be protected by global middleware / network firewalls.
    pass

router = APIRouter(tags=["faucet"], dependencies=_dependencies)

# Models (request/response)
try:
    from studio_services.models.faucet import FaucetRequest, FaucetResponse
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"faucet router missing models: {e}")


def _resolve(func_names: Sequence[str]) -> Callable[..., Any]:
    """
    Find a callable in studio_services.services.faucet by trying a list of names.
    Makes the router tolerant to light refactors in the service layer.
    """
    import importlib

    mod = importlib.import_module("studio_services.services.faucet")
    for name in func_names:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise RuntimeError(
        f"None of the expected service functions found in services.faucet: {', '.join(func_names)}"
    )


# Resolve service function (primary name first; accept common aliases)
_request_drip = _resolve(("request_drip", "drip", "submit_drip", "faucet_drip"))


@router.post(
    "/faucet/drip",
    summary="Request a testnet drip to an address",
    response_model=FaucetResponse,
)
def post_faucet_drip(req: FaucetRequest) -> FaucetResponse:
    """
    Request a small amount of test funds to the provided address.

    The service will validate:
      - Faucet enablement (may be disabled in production).
      - Address format and chainId.
      - Rate limits and optional API key / origin policies.

    Returns:
        FaucetResponse: contains txHash or a queued/denied status, depending on policy.
    """
    log.debug("POST /faucet/drip address=%s", getattr(req, "address", None))
    res = _request_drip(req)
    if not isinstance(res, FaucetResponse):
        log.warning("service returned non-FaucetResponse type: %r", type(res))
    return res


def get_router() -> APIRouter:
    # For dynamic loader from routers.__init__.py
    return router
