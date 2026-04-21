from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from dataclasses import replace
from typing import Awaitable, Callable, Deque, Optional

from core.utils.pow import micro_threshold_to_target256
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
        if self._pool_mode not in {"pps", "solo", "both"}:
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
        self._current_stratum_job: Optional[StratumJob] = None
        self._current_share_threshold_micro: Optional[int] = None
        self._external_submit_hook: Optional[
            Callable[[object, StratumJob, dict, bool, Optional[str], bool, int], Awaitable[None]]
        ] = None
        self._vardiff_samples: Deque[tuple[float, bool, bool]] = deque(maxlen=24)
        self._vardiff_lock = asyncio.Lock()
        self._last_vardiff_adjust_ts = 0.0
        self._vardiff_min_samples = 8
        self._vardiff_cooldown_s = 2.0
        self._server = StratumServer(
            host=config.host,
            port=config.port,
            default_share_target=1.0,
            default_theta_micro=0,
            max_cached_jobs=128,
            validator=self._validator,
            submit_hook=self._handle_share_submit,
            pool_mode=config.pool_mode,
        )

    def set_submit_hook(
        self,
        hook: Optional[
            Callable[[object, StratumJob, dict, bool, Optional[str], bool, int], Awaitable[None]]
        ],
    ) -> None:
        self._external_submit_hook = hook

    def _difficulty_value_to_threshold_micro(self, value: float, theta_micro: int) -> int:
        """
        Normalize configured difficulty values into θµ thresholds.

        Compatibility:
        - value <= 1.0: legacy ratio mode (ratio vs current θ)
        - value > 1.0:  absolute θµ threshold
        """
        numeric = max(float(value or 0.0), 0.0)
        theta = max(int(theta_micro or 0), 0)
        if numeric <= 0:
            return 1
        if numeric <= 1.0:
            if theta <= 0:
                return 1
            return max(1, int(theta * numeric))
        return max(1, int(numeric))

    def _share_bounds_for_theta(self, theta_micro: int) -> tuple[int, int]:
        theta = max(int(theta_micro or 0), 0)
        lower = self._difficulty_value_to_threshold_micro(
            float(self._config.min_difficulty), theta
        )
        upper = self._difficulty_value_to_threshold_micro(
            float(self._config.max_difficulty), theta
        )
        if theta > 0:
            # Share threshold should not become harder than the block threshold.
            lower = min(lower, theta)
            upper = min(upper, theta)
        lower = max(1, int(lower))
        upper = max(1, int(upper))
        if upper < lower:
            upper = lower
        return lower, upper

    @staticmethod
    def _ratio_to_threshold_micro(theta_micro: int, ratio: float) -> int:
        theta = max(int(theta_micro or 0), 0)
        if theta <= 0:
            return 0
        value = max(float(ratio or 0.0), 0.0)
        if value <= 0.0:
            value = 1.0
        return max(1, int(theta * min(value, 1.0)))

    @staticmethod
    def _threshold_micro_to_ratio(theta_micro: int, threshold_micro: int) -> float:
        theta = max(int(theta_micro or 0), 0)
        if theta <= 0:
            return 1.0
        ratio = float(max(1, int(threshold_micro))) / float(theta)
        return min(max(ratio, 1e-9), 1.0)

    @staticmethod
    def _coerce_positive_float(value: object, *, fallback: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return fallback
        return parsed if parsed > 0.0 else fallback

    def _resolve_share_target(self, requested: float, theta_micro: int) -> tuple[float, int]:
        lower, upper = self._share_bounds_for_theta(theta_micro)
        requested_threshold = self._ratio_to_threshold_micro(theta_micro, requested)
        if requested_threshold <= 0:
            requested_threshold = lower
        current_threshold = (
            int(self._current_share_threshold_micro)
            if self._current_share_threshold_micro is not None
            else requested_threshold
        )
        clamped_threshold = min(max(current_threshold, lower), upper)
        if clamped_threshold != current_threshold:
            self._log.warning(
                "share_target_clamped",
                extra={
                    "requested_threshold_micro": current_threshold,
                    "clamped_threshold_micro": clamped_threshold,
                    "min_difficulty": self._config.min_difficulty,
                    "max_difficulty": self._config.max_difficulty,
                    "theta_micro": theta_micro,
                },
            )
        if theta_micro > 0 and clamped_threshold >= int(theta_micro * 0.95):
            self._log.warning(
                "share_target_near_block_target",
                extra={
                    "share_threshold_micro": clamped_threshold,
                    "theta_micro": theta_micro,
                    "note": "Shares are close to full block difficulty.",
                },
            )
        ratio = self._threshold_micro_to_ratio(theta_micro, clamped_threshold)
        return ratio, clamped_threshold

    async def start(self) -> None:
        self._job_manager.subscribe(self._on_new_job)
        self._job_manager.start()
        await self._server.start()

    async def stop(self) -> None:
        await self._server.stop()
        await self._job_manager.stop()

    async def _on_new_job(self, job: MiningJob) -> None:
        frozen_job = freeze_mining_job(job, fallback_chain_id=self._config.chain_id)
        issued_theta_micro = int(frozen_job.issued_theta_micro or frozen_job.theta_micro or 0)
        raw_hint = dict(frozen_job.raw) if isinstance(frozen_job.raw, dict) else {}
        requested_share_target = float(
            frozen_job.share_target or self._config.min_difficulty
        )
        share_target_provided = raw_hint.get("_shareTargetProvided")
        if share_target_provided is False:
            requested_share_target = float(self._config.min_difficulty)
        elif share_target_provided is True:
            requested_share_target = self._coerce_positive_float(
                raw_hint.get("_requestedShareTarget"),
                fallback=requested_share_target,
            )
        share_target, share_threshold_micro = self._resolve_share_target(
            requested_share_target,
            issued_theta_micro,
        )
        self._current_share_threshold_micro = share_threshold_micro
        if (
            share_target != float(frozen_job.share_target)
            or int(frozen_job.share_threshold_micro or 0) != int(share_threshold_micro)
        ):
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
            self._current_share_threshold_micro = int(frozen_job.share_threshold_micro or 0)

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
        self._current_stratum_job = stratum_job
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

    async def _handle_share_submit(
        self,
        session: object,
        job: StratumJob,
        submit_params: dict,
        ok: bool,
        reason: Optional[str],
        is_block: bool,
        tx_count: int,
    ) -> None:
        try:
            await self._maybe_auto_adjust_share_target(job, ok=ok, reason=reason)
        finally:
            if self._external_submit_hook is not None:
                await self._external_submit_hook(
                    session, job, submit_params, ok, reason, is_block, tx_count
                )

    async def _maybe_auto_adjust_share_target(
        self,
        job: StratumJob,
        *,
        ok: bool,
        reason: Optional[str],
    ) -> None:
        theta_micro = int(job.theta_micro or 0)
        if theta_micro <= 0:
            return
        is_low_diff_reject = (not ok) and str(reason or "").strip().lower() == "low difficulty share"

        now = time.time()
        self._vardiff_samples.append((now, bool(ok), bool(is_low_diff_reject)))
        if len(self._vardiff_samples) < self._vardiff_min_samples:
            return
        if now - self._last_vardiff_adjust_ts < self._vardiff_cooldown_s:
            return

        async with self._vardiff_lock:
            if now - self._last_vardiff_adjust_ts < self._vardiff_cooldown_s:
                return
            window = list(self._vardiff_samples)
            total = len(window)
            accepted = sum(1 for _, accepted_flag, _ in window if accepted_flag)
            low_rejects = sum(1 for _, _, low_reject_flag in window if low_reject_flag)

            current_threshold = int(
                self._current_share_threshold_micro
                or self._ratio_to_threshold_micro(theta_micro, job.share_target)
                or 1
            )
            proposed_threshold = current_threshold
            trigger: Optional[str] = None
            if low_rejects / float(total) >= 0.55:
                proposed_threshold = max(1, int(current_threshold * 0.60))
                trigger = "low_difficulty_rejects"
            elif accepted / float(total) >= 0.95 and low_rejects <= 1:
                proposed_threshold = max(1, int(current_threshold * 1.05))
                trigger = "high_accept_rate"
            else:
                return

            lower, upper = self._share_bounds_for_theta(theta_micro)
            proposed_threshold = min(max(proposed_threshold, lower), upper)
            if proposed_threshold == current_threshold:
                return

            new_share_target = self._threshold_micro_to_ratio(theta_micro, proposed_threshold)
            self._current_share_threshold_micro = proposed_threshold
            self._last_vardiff_adjust_ts = now
            self._vardiff_samples.clear()

            raw_template = (
                dict(self._current_stratum_job.raw)
                if self._current_stratum_job is not None
                and isinstance(self._current_stratum_job.raw, dict)
                else {}
            )
            raw_template["_issuedThetaMicro"] = theta_micro
            raw_template["_shareThresholdMicro"] = int(proposed_threshold)
            raw_template["_shareTargetInt"] = int(
                micro_threshold_to_target256(int(proposed_threshold))
            )
            if self._current_stratum_job is not None:
                self._current_stratum_job = replace(
                    self._current_stratum_job,
                    share_target=float(new_share_target),
                    theta_micro=theta_micro,
                    raw=raw_template,
                )

            diff_tuple = (float(new_share_target), int(theta_micro))
            if self._last_diff_tuple != diff_tuple:
                await self._server.set_global_difficulty(new_share_target, theta_micro)
                self._last_diff_tuple = diff_tuple
            if self._current_stratum_job is not None:
                await self._server.publish_job(self._current_stratum_job, clean_jobs=False)

            self._log.info(
                "share_target_auto_adjusted",
                extra={
                    "trigger": trigger,
                    "share_threshold_micro": proposed_threshold,
                    "share_target": new_share_target,
                    "theta_micro": theta_micro,
                    "accepted_samples": accepted,
                    "low_difficulty_reject_samples": low_rejects,
                    "window_size": total,
                },
            )

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
