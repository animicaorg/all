from __future__ import annotations

"""
Reference Stratum miner used for tests, devnets, and the
`animica miner mine-blocks --pool-stratum` CLI path.

This helper wires ``StratumClient`` to a device-aware scanner (CUDA/ROCm/
OpenCL/Metal when available, CPU fallback) so the same code path drives
both GPU-equipped rigs and the pure-Python test environment. The class is
intentionally simple so tests can assert on specific nonces.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from .hash_search import HashScanner, h_micro_from_digest, micro_threshold_to_target256
from .stratum_client import StratumClient
from .template_block import hash_candidate_header, header_from_template_view

log = logging.getLogger("mining.stratum_miner")


@dataclass
class CpuMinerResult:
    job_id: str
    nonce: int
    h_micro: int
    accepted: bool
    is_block: bool
    reason: Optional[str]


def _select_device_backend(
    device: str = "auto", *, threads: int = 0, batch_size: int = 0
) -> tuple[Optional[Any], str]:
    """
    Resolve a `MiningDevice` instance from `mining.device` for the
    requested backend. Falls back through preference order on failure.

    Returns (device_backend_or_None, effective_device_name). If the
    returned backend is None the caller should use the pure-Python
    HashScanner path.
    """
    try:
        from . import device as _device_mod
    except Exception as exc:  # pragma: no cover - mining package always present
        log.warning("device module unavailable: %s; falling back to CPU scanner", exc)
        return None, "cpu"

    requested = (device or "auto").strip().lower()
    if requested == "auto":
        try:
            requested = _device_mod.auto_detect_device()
        except Exception as exc:
            log.warning("auto-detect failed: %s; falling back to CPU", exc)
            requested = "cpu"

    # Build a preference chain so we never silently fail to start mining.
    if requested == "cpu":
        order = ["cpu"]
    else:
        order = [requested, "cpu"]

    last_err: Optional[BaseException] = None
    for backend in order:
        try:
            dev = _device_mod.create(backend, threads=threads, batch_size=batch_size)
            return dev, backend
        except Exception as exc:
            last_err = exc
            log.info("device backend %s unavailable: %s", backend, exc)
            continue

    if last_err is not None:
        log.warning(
            "no device backend could be instantiated (last error: %s); "
            "falling back to pure-Python HashScanner",
            last_err,
        )
    return None, "cpu"


class CpuStratumMiner:
    """
    Stratum miner that scans for one share per notify and submits it.

    Despite the class name (kept for backwards compatibility with tests),
    this miner selects the best available hardware backend at construction
    time via `mining.device.auto_detect_device` and uses its `scan()`
    method. When no GPU backend is available, or `device='cpu'` is
    requested explicitly, the pure-Python `HashScanner` is used so the
    miner still works in environments without GPU drivers.
    """

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 23454,
        agent: str = "animica-cpu-miner/0.1",
        worker: str = "cpu.worker",
        address: str = "anim1qqq",
        scan_window: int = 10_000,
        device: str = "auto",
        threads: int = 0,
        batch_size: int = 0,
        tls: bool = False,
        tls_verify: bool = True,
    ) -> None:
        self._client = StratumClient(
            host, port, agent=agent, tls=tls, tls_verify=tls_verify
        )
        self._worker = worker
        self._address = address
        self._scanner = HashScanner()
        self._scan_window = scan_window

        # Device backend (GPU-aware). May be None when no hardware
        # backend is usable — in that case the legacy pure-Python
        # HashScanner path runs.
        self._device, self._device_name = _select_device_backend(
            device, threads=threads, batch_size=batch_size
        )
        if self._device is not None:
            try:
                info = self._device.info()
                log.info(
                    "stratum miner: device=%s name=%s vendor=%s",
                    self._device_name,
                    getattr(info, "name", "?"),
                    getattr(info, "vendor", "?"),
                )
            except Exception:
                log.info("stratum miner: device=%s", self._device_name)

        self._share_target: float = 0.0
        self._theta_micro: int = 0
        self._stop = asyncio.Event()

    @property
    def device_name(self) -> str:
        """Effective device backend in use (e.g. 'cuda', 'opencl', 'cpu')."""
        return self._device_name

    @staticmethod
    def _header_template_from_job(job: dict) -> Optional[dict]:
        header = job.get("header") or {}
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
        return header

    def _scan_header_template(
        self,
        header_view: dict,
        *,
        theta_micro: int,
        share_ratio: float,
    ) -> Optional[CpuMinerResult]:
        target_int = micro_threshold_to_target256(
            max(1, int(theta_micro * max(share_ratio, 1e-9)))
        )
        header = header_from_template_view(header_view, nonce=0)
        for nonce in range(self._scan_window):
            candidate_hash = hash_candidate_header(header, nonce=nonce)
            if candidate_hash.digest_int > target_int:
                continue
            h_micro = h_micro_from_digest(candidate_hash.digest)
            return CpuMinerResult(
                job_id=str(header_view.get("hash") or "unknown"),
                nonce=nonce,
                h_micro=h_micro,
                accepted=False,
                is_block=False,
                reason=None,
            )
        return None

    async def start(self) -> None:
        self._client.on_notify = self._on_notify
        self._client.on_set_difficulty = self._on_set_difficulty
        await self._client.connect()
        await self._client.subscribe()
        await self._client.authorize(worker=self._worker, address=self._address)

    async def stop(self) -> None:
        self._stop.set()
        await self._client.close()

    async def _on_set_difficulty(self, share_target: float, theta_micro: int) -> None:
        self._share_target = float(share_target)
        self._theta_micro = int(theta_micro)

    async def _on_notify(self, job: dict) -> None:
        # Spawn the scan+submit work in a separate task so the client's rx
        # loop (which calls this handler) is free to receive the submit
        # response. Otherwise the submit awaits a future that only resolves
        # once the rx loop returns — classic deadlock.
        if self._stop.is_set():
            return
        asyncio.create_task(self._mine_and_submit(dict(job)))

    async def _mine_and_submit(self, job: dict) -> None:
        if self._stop.is_set():
            return
        header = job.get("header") or {}
        theta_micro = self._theta_micro or int(
            job.get("thetaMicro")
            or job.get("thetaTargetMicro")
            or job.get("theta_micro")
            or 0
        )
        if theta_micro <= 0:
            log.warning(
                "[cpu-miner] missing thetaMicro; cannot mine job %s", job.get("jobId")
            )
            return
        share_ratio = float(job.get("shareTarget") or self._share_target or 0.0)
        if share_ratio <= 0.0:
            share_ratio = 1.0
        share = None
        header_template = self._header_template_from_job(job)
        if header_template is not None:
            share = self._scan_header_template(
                header_template,
                theta_micro=theta_micro,
                share_ratio=share_ratio,
            )
        else:
            sign_hex = header.get("signBytes")
            if not isinstance(sign_hex, str) or not sign_hex.startswith("0x"):
                log.warning(
                    "[cpu-miner] missing usable header template; cannot mine job %s",
                    job.get("jobId"),
                )
                return
            prefix = bytes.fromhex(sign_hex[2:])
            t_share_micro = max(1, int(theta_micro * share_ratio))
            # Prefer the hardware-aware device backend (GPU when available)
            # over the pure-Python HashScanner. The device returns the same
            # dict shape (nonce, hash/digest, h_micro/d_ratio) so we can
            # build CpuMinerResult uniformly.
            if self._device is not None:
                mix_hex = (
                    header.get("mixSeed")
                    or (job.get("hints") or {}).get("mixSeed")
                    or ""
                )
                if isinstance(mix_hex, str) and mix_hex.startswith("0x"):
                    mix_seed = bytes.fromhex(mix_hex[2:])
                else:
                    mix_seed = b"\x00" * 32
                try:
                    prepared = self._device.prepare_header(prefix, mix_seed)
                    found = self._device.scan(
                        prepared,
                        theta_micro=float(t_share_micro),
                        start_nonce=0,
                        iterations=self._scan_window,
                        max_found=1,
                        thread_id=0,
                    )
                except Exception as exc:
                    log.warning(
                        "[stratum-miner] %s scan failed (%s); falling back to "
                        "pure-Python HashScanner for this job",
                        self._device_name,
                        exc,
                    )
                    found = None
                if found:
                    entry = found[0]
                    nonce_val = int(entry.get("nonce") or 0)
                    h_micro_val = entry.get("h_micro")
                    if h_micro_val is None:
                        digest = entry.get("digest") or entry.get("hash") or b""
                        if isinstance(digest, str) and digest.startswith("0x"):
                            digest = bytes.fromhex(digest[2:])
                        if isinstance(digest, (bytes, bytearray)) and digest:
                            h_micro_val = h_micro_from_digest(bytes(digest))
                        else:
                            h_micro_val = 0
                    share = CpuMinerResult(
                        job_id=str(job.get("jobId") or "unknown"),
                        nonce=nonce_val,
                        h_micro=int(h_micro_val),
                        accepted=False,
                        is_block=False,
                        reason=None,
                    )
                    # Skip the pure-Python scan below; we already have a share.
                    # Use the same code path as before to submit.
                    return await self._submit_found_share(job, share)
            shares = self._scanner.scan_batch(
                prefix,
                t_share_micro,
                nonce_start=0,
                nonce_count=self._scan_window,
                theta_micro=theta_micro,
            )
            if shares:
                found = shares[0]
                share = CpuMinerResult(
                    job_id=str(job.get("jobId") or "unknown"),
                    nonce=found.nonce,
                    h_micro=found.h_micro,
                    accepted=False,
                    is_block=False,
                    reason=None,
                )

        if share is None:
            log.warning(
                "[stratum-miner] no shares found in window for job %s",
                job.get("jobId"),
            )
            return

        await self._submit_found_share(job, share)

    async def _submit_found_share(self, job: dict, share: CpuMinerResult) -> None:
        """Submit a single share to the pool, attaching any pending UW proofs."""
        hs_body = {"nonce": hex(share.nonce), "body": {"hMicro": share.h_micro}}

        # Attach any pending UsefulWorkProof envelopes from the AI/Quantum/
        # Storage/VDF workers (see mining.uw_inbox). The node-side UWP
        # verifier credits bonus AICF credits to the miner when accepted.
        attached_proofs = []
        try:
            from . import uw_inbox as _uw_inbox

            attached_proofs = _uw_inbox.drain(max_n=4)
        except Exception:
            attached_proofs = []

        if attached_proofs:
            res = await self._client.submit_share(
                job["jobId"], hs_body, proofs=attached_proofs
            )
        else:
            res = await self._client.submit_share(job["jobId"], hs_body)
        log.info(
            "[stratum-miner] submitted nonce=%d accepted=%s attached_proofs=%d device=%s",
            share.nonce,
            res.get("accepted"),
            len(attached_proofs),
            self._device_name,
        )

    async def run_until_stopped(self) -> None:
        await self.start()
        await self._stop.wait()
        await self.stop()
