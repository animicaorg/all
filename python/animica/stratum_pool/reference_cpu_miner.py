#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import hashlib
import json
import logging
import math
import multiprocessing as mp
import os
import secrets
import signal
import socket
import ssl
import struct
import threading
import time
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_EVEN, localcontext
from pathlib import Path
from typing import Any, Optional

try:
    from mining.template_block import (
        hash_candidate_header as _hash_candidate_header,
    )
    from mining.template_block import (
        header_from_template_view as _header_from_template_view,
    )
except Exception:
    _hash_candidate_header = None
    _header_from_template_view = None

UINT256_MAX = (1 << 256) - 1
MICRO = 1_000_000
PLACEHOLDER_ADDRESS = "YOUR_ANIMICA_ADDRESS"
DEFAULT_STATS_INTERVAL_SEC = 20.0


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


def _normalize_pool_mode(value: Optional[str]) -> str:
    lowered = str(value or "").strip().lower()
    return lowered if lowered in {"pps", "solo"} else "pps"


def _default_api_base_url(host: str, tls: bool) -> str:
    scheme = "https" if tls else "http"
    return f"{scheme}://{host}:8550"


def _thread_cap() -> int:
    return 61 if os.name == "nt" else 256


def _default_threads() -> int:
    detected = int(os.cpu_count() or 1)
    return max(1, min(detected, _thread_cap()))


def _resolve_threads(value: Any) -> int:
    if value in (None, ""):
        return _default_threads()
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return _default_threads()
    if parsed <= 0:
        return _default_threads()
    return max(1, min(parsed, _thread_cap()))


def _join_url(base: str, path: str) -> str:
    return f"{base.rstrip('/')}/{path.lstrip('/')}"


def _has_canonical_header_hashing() -> bool:
    return _hash_candidate_header is not None and _header_from_template_view is not None


def _format_hashrate(hashrate_hps: float) -> str:
    units = ("H/s", "KH/s", "MH/s", "GH/s", "TH/s")
    value = max(0.0, float(hashrate_hps))
    unit = units[0]
    for candidate in units:
        unit = candidate
        if value < 1000.0 or candidate == units[-1]:
            break
        value /= 1000.0
    return f"{value:.2f} {unit}"


def _format_power(power_w: Optional[float]) -> str:
    if power_w is None or power_w < 0:
        return "n/a"
    return f"{power_w:.1f} W"


def _format_counter(value: Optional[int]) -> str:
    if value is None or value < 0:
        return "n/a"
    return str(int(value))


def _fetch_json(url: str, *, timeout: float = 3.0) -> dict[str, Any]:
    req = urllib_request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "animica-cpu-miner/0.1.1",
        },
    )
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        payload = resp.read().decode("utf-8")
    data = json.loads(payload)
    return data if isinstance(data, dict) else {}


class _RaplPowerSampler:
    def __init__(self) -> None:
        self._channels: list[tuple[Path, int]] = []
        self._last_values: dict[Path, int] = {}
        self._last_ts: Optional[float] = None
        root = Path("/sys/class/powercap")
        if not root.exists():
            return
        for channel_dir in sorted(root.glob("intel-rapl:*")):
            if channel_dir.name.count(":") != 1:
                continue
            energy_path = channel_dir / "energy_uj"
            max_path = channel_dir / "max_energy_range_uj"
            if not energy_path.exists():
                continue
            max_uj = 0
            try:
                if max_path.exists():
                    max_uj = int(max_path.read_text(encoding="utf-8").strip())
            except Exception:
                max_uj = 0
            self._channels.append((energy_path, max_uj))

    def sample(self) -> Optional[float]:
        if not self._channels:
            return None
        now = time.monotonic()
        total_delta_uj = 0
        initialized = False
        for energy_path, max_uj in self._channels:
            try:
                current = int(energy_path.read_text(encoding="utf-8").strip())
            except Exception:
                return None
            previous = self._last_values.get(energy_path)
            self._last_values[energy_path] = current
            if previous is None:
                initialized = True
                continue
            delta = current - previous
            if delta < 0 and max_uj > 0:
                delta += max_uj
            total_delta_uj += max(0, delta)
        if self._last_ts is None or initialized:
            self._last_ts = now
            return None
        elapsed = now - self._last_ts
        self._last_ts = now
        if elapsed <= 0:
            return None
        return (total_delta_uj / 1_000_000.0) / elapsed


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
    api_base_url: str = ""
    pool_mode: str = "pps"
    stats_interval_sec: float = DEFAULT_STATS_INTERVAL_SEC
    log_level: str = "INFO"

    @property
    def stratum_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


@dataclass(frozen=True)
class ShareResult:
    nonce: int
    h_micro: int
    d_ratio: float
    hashes_tried: int = 0


@dataclass(frozen=True)
class SubmitOutcome:
    accepted: bool
    is_block: bool
    reason: Optional[str]
    stale_job: bool


@dataclass(frozen=True)
class PoolStatusSnapshot:
    global_height: int = 0
    pool_blocks_found: int = 0
    worker_blocks_found: int = 0


def normalize_share_ratio(value: Any, *, default: float = 1.0) -> float:
    try:
        ratio = float(value)
    except (TypeError, ValueError):
        ratio = float(default)
    if ratio <= 0:
        ratio = float(default)
    if ratio <= 0:
        ratio = 1.0
    if ratio > 1.0:
        ratio = 1.0
    return ratio


def derive_share_threshold_micro(theta_micro: int, share_ratio: Any) -> int:
    theta = max(int(theta_micro), 0)
    if theta <= 0:
        return 0
    ratio = Decimal(str(normalize_share_ratio(share_ratio)))
    with localcontext() as ctx:
        ctx.prec = 50
        threshold = (Decimal(theta) * ratio).to_integral_value(rounding=ROUND_FLOOR)
    return max(1, int(threshold))


def parse_hex_bytes(value: Any, *, default: bytes = b"") -> bytes:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("0x"):
            text = text[2:]
        if not text:
            return default
        if len(text) % 2:
            text = "0" + text
        try:
            return bytes.fromhex(text)
        except Exception:
            return default
    return default


def digest_from_sign_bytes(
    sign_bytes: bytes,
    *,
    mix_seed: bytes = b"",
    nonce_int: int,
    nonce_byteorder: str = "little",
) -> bytes:
    nonce_bytes = int(nonce_int).to_bytes(8, nonce_byteorder, signed=False)
    hasher = hashlib.sha3_256()
    hasher.update(sign_bytes)
    hasher.update(mix_seed)
    hasher.update(nonce_bytes)
    return hasher.digest()


def _extract_mix_seed_bytes(job: dict[str, Any], header: dict[str, Any]) -> bytes:
    candidates = []
    for mapping in (
        job,
        _dict_value(job, "hints", "targetHint", "target_hint"),
        header,
        _dict_value(header, "hints", "targetHint", "target_hint"),
    ):
        if isinstance(mapping, dict):
            candidates.append(mapping.get("mixSeed"))
            candidates.append(mapping.get("mix_seed"))
    for value in candidates:
        parsed = parse_hex_bytes(value, default=b"")
        if parsed:
            return parsed
    return b""


def _scan_range_worker(
    prefix: bytes,
    mix_seed: bytes,
    target: int,
    theta_micro: int,
    start_nonce: int,
    iterations: int,
) -> Optional[ShareResult]:
    nonce = start_nonce
    best_share: Optional[ShareResult] = None
    for attempt in range(1, iterations + 1):
        digest = digest_from_sign_bytes(
            prefix,
            mix_seed=mix_seed,
            nonce_int=nonce,
            nonce_byteorder="little",
        )
        digest_int = int.from_bytes(digest, "big", signed=False)
        if digest_int <= target:
            h_micro = h_micro_from_digest(digest)
            d_ratio = h_micro / float(theta_micro) if theta_micro > 0 else 0.0
            candidate = ShareResult(
                nonce=nonce,
                h_micro=h_micro,
                d_ratio=d_ratio,
                hashes_tried=attempt,
            )
            if best_share is None or candidate.h_micro > best_share.h_micro:
                best_share = candidate
        nonce = (nonce + 1) & 0xFFFFFFFFFFFFFFFF
    return best_share


def _scan_header_range_worker(
    header_template: Any,
    target: int,
    theta_micro: int,
    start_nonce: int,
    iterations: int,
) -> Optional[ShareResult]:
    if _hash_candidate_header is None:
        return None
    nonce = start_nonce
    best_share: Optional[ShareResult] = None
    for attempt in range(1, iterations + 1):
        candidate_hash = _hash_candidate_header(header_template, nonce=nonce)
        if candidate_hash.digest_int <= target:
            h_micro = h_micro_from_digest(candidate_hash.digest)
            d_ratio = h_micro / float(theta_micro) if theta_micro > 0 else 0.0
            candidate = ShareResult(
                nonce=nonce,
                h_micro=h_micro,
                d_ratio=d_ratio,
                hashes_tried=attempt,
            )
            if best_share is None or candidate.h_micro > best_share.h_micro:
                best_share = candidate
        nonce = (nonce + 1) & 0xFFFFFFFFFFFFFFFF
    return best_share


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


def _apply_runtime_difficulty_to_job(
    job: dict[str, Any],
    *,
    theta_micro: int,
    share_target: float,
) -> dict[str, Any]:
    updated = dict(job) if isinstance(job, dict) else {}
    if share_target > 0:
        normalized_share_target = normalize_share_ratio(share_target, default=share_target)
        updated["shareTarget"] = normalized_share_target
        if "share_target" in updated:
            updated["share_target"] = normalized_share_target
    if theta_micro <= 0:
        return updated

    updated["thetaMicro"] = int(theta_micro)
    updated["thetaTargetMicro"] = int(theta_micro)
    updated["theta_target_micro"] = int(theta_micro)

    for header_key in ("header", "headerTemplate"):
        header = updated.get(header_key)
        if not isinstance(header, dict):
            continue
        next_header = dict(header)
        next_header["thetaMicro"] = int(theta_micro)
        next_header["thetaTargetMicro"] = int(theta_micro)
        next_header["theta_target_micro"] = int(theta_micro)
        updated[header_key] = next_header
    return updated


def _header_template_from_job(header: dict[str, Any]) -> Optional[Any]:
    if not _has_canonical_header_hashing():
        return None
    if not isinstance(header, dict):
        return None
    required = (
        "parentHash",
        "stateRoot",
        "txsRoot",
        "receiptsRoot",
        "proofsRoot",
        "daRoot",
        "mixSeed",
        "poiesPolicyRoot",
        "pqAlgPolicyRoot",
        "timestamp",
    )
    if not all(key in header for key in required):
        return None
    try:
        return _header_from_template_view(header, nonce=0)
    except Exception:
        return None


def _response_reason(response: dict[str, Any]) -> Optional[str]:
    error = response.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message") or error.get("data")
        if code is not None and message:
            return f"rpc:{code}:{message}"
        if code is not None:
            return f"rpc:{code}"
        if message:
            return str(message)
    result = response.get("result")
    if isinstance(result, dict):
        reason = result.get("reason")
        if reason is not None:
            return str(reason)
    return None


def _is_stale_job_reason(reason: Optional[str]) -> bool:
    if not reason:
        return False
    lowered = str(reason).lower()
    return (
        "stale job" in lowered
        or "stale jobid" in lowered
        or "stale template" in lowered
        or "stale_template" in lowered
    )


def _should_stop_job(outcome: SubmitOutcome) -> bool:
    return (outcome.accepted and outcome.is_block) or outcome.stale_job


def load_json_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_animica_address(address: str) -> bool:
    candidate = str(address or "").strip()
    if not candidate.startswith("anim1"):
        return False
    try:
        from pq.py.address import validate_address

        validate_address(candidate, expect_hrp="anim")
        return True
    except Exception:
        # Keep a light fallback for bundled executable environments that may
        # not carry full wallet helper dependencies.
        return len(candidate) >= 20


def resolve_config(args: argparse.Namespace) -> MinerConfig:
    file_data: dict[str, Any] = {}
    if args.config:
        file_data = load_json_config(Path(args.config))

    pool_url = str(
        getattr(args, "pool_url", None) or file_data.get("pool_url") or ""
    ).strip()
    parsed_host: Optional[str] = None
    parsed_port: Optional[int] = None
    parsed_scheme: Optional[str] = None
    if pool_url:
        parsed = urllib_parse.urlsplit(
            pool_url if "://" in pool_url else f"stratum+tcp://{pool_url}"
        )
        parsed_host = parsed.hostname
        parsed_scheme = parsed.scheme or None
        try:
            parsed_port = parsed.port
        except ValueError:
            parsed_port = None

    host = str(args.host or parsed_host or file_data.get("host") or "127.0.0.1")
    port = int(args.port or parsed_port or file_data.get("port") or 3333)
    scheme = str(args.scheme or parsed_scheme or file_data.get("scheme") or "stratum+tcp")
    tls = bool(args.tls or file_data.get("tls") or scheme in {"stratum+tls", "stratum+ssl"})
    address = str(args.address or file_data.get("address") or "").strip()
    if not address or address == PLACEHOLDER_ADDRESS:
        address = input("Animica payout address: ").strip()
    if not address:
        raise SystemExit("A payout address is required.")
    if not _validate_animica_address(address):
        raise SystemExit(
            "Invalid Animica payout address. Expected a Bech32 address beginning with 'anim1'."
        )
    if not host:
        raise SystemExit("Pool host is required.")
    if port <= 0 or port > 65535:
        raise SystemExit("Pool port must be between 1 and 65535.")
    worker = sanitize_worker_name(args.worker or file_data.get("worker"))
    threads = _resolve_threads(args.threads if args.threads is not None else file_data.get("threads"))
    scan_window = max(25_000, int(args.scan_window or file_data.get("scan_window") or 200_000))
    api_base_url = str(
        args.api_base_url
        or file_data.get("api_base_url")
        or _default_api_base_url(host, tls)
    ).rstrip("/")
    pool_mode = _normalize_pool_mode(args.pool_mode or file_data.get("pool_mode"))
    stats_interval_raw = (
        args.stats_interval
        if getattr(args, "stats_interval", None) is not None
        else file_data.get("stats_interval_sec")
    )
    stats_interval_sec = max(
        5.0, _float_value(stats_interval_raw) or DEFAULT_STATS_INTERVAL_SEC
    )
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
        api_base_url=api_base_url,
        pool_mode=pool_mode,
        stats_interval_sec=stats_interval_sec,
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
        self._status_task: Optional[asyncio.Task[None]] = None
        self._closed = False
        self._stop = asyncio.Event()
        self._job_token = 0
        self._session_id = ""
        self._share_target = 1.0
        self._theta_micro = 0
        self._local_blocks_found = 0
        self._last_pool_snapshot = PoolStatusSnapshot()
        self._stats_lock = threading.Lock()
        self._total_hashes = 0
        self._last_total_hashes = 0
        self._last_report_ts = time.monotonic()
        self._last_hashrate_hps = 0.0
        self._accepted_shares = 0
        self._rejected_shares = 0
        self._last_power_w: Optional[float] = None
        self._power_sampler = _RaplPowerSampler()
        self._current_job: Optional[dict[str, Any]] = None
        self._last_reported_accepted = 0
        self._last_reported_rejected = 0
        self._rejection_reason_counts_since_report: dict[str, int] = defaultdict(int)
        self._scan_executor = concurrent.futures.ProcessPoolExecutor(
            max_workers=self.config.threads,
            mp_context=mp.get_context("spawn"),
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
        self._status_task = asyncio.create_task(self._status_loop())

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
        if self._status_task:
            self._status_task.cancel()
            await asyncio.gather(self._status_task, return_exceptions=True)
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        self.writer.close()
        await self.writer.wait_closed()
        self._scan_executor.shutdown(wait=False, cancel_futures=True)

    def _record_hashes(self, hashes_tried: int) -> None:
        if hashes_tried <= 0:
            return
        with self._stats_lock:
            self._total_hashes += int(hashes_tried)

    def _drain_hashrate(self) -> tuple[float, int]:
        now = time.monotonic()
        with self._stats_lock:
            elapsed = max(now - self._last_report_ts, 1e-6)
            total = self._total_hashes
            hashes = max(0, total - self._last_total_hashes)
            self._last_total_hashes = total
            self._last_report_ts = now
        if hashes > 0:
            self._last_hashrate_hps = float(hashes) / elapsed
        return self._last_hashrate_hps, total

    def _emit_status_line(
        self,
        *,
        hashrate_hps: float,
        total_hashes: int,
        power_w: Optional[float],
        snapshot: PoolStatusSnapshot,
    ) -> None:
        ts = time.strftime("%H:%M:%S")
        print(
            f"[{ts}] mode={self.config.pool_mode} | "
            f"backend=pool-cpu-multiprocess | "
            f"hashrate={_format_hashrate(hashrate_hps)} | "
            f"total_hashes={total_hashes} | "
            f"accepted={self._accepted_shares} | "
            f"rejected={self._rejected_shares} | "
            f"power={_format_power(power_w)} | "
            f"blocks global={_format_counter(snapshot.global_height)} "
            f"pool={_format_counter(snapshot.pool_blocks_found)} "
            f"you={_format_counter(snapshot.worker_blocks_found)}"
        )

    def _emit_block_update(self, label: str, snapshot: PoolStatusSnapshot) -> None:
        ts = time.strftime("%H:%M:%S")
        print(
            f"[{ts}] {label} | "
            f"global={_format_counter(snapshot.global_height)} "
            f"pool={_format_counter(snapshot.pool_blocks_found)} "
            f"you={_format_counter(snapshot.worker_blocks_found)}"
        )

    def _emit_share_batch_update(self) -> None:
        accepted_delta = self._accepted_shares - self._last_reported_accepted
        rejected_delta = self._rejected_shares - self._last_reported_rejected
        if accepted_delta <= 0 and rejected_delta <= 0:
            return
        ts = time.strftime("%H:%M:%S")
        breakdown = ""
        if self._rejection_reason_counts_since_report:
            top_reasons = sorted(
                self._rejection_reason_counts_since_report.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:3]
            reason_summary = ", ".join(f"{reason} x{count}" for reason, count in top_reasons)
            breakdown = f" | reject_reasons={reason_summary}"
        print(
            f"[{ts}] shares/{int(self.config.stats_interval_sec)}s "
            f"accepted=+{accepted_delta} rejected=+{rejected_delta}{breakdown}"
        )
        self._last_reported_accepted = self._accepted_shares
        self._last_reported_rejected = self._rejected_shares
        self._rejection_reason_counts_since_report.clear()

    async def _fetch_pool_snapshot(self) -> PoolStatusSnapshot:
        status_url = _join_url(self.config.api_base_url, "/api/mining/status")
        worker_url = _join_url(
            self.config.api_base_url,
            f"/api/miners/{urllib_parse.quote(self.config.worker, safe='')}",
        )
        try:
            status = await asyncio.to_thread(_fetch_json, status_url)
        except Exception:
            return PoolStatusSnapshot(
                global_height=-1,
                pool_blocks_found=-1,
                worker_blocks_found=self._local_blocks_found,
            )
        try:
            worker_detail = await asyncio.to_thread(_fetch_json, worker_url)
        except urllib_error.HTTPError as exc:
            if exc.code == 404:
                worker_detail = {}
            else:
                worker_detail = {}
        except Exception:
            worker_detail = {}
        return PoolStatusSnapshot(
            global_height=_int_value(status.get("height")),
            pool_blocks_found=_int_value(status.get("blocks_found_total")),
            worker_blocks_found=max(
                self._local_blocks_found,
                _int_value(worker_detail.get("blocks_found")),
            ),
        )

    async def _status_loop(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self.config.stats_interval_sec
                )
                return
            except asyncio.TimeoutError:
                pass

            snapshot = await self._fetch_pool_snapshot()
            if snapshot.worker_blocks_found > self._last_pool_snapshot.worker_blocks_found:
                self._emit_block_update("Block found by this miner", snapshot)
            elif snapshot.pool_blocks_found > self._last_pool_snapshot.pool_blocks_found:
                self._emit_block_update("Pool found a block", snapshot)
            elif snapshot.global_height > self._last_pool_snapshot.global_height:
                self._emit_block_update("Network found a block", snapshot)
            self._last_pool_snapshot = snapshot

            hashrate_hps, total_hashes = self._drain_hashrate()
            power_w = self._power_sampler.sample()
            if power_w is not None:
                self._last_power_w = power_w
            self._emit_status_line(
                hashrate_hps=hashrate_hps,
                total_hashes=total_hashes,
                power_w=self._last_power_w,
                snapshot=snapshot,
            )
            self._emit_share_batch_update()

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
        # AICF model-serving is ON by default. The miner advertises the
        # "standard" tier so the pool can route inference jobs here out
        # of the box; on miners without transformers+torch installed the
        # inference engine falls back to a labeled stub so the protocol
        # still round-trips. Opt out with ANIMICA_AICF_DISABLE=1.
        import os as _os
        aicf_features: dict = {}
        if not _os.environ.get("ANIMICA_AICF_DISABLE", "").strip():
            aicf_tiers_env = _os.environ.get("ANIMICA_AICF_TIERS", "").strip()
            if aicf_tiers_env:
                tiers = [t.strip() for t in aicf_tiers_env.split(",") if t.strip()]
            else:
                tiers = ["standard"]
            if tiers:
                aicf_features["tiers"] = tiers
                # Pre-download the models for the advertised tiers so the first
                # inference job serves immediately instead of stalling minutes on
                # a multi-GB fetch. Background + guarded so it runs once and never
                # blocks the PoW loop; disable with ANIMICA_AICF_PREFETCH=0.
                if not getattr(self, "_aicf_prefetched", False):
                    self._aicf_prefetched = True
                    try:
                        import threading as _threading
                        from animica.stratum_pool.aicf_inference import prefetch_tier_models as _prefetch
                        _threading.Thread(
                            target=_prefetch, args=(list(tiers),),
                            kwargs={"log": self.log.info}, daemon=True,
                            name="aicf-prefetch",
                        ).start()
                        self.log.info("aicf-prefetch: ensuring models for tiers %s are downloaded", ",".join(tiers))
                    except Exception as _pf_exc:   # noqa: BLE001
                        self.log.debug("aicf prefetch not started: %s", _pf_exc)
                hardware: dict = {}
                for key, env in (
                    ("gpu", "ANIMICA_AICF_GPU"),
                    ("vram_gb", "ANIMICA_AICF_VRAM_GB"),
                    ("model", "ANIMICA_AICF_MODEL"),
                ):
                    val = _os.environ.get(env, "").strip()
                    if val:
                        hardware[key] = val
                if "backend" not in hardware:
                    try:
                        import importlib as _il
                        _il.import_module("transformers")
                        _il.import_module("torch")
                        hardware["backend"] = "transformers"
                    except Exception:
                        hardware["backend"] = "stub"
                aicf_features["hardware"] = hardware
        features: dict = {"framing": "lines"}
        if aicf_features:
            features["aicf"] = aicf_features
        response = await self._call(
            "mining.subscribe",
            {
                "agent": "animica-cpu-miner/0.1.1",
                "features": features,
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
            {
                "worker": self.config.worker,
                "address": self.config.address,
                "pool_mode": self.config.pool_mode,
            },
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
                    changed = False
                    if share_target > 0:
                        normalized_share_target = normalize_share_ratio(
                            share_target, default=self._share_target
                        )
                        if abs(normalized_share_target - self._share_target) > 1e-12:
                            self._share_target = normalized_share_target
                            changed = True
                    if theta_micro > 0:
                        if theta_micro != self._theta_micro:
                            self._theta_micro = theta_micro
                            changed = True
                    if changed and self._current_job is not None:
                        self._current_job = _apply_runtime_difficulty_to_job(
                            self._current_job,
                            theta_micro=self._theta_micro,
                            share_target=self._share_target,
                        )
                        self.log.info(
                            "Difficulty update theta=%s shareTarget=%.6f; restarting current job",
                            self._theta_micro,
                            self._share_target,
                        )
                        await self._restart_current_job()
                elif method == "mining.notify":
                    await self._handle_notify(params)
                elif method == "mining.aicf.notify":
                    asyncio.create_task(self._handle_aicf_notify(params))
        except asyncio.CancelledError:
            return
        except Exception as exc:
            self.log.error("Reader loop stopped: %s", exc)
            self._stop.set()

    async def _handle_notify(self, job: dict[str, Any]) -> None:
        self._current_job = dict(job)
        self._job_token += 1
        if self._mining_task:
            self._mining_task.cancel()
            await asyncio.gather(self._mining_task, return_exceptions=True)
        token = self._job_token
        self._mining_task = asyncio.create_task(
            self._mine_job(token, dict(self._current_job))
        )

    async def _handle_aicf_notify(self, params: dict[str, Any]) -> None:
        """Run inference on the JobSpec the pool pushed and submit
        the result back via mining.aicf.submit. Runs in a separate
        task so PoW mining isn't blocked. Inference itself runs on
        a thread because transformers.generate() is synchronous."""
        job_id = str(params.get("jobId") or "")
        spec = params.get("spec") or {}
        tier = str(params.get("tier") or "standard")
        if not job_id or not isinstance(spec, dict):
            self.log.warning("Bad mining.aicf.notify payload: %r", params)
            return
        try:
            from animica.stratum_pool.aicf_inference import get_engine
        except Exception as exc:
            self.log.warning("aicf inference engine not importable: %s", exc)
            return
        engine = get_engine()
        try:
            result = await asyncio.to_thread(engine.generate, spec, tier=tier)
        except Exception as exc:
            self.log.warning("aicf inference failed job=%s: %s", job_id, exc)
            return
        self.log.info(
            "aicf job done id=%s tier=%s backend=%s latency=%dms model=%s",
            job_id,
            tier,
            result.used_backend,
            result.latency_ms,
            result.used_model,
        )
        try:
            await self._send({
                "jsonrpc": "2.0",
                "id": None,
                "method": "mining.aicf.submit",
                "params": {
                    "worker": self.config.worker,
                    "jobId": job_id,
                    "text": result.text,
                    "latencyMs": int(result.latency_ms),
                    "attestation": {
                        "backend": result.used_backend,
                        "model": result.used_model,
                    },
                },
            })
        except Exception as exc:
            self.log.warning("Failed to send mining.aicf.submit for %s: %s", job_id, exc)

    async def _restart_current_job(self) -> None:
        if self._current_job is None:
            return
        self._job_token += 1
        if self._mining_task:
            self._mining_task.cancel()
            await asyncio.gather(self._mining_task, return_exceptions=True)
        token = self._job_token
        self._mining_task = asyncio.create_task(
            self._mine_job(token, dict(self._current_job))
        )

    def _apply_submit_outcome(self, outcome: SubmitOutcome) -> None:
        if outcome.accepted:
            self._accepted_shares += 1
        else:
            self._rejected_shares += 1
            reason = (
                str(outcome.reason or "unknown_reject")
                if not outcome.stale_job
                else "stale_job"
            )
            self._rejection_reason_counts_since_report[reason] += 1
        if outcome.accepted and outcome.is_block:
            self._local_blocks_found += 1
            snapshot = PoolStatusSnapshot(
                global_height=self._last_pool_snapshot.global_height,
                pool_blocks_found=max(
                    self._last_pool_snapshot.pool_blocks_found,
                    self._local_blocks_found,
                ),
                worker_blocks_found=self._local_blocks_found,
            )
            self._emit_block_update("Block found by this miner", snapshot)
            self._last_pool_snapshot = snapshot

    async def _mine_job(self, token: int, job: dict[str, Any]) -> None:
        job_id, header, sign_hex, theta_micro, share_target = _normalize_job_payload(
            job,
            default_theta_micro=self._theta_micro,
            default_share_target=self._share_target,
        )
        if theta_micro <= 0:
            self.log.warning("Job %s missing thetaMicro; skipping", job_id)
            return

        header_template = _header_template_from_job(header)
        prefix = None
        if header_template is None:
            if not isinstance(sign_hex, str) or not sign_hex.startswith("0x"):
                if _has_canonical_header_hashing():
                    self.log.warning("Job %s missing usable header template; skipping", job_id)
                else:
                    self.log.warning(
                        "Job %s missing signBytes and standalone canonical-header helpers are unavailable; skipping",
                        job_id,
                    )
                return
            prefix = bytes.fromhex(sign_hex[2:])
            mix_seed = _extract_mix_seed_bytes(job, header)
        else:
            mix_seed = b""

        share_ratio = normalize_share_ratio(share_target, default=1.0)
        target_micro = derive_share_threshold_micro(theta_micro, share_ratio)
        nonce_start = secrets.randbelow(2**32)
        submit_tasks: list[asyncio.Task[SubmitOutcome]] = []
        best_pending_share: Optional[ShareResult] = None
        max_submit_tasks = 4

        try:
            while token == self._job_token and not self._stop.is_set():
                if submit_tasks:
                    done = [task for task in submit_tasks if task.done()]
                    if not done:
                        await asyncio.sleep(0)
                        done = [task for task in submit_tasks if task.done()]
                    for task in done:
                        submit_tasks.remove(task)
                        outcome = await task
                        self._apply_submit_outcome(outcome)
                        if _should_stop_job(outcome):
                            return

                if header_template is not None:
                    share = await asyncio.to_thread(
                        self._scan_header_parallel,
                        header_template,
                        target_micro,
                        theta_micro,
                        nonce_start,
                    )
                else:
                    share = await asyncio.to_thread(
                        self._scan_parallel,
                        prefix,
                        mix_seed,
                        target_micro,
                        theta_micro,
                        nonce_start,
                    )
                nonce_start = (nonce_start + self.config.scan_window) & 0xFFFFFFFFFFFFFFFF
                if share is None:
                    continue

                if best_pending_share is None or share.h_micro > best_pending_share.h_micro:
                    best_pending_share = share

                while len(submit_tasks) < max_submit_tasks and best_pending_share is not None:
                    submit_tasks.append(
                        asyncio.create_task(self._submit_share(job_id, best_pending_share))
                    )
                    best_pending_share = None

            for task in submit_tasks:
                outcome = await task
                self._apply_submit_outcome(outcome)
        except asyncio.CancelledError:
            for task in submit_tasks:
                task.cancel()
            await asyncio.gather(*submit_tasks, return_exceptions=True)
            return

    def _scan_parallel(
        self,
        prefix: bytes,
        mix_seed: bytes,
        target_micro: int,
        theta_micro: int,
        nonce_start: int,
    ) -> Optional[ShareResult]:
        per_worker = max(25_000, self.config.scan_window // max(1, self.config.threads))
        target = micro_threshold_to_target256(target_micro)
        estimated_hashes = per_worker * self.config.threads
        futures = []
        best_share: Optional[ShareResult] = None
        for index in range(self.config.threads):
            start = (nonce_start + (index * per_worker)) & 0xFFFFFFFFFFFFFFFF
            futures.append(
                self._scan_executor.submit(
                    _scan_range_worker,
                    prefix,
                    mix_seed,
                    target,
                    theta_micro,
                    start,
                    per_worker,
                )
            )
        for future in concurrent.futures.as_completed(futures):
            share = future.result()
            if share is not None and (
                best_share is None or share.h_micro > best_share.h_micro
            ):
                best_share = share
        self._record_hashes(estimated_hashes)
        if best_share is None:
            return None
        return replace(best_share, hashes_tried=estimated_hashes)

    def _scan_header_parallel(
        self,
        header_template: Any,
        target_micro: int,
        theta_micro: int,
        nonce_start: int,
    ) -> Optional[ShareResult]:
        per_worker = max(25_000, self.config.scan_window // max(1, self.config.threads))
        target = micro_threshold_to_target256(target_micro)
        estimated_hashes = per_worker * self.config.threads
        futures = []
        best_share: Optional[ShareResult] = None
        for index in range(self.config.threads):
            start = (nonce_start + (index * per_worker)) & 0xFFFFFFFFFFFFFFFF
            futures.append(
                self._scan_executor.submit(
                    _scan_header_range_worker,
                    header_template,
                    target,
                    theta_micro,
                    start,
                    per_worker,
                )
            )
        for future in concurrent.futures.as_completed(futures):
            share = future.result()
            if share is not None and (
                best_share is None or share.h_micro > best_share.h_micro
            ):
                best_share = share
        self._record_hashes(estimated_hashes)
        if best_share is None:
            return None
        return replace(best_share, hashes_tried=estimated_hashes)

    async def _submit_share(self, job_id: str, share: ShareResult) -> SubmitOutcome:
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
        reason = _response_reason(response)
        stale_job = _is_stale_job_reason(reason)
        if response.get("error"):
            if not stale_job:
                # Share rejects can be frequent on dynamic jobs; keep per-reject
                # detail at debug level and use batched status updates for UX.
                self.log.debug("Share rejected: %s", reason or response["error"])
            return SubmitOutcome(
                accepted=False,
                is_block=False,
                reason=reason,
                stale_job=stale_job,
            )
        result = response.get("result") or {}
        accepted = bool(result.get("accepted"))
        is_block = bool(result.get("isBlock"))
        return SubmitOutcome(
            accepted=accepted,
            is_block=is_block,
            reason=reason,
            stale_job=stale_job,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Animica CPU Stratum miner")
    parser.add_argument("--config", help="Path to miner JSON config")
    parser.add_argument(
        "--pool-url",
        help="Pool URL (for example stratum+tcp://pool.animica.org:3333)",
    )
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--scheme")
    parser.add_argument("--tls", action="store_true")
    parser.add_argument("--api-base-url")
    parser.add_argument("--address")
    parser.add_argument("--worker")
    parser.add_argument("--pool-mode")
    parser.add_argument("--threads", type=int)
    parser.add_argument("--scan-window", type=int)
    parser.add_argument("--stats-interval", type=float)
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

    print(
        "Animica CPU Miner | "
        f"endpoint={config.stratum_url} | "
        f"mode={config.pool_mode} | "
        f"worker={config.worker} | "
        f"threads={config.threads} | "
        "backend=pool-cpu-multiprocess"
    )

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
    mp.freeze_support()
    raise SystemExit(main())
