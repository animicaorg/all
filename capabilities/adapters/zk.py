"""
capabilities.adapters.zk
=======================

Thin adapter that routes zero-knowledge proof verification requests to one of:
  1) A provided *service* object (duck-typed) with `verify(circuit, proof, public_input, **opts)`
     or `zk_verify(...)` that returns a bool or a dict.
  2) A JSON-RPC endpoint exposing one of:
       - "zk.verify"
       - "zk.verifyProof"
       - "capabilities.zkVerify"
     which returns a boolean or a result-object containing `ok: bool`.
  3) A deterministic local stub (for tests/dev) that checks an optional
     `expected_challenge` field on the circuit. The stub computes:
         challenge = sha3_256( json_dumps(public_input) || proof_bytes )
     and succeeds iff it matches `expected_challenge` (hex, "0x" allowed).
     You can force enabling the stub by setting ZK_LOCAL_STUB=1 (used if
     no service/endpoint is configured). To *always* pass in dev, set
     ZK_LOCAL_ACCEPT=1 (NOT for production).

Return shape (normalized):

    {
      "ok": bool,
      "units": int,          # verifier cost units (best-effort / 0 if unknown)
      "scheme": str | None,  # e.g., "groth16", "plonk", ...
      "provider": "service" | "rpc" | "local",
      "details": dict        # backend-specific extras
    }

This module uses only the Python stdlib to keep it lightweight.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from typing import Any, Dict, Optional, Tuple, Union
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

__all__ = [
    "ZKAdapter",
    "verify",
    "normalize_result",
]

HexLike = Union[str, bytes, bytearray, memoryview]


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _as_hex(data: HexLike) -> str:
    if isinstance(data, (bytes, bytearray, memoryview)):
        return "0x" + bytes(data).hex()
    s = str(data)
    return s if s.startswith("0x") else "0x" + s.encode().hex()


def _from_hex(x: HexLike) -> bytes:
    if isinstance(x, (bytes, bytearray, memoryview)):
        return bytes(x)
    s = str(x).strip()
    if s.startswith(("0x", "0X")):
        s = s[2:]
    try:
        return binascii.unhexlify(s)
    except binascii.Error as e:
        raise ValueError(f"Invalid hex: {x!r}") from e


def _sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(data).digest()


def _canonical_json(obj: Any) -> bytes:
    # Canonical-ish JSON for challenge derivation
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


# ---------------------------------------------------------------------------
# Minimal JSON-RPC shim (stdlib only)
# ---------------------------------------------------------------------------


class _JsonRpc:
    def __init__(self, url: str, api_key: Optional[str] = None, timeout: float = 30.0):
        self.url = url
        self.api_key = api_key
        self.timeout = timeout

    def call(self, method: str, params: Any) -> Any:
        payload = {
            "jsonrpc": "2.0",
            "id": "zk-verify",
            "method": method,
            "params": params,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "ignore")
            except Exception:
                pass
            raise RuntimeError(f"HTTP {e.code} calling {method}: {body}") from e
        except URLError as e:
            raise RuntimeError(f"Network error calling {method}: {e}") from e

        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception as e:
            preview = raw[:256]
            raise RuntimeError(f"Non-JSON response from RPC: {preview!r}") from e

        if "error" in obj:
            err = obj["error"]
            raise RuntimeError(f"RPC error {err.get('code')}: {err.get('message')}")
        return obj.get("result")


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class ZKAdapter:
    """
    Verifier facade. Tries a service object, then JSON-RPC, finally a local stub.
    """

    def __init__(
        self,
        *,
        service: Optional[Any] = None,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout_s: float = 30.0,
        allow_local_stub: Optional[bool] = None,
    ):
        self.service = service
        self.endpoint = endpoint or os.getenv("ZK_ENDPOINT")
        self.api_key = api_key or os.getenv("ZK_API_KEY")
        self.rpc = (
            _JsonRpc(self.endpoint, self.api_key, timeout_s) if self.endpoint else None
        )
        if allow_local_stub is None:
            allow_local_stub = os.getenv("ZK_LOCAL_STUB", "0") == "1"
        self.allow_local_stub = allow_local_stub

    # Public API -------------------------------------------------------------

    def verify(
        self,
        *,
        circuit: Union[Dict[str, Any], str],
        proof: Union[Dict[str, Any], bytes, str],
        public_input: Any,
        max_units: Optional[int] = None,
        scheme: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Verify a proof against a circuit and public input.

        Parameters
        ----------
        circuit : dict|str
            Circuit object or identifier. If dict, may include "scheme" and
            "expected_challenge" for local stub.
        proof : dict|bytes|hexstr
            Proof data; dicts/objects will be JSON-encoded for RPC/local stub.
        public_input : any
            Public inputs expected by the circuit.
        max_units : Optional[int]
            Optional limit communicated to remote verifier (best-effort).
        scheme : Optional[str]
            Hint for the verifier (e.g., "groth16", "plonk"); overrides circuit["scheme"].
        """
        # 1) Service (duck-typed)
        if self.service is not None:
            for name in ("verify", "zk_verify", "verify_proof"):
                fn = getattr(self.service, name, None)
                if callable(fn):
                    res = fn(circuit=circuit, proof=proof, public_input=public_input, max_units=max_units, scheme=scheme)  # type: ignore[misc]
                    return normalize_result(
                        res,
                        default_scheme=_get_scheme_hint(circuit, scheme),
                        provider="service",
                    )

        # 2) JSON-RPC
        if self.rpc:
            params = {
                "circuit": circuit,
                "proof": _prepare_proof_for_rpc(proof),
                "public_input": public_input,
                "max_units": max_units,
                "scheme": scheme or _get_scheme_hint(circuit, None),
            }
            for method in ("zk.verify", "zk.verifyProof", "capabilities.zkVerify"):
                try:
                    res = self.rpc.call(method, params)
                    return normalize_result(
                        res, default_scheme=params["scheme"], provider="rpc"
                    )
                except RuntimeError as e:
                    # try next method if "Method not found"
                    msg = str(e)
                    if "Method not found" in msg or "-32601" in msg:
                        continue
                    raise

        # 3) Local stub (tests/dev only)
        if self.allow_local_stub or os.getenv("ZK_LOCAL_ACCEPT") == "1":
            return _local_stub_verify(
                circuit=circuit, proof=proof, public_input=public_input, scheme=scheme
            )

        raise RuntimeError(
            "No ZK verifier backend available (service/endpoint missing and local stub disabled)."
        )


# ---------------------------------------------------------------------------
# Normalization & helpers
# ---------------------------------------------------------------------------


def _get_scheme_hint(
    circuit: Union[Dict[str, Any], str], override: Optional[str]
) -> Optional[str]:
    if override:
        return override
    if isinstance(circuit, dict):
        val = circuit.get("scheme") or circuit.get("alg") or circuit.get("proof_system")
        return str(val) if val else None
    return None


def _prepare_proof_for_rpc(proof: Union[Dict[str, Any], bytes, str]) -> Any:
    if isinstance(proof, (bytes, bytearray, memoryview)):
        return {
            "hex": "0x" + bytes(proof).hex(),
            "b64": base64.b64encode(bytes(proof)).decode("ascii"),
        }
    if isinstance(proof, str):
        # If hex, pass both forms; else treat as opaque string
        if proof.startswith(("0x", "0X")):
            b = _from_hex(proof)
            return {"hex": proof, "b64": base64.b64encode(b).decode("ascii")}
        return {"text": proof}
    # assume dict / JSON-encodable
    return proof


def normalize_result(
    res: Any, *, default_scheme: Optional[str], provider: str
) -> Dict[str, Any]:
    """
    Map various backend return shapes to the normalized result.
    """
    ok = False
    units = 0
    scheme = default_scheme
    details: Dict[str, Any] = {}

    if isinstance(res, bool):
        ok = res
    elif isinstance(res, dict):
        # common keys
        ok = bool(res.get("ok", res.get("valid", res.get("verified", False))))
        units = int(res.get("units", res.get("cost_units", res.get("gas", 0))) or 0)
        scheme = (
            str(res.get("scheme", scheme)) if res.get("scheme") is not None else scheme
        )
        details = {
            k: v
            for k, v in res.items()
            if k
            not in ("ok", "valid", "verified", "units", "cost_units", "gas", "scheme")
        }
    else:
        # Unknown type: keep raw
        details = {"raw": res}

    return {
        "ok": ok,
        "units": units,
        "scheme": scheme,
        "provider": provider,
        "details": details,
    }


# ---------------------------------------------------------------------------
# Local stub (deterministic, for tests/dev)
# ---------------------------------------------------------------------------


def _local_stub_verify(
    *,
    circuit: Union[Dict[str, Any], str],
    proof: Union[Dict[str, Any], bytes, str],
    public_input: Any,
    scheme: Optional[str],
) -> Dict[str, Any]:
    """
    Deterministic stub:

      - If env ZK_LOCAL_ACCEPT=1: always succeed (units=0).
      - Else, if circuit contains "expected_challenge" (hex), compute:

            challenge = sha3_256( canonical_json(public_input) || proof_bytes )

        and require equality (case-insensitive, with or without "0x").
        If `proof` is dict/string, its canonical JSON bytes are used.

      - If none of the above, return failure.

    This allows predictable tests without a heavy verifier.
    """
    if os.getenv("ZK_LOCAL_ACCEPT") == "1":
        return {
            "ok": True,
            "units": 0,
            "scheme": scheme or _get_scheme_hint(circuit, None),
            "provider": "local",
            "details": {"mode": "force-accept"},
        }

    # Normalize proof bytes for challenge
    if isinstance(proof, (bytes, bytearray, memoryview)):
        pbytes = bytes(proof)
    elif isinstance(proof, str):
        if proof.startswith(("0x", "0X")):
            pbytes = _from_hex(proof)
        else:
            pbytes = _canonical_json(proof)
    else:
        # dict/obj → canonical JSON
        pbytes = _canonical_json(proof)

    pub_enc = _canonical_json(public_input)
    challenge = _sha3_256(pub_enc + pbytes).hex()

    exp = None
    if isinstance(circuit, dict):
        exp = circuit.get("expected_challenge")
        if isinstance(exp, str):
            exp = exp.lower().removeprefix("0x")

    ok = (exp == challenge) if exp else False
    return {
        "ok": ok,
        "units": 0,
        "scheme": scheme or _get_scheme_hint(circuit, None),
        "provider": "local",
        "details": {
            "expected_challenge": ("0x" + exp) if isinstance(exp, str) else None,
            "challenge": "0x" + challenge,
            "note": "local stub comparison on sha3_256(JSON(public)||proof_bytes)",
        },
    }


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

_default_adapter: Optional[ZKAdapter] = None


def _get_default() -> ZKAdapter:
    global _default_adapter
    if _default_adapter is None:
        _default_adapter = ZKAdapter()
    return _default_adapter


def verify(
    *,
    circuit: Union[Dict[str, Any], str],
    proof: Union[Dict[str, Any], bytes, str],
    public_input: Any,
    max_units: Optional[int] = None,
    scheme: Optional[str] = None,
    service: Optional[Any] = None,
    endpoint: Optional[str] = None,
    api_key: Optional[str] = None,
    timeout_s: float = 30.0,
    allow_local_stub: Optional[bool] = None,
) -> Dict[str, Any]:
    """
    One-shot verification helper that constructs a temporary adapter if any
    backend overrides are provided; otherwise reuses the default adapter.
    """
    if (
        any(x is not None for x in (service, endpoint, api_key, allow_local_stub))
        or timeout_s != 30.0
    ):
        adapter = ZKAdapter(
            service=service,
            endpoint=endpoint,
            api_key=api_key,
            timeout_s=timeout_s,
            allow_local_stub=allow_local_stub,
        )
    else:
        adapter = _get_default()
    return adapter.verify(
        circuit=circuit,
        proof=proof,
        public_input=public_input,
        max_units=max_units,
        scheme=scheme,
    )
