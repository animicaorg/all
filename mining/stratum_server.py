from __future__ import annotations

import asyncio
import contextvars
import errno
import json
import logging
import os
import secrets
import socket
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field, replace
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .hash_search import digest_to_int256
from .pow_validation import (derive_block_target_int, digest_from_sign_bytes,
                             evaluate_digest, parse_hex_bytes)
from .stratum_protocol import (InvalidParams, InvalidRequest, Method,
                               MethodNotFound, RpcErrorCodes, decode_lenpref,
                               decode_lines, encode_lenpref, encode_lines,
                               make_error, make_result, push_aicf_notify,
                               push_notify, push_notify_v1,
                               push_set_difficulty, push_set_difficulty_v1,
                               req_submit, req_subscribe, res_aicf_submit,
                               res_authorize, res_authorize_v1, res_submit,
                               res_submit_v1, res_subscribe, res_subscribe_v1,
                               validate_request)
from .templates import MiningJob, share_target_to_difficulty

# Set on connections that arrive on the dedicated solo stratum port, so the
# session they allocate is forced to solo accounting (95%-to-finder) regardless
# of any username/password mode hint. Per-connection (each accept runs in its
# own task/context), so it never leaks across connections.
_SOLO_FORCED: "contextvars.ContextVar[bool]" = contextvars.ContextVar(
    "animica_solo_forced", default=False
)

try:
    # Prefer our shared logger if present
    from core.logging import get_logger  # type: ignore
except Exception:  # pragma: no cover

    def get_logger(name: str) -> logging.Logger:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
        )
        return logging.getLogger(name)


Hex = str
JSON = Dict[str, Any]
log = get_logger("mining.stratum_server")


# --------------------------------------------------------------------------------------
# Job & session models
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class StratumJob:
    job_id: str
    header: JSON  # header template (deterministic, sign-bytes ready)
    share_target: float  # micro-target difficulty for shares (ratio vs Θ)
    theta_micro: int  # current Θ in µ-nats
    hints: Optional[JSON] = None
    target: Optional[str] = None  # optional full block target (hex int)
    sign_bytes: Optional[str] = None  # optional explicit signBytes prefix (0x…)
    height: Optional[int] = None
    parent_hash: Optional[str] = None
    parent_height: Optional[int] = None
    chain_id: Optional[int] = None
    expires_at: Optional[float] = None
    proof_type: Optional[str] = None
    script_hash: Optional[str] = None
    inputs_commit: Optional[str] = None
    outputs_commit: Optional[str] = None
    raw: Optional[JSON] = None
    created_ts: float = field(default_factory=lambda: time.time())


@dataclass
class Session:
    session_id: str
    writer: asyncio.StreamWriter
    framing: str = "lines"  # "lines" | "lenpref"
    # Remote IP the connection arrived from; drives the per-IP session cap and
    # lets stats group sessions by origin. None when the transport has no
    # peername (unix sockets in tests).
    peer_ip: Optional[str] = None
    extranonce1: Hex = ""
    extranonce2_size: int = 8
    worker: Optional[str] = None
    address: Optional[str] = None
    pool_mode: str = "pps"
    # True when this connection arrived on the dedicated solo stratum port. Such
    # sessions are pinned to solo accounting and ignore any pool-mode hint.
    solo_forced: bool = False
    authorized: bool = False
    share_target: float = 0.01
    theta_micro: int = 800_000
    last_seen: float = field(default_factory=lambda: time.time())
    # Wall-clock of the last INBOUND bytes from this client. Distinct from
    # last_seen, which touch() also refreshes on OUTBOUND keepalive pushes
    # (_push_difficulty) — using last_seen for idle detection makes a silent
    # client look active forever because our own heartbeat keeps resetting it.
    # The idle reaper must key off this field only.
    last_inbound: float = field(default_factory=lambda: time.time())
    connected_since: float = field(default_factory=lambda: time.time())
    jobs_seen: List[str] = field(default_factory=list)
    shares_accepted: int = 0
    shares_rejected: int = 0
    last_share_at: Optional[float] = None
    last_share_status: Optional[str] = None
    current_difficulty: float = 0.0
    low_diff_reject_streak: int = 0
    accepted_share_streak: int = 0
    is_v1: bool = False
    # True when this client advertised sub-block share support on
    # mining.subscribe. Only such sessions are handed a share target below the
    # block target; everything else (xmrig, ASIC dashboards, older miners) keeps
    # the wire-difficulty-floored target it has always received.
    supports_subblock: bool = False
    subscription_ids: Tuple[str, str] = ("subscription-id-1", "subscription-id-2")
    # Features dict the miner sent on mining.subscribe. Captured so the
    # AUTHORIZE hook can forward AICF tier / hardware metadata to the
    # worker-registration callback without re-parsing the wire payload.
    subscribed_features: Dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        self.last_seen = time.time()


def _share_identity(params: Any) -> Optional[tuple]:
    """Identity of a submitted solution, for replay detection.

    A solution is the (nonce, extranonce2) pair — everything else in the submit
    is derived or advisory. Returns None when no nonce can be found, in which
    case the share is simply not deduped (the validator will reject it anyway).
    """
    if not isinstance(params, dict):
        return None
    nonce = params.get("nonce")
    if nonce in (None, ""):
        hs = params.get("hashshare")
        if isinstance(hs, dict):
            nonce = hs.get("nonce")
    if nonce in (None, ""):
        return None
    # Normalise 0x-prefixed / bare hex / int so "0x1", "1" and 1 collide.
    try:
        nonce_key: Any = int(str(nonce), 16) if isinstance(nonce, str) else int(nonce)
    except (TypeError, ValueError):
        nonce_key = str(nonce).strip().lower()
    extranonce2 = params.get("extranonce2")
    en2 = str(extranonce2).strip().lower() if extranonce2 not in (None, "") else ""
    return (nonce_key, en2)


def _parse_subblock_optin(features: Any) -> bool:
    """True when a subscribe `features` dict opts in to sub-block share targets.

    Accepts `subblockShares` / `subblock_shares` / `subShares` as a truthy flag,
    a version number, or a dict (any of `{"version": n}` shapes). Deliberately
    permissive on shape but never inferred from anything else — absence means
    "send me exactly what you always sent".
    """
    if not isinstance(features, dict):
        return False
    for key in ("subblockShares", "subblock_shares", "subShares", "sub_shares"):
        if key not in features:
            continue
        value = features[key]
        if isinstance(value, dict):
            return bool(value) and value.get("enabled", True) is not False
        if isinstance(value, str):
            return value.strip().lower() not in ("", "0", "false", "no", "off")
        return bool(value)
    return False


def _parse_worker_identity(raw_worker: Any) -> tuple[Optional[str], Optional[str]]:
    """
    Parse Stratum username identity into `(worker, address)`.

    Accepted forms:
    - `anim1...` -> worker=`anim1...`, address=`anim1...`
    - `anim1....worker` (or `/`, `:` delimiter) -> worker=`worker`, address=`anim1...`
    - fallback -> worker=`raw`, address=None
    """
    text = str(raw_worker or "").strip()
    if not text:
        return None, None

    split_pos = -1
    for delim in (".", "/", ":"):
        idx = text.find(delim)
        if idx > 0 and (split_pos < 0 or idx < split_pos):
            split_pos = idx

    if split_pos > 0:
        head = text[:split_pos].strip()
        tail = text[split_pos + 1 :].strip()
    else:
        head = text
        tail = ""

    if head.startswith("anim1"):
        worker = tail or text
        return worker, head

    if text.startswith("anim1"):
        return text, text

    return text, None


def _normalize_pool_mode(
    raw_mode: Any, *, default: str = "pps", allow_both: bool = False
) -> str:
    allowed = {"pps", "solo"}
    if allow_both:
        allowed.add("both")
    candidate = str(raw_mode or "").strip().lower()
    if candidate in allowed:
        return candidate
    fallback = str(default or "pps").strip().lower() or "pps"
    if fallback in allowed:
        return fallback
    return "pps"


def _parse_pool_mode_hint(raw_value: Any) -> Optional[str]:
    text = str(raw_value or "").strip().lower()
    if not text:
        return None
    if text in {"pps", "solo"}:
        return text
    for chunk in text.replace(";", ",").replace("|", ",").split(","):
        token = chunk.strip()
        if not token:
            continue
        if token in {"pps", "solo"}:
            return token
        if "=" in token:
            key, value = token.split("=", 1)
            if key.strip() in {"mode", "pool_mode"} and value.strip() in {"pps", "solo"}:
                return value.strip()
    return None


# --------------------------------------------------------------------------------------
# Validator interface (pluggable)
# --------------------------------------------------------------------------------------


class ShareValidator:
    """
    Pluggable share validator. The default implementation performs structural
    checks and defers to optional adapters if available.
    """

    async def validate(
        self, job: StratumJob, submit_params: JSON
    ) -> Tuple[bool, Optional[str], bool, int]:
        """
        Returns: (accepted, reason, is_block, tx_count)
        - accepted: whether the share passes target and sanity checks
        - reason: human string on failure
        - is_block: True if the share sealed a full block
        - tx_count: number of txs included if is_block
        """
        # Attempt to use deep verifiers if adapters exist
        try:
            # Late import to avoid hard-dep before those files land
            from mining.adapters.proofs_view import \
                verify_hashshare_envelope  # type: ignore

            ok, reason, is_block, tx_count = await verify_hashshare_envelope(
                job.header, submit_params
            )
            return ok, reason, is_block, tx_count
        except Exception as e:
            # Fallback to lightweight sanity checks
            log.debug("[Stratum] deep validator unavailable; using fallback: %s", e)

        # Lightweight checks with basic PoW predicate using provided signBytes.
        hs = submit_params.get("hashshare") or {}
        nonce_hex = hs.get("nonce")
        body = hs.get("body")
        if not isinstance(nonce_hex, str) or not nonce_hex.startswith("0x"):
            return False, "nonce must be hex", False, 0
        if not isinstance(body, dict):
            return False, "hashshare.body must be object", False, 0

        try:
            nonce_int = int(nonce_hex, 16)
        except Exception:
            return False, "bad nonce", False, 0

        # Prefer canonical header hashing when a full template header is present.
        digest: bytes | None = None
        if isinstance(job.header, dict):
            try:
                from mining.template_block import hash_candidate_header

                digest = hash_candidate_header(job.header, nonce=nonce_int).digest
            except Exception:
                digest = None

        # Ensure we have signBytes to recompute the digest when header hashing is
        # unavailable; if absent, fall back to accepting shares (legacy behavior)
        # so SHA-256 style templates remain usable.
        sign_hex = (
            job.sign_bytes or job.header.get("signBytes")
            if isinstance(job.header, dict)
            else None
        )
        if digest is None and (not isinstance(sign_hex, str) or not sign_hex.startswith("0x")):
            return True, None, False, 0

        if digest is None:
            try:
                prefix = bytes.fromhex(sign_hex[2:])
            except Exception:
                return False, "invalid signBytes", False, 0

            mix_hex = None
            if isinstance(submit_params.get("hints"), dict):
                mix_hex = submit_params.get("hints", {}).get("mixSeed")
            if mix_hex is None and isinstance(job.hints, dict):
                mix_hex = job.hints.get("mixSeed")
            mix_seed = parse_hex_bytes(mix_hex, default=b"")
            digest = digest_from_sign_bytes(
                prefix,
                mix_seed=mix_seed,
                nonce_int=nonce_int,
                nonce_byteorder="little",
            )

        digest_int = digest_to_int256(digest)
        block_target = (
            job.target or job.header.get("target")
            if isinstance(job.header, dict)
            else None
        )
        decision = evaluate_digest(
            digest_int,
            theta_micro=int(job.theta_micro),
            share_ratio=job.share_target,
            block_target=derive_block_target_int(block_target),
            enforce_share_target=True,
        )
        if decision.share_target_int <= 0:
            return False, "missing share target", False, 0
        if not decision.share_ok:
            return False, "low difficulty share", False, 0

        return True, None, bool(decision.is_block), 0


class _ReplayReader:
    """asyncio.StreamReader-compatible wrapper that prepends an already-
    consumed byte string before delegating to the underlying reader.

    Used by the single-port protocol-detect handoff in
    StratumServer._handle_client: we sniffed the first line off the wire
    to decide whether to route to Animica or cryptonote, but any bytes
    that arrived in the same read() AFTER the first newline still belong
    to the connection. The cryptonote handler gets a feeder that yields
    those leftover bytes first, then transparently forwards.
    """

    def __init__(self, leftover: bytes, inner: asyncio.StreamReader) -> None:
        self._leftover = bytearray(leftover)
        self._inner = inner

    async def readline(self) -> bytes:
        if self._leftover:
            nl = self._leftover.find(b"\n")
            if nl != -1:
                line = bytes(self._leftover[: nl + 1])
                del self._leftover[: nl + 1]
                return line
            # No newline in leftover yet; pull more
            try:
                more = await self._inner.readline()
            except Exception:
                more = b""
            if not more:
                # Connection closed before a full line — return what we have
                line = bytes(self._leftover)
                self._leftover.clear()
                return line
            line = bytes(self._leftover) + more
            self._leftover.clear()
            return line
        return await self._inner.readline()

    async def read(self, n: int = -1) -> bytes:
        if self._leftover:
            if n < 0 or n >= len(self._leftover):
                out = bytes(self._leftover)
                self._leftover.clear()
                if n < 0:
                    out += await self._inner.read(n)
                else:
                    remaining = n - len(out)
                    if remaining > 0:
                        out += await self._inner.read(remaining)
                return out
            out = bytes(self._leftover[:n])
            del self._leftover[:n]
            return out
        return await self._inner.read(n)


# --------------------------------------------------------------------------------------
# Stratum Server
# --------------------------------------------------------------------------------------


class StratumServer:
    """
    Asyncio TCP JSON-RPC Stratum server.

    External integrations:
      - Call `publish_job(job: StratumJob)` when a new template is available.
      - Optionally call `set_global_difficulty(share_target, theta_micro)`.

    Minimal start:
      server = StratumServer(host="0.0.0.0", port=23454)
      await server.start()
      await server.publish_job(template_builder())    # from mining.templates

    NOTE: The implementation below still targets Animica's draft protocol.  A
    complete SHA-256 Stratum v1 surface (for ASIC dashboards) is expected to
    adapt the handshake and submit path so miners see per-connection
    extranonces, explicit difficulty pushes, and canonical `mining.notify`
    payloads.  Those adaptations are tracked in the surrounding tasks and this
    docstring callout is a breadcrumb to keep future edits discoverable.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 23454,
        extranonce2_size: int = 8,
        default_share_target: float = 0.01,
        default_theta_micro: int = 800_000,
        keepalive_secs: float = 45.0,
        send_timeout_secs: float = 60.0,
        max_cached_jobs: int = 64,
        validator: Optional[ShareValidator] = None,
        submit_hook: Optional[
            Callable[
                [Session, StratumJob, JSON, bool, Optional[str], bool, int],
                Awaitable[None],
            ]
        ] = None,
        # Fired after a miner successfully completes mining.authorize.
        # Used by the pool to register the miner as an AICF worker via
        # aicf.workerRegister so distributed-inference jobs can be served
        # by the same hardware that's mining PoW. See
        # rpc/methods/aicf_jobs.py and the "miners are AICF workers"
        # architecture note in the project memory.
        on_worker_authorized: Optional[
            Callable[[Session, JSON], Awaitable[None]]
        ] = None,
        # Fired when a miner sends mining.aicf.submit with an inference
        # result. The pool relays this to aicf.workerSubmitResult on the
        # local node, which finalizes the job. Returns the result dict
        # (accepted, state, reason) which is shipped back to the miner.
        on_aicf_result: Optional[
            Callable[[Session, JSON], Awaitable[JSON]]
        ] = None,
        # Fired when a miner sends mining.submit_xmr with a Monero share.
        # Returns {accepted, reason, block_solved} which is shipped back.
        # See python/animica/stratum_pool/xmr.py XmrShareValidator.validate
        # for the canonical share-acceptance logic.
        on_xmr_result: Optional[
            Callable[[Session, JSON], Awaitable[JSON]]
        ] = None,
        # Optional handoff for cryptonote pool protocol (Monero/RandomX
        # stock xmrig clients). When the very first inbound JSON message
        # is `{"method":"login", ...}` the server delegates the entire
        # connection to this callback (the Animica session is abandoned
        # before authorize). This lets ONE TCP port serve both Animica
        # stratum-v1 AND cryptonote miners — the protocol is detected
        # from the first message. See
        # python/animica/stratum_pool/xmr_stratum.py for the canonical
        # cryptonote handler the pool wires here.
        cryptonote_handler: Optional[
            Callable[
                [
                    asyncio.StreamReader,
                    asyncio.StreamWriter,
                    JSON,  # the already-parsed login message
                ],
                Awaitable[None],
            ]
        ] = None,
        pool_mode: str = "pps",
        solo_port: Optional[int] = None,
        session_vardiff_enabled: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._solo_port = int(solo_port) if solo_port else 0
        self._server: Optional[asyncio.AbstractServer] = None
        self._solo_server: Optional[asyncio.AbstractServer] = None
        self._sessions: Dict[str, Session] = {}
        self._conn_tasks: Dict[asyncio.Task, None] = {}
        self._jobs: Dict[str, StratumJob] = {}
        self._job_order: List[str] = []
        self._current_job_id: Optional[str] = None
        self._max_cached_jobs = max(2, int(max_cached_jobs))
        self._extranonce2_size = int(extranonce2_size)
        self._default_share_target = float(default_share_target)
        self._default_theta_micro = int(default_theta_micro)
        self._keepalive_secs = float(keepalive_secs)
        # A too-tight send timeout cancels socket writes mid-flight on slow/
        # distant miners, corrupting the stream and churning "connection reset by
        # peer". Default is generous; override via ANIMICA_STRATUM_SEND_TIMEOUT.
        self._send_timeout_secs = max(
            float(os.environ.get("ANIMICA_STRATUM_SEND_TIMEOUT", send_timeout_secs)),
            0.0)
        self._validator = validator or ShareValidator()
        self._submit_hook = submit_hook
        self._on_worker_authorized = on_worker_authorized
        self._on_aicf_result = on_aicf_result
        self._on_xmr_result = on_xmr_result
        self._cryptonote_handler = cryptonote_handler
        self._pool_mode = _normalize_pool_mode(
            pool_mode, default="pps", allow_both=True
        )
        self._default_session_pool_mode = (
            "pps" if self._pool_mode == "both" else self._pool_mode
        )
        self._session_vardiff_enabled = bool(session_vardiff_enabled)
        # Abuse guards. On 2026-08-06 a single client held 835 idle sockets on
        # the solo port — authorized once per socket, then never spoke again —
        # inflating the "miners" stat and walking the process toward fd
        # exhaustion. Cap concurrent sessions per remote IP and reap sessions
        # that stop sending. Legit miners reconnect transparently; 0 disables.
        self._max_sessions_per_ip = max(
            0, int(os.environ.get("ANIMICA_STRATUM_MAX_SESSIONS_PER_IP", "64"))
        )
        self._idle_timeout_secs = max(
            0.0, float(os.environ.get("ANIMICA_STRATUM_IDLE_TIMEOUT_SECS", "900"))
        )
        self._handshake_timeout_secs = max(
            0.0, float(os.environ.get("ANIMICA_STRATUM_HANDSHAKE_TIMEOUT_SECS", "180"))
        )
        # Max bytes we will buffer for ONE un-terminated line. decode_lines
        # only consumes complete newline-delimited envelopes, so a client that
        # streams forever without a newline grows this session's buffer without
        # limit (measured ~100MB in 17s on one socket) — and it stays "active"
        # for the reaper because bytes keep arriving. Legit stratum lines are
        # well under 64KiB. 0 disables the bound.
        self._max_line_bytes = max(
            0, int(os.environ.get("ANIMICA_STRATUM_MAX_LINE_BYTES", str(1 << 20)))
        )
        self._session_low_reject_streak_threshold = 3
        self._session_high_accept_streak_threshold = 24
        base_target = max(float(self._default_share_target), 1e-9)
        self._session_min_share_target = max(1e-9, base_target * 0.05)
        self._session_max_share_target = min(1.0, max(base_target * 1.5, base_target))
        if self._session_max_share_target < self._session_min_share_target:
            self._session_max_share_target = self._session_min_share_target

        # Live connection count per remote IP, maintained from accept to
        # close in _handle_client. The cap must count CONNECTIONS, not
        # `_sessions` entries: during the cryptonote protocol sniff (up to 10s,
        # or forever for a client that never sends a newline) and for the whole
        # life of a cryptonote session, a connection holds an fd without ever
        # appearing in _sessions — counting sessions would let a burst of
        # simultaneous connects sail past the cap before any of them registers.
        self._ip_conn_counts: Dict[str, int] = {}
        # Refusals are attacker-paced (one TCP handshake each), and the pool
        # logs synchronously to journald where every line shares one rate-limit
        # bucket with share/job/payout INFO. An unthrottled per-refusal warning
        # would let a connect flood suppress the logs that matter. Count always,
        # log at most once per window per IP.
        self._refused_total = 0
        self._refused_by_ip: Dict[str, int] = {}
        self._refused_log_ts: Dict[str, float] = {}
        self._refused_log_interval = max(
            0.0, float(os.environ.get("ANIMICA_STRATUM_REFUSE_LOG_INTERVAL", "60"))
        )
        # How long a broadcast (job notify / difficulty) may hold its caller
        # before slow peers are left behind. The template loop awaits job
        # publication, so this bounds the pool-wide stall one wedged miner can
        # cause. 0 = wait for every send (legacy behaviour).
        self._broadcast_deadline_secs = max(
            0.0, float(os.environ.get("ANIMICA_STRATUM_BROADCAST_DEADLINE", "5"))
        )
        # Sends left running past a broadcast deadline; held so the event loop
        # keeps a strong reference until they complete.
        self._detached_sends: set = set()
        # Accepted (job_id, nonce, extranonce2) triples, so the same solution can
        # never be credited twice. REQUIRED once sub-block shares exist: a
        # non-block share is accepted locally and never reaches the node
        # (stratum_pool/core.py returns early for `not is_block`), so nothing
        # else dedupes it — a miner could replay one valid share for unlimited
        # PPS credit. Block shares were always covered because the node reports
        # duplicates. Keyed pool-wide, not per session, so a second connection
        # cannot re-submit another session's solution either. Bounded: one entry
        # set per cached job, evicted with the job.
        self._seen_shares: Dict[str, set] = {}
        # Policy hook: θµ -> share ratio for sub-block-capable sessions, or None
        # to leave them on the normal floored target. Owned by the pool (it has
        # the config and the live θ); the server only applies it, and only to
        # sessions that advertised support. Unset => feature entirely inert.
        self._subblock_ratio_for_theta: Optional[Callable[[int], Optional[float]]] = None

        # Stats
        self._accepted = 0
        self._rejected = 0
        self._started_ts = time.time()

        # Background heartbeat tasks per session
        self._heartbeats: Dict[str, asyncio.Task] = {}
        # Server-level idle reaper. Deliberately NOT part of the per-session
        # heartbeat: the heartbeat's keepalive push awaits an un-timed
        # writer.drain(), so a client that stops reading wedges that coroutine
        # forever (and a failed send ends it) — a reaper riding it would die
        # with it. One independent task sweeping all sessions cannot be
        # wedged by any single client, and closing a wedged session's writer
        # is what unblocks its stuck heartbeat.
        self._reaper_task: Optional[asyncio.Task] = None

    # ---------------- lifecycle ----------------

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )
        sockets = ", ".join(str(s.getsockname()) for s in self._server.sockets or [])
        log.info(f"[Stratum] listening on {sockets}")
        if self._solo_port:
            self._solo_server = await asyncio.start_server(
                self._handle_client_solo, self._host, self._solo_port
            )
            solo_sockets = ", ".join(
                str(s.getsockname()) for s in self._solo_server.sockets or []
            )
            log.info(f"[Stratum] SOLO listening on {solo_sockets} (95%-to-finder)")
        if self._idle_timeout_secs > 0 or self._handshake_timeout_secs > 0:
            self._reaper_task = asyncio.create_task(self._reap_idle_sessions_loop())

    async def _handle_client_solo(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Same connection handler as the main port, but flag this connection so
        # its session is pinned to solo accounting (the flag is read in
        # _alloc_session). Each accept runs in its own task/context.
        token = _SOLO_FORCED.set(True)
        try:
            await self._handle_client(reader, writer)
        finally:
            _SOLO_FORCED.reset(token)

    async def _reap_idle_sessions_loop(self) -> None:
        """Close sessions whose client stopped sending. Keyed off
        `last_inbound` (inbound bytes only — NOT last_seen, which our own
        outbound keepalives refresh), so a session past its timeout is a
        client holding the socket without mining (vardiff keeps real miners
        submitting well inside the window; 2026-08-06 one client parked 835
        authorized, forever-silent sessions). Unauthorized sessions get the
        shorter handshake deadline (a handshake timeout of 0 falls back to the
        idle timeout; set both to 0 to disable reaping entirely). Closing the
        writer EOFs the session's read loop, which runs the normal disconnect
        cleanup — and also unwedges a heartbeat stuck in drain() on a
        never-reading client."""
        interval = min(
            t for t in (self._idle_timeout_secs, self._handshake_timeout_secs, 30.0)
            if t > 0
        )
        while True:
            await asyncio.sleep(interval)
            now = time.time()
            for session in list(self._sessions.values()):
                if session.writer.is_closing():
                    continue
                if session.authorized:
                    reap_after = self._idle_timeout_secs
                else:
                    reap_after = (
                        self._handshake_timeout_secs or self._idle_timeout_secs
                    )
                idle = now - session.last_inbound
                if reap_after > 0 and idle >= reap_after:
                    log.info(
                        f"[Stratum] reaping idle session={session.session_id} "
                        f"ip={session.peer_ip} worker={session.worker} "
                        f"authorized={session.authorized} idle={int(idle)}s"
                    )
                    try:
                        session.writer.close()
                    except Exception:
                        pass

    async def stop(self) -> None:
        if self._reaper_task is not None:
            self._reaper_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._reaper_task
            self._reaper_task = None
        for task in list(self._conn_tasks.keys()):
            task.cancel()
        for hb in list(self._heartbeats.values()):
            hb.cancel()
            with suppress(asyncio.CancelledError):
                await hb
        self._heartbeats.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self._server = None
        if self._solo_server:
            self._solo_server.close()
            await self._solo_server.wait_closed()
        self._solo_server = None
        log.info("[Stratum] stopped")

    # ---------------- job control ----------------

    def _from_mining_job(self, job: MiningJob) -> StratumJob:
        job_view = job.to_dict()
        header_view = job_view.get("header", {})
        hints = {"mixSeed": "0x" + job.header.mix_seed.hex()}
        for key in ("scriptHash", "inputsCommit", "outputsCommit"):
            if job_view.get(key):
                hints[key] = job_view[key]
        return StratumJob(
            job_id=job.job_id,
            header=header_view,
            share_target=self._default_share_target,
            theta_micro=job.theta_target_micro,
            hints=hints,
            target=hex(job.target),
            sign_bytes="0x" + job.sign_bytes.hex(),
            height=job.header.number,
            parent_hash="0x" + job.parent_hash.hex(),
            parent_height=job.parent_height,
            chain_id=job.chain_id,
            expires_at=job.expires_at,
            proof_type=job.proof_type,
            script_hash=job.script_hash,
            inputs_commit=job.inputs_commit,
            outputs_commit=job.outputs_commit,
        )

    def _prune_jobs(self, *, now: Optional[float] = None) -> None:
        now = now if now is not None else time.time()
        stale_ids: list[str] = []
        for jid in list(self._job_order):
            entry = self._jobs.get(jid)
            if entry is None:
                stale_ids.append(jid)
                continue
            if entry.expires_at is not None and now >= float(entry.expires_at):
                stale_ids.append(jid)

        for jid in stale_ids:
            self._jobs.pop(jid, None)
            self._seen_shares.pop(jid, None)
            with suppress(ValueError):
                self._job_order.remove(jid)
            if jid == self._current_job_id:
                self._current_job_id = None

        while len(self._job_order) > self._max_cached_jobs:
            drop_id = self._job_order.pop(0)
            self._jobs.pop(drop_id, None)
            self._seen_shares.pop(drop_id, None)
            if drop_id == self._current_job_id:
                self._current_job_id = self._job_order[-1] if self._job_order else None
        # A share can only be replayed against a job we would still accept, so
        # the dedup set never outlives its job. Belt-and-braces against any
        # path that adds a key without a cached job.
        if len(self._seen_shares) > self._max_cached_jobs * 2:
            for jid in [j for j in self._seen_shares if j not in self._jobs]:
                self._seen_shares.pop(jid, None)

    def _find_authorized_session_by_address(self, address: str) -> Optional[Session]:
        addr = (address or "").strip()
        if not addr:
            return None
        for sess in self._sessions.values():
            if sess.authorized and (sess.address or "").strip() == addr:
                return sess
        return None

    def list_aicf_capable_sessions(self) -> List[Session]:
        """All authorized sessions whose subscribe features advertised
        a non-empty AICF tier list. The pool dispatcher uses this to
        decide which miners are eligible for inference work."""
        out: List[Session] = []
        for sess in self._sessions.values():
            if not sess.authorized:
                continue
            feats = sess.subscribed_features or {}
            aicf = feats.get("aicf") if isinstance(feats, dict) else None
            if isinstance(aicf, dict) and aicf.get("tiers"):
                out.append(sess)
        return out

    async def push_aicf_job(
        self,
        *,
        worker_address: str,
        job_id: str,
        spec: JSON,
        tier: str,
        claim_expires_at: Optional[float] = None,
        estimated_cost_animica: Optional[float] = None,
    ) -> bool:
        """Push an AICF JobSpec to the miner registered at worker_address.

        Returns True if a session was found and the notify was queued.
        The miner is expected to reply asynchronously with
        ``mining.aicf.submit`` (handled by the on_aicf_result callback).
        """
        sess = self._find_authorized_session_by_address(worker_address)
        if sess is None:
            return False
        msg = push_aicf_notify(
            job_id,
            spec,
            tier,
            claim_expires_at=claim_expires_at,
            estimated_cost_animica=estimated_cost_animica,
        )
        try:
            await self._send(sess, msg)
        except Exception as exc:
            log.warning(
                f"[Stratum] failed to push aicf job={job_id} to "
                f"address={worker_address}: {exc}"
            )
            return False
        log.info(
            "[Stratum] aicf.notify job=%s tier=%s address=%s",
            job_id, tier, worker_address,
        )
        return True

    async def push_xmr_job(
        self,
        *,
        job_id: str,
        blob_hex: str,
        target_hex: str,
        seed_hash_hex: str,
        height: int,
    ) -> int:
        """Broadcast a Monero (RandomX) job to every authorized session
        whose feature negotiation included dual-mode XMR.

        Returns the number of sessions notified. The wire shape mirrors
        XMRig's "login" response: {job_id, blob, target, seed_hash,
        height, algo: "rx/0"}.
        """
        payload = {
            "jsonrpc": "2.0",
            "method": Method.XMR_NOTIFY.value,
            "params": {
                "jobId": job_id,
                "blob": blob_hex,
                "target": target_hex,
                "seed_hash": seed_hash_hex,
                "height": height,
                "algo": "rx/0",
            },
        }
        sent = 0
        for sess in list(self._sessions.values()):
            if not sess.authorized:
                continue
            features = getattr(sess, "subscribed_features", None) or {}
            if not features.get("xmr_dual"):
                continue
            try:
                await self._send(sess, payload)
                sent += 1
            except Exception as exc:
                log.warning(
                    "[Stratum] xmr notify drop session=%s: %s",
                    sess.session_id, exc,
                )
        if sent > 0:
            log.info(
                "[Stratum] xmr.notify job=%s height=%d sessions=%d",
                job_id, height, sent,
            )
        return sent

    async def publish_job(
        self,
        job: StratumJob | MiningJob,
        *,
        clean_jobs: bool = True,
    ) -> None:
        """
        Publish a new job (header template) to all connected sessions.
        """
        if isinstance(job, MiningJob):
            job = self._from_mining_job(job)
        self._jobs[job.job_id] = job
        with suppress(ValueError):
            self._job_order.remove(job.job_id)
        self._job_order.append(job.job_id)
        self._current_job_id = job.job_id
        self._prune_jobs()
        await self._broadcast_job(job, clean_jobs=clean_jobs)
        if clean_jobs:
            log.info(
                "[Stratum] notify job=%s θμ=%s shareTarget=%s sessions=%s",
                job.job_id,
                job.theta_micro,
                job.share_target,
                len(self._sessions),
            )
        else:
            log.info(
                "[Stratum] refreshed current job metadata job=%s θμ=%s shareTarget=%s sessions=%s",
                job.job_id,
                job.theta_micro,
                job.share_target,
                len(self._sessions),
            )

    def set_subblock_ratio_policy(
        self, policy: Optional[Callable[[int], Optional[float]]]
    ) -> None:
        """Install the θµ -> sub-block ratio policy (see PoolConfig docs)."""
        self._subblock_ratio_for_theta = policy

    def _effective_share_target(
        self, session: Session, share_target: float, theta_micro: int
    ) -> float:
        """The share target THIS session should mine at.

        Sub-block-capable sessions get the (easier) policy ratio so their
        non-winning work earns PPS credit. Everyone else — and every v1/xmrig
        session regardless of what it claims, because their wire difficulty must
        stay ≥ the compat floor — keeps `share_target` unchanged. The policy may
        only make a target EASIER; it can never make one harder.
        """
        # Solo sessions are credited only for full blocks (95%-to-finder), so a
        # sub-block target would just add traffic they earn nothing for.
        if not session.supports_subblock or session.is_v1:
            return float(share_target)
        if str(session.pool_mode or "").strip().lower() == "solo":
            return float(share_target)
        policy = self._subblock_ratio_for_theta
        if policy is None:
            return float(share_target)
        try:
            ratio = policy(int(theta_micro))
        except Exception as e:  # pragma: no cover - policy must never break mining
            log.warning(f"[Stratum] subblock ratio policy failed: {e}")
            return float(share_target)
        if ratio is None:
            return float(share_target)
        ratio = float(ratio)
        if not (0.0 < ratio <= 1.0):
            return float(share_target)
        return min(float(share_target), ratio)

    async def set_global_difficulty(
        self, share_target: float, theta_micro: Optional[int] = None
    ) -> None:
        if theta_micro is None:
            theta_micro = self._default_theta_micro
        self._default_share_target = float(share_target)
        self._default_theta_micro = int(theta_micro)
        difficulty = share_target_to_difficulty(theta_micro, share_target)
        msg = push_set_difficulty(share_target=share_target, theta_micro=theta_micro)
        sends: List[Tuple[str, Session, JSON]] = []
        for sid, s in list(self._sessions.items()):
            s.theta_micro = theta_micro
            session_target = self._effective_share_target(s, share_target, theta_micro)
            s.share_target = session_target
            s.current_difficulty = difficulty if s.is_v1 else session_target
            if s.is_v1:
                sends.append((sid, s, push_set_difficulty_v1(difficulty)))
            elif session_target != share_target:
                sends.append(
                    (
                        sid,
                        s,
                        push_set_difficulty(
                            share_target=session_target, theta_micro=theta_micro
                        ),
                    )
                )
            else:
                sends.append((sid, s, msg))
        dead = await self._send_batch(
            sends, context="set_difficulty", deadline=self._broadcast_deadline_secs
        )
        for sid in dead:
            await self._drop_session(sid)
        log.info(
            f"[Stratum] set difficulty shareTarget={share_target} θμ={theta_micro} diff={difficulty}"
        )

    async def _broadcast_job(self, job: StratumJob, clean_jobs: bool) -> None:
        """Send a job to each session in the format it expects."""
        sends: List[Tuple[str, Session, JSON]] = []
        for sid, s in list(self._sessions.items()):
            try:
                if s.is_v1:
                    if job.job_id in s.jobs_seen and not clean_jobs:
                        continue
                    msg = self._build_v1_notify(job, clean_jobs=clean_jobs)
                else:
                    if job.job_id in s.jobs_seen and not clean_jobs:
                        continue
                    msg = push_notify(
                        job_id=job.job_id,
                        header=job.header,
                        share_target=(
                            float(s.share_target)
                            if float(s.share_target or 0.0) > 0.0
                            else float(job.share_target)
                        ),
                        clean_jobs=clean_jobs,
                        hints=job.hints or {},
                    )
                sends.append((sid, s, msg))
            except Exception as e:  # pragma: no cover
                log.warning(f"[Stratum] build job payload for {sid} failed: {e}")
        dead = await self._send_batch(
            sends,
            context=f"broadcast job={job.job_id}",
            on_success=lambda sid, s: self._mark_job_seen(s, job.job_id, sid, clean_jobs),
            deadline=self._broadcast_deadline_secs,
        )
        for sid in dead:
            await self._drop_session(sid)

    def _build_v1_notify(self, job: StratumJob, *, clean_jobs: bool) -> JSON:
        header = job.header or {}
        prevhash = header.get("parentHash") or header.get("prevhash") or "0" * 64
        if isinstance(prevhash, str) and prevhash.startswith("0x"):
            prevhash = prevhash[2:]
        coinb1 = header.get("coinb1") or ""
        coinb2 = header.get("coinb2") or ""
        merkle_branch = header.get("merkleBranch") or header.get("merkle_branch") or []
        version = header.get("version") or header.get("versionHex") or 0
        if isinstance(version, int):
            version = f"{version:08x}"
        nbits = header.get("nbits") or header.get("bits") or ""
        ntime = (
            header.get("timestamp")
            or header.get("ntime")
            or header.get("time")
            or int(time.time())
        )
        if isinstance(ntime, int):
            ntime = f"{ntime:08x}"
        return push_notify_v1(
            job_id=job.job_id,
            prevhash=str(prevhash),
            coinb1=str(coinb1),
            coinb2=str(coinb2),
            merkle_branch=list(merkle_branch),
            version=str(version),
            nbits=str(nbits),
            ntime=str(ntime),
            clean_jobs=clean_jobs,
        )

    # ---------------- internal helpers ----------------

    def _alloc_session(
        self, writer: asyncio.StreamWriter, framing: str = "lines"
    ) -> Session:
        sid = uuid.uuid4().hex
        # 4 bytes of extranonce1 is common; we allow 8 hex chars (4 bytes)
        extranonce1 = "0x" + secrets.token_hex(4)
        forced_solo = bool(_SOLO_FORCED.get())
        peername = writer.get_extra_info("peername")
        peer_ip = (
            peername[0]
            if isinstance(peername, (tuple, list)) and peername
            else None
        )
        s = Session(
            session_id=sid,
            writer=writer,
            framing=framing,
            peer_ip=peer_ip,
            extranonce1=extranonce1,
            extranonce2_size=self._extranonce2_size,
            pool_mode="solo" if forced_solo else self._default_session_pool_mode,
            solo_forced=forced_solo,
            share_target=self._default_share_target,
            theta_micro=self._default_theta_micro,
            current_difficulty=share_target_to_difficulty(
                self._default_theta_micro, self._default_share_target
            ),
        )
        self._sessions[sid] = s
        return s

    async def _broadcast(self, obj: JSON) -> None:
        sends = [(sid, s, obj) for sid, s in list(self._sessions.items())]
        dead = await self._send_batch(
            sends, context="broadcast", deadline=self._broadcast_deadline_secs
        )
        for sid in dead:
            await self._drop_session(sid)

    async def _send_batch(
        self,
        sends: List[Tuple[str, Session, JSON]],
        *,
        context: str,
        on_success: Optional[Callable[[str, Session], None]] = None,
        deadline: Optional[float] = None,
    ) -> List[str]:
        """Fan out one payload per session concurrently and report dead ones.

        `deadline` bounds how long the CALLER waits, not the sends themselves.
        Job broadcasts run on the template loop: without a deadline the batch
        resolves only when the slowest peer does, so one non-reading miner
        holds job publication for the full send timeout (60s default) and
        stalls block production pool-wide. Sends still in flight past the
        deadline are left running — cancelling mid-write corrupts the stream —
        and are simply not waited on; they finish or hit their own send timeout
        and get reaped on a later pass. A slow peer therefore misses this job,
        not the whole pool.
        """
        if not sends:
            return []
        tasks = [
            asyncio.ensure_future(
                self._send_with_timeout(sid, session, payload, context=context)
            )
            for sid, session, payload in sends
        ]
        if deadline and deadline > 0:
            done, pending = await asyncio.wait(tasks, timeout=deadline)
            if pending:
                log.warning(
                    f"[Stratum] {context}: {len(pending)}/{len(tasks)} sends still "
                    f"in flight after {deadline}s — not waiting (slow peers miss "
                    f"this message)"
                )
                for task in pending:
                    # Keep a reference so the task isn't GC'd mid-write, and
                    # swallow its eventual result/exception.
                    self._detached_sends.add(task)
                    task.add_done_callback(self._detached_sends.discard)
        else:
            await asyncio.wait(tasks)
        dead: List[str] = []
        for (sid, session, _), task in zip(sends, tasks):
            if not task.done():
                continue  # still in flight past the deadline: undecided, not dead
            try:
                ok = task.result()
            except Exception:
                ok = False
            if ok:
                if on_success is not None:
                    on_success(sid, session)
            else:
                dead.append(sid)
        return dead

    async def _send_with_timeout(
        self,
        sid: str,
        session: Session,
        payload: JSON,
        *,
        context: str,
    ) -> bool:
        try:
            if self._send_timeout_secs > 0:
                await asyncio.wait_for(
                    self._send(session, payload),
                    timeout=self._send_timeout_secs,
                )
            else:
                await self._send(session, payload)
            return True
        except Exception as e:  # pragma: no cover - best-effort
            log.warning(f"[Stratum] {context} to {sid} failed: {e}")
            return False

    @staticmethod
    def _mark_job_seen(session: Session, job_id: str, sid: str, clean_jobs: bool) -> None:
        session.jobs_seen.append(job_id)
        session.jobs_seen = session.jobs_seen[-16:]
        log.debug(
            "[Stratum] sent job=%s to worker=%s session=%s clean=%s diff=%s",
            job_id,
            session.worker,
            sid,
            clean_jobs,
            session.current_difficulty,
        )

    async def _drop_session(self, sid: str) -> None:
        session = self._sessions.pop(sid, None)
        task = self._heartbeats.pop(sid, None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        # Close the socket so the miner gets an EOF and reconnects, instead of
        # being silently orphaned — dropped from _sessions but left holding a
        # half-open TCP connection with no more notify/set_difficulty, so it
        # stale-grinds forever on a dead job. abort() forces an immediate close
        # (discards any stuck send buffer that caused this drop); close() is the
        # graceful fallback when the transport isn't exposed.
        if session is not None:
            writer = getattr(session, "writer", None)
            if writer is not None:
                transport = getattr(writer, "transport", None)
                if transport is not None:
                    with suppress(Exception):
                        transport.abort()
                with suppress(Exception):
                    writer.close()

    async def _send(self, session: Session, obj: JSON) -> None:
        if session.framing == "lenpref":
            payload = encode_lenpref(obj)
        else:
            payload = encode_lines(obj)
        session.writer.write(payload)
        await session.writer.drain()

    async def _push_difficulty(
        self,
        session: Session,
        share_target: float,
        theta_micro: int,
        *,
        log_level: int = logging.INFO,
    ) -> None:
        """Send a set_difficulty notification for the given session."""
        # v1 sessions keep the floored wire difficulty; capable sessions may be
        # dropped to the sub-block ratio (never raised) — see
        # _effective_share_target.
        share_target = self._effective_share_target(
            session, share_target, int(theta_micro)
        )
        session.share_target = share_target
        session.theta_micro = theta_micro
        difficulty = share_target_to_difficulty(theta_micro, share_target)
        session.current_difficulty = difficulty if session.is_v1 else share_target
        session.touch()
        msg = (
            push_set_difficulty_v1(difficulty)
            if session.is_v1
            else push_set_difficulty(share_target, theta_micro)
        )
        await self._send(session, msg)
        log.log(
            log_level,
            f"[Stratum] set_difficulty push worker={session.worker} session={session.session_id} shareTarget={share_target} θμ={theta_micro} diff={difficulty}",
        )

    async def _resync_session_target(self, session: Session) -> None:
        """Re-apply the sub-block policy after the session's mode is known.

        `mining.authorize` can flip a session pps<->solo (pool_mode "both"), and
        solo sessions must not hold a sub-block target. Also re-sends the current
        job when the target moved: the native miner reads the ratio from the JOB
        first and only falls back to the last set_difficulty, so a difficulty
        push alone would leave it mining the stale ratio until the next job.
        """
        if session.session_id not in self._sessions:
            return
        desired = self._effective_share_target(
            session, self._default_share_target, int(session.theta_micro)
        )
        if abs(float(desired) - float(session.share_target)) <= 1e-15:
            return
        await self._push_difficulty(
            session, desired, int(session.theta_micro), log_level=logging.DEBUG
        )
        if not self._current_job_id:
            return
        job = self._jobs.get(self._current_job_id)
        if job is None:
            return
        try:
            if session.is_v1:
                msg = self._build_v1_notify(job, clean_jobs=True)
            else:
                msg = push_notify(
                    job_id=job.job_id,
                    header=job.header,
                    share_target=float(session.share_target),
                    clean_jobs=True,
                    hints=job.hints or {},
                )
        except Exception as e:  # pragma: no cover - never block authorize
            log.warning(f"[Stratum] resync notify build failed: {e}")
            return
        await self._send(session, msg)

    def _resolve_session_pool_mode(
        self,
        hinted_mode: Optional[str],
        *,
        current_mode: Optional[str] = None,
    ) -> str:
        if self._pool_mode != "both":
            return self._default_session_pool_mode
        current = _normalize_pool_mode(
            current_mode, default=self._default_session_pool_mode
        )
        parsed = _normalize_pool_mode(hinted_mode, default=current)
        if parsed in {"pps", "solo"}:
            return parsed
        return current

    @staticmethod
    def _mode_hint_from_authorize(params: JSON) -> Optional[str]:
        for key in ("pool_mode", "mode", "password", "pass", "worker"):
            hinted = _parse_pool_mode_hint(params.get(key))
            if hinted is not None:
                return hinted
        return None

    @staticmethod
    def _job_for_session(session: Session, job: StratumJob) -> StratumJob:
        if (
            abs(float(session.share_target) - float(job.share_target)) <= 1e-15
            and int(session.theta_micro) == int(job.theta_micro)
        ):
            return job
        raw_payload = dict(job.raw) if isinstance(job.raw, dict) else None
        if isinstance(raw_payload, dict):
            # Force downstream validators to recalculate session-level share thresholds.
            raw_payload["_shareThresholdMicro"] = 0
            raw_payload["_shareTargetInt"] = 0
            raw_payload["_validationFingerprint"] = None
        return replace(
            job,
            share_target=float(session.share_target),
            theta_micro=int(session.theta_micro),
            raw=raw_payload,
        )

    async def _maybe_adjust_session_share_target(
        self,
        session: Session,
        *,
        ok: bool,
        reason: Optional[str],
    ) -> None:
        if not self._session_vardiff_enabled:
            return
        if session.session_id not in self._sessions:
            return
        if not session.authorized:
            return

        normalized_reason = str(reason or "").strip().lower()
        low_diff_reject = (not ok) and normalized_reason == "low difficulty share"
        if low_diff_reject:
            session.low_diff_reject_streak += 1
            session.accepted_share_streak = 0
        elif ok:
            session.accepted_share_streak += 1
            session.low_diff_reject_streak = 0
        else:
            session.low_diff_reject_streak = 0
            session.accepted_share_streak = 0

        current_target = max(float(session.share_target or 0.0), 1e-9)
        proposed_target = current_target
        trigger: Optional[str] = None

        if (
            low_diff_reject
            and session.low_diff_reject_streak
            >= self._session_low_reject_streak_threshold
        ):
            factor = (
                0.65
                if session.low_diff_reject_streak
                >= (self._session_low_reject_streak_threshold * 2)
                else 0.80
            )
            proposed_target = max(self._session_min_share_target, current_target * factor)
            session.low_diff_reject_streak = 0
            trigger = "low_difficulty_reject_streak"
        elif (
            ok
            and session.accepted_share_streak
            >= self._session_high_accept_streak_threshold
        ):
            proposed_target = min(self._session_max_share_target, current_target * 1.08)
            session.accepted_share_streak = 0
            trigger = "high_accept_streak"

        if trigger is None:
            return
        if abs(proposed_target - current_target) <= max(current_target * 0.01, 1e-12):
            return

        try:
            await self._push_difficulty(
                session,
                proposed_target,
                session.theta_micro,
                log_level=logging.DEBUG,
            )
            if self._current_job_id:
                current_job = self._jobs.get(self._current_job_id)
                if current_job is not None:
                    if session.is_v1:
                        await self._send(
                            session, self._build_v1_notify(current_job, clean_jobs=False)
                        )
                    else:
                        await self._send(
                            session,
                            push_notify(
                                current_job.job_id,
                                current_job.header,
                                proposed_target,
                                False,
                                current_job.hints or {},
                            ),
                        )
            log.info(
                "[Stratum] session vardiff adjusted worker=%s session=%s trigger=%s oldShareTarget=%.8f newShareTarget=%.8f",
                session.worker,
                session.session_id,
                trigger,
                current_target,
                proposed_target,
            )
        except Exception as e:  # pragma: no cover - best-effort
            log.warning(
                "[Stratum] session vardiff update failed for worker=%s session=%s: %s",
                session.worker,
                session.session_id,
                e,
            )

    async def _session_heartbeat(self, session: Session) -> None:
        """Periodically push a difficulty keepalive to reassure ASIC dashboards."""
        interval = max(self._keepalive_secs, 1.0)
        while True:
            await asyncio.sleep(interval)
            if session.session_id not in self._sessions or session.writer.is_closing():
                return
            idle = time.time() - session.last_seen
            if idle >= interval:
                try:
                    await self._push_difficulty(
                        session,
                        session.share_target,
                        session.theta_micro,
                        log_level=logging.DEBUG,
                    )
                except Exception as e:  # pragma: no cover - best-effort keepalive
                    log.warning(
                        f"[Stratum] keepalive failed for session={session.session_id} worker={session.worker}: {e}"
                    )
                    return

    # ---------------- connection handler ----------------

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Per-IP connection cap, enforced at accept time on a live counter so
        # neither the protocol-sniff window nor cryptonote sessions (which
        # never enter _sessions) can slip past it. One misbehaving client
        # opening sockets in bursts must not exhaust the fd budget.
        peer = writer.get_extra_info("peername")
        peer_ip = peer[0] if isinstance(peer, (tuple, list)) and peer else None
        counted_ip: Optional[str] = None
        if self._max_sessions_per_ip > 0 and peer_ip:
            held = self._ip_conn_counts.get(peer_ip, 0)
            if held >= self._max_sessions_per_ip:
                self._refused_total += 1
                refused_for_ip = self._refused_by_ip.get(peer_ip, 0) + 1
                self._refused_by_ip[peer_ip] = refused_for_ip
                now = time.time()
                last_logged = self._refused_log_ts.get(peer_ip, 0.0)
                if now - last_logged >= self._refused_log_interval:
                    self._refused_log_ts[peer_ip] = now
                    log.warning(
                        f"[Stratum] refusing connections from {peer_ip}: "
                        f"{held} already open (cap {self._max_sessions_per_ip}); "
                        f"{refused_for_ip} refused from this IP so far"
                    )
                try:
                    writer.write(
                        (
                            json.dumps(
                                make_error(
                                    None,
                                    RpcErrorCodes.INVALID_REQUEST,
                                    "too many connections from your IP",
                                )
                            )
                            + "\n"
                        ).encode("utf-8")
                    )
                    await asyncio.wait_for(writer.drain(), timeout=5.0)
                except Exception:
                    pass
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                return
            self._ip_conn_counts[peer_ip] = held + 1
            counted_ip = peer_ip
        try:
            await self._handle_client_inner(reader, writer, peer)
        finally:
            if counted_ip is not None:
                remaining = self._ip_conn_counts.get(counted_ip, 0) - 1
                if remaining > 0:
                    self._ip_conn_counts[counted_ip] = remaining
                else:
                    self._ip_conn_counts.pop(counted_ip, None)

    async def _handle_client_inner(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        peer: object,
    ) -> None:
        sock = writer.get_extra_info("socket")
        try:
            if sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                # Default Linux TCP keepalive sends the first probe only after
                # 7200s of idle — long after any NAT/router idle timeout, and
                # long after Windows clients give up with WSAETIMEDOUT
                # ("Semaphore timeout period has expired"). Tighten so the OS
                # itself keeps the TCP path warm: first probe at 30s idle,
                # then every 15s, give up after 5 misses (= ~105s to detect a
                # truly dead peer).
                for opt_name, opt_val in (
                    ("TCP_KEEPIDLE", 30),
                    ("TCP_KEEPINTVL", 15),
                    ("TCP_KEEPCNT", 5),
                ):
                    opt = getattr(socket, opt_name, None)
                    if opt is not None:
                        try:
                            sock.setsockopt(socket.IPPROTO_TCP, opt, opt_val)
                        except Exception:
                            pass
        except Exception:
            # Keepalive is best-effort; do not fail if platform does not support tweaks
            pass
        task = asyncio.current_task()
        assert task is not None
        self._conn_tasks[task] = None
        log.info(f"[Stratum] client connected {peer}")

        # ── protocol detection ──────────────────────────────────────────
        # Sniff the first line. If it's `{"method":"login", ...}` this is
        # a cryptonote pool client (stock xmrig speaking the Monero pool
        # protocol) and we hand the connection off to the cryptonote
        # handler. Otherwise the regular Animica stratum-v1 flow takes
        # over. Lets ONE port serve both algos.
        if self._cryptonote_handler is not None:
            preview_buf = bytearray()
            try:
                while b"\n" not in preview_buf and len(preview_buf) < 8192:
                    chunk = await asyncio.wait_for(
                        reader.read(4096), timeout=10.0
                    )
                    if not chunk:
                        break
                    preview_buf.extend(chunk)
            except asyncio.TimeoutError:
                pass
            except Exception:
                preview_buf = bytearray()

            if preview_buf:
                # Try to parse the first line as JSON
                nl = preview_buf.find(b"\n")
                first_line = bytes(preview_buf[:nl]) if nl != -1 else bytes(preview_buf)
                try:
                    first_obj = json.loads(first_line.decode("utf-8").strip())
                except Exception:
                    first_obj = None
                if isinstance(first_obj, dict) and first_obj.get("method") == "login":
                    # Cryptonote handoff. Build a feeder reader that
                    # replays the bytes we already consumed (the rest of
                    # preview_buf after the first newline, if any), then
                    # streams from the original reader.
                    leftover = bytes(preview_buf[nl + 1:]) if nl != -1 else b""
                    feeder = _ReplayReader(leftover, reader)
                    log.info(
                        "[Stratum] %s spoke cryptonote on first line — "
                        "handing off to xmr handler", peer,
                    )
                    try:
                        await self._cryptonote_handler(feeder, writer, first_obj)
                    finally:
                        try:
                            writer.close()
                            await writer.wait_closed()
                        except Exception:
                            pass
                        self._conn_tasks.pop(task, None)
                    return

                # Not cryptonote — feed the preview bytes back into the
                # normal Animica pipeline so we don't lose the
                # mining.subscribe / authorize that already arrived.
                preserved = bytes(preview_buf)
            else:
                preserved = b""
        else:
            preserved = b""

        # Before subscribe, assume line framing; can be changed after subscribe
        session = self._alloc_session(writer, framing="lines")
        buf = bytearray(preserved)
        hb_task: Optional[asyncio.Task] = None

        try:
            if self._keepalive_secs > 0:
                hb_task = asyncio.create_task(self._session_heartbeat(session))
                self._heartbeats[session.session_id] = hb_task

            # If we consumed preview bytes that contained complete lines,
            # process them now before reading more from the socket.
            if preserved:
                try:
                    decoded = decode_lines(buf)
                except Exception:
                    decoded = []
                for obj in decoded:
                    await self._process_message(session, obj)

            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                session.last_inbound = time.time()

                if session.framing == "lenpref":
                    try:
                        decoded = decode_lenpref(bytearray(chunk))
                    except Exception as e:
                        log.warning(f"[Stratum] decode lenpref error from {peer}: {e}")
                        await self._send(
                            session,
                            make_error(None, RpcErrorCodes.INVALID_REQUEST, str(e)),
                        )
                        continue
                    for obj in decoded:
                        await self._process_message(session, obj)
                else:
                    buf.extend(chunk)
                    try:
                        decoded = decode_lines(buf)
                    except Exception as e:
                        log.warning(f"[Stratum] decode line error from {peer}: {e}")
                        await self._send(
                            session, make_error(None, RpcErrorCodes.PARSE_ERROR, str(e))
                        )
                        continue
                    # decode_lines leaves an un-terminated tail in place; a
                    # client that never sends "\n" would grow it forever.
                    if self._max_line_bytes and len(buf) > self._max_line_bytes:
                        log.warning(
                            f"[Stratum] dropping {peer}: unterminated line "
                            f"exceeded {self._max_line_bytes} bytes"
                        )
                        with suppress(Exception):
                            await self._send(
                                session,
                                make_error(
                                    None,
                                    RpcErrorCodes.INVALID_REQUEST,
                                    "line too long",
                                ),
                            )
                        break
                    for obj in decoded:
                        await self._process_message(session, obj)
        except asyncio.CancelledError:  # pragma: no cover
            pass
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as e:
            # A miner dropped its TCP connection (RST/EPIPE) — normal churn, not a
            # server fault. Log quietly so it doesn't flood the warning stream.
            log.debug(f"[Stratum] client {peer} dropped: {e}")
        except OSError as e:  # pragma: no cover
            if e.errno in (errno.ECONNRESET, errno.EPIPE, errno.ETIMEDOUT,
                           errno.ECONNABORTED, errno.ENOTCONN):
                log.debug(f"[Stratum] client {peer} dropped: {e}")
            else:
                log.warning(f"[Stratum] client {peer} error: {e}")
        except Exception as e:  # pragma: no cover
            log.warning(f"[Stratum] client {peer} error: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            self._sessions.pop(session.session_id, None)
            if hb_task:
                hb_task.cancel()
                with suppress(asyncio.CancelledError):
                    await hb_task
            self._heartbeats.pop(session.session_id, None)
            self._conn_tasks.pop(task, None)
            log.info(
                f"[Stratum] client disconnected {peer} session={session.session_id}"
            )

    # ---------------- JSON-RPC routing ----------------

    async def _process_message(self, session: Session, obj: JSON) -> None:
        # Detect classic Stratum v1 list-style params and normalize into our
        # object-based handlers. This keeps ASIC dashboards happy without
        # breaking existing structured clients.
        raw_params = obj.get("params")
        method_name = obj.get("method")
        # Cryptonote-style keepalive: xmrig sends `keepalived` on its
        # stratum-v1/EthStratum connections too when --keepalive is on
        # (the timer lives in the base Client class). Answer like a
        # cryptonote pool would — replying METHOD_NOT_FOUND makes xmrig
        # treat the connection as broken, drop it on every keepalive
        # round, and eventually park with "no active pools, stop mining".
        if method_name == "keepalived":
            session.touch()
            await self._send(
                session, make_result(obj.get("id"), {"status": "KEEPALIVED"})
            )
            return
        if isinstance(raw_params, list):
            if method_name == Method.SUBSCRIBE.value:
                session.is_v1 = True
                id_val = obj.get("id")
                extranonce1 = session.extranonce1
                if extranonce1.startswith("0x"):
                    extranonce1 = extranonce1[2:]
                reply = res_subscribe_v1(
                    id_val,
                    extranonce1=extranonce1,
                    extranonce2_size=session.extranonce2_size,
                )
                await self._send(session, reply)
                await self._push_difficulty(
                    session, session.share_target, session.theta_micro
                )
                if self._current_job_id:
                    job = self._jobs[self._current_job_id]
                    await self._send(
                        session, self._build_v1_notify(job, clean_jobs=True)
                    )
                return
            if method_name == Method.AUTHORIZE.value:
                session.is_v1 = True
                identity = raw_params[0] if raw_params else None
                password = raw_params[1] if len(raw_params) > 1 else None
                worker, address = _parse_worker_identity(identity)
                session.worker = worker
                session.address = address
                mode_hint = (
                    _parse_pool_mode_hint(password)
                    or _parse_pool_mode_hint(identity)
                    or _parse_pool_mode_hint(raw_params)
                )
                if not session.solo_forced:
                    session.pool_mode = self._resolve_session_pool_mode(
                        mode_hint, current_mode=session.pool_mode
                    )
                session.authorized = True
                session.touch()
                await self._send(session, res_authorize_v1(obj.get("id"), True))
                await self._resync_session_target(session)
                return
            if method_name == Method.SUBMIT.value:
                mapped = {
                    "worker": raw_params[0] if len(raw_params) > 0 else None,
                    "jobId": raw_params[1] if len(raw_params) > 1 else None,
                    "extranonce2": raw_params[2] if len(raw_params) > 2 else None,
                    "ntime": raw_params[3] if len(raw_params) > 3 else None,
                    "nonce": raw_params[4] if len(raw_params) > 4 else None,
                }
                # Build a hashshare-shaped payload to reuse validators
                nonce_hex = mapped.get("nonce") or ""
                if isinstance(nonce_hex, str) and not nonce_hex.startswith("0x"):
                    nonce_hex = "0x" + nonce_hex
                mapped_params: JSON = {
                    "worker": mapped.get("worker") or "",
                    "jobId": mapped.get("jobId") or "",
                    "extranonce2": mapped.get("extranonce2") or "",
                    "hashshare": {
                        "nonce": nonce_hex,
                        "body": {"ntime": mapped.get("ntime")},
                    },
                    "ntime": mapped.get("ntime"),
                    "nonce": mapped.get("nonce"),
                }
                obj = {
                    "jsonrpc": "2.0",
                    "id": obj.get("id"),
                    "method": method_name,
                    "params": mapped_params,
                }

        try:
            method, id_val, params = validate_request(obj)
        except (InvalidRequest, InvalidParams, MethodNotFound) as e:
            err = make_error(obj.get("id"), int(e.code), str(e))
            await self._send(session, err)
            return

        session.touch()

        if method == Method.SUBSCRIBE:
            features = params.get("features") or {}
            framing = features.get("framing", "lines")
            if framing not in ("lines", "lenpref"):
                framing = "lines"
            session.framing = framing
            # Stash the full features dict so the AUTHORIZE handler can
            # forward miner-advertised AICF tiers + hardware to the worker
            # registration hook. Miners that don't serve inference simply
            # omit `aicf` from their features and get registered with no
            # tiers (so they're never selected for inference jobs).
            try:
                session.subscribed_features = dict(features)
            except Exception:
                session.subscribed_features = {}
            # Sub-block share opt-in. A client must ASK for a target below the
            # block target; we never infer capability, because a miner that
            # mishandles an unexpected set_difficulty drops its connection and
            # stops mining (that failure cost ~2h of mainnet block production on
            # 2026-07-10). Unknown/absent feature => unchanged behaviour.
            session.supports_subblock = _parse_subblock_optin(features)
            agent = params.get("agent", "unknown")
            log.info(
                f"[Stratum] subscribe agent={agent} framing={framing} "
                f"subblock={session.supports_subblock} session={session.session_id}"
            )
            reply = res_subscribe(
                id_val,
                session_id=session.session_id,
                extranonce1=session.extranonce1,
                extranonce2_size=session.extranonce2_size,
                framing=framing,
            )
            await self._send(session, reply)

            # Push current difficulty & job if any
            await self._push_difficulty(
                session, session.share_target, session.theta_micro
            )
            if self._current_job_id:
                job = self._jobs[self._current_job_id]
                await self._send(
                    session,
                    push_notify(
                        job.job_id,
                        job.header,
                        float(session.share_target)
                        if float(session.share_target or 0.0) > 0.0
                        else job.share_target,
                        True,
                        job.hints or {},
                    ),
                )

        elif method == Method.AUTHORIZE:
            worker_input = params.get("worker")
            address_input = params.get("address")
            parsed_worker, parsed_address = _parse_worker_identity(worker_input)
            session.worker = str(worker_input).strip() if worker_input else parsed_worker
            session.address = (
                str(address_input).strip()
                if address_input
                else parsed_address
            )
            mode_hint = self._mode_hint_from_authorize(params)
            if not session.solo_forced:
                session.pool_mode = self._resolve_session_pool_mode(
                    mode_hint, current_mode=session.pool_mode
                )
            session.authorized = (
                True  # Add real checks here if desired (e.g., bech32 format)
            )
            await self._send(session, res_authorize(id_val, True))
            log.info(
                f"[Stratum] authorize worker={session.worker} address={session.address} mode={session.pool_mode} session={session.session_id}"
            )
            # The mode is only final here (authorize can flip pps<->solo), so
            # re-apply the sub-block policy now.
            await self._resync_session_target(session)
            # Fire-and-forget AICF worker registration. Miners that
            # advertised aicf features in mining.subscribe get registered
            # under their payout address so the local node's aicf job
            # queue can route inference work to them. Failures here must
            # not break mining — log and move on.
            if self._on_worker_authorized is not None:
                features = getattr(session, "subscribed_features", {}) or {}
                try:
                    await self._on_worker_authorized(session, features)
                except Exception as exc:
                    log.warning(
                        "[Stratum] on_worker_authorized hook failed for "
                        f"session={session.session_id}: {exc}"
                    )

        elif method == Method.SET_DIFFICULTY:
            # Clients should not be sending this; treat as request to fetch current settings
            await self._send(
                session,
                make_result(
                    id_val,
                    {
                        "shareTarget": session.share_target,
                        "thetaMicro": session.theta_micro,
                    },
                ),
            )

        elif method == Method.NOTIFY:
            # Server-only method; ignore
            await self._send(
                session,
                make_error(
                    id_val, RpcErrorCodes.INVALID_REQUEST, "notify is server-push only"
                ),
            )

        elif method == Method.SUBMIT:
            # Validate job and share via validator
            job_id = params.get("jobId")
            self._prune_jobs()
            if job_id not in self._jobs:
                await self._send(
                    session,
                    make_error(id_val, RpcErrorCodes.STALE_JOB, "unknown or stale job"),
                )
                return
            job = self._jobs[job_id]
            if job.expires_at and time.time() >= job.expires_at:
                await self._send(
                    session,
                    make_error(id_val, RpcErrorCodes.STALE_JOB, "job expired"),
                )
                return
            log.debug(
                "[Stratum] submit job_matched worker=%s session=%s submitJob=%s currentJob=%s",
                session.worker,
                session.session_id,
                job_id,
                self._current_job_id,
            )
            params_with_context = dict(params)
            resolved_address = (
                str(session.address).strip() if session.address else None
            )
            if not resolved_address:
                _worker, resolved_address = _parse_worker_identity(session.worker)
                if resolved_address:
                    session.address = resolved_address
            params_with_context["_session_id"] = session.session_id
            params_with_context["_worker"] = session.worker
            params_with_context["_address"] = resolved_address
            params_with_context["_pool_mode"] = session.pool_mode
            # The share target is the POOL's assignment, never the miner's
            # claim. PPS credit is priced from this ratio
            # (credit_fraction = exp(-θ(1-r)/MICRO)), so a miner that mines the
            # easy sub-block target and then claims d_ratio/shareTarget = 1.0
            # would be paid a full block reward per share — 64x over-credit at
            # S=64, silently, because validation uses the session target
            # (_job_for_session) while pricing used to trust these fields.
            # Harmless before 9.1.0 (every session was pinned at 1.0, the
            # maximum); exploitable the moment sub-block targets exist. OVERWRITE
            # both, unconditionally.
            params_with_context["shareTarget"] = float(session.share_target)
            params_with_context["d_ratio"] = float(session.share_target)
            # Replay guard: the same solution must never be credited twice.
            share_key = _share_identity(params)
            if share_key is not None and share_key in self._seen_shares.get(job_id, ()):
                self._rejected += 1
                session.shares_rejected += 1
                log.warning(
                    f"[Stratum] duplicate share worker={session.worker} "
                    f"session={session.session_id} job={job_id}"
                )
                if session.is_v1:
                    await self._send(
                        session, res_submit_v1(id_val, False, reason="duplicate share")
                    )
                else:
                    await self._send(
                        session,
                        res_submit(
                            id_val, False, reason="duplicate share",
                            is_block=False, tx_count=0,
                        ),
                    )
                return
            job_for_validation = self._job_for_session(session, job)
            ok, reason, is_block, tx_count = await self._validator.validate(
                job_for_validation, params_with_context
            )
            if ok:
                self._accepted += 1
                session.shares_accepted += 1
                # Record only on ACCEPT: a share rejected for a transient
                # reason (notably transport_error on the node submit) must stay
                # re-submittable, or a timeout would permanently burn a
                # solution — including a block.
                if share_key is not None:
                    self._seen_shares.setdefault(job_id, set()).add(share_key)
            else:
                self._rejected += 1
                session.shares_rejected += 1
            session.last_share_at = time.time()
            session.last_share_status = "accepted" if ok else "rejected"
            # Same rule as pricing: report the target we ASSIGNED, not what the
            # miner claimed, so dashboards and /api/miners can't be spoofed either.
            share_ratio = float(
                session.share_target or job_for_validation.share_target or 0.0
            )
            session.current_difficulty = (
                share_target_to_difficulty(session.theta_micro, share_ratio)
                if session.is_v1
                else share_ratio
            )
            if session.is_v1:
                await self._send(session, res_submit_v1(id_val, ok, reason=reason))
            else:
                await self._send(
                    session,
                    res_submit(
                        id_val, ok, reason=reason, is_block=is_block, tx_count=tx_count
                    ),
                )
            if ok:
                log.debug(
                    "[Stratum] submit worker=%s session=%s job=%s ok=%s block=%s reason=%s diff=%s",
                    session.worker,
                    session.session_id,
                    job_id,
                    ok,
                    is_block,
                    reason,
                    session.current_difficulty,
                )
            else:
                log.warning(
                    "[Stratum] submit worker=%s session=%s job=%s ok=%s block=%s reason=%s diff=%s",
                    session.worker,
                    session.session_id,
                    job_id,
                    ok,
                    is_block,
                    reason,
                    session.current_difficulty,
                )
            await self._maybe_adjust_session_share_target(
                session,
                ok=ok,
                reason=reason,
            )
            if self._submit_hook is not None:
                await self._submit_hook(
                    session,
                    job_for_validation,
                    params_with_context,
                    ok,
                    reason,
                    is_block,
                    tx_count,
                )

        elif method == Method.GET_VERSION:
            await self._send(
                session,
                make_result(id_val, {"name": "animica-stratum", "version": "0.1.0"}),
            )

        elif method == Method.AICF_NOTIFY:
            # Server-push-only — miners must not send this direction.
            await self._send(
                session,
                make_error(
                    id_val, RpcErrorCodes.INVALID_REQUEST,
                    "mining.aicf.notify is server-push only",
                ),
            )

        elif method == Method.AICF_SUBMIT:
            if not session.authorized:
                await self._send(
                    session,
                    make_error(id_val, RpcErrorCodes.UNAUTHORIZED, "not authorized"),
                )
                return
            job_id = str(params.get("jobId") or "")
            text = params.get("text")
            if not job_id or text is None:
                await self._send(
                    session,
                    make_error(
                        id_val, RpcErrorCodes.INVALID_PARAMS,
                        "aicf.submit requires jobId and text",
                    ),
                )
                return
            if self._on_aicf_result is None:
                # No relay configured (server running standalone); accept
                # the result but warn that nothing was persisted.
                log.warning(
                    "[Stratum] aicf.submit received but on_aicf_result is "
                    f"not configured; dropping result for job={job_id}"
                )
                await self._send(session, res_aicf_submit(id_val, False,
                                                          reason="relay_unavailable"))
                return
            try:
                relay_result = await self._on_aicf_result(session, dict(params))
            except Exception as exc:
                log.warning(
                    f"[Stratum] aicf.submit relay failed job={job_id}: {exc}"
                )
                await self._send(session, res_aicf_submit(id_val, False,
                                                          reason=str(exc)))
                return
            accepted = bool(relay_result.get("accepted"))
            state = relay_result.get("state")
            reason = relay_result.get("reason")
            await self._send(session, res_aicf_submit(id_val, accepted,
                                                      state=state, reason=reason))

        elif method == Method.XMR_NOTIFY:
            await self._send(
                session,
                make_error(
                    id_val, RpcErrorCodes.INVALID_REQUEST,
                    "mining.notify_xmr is server-push only",
                ),
            )

        elif method == Method.XMR_SUBMIT:
            if not session.authorized:
                await self._send(
                    session,
                    make_error(id_val, RpcErrorCodes.UNAUTHORIZED, "not authorized"),
                )
                return
            if self._on_xmr_result is None:
                await self._send(
                    session,
                    make_error(
                        id_val, RpcErrorCodes.METHOD_NOT_FOUND,
                        "XMR mining not enabled on this pool",
                    ),
                )
                return
            job_id = str(params.get("jobId") or params.get("job_id") or "")
            nonce_hex = str(params.get("nonce") or "")
            result_hex = str(params.get("result") or "")
            if not job_id or not nonce_hex or not result_hex:
                await self._send(
                    session,
                    make_error(
                        id_val, RpcErrorCodes.INVALID_PARAMS,
                        "submit_xmr requires jobId, nonce, result",
                    ),
                )
                return
            try:
                relay_result = await self._on_xmr_result(session, dict(params))
            except Exception as exc:
                log.warning(
                    f"[Stratum] submit_xmr relay failed job={job_id}: {exc}"
                )
                await self._send(
                    session,
                    make_error(id_val, RpcErrorCodes.INTERNAL_ERROR, str(exc)),
                )
                return
            await self._send(
                session,
                make_result(id_val, {
                    "accepted": bool(relay_result.get("accepted")),
                    "reason": relay_result.get("reason"),
                    "block_solved": bool(relay_result.get("block_solved", False)),
                }),
            )

        else:  # pragma: no cover - exhaustive enum
            await self._send(
                session,
                make_error(id_val, RpcErrorCodes.METHOD_NOT_FOUND, "unknown method"),
            )

    # ---------------- diagnostics ----------------

    def stats(self) -> JSON:
        mode_counts = {"pps": 0, "solo": 0}
        authorized = 0
        unique_addresses: set[str] = set()
        for session in self._sessions.values():
            mode = _normalize_pool_mode(
                session.pool_mode, default=self._default_session_pool_mode
            )
            if mode in mode_counts:
                mode_counts[mode] += 1
            if session.authorized:
                authorized += 1
                addr = (session.address or "").strip()
                if addr:
                    unique_addresses.add(addr)
        return {
            # `clients` is raw open sessions — an fd/abuse metric, NOT a miner
            # count (one client can hold hundreds of sockets). Miner-facing
            # stats must use `unique_miners` / the summary's num_miners.
            "clients": len(self._sessions),
            "authorized_sessions": authorized,
            "unique_miners": len(unique_addresses),
            "subblock_sessions": sum(
                1
                for s in self._sessions.values()
                if s.supports_subblock and not s.is_v1
            ),
            "refused_connections": self._refused_total,
            "capped_ips": sum(
                1
                for ip, n in self._ip_conn_counts.items()
                if self._max_sessions_per_ip and n >= self._max_sessions_per_ip
            ),
            "accepted": self._accepted,
            "rejected": self._rejected,
            "uptime_sec": int(time.time() - self._started_ts),
            "currentJob": self._current_job_id,
            "activeJobs": len(self._jobs),
            "pool_mode": self._pool_mode,
            "session_modes": mode_counts,
        }

    def session_snapshots(self) -> List[JSON]:
        return [
            {
                "session_id": s.session_id,
                "worker": s.worker,
                "address": s.address,
                "peer_ip": s.peer_ip,
                "pool_mode": s.pool_mode,
                "authorized": s.authorized,
                "supports_subblock": s.supports_subblock,
                "share_target": s.share_target,
                "theta_micro": s.theta_micro,
                "last_seen": s.last_seen,
                "connected_since": s.connected_since,
                "last_share_at": s.last_share_at,
                "last_share_status": s.last_share_status,
                "shares_accepted": s.shares_accepted,
                "shares_rejected": s.shares_rejected,
                "current_difficulty": s.current_difficulty,
            }
            for s in self._sessions.values()
        ]

    def set_submit_hook(
        self,
        hook: Optional[
            Callable[
                [Session, StratumJob, JSON, bool, Optional[str], bool, int],
                Awaitable[None],
            ]
        ],
    ) -> None:
        self._submit_hook = hook


# --------------------------------------------------------------------------------------
# Small demo runner (manual testing)
# --------------------------------------------------------------------------------------


async def _demo() -> None:  # pragma: no cover
    server = StratumServer()
    await server.start()

    # Build a toy job if a template helper exists
    header = {
        "parentHash": "0x" + "00" * 32,
        "number": 1,
        "thetaMicro": 800000,
        "mixSeed": "0x" + "11" * 32,
        "roots": {
            "stateRoot": "0x" + "22" * 32,
            "txsRoot": "0x" + "33" * 32,
            "proofsRoot": "0x" + "44" * 32,
            "daRoot": "0x" + "55" * 32,
        },
        "chainId": 1,
        "nonceDomain": "animica.hashshare.v1",
    }
    job = StratumJob(
        job_id=uuid.uuid4().hex[:16],
        header=header,
        share_target=0.02,
        theta_micro=800_000,
        hints={
            "mixSeed": header["mixSeed"],
            "proofCaps": {"ai": True, "quantum": True, "storage": True, "vdf": True},
        },
    )
    await server.publish_job(job)

    # Run until Ctrl-C
    try:
        while True:
            await asyncio.sleep(5)
            log.info(f"[Stratum] stats: {server.stats()}")
    except KeyboardInterrupt:
        pass
    finally:
        await server.stop()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_demo())
