from __future__ import annotations

"""
Animica Stratum Protocol (JSON-RPC over TCP)
============================================

Purpose
-------
Defines the *schema* (dataclasses, enums, error codes) and light helpers for
Animica's Stratum-style mining protocol. Transport is newline-delimited JSON
(default) or 4-byte big-endian length-prefixed frames (optional). This module
is pure-Python and dependency-free so both server and client can import it.

JSON-RPC 2.0 Envelope
---------------------
Requests:
  {"jsonrpc": "2.0", "id": 1, "method": "mining.subscribe", "params": {...}}
Responses:
  {"jsonrpc": "2.0", "id": 1, "result": {...}}
Errors:
  {"jsonrpc": "2.0", "id": 1, "error": {"code": -32004, "message": "Stale job"}}

Canonical Methods
-----------------
Client -> Server:
  - mining.subscribe       : identify client and negotiate features
  - mining.authorize       : authenticate/associate worker with address
  - mining.submit          : submit a candidate share or full block

Server -> Client:
  - mining.set_difficulty  : set/adjust share target (micro-target)
  - mining.notify          : push a new job (header template + hints)

Utility (either direction when appropriate):
  - mining.get_version     : exchange versions
  - mining.extranonce.subscribe : subscribe to extranonce updates (optional)

Params/Results (high level)
---------------------------
mining.subscribe
  params: { "agent": str, "features": {"framing": "lines|lenpref", "compress": bool}, "algo": "hashshare" }
  result: { "sessionId": str, "extranonce1": Hex, "extranonce2Size": int, "framing": "lines|lenpref" }

mining.authorize
  params: { "worker": str, "address": str, "signature": Hex? }
  result: { "ok": bool, "reason": str? }

mining.set_difficulty
  params: { "shareTarget": float, "thetaMicro": int }
  result: null

mining.notify
  params: {
    "jobId": str,
    "cleanJobs": bool,
    "header": { ...header template payload... },
    "shareTarget": float,
    "hints": {
      "mixSeed": Hex,
      "algPolicyRoot": Hex?,
      "proofCaps": {"ai": bool, "quantum": bool, "storage": bool, "vdf": bool}
    }
  }
  result: null

mining.submit
  params: {
    "worker": str,
    "jobId": str,
    "extranonce2": Hex,
    "hashshare": {
      "nonce": Hex,          # header nonce (domain-correct size)
      "mix": Hex,            # optional mix digest if computed by device
      "body": { ... }        # full HashShare body per proofs/schemas/hashshare.cddl
    },
    "attachments": {
      "ai": {...}?, "quantum": {...}?, "storage": {...}?, "vdf": {...}?
    }
  }
  result: { "accepted": bool, "reason": str?, "isBlock": bool?, "txCount": int? }

Errors
------
JSON-RPC standard: -32700, -32600, -32601, -32602, -32603
Animica Stratum custom (-32099..-32000):
  -32000 InternalError
  -32001 Unauthorized
  -32002 InvalidShare
  -32003 LowDifficultyShare
  -32004 StaleJob
  -32005 JobNotFound
  -32006 BadParams
  -32007 RateLimited
  -32008 BackendUnavailable

Framing
-------
lines   : UTF-8 JSON + '\n' (default)
lenpref : 4-byte big-endian length prefix + UTF-8 JSON body

This module exposes helpers for both:
  - encode_lines(obj) -> bytes
  - decode_lines(buffer) -> list[dict]
  - frame_lenpref(payload_bytes) -> bytes
  - unframe_lenpref(bytearray_buffer) -> list[bytes]

See also:
  mining/stratum_server.py  (server that uses this schema)
  mining/stratum_client.py  (reference client)
"""

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

JSON = Dict[str, Any]
Hex = str


# ---------------------- Error Codes ----------------------


class RpcErrorCodes(int, Enum):
    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603

    INTERNAL = -32000
    UNAUTHORIZED = -32001
    INVALID_SHARE = -32002
    LOW_DIFFICULTY = -32003
    STALE_JOB = -32004
    JOB_NOT_FOUND = -32005
    BAD_PARAMS = -32006
    RATE_LIMITED = -32007
    BACKEND_UNAVAILABLE = -32008


class StratumError(Exception):
    """Base exception for protocol validation and builder helpers."""

    code: int = RpcErrorCodes.INTERNAL

    def to_error_obj(self) -> JSON:
        return {
            "code": int(self.code),
            "message": self.__class__.__name__,
            "data": str(self),
        }


class InvalidRequest(StratumError):
    code = RpcErrorCodes.INVALID_REQUEST


class InvalidParams(StratumError):
    code = RpcErrorCodes.INVALID_PARAMS


class MethodNotFound(StratumError):
    code = RpcErrorCodes.METHOD_NOT_FOUND


# ---------------------- Methods ----------------------


class Method(str, Enum):
    SUBSCRIBE = "mining.subscribe"
    AUTHORIZE = "mining.authorize"
    SET_DIFFICULTY = "mining.set_difficulty"
    NOTIFY = "mining.notify"
    SUBMIT = "mining.submit"
    GET_VERSION = "mining.get_version"
    EXTRANONCE_SUBSCRIBE = "mining.extranonce.subscribe"


# ---------------------- Dataclasses (schema) ----------------------


@dataclass(frozen=True)
class SubscribeParams:
    agent: str
    features: JSON
    algo: str = "hashshare"


@dataclass(frozen=True)
class SubscribeResult:
    sessionId: str
    extranonce1: Hex
    extranonce2Size: int
    framing: str  # "lines" | "lenpref"


@dataclass(frozen=True)
class AuthorizeParams:
    worker: str
    address: str
    signature: Optional[Hex] = None  # chain-dependent domain; optional for dev


@dataclass(frozen=True)
class AuthorizeResult:
    ok: bool
    reason: Optional[str] = None


@dataclass(frozen=True)
class SetDifficultyParams:
    shareTarget: float
    thetaMicro: int


@dataclass(frozen=True)
class NotifyHints:
    mixSeed: Hex
    algPolicyRoot: Optional[Hex] = None
    proofCaps: Optional[JSON] = (
        None  # {"ai": bool, "quantum": bool, "storage": bool, "vdf": bool}
    )


@dataclass(frozen=True)
class NotifyParams:
    jobId: str
    cleanJobs: bool
    header: JSON
    shareTarget: float
    hints: Optional[NotifyHints] = None


@dataclass(frozen=True)
class SubmitHashshare:
    nonce: Hex
    mix: Optional[Hex]
    body: JSON  # must match proofs/schemas/hashshare.cddl


@dataclass(frozen=True)
class SubmitParams:
    worker: str
    jobId: str
    extranonce2: Hex
    hashshare: SubmitHashshare
    attachments: Optional[JSON] = (
        None  # {"ai": {...}, "quantum": {...}, "storage": {...}, "vdf": {...}}
    )


@dataclass(frozen=True)
class SubmitResult:
    accepted: bool
    reason: Optional[str] = None
    isBlock: Optional[bool] = None
    txCount: Optional[int] = None


# ---------------------- JSON-RPC helpers ----------------------


def make_request(
    method: Union[str, Method],
    params: Optional[JSON] = None,
    id: Union[int, str, None] = None,
) -> JSON:
    if isinstance(method, Method):
        method = method.value
    if not isinstance(method, str) or not method:
        raise InvalidRequest("method must be non-empty string")
    env: JSON = {"jsonrpc": "2.0", "method": method}
    if id is not None:
        env["id"] = id
    if params is not None:
        env["params"] = params
    return env


def make_result(id: Union[int, str, None], result: Any) -> JSON:
    return {"jsonrpc": "2.0", "id": id, "result": result}


def make_error(
    id: Union[int, str, None], code: int, message: str, data: Any = None
) -> JSON:
    err = {"code": int(code), "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": id, "error": err}


def is_request(obj: JSON) -> bool:
    return isinstance(obj, dict) and obj.get("jsonrpc") == "2.0" and "method" in obj


def is_response(obj: JSON) -> bool:
    return (
        isinstance(obj, dict)
        and obj.get("jsonrpc") == "2.0"
        and ("result" in obj or "error" in obj)
    )


# ---------------------- Validation (lightweight) ----------------------


def _expect(cond: bool, msg: str) -> None:
    if not cond:
        raise InvalidParams(msg)


def validate_method_name(name: str) -> Method:
    try:
        return Method(name)
    except ValueError:
        raise MethodNotFound(f"unknown method: {name}")


def validate_request(obj: JSON) -> Tuple[Method, Optional[Union[int, str]], JSON]:
    if not is_request(obj):
        raise InvalidRequest("not a JSON-RPC 2.0 request")
    method = validate_method_name(str(obj["method"]))
    id_val = obj.get("id")
    params = obj.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise InvalidParams("params must be an object")
    # quick per-method structural checks (deep checks done in handlers)
    if method == Method.SUBSCRIBE:
        _expect(
            "agent" in params and isinstance(params["agent"], str),
            "subscribe.agent required",
        )
        _expect(
            "features" in params and isinstance(params["features"], dict),
            "subscribe.features required",
        )
    elif method == Method.AUTHORIZE:
        for k in ("worker", "address"):
            _expect(
                k in params and isinstance(params[k], str), f"authorize.{k} required"
            )
    elif method == Method.SET_DIFFICULTY:
        for k in ("shareTarget", "thetaMicro"):
            _expect(k in params, f"set_difficulty.{k} required")
    elif method == Method.NOTIFY:
        for k in ("jobId", "cleanJobs", "header", "shareTarget"):
            _expect(k in params, f"notify.{k} required")
    elif method == Method.SUBMIT:
        for k in ("worker", "jobId", "extranonce2", "hashshare"):
            _expect(k in params, f"submit.{k} required")
        _expect(
            isinstance(params["hashshare"], dict), "submit.hashshare must be object"
        )
        for k in ("nonce", "body"):
            _expect(k in params["hashshare"], f"submit.hashshare.{k} required")
    return method, id_val, params  # type: ignore[return-value]


# ---------------------- Framing helpers ----------------------


def dumps(obj: JSON) -> bytes:
    """
    Canonical JSON dump suitable for wire use (UTF-8, no spaces, stable key order).
    """
    return json.dumps(obj, separators=(",", ":"), sort_keys=True).encode("utf-8")


def loads(data: Union[bytes, str]) -> JSON:
    if isinstance(data, (bytes, bytearray)):
        data = data.decode("utf-8", errors="strict")
    obj = json.loads(data)
    if not isinstance(obj, dict):
        raise InvalidRequest("top-level must be object")
    return obj


# line-delimited framing -------------------------------------------------------


def encode_lines(obj: JSON) -> bytes:
    return dumps(obj) + b"\n"


def decode_lines(buffer: bytearray) -> List[JSON]:
    """
    Consume as many newline-delimited JSON envelopes as available from a buffer.
    Leaves partial trailing data in-place.
    """
    out: List[JSON] = []
    while True:
        idx = buffer.find(b"\n")
        if idx < 0:
            break
        line = bytes(buffer[:idx])  # exclude newline
        del buffer[: idx + 1]
        if not line:
            continue
        out.append(loads(line))
    return out


# 4-byte BE length-prefixed framing -------------------------------------------


def frame_lenpref(payload: bytes) -> bytes:
    n = len(payload)
    return n.to_bytes(4, "big") + payload


def unframe_lenpref(buffer: bytearray) -> List[bytes]:
    """
    Return a list of complete payloads (without the 4B prefix).
    """
    out: List[bytes] = []
    while True:
        if len(buffer) < 4:
            break
        n = int.from_bytes(buffer[:4], "big")
        if n < 1 or n > 8_388_608:  # 8 MiB sanity
            raise InvalidRequest(f"unreasonable frame length: {n}")
        if len(buffer) < 4 + n:
            break
        body = bytes(buffer[4 : 4 + n])
        del buffer[: 4 + n]
        out.append(body)
    return out


def encode_lenpref(obj: JSON) -> bytes:
    return frame_lenpref(dumps(obj))


def decode_lenpref(buffer: bytearray) -> List[JSON]:
    return [loads(b) for b in unframe_lenpref(buffer)]


# ---------------------- Convenience builders ----------------------


def req_subscribe(
    agent: str,
    features: Optional[JSON] = None,
    algo: str = "hashshare",
    id: Union[int, str, None] = 1,
) -> JSON:
    return make_request(
        Method.SUBSCRIBE,
        {
            "agent": agent,
            "features": features or {"framing": "lines", "compress": False},
            "algo": algo,
        },
        id=id,
    )


def res_subscribe(
    id: Union[int, str, None],
    session_id: str,
    extranonce1: Hex,
    extranonce2_size: int,
    framing: str = "lines",
) -> JSON:
    return make_result(
        id,
        {
            "sessionId": session_id,
            "extranonce1": extranonce1,
            "extranonce2Size": int(extranonce2_size),
            "framing": framing,
        },
    )


def res_subscribe_v1(
    id: Union[int, str, None], extranonce1: Hex, extranonce2_size: int
) -> JSON:
    """Standard Stratum v1 subscribe reply.

    Format:
      [
        [["mining.set_difficulty", "<subid1>"], ["mining.notify", "<subid2>"]],
        "extranonce1",
        extranonce2_size
      ]
    """
    sub1 = "subscription-id-1"
    sub2 = "subscription-id-2"
    return {
        "id": id,
        "result": [
            [["mining.set_difficulty", sub1], ["mining.notify", sub2]],
            extranonce1,
            int(extranonce2_size),
        ],
        "error": None,
    }


def req_authorize(
    worker: str,
    address: str,
    signature: Optional[Hex] = None,
    id: Union[int, str, None] = 2,
) -> JSON:
    p: JSON = {"worker": worker, "address": address}
    if signature is not None:
        p["signature"] = signature
    return make_request(Method.AUTHORIZE, p, id=id)


def res_authorize(
    id: Union[int, str, None], ok: bool, reason: Optional[str] = None
) -> JSON:
    r: JSON = {"ok": bool(ok), "authorized": bool(ok)}
    if reason:
        r["reason"] = reason
    return make_result(id, r)


def res_authorize_v1(
    id: Union[int, str, None], ok: bool = True, reason: Optional[str] = None
) -> JSON:
    return {
        "id": id,
        "result": bool(ok),
        "error": (
            None
            if ok
            else {
                "code": RpcErrorCodes.UNAUTHORIZED,
                "message": reason or "unauthorized",
            }
        ),
    }


def push_set_difficulty(share_target: float, theta_micro: int) -> JSON:
    return make_request(
        Method.SET_DIFFICULTY,
        {"shareTarget": float(share_target), "thetaMicro": int(theta_micro)},
        id=None,
    )


def push_set_difficulty_v1(difficulty: float) -> JSON:
    return {
        "id": None,
        "method": Method.SET_DIFFICULTY.value,
        "params": [float(difficulty)],
    }


def push_notify(
    job_id: str,
    header: JSON,
    share_target: float,
    clean_jobs: bool = True,
    hints: Optional[JSON] = None,
) -> JSON:
    p: JSON = {
        "jobId": job_id,
        "cleanJobs": bool(clean_jobs),
        "header": header,
        "shareTarget": float(share_target),
    }
    if hints is not None:
        p["hints"] = hints
    return make_request(Method.NOTIFY, p, id=None)


def push_notify_v1(
    job_id: str,
    prevhash: Hex,
    coinb1: Hex,
    coinb2: Hex,
    merkle_branch: List[Hex],
    version: Hex,
    nbits: Hex,
    ntime: Hex,
    clean_jobs: bool,
) -> JSON:
    params: List[Any] = [
        job_id,
        prevhash,
        coinb1,
        coinb2,
        merkle_branch,
        version,
        nbits,
        ntime,
        bool(clean_jobs),
    ]
    return {"id": None, "method": Method.NOTIFY.value, "params": params}


def req_submit(
    worker: str,
    job_id: str,
    extranonce2: Hex,
    hashshare_body: JSON,
    nonce: Hex,
    mix: Optional[Hex] = None,
    attachments: Optional[JSON] = None,
    id: Union[int, str, None] = 3,
) -> JSON:
    hs: JSON = {"nonce": nonce, "body": hashshare_body}
    if mix is not None:
        hs["mix"] = mix
    p: JSON = {
        "worker": worker,
        "jobId": job_id,
        "extranonce2": extranonce2,
        "hashshare": hs,
    }
    if attachments is not None:
        p["attachments"] = attachments
    return make_request(Method.SUBMIT, p, id=id)


def res_submit(
    id: Union[int, str, None],
    accepted: bool,
    reason: Optional[str] = None,
    is_block: Optional[bool] = None,
    tx_count: Optional[int] = None,
) -> JSON:
    r: JSON = {"accepted": bool(accepted)}
    if reason:
        r["reason"] = reason
    if is_block is not None:
        r["isBlock"] = bool(is_block)
    if tx_count is not None:
        r["txCount"] = int(tx_count)
    return make_result(id, r)


def res_submit_v1(
    id: Union[int, str, None], accepted: bool, reason: Optional[str] = None
) -> JSON:
    err = (
        None
        if accepted
        else {"code": RpcErrorCodes.INVALID_SHARE, "message": reason or "rejected"}
    )
    return {"id": id, "result": bool(accepted), "error": err}


# ---------------------- Tiny self-test (manual) ----------------------


def _roundtrip_demo() -> None:  # pragma: no cover
    buf = bytearray()
    m1 = req_subscribe("animica-miner/0.1", {"framing": "lenpref", "compress": False})
    m2 = push_set_difficulty(share_target=0.0125, theta_micro=850000)
    for m in (m1, m2):
        buf += encode_lines(m)
    decoded = decode_lines(buf)
    assert decoded[0]["method"] == Method.SUBSCRIBE.value
    assert decoded[1]["method"] == Method.SET_DIFFICULTY.value
    method, idv, params = validate_request(decoded[0])
    assert (
        method == Method.SUBSCRIBE
        and idv == 1
        and params["agent"].startswith("animica")
    )


if __name__ == "__main__":  # pragma: no cover
    _roundtrip_demo()
