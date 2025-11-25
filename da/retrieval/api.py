from __future__ import annotations

"""
Animica • DA • Retrieval API (FastAPI)

Endpoints
---------
POST /da/blob
    Content-Type: application/octet-stream
    Query: ns=<int namespace id>
    Body : raw blob bytes
    Resp : { "commitment": "0x...", "namespace": 24, "size": 4096, "receipt": {...} }

GET /da/blob/{commitment}
    Path : commitment hex (0x-prefixed or plain)
    Resp : application/octet-stream (raw blob bytes)

GET /da/proof
    Query: commitment=0x...&indices=1,5,42 (CSV)
    Resp : JSON proof object (sufficient for light verification)

Notes
-----
- This module is deliberately thin. It delegates the heavy lifting to
  da.retrieval.service.RetrievalService, which handles store/NMT/erasure/proofs.
- Optional auth/rate-limit middleware is supported if present, but not required.
"""

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Mapping, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, NonNegativeInt, conint

# Optional imports (graceful when absent)
try:  # errors
    from da.errors import DAError, NotFound, InvalidProof, NamespaceRangeError
except Exception:  # pragma: no cover
    class DAError(Exception):
        pass
    class NotFound(DAError):
        pass
    class InvalidProof(DAError):
        pass
    class NamespaceRangeError(DAError):
        pass

try:  # service
    from .service import RetrievalService
except Exception as e:  # pragma: no cover
    raise RuntimeError("da.retrieval.service.RetrievalService is required by api.py") from e

# Optional auth / rate limit dependencies (no-ops if modules missing)
def _noop_dep():
    return None

try:
    from .auth import auth_dependency  # type: ignore
except Exception:  # pragma: no cover
    auth_dependency = _noop_dep

try:
    from .rate_limit import rate_limit_dependency  # type: ignore
except Exception:  # pragma: no cover
    rate_limit_dependency = _noop_dep


# -------------------------- Models --------------------------

class BlobPostResponse(BaseModel):
    commitment: str = Field(..., description="NMT root commitment (0x-hex)")
    namespace: conint(ge=0) = Field(..., description="Namespace id used for the blob")
    size: NonNegativeInt = Field(..., description="Blob size in bytes")
    receipt: Optional[Mapping[str, Any]] = Field(
        None, description="Post receipt (sig/alg policy binding) if available"
    )


class ErrorPayload(BaseModel):
    error: str
    detail: Optional[str] = None


# -------------------------- Helpers --------------------------

def _hex_to_bytes(x: str) -> bytes:
    xs = x[2:] if x.startswith(("0x", "0X")) else x
    if len(xs) % 2 == 1:
        xs = "0" + xs
    try:
        return bytes.fromhex(xs)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid hex string")

def _bytes_to_hex(b: bytes) -> str:
    return "0x" + b.hex()

def _coerce_mapping(obj: Any) -> Mapping[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, Mapping):
        return obj  # type: ignore[return-value]
    if is_dataclass(obj):
        return asdict(obj)
    # Last resort: try to read common attributes
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)
    raise TypeError("Receipt/object is not JSON-serializable")

def _http_error_from_da(e: DAError) -> HTTPException:
    if isinstance(e, NotFound):
        return HTTPException(status_code=404, detail=str(e) or "Not found")
    if isinstance(e, InvalidProof):
        return HTTPException(status_code=422, detail=str(e) or "Invalid proof")
    if isinstance(e, NamespaceRangeError):
        return HTTPException(status_code=400, detail=str(e) or "Namespace out of range")
    return HTTPException(status_code=400, detail=str(e) or "DA error")


# -------------------------- App factory --------------------------

def create_app(service: Optional[RetrievalService] = None) -> FastAPI:
    """
    Build and return a FastAPI app exposing the DA endpoints.
    You can pass a pre-wired RetrievalService; otherwise a default is constructed.
    """
    svc = service or RetrievalService()
    app = FastAPI(
        title="Animica DA Retrieval",
        version=getattr(svc, "version", "0.0.0"),
        description="Post/get blobs and request DA sampling proofs.",
        openapi_tags=[
            {"name": "da", "description": "Data Availability endpoints"},
        ],
    )

    @app.post(
        "/da/blob",
        response_model=BlobPostResponse,
        responses={400: {"model": ErrorPayload}, 401: {}, 429: {}},
        tags=["da"],
        summary="Post a blob",
    )
    async def post_blob(
        request: Request,
        response: Response,
        ns: conint(ge=0) = Query(..., description="Namespace id (non-negative integer)"),
        _auth=Depends(auth_dependency),
        _rl=Depends(rate_limit_dependency),
    ) -> BlobPostResponse:
        try:
            body = await request.body()
            if not body:
                raise HTTPException(status_code=400, detail="Empty body")
            rec = await _maybe_await(svc.post_blob(namespace=int(ns), data=body))
            # Expected: rec has fields commitment: bytes, size: int, namespace: int, receipt: mapping?
            commitment_b = rec.get("commitment") if isinstance(rec, dict) else getattr(rec, "commitment", None)
            size_v = rec.get("size") if isinstance(rec, dict) else getattr(rec, "size", None)
            ns_v = rec.get("namespace") if isinstance(rec, dict) else getattr(rec, "namespace", ns)
            receipt_v = rec.get("receipt") if isinstance(rec, dict) else getattr(rec, "receipt", None)

            if not isinstance(commitment_b, (bytes, bytearray)):
                raise HTTPException(status_code=500, detail="Service did not return a bytes commitment")
            response.headers["X-DA-Commitment"] = _bytes_to_hex(commitment_b)
            return BlobPostResponse(
                commitment=_bytes_to_hex(commitment_b),
                namespace=int(ns_v),
                size=int(size_v if size_v is not None else len(body)),
                receipt=_coerce_mapping(receipt_v) if receipt_v is not None else None,
            )
        except HTTPException:
            raise
        except DAError as e:
            raise _http_error_from_da(e)
        except Exception as e:  # pragma: no cover
            raise HTTPException(status_code=500, detail=f"Internal error: {e}")

    @app.get(
        "/da/blob/{commitment}",
        responses={
            200: {"content": {"application/octet-stream": {}}},
            400: {"model": ErrorPayload},
            404: {"model": ErrorPayload},
        },
        tags=["da"],
        summary="Get a blob by commitment",
    )
    async def get_blob(
        commitment: str,
        _auth=Depends(auth_dependency),
        _rl=Depends(rate_limit_dependency),
    ):
        try:
            commit_b = _hex_to_bytes(commitment)
            data = await _maybe_await(svc.get_blob(commitment=commit_b))
            if not isinstance(data, (bytes, bytearray)):
                # allow service to return a stream-like
                if hasattr(data, "read"):
                    return StreamingResponse(data, media_type="application/octet-stream")
                raise HTTPException(status_code=500, detail="Service returned non-bytes payload")
            headers = {"X-DA-Commitment": _bytes_to_hex(commit_b)}
            return Response(content=bytes(data), media_type="application/octet-stream", headers=headers)
        except HTTPException:
            raise
        except DAError as e:
            raise _http_error_from_da(e)
        except Exception as e:  # pragma: no cover
            raise HTTPException(status_code=500, detail=f"Internal error: {e}")

    @app.get(
        "/da/proof",
        responses={200: {"content": {"application/json": {}}}, 400: {"model": ErrorPayload}, 404: {"model": ErrorPayload}},
        tags=["da"],
        summary="Get a sampling proof for selected indices",
    )
    async def get_proof(
        commitment: str = Query(..., description="Blob commitment (0x-hex)"),
        indices: str = Query(..., description="CSV of integer indices to sample"),
        namespace: Optional[int] = Query(None, description="Optional namespace hint"),
        _auth=Depends(auth_dependency),
        _rl=Depends(rate_limit_dependency),
    ):
        try:
            commit_b = _hex_to_bytes(commitment)
            idx_list = _parse_indices_csv(indices)
            payload = await _maybe_await(
                svc.get_proof(commitment=commit_b, indices=idx_list, namespace=namespace)
            )
            # Ensure JSON-serializable
            if isinstance(payload, (bytes, bytearray)):
                # If service returns CBOR/bytes, expose as base64 in JSON wrapper
                import base64
                payload = {"format": "bytes", "data_b64": base64.b64encode(bytes(payload)).decode()}
            elif not _is_jsonable(payload):
                payload = _coerce_mapping(payload)
            return JSONResponse(payload)  # type: ignore[arg-type]
        except HTTPException:
            raise
        except DAError as e:
            raise _http_error_from_da(e)
        except Exception as e:  # pragma: no cover
            raise HTTPException(status_code=500, detail=f"Internal error: {e}")

    return app


# -------------------------- Small utils --------------------------

def _parse_indices_csv(csv: str) -> List[int]:
    out: List[int] = []
    for piece in csv.split(","):
        p = piece.strip()
        if not p:
            continue
        try:
            v = int(p, 10)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid index: {p!r}")
        if v < 0:
            raise HTTPException(status_code=400, detail=f"Index must be non-negative: {v}")
        out.append(v)
    if not out:
        raise HTTPException(status_code=400, detail="No indices provided")
    return out

def _is_jsonable(x: Any) -> bool:
    try:
        from json import dumps
        dumps(x)
        return True
    except Exception:
        return False

async def _maybe_await(x):
    return await x if hasattr(x, "__await__") else x


# -------------------------- Dev entry --------------------------

if __name__ == "__main__":  # pragma: no cover
    # Minimal dev server for quick smoke test:
    #   python -m da.retrieval.api
    # or: uvicorn da.retrieval.api:create_app().  To pass a custom service, import and wire externally.
    import uvicorn  # type: ignore
    app = create_app()
    uvicorn.run(app, host="127.0.0.1", port=8549)
