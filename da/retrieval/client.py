from __future__ import annotations

"""
Animica • DA • Retrieval • Python Client

Tiny, dependency-light client for the DA retrieval service used by SDK/tests.

Endpoints (conventions)
-----------------------
- POST {base}/da/blob?ns=<int>[&filename=<str>]
    Body: raw bytes (application/octet-stream) or JSON {"data":"0x...","ns":int}
    Returns JSON:
      {
        "commitment": "0x…",      # NMT root (hex)
        "namespace": 24,          # int
        "size": 12345,            # bytes
        "receipt": {...}          # optional, schema per da/blob/receipt.py
      }

- GET  {base}/da/blob/{commitment}
    Headers: optional Range: bytes=START-END
    Returns: blob bytes (200) or partial (206); ETag bound to commitment.

- GET  {base}/da/proof?commitment=<hex>&samples=3,11,42
    Returns JSON proof object matching schemas/availability_proof.cddl JSON form.

This client:
- Supports retries with exponential backoff.
- Exposes streaming download (iterator) and convenience `get_blob_to_file`.
- Handles simple hex normalization and Range helpers.
- Keeps an internal httpx.Client; safe to use as a context manager.

Usage
-----
    with DAClient("http://localhost:8087") as da:
        res = da.post_blob(ns=24, data=b"hello world")
        blob = da.get_blob(res.commitment)
        assert blob.data == b"hello world"

        # Partial:
        part = da.get_blob(res.commitment, byte_range=(0, 4))
        assert part.data == b"hello"

        # Proof (server must support sampling on stored blob):
        pr = da.get_proof(res.commitment, samples=[0, 5, 9])

Env overrides
-------------
- DA_HTTP_TIMEOUT      (seconds, float; default 30)
- DA_HTTP_RETRIES      (int; default 3)
- DA_HTTP_BACKOFF_BASE (seconds, float; default 0.25)
- DA_HTTP_HEADERS_*    (arbitrary extra headers; e.g. DA_HTTP_HEADERS_AUTH="Bearer abc")

Note: This client intentionally does not depend on the rest of the repository to simplify
use in SDK tests; it inlines minimal helpers where convenient.
"""

from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Optional, Tuple, Union
import os
import time

try:
    import httpx  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError("da.retrieval.client requires 'httpx' (pip install httpx).") from e


# ----------------------------- Small helpers --------------------------------

def _normalize_hex(s: str) -> str:
    if not isinstance(s, str) or not s:
        raise ValueError("hex string required")
    t = s.strip().lower()
    if t.startswith("0x"):
        t = t[2:]
    # basic sanity
    int(t or "0", 16)  # raises ValueError on bad hex
    return "0x" + t

def _headers_from_env() -> Dict[str, str]:
    # Any env var starting with DA_HTTP_HEADERS_ contributes a header
    headers: Dict[str, str] = {}
    for k, v in os.environ.items():
        if not k.startswith("DA_HTTP_HEADERS_"):
            continue
        name = k[len("DA_HTTP_HEADERS_") :].replace("_", "-")
        headers[name] = v
    return headers


# ----------------------------- Result models --------------------------------

@dataclass(frozen=True)
class PostBlobResult:
    commitment: str          # hex with 0x prefix
    namespace: int
    size: int
    receipt: Optional[dict] = None

@dataclass(frozen=True)
class BlobBytes:
    data: bytes
    status_code: int               # 200 or 206
    etag: Optional[str]
    content_range: Optional[str]   # e.g. "bytes 0-99/1000"
    content_length: Optional[int]
    content_type: Optional[str]

@dataclass(frozen=True)
class ProofResult:
    commitment: str
    samples: List[int]
    proof: dict


# ----------------------------- The Client -----------------------------------

class DAClient:
    """
    Synchronous DA client with retries and streaming helpers.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: Optional[float] = None,
        retries: Optional[int] = None,
        backoff_base: Optional[float] = None,
        api_key: Optional[str] = None,  # sets Authorization: Bearer <api_key>
        default_headers: Optional[Dict[str, str]] = None,
        client: Optional[httpx.Client] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(os.getenv("DA_HTTP_TIMEOUT", timeout if timeout is not None else 30.0))
        self.retries = int(os.getenv("DA_HTTP_RETRIES", retries if retries is not None else 3))
        self.backoff_base = float(os.getenv("DA_HTTP_BACKOFF_BASE", backoff_base if backoff_base is not None else 0.25))

        hdrs = {"Accept": "*/*"}
        hdrs.update(_headers_from_env())
        if api_key:
            hdrs["Authorization"] = f"Bearer {api_key}"
        if default_headers:
            hdrs.update(default_headers)

        self._own_client = client is None
        self._client = client or httpx.Client(base_url=self.base_url, headers=hdrs, timeout=self.timeout)

    # --- context management

    def close(self) -> None:
        if self._own_client:
            self._client.close()

    def __enter__(self) -> "DAClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # --- high-level API

    def post_blob(
        self,
        *,
        ns: int,
        data: Union[bytes, bytearray, memoryview],
        filename: Optional[str] = None,
        content_type: str = "application/octet-stream",
    ) -> PostBlobResult:
        """
        Upload a blob under a namespace. Sends raw bytes with query params.

        Returns PostBlobResult(commitment, namespace, size, receipt?)
        """
        if ns < 0 or ns > 2**32 - 1:
            raise ValueError("namespace must be a non-negative 32-bit integer")

        params = {"ns": str(int(ns))}
        if filename:
            params["filename"] = filename

        def _op() -> httpx.Response:
            return self._client.post(
                url="/da/blob",
                params=params,
                content=bytes(data),
                headers={"Content-Type": content_type},
            )

        resp = self._retry_request(_op, method="POST", path="/da/blob")
        self._raise_for_status(resp)

        payload = resp.json()
        commitment = _normalize_hex(payload.get("commitment"))
        namespace = int(payload.get("namespace"))
        size = int(payload.get("size"))
        receipt = payload.get("receipt")

        return PostBlobResult(commitment=commitment, namespace=namespace, size=size, receipt=receipt)

    def get_blob(
        self,
        commitment: str,
        *,
        byte_range: Optional[Tuple[int, int]] = None,
    ) -> BlobBytes:
        """
        Download a blob (full or partial). If `byte_range=(start, end)` is given, sends a Range header.
        Returns bytes plus headers useful for validation and resume.
        """
        c = _normalize_hex(commitment)
        path = f"/da/blob/{c}"

        headers: Dict[str, str] = {}
        if byte_range is not None:
            start, end = byte_range
            if start < 0 or (end is not None and end < start):
                raise ValueError("invalid byte_range")
            headers["Range"] = f"bytes={start}-{'' if end is None else end}"

        def _op() -> httpx.Response:
            return self._client.get(path, headers=headers)

        resp = self._retry_request(_op, method="GET", path=path)
        if resp.status_code not in (200, 206):
            self._raise_for_status(resp)

        return BlobBytes(
            data=resp.content,
            status_code=resp.status_code,
            etag=resp.headers.get("ETag"),
            content_range=resp.headers.get("Content-Range"),
            content_length=int(resp.headers["Content-Length"]) if "Content-Length" in resp.headers else None,
            content_type=resp.headers.get("Content-Type"),
        )

    def iter_blob(
        self,
        commitment: str,
        *,
        byte_range: Optional[Tuple[int, int]] = None,
        chunk_size: int = 1024 * 1024,
    ) -> Iterator[bytes]:
        """
        Stream a blob as chunks (iterator). Honors optional Range.
        """
        c = _normalize_hex(commitment)
        path = f"/da/blob/{c}"
        headers: Dict[str, str] = {}
        if byte_range is not None:
            start, end = byte_range
            if start < 0 or (end is not None and end < start):
                raise ValueError("invalid byte_range")
            headers["Range"] = f"bytes={start}-{'' if end is None else end}"

        def _op() -> httpx.Response:
            return self._client.build_request("GET", path, headers=headers)

        # For streaming, do the request manually to avoid buffering the body.
        for attempt in range(max(1, self.retries + 1)):
            try:
                req = _op()
                with self._client.send(req, stream=True) as resp:
                    if resp.status_code not in (200, 206):
                        self._raise_for_status(resp)
                    for chunk in resp.iter_bytes(chunk_size=chunk_size):
                        if chunk:
                            yield chunk
                    return
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt >= self.retries:
                    raise
                time.sleep(self._backoff(attempt))

    def get_blob_to_file(
        self,
        commitment: str,
        out_path: str,
        *,
        byte_range: Optional[Tuple[int, int]] = None,
        chunk_size: int = 1024 * 1024,
    ) -> BlobBytes:
        """
        Download a blob (optionally a range) directly to a file.
        Returns headers metadata like `get_blob`.
        """
        rst = self.get_blob(commitment, byte_range=byte_range) if chunk_size <= 0 else None
        if rst is not None:
            with open(out_path, "wb") as f:
                f.write(rst.data)
            return rst

        # streaming path
        meta: Optional[BlobBytes] = None
        c = _normalize_hex(commitment)
        path = f"/da/blob/{c}"
        headers: Dict[str, str] = {}
        if byte_range is not None:
            start, end = byte_range
            headers["Range"] = f"bytes={start}-{'' if end is None else end}"

        req = self._client.build_request("GET", path, headers=headers)
        with self._client.send(req, stream=True) as resp:
            if resp.status_code not in (200, 206):
                self._raise_for_status(resp)
            meta = BlobBytes(
                data=b"",
                status_code=resp.status_code,
                etag=resp.headers.get("ETag"),
                content_range=resp.headers.get("Content-Range"),
                content_length=int(resp.headers["Content-Length"]) if "Content-Length" in resp.headers else None,
                content_type=resp.headers.get("Content-Type"),
            )
            with open(out_path, "wb") as f:
                for chunk in resp.iter_bytes(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
        assert meta is not None
        return meta

    def get_proof(self, commitment: str, *, samples: Iterable[int]) -> ProofResult:
        """
        Fetch an availability proof for a blob for the given sample indices.
        """
        c = _normalize_hex(commitment)
        sample_list = sorted({int(s) for s in samples})
        params = {"commitment": c, "samples": ",".join(str(x) for x in sample_list)}

        def _op() -> httpx.Response:
            return self._client.get("/da/proof", params=params)

        resp = self._retry_request(_op, method="GET", path="/da/proof")
        self._raise_for_status(resp)
        payload = resp.json()
        return ProofResult(commitment=c, samples=sample_list, proof=payload)

    # --- internals

    def _retry_request(self, op, *, method: str, path: str) -> httpx.Response:
        last_exc: Optional[Exception] = None
        for attempt in range(max(1, self.retries + 1)):
            try:
                resp: httpx.Response = op()
                return resp
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                if attempt >= self.retries:
                    raise
                time.sleep(self._backoff(attempt))
        assert last_exc is not None
        raise last_exc

    def _backoff(self, attempt: int) -> float:
        # attempt: 0,1,2,… -> base * 2^attempt with jitter
        base = self.backoff_base
        factor = 2 ** attempt
        jitter = 0.1 * base
        return base * factor + (jitter * (os.getpid() % 7) / 7.0)

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            # Try to enrich with server JSON error if present
            detail = None
            try:
                j = resp.json()
                detail = j.get("detail") or j.get("error") or j
            except Exception:
                pass
            msg = f"HTTP {resp.status_code} for {resp.request.method} {resp.request.url}"
            if detail:
                msg += f" — {detail}"
            raise httpx.HTTPStatusError(msg, request=resp.request, response=resp) from e


__all__ = [
    "DAClient",
    "PostBlobResult",
    "BlobBytes",
    "ProofResult",
]
