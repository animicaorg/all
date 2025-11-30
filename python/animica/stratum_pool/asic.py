from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List, Optional

from mining.share_submitter import JsonRpcClient

JsonDict = Dict[str, Any]


D1_TARGET = (0xFFFF) * 2 ** (8 * (0x1D - 3))


def _double_sha(data: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def _bits_to_target(bits_hex: str) -> int:
    bits = int(bits_hex, 16)
    exponent = bits >> 24
    mantissa = bits & 0xFFFFFF
    return mantissa * (1 << (8 * (exponent - 3)))


@dataclass
class Sha256Job:
    job_id: str
    prevhash: str
    coinb1: str
    coinb2: str
    merkle_branch: List[str]
    version: str
    nbits: str
    ntime: str
    clean_jobs: bool
    target: int
    difficulty: float
    height: int = 0

    @property
    def header(self) -> Dict[str, Any]:
        return {"height": self.height, "prevhash": self.prevhash}

    @property
    def share_target(self) -> float:
        return self.difficulty


class Sha256RpcAdapter:
    def __init__(
        self,
        rpc_url: str,
        pool_address: str,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._rpc = JsonRpcClient(rpc_url)
        self._pool_address = pool_address
        self._log = logger or logging.getLogger("animica.stratum_pool.sha256.rpc")

    async def _rpc_call(self, method: str, params: Any) -> Any:
        return await asyncio.to_thread(self._rpc.call, method, params)

    async def get_new_job(self) -> Sha256Job:
        payload = await self._rpc_call(
            "miner.get_sha256_job", [{"address": self._pool_address}]
        )
        return Sha256Job(
            job_id=payload.get("jobId") or uuid.uuid4().hex,
            prevhash=str(payload.get("prevhash") or "0" * 64),
            coinb1=str(payload.get("coinb1") or ""),
            coinb2=str(payload.get("coinb2") or ""),
            merkle_branch=list(payload.get("merkle_branch") or []),
            version=str(payload.get("version") or "20000000"),
            nbits=str(payload.get("nbits") or "1d00ffff"),
            ntime=str(payload.get("ntime") or f"{int(time.time()):08x}"),
            clean_jobs=bool(payload.get("clean_jobs", True)),
            target=(
                int(payload.get("target"), 16)
                if payload.get("target") is not None
                else _bits_to_target("1d00ffff")
            ),
            difficulty=float(payload.get("difficulty") or 1.0),
            height=int(payload.get("height") or 0),
        )

    async def submit_block(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._rpc_call("miner.submit_sha256_block", payload)


class Sha256ShareValidator:
    def __init__(self, extranonce2_size: int) -> None:
        self._extranonce2_size = extranonce2_size

    def _merkle_root(self, job: Sha256Job, coinbase: bytes) -> bytes:
        h = _double_sha(coinbase)
        for branch_hex in job.merkle_branch:
            branch = bytes.fromhex(branch_hex)
            h = _double_sha(h + branch)
        return h

    def _header_hash(
        self, job: Sha256Job, merkle_root: bytes, ntime_hex: str, nonce_hex: str
    ) -> bytes:
        import struct

        version = struct.pack("<I", int(job.version, 16))
        prevhash = bytes.fromhex(job.prevhash)
        merkle_le = merkle_root[::-1]
        ntime = struct.pack("<I", int(ntime_hex, 16))
        nbits = struct.pack("<I", int(job.nbits, 16))
        nonce = struct.pack("<I", int(nonce_hex, 16))
        header = version + prevhash + merkle_le + ntime + nbits + nonce
        return _double_sha(header)

    def _target_for_difficulty(self, difficulty: float) -> int:
        if difficulty <= 0:
            difficulty = 1e-12
        target = int(D1_TARGET / difficulty)
        max_target = 2**256 - 1
        return min(target, max_target)

    def validate(
        self, job: Sha256Job, session: "Sha256Session", submit_params: List[Any]
    ) -> tuple[bool, Optional[str], bool]:
        try:
            worker, job_id, extranonce2, ntime, nonce = submit_params[:5]
        except ValueError:
            return False, "bad submit params", False

        if job_id != job.job_id:
            return False, "stale job", False

        if len(extranonce2) != self._extranonce2_size * 2:
            return False, "bad extranonce2", False

        coinbase_hex = job.coinb1 + session.extranonce1 + extranonce2 + job.coinb2
        coinbase = bytes.fromhex(coinbase_hex)
        merkle_root = self._merkle_root(job, coinbase)
        header_hash = self._header_hash(job, merkle_root, ntime, nonce)
        hash_int = int.from_bytes(header_hash, "big")

        share_target = self._target_for_difficulty(session.difficulty)
        block_target = _bits_to_target(job.nbits)
        is_block = hash_int <= block_target

        if hash_int > share_target:
            return False, "low difficulty share", False

        return True, None, is_block


@dataclass
class Sha256Session:
    writer: asyncio.StreamWriter
    extranonce1: str
    extranonce2_size: int
    difficulty: float
    worker: Optional[str] = None
    authorized: bool = False
    shares_accepted: int = 0
    shares_rejected: int = 0
    last_share_at: Optional[float] = None
    current_job_id: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "session_id": self.extranonce1,
            "worker": self.worker,
            "address": self.worker,
            "authorized": self.authorized,
            "share_target": self.difficulty,
            "theta_micro": 0,
            "last_seen": time.time(),
            "connected_since": 0,
            "last_share_at": self.last_share_at,
            "last_share_status": (
                "accepted"
                if self.shares_accepted
                else "rejected" if self.shares_rejected else None
            ),
            "shares_accepted": self.shares_accepted,
            "shares_rejected": self.shares_rejected,
            "current_difficulty": self.difficulty,
        }


class Sha256StratumServer:
    def __init__(
        self,
        host: str,
        port: int,
        adapter: Sha256RpcAdapter,
        *,
        extranonce2_size: int = 4,
        default_difficulty: float = 1.0,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._host = host
        self._port = port
        self._adapter = adapter
        self._extranonce2_size = extranonce2_size
        self._default_difficulty = default_difficulty
        self._log = logger or logging.getLogger("animica.stratum_pool.sha256")
        self._server: Optional[asyncio.AbstractServer] = None
        self._sessions: Dict[asyncio.StreamWriter, Sha256Session] = {}
        self._jobs: Dict[str, Sha256Job] = {}
        self._current_job: Optional[Sha256Job] = None
        self._validator = Sha256ShareValidator(extranonce2_size)
        self._submit_hook: Optional[
            Callable[
                [
                    Sha256Session,
                    Sha256Job,
                    Dict[str, Any],
                    bool,
                    Optional[str],
                    bool,
                    int,
                ],
                Awaitable[None],
            ]
        ] = None
        self._accepted = 0
        self._rejected = 0

    def set_submit_hook(
        self,
        hook: Optional[
            Callable[
                [
                    Sha256Session,
                    Sha256Job,
                    Dict[str, Any],
                    bool,
                    Optional[str],
                    bool,
                    int,
                ],
                Awaitable[None],
            ]
        ],
    ) -> None:
        self._submit_hook = hook

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )
        sockets = ", ".join(str(s.getsockname()) for s in self._server.sockets or [])
        self._log.info("[ASIC] listening on %s", sockets)

    async def stop(self) -> None:
        for writer in list(self._sessions.keys()):
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        self._sessions.clear()

    async def publish_job(self, job: Sha256Job) -> None:
        self._jobs[job.job_id] = job
        self._current_job = job
        await self._broadcast(
            {
                "id": None,
                "method": "mining.notify",
                "params": [
                    job.job_id,
                    job.prevhash,
                    job.coinb1,
                    job.coinb2,
                    job.merkle_branch,
                    job.version,
                    job.nbits,
                    job.ntime,
                    job.clean_jobs,
                ],
            }
        )
        self._log.info(
            "[ASIC] notify job=%s difficulty=%s height=%s",
            job.job_id,
            job.difficulty,
            job.height,
        )

    async def set_difficulty(self, session: Sha256Session, difficulty: float) -> None:
        difficulty = max(float(difficulty), 1e-12)
        session.difficulty = difficulty
        await self._send(
            session.writer,
            {"id": None, "method": "mining.set_difficulty", "params": [difficulty]},
        )
        self._log.debug(
            "[ASIC] set_difficulty worker=%s session_ex1=%s diff=%s",
            session.worker,
            session.extranonce1,
            difficulty,
        )

    async def _broadcast(self, obj: Dict[str, Any]) -> None:
        for writer in list(self._sessions.keys()):
            await self._send(writer, obj)

    async def _send(self, writer: asyncio.StreamWriter, obj: Dict[str, Any]) -> None:
        payload = json.dumps(obj) + "\n"
        writer.write(payload.encode())
        await writer.drain()

    def _alloc_session(self, writer: asyncio.StreamWriter) -> Sha256Session:
        extranonce1 = secrets.token_hex(4)
        session = Sha256Session(
            writer=writer,
            extranonce1=extranonce1,
            extranonce2_size=self._extranonce2_size,
            difficulty=self._default_difficulty,
        )
        self._sessions[writer] = session
        return session

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        session = self._alloc_session(writer)
        peer = writer.get_extra_info("peername")
        self._log.info(
            "[ASIC] client connected peer=%s ex1=%s", peer, session.extranonce1
        )
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode().strip())
                except json.JSONDecodeError:
                    await self._send(writer, {"id": None, "error": "invalid json"})
                    continue
                try:
                    await self._process_message(session, msg)
                except Exception as exc:  # noqa: BLE001
                    self._log.warning(
                        "[ASIC] error processing message from %s: %s",
                        peer,
                        exc,
                        exc_info=True,
                    )
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            self._sessions.pop(writer, None)
            self._log.info("[ASIC] client disconnected peer=%s", peer)

    async def _process_message(
        self, session: Sha256Session, msg: Dict[str, Any]
    ) -> None:
        method = msg.get("method")
        params = msg.get("params") or []
        msg_id = msg.get("id")

        if method == "mining.subscribe":
            result = [
                [
                    ["mining.set_difficulty", uuid.uuid4().hex],
                    ["mining.notify", uuid.uuid4().hex],
                ],
                session.extranonce1,
                session.extranonce2_size,
            ]
            await self._send(
                session.writer, {"id": msg_id, "result": result, "error": None}
            )
            await self.set_difficulty(session, session.difficulty)
            if self._current_job:
                await self.publish_job(self._current_job)
            self._log.info(
                "[ASIC] subscribe peer=%s ex1=%s diff=%s",
                session.writer.get_extra_info("peername"),
                session.extranonce1,
                session.difficulty,
            )

        elif method == "mining.authorize":
            session.worker = params[0] if params else None
            session.authorized = True
            await self._send(
                session.writer, {"id": msg_id, "result": True, "error": None}
            )
            self._log.info(
                "[ASIC] authorize worker=%s peer=%s",
                session.worker,
                session.writer.get_extra_info("peername"),
            )

        elif method == "mining.submit":
            job_id = params[1] if len(params) > 1 else None
            job = self._jobs.get(job_id or "")
            if not job:
                await self._send(
                    session.writer,
                    {"id": msg_id, "result": None, "error": [21, "stale job", None]},
                )
                self._log.warning(
                    "[ASIC] submit stale job worker=%s job=%s", session.worker, job_id
                )
                return

            accepted, reason, is_block = self._validator.validate(job, session, params)
            if accepted:
                self._accepted += 1
                session.shares_accepted += 1
            else:
                self._rejected += 1
                session.shares_rejected += 1
            session.last_share_at = time.time()

            error = None if accepted else [23, reason or "invalid share", None]
            resp = {"id": msg_id, "result": accepted, "error": error}
            await self._send(session.writer, resp)
            level = logging.INFO if accepted else logging.WARNING
            self._log.log(
                level,
                "[ASIC] submit worker=%s job=%s accepted=%s block=%s reason=%s diff=%s",
                session.worker,
                job_id,
                accepted,
                is_block,
                reason,
                session.difficulty,
            )

            submit_payload = {
                "job_id": job.job_id,
                "params": params,
                "shareTarget": session.difficulty,
                "height": job.height,
            }

            if accepted and is_block:
                await self._adapter.submit_block(submit_payload)
            if self._submit_hook:
                await self._submit_hook(
                    session, job, submit_payload, accepted, reason, is_block, 0
                )

        elif method == "mining.configure":
            await self._send(
                session.writer, {"id": msg_id, "result": {}, "error": None}
            )

        else:
            await self._send(
                session.writer,
                {"id": msg_id, "result": None, "error": "unknown method"},
            )

    def stats(self) -> Dict[str, Any]:
        return {
            "clients": len(self._sessions),
            "accepted": self._accepted,
            "rejected": self._rejected,
            "currentJob": self._current_job.job_id if self._current_job else None,
            "uptime_sec": 0,
        }

    def session_snapshots(self) -> List[Dict[str, Any]]:
        return [s.snapshot() for s in self._sessions.values()]


class Sha256PoolServer:
    def __init__(
        self,
        adapter: Sha256RpcAdapter,
        *,
        host: str,
        port: int,
        extranonce2_size: int,
        default_difficulty: float,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._adapter = adapter
        self._job_manager = None
        self._log = logger or logging.getLogger("animica.stratum_pool.sha256.server")
        self._server = Sha256StratumServer(
            host=host,
            port=port,
            adapter=adapter,
            extranonce2_size=extranonce2_size,
            default_difficulty=default_difficulty,
            logger=logger,
        )
        self._job_manager = _Sha256JobManager(adapter, self._on_new_job)

    @property
    def stratum(self) -> Sha256StratumServer:
        return self._server

    @property
    def job_manager(self) -> "_Sha256JobManager":
        return self._job_manager

    async def start(self) -> None:
        self._job_manager.start()
        await self._server.start()

    async def stop(self) -> None:
        await self._server.stop()
        await self._job_manager.stop()

    async def _on_new_job(self, job: Sha256Job) -> None:
        await self._server.publish_job(job)

    async def wait_closed(self) -> None:
        while True:
            await asyncio.sleep(1)

    def stats(self) -> Dict[str, Any]:
        return self._server.stats()

    def session_snapshots(self) -> List[Dict[str, Any]]:
        return self._server.session_snapshots()


class _Sha256JobManager:
    def __init__(
        self, adapter: Sha256RpcAdapter, on_job: Callable[[Sha256Job], Awaitable[None]]
    ) -> None:
        self._adapter = adapter
        self._on_job = on_job
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None
        self._current: Optional[Sha256Job] = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="sha256-jobs")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task
            self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                job = await self._adapter.get_new_job()
                if self._current is None or job.job_id != self._current.job_id:
                    self._current = job
                    await self._on_job(job)
            except Exception:
                await asyncio.sleep(1.0)
            else:
                await asyncio.sleep(1.0)

    def current_job(self) -> Optional[Sha256Job]:
        return self._current
