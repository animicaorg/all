"""
omni_sdk.randomness.client
==========================

Client for the randomness beacon RPC surface:

- rand.getParams      → current parameters (windows, VDF profile, etc.)
- rand.getRound       → current round status (ids, windows, counts)
- rand.commit         → submit a commitment {salt, payload}
- rand.reveal         → reveal a prior commitment {salt, payload}
- rand.getBeacon      → latest (or specific) beacon output
- rand.getHistory     → list of recent beacon outputs

This is a thin, typed wrapper around the node's JSON-RPC API. It can talk
through an omni_sdk RPC client (with `.call(method, params)`) or directly to
`<base_url>/rpc`.

It also provides a convenience `compute_commitment(...)` that mirrors the
spec's domain-separated hashing, **for UX only**. The node remains the
source of truth for acceptance and binding rules.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Union
from urllib.parse import urljoin

try:
    import requests
except Exception as _e:  # pragma: no cover
    raise RuntimeError("requests is required for omni_sdk.randomness.client") from _e

# --- Utilities ---------------------------------------------------------------

# Hex/bytes helpers (fall back if omni_sdk.utils isn't present yet)
try:
    from omni_sdk.utils.bytes import to_hex as _to_hex, from_hex as _from_hex  # type: ignore
except Exception:  # pragma: no cover
    def _to_hex(b: bytes) -> str:
        return "0x" + bytes(b).hex()
    def _from_hex(s: str) -> bytes:
        s = s[2:] if isinstance(s, str) and s.startswith("0x") else s
        return bytes.fromhex(s)

# Hash helper (for local commitment preview)
try:
    from omni_sdk.utils.hash import sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib as _hashlib
    def sha3_256(data: bytes) -> bytes:
        return _hashlib.sha3_256(data).digest()

# Errors
try:
    from omni_sdk.errors import RpcError  # type: ignore
except Exception:  # pragma: no cover
    class RpcError(RuntimeError): ...

Json = Dict[str, Any]


def _detect_base_url(rpc_or_url: Union[str, Any]) -> Optional[str]:
    if isinstance(rpc_or_url, str):
        return rpc_or_url
    for attr in ("base_url", "endpoint", "url"):
        v = getattr(rpc_or_url, attr, None)
        if isinstance(v, str) and v:
            return v
    return None


@dataclass(frozen=True)
class _RPCCompat:
    call_fn: Any
    base_url: Optional[str]
    session: requests.Session


def _wrap_rpc(
    rpc_or_url: Union[str, Any],
    *,
    session: Optional[requests.Session],
    timeout_s: float,
) -> _RPCCompat:
    # Prefer rich client with .call(method, params)
    if not isinstance(rpc_or_url, str) and hasattr(rpc_or_url, "call"):
        return _RPCCompat(
            call_fn=getattr(rpc_or_url, "call"),
            base_url=_detect_base_url(rpc_or_url),
            session=session or requests.Session(),
        )

    base = _detect_base_url(rpc_or_url) if not isinstance(rpc_or_url, str) else rpc_or_url
    if not isinstance(base, str) or not base:
        raise ValueError("RandomnessClient needs an RPC object with .call(...) or a base URL string")

    rpc_url = urljoin(base.rstrip("/") + "/", "rpc")
    sess = session or requests.Session()

    def _direct_call(method: str, params: Any = None) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
        try:
            resp = sess.post(rpc_url, json=payload, timeout=timeout_s)
        except requests.RequestException as e:  # pragma: no cover
            raise RpcError(f"POST {rpc_url} failed: {e}") from e
        try:
            data = resp.json()
        except Exception as e:
            raise RpcError(f"Invalid JSON-RPC response from {rpc_url}: {resp.text[:256]}") from e
        if "error" in data and data["error"]:
            raise RpcError(f"RPC {method} failed: {data['error']}")
        return data.get("result")

    return _RPCCompat(call_fn=_direct_call, base_url=base, session=sess)


# --- Client ------------------------------------------------------------------

class RandomnessClient:
    """
    Randomness beacon client.

    Parameters
    ----------
    rpc_or_url : str | RPC client with `.call(method, params)`
        Base URL string (e.g., "http://127.0.0.1:8545") or an omni_sdk RPC client.
    timeout_s : float
        Network timeout used in direct JSON-RPC mode.
    session : requests.Session | None
        Optional custom session for direct JSON-RPC mode.
    """

    # Domain tag used for local commitment preview (UX helper only).
    _DOMAIN_COMMIT = b"animica:rand.commit.v1"

    def __init__(
        self,
        rpc_or_url: Union[str, Any],
        *,
        timeout_s: float = 30.0,
        session: Optional[requests.Session] = None,
    ) -> None:
        self._timeout = float(timeout_s)
        self._rpc = _wrap_rpc(rpc_or_url, session=session, timeout_s=self._timeout)

    # ---- Read APIs -----------------------------------------------------------

    def get_params(self) -> Json:
        """Return current randomness parameters."""
        res = self._rpc.call_fn("rand.getParams", {})
        if not isinstance(res, Mapping):
            raise RpcError("rand.getParams: invalid response")
        return dict(res)

    def get_round(self) -> Json:
        """Return current round info (ids, windows, counts)."""
        res = self._rpc.call_fn("rand.getRound", {})
        if not isinstance(res, Mapping):
            raise RpcError("rand.getRound: invalid response")
        return dict(res)

    def get_beacon(self, round_id: Optional[int] = None) -> Json:
        """
        Return the latest beacon, or the one for `round_id` if provided.

        Response usually includes fields like:
        { "round": N, "output": "0x...", "vdf": {...}, "prev": "0x...", ... }
        """
        params = {} if round_id is None else {"round": int(round_id)}
        res = self._rpc.call_fn("rand.getBeacon", params)
        if not isinstance(res, Mapping):
            raise RpcError("rand.getBeacon: invalid response")
        return dict(res)

    def get_history(self, *, start: Optional[int] = None, limit: int = 10) -> Sequence[Json]:
        """Return recent beacons (descending or server-defined order)."""
        params: Json = {"limit": int(limit)}
        if start is not None:
            params["start"] = int(start)
        res = self._rpc.call_fn("rand.getHistory", params)
        if not isinstance(res, list):
            raise RpcError("rand.getHistory: invalid response")
        return [dict(x) for x in res if isinstance(x, Mapping)]

    # ---- Write-ish (RPC) APIs ------------------------------------------------

    def commit(
        self,
        *,
        salt: Union[bytes, bytearray, memoryview, str],
        payload: Union[bytes, bytearray, memoryview, str],
        account: Optional[str] = None,
    ) -> Json:
        """
        Submit a commitment for the current (open) round.

        Parameters
        ----------
        salt : bytes | hex str
            Secret salt (kept until reveal). If str, must be hex 0x…
        payload : bytes | hex str
            Entropy payload (can be public or derived). If str, hex 0x…
        account : Optional[str]
            Optional caller/account/address hint (if the node expects it).

        Returns
        -------
        dict: server's CommitRecord (shape defined by the node).
        """
        salt_hex = salt if isinstance(salt, str) else _to_hex(bytes(salt))
        payload_hex = payload if isinstance(payload, str) else _to_hex(bytes(payload))
        params: Json = {"salt": salt_hex, "payload": payload_hex}
        if account:
            params["from"] = account
        res = self._rpc.call_fn("rand.commit", params)
        if not isinstance(res, Mapping):
            raise RpcError("rand.commit: invalid response")
        return dict(res)

    def reveal(
        self,
        *,
        salt: Union[bytes, bytearray, memoryview, str],
        payload: Union[bytes, bytearray, memoryview, str],
        account: Optional[str] = None,
    ) -> Json:
        """
        Reveal a prior commitment for the current (reveal) window.

        Returns
        -------
        dict: server's RevealRecord.
        """
        salt_hex = salt if isinstance(salt, str) else _to_hex(bytes(salt))
        payload_hex = payload if isinstance(payload, str) else _to_hex(bytes(payload))
        params: Json = {"salt": salt_hex, "payload": payload_hex}
        if account:
            params["from"] = account
        res = self._rpc.call_fn("rand.reveal", params)
        if not isinstance(res, Mapping):
            raise RpcError("rand.reveal: invalid response")
        return dict(res)

    # ---- Convenience helpers -------------------------------------------------

    def compute_commitment(
        self,
        *,
        salt: Union[bytes, bytearray, memoryview, str],
        payload: Union[bytes, bytearray, memoryview, str],
        account: Optional[str] = None,
        domain_tag: Optional[bytes] = None,
    ) -> str:
        """
        Locally compute a domain-separated commitment preview.

        NOTE: This helper is for UX only; consensus rules may add more fields
        (e.g., address binding) and the node's acceptance is authoritative.

        C = sha3_256(domain || account? || salt || payload)

        Returns
        -------
        hex string (0x…)
        """
        dom = bytes(domain_tag or self._DOMAIN_COMMIT)
        s = salt if isinstance(salt, (bytes, bytearray, memoryview)) else _from_hex(str(salt))
        p = payload if isinstance(payload, (bytes, bytearray, memoryview)) else _from_hex(str(payload))
        parts = [dom]
        if account:
            # Bind textual address bytes as-is (already normalized upstream)
            parts.append(account.encode("utf-8"))
        parts.extend([bytes(s), bytes(p)])
        digest = sha3_256(b"".join(parts))
        return _to_hex(digest)

    def wait_for_beacon(
        self,
        *,
        target_round: Optional[int] = None,
        timeout_s: float = 120.0,
        poll_interval_s: float = 1.0,
    ) -> Json:
        """
        Poll until a new beacon is finalized (or until `target_round` is reached).

        Returns the beacon object from `rand.getBeacon`.
        """
        deadline = time.monotonic() + float(timeout_s)
        start_round = None
        try:
            info = self.get_round()
            start_round = int(info.get("round", 0))
        except Exception:
            pass

        while True:
            bea = self.get_beacon()
            r = int(bea.get("round", 0))
            if (target_round is None and start_round is not None and r > start_round) or (
                target_round is not None and r >= int(target_round)
            ):
                return bea
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for beacon")
            time.sleep(float(poll_interval_s))


__all__ = ["RandomnessClient"]
