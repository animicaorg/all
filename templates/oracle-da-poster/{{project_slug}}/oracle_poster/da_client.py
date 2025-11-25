"""
DA Client: minimal, dependency-free helpers to post blobs to a Data Availability
endpoint and (optionally) verify light commitments.

This template supports two modes out of the box:

1) "services" mode (default): POST JSON to a REST-like endpoint
     POST {services_url}/da/post
     body: {
       "payload_b64": "<base64>",
       "content_type": "application/json",
       "commitment": "0x…sha256…"
     }

   An optional light-verify endpoint is also attempted if you call
   `verify_commitment_light`, at:
     GET  {services_url}/da/verify?commitment=0x…  (or POST JSON)

2) "rpc" mode: JSON-RPC over HTTP(s) to the node RPC
     method: "da_postBlob"
     params: { "payload_b64": "<base64>", "content_type": "...", "commitment": "0x…" }

Both are intentionally simple so you can align them with your infra.
If your stack uses different paths or method names, tweak the defaults below.

No external dependencies are used; only the standard library.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Dict, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from . import PosterEnv, get_logger

_LOG = get_logger("oracle_poster.da_client")


# --------------------------------------------------------------------------------------
# Errors & Results
# --------------------------------------------------------------------------------------

class DAClientError(RuntimeError):
    """Raised on DA posting / verification issues."""


@dataclass(frozen=True)
class PostResult:
    """
    Outcome of a DA post attempt.

    Attributes:
        ok:            Whether the post succeeded as far as the client can tell.
        commitment:    0x-prefixed hex sha256(payload). Echoed back or computed locally.
        size:          Payload length in bytes.
        content_type:  Content-Type sent alongside the blob.
        endpoint:      URL used (for "services") or RPC URL (for "rpc").
        status_code:   HTTP status (services) or 200/None in RPC mode.
        blob_id:       Optional server-provided identifier/handle.
        response:      Parsed JSON response (if available).
        elapsed_ms:    Client-side elapsed time in milliseconds.
    """
    ok: bool
    commitment: str
    size: int
    content_type: str
    endpoint: str
    status_code: Optional[int]
    blob_id: Optional[str]
    response: Optional[Dict[str, Any]]
    elapsed_ms: int


# --------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------

def _commitment_hex(payload: bytes) -> str:
    return "0x" + sha256(payload).hexdigest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _request_json(url: str, *, method: str, json_body: Optional[Dict[str, Any]], timeout: int) -> Tuple[int, Dict[str, Any]]:
    """
    Perform an HTTP request with JSON body/response using stdlib.
    Returns (status_code, parsed_json). Raises DAClientError on network/parse errors.
    """
    body_bytes: Optional[bytes] = None
    headers = {"Content-Type": "application/json"}
    if json_body is not None:
        body_bytes = json.dumps(json_body).encode("utf-8")
    req = Request(url=url, data=body_bytes, headers=headers, method=method.upper())
    try:
        with urlopen(req, timeout=timeout) as resp:
            status = getattr(resp, "status", 200)  # Py3.8 compat
            raw = resp.read()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError as e:
                raise DAClientError(f"Non-JSON response from {url} (status {status}): {raw[:256]!r}") from e
            return int(status), parsed
    except HTTPError as e:
        raw = e.read()
        msg = raw.decode("utf-8", errors="replace")
        raise DAClientError(f"HTTPError {e.code} for {url}: {msg}") from e
    except URLError as e:
        raise DAClientError(f"URLError for {url}: {e}") from e


def _jsonrpc(url: str, *, method: str, params: Dict[str, Any], timeout: int) -> Dict[str, Any]:
    """
    Minimal JSON-RPC 2.0 client. Returns parsed JSON or raises DAClientError.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000) & 0x7FFFFFFF,
        "method": method,
        "params": params,
    }
    status, parsed = _request_json(url, method="POST", json_body=payload, timeout=timeout)
    if "error" in parsed:
        err = parsed["error"]
        raise DAClientError(f"JSON-RPC error calling {method}: {err}")
    if "result" not in parsed:
        raise DAClientError(f"Malformed JSON-RPC response from {url}: {parsed}")
    return parsed["result"]


# --------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------

class DAClient:
    """
    A flexible DA client supporting both REST-like "services" mode and "rpc" mode.

    Configuration (PosterEnv expected fields):
        - da_mode:            "services" (default) or "rpc"
        - services_url:       Base URL for services (e.g., https://services.devnet.example.com)
        - rpc_url:            Node RPC URL for JSON-RPC (e.g., https://rpc.devnet.example.com)
        - http_timeout_sec:   Timeout for HTTP/JSON-RPC requests
        - da_post_path:       Optional override for services mode (default: "/da/post")
        - da_verify_path:     Optional path for light verify (default: "/da/verify")
        - da_max_blob_bytes:  Upper bound enforced client-side (feeds also enforces)
    """

    def __init__(self, cfg: PosterEnv) -> None:
        self.cfg = cfg
        self.mode = (getattr(cfg, "da_mode", None) or "services").strip().lower()
        self.services_url = (getattr(cfg, "services_url", None) or "").rstrip("/")
        self.rpc_url = getattr(cfg, "rpc_url", None)
        self.post_path = getattr(cfg, "da_post_path", "/da/post")
        self.verify_path = getattr(cfg, "da_verify_path", "/da/verify")
        self.timeout = int(max(1, getattr(cfg, "http_timeout_sec", 10)))

        if self.mode not in ("services", "rpc"):
            _LOG.warning("Unknown da_mode=%r; defaulting to 'services'", self.mode)
            self.mode = "services"

        if self.mode == "services" and not self.services_url:
            _LOG.warning("da_mode='services' but services_url is empty.")
        if self.mode == "rpc" and not self.rpc_url:
            _LOG.warning("da_mode='rpc' but rpc_url is empty.")

    # ------------------------------------------------------------------ Public API

    def post_blob(
        self,
        *,
        payload: bytes,
        content_type: str,
        commitment: Optional[str] = None,
    ) -> PostResult:
        """
        Post a blob to DA using the configured mode.

        Returns a PostResult with useful context. Raises DAClientError on failure.
        """
        if commitment is None:
            commitment = _commitment_hex(payload)

        size = len(payload)
        if size > getattr(self.cfg, "da_max_blob_bytes", 10 * 1024 * 1024):
            raise DAClientError(
                f"Blob size {size} exceeds da_max_blob_bytes={self.cfg.da_max_blob_bytes}"
            )

        started = time.time()
        if self.mode == "services":
            endpoint = f"{self.services_url}{self.post_path}"
            status, parsed = _post_via_services(
                endpoint=endpoint,
                timeout=self.timeout,
                payload=payload,
                content_type=content_type,
                commitment=commitment,
            )
            elapsed_ms = int((time.time() - started) * 1000)
            ok = bool(parsed.get("ok", status == 200))
            blob_id = parsed.get("blob_id") or parsed.get("id")
            return PostResult(
                ok=ok,
                commitment=commitment,
                size=size,
                content_type=content_type,
                endpoint=endpoint,
                status_code=status,
                blob_id=blob_id,
                response=parsed,
                elapsed_ms=elapsed_ms,
            )

        # RPC mode
        endpoint = self.rpc_url or ""
        result = _post_via_rpc(
            rpc_url=endpoint,
            timeout=self.timeout,
            payload=payload,
            content_type=content_type,
            commitment=commitment,
        )
        elapsed_ms = int((time.time() - started) * 1000)
        blob_id = None
        if isinstance(result, dict):
            blob_id = result.get("blob_id") or result.get("id")
        return PostResult(
            ok=True,
            commitment=commitment,
            size=size,
            content_type=content_type,
            endpoint=endpoint,
            status_code=200,
            blob_id=blob_id,
            response=result if isinstance(result, dict) else {"result": result},
            elapsed_ms=elapsed_ms,
        )

    def verify_commitment_light(self, commitment: str) -> bool:
        """
        Try a best-effort "light" check with the services endpoint.

        Returns True if the endpoint reports the commitment as known/available,
        False if it reports missing; raises DAClientError on connectivity errors.

        In "rpc" mode, this method returns False unless you adapt it to your stack.
        """
        if self.mode != "services":
            _LOG.info("verify_commitment_light: not implemented for da_mode=%s", self.mode)
            return False

        endpoint = f"{self.services_url}{self.verify_path}"
        # Prefer GET ?commitment=0x…, fallback to POST JSON.
        qs = urlencode({"commitment": commitment})
        url = f"{endpoint}?{qs}"
        try:
            status, parsed = _request_json(url, method="GET", json_body=None, timeout=self.timeout)
            if "ok" in parsed and parsed.get("ok") is True:
                return True
            # Accept boolean result field too
            if isinstance(parsed.get("result"), bool):
                return bool(parsed["result"])
            if parsed.get("status") == "present":
                return True
            return False
        except DAClientError:
            # Try POST fallback
            status, parsed = _request_json(
                endpoint,
                method="POST",
                json_body={"commitment": commitment},
                timeout=self.timeout,
            )
            if "ok" in parsed and parsed.get("ok") is True:
                return True
            if isinstance(parsed.get("result"), bool):
                return bool(parsed["result"])
            if parsed.get("status") == "present":
                return True
            return False


# --------------------------------------------------------------------------------------
# Backends
# --------------------------------------------------------------------------------------

def _post_via_services(
    *,
    endpoint: str,
    timeout: int,
    payload: bytes,
    content_type: str,
    commitment: str,
) -> Tuple[int, Dict[str, Any]]:
    """
    REST-like posting:
      POST endpoint
      JSON body with base64 payload.
    """
    body = {
        "payload_b64": _b64(payload),
        "content_type": content_type,
        "commitment": commitment,
    }
    _LOG.debug("POST %s (bytes=%d, type=%s)", endpoint, len(payload), content_type)
    return _request_json(endpoint, method="POST", json_body=body, timeout=timeout)


def _post_via_rpc(
    *,
    rpc_url: str,
    timeout: int,
    payload: bytes,
    content_type: str,
    commitment: str,
) -> Dict[str, Any]:
    """
    JSON-RPC posting: method 'da_postBlob'.
    """
    params = {
        "payload_b64": _b64(payload),
        "content_type": content_type,
        "commitment": commitment,
    }
    _LOG.debug("JSON-RPC da_postBlob -> %s (bytes=%d, type=%s)", rpc_url, len(payload), content_type)
    return _jsonrpc(rpc_url, method="da_postBlob", params=params, timeout=timeout)
