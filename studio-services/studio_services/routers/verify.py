from __future__ import annotations

"""
Verify Routers

Endpoints:
  - POST /verify                 : submit source+manifest for verification (optional wait)
  - GET  /verify/{address}       : fetch verification result by contract address (bech32m anim1…)
  - GET  /verify/{txHash}        : fetch verification result by deploy tx hash (0x…64 hex)

These are thin shims over `studio_services.services.verify`.
"""

import inspect
import logging
from typing import Any, Callable, Dict, Optional, Sequence

from fastapi import APIRouter, Path, Query

# Pydantic models
try:
    from studio_services.models.verify import (
        VerifyRequest,
        VerifyStatus,
        VerifyResult,
    )
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"verify router missing models: {e}")

log = logging.getLogger(__name__)
router = APIRouter(tags=["verify"])


def _resolve(func_names: Sequence[str]) -> Callable[..., Any]:
    """Best-effort resolver for service functions with tolerant naming."""
    import importlib

    mod = importlib.import_module("studio_services.services.verify")
    for name in func_names:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise RuntimeError(
        f"None of the expected functions found in services.verify: {', '.join(func_names)}"
    )


def _call_flexible(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """
    Call `fn` with kwargs if supported; otherwise drop unknown kwargs gracefully.
    Useful for optional params like `wait_seconds`.
    """
    try:
        sig = inspect.signature(fn)
        bound = sig.bind_partial(*args, **kwargs)  # may raise if totally incompatible
        return fn(*bound.args, **bound.kwargs)
    except TypeError:
        # Retry without kwargs
        return fn(*args)


# Resolve service-layer functions (primary name first; fallbacks allowed)
_submit_verify = _resolve(("submit_verify", "verify_submit", "start_verify", "enqueue_verify"))
_get_by_address = _resolve(("get_verify_by_address", "get_by_address", "result_by_address"))
_get_by_txhash = _resolve(("get_verify_by_txhash", "get_by_tx", "result_by_txhash", "result_by_tx"))


@router.post(
    "/verify",
    summary="Submit verification job (optionally wait for completion)",
    response_model=VerifyStatus,
)
def post_verify(
    req: VerifyRequest,
    wait_seconds: Optional[float] = Query(
        default=None,
        ge=0.0,
        description="If set, block for up to N seconds waiting for completion.",
    ),
) -> VerifyStatus:
    """
    Accept a contract source+manifest for verification. Returns a job status with id.
    If `wait_seconds` is provided and the service supports waiting, this will return
    the terminal status (success/failure) or 'pending' on timeout.
    """
    log.debug("POST /verify (wait_seconds=%s)", wait_seconds)
    status_obj = _call_flexible(_submit_verify, req, wait_seconds=wait_seconds)
    if not isinstance(status_obj, VerifyStatus):
        log.warning("verify service returned unexpected type: %r", type(status_obj))
    return status_obj


# Important: register the txHash route with a strict regex so it doesn't conflict with address
@router.get(
    "/verify/{tx_hash}",
    summary="Get verification result by deploy tx hash",
    response_model=VerifyResult,
)
def get_verify_by_tx(
    tx_hash: str = Path(
        ...,
        pattern=r"0x[0-9a-fA-F]{64}",
        description="Deploy transaction hash (0x-prefixed 64 hex chars).",
    )
) -> VerifyResult:
    """
    Look up verification result linked to a specific deploy transaction.
    """
    log.debug("GET /verify/{tx_hash} tx_hash=%s", tx_hash)
    res = _get_by_txhash(tx_hash)
    if not isinstance(res, VerifyResult):
        log.warning("verify service returned unexpected type for tx lookup: %r", type(res))
    return res


@router.get(
    "/verify/{address}",
    summary="Get verification result by contract address",
    response_model=VerifyResult,
)
def get_verify_by_address(
    address: str = Path(
        ...,
        description="Contract address (bech32m anim1… or canonical format supported by the service).",
    )
) -> VerifyResult:
    """
    Look up verification result for a deployed contract address.
    """
    log.debug("GET /verify/{address} address=%s", address)
    res = _get_by_address(address)
    if not isinstance(res, VerifyResult):
        log.warning("verify service returned unexpected type for address lookup: %r", type(res))
    return res


def get_router() -> APIRouter:
    return router
