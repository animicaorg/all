from __future__ import annotations

"""
Artifacts Router

Endpoints:
  - POST /artifacts                      : store artifact blob + metadata (returns ArtifactMeta)
  - GET  /artifacts/{id}                 : fetch artifact metadata by id
  - GET  /address/{addr}/artifacts       : list artifacts linked to a contract/address (paged)

This module is a thin shim over `studio_services.services.artifacts`.
It tolerates minor service-layer renames by resolving among common aliases.
"""

import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

from fastapi import APIRouter, Depends, Path, Query

log = logging.getLogger(__name__)

# Models
try:
    from studio_services.models.artifacts import ArtifactPut, ArtifactMeta
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"artifacts router missing models: {e}")

router = APIRouter(tags=["artifacts"])


def _resolve(func_names: Sequence[str]) -> Callable[..., Any]:
    """Find a callable in studio_services.services.artifacts by trying a list of names."""
    import importlib

    mod = importlib.import_module("studio_services.services.artifacts")
    for name in func_names:
        fn = getattr(mod, name, None)
        if callable(fn):
            return fn
    raise RuntimeError(
        f"None of the expected service functions found in services.artifacts: {', '.join(func_names)}"
    )


# Resolve service functions (primary names first; allow common aliases)
_put_artifact = _resolve(("put_artifact", "store_artifact", "create_artifact"))
_get_artifact = _resolve(("get_artifact", "fetch_artifact", "read_artifact", "artifact_by_id"))
_list_by_addr = _resolve(
    ("list_artifacts_by_address", "list_by_address", "artifacts_for_address", "get_by_address")
)


def _maybe_guard_dep() -> List[Any]:
    """
    Optional API-key guard for write route; returns a list of dependencies.
    If `require_api_key` isn't available, returns empty list.
    """
    try:  # pragma: no cover - optional import
        from studio_services.security.auth import require_api_key  # type: ignore

        return [Depends(require_api_key)]
    except Exception:
        return []


@router.post(
    "/artifacts",
    summary="Store an artifact (code/ABI/manifest) and return its metadata",
    response_model=ArtifactMeta,
    dependencies=_maybe_guard_dep(),
)
def post_artifact(req: ArtifactPut) -> ArtifactMeta:
    """
    Accept a content-addressed artifact blob with associated metadata and persist it.

    The service layer is responsible for:
      - Validating content digests and computing canonical id.
      - Enforcing write-once semantics.
      - Optionally pinning content to DA and linking to address/tx if provided.
    """
    log.debug("POST /artifacts: kind=%s size=%s addr=%s", getattr(req, "kind", None), getattr(req, "size", None), getattr(req, "address", None))
    meta = _put_artifact(req)
    if not isinstance(meta, ArtifactMeta):
        log.warning("service returned unexpected type for put_artifact: %r", type(meta))
    return meta


@router.get(
    "/artifacts/{artifact_id}",
    summary="Get artifact metadata by id",
    response_model=ArtifactMeta,
)
def get_artifact(
    artifact_id: str = Path(
        ...,
        description="Artifact id (content-address / digest string).",
    )
) -> ArtifactMeta:
    """
    Fetch metadata for a stored artifact by id. The service may also serve
    content from a static route if enabled; this endpoint returns metadata.
    """
    log.debug("GET /artifacts/{artifact_id} id=%s", artifact_id)
    meta = _get_artifact(artifact_id)
    if not isinstance(meta, ArtifactMeta):
        log.warning("service returned unexpected type for get_artifact: %r", type(meta))
    return meta


@router.get(
    "/address/{address}/artifacts",
    summary="List artifacts associated with an address",
    response_model=List[ArtifactMeta],
)
def list_address_artifacts(
    address: str = Path(
        ...,
        description="Contract or account address (bech32m anim1… or canonical format supported by the service).",
    ),
    limit: int = Query(
        50,
        ge=1,
        le=200,
        description="Max number of items to return (1–200).",
    ),
    cursor: Optional[str] = Query(
        None,
        description="Opaque cursor for pagination (pass value from previous response).",
    ),
) -> List[ArtifactMeta]:
    """
    List artifacts linked to an address. Supports simple cursor pagination via service layer.
    """
    log.debug("GET /address/{address}/artifacts addr=%s limit=%d cursor=%s", address, limit, cursor)
    items = _list_by_addr(address, limit=limit, cursor=cursor)
    # Be lenient: accept list-like iterables and coerce to list for response_model
    if not isinstance(items, list):
        try:
            items = list(items)  # type: ignore[assignment]
        except Exception:
            log.warning("service returned non-iterable for list_by_address: %r", type(items))
            items = []
    # Best-effort type check
    for it in items:
        if not isinstance(it, ArtifactMeta):
            log.warning("list_by_address element has unexpected type: %r", type(it))
            break
    return items  # type: ignore[return-value]


def get_router() -> APIRouter:
    return router
