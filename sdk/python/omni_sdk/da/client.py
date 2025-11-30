"""
omni_sdk.da.client
==================

High-level client for the DA (Data Availability) retrieval API mounted by the node.

The DA retrieval service (mounted via ``da/adapters/rpc_mount.py``) exposes REST
endpoints:

- **POST /da/blob**: store a blob under a namespace, returning a commitment and receipt
- **GET  /da/blob/{commitment}**: fetch the raw blob bytes by commitment
- **GET  /da/blob/{commitment}/proof**: fetch an availability proof (server-verified)

This client wraps those endpoints and provides a small, typed surface.

Typical usage
-------------
    from omni_sdk.rpc.http import HttpClient
    from omni_sdk.da.client import DAClient

    rpc = HttpClient("http://127.0.0.1:8545")
    da = DAClient(rpc)

    commit, receipt = da.post_blob(namespace=24, data=b"hello")
    data = da.get_blob(commit)
    ok = da.verify_availability(commit)

Design notes
------------
* Uses deterministic hex encoding (0x…) for bytes in JSON bodies.
* Tries to interoperate with either an ``HttpClient`` (with a ``base_url`` attribute)
  or a plain base URL string.
* Conservatively interprets the proof response. If the server returns a boolean
  field such as ``ok``/``available``/``verified`` it will be honored. Otherwise
  the presence of a non-empty, well-formed proof object will be treated as True.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple, Union
from urllib.parse import urljoin

try:
    import requests
except Exception as _e:  # pragma: no cover
    raise RuntimeError("requests is required for omni_sdk.da.client") from _e

# Hex/bytes helpers
try:
    from omni_sdk.utils.bytes import from_hex as _from_hex
    from omni_sdk.utils.bytes import to_hex as _to_hex  # type: ignore
except Exception:

    def _to_hex(b: bytes) -> str:
        return "0x" + bytes(b).hex()

    def _from_hex(s: str) -> bytes:
        s = s[2:] if isinstance(s, str) and s.startswith("0x") else s
        return bytes.fromhex(s)


# Errors surface
try:
    from omni_sdk.errors import RpcError  # type: ignore
except Exception:  # pragma: no cover

    class RpcError(RuntimeError): ...


JsonDict = Dict[str, Any]


@dataclass(frozen=True)
class DAEndpoints:
    """Resolved endpoint paths."""

    post_blob: str
    get_blob: str  # format: f"/da/blob/{commitment}"
    get_proof: str  # format: f"/da/blob/{commitment}/proof"


def _detect_base_url(rpc_or_url: Union[str, Any]) -> str:
    """
    Accept either a base URL string or an RPC client with a `.base_url` attribute.
    """
    if isinstance(rpc_or_url, str):
        return rpc_or_url
    # Common attribute on omni_sdk.rpc.http.HttpClient
    for attr in ("base_url", "endpoint", "url"):
        v = getattr(rpc_or_url, attr, None)
        if isinstance(v, str) and v:
            return v
    # Fallback: try stringifying (last resort)
    if hasattr(rpc_or_url, "__str__"):
        s = str(rpc_or_url)
        if s.startswith("http://") or s.startswith("https://"):
            return s
    raise ValueError(
        "DAClient needs a base URL string or an RPC client exposing .base_url"
    )


class DAClient:
    """
    Client for DA blob post/get/proof endpoints.

    Parameters
    ----------
    rpc_or_url : str | RPC client
        Base HTTP URL to the node (e.g., "http://127.0.0.1:8545"), or an RPC client
        that exposes `.base_url`.
    timeout_s : float
        Default timeout for HTTP operations.
    session : requests.Session | None
        Optional custom session. If not provided, a new session is created.
    """

    def __init__(
        self,
        rpc_or_url: Union[str, Any],
        *,
        timeout_s: float = 30.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        base = _detect_base_url(rpc_or_url).rstrip("/") + "/"
        self._base_url = base
        self._timeout = float(timeout_s)
        self._http = session or requests.Session()
        # Resolve endpoints
        self._ep = DAEndpoints(
            post_blob="da/blob",
            get_blob="da/blob/{commitment}",
            get_proof="da/blob/{commitment}/proof",
        )

    # ---- Helpers -------------------------------------------------------------

    def _abs(self, path: str) -> str:
        return urljoin(self._base_url, path)

    @staticmethod
    def _ns_to_int(namespace: Union[int, bytes, str]) -> int:
        if isinstance(namespace, int):
            if namespace < 0:
                raise ValueError("namespace must be non-negative")
            return namespace
        if isinstance(namespace, (bytes, bytearray)):
            # Interpret as big-endian integer
            return int.from_bytes(bytes(namespace), "big", signed=False)
        if isinstance(namespace, str):
            # Hex or decimal string
            if namespace.startswith("0x"):
                return int(namespace, 16)
            return int(namespace, 10)
        raise TypeError("namespace must be int | bytes | hex/decimal str")

    # ---- Public API ----------------------------------------------------------

    def post_blob(
        self,
        *,
        namespace: Union[int, bytes, str],
        data: Union[bytes, bytearray, memoryview, str, os.PathLike],
        mime: Optional[str] = None,
        pin: bool = True,
    ) -> Tuple[str, JsonDict]:
        """
        Store a blob; return (commitment_hex, receipt_dict).

        Parameters
        ----------
        namespace : int | bytes | str
            Namespace id. If bytes, interpreted big-endian.
        data : bytes | str | PathLike
            Blob content. If `str` or `PathLike`, the referenced file will be read.
        mime : Optional[str]
            Optional MIME type hint stored in metadata.
        pin : bool
            If True (default), request the node to pin the blob for retention.

        Returns
        -------
        (commitment_hex, receipt)
        """
        if isinstance(data, (str, os.PathLike)):
            with open(data, "rb") as f:
                data_bytes = f.read()
        else:
            data_bytes = bytes(data)

        body: JsonDict = {
            "namespace": self._ns_to_int(namespace),
            "data": _to_hex(data_bytes),
            "pin": bool(pin),
        }
        if mime:
            body["mime"] = str(mime)

        url = self._abs(self._ep.post_blob)
        try:
            resp = self._http.post(url, json=body, timeout=self._timeout)
        except requests.RequestException as e:  # pragma: no cover
            raise RpcError(f"POST {url} failed: {e}") from e
        if resp.status_code // 100 != 2:
            raise RpcError(f"POST {url} -> {resp.status_code}: {resp.text}")
        try:
            payload = resp.json()
        except Exception as e:
            raise RpcError("DA post: expected JSON response") from e

        commit = (
            payload.get("commitment") or payload.get("commit") or payload.get("root")
        )
        receipt = payload.get("receipt") or {}
        if not isinstance(commit, str):
            raise RpcError("DA post: server did not return a commitment string")
        if not isinstance(receipt, dict):
            receipt = {"raw": payload}
        return commit, receipt

    def get_blob(self, commitment: Union[str, bytes]) -> bytes:
        """
        Retrieve the raw blob bytes by commitment.

        Parameters
        ----------
        commitment : hex string or bytes
            The blob commitment (NMT root). A bytes value will be hex-encoded.

        Returns
        -------
        bytes : blob content
        """
        commit_hex = commitment if isinstance(commitment, str) else _to_hex(commitment)
        url = self._abs(self._ep.get_blob.format(commitment=commit_hex))
        headers = {"Accept": "application/octet-stream"}
        try:
            resp = self._http.get(
                url, headers=headers, timeout=self._timeout, stream=True
            )
        except requests.RequestException as e:  # pragma: no cover
            raise RpcError(f"GET {url} failed: {e}") from e

        # If server returns JSON envelope, support that as well
        ctype = resp.headers.get("Content-Type", "")
        if resp.status_code // 100 != 2:
            # Try to surface JSON error details if present
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text
            raise RpcError(f"GET {url} -> {resp.status_code}: {detail}")

        if "application/json" in ctype:
            try:
                payload = resp.json()
            except Exception as e:
                raise RpcError("DA get: invalid JSON response") from e
            data_hex = payload.get("data")
            if not isinstance(data_hex, str):
                raise RpcError("DA get: server JSON missing 'data'")
            return _from_hex(data_hex)

        # Otherwise, treat body as raw bytes
        return resp.content

    def get_proof(self, commitment: Union[str, bytes]) -> JsonDict:
        """
        Retrieve an availability proof object for a commitment.

        Returns the raw JSON proof payload as provided by the server.
        """
        commit_hex = commitment if isinstance(commitment, str) else _to_hex(commitment)
        url = self._abs(self._ep.get_proof.format(commitment=commit_hex))
        try:
            resp = self._http.get(
                url, headers={"Accept": "application/json"}, timeout=self._timeout
            )
        except requests.RequestException as e:  # pragma: no cover
            raise RpcError(f"GET {url} failed: {e}") from e
        if resp.status_code // 100 != 2:
            raise RpcError(f"GET {url} -> {resp.status_code}: {resp.text}")
        try:
            proof = resp.json()
        except Exception as e:
            raise RpcError("DA proof: expected JSON response") from e
        if not isinstance(proof, dict):
            raise RpcError("DA proof: server returned non-object JSON")
        return proof

    def verify_availability(self, commitment: Union[str, bytes]) -> bool:
        """
        Ask the server for an availability proof and interpret it as True/False.

        This does NOT implement full light-client verification locally; it relies
        on the server response semantics until a local verifier is wired here.
        """
        proof = self.get_proof(commitment)
        # Common boolean fields
        for key in ("ok", "available", "verified", "is_available"):
            v = proof.get(key)
            if isinstance(v, bool):
                return v
        # Heuristic: a non-empty proof with expected keys implies success
        keys = {"samples", "branches", "root", "namespace"}
        if any(k in proof for k in keys):
            # Assume server only returns proof if verification passed server-side
            return True
        return False


__all__ = ["DAClient"]
