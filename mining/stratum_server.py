from __future__ import annotations

import asyncio
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
    extranonce1: Hex = ""
    extranonce2_size: int = 8
    worker: Optional[str] = None
    address: Optional[str] = None
    pool_mode: str = "pps"
    authorized: bool = False
    share_target: float = 0.01
    theta_micro: int = 800_000
    last_seen: float = field(default_factory=lambda: time.time())
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
    subscription_ids: Tuple[str, str] = ("subscription-id-1", "subscription-id-2")
    # Features dict the miner sent on mining.subscribe. Captured so the
    # AUTHORIZE hook can forward AICF tier / hardware metadata to the
    # worker-registration callback without re-parsing the wire payload.
    subscribed_features: Dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        self.last_seen = time.time()


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
        send_timeout_secs: float = 15.0,
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
        session_vardiff_enabled: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._server: Optional[asyncio.AbstractServer] = None
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
        self._session_low_reject_streak_threshold = 3
        self._session_high_accept_streak_threshold = 24
        base_target = max(float(self._default_share_target), 1e-9)
        self._session_min_share_target = max(1e-9, base_target * 0.05)
        self._session_max_share_target = min(1.0, max(base_target * 1.5, base_target))
        if self._session_max_share_target < self._session_min_share_target:
            self._session_max_share_target = self._session_min_share_target

        # Stats
        self._accepted = 0
        self._rejected = 0
        self._started_ts = time.time()

        # Background heartbeat tasks per session
        self._heartbeats: Dict[str, asyncio.Task] = {}

    # ---------------- lifecycle ----------------

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )
        sockets = ", ".join(str(s.getsockname()) for s in self._server.sockets or [])
        log.info(f"[Stratum] listening on {sockets}")

    async def stop(self) -> None:
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
            with suppress(ValueError):
                self._job_order.remove(jid)
            if jid == self._current_job_id:
                self._current_job_id = None

        while len(self._job_order) > self._max_cached_jobs:
            drop_id = self._job_order.pop(0)
            self._jobs.pop(drop_id, None)
            if drop_id == self._current_job_id:
                self._current_job_id = self._job_order[-1] if self._job_order else None

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
            s.share_target = share_target
            s.theta_micro = theta_micro
            s.current_difficulty = difficulty if s.is_v1 else share_target
            if s.is_v1:
                sends.append((sid, s, push_set_difficulty_v1(difficulty)))
            else:
                sends.append((sid, s, msg))
        dead = await self._send_batch(sends, context="set_difficulty")
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
        s = Session(
            session_id=sid,
            writer=writer,
            framing=framing,
            extranonce1=extranonce1,
            extranonce2_size=self._extranonce2_size,
            pool_mode=self._default_session_pool_mode,
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
        dead = await self._send_batch(sends, context="broadcast")
        for sid in dead:
            await self._drop_session(sid)

    async def _send_batch(
        self,
        sends: List[Tuple[str, Session, JSON]],
        *,
        context: str,
        on_success: Optional[Callable[[str, Session], None]] = None,
    ) -> List[str]:
        if not sends:
            return []
        outcomes = await asyncio.gather(
            *(
                self._send_with_timeout(sid, session, payload, context=context)
                for sid, session, payload in sends
            )
        )
        dead: List[str] = []
        for (sid, session, _), ok in zip(sends, outcomes):
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
        self._sessions.pop(sid, None)
        task = self._heartbeats.pop(sid, None)
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

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
        peer = writer.get_extra_info("peername")
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
                session.pool_mode = self._resolve_session_pool_mode(
                    mode_hint, current_mode=session.pool_mode
                )
                session.authorized = True
                await self._send(session, res_authorize_v1(obj.get("id"), True))
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
            agent = params.get("agent", "unknown")
            log.info(
                f"[Stratum] subscribe agent={agent} framing={framing} session={session.session_id}"
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
            if params_with_context.get("shareTarget") in (None, ""):
                params_with_context["shareTarget"] = float(session.share_target)
            job_for_validation = self._job_for_session(session, job)
            ok, reason, is_block, tx_count = await self._validator.validate(
                job_for_validation, params_with_context
            )
            if ok:
                self._accepted += 1
                session.shares_accepted += 1
            else:
                self._rejected += 1
                session.shares_rejected += 1
            session.last_share_at = time.time()
            session.last_share_status = "accepted" if ok else "rejected"
            share_ratio = float(
                params.get("d_ratio")
                or params.get("shareTarget")
                or session.share_target
                or job_for_validation.share_target
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
        for session in self._sessions.values():
            mode = _normalize_pool_mode(
                session.pool_mode, default=self._default_session_pool_mode
            )
            if mode in mode_counts:
                mode_counts[mode] += 1
        return {
            "clients": len(self._sessions),
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
                "pool_mode": s.pool_mode,
                "authorized": s.authorized,
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
