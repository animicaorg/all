"""animica.mcp.seams — backend access for the Animica MCP server.

This is the *only* module that talks to the network, and the single seam that
tests mock (via ``respx`` on httpx, or by monkeypatching the functions here).

Every public Animica surface used by the MCP tools is reachable here:

  * ``v1_chat`` / ``v1_models`` — the OpenAI-compatible /v1 SaaS (chat, models).
  * ``rpc_call``               — node JSON-RPC, restricted to a hard allow-list
                                 of READ methods (defense in depth: a mutating
                                 method can never be reached, even by a bug).
  * ``pool_get``               — the public pool/mining stats API.
  * ``beacon_latest`` / ``beacon_round`` — the public quantum randomness beacon.

All endpoints are configured from the environment with public defaults, so the
server works out-of-the-box from a laptop (Claude Code plugin) and degrades to a
clear message when a backend is unreachable. Pure-compute tools never come here.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

DEFAULT_TIMEOUT = float(os.environ.get("ANIMICA_MCP_TIMEOUT_S", "60"))


class SeamError(Exception):
    """A backend (network / RPC / HTTP) error, with a human-friendly message."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------- #
# Endpoint resolution (read env each call so tests/CLI can override freely)
# --------------------------------------------------------------------------- #


def v1_base() -> str:
    return os.environ.get("ANIMICA_BASE_URL", "https://pool.animica.org/v1").rstrip("/")


def rpc_url() -> str:
    return os.environ.get("ANIMICA_RPC_URL", "https://rpc.animica.org/rpc").rstrip("/")


def pool_url() -> str:
    return os.environ.get("ANIMICA_POOL_URL", "https://pool.animica.org").rstrip("/")


def beacon_url() -> str:
    # The verifiable quantum beacon HTTP API (drand-style /beacon/* endpoints).
    return os.environ.get("ANIMICA_BEACON_URL", "https://pool.animica.org").rstrip("/")


def api_key() -> str:
    return os.environ.get("ANIMICA_API_KEY", "").strip()


# --------------------------------------------------------------------------- #
# Low-level HTTP (the two functions tests most often mock)
# --------------------------------------------------------------------------- #


def _get_json(url: str, *, params: Optional[dict] = None,
              headers: Optional[dict] = None, timeout: Optional[float] = None) -> Any:
    try:
        r = httpx.get(url, params=params, headers=headers or {"accept": "application/json"},
                      timeout=timeout or DEFAULT_TIMEOUT)
    except Exception as e:  # noqa: BLE001 — surface a clean message to the agent
        raise SeamError(f"could not reach {url}: {e}") from e
    if r.status_code >= 400:
        raise SeamError(f"{url} -> HTTP {r.status_code}: {r.text[:200]}", r.status_code)
    return r.json() if r.text else {}


def _post_json(url: str, body: dict, *, headers: Optional[dict] = None,
               timeout: Optional[float] = None) -> Any:
    hdrs = {"content-type": "application/json"}
    if headers:
        hdrs.update(headers)
    try:
        r = httpx.post(url, json=body, headers=hdrs, timeout=timeout or DEFAULT_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        raise SeamError(f"could not reach {url}: {e}") from e
    if r.status_code >= 400:
        try:
            j = r.json()
            msg = (j.get("error") or {}).get("message") or j.get("message") or r.text[:200]
        except Exception:  # noqa: BLE001
            msg = r.text[:200]
        raise SeamError(f"{url} -> HTTP {r.status_code}: {msg}", r.status_code)
    return r.json() if r.text else {}


# --------------------------------------------------------------------------- #
# JSON-RPC (READ-ONLY allow-list — the security boundary at the wire level)
# --------------------------------------------------------------------------- #

# Only these node methods may be called through the MCP server. Every method is a
# pure READ (or a deterministic compute that mutates nothing). Anything that
# signs, spends, submits, claims, funds, deploys or otherwise mutates state is
# intentionally absent and therefore impossible to invoke from a tool.
RPC_READ_ALLOWLIST: frozenset[str] = frozenset({
    # chain reads
    "chain.getHead", "chain.getChainId", "chain.getChainIdentity",
    "chain.getBlockByHeight", "chain.getBlockByHash", "chain.getBlockByNumber",
    "chain.getNetworkHashrate", "chain.getParams", "chain.getCheckpoints",
    # state reads (balances/nonces — read-only, no keys)
    "state.getBalance", "state.getAddressBalance", "state.getNonce",
    "state.getNextNonce", "state.getAccount", "state.getTotalSupply",
    # transaction reads (lookup only)
    "tx.get", "tx.getTransaction", "tx.getTransactionByHash",
    "tx.getReceipt", "tx.getTransactionReceipt", "tx.getStatus", "tx.getTransactionStatus",
    # ENA AI reads
    "ena.listModels", "ena.getActiveModel", "ena.getRequestStatus",
    "ena.getResult", "ena.getReceipt", "ena.getQuote",
    # AICF status reads
    "aicf.getStatus", "aicf.status", "aicf.summary", "aicf.jobStatus", "aicf.workerStatus",
    # AICF deployed-function registry reads (Studio)
    "aicf.fn.list", "aicf.fn.get",
    # quantum beacon reads + verifiable compute (mutate nothing)
    "quantum.getStatus", "rand.getQuantumStatus", "rand.getQuantumBeacon",
    "rand.quantumDraw", "rand.verifyQuantumResult",
})


def rpc_call(method: str, params: Any = None, *, timeout: Optional[float] = None) -> Any:
    """Call a node JSON-RPC method. Refuses any method not on the READ allow-list."""
    if method not in RPC_READ_ALLOWLIST:
        raise SeamError(
            f"RPC method {method!r} is not on the read-only allow-list; the Animica "
            f"MCP surface exposes READ/COMPUTE methods only."
        )
    payload = {"jsonrpc": "2.0", "id": 1, "method": method,
               "params": params if params is not None else {}}
    data = _post_json(rpc_url(), payload, timeout=timeout)
    if isinstance(data, dict) and data.get("error"):
        err = data["error"]
        raise SeamError(f"{method}: {err.get('message', err)} (code {err.get('code')})")
    return data.get("result") if isinstance(data, dict) else data


# --------------------------------------------------------------------------- #
# OpenAI-compatible /v1 SaaS (chat + models)
# --------------------------------------------------------------------------- #


def v1_chat(messages: list[dict], *, model: Optional[str] = None,
            max_tokens: int = 1024, temperature: float = 0.4,
            timeout: Optional[float] = None) -> str:
    """ENA inference via the OpenAI-compatible /v1/chat/completions endpoint."""
    headers = {}
    key = api_key()
    if key:
        headers["authorization"] = f"Bearer {key}"
    body = {
        "model": model or os.environ.get("ANIMICA_MODEL", "anm-fast-8b"),
        "messages": messages, "max_tokens": max_tokens,
        "temperature": temperature, "stream": False,
    }
    data = _post_json(f"{v1_base()}/chat/completions", body, headers=headers, timeout=timeout)
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "")


def v1_models(*, timeout: Optional[float] = None) -> Any:
    headers = {}
    key = api_key()
    if key:
        headers["authorization"] = f"Bearer {key}"
    return _get_json(f"{v1_base()}/models", headers=headers, timeout=timeout)


# --------------------------------------------------------------------------- #
# Pool / mining stats (public, read-only)
# --------------------------------------------------------------------------- #


def pool_get(path: str, *, timeout: Optional[float] = None) -> Any:
    if not path.startswith("/"):
        path = "/" + path
    return _get_json(f"{pool_url()}{path}", timeout=timeout)


# --------------------------------------------------------------------------- #
# Quantum randomness beacon (public, verifiable)
# --------------------------------------------------------------------------- #


def beacon_latest(*, timeout: Optional[float] = None) -> Any:
    return _get_json(f"{beacon_url()}/beacon/latest", timeout=timeout)


def beacon_round(round_id: int, *, timeout: Optional[float] = None) -> Any:
    return _get_json(f"{beacon_url()}/beacon/round/{int(round_id)}", timeout=timeout)
