from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Callable, Awaitable, Tuple, List

from .stratum_protocol import (
    Method,
    RpcErrorCodes,
    InvalidRequest,
    InvalidParams,
    MethodNotFound,
    validate_request,
    make_result,
    make_error,
    req_subscribe,
    res_subscribe,
    res_authorize,
    res_authorize_v1,
    push_set_difficulty,
    push_set_difficulty_v1,
    push_notify,
    push_notify_v1,
    req_submit,
    res_submit,
    res_submit_v1,
    encode_lines,
    decode_lines,
    encode_lenpref,
    decode_lenpref,
)

try:
    # Prefer our shared logger if present
    from core.logging import get_logger  # type: ignore
except Exception:  # pragma: no cover
    def get_logger(name: str) -> logging.Logger:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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
    header: JSON           # header template (deterministic, sign-bytes ready)
    share_target: float    # micro-target difficulty for shares
    theta_micro: int       # current Θ in µ-nats
    hints: Optional[JSON] = None
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


# --------------------------------------------------------------------------------------
# Validator interface (pluggable)
# --------------------------------------------------------------------------------------

class ShareValidator:
    """
    Pluggable share validator. The default implementation performs structural
    checks and defers to optional adapters if available.
    """
    async def validate(self, job: StratumJob, submit_params: JSON) -> Tuple[bool, Optional[str], bool, int]:
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
            from mining.adapters.proofs_view import verify_hashshare_envelope  # type: ignore
            ok, reason, is_block, tx_count = await verify_hashshare_envelope(job.header, submit_params)
            return ok, reason, is_block, tx_count
        except Exception as e:
            # Fallback to lightweight sanity checks
            pass

        # Lightweight checks (structural only). Full security comes from adapters.
        hs = submit_params.get("hashshare") or {}
        nonce = hs.get("nonce")
        body = hs.get("body")
        if not isinstance(nonce, str) or not nonce.startswith("0x"):
            return False, "nonce must be hex", False, 0
        if not isinstance(body, dict):
            return False, "hashshare.body must be object", False, 0

        # Basic jobId check performed by server; here ensure header hash matches template if provided
        # (We accept in fallback mode; target enforcement delegated to server difficulty heuristics.)
        return True, None, False, 0


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
        validator: Optional[ShareValidator] = None,
        submit_hook: Optional[
            Callable[[Session, StratumJob, JSON, bool, Optional[str], bool, int], Awaitable[None]]
        ] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._server: Optional[asyncio.AbstractServer] = None
        self._sessions: Dict[str, Session] = {}
        self._conn_tasks: Dict[asyncio.Task, None] = {}
        self._jobs: Dict[str, StratumJob] = {}
        self._current_job_id: Optional[str] = None
        self._extranonce2_size = int(extranonce2_size)
        self._default_share_target = float(default_share_target)
        self._default_theta_micro = int(default_theta_micro)
        self._validator = validator or ShareValidator()
        self._submit_hook = submit_hook

        # Stats
        self._accepted = 0
        self._rejected = 0
        self._started_ts = time.time()

    # ---------------- lifecycle ----------------

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        sockets = ", ".join(str(s.getsockname()) for s in self._server.sockets or [])
        log.info(f"[Stratum] listening on {sockets}")

    async def stop(self) -> None:
        for task in list(self._conn_tasks.keys()):
            task.cancel()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self._server = None
        log.info("[Stratum] stopped")

    # ---------------- job control ----------------

    async def publish_job(self, job: StratumJob) -> None:
        """
        Publish a new job (header template) to all connected sessions.
        """
        self._jobs[job.job_id] = job
        self._current_job_id = job.job_id
        await self._broadcast_job(job, clean_jobs=True)
        log.info(f"[Stratum] notify job={job.job_id} θμ={job.theta_micro} shareTarget={job.share_target}")

    async def set_global_difficulty(self, share_target: float, theta_micro: Optional[int] = None) -> None:
        if theta_micro is None:
            theta_micro = self._default_theta_micro
        msg = push_set_difficulty(share_target=share_target, theta_micro=theta_micro)
        for s in self._sessions.values():
            s.share_target = share_target
            s.theta_micro = theta_micro
            if s.is_v1:
                await self._send(s, push_set_difficulty_v1(share_target))
            else:
                await self._send(s, msg)
        log.info(f"[Stratum] set difficulty shareTarget={share_target} θμ={theta_micro}")

    async def _broadcast_job(self, job: StratumJob, clean_jobs: bool) -> None:
        """Send a job to each session in the format it expects."""
        dead: List[str] = []
        for sid, s in self._sessions.items():
            try:
                if s.is_v1:
                    msg = self._build_v1_notify(job, clean_jobs=clean_jobs)
                else:
                    msg = push_notify(
                        job_id=job.job_id,
                        header=job.header,
                        share_target=job.share_target,
                        clean_jobs=clean_jobs,
                        hints=job.hints or {},
                    )
                await self._send(s, msg)
            except Exception as e:  # pragma: no cover
                log.warning(f"[Stratum] broadcast job to {sid} failed: {e}")
                dead.append(sid)
        for sid in dead:
            self._sessions.pop(sid, None)

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
        ntime = header.get("timestamp") or header.get("ntime") or header.get("time") or int(time.time())
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

    def _alloc_session(self, writer: asyncio.StreamWriter, framing: str = "lines") -> Session:
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
        )
        self._sessions[sid] = s
        return s

    async def _broadcast(self, obj: JSON) -> None:
        dead: List[str] = []
        for sid, s in self._sessions.items():
            try:
                await self._send(s, obj)
            except Exception as e:  # pragma: no cover - best-effort
                log.warning(f"[Stratum] broadcast to {sid} failed: {e}")
                dead.append(sid)
        for sid in dead:
            self._sessions.pop(sid, None)

    async def _send(self, session: Session, obj: JSON) -> None:
        if session.framing == "lenpref":
            payload = encode_lenpref(obj)
        else:
            payload = encode_lines(obj)
        session.writer.write(payload)
        await session.writer.drain()

    # ---------------- connection handler ----------------

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        task = asyncio.current_task()
        assert task is not None
        self._conn_tasks[task] = None
        log.info(f"[Stratum] client connected {peer}")

        # Before subscribe, assume line framing; can be changed after subscribe
        session = self._alloc_session(writer, framing="lines")
        buf = bytearray()

        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break

                if session.framing == "lenpref":
                    for obj in decode_lenpref(bytearray(chunk)):
                        await self._process_message(session, obj)
                else:
                    buf.extend(chunk)
                    for obj in decode_lines(buf):
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
            self._conn_tasks.pop(task, None)
            log.info(f"[Stratum] client disconnected {peer} session={session.session_id}")

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
                reply = res_subscribe_v1(id_val, extranonce1=extranonce1, extranonce2_size=session.extranonce2_size)
                await self._send(session, reply)
                await self._send(session, push_set_difficulty_v1(session.share_target))
                if self._current_job_id:
                    job = self._jobs[self._current_job_id]
                    await self._send(session, self._build_v1_notify(job, clean_jobs=True))
                return
            if method_name == Method.AUTHORIZE.value:
                session.is_v1 = True
                session.worker = raw_params[0] if raw_params else None
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
                    "hashshare": {"nonce": nonce_hex, "body": {"ntime": mapped.get("ntime")}},
                    "ntime": mapped.get("ntime"),
                    "nonce": mapped.get("nonce"),
                }
                obj = {"jsonrpc": "2.0", "id": obj.get("id"), "method": method_name, "params": mapped_params}

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
            log.info(f"[Stratum] subscribe agent={agent} framing={framing} session={session.session_id}")
            reply = res_subscribe(
                id_val,
                session_id=session.session_id,
                extranonce1=session.extranonce1,
                extranonce2_size=session.extranonce2_size,
                framing=framing,
            )
            await self._send(session, reply)

            # Push current difficulty & job if any
            await self._send(session, push_set_difficulty(session.share_target, session.theta_micro))
            if self._current_job_id:
                job = self._jobs[self._current_job_id]
                await self._send(session, push_notify(job.job_id, job.header, job.share_target, True, job.hints or {}))

        elif method == Method.AUTHORIZE:
            session.worker = params.get("worker")
            session.address = params.get("address")
            session.authorized = True  # Add real checks here if desired (e.g., bech32 format)
            await self._send(session, res_authorize(id_val, True))
            log.info(f"[Stratum] authorize worker={session.worker} address={session.address} session={session.session_id}")

        elif method == Method.SET_DIFFICULTY:
            # Clients should not be sending this; treat as request to fetch current settings
            await self._send(session, make_result(id_val, {"shareTarget": session.share_target, "thetaMicro": session.theta_micro}))

        elif method == Method.NOTIFY:
            # Server-only method; ignore
            await self._send(session, make_error(id_val, RpcErrorCodes.INVALID_REQUEST, "notify is server-push only"))

        elif method == Method.SUBMIT:
            # Validate job and share via validator
            job_id = params.get("jobId")
            if job_id not in self._jobs:
                await self._send(session, make_error(id_val, RpcErrorCodes.STALE_JOB, "unknown or stale job"))
                return
            job = self._jobs[job_id]
            ok, reason, is_block, tx_count = await self._validator.validate(job, params)
            if ok:
                self._accepted += 1
                session.shares_accepted += 1
            else:
                self._rejected += 1
                session.shares_rejected += 1
            session.last_share_at = time.time()
            session.last_share_status = "accepted" if ok else "rejected"
            session.current_difficulty = float(params.get("d_ratio") or params.get("shareTarget") or job.share_target)
            if session.is_v1:
                await self._send(session, res_submit_v1(id_val, ok, reason=reason))
            else:
                await self._send(session, res_submit(id_val, ok, reason=reason, is_block=is_block, tx_count=tx_count))
            level = logging.INFO if ok else logging.WARNING
            log.log(level, f"[Stratum] submit worker={session.worker} job={job_id} ok={ok} reason={reason}")
            if self._submit_hook is not None:
                await self._submit_hook(session, job, params, ok, reason, is_block, tx_count)

        elif method == Method.GET_VERSION:
            await self._send(session, make_result(id_val, {"name": "animica-stratum", "version": "0.1.0"}))

        else:  # pragma: no cover - exhaustive enum
            await self._send(session, make_error(id_val, RpcErrorCodes.METHOD_NOT_FOUND, "unknown method"))

    # ---------------- diagnostics ----------------

    def stats(self) -> JSON:
        return {
            "clients": len(self._sessions),
            "accepted": self._accepted,
            "rejected": self._rejected,
            "uptime_sec": int(time.time() - self._started_ts),
            "currentJob": self._current_job_id,
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
            Callable[[Session, StratumJob, JSON, bool, Optional[str], bool, int], Awaitable[None]]
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
        "parentHash": "0x" + "00"*32,
        "number": 1,
        "thetaMicro": 800000,
        "mixSeed": "0x" + "11"*32,
        "roots": {
            "stateRoot": "0x" + "22"*32,
            "txsRoot": "0x" + "33"*32,
            "proofsRoot": "0x" + "44"*32,
            "daRoot": "0x" + "55"*32,
        },
        "chainId": 1,
        "nonceDomain": "animica.hashshare.v1",
    }
    job = StratumJob(
        job_id=uuid.uuid4().hex[:16],
        header=header,
        share_target=0.02,
        theta_micro=800_000,
        hints={"mixSeed": header["mixSeed"], "proofCaps": {"ai": True, "quantum": True, "storage": True, "vdf": True}},
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
