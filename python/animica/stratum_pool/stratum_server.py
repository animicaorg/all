from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import replace
from typing import Optional

from mining.stratum_server import StratumJob, StratumServer

from .config import PoolConfig
from .core import MiningCoreAdapter, MiningJob, freeze_mining_job, job_validation_fingerprint
from .job_manager import JobManager


def _fingerprint_header(header: dict) -> str:
    encoded = json.dumps(header, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _effective_job_id(source_job_id: str, validation_fingerprint: str) -> str:
    source = str(source_job_id or "job").strip() or "job"
    suffix = (validation_fingerprint or "")[:12]
    return f"{source}-{suffix}" if suffix else source


class PoolShareValidator:
    def __init__(
        self,
        adapter: MiningCoreAdapter,
        *,
        pool_mode: str = "pps",
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._adapter = adapter
        self._pool_mode = str(pool_mode or "pps").strip().lower()
        if self._pool_mode not in {"pps", "solo"}:
            self._pool_mode = "pps"
        self._log = logger or logging.getLogger("animica.stratum_pool.validator")

    async def validate(self, job: StratumJob, submit_params):
        address = str(submit_params.get("_address") or "").strip()
        if not address:
            return False, "missing miner payout address", False, 0
        if not address.startswith("anim1"):
            return False, "invalid miner payout address", False, 0
        raw_template = dict(job.raw) if isinstance(job.raw, dict) else {}
        source_job_id = str(raw_template.get("_sourceJobId") or job.job_id)
        mining_job = MiningJob(
            job_id=job.job_id,
            source_job_id=source_job_id,
            header=job.header,
            theta_micro=job.theta_micro,
            share_target=job.share_target,
            height=submit_params.get("height") or job.height or 0,
            hints=job.hints,
            target=job.target,
            sign_bytes=job.sign_bytes,
            template_id=raw_template.get("templateId"),
            parent_hash=job.parent_hash,
            parent_height=job.parent_height,
            chain_id=job.chain_id,
            expires_at=job.expires_at,
            issued_theta_micro=raw_template.get("_issuedThetaMicro"),
            share_threshold_micro=raw_template.get("_shareThresholdMicro"),
            share_target_int=raw_template.get("_shareTargetInt"),
            block_target_int=raw_template.get("_blockTargetInt"),
            mix_seed=raw_template.get("_mixSeed"),
            proof_type=raw_template.get("_proofType"),
            validation_fingerprint=raw_template.get("_validationFingerprint"),
            raw=raw_template,
        )
        fallback_chain_id = int(getattr(self._adapter, "chain_id", 0) or 0)
        mining_job = freeze_mining_job(
            mining_job, fallback_chain_id=fallback_chain_id
        )
        try:
            return await self._adapter.validate_and_submit_share(
                mining_job, submit_params
            )
        except Exception as exc:  # noqa: BLE001
            self._log.warning("share validation failed", exc_info=True)
            return False, str(exc), False, 0


class StratumPoolServer:
    def __init__(
        self,
        adapter: MiningCoreAdapter,
        config: PoolConfig,
        job_manager: JobManager,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._adapter = adapter
        self._config = config
        self._job_manager = job_manager
        self._log = logger or logging.getLogger("animica.stratum_pool.server")
        self._validator = PoolShareValidator(
            adapter,
            pool_mode=config.pool_mode,
            logger=logger,
        )
        self._last_published_job_id: Optional[str] = None
        self._last_published_fingerprint: Optional[str] = None
        self._last_diff_tuple: Optional[tuple[float, int]] = None
        self._server = StratumServer(
            host=config.host,
            port=config.port,
            default_share_target=config.min_difficulty,
            default_theta_micro=0,
            max_cached_jobs=128,
            validator=self._validator,
        )

    def _resolve_share_target(self, requested: float) -> float:
        value = float(requested or 0.0)
        if value <= 0.0:
            value = float(self._config.min_difficulty)

        lower = max(1e-9, float(self._config.min_difficulty))
        upper = min(1.0, float(self._config.max_difficulty))
        if upper < lower:
            upper = lower

        clamped = min(max(value, lower), upper)
        if clamped != value:
            self._log.warning(
                "share_target_clamped",
                extra={
                    "requested": value,
                    "clamped": clamped,
                    "min_difficulty": self._config.min_difficulty,
                    "max_difficulty": self._config.max_difficulty,
                },
            )
        if clamped >= 0.95:
            self._log.warning(
                "share_target_near_block_target",
                extra={
                    "share_target": clamped,
                    "note": "Shares will be close to full block difficulty.",
                },
            )
        return clamped

    async def start(self) -> None:
        self._job_manager.subscribe(self._on_new_job)
        self._job_manager.start()
        await self._server.start()

    async def stop(self) -> None:
        await self._server.stop()
        await self._job_manager.stop()

    async def _on_new_job(self, job: MiningJob) -> None:
        frozen_job = freeze_mining_job(job, fallback_chain_id=self._config.chain_id)
        share_target = self._resolve_share_target(
            frozen_job.share_target or self._config.min_difficulty
        )
        if share_target != float(frozen_job.share_target):
            frozen_job = freeze_mining_job(
                replace(
                    frozen_job,
                    share_target=float(share_target),
                    share_threshold_micro=None,
                    share_target_int=None,
                    validation_fingerprint=None,
                ),
                fallback_chain_id=self._config.chain_id,
            )

        validation_fingerprint = (
            frozen_job.validation_fingerprint or job_validation_fingerprint(frozen_job)
        )
        effective_job_id = _effective_job_id(
            str(frozen_job.job_id), validation_fingerprint
        )

        header = dict(frozen_job.header or {})
        if frozen_job.sign_bytes:
            header.setdefault("signBytes", frozen_job.sign_bytes)
        if frozen_job.target:
            header.setdefault("target", frozen_job.target)
        if frozen_job.height:
            header.setdefault("number", frozen_job.height)
        issued_theta_micro = int(frozen_job.issued_theta_micro or frozen_job.theta_micro or 0)
        if issued_theta_micro > 0:
            header.setdefault("thetaMicro", issued_theta_micro)
            header.setdefault("thetaTargetMicro", issued_theta_micro)
            header.setdefault("theta_target_micro", issued_theta_micro)

        raw_template = dict(frozen_job.raw) if isinstance(frozen_job.raw, dict) else {}
        raw_template["header"] = dict(header)
        raw_template["_sourceJobId"] = str(frozen_job.source_job_id or frozen_job.job_id)
        raw_template["_effectiveJobId"] = effective_job_id
        raw_template["_validationFingerprint"] = validation_fingerprint
        raw_template["_issuedThetaMicro"] = issued_theta_micro
        raw_template["_shareThresholdMicro"] = int(frozen_job.share_threshold_micro or 0)
        raw_template["_shareTargetInt"] = int(frozen_job.share_target_int or 0)
        raw_template["_blockTargetInt"] = int(frozen_job.block_target_int or 0)
        raw_template["_mixSeed"] = frozen_job.mix_seed
        raw_template["_proofType"] = frozen_job.proof_type
        raw_template["_headerFingerprint"] = _fingerprint_header(header)

        stratum_job = StratumJob(
            job_id=effective_job_id,
            header=header,
            share_target=share_target,
            theta_micro=issued_theta_micro,
            hints=frozen_job.hints,
            target=frozen_job.target,
            sign_bytes=frozen_job.sign_bytes or header.get("signBytes"),
            height=frozen_job.height,
            parent_hash=frozen_job.parent_hash,
            parent_height=frozen_job.parent_height,
            chain_id=frozen_job.chain_id,
            expires_at=frozen_job.expires_at,
            proof_type=frozen_job.proof_type,
            raw=raw_template,
        )
        diff_tuple = (float(stratum_job.share_target), int(stratum_job.theta_micro))
        if self._last_diff_tuple != diff_tuple:
            await self._server.set_global_difficulty(
                stratum_job.share_target,
                stratum_job.theta_micro,
            )
            self._last_diff_tuple = diff_tuple

        clean_jobs = (
            stratum_job.job_id != self._last_published_job_id
            or validation_fingerprint != self._last_published_fingerprint
        )
        await self._server.publish_job(stratum_job, clean_jobs=clean_jobs)
        self._last_published_job_id = stratum_job.job_id
        self._last_published_fingerprint = validation_fingerprint

    async def wait_closed(self) -> None:
        while True:
            await asyncio.sleep(1)

    @property
    def stratum(self) -> StratumServer:
        return self._server

    def stats(self) -> dict:
        return self._server.stats()

    def session_snapshots(self):
        return self._server.session_snapshots()
