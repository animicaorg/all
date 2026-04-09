#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import hashlib
import json
import logging
import math
import secrets
import signal
import socket
import ssl
import struct
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
from pathlib import Path
from typing import Any, Optional

UINT256_MAX = (1 << 256) - 1
MICRO = 1_000_000
PLACEHOLDER_ADDRESS = "YOUR_ANIMICA_ADDRESS"


def micro_threshold_to_target256(t_micro: int) -> int:
    if t_micro <= 0:
        return UINT256_MAX
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.rounding = ROUND_HALF_EVEN
        threshold = Decimal(t_micro) / Decimal(MICRO)
        probability = (-threshold).exp()
        if probability <= 0:
            return 0
        if probability >= 1:
            return UINT256_MAX
        return int(probability * Decimal(UINT256_MAX))


def h_micro_from_digest(digest: bytes) -> int:
    digest_int = int.from_bytes(digest, "big", signed=False)
    unit = (digest_int + 0.5) / float(UINT256_MAX + 1)
    if unit <= 0.0:
        return MICRO * 100
    return int(round(-math.log(unit) * MICRO))


def sanitize_worker_name(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if not raw:
        raw = f"{socket.gethostname().split('.')[0] or 'miner'}-cpu"
    clean = []
    for char in raw:
        if char.isalnum() or char in {".", "_", "-"}:
            clean.append(char)
        else:
            clean.append("-")
    result = "".join(clean).strip("._-")
    return result or "animica-cpu"


@dataclass(frozen=True)
class MinerConfig:
    host: str
    port: int
    scheme: str
    tls: bool
    address: str
    worker: str
    threads: int
    scan_window: int
    log_level: str

    @property
    def stratum_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


@dataclass(frozen=True)
class ShareResult:
    nonce: int
    h_micro: int
    d_ratio: float


def _first_present(mapping: Any, *keys: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _dict_value(mapping: Any, *keys: str) -> dict[str, Any]:
    value = _first_present(mapping, *keys)
    return value if isinstance(value, dict) else {}


def _int_value(value: Any) -> int:
    if value in (None, ""):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _float_value(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _extract_theta_micro(*mappings: Any) -> int:
    for mapping in mappings:
        theta_micro = _int_value(
            _first_present(
                mapping,
                "thetaMicro",
                "thetaTargetMicro",
                "theta_target_micro",
                "theta_micro",
            )
        )
        if theta_micro > 0:
            return theta_micro
    return 0


def _extract_share_target(*mappings: Any, fallback: float = 0.0) -> float:
    for mapping in mappings:
        share_target = _float_value(
            _first_present(
                mapping,
                "shareTarget",
                "share_target",
                "shareRatio",
                "share_ratio",
            )
        )
        if share_target > 0:
            return share_target
    return fallback if fallback > 0 else 0.0


def _normalize_job_payload(
    job: dict[str, Any],
    *,
    default_theta_micro: int,
    default_share_target: float,
) -> tuple[str, dict[str, Any], Optional[str], int, float]:
    header = _dict_value(job, "header", "headerTemplate")
    job_id = str(
        _first_present(job, "jobId", "job_id")
        or _first_present(header, "hash", "headerHash", "header_hash")
        or "unknown"
    )
    sign_hex = _first_present(job, "signBytes", "sign_bytes") or _first_present(
        header, "signBytes", "sign_bytes"
    )
    theta_micro = _extract_theta_micro(job, header) or max(0, int(default_theta_micro))
    share_target = _extract_share_target(job, fallback=default_share_target) or 1.0
    return job_id, header, sign_hex, theta_micro, share_target


def load_json_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_config(args: argparse.Namespace) -> MinerConfig:
    file_data: dict[str, Any] = {}
    if args.config:
        file_data = load_json_config(Path(args.config))

    host = str(args.host or file_data.get("host") or "127.0.0.1")
    port = int(args.port or file_data.get("port") or 3333)
    scheme = str(args.scheme or file_data.get("scheme") or "stratum+tcp")
    tls = bool(args.tls or file_data.get("tls") or scheme in {"stratum+tls", "stratum+ssl"})
    address = str(args.address or file_data.get("address") or "").strip()
    if not address or address == PLACEHOLDER_ADDRESS:
        address = input("Animica payout address: ").strip()
    if not address:
        raise SystemExit("A payout address is required.")
    worker = sanitize_worker_name(args.worker or file_data.get("worker"))
    threads = max(1, int(args.threads or file_data.get("threads") or 4))
    scan_window = max(25_000, int(args.scan_window or file_data.get("scan_window") or 200_000))
    log_level = str(args.log_level or file_data.get("log_level") or "INFO").upper()
    return MinerConfig(
        host=host,
        port=port,
        scheme=scheme,
        tls=tls,
        address=address,
        worker=worker,
        threads=threads,
        scan_window=scan_window,
        log_level=log_level,
    )


class StratumCpuMiner:
    def __init__(self, config: MinerConfig) -> None:
        self.config = config
        self.log = logging.getLogger("animica.cpu_miner")
        self.reader: asyncio.StreamReader
        self.writer: asyncio.StreamWriter
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._next_id = 1
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._mining_task: Optional[asyncio.Task[None]] = None
        self._closed = False
        self._stop = asyncio.Event()
        self._job_token = 0
        self._session_id = ""
        self._share_target = 1.0
        self._theta_micro = 0
        self._scan_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=self.config.threads,
            thread_name_prefix="animica-cpu-scan",
        )

    async def start(self) -> None:
        ssl_ctx = ssl.create_default_context() if self.config.tls else None
        self.reader, self.writer = await asyncio.open_connection(
            self.config.host,
            self.config.port,
            ssl=ssl_ctx,
            server_hostname=self.config.host if self.config.tls else None,
        )
        self._reader_task = asyncio.create_task(self._reader_loop())
        await self._subscribe()
        await self._authorize()
        self.log.info(
            "Connected to %s as worker=%s address=%s threads=%s",
            self.config.stratum_url,
            self.config.worker,
            self.config.address,
            self.config.threads,
        )

    async def wait_forever(self) -> None:
        await self._stop.wait()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._mining_task:
            self._mining_task.cancel()
            await asyncio.gather(self._mining_task, return_exceptions=True)
        if self._reader_task:
            self._reader_task.cancel()
            await asyncio.gather(self._reader_task, return_exceptions=True)
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        self.writer.close()
        await self.writer.wait_closed()
        self._scan_executor.shutdown(wait=False, cancel_futures=True)

    async def _send(self, payload: dict[str, Any]) -> None:
        self.writer.write((json.dumps(payload) + "\n").encode("utf-8"))
        await self.writer.drain()

    async def _call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = self._next_id
        self._next_id += 1
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
        )
        return await future

    async def _subscribe(self) -> None:
        response = await self._call(
            "mining.subscribe",
            {
                "agent": "animica-cpu-miner/0.1",
                "features": {"framing": "lines"},
                "algo": "hashshare",
            },
        )
        result = response.get("result") or {}
        self._session_id = str(result.get("sessionId") or result.get("session_id") or "")
        target_hint = _dict_value(result, "targetHint", "target_hint")
        theta_micro = _extract_theta_micro(result, target_hint)
        if theta_micro > 0:
            self._theta_micro = theta_micro
        share_target = _extract_share_target(result, target_hint, fallback=self._share_target)
        if share_target > 0:
            self._share_target = share_target

    async def _authorize(self) -> None:
        response = await self._call(
            "mining.authorize",
            {"worker": self.config.worker, "address": self.config.address},
        )
        result = response.get("result") or {}
        ok = bool(result.get("ok", result.get("authorized", True)))
        if not ok:
            raise RuntimeError(result.get("reason") or "authorization rejected by pool")

    async def _reader_loop(self) -> None:
        try:
            while not self._closed:
                line = await self.reader.readline()
                if not line:
                    raise ConnectionError("Stratum connection closed")
                payload = json.loads(line.decode("utf-8"))
                if "id" in payload and (
                    payload.get("result") is not None or payload.get("error") is not None
                ):
                    future = self._pending.pop(int(payload["id"]), None)
                    if future and not future.done():
                        future.set_result(payload)
                    continue
                method = payload.get("method")
                params = payload.get("params") or {}
                if not isinstance(params, dict):
                    continue
                if method == "mining.set_difficulty":
                    share_target = _extract_share_target(params, fallback=self._share_target)
                    theta_micro = _extract_theta_micro(params)
                    if share_target > 0:
                        self._share_target = share_target
                    if theta_micro > 0:
                        self._theta_micro = theta_micro
                    self.log.info(
                        "Difficulty update share_target=%.6f theta_micro=%s",
                        self._share_target,
                        self._theta_micro,
                    )
                elif method == "mining.notify":
                    await self._handle_notify(params)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.log.error("Reader loop stopped: %s", exc)
            self._stop.set()

    async def _handle_notify(self, job: dict[str, Any]) -> None:
        self._job_token += 1
        if self._mining_task:
            self._mining_task.cancel()
            await asyncio.gather(self._mining_task, return_exceptions=True)
        token = self._job_token
        self._mining_task = asyncio.create_task(self._mine_job(token, job))

    async def _mine_job(self, token: int, job: dict[str, Any]) -> None:
        job_id, header, sign_hex, theta_micro, share_target = _normalize_job_payload(
            job,
            default_theta_micro=self._theta_micro,
            default_share_target=self._share_target,
        )
        if not isinstance(sign_hex, str) or not sign_hex.startswith("0x"):
            self.log.warning("Job %s missing signBytes; skipping", job_id)
            return

        if theta_micro <= 0:
            self.log.warning("Job %s missing thetaMicro; skipping", job_id)
            return
        target_micro = max(1, int(theta_micro * max(share_target, 1e-9)))
        prefix = bytes.fromhex(sign_hex[2:])
        nonce_start = secrets.randbelow(2**32)
        self.log.info(
            "New job job_id=%s theta_micro=%s share_target=%.6f",
            job_id,
            theta_micro,
            share_target,
        )

        try:
            while token == self._job_token and not self._stop.is_set():
                share = await asyncio.to_thread(
                    self._scan_parallel,
                    prefix,
                    target_micro,
                    theta_micro,
                    nonce_start,
                )
                nonce_start = (nonce_start + self.config.scan_window) & 0xFFFFFFFFFFFFFFFF
                if share is None:
                    continue
                await self._submit_share(job_id, share)
        except asyncio.CancelledError:
            return

    def _scan_parallel(
        self,
        prefix: bytes,
        target_micro: int,
        theta_micro: int,
        nonce_start: int,
    ) -> Optional[ShareResult]:
        per_worker = max(25_000, self.config.scan_window // max(1, self.config.threads))
        target = micro_threshold_to_target256(target_micro)
        futures = []
        for index in range(self.config.threads):
            start = (nonce_start + (index * per_worker)) & 0xFFFFFFFFFFFFFFFF
            futures.append(
                self._scan_executor.submit(
                    self._scan_range,
                    prefix,
                    target,
                    theta_micro,
                    start,
                    per_worker,
                )
            )
        try:
            for future in concurrent.futures.as_completed(futures):
                share = future.result()
                if share is not None:
                    for other in futures:
                        other.cancel()
                    return share
        finally:
            for future in futures:
                future.cancel()
        return None

    @staticmethod
    def _scan_range(
        prefix: bytes,
        target: int,
        theta_micro: int,
        start_nonce: int,
        iterations: int,
    ) -> Optional[ShareResult]:
        base = hashlib.sha3_256()
        base.update(prefix)
        nonce = start_nonce
        for _ in range(iterations):
            hasher = base.copy()
            hasher.update(struct.pack("<Q", nonce))
            digest = hasher.digest()
            if int.from_bytes(digest, "big", signed=False) <= target:
                h_micro = h_micro_from_digest(digest)
                d_ratio = h_micro / float(theta_micro) if theta_micro > 0 else 0.0
                return ShareResult(nonce=nonce, h_micro=h_micro, d_ratio=d_ratio)
            nonce = (nonce + 1) & 0xFFFFFFFFFFFFFFFF
        return None

    async def _submit_share(self, job_id: str, share: ShareResult) -> None:
        response = await self._call(
            "mining.submit",
            {
                "worker": self._session_id or self.config.worker,
                "jobId": job_id,
                "extranonce2": "0x00",
                "hashshare": {
                    "nonce": hex(share.nonce),
                    "body": {
                        "hMicro": share.h_micro,
                        "dRatio": share.d_ratio,
                    },
                },
            },
        )
        if response.get("error"):
            self.log.warning("Share rejected: %s", response["error"])
            return
        result = response.get("result") or {}
        self.log.info(
            "Share submitted accepted=%s is_block=%s reason=%s",
            result.get("accepted"),
            result.get("isBlock"),
            result.get("reason"),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Animica CPU Stratum miner")
    parser.add_argument("--config", help="Path to miner JSON config")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--scheme")
    parser.add_argument("--tls", action="store_true")
    parser.add_argument("--address")
    parser.add_argument("--worker")
    parser.add_argument("--threads", type=int)
    parser.add_argument("--scan-window", type=int)
    parser.add_argument("--log-level")
    return parser


async def async_main(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    logging.basicConfig(
        level=getattr(logging, config.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    miner = StratumCpuMiner(config)
    loop = asyncio.get_running_loop()

    def request_shutdown() -> None:
        loop.create_task(miner.close())

    for signame in ("SIGINT", "SIGTERM"):
        if hasattr(signal, signame):
            try:
                loop.add_signal_handler(getattr(signal, signame), request_shutdown)
            except NotImplementedError:
                pass

    print(f"Animica CPU Miner")
    print(f"Endpoint: {config.stratum_url}")
    print(f"Worker:   {config.worker}")
    print(f"Address:  {config.address}")
    print(f"Threads:  {config.threads}")

    await miner.start()
    try:
        await miner.wait_forever()
    finally:
        await miner.close()
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
