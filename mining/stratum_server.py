from __future__ import annotations

import asyncio
import json
import logging
import secrets
import socket
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from .hash_search import digest_to_int256
from .pow_validation import (derive_block_target_int, digest_from_sign_bytes,
                             evaluate_digest, parse_hex_bytes)
from .stratum_protocol import (InvalidParams, InvalidRequest, Method,
                               MethodNotFound, RpcErrorCodes, decode_lenpref,
                               decode_lines, encode_lenpref, encode_lines,
                               make_error, make_result, push_notify,
                               push_notify_v1, push_set_difficulty,
                               push_set_difficulty_v1, req_submit,
                               req_subscribe, res_authorize, res_authorize_v1,
                               res_submit, res_submit_v1, res_subscribe,
                               res_subscribe_v1, validate_request)
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
    is_v1: bool = False
    subscription_ids: Tuple[str, str] = ("subscription-id-1", "subscription-id-2")

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
        send_timeout_secs: float = 1.0,
        max_cached_jobs: int = 64,
        validator: Optional[ShareValidator] = None,
        submit_hook: Optional[
            Callable[
                [Session, StratumJob, JSON, bool, Optional[str], bool, int],
                Awaitable[None],
            ]
        ] = None,
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
        self._send_timeout_secs = max(float(send_timeout_secs), 0.0)
        self._validator = validator or ShareValidator()
        self._submit_hook = submit_hook

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
                        share_target=job.share_target,
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
        except Exception:
            # Keepalive is best-effort; do not fail if platform does not support tweaks
            pass
        task = asyncio.current_task()
        assert task is not None
        self._conn_tasks[task] = None
        log.info(f"[Stratum] client connected {peer}")

        # Before subscribe, assume line framing; can be changed after subscribe
        session = self._alloc_session(writer, framing="lines")
        buf = bytearray()
        hb_task: Optional[asyncio.Task] = None

        try:
            if self._keepalive_secs > 0:
                hb_task = asyncio.create_task(self._session_heartbeat(session))
                self._heartbeats[session.session_id] = hb_task

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
                worker, address = _parse_worker_identity(identity)
                session.worker = worker
                session.address = address
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
                        job.job_id, job.header, job.share_target, True, job.hints or {}
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
            session.authorized = (
                True  # Add real checks here if desired (e.g., bech32 format)
            )
            await self._send(session, res_authorize(id_val, True))
            log.info(
                f"[Stratum] authorize worker={session.worker} address={session.address} session={session.session_id}"
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
            log.info(
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
            ok, reason, is_block, tx_count = await self._validator.validate(
                job, params_with_context
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
                params.get("d_ratio") or params.get("shareTarget") or job.share_target
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
            level = logging.INFO if ok else logging.WARNING
            log.log(
                level,
                "[Stratum] submit worker=%s session=%s job=%s ok=%s block=%s reason=%s diff=%s",
                session.worker,
                session.session_id,
                job_id,
                ok,
                is_block,
                reason,
                session.current_difficulty,
            )
            if self._submit_hook is not None:
                await self._submit_hook(
                    session, job, params_with_context, ok, reason, is_block, tx_count
                )

        elif method == Method.GET_VERSION:
            await self._send(
                session,
                make_result(id_val, {"name": "animica-stratum", "version": "0.1.0"}),
            )

        else:  # pragma: no cover - exhaustive enum
            await self._send(
                session,
                make_error(id_val, RpcErrorCodes.METHOD_NOT_FOUND, "unknown method"),
            )

    # ---------------- diagnostics ----------------

    def stats(self) -> JSON:
        return {
            "clients": len(self._sessions),
            "accepted": self._accepted,
            "rejected": self._rejected,
            "uptime_sec": int(time.time() - self._started_ts),
            "currentJob": self._current_job_id,
            "activeJobs": len(self._jobs),
        }

    def session_snapshots(self) -> List[JSON]:
        return [
            {
                "session_id": s.session_id,
                "worker": s.worker,
                "address": s.address,
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
