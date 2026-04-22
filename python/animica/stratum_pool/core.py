from __future__ import annotations

"""
Core adapters that bridge the Stratum pool to the existing Animica mining module.

Discovered mining APIs in this repository
----------------------------------------
- ``mining.templates.TemplateBuilder`` builds ``WorkTemplate`` instances that
  encapsulate header fields and sign-bytes; the builder is fed by small
  callables that read head info, Θ (theta) and policy roots. Its
  ``current_template(force=False)`` method caches until head or Θ changes.
- ``mining.stratum_server.StratumServer`` and its ``StratumJob`` dataclass are
  the canonical Stratum V1 server implementation used by Animica. It validates
  shares via ``ShareValidator.validate(job, submit_params)`` which, when
  available, delegates to ``mining.adapters.proofs_view.verify_hashshare_envelope``
  so that HashShare envelopes are verified using the real proofs logic rather
  than custom hashing.

This module reuses those components directly: it prefers ``miner.getBlockTemplate``
for canonical block assembly (same submit path as ``animica miner mine-blocks``),
falls back to ``miner.getWork`` only when template APIs are unavailable, validates
shares with ``ShareValidator`` and submits either:
  - ``miner.submitBlock`` for canonical template-backed candidates
  - ``miner.submitWork`` for legacy getWork compatibility
"""

import asyncio
import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple

from mining.pow_validation import (derive_block_target_int as _pow_block_target_int,
                                   derive_share_target_int as _pow_share_target_int,
                                   derive_share_threshold_micro as _pow_share_threshold_micro,
                                   evaluate_digest as _pow_evaluate_digest,
                                   normalize_share_ratio as _pow_normalize_share_ratio)
from mining.share_submitter import JsonRpcClient, RpcError
from mining.stratum_server import ShareValidator, StratumJob
from mining.template_block import (build_submit_block_payload,
                                   hash_candidate_header,
                                   header_sign_bytes_from_template_view,
                                   int_from_value,
                                   looks_like_block_template,
                                   template_tx_count)

Json = Dict[str, Any]


class TemplateUnavailable(RuntimeError):
    """Raised when the node cannot issue a usable block template yet."""

    def __init__(
        self,
        reason: str,
        *,
        wait_seconds: float = 0.0,
        head: Optional[Json] = None,
    ) -> None:
        self.reason = str(reason or "unknown")
        self.wait_seconds = max(0.0, float(wait_seconds or 0.0))
        self.head = head if isinstance(head, dict) else {}
        super().__init__(f"block template unavailable ({self.reason})")


def _parse_int(value: Any, *, default: int = 0) -> int:
    try:
        return int_from_value(value, default=default)
    except Exception:
        return default


def _parse_float(value: Any, *, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except Exception:
        return default


def _extract_share_target_from_payload(work: Json, header: Json) -> tuple[float, bool]:
    containers: list[Json] = []
    for candidate in (
        work,
        work.get("targetHint"),
        work.get("target_hint"),
        work.get("hints"),
        header,
        header.get("targetHint"),
        header.get("target_hint"),
        header.get("hints"),
    ):
        if isinstance(candidate, dict):
            containers.append(candidate)

    for container in containers:
        for key in (
            "shareTarget",
            "share_target",
            "share_target_fraction",
            "shareRatio",
            "share_ratio",
        ):
            share_target = _parse_float(container.get(key), default=0.0)
            if share_target > 0:
                return share_target, True
    return 0.0, False


def _derive_share_threshold_micro(theta_micro: int, share_ratio: Any) -> int:
    return _pow_share_threshold_micro(theta_micro, share_ratio)


def _derive_share_target_int(theta_micro: int, share_ratio: Any) -> int:
    return _pow_share_target_int(theta_micro, share_ratio)


def _derive_block_target_int(target_value: Any) -> int:
    return _pow_block_target_int(target_value)


def _normalize_hex(value: Any) -> Optional[str]:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        return text.lower()
    return None


def _template_parent_hash(work: Json, header: Json) -> Optional[str]:
    parent = work.get("parent")
    parent_hash = None
    if isinstance(parent, dict):
        parent_hash = parent.get("hash")
    parent_hash = parent_hash or work.get("parentHash") or header.get("parentHash")
    return _normalize_hex(parent_hash)


def _template_parent_height(work: Json, header: Json) -> Optional[int]:
    parent = work.get("parent")
    parent_height = None
    if isinstance(parent, dict):
        parent_height = parent.get("height") or parent.get("number")
    parent_height = parent_height or work.get("parentHeight") or header.get("parentHeight")
    parsed = _parse_int(parent_height, default=0)
    return parsed if parsed > 0 else None


def _template_chain_id(work: Json, header: Json, fallback: int) -> int:
    parsed = _parse_int(
        work.get("chainId")
        or work.get("chain_id")
        or header.get("chainId")
        or header.get("chain_id")
        or fallback,
        default=fallback,
    )
    return parsed if parsed > 0 else int(fallback)


def _template_mempool_fingerprint(work: Json, header: Json) -> str:
    tx_root = _normalize_hex(header.get("txsRoot")) or "0x"
    tx_count = template_tx_count(work)
    hashes: list[str] = []
    txs = work.get("txs")
    if isinstance(txs, list):
        for entry in txs:
            if not isinstance(entry, dict):
                continue
            tx_hash = _normalize_hex(entry.get("hash"))
            if tx_hash:
                hashes.append(tx_hash)
    digest = hashlib.sha256(",".join(sorted(hashes)).encode("utf-8")).hexdigest()
    return f"{tx_root}:{tx_count}:{digest}"


def _extract_submit_nonce(params: Json) -> int:
    hashshare = params.get("hashshare") or {}
    nonce: Any = None
    for candidate in (
        hashshare.get("nonce"),
        hashshare.get("n"),
        params.get("nonce"),
        params.get("nonce64"),
        params.get("n"),
    ):
        if candidate is None or candidate == "":
            continue
        nonce = candidate
        break
    if nonce is None:
        raise ValueError("hashshare.nonce is required")
    return int_from_value(nonce, default=-1)


def _parse_submit_timestamp_value(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        parsed = int(value)
        return parsed if parsed > 0 else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        lowered = text.lower()
        try:
            if lowered.startswith("0x"):
                parsed = int(lowered, 16)
            elif len(lowered) == 8 and all(ch in "0123456789abcdef" for ch in lowered):
                # Stratum V1 ntime is commonly an 8-hex-digit field.
                parsed = int(lowered, 16)
            else:
                parsed = int_from_value(text, default=-1)
        except Exception:
            return None
        return parsed if parsed > 0 else None
    return None


def _extract_submit_timestamp(params: Json) -> Optional[int]:
    hashshare = params.get("hashshare") or {}
    body = hashshare.get("body") if isinstance(hashshare, dict) else {}
    for candidate in (
        hashshare.get("ntime"),
        body.get("ntime") if isinstance(body, dict) else None,
        params.get("ntime"),
        params.get("timestamp"),
        params.get("time"),
    ):
        parsed = _parse_submit_timestamp_value(candidate)
        if parsed is not None:
            return parsed
    return None


@dataclass
class MiningJob:
    job_id: str
    header: Json
    theta_micro: int
    share_target: float
    height: int
    target: Optional[str] = None
    sign_bytes: Optional[str] = None
    hints: Optional[Json] = None
    template_id: Optional[str] = None
    parent_hash: Optional[str] = None
    issued_at: Optional[float] = None
    expires_at: Optional[float] = None
    head_hash_at_issue: Optional[str] = None
    mempool_fingerprint: Optional[str] = None
    source_job_id: Optional[str] = None
    chain_id: Optional[int] = None
    parent_height: Optional[int] = None
    issued_theta_micro: Optional[int] = None
    share_threshold_micro: Optional[int] = None
    share_target_int: Optional[int] = None
    block_target_int: Optional[int] = None
    mix_seed: Optional[str] = None
    proof_type: Optional[str] = None
    validation_fingerprint: Optional[str] = None
    raw: Json = field(default_factory=dict)


def _job_validation_payload(job: MiningJob) -> Json:
    header_for_hash = job.header if isinstance(job.header, dict) else {}
    header_serialized = json.dumps(
        header_for_hash, sort_keys=True, separators=(",", ":"), default=str
    )
    return {
        "source_job_id": str(job.source_job_id or job.job_id),
        "template_id": job.template_id,
        "chain_id": int(job.chain_id or 0),
        "height": int(job.height or 0),
        "parent_hash": _normalize_hex(job.parent_hash),
        "parent_height": int(job.parent_height or 0),
        "issued_theta_micro": int(job.issued_theta_micro or job.theta_micro or 0),
        "share_threshold_micro": int(job.share_threshold_micro or 0),
        "share_target_int": int(job.share_target_int or 0),
        "block_target_int": int(job.block_target_int or 0),
        "sign_bytes": job.sign_bytes,
        "mix_seed": _normalize_hex(job.mix_seed),
        "proof_type": job.proof_type,
        "header_sha256": hashlib.sha256(header_serialized.encode("utf-8")).hexdigest(),
    }


def job_validation_fingerprint(job: MiningJob) -> str:
    payload = _job_validation_payload(job)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def freeze_mining_job(job: MiningJob, *, fallback_chain_id: int) -> MiningJob:
    raw_template: Json = dict(job.raw) if isinstance(job.raw, dict) else {}
    header_source: Any = (
        job.header
        if isinstance(job.header, dict)
        else raw_template.get("header")
        if isinstance(raw_template.get("header"), dict)
        else {}
    )
    header_view: Json = dict(header_source) if isinstance(header_source, dict) else {}
    raw_template["header"] = dict(header_view)

    sign_bytes = job.sign_bytes

    hints = dict(job.hints) if isinstance(job.hints, dict) else {}
    mix_seed = _normalize_hex(
        job.mix_seed
        or hints.get("mixSeed")
        or header_view.get("mixSeed")
        or raw_template.get("mixSeed")
    )
    if mix_seed and "mixSeed" not in hints:
        hints["mixSeed"] = mix_seed

    source_job_id = str(job.source_job_id or raw_template.get("_sourceJobId") or job.job_id)
    parent_hash = _normalize_hex(job.parent_hash) or _template_parent_hash(raw_template, header_view)
    parent_height = (
        int(job.parent_height)
        if job.parent_height is not None
        else _template_parent_height(raw_template, header_view)
    )
    chain_id = _template_chain_id(
        raw_template,
        header_view,
        int(job.chain_id or fallback_chain_id or 0),
    )
    issued_theta_micro = int(
        job.issued_theta_micro
        or job.theta_micro
        or _parse_int(
            raw_template.get("thetaMicro")
            or header_view.get("thetaMicro")
            or header_view.get("thetaTargetMicro")
            or header_view.get("theta_target_micro"),
            default=0,
        )
    )
    if issued_theta_micro > 0:
        header_view["thetaMicro"] = issued_theta_micro
        header_view["thetaTargetMicro"] = issued_theta_micro
        header_view["theta_target_micro"] = issued_theta_micro
        raw_template["header"] = dict(header_view)
    if sign_bytes is None:
        try:
            sign_bytes = "0x" + header_sign_bytes_from_template_view(header_view).hex()
        except Exception:
            pass
    share_ratio = _pow_normalize_share_ratio(job.share_target, default=1.0)
    share_threshold_micro = int(job.share_threshold_micro or 0) or _derive_share_threshold_micro(
        issued_theta_micro,
        share_ratio,
    )
    share_target_int = int(job.share_target_int or 0) or _derive_share_target_int(
        issued_theta_micro,
        share_ratio,
    )
    block_target_int = int(job.block_target_int or 0) or _derive_block_target_int(
        job.target or raw_template.get("target") or header_view.get("target")
    )
    proof_type = (
        str(job.proof_type)
        if job.proof_type not in (None, "")
        else str(raw_template.get("proofType") or header_view.get("proofType") or "")
        or None
    )
    raw_template["_sourceJobId"] = source_job_id
    raw_template["_issuedThetaMicro"] = issued_theta_micro
    raw_template["_shareThresholdMicro"] = share_threshold_micro
    raw_template["_shareTargetInt"] = share_target_int
    raw_template["_blockTargetInt"] = block_target_int
    if mix_seed:
        raw_template["_mixSeed"] = mix_seed
    if proof_type:
        raw_template["_proofType"] = proof_type

    frozen = MiningJob(
        job_id=str(job.job_id),
        source_job_id=source_job_id,
        header=header_view,
        theta_micro=issued_theta_micro,
        share_target=share_ratio,
        height=_parse_int(job.height or header_view.get("height") or header_view.get("number"), default=0),
        target=job.target if job.target is not None else raw_template.get("target") or header_view.get("target"),
        sign_bytes=sign_bytes,
        hints=hints or None,
        template_id=job.template_id,
        parent_hash=parent_hash,
        parent_height=parent_height,
        chain_id=chain_id,
        issued_at=job.issued_at,
        expires_at=job.expires_at,
        head_hash_at_issue=job.head_hash_at_issue,
        mempool_fingerprint=job.mempool_fingerprint,
        issued_theta_micro=issued_theta_micro,
        share_threshold_micro=share_threshold_micro,
        share_target_int=share_target_int,
        block_target_int=block_target_int,
        mix_seed=mix_seed,
        proof_type=proof_type,
        raw=raw_template,
    )
    frozen.validation_fingerprint = job.validation_fingerprint or job_validation_fingerprint(frozen)
    return frozen


def _is_frozen_job(job: MiningJob) -> bool:
    """Fast check for jobs that already carry normalized validation metadata."""
    return bool(
        str(job.validation_fingerprint or "").strip()
        and int(job.issued_theta_micro or 0) > 0
        and int(job.share_threshold_micro or 0) > 0
        and int(job.share_target_int or 0) > 0
    )


class MiningCoreAdapter:
    def __init__(
        self,
        rpc_url: str,
        chain_id: int,
        pool_address: str,
        rpc_timeout_s: float = 15.0,
        *,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self._rpc = JsonRpcClient(rpc_url, timeout_s=rpc_timeout_s)
        self._validator = ShareValidator()
        self._chain_id = chain_id
        self._pool_address = pool_address
        self._log = logger or logging.getLogger("animica.stratum_pool.core")

    async def _rpc_call(self, method: str, params: Any) -> Any:
        return await asyncio.to_thread(self._rpc.call, method, params)

    @property
    def chain_id(self) -> int:
        return self._chain_id

    async def get_head_snapshot(self) -> Json:
        last_exc: Optional[Exception] = None
        for method in ("chain.getHead", "chain_getHead"):
            try:
                result = await self._rpc_call(method, [])
                self._log.debug(
                    "DEBUG_STRATUM_HEAD_SNAPSHOT_TRACE method=%s result=%s",
                    method,
                    result,
                )
            except RpcError as exc:
                last_exc = exc
                if exc.code == -32601:
                    continue
                raise RuntimeError(f"unable to fetch chain head: {exc}") from exc
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                continue
            if isinstance(result, dict):
                return result
        if last_exc is not None:
            self._log.debug("head snapshot unavailable: %s", last_exc)
        return {}

    @staticmethod
    def _block_template_param_variants(pool_address: str) -> Iterable[Any]:
        address = str(pool_address).strip()
        if not address:
            return ()
        payload_snake = {
            "address": address,
            "include_mempool": True,
            "disable_block_time_limits": True,
        }
        payload_snake_payout = {
            "payout_address": address,
            "include_mempool": True,
            "disable_block_time_limits": True,
        }
        payload_camel = {
            "address": address,
            "includeMempool": True,
            "disableBlockTimeLimits": True,
        }
        payload_camel_payout = {
            "payout_address": address,
            "includeMempool": True,
            "disableBlockTimeLimits": True,
        }
        return (
            payload_snake,
            payload_snake_payout,
            payload_camel,
            payload_camel_payout,
            [payload_snake],
            [payload_camel],
            [address, True, True],
            [address, True],
            [address],
        )

    async def get_new_job(self) -> MiningJob:
        last_exc: Optional[Exception] = None
        work: Optional[Json] = None

        if self._pool_address:
            deferred_template_unavailable: Optional[TemplateUnavailable] = None
            for template_params in self._block_template_param_variants(self._pool_address):
                try:
                    template = await self._rpc_call(
                        "miner.getBlockTemplate",
                        template_params,
                    )
                    if isinstance(template, dict):
                        if template.get("enabled") is False:
                            deferred_template_unavailable = TemplateUnavailable(
                                str(template.get("reason") or "disabled"),
                                wait_seconds=_parse_float(
                                    template.get("waitSeconds"), default=0.0
                                ),
                                head=(
                                    template.get("head")
                                    if isinstance(template.get("head"), dict)
                                    else {}
                                ),
                            )
                            continue
                        if looks_like_block_template(template):
                            work = template
                            break
                        last_exc = RuntimeError("block template payload missing header/target")
                except RpcError as exc:
                    last_exc = exc
                    if exc.code == -32602:
                        continue
                    raise RuntimeError(
                        f"unable to fetch block template: {exc}"
                    ) from exc
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    raise
            if work is None:
                if deferred_template_unavailable is not None:
                    raise deferred_template_unavailable
                raise RuntimeError(
                    f"unable to fetch block template for pool mining: {last_exc}"
                )

        metadata = {"chainId": self._chain_id}
        if self._pool_address:
            metadata["address"] = self._pool_address

        params_variants = [[metadata], []]
        method_variants = ("miner.getWork", "miner_getWork")

        if work is None:
            for method in method_variants:
                for params in params_variants:
                    try:
                        work = await self._rpc_call(method, params)
                        if work:
                            break
                    except RpcError as exc:
                        last_exc = exc
                        if exc.code == -32601:
                            break
                    except Exception as exc:  # noqa: BLE001
                        last_exc = exc
                if work:
                    break

        if work is None:
            raise RuntimeError(f"unable to fetch work: {last_exc}")

        header = work.get("header") or {}
        if not isinstance(header, dict):
            header = {}
        template_id = work.get("templateId") or work.get("template_id")
        job_id = str(
            template_id
            or work.get("jobId")
            or work.get("job_id")
            or work.get("headerHash")
            or header.get("hash")
            or uuid.uuid4().hex
        )
        theta_micro = _parse_int(
            work.get("thetaMicro")
            or work.get("theta_target_micro")
            or work.get("thetaTargetMicro")
            or header.get("thetaMicro")
            or header.get("thetaTargetMicro")
            or header.get("theta_target_micro")
            or 0
        )
        share_target, share_target_provided = _extract_share_target_from_payload(
            work, header
        )
        work["_shareTargetProvided"] = bool(share_target_provided)
        work["_requestedShareTarget"] = float(share_target) if share_target_provided else None
        height = _parse_int(
            work.get("height") or header.get("number") or header.get("height") or 0
        )
        target = work.get("target") if work.get("target") is not None else header.get("target")
        sign_bytes = work.get("signBytes")
        if sign_bytes is None and isinstance(header, dict):
            try:
                sign_bytes = "0x" + header_sign_bytes_from_template_view(header).hex()
            except Exception:
                sign_bytes = None
        hints = work.get("hints") or {}
        if not hints and isinstance(header, dict) and header.get("mixSeed"):
            hints = {"mixSeed": header.get("mixSeed")}
        issued_at = _parse_float(work.get("issuedAt"), default=0.0)
        expires_at = _parse_float(work.get("expiresAt"), default=0.0)
        job_parent_hash = _template_parent_hash(work, header)
        job_parent_height = _template_parent_height(work, header)
        chain_id = _template_chain_id(work, header, self._chain_id)
        mempool_fingerprint = (
            _template_mempool_fingerprint(work, header)
            if looks_like_block_template(work)
            else None
        )

        job = MiningJob(
            job_id=job_id,
            source_job_id=job_id,
            header=header,
            theta_micro=theta_micro,
            share_target=share_target,
            height=height,
            target=target,
            sign_bytes=sign_bytes,
            hints=hints,
            template_id=str(template_id) if template_id is not None else None,
            parent_hash=job_parent_hash,
            parent_height=job_parent_height,
            chain_id=chain_id,
            issued_at=issued_at if issued_at > 0 else None,
            expires_at=expires_at if expires_at > 0 else None,
            head_hash_at_issue=_normalize_hex(work.get("headHashAtIssue")),
            mempool_fingerprint=mempool_fingerprint,
            raw=work,
        )
        return freeze_mining_job(job, fallback_chain_id=self._chain_id)

    async def _head_hash_height(self) -> tuple[Optional[str], Optional[int], Json]:
        head = await self.get_head_snapshot()
        head_hash = _normalize_hex(
            head.get("hash") or head.get("block_hash") or head.get("head")
        )
        head_height = _parse_int(head.get("height"), default=-1)
        if head_height < 0:
            head_height = None
        return head_hash, head_height, head

    def _log_share_reject(
        self,
        *,
        submit_params: Json,
        job: MiningJob,
        reason: str,
        share_hash_hex: Optional[str],
        share_target_int: int,
        block_target_int: int,
        template_height: Optional[int],
        template_parent_hash: Optional[str],
        head_hash: Optional[str],
        head_height: Optional[int],
        stale_by_server: bool,
        local_prevalidation_ok: bool,
        node_rejection: Any = None,
        nonce: Optional[int] = None,
        digest_int: Optional[int] = None,
        frozen_theta_micro: Optional[int] = None,
        share_threshold_micro: Optional[int] = None,
        live_theta_micro: Optional[int] = None,
        head_changed: Optional[bool] = None,
    ) -> None:
        self._log.warning(
            "stratum_share_rejected",
            extra={
                "worker": submit_params.get("_worker") or submit_params.get("worker"),
                "session_id": submit_params.get("_session_id"),
                "job_id": job.job_id,
                "source_job_id": job.source_job_id,
                "address": submit_params.get("_address"),
                "nonce": nonce,
                "share_hash": share_hash_hex,
                "share_hash_int": hex(int(digest_int)) if digest_int is not None else None,
                "share_target": hex(int(share_target_int))
                if share_target_int > 0
                else "0x0",
                "block_target": hex(int(block_target_int))
                if block_target_int > 0
                else "0x0",
                "frozen_theta_micro": frozen_theta_micro,
                "share_threshold_micro": share_threshold_micro,
                "current_head_height": head_height,
                "current_head_hash": head_hash,
                "current_head_theta_micro": live_theta_micro,
                "head_changed": head_changed,
                "template_height": template_height,
                "template_parent_hash": template_parent_hash,
                "template_id": job.template_id,
                "template_issued_at": job.issued_at,
                "template_expires_at": job.expires_at,
                "template_head_hash_at_issue": job.head_hash_at_issue,
                "stale_by_server_state": stale_by_server,
                "local_prevalidation_ok": local_prevalidation_ok,
                "node_rejection": node_rejection,
                "reason": reason,
            },
        )

    async def _validate_and_submit_template_block(
        self, job: MiningJob, submit_params: Json
    ) -> Tuple[bool, Optional[str], bool, int]:
        if not _is_frozen_job(job):
            job = freeze_mining_job(job, fallback_chain_id=self._chain_id)
        template = dict(job.raw) if isinstance(job.raw, dict) else {}
        header_view = dict(job.header) if isinstance(job.header, dict) else {}
        if not header_view and isinstance(template.get("header"), dict):
            header_view = dict(template.get("header") or {})
        if not isinstance(header_view, dict) or not header_view:
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason="missing header template",
                share_hash_hex=None,
                share_target_int=0,
                block_target_int=0,
                template_height=None,
                template_parent_hash=None,
                head_hash=None,
                head_height=None,
                stale_by_server=True,
                local_prevalidation_ok=False,
            )
            return False, "missing header template", False, 0
        template["header"] = dict(header_view)

        nonce_int = _extract_submit_nonce(submit_params)
        if nonce_int < 0:
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason="invalid nonce",
                share_hash_hex=None,
                share_target_int=int(job.share_target_int or 0),
                block_target_int=int(job.block_target_int or 0),
                template_height=int(job.height or 0) or None,
                template_parent_hash=job.parent_hash,
                head_hash=None,
                head_height=None,
                stale_by_server=False,
                local_prevalidation_ok=False,
                nonce=nonce_int,
                frozen_theta_micro=int(job.issued_theta_micro or job.theta_micro or 0),
                share_threshold_micro=int(job.share_threshold_micro or 0),
            )
            return False, "invalid nonce", False, 0

        submit_timestamp = _extract_submit_timestamp(submit_params)
        if submit_timestamp is not None:
            header_view = dict(header_view)
            header_view["timestamp"] = int(submit_timestamp)
            template["header"] = dict(header_view)

        try:
            candidate_hash = hash_candidate_header(header_view, nonce=nonce_int)
        except Exception as exc:  # noqa: BLE001
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason=f"invalid header template: {exc}",
                share_hash_hex=None,
                share_target_int=int(job.share_target_int or 0),
                block_target_int=int(job.block_target_int or 0),
                template_height=int(job.height or 0) or None,
                template_parent_hash=job.parent_hash,
                head_hash=None,
                head_height=None,
                stale_by_server=False,
                local_prevalidation_ok=False,
                nonce=nonce_int,
                frozen_theta_micro=int(job.issued_theta_micro or job.theta_micro or 0),
                share_threshold_micro=int(job.share_threshold_micro or 0),
            )
            return False, f"invalid header template: {exc}", False, 0

        digest_int = int(candidate_hash.digest_int)
        template_parent_hash = job.parent_hash or _template_parent_hash(template, header_view)
        template_height = int(job.height or _parse_int(header_view.get("height") or header_view.get("number"), default=0))
        theta_micro = int(job.issued_theta_micro or job.theta_micro or 0)
        block_target = int(job.block_target_int or 0) or _derive_block_target_int(
            job.target or template.get("target") or header_view.get("target")
        )
        decision = _pow_evaluate_digest(
            digest_int,
            theta_micro=theta_micro,
            share_ratio=job.share_target,
            block_target=block_target,
            enforce_share_target=True,
        )
        share_threshold_micro = int(decision.share_threshold_micro)
        share_target_int = int(decision.share_target_int)
        block_target = int(decision.block_target_int)

        if theta_micro <= 0:
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason="missing thetaMicro",
                share_hash_hex="0x" + candidate_hash.digest.hex(),
                share_target_int=share_target_int,
                block_target_int=block_target,
                template_height=template_height or None,
                template_parent_hash=template_parent_hash,
                head_hash=None,
                head_height=None,
                stale_by_server=False,
                local_prevalidation_ok=False,
                nonce=nonce_int,
                digest_int=digest_int,
                frozen_theta_micro=theta_micro,
                share_threshold_micro=share_threshold_micro,
            )
            return False, "missing thetaMicro", False, 0

        if share_target_int <= 0:
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason="missing share target",
                share_hash_hex="0x" + candidate_hash.digest.hex(),
                share_target_int=share_target_int,
                block_target_int=block_target,
                template_height=template_height or None,
                template_parent_hash=template_parent_hash,
                head_hash=None,
                head_height=None,
                stale_by_server=False,
                local_prevalidation_ok=False,
                nonce=nonce_int,
                digest_int=digest_int,
                frozen_theta_micro=theta_micro,
                share_threshold_micro=share_threshold_micro,
            )
            return False, "missing share target", False, 0

        self._log.debug(
            "stratum_submit_job_matched",
            extra={
                "worker": submit_params.get("_worker") or submit_params.get("worker"),
                "session_id": submit_params.get("_session_id"),
                "job_id": job.job_id,
                "source_job_id": job.source_job_id,
                "template_id": job.template_id,
                "template_height": template_height or None,
                "template_parent_hash": template_parent_hash,
                "template_issued_at": job.issued_at,
                "template_expires_at": job.expires_at,
                "nonce": nonce_int,
                "share_hash": "0x" + candidate_hash.digest.hex(),
                "share_hash_int": hex(digest_int),
                "frozen_theta_micro": theta_micro,
                "share_threshold_micro": share_threshold_micro,
                "frozen_share_target": hex(share_target_int),
                "frozen_block_target": hex(block_target) if block_target > 0 else "0x0",
            },
        )

        share_ok = digest_int <= share_target_int
        self._log.debug(
            "stratum_submit_share_target_compare",
            extra={
                "worker": submit_params.get("_worker") or submit_params.get("worker"),
                "session_id": submit_params.get("_session_id"),
                "address": submit_params.get("_address"),
                "job_id": job.job_id,
                "source_job_id": job.source_job_id,
                "template_id": job.template_id,
                "nonce": nonce_int,
                "share_hash": "0x" + candidate_hash.digest.hex(),
                "share_hash_int": hex(digest_int),
                "share_target": hex(share_target_int),
                "share_ratio": float(job.share_target),
                "theta_micro": theta_micro,
                "share_threshold_micro": share_threshold_micro,
                "block_target": hex(int(block_target)) if block_target > 0 else "0x0",
                "pass": bool(decision.share_ok),
            },
        )

        share_ok = bool(decision.share_ok)
        if not share_ok:
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason="low difficulty share",
                share_hash_hex="0x" + candidate_hash.digest.hex(),
                share_target_int=share_target_int,
                block_target_int=block_target,
                template_height=template_height or None,
                template_parent_hash=template_parent_hash,
                head_hash=None,
                head_height=None,
                stale_by_server=False,
                local_prevalidation_ok=True,
                nonce=nonce_int,
                digest_int=digest_int,
                frozen_theta_micro=theta_micro,
                share_threshold_micro=share_threshold_micro,
            )
            return False, "low difficulty share", False, 0

        is_block = bool(decision.is_block)
        self._log.debug(
            "stratum_submit_block_target_compare",
            extra={
                "worker": submit_params.get("_worker") or submit_params.get("worker"),
                "session_id": submit_params.get("_session_id"),
                "address": submit_params.get("_address"),
                "job_id": job.job_id,
                "source_job_id": job.source_job_id,
                "template_id": job.template_id,
                "nonce": nonce_int,
                "share_hash": "0x" + candidate_hash.digest.hex(),
                "share_hash_int": hex(digest_int),
                "block_target": hex(int(block_target)) if block_target > 0 else "0x0",
                "theta_micro": theta_micro,
                "share_threshold_micro": share_threshold_micro,
                "pass": is_block,
            },
        )
        tx_count = template_tx_count(template)
        if not is_block:
            self._log.debug(
                "stratum_share_accepted",
                extra={
                    "worker": submit_params.get("_worker") or submit_params.get("worker"),
                    "session_id": submit_params.get("_session_id"),
                    "job_id": job.job_id,
                    "source_job_id": job.source_job_id,
                    "template_id": job.template_id,
                    "nonce": nonce_int,
                    "share_hash": "0x" + candidate_hash.digest.hex(),
                    "share_hash_int": hex(digest_int),
                    "is_block": False,
                    "tx_count": 0,
                },
            )
            return True, None, False, 0

        local_prevalidation_ok = True
        stale_by_server = False
        head_hash: Optional[str] = None
        head_height: Optional[int] = None
        live_theta_micro: Optional[int] = None

        expires_at = job.expires_at
        if expires_at is None:
            expiry_raw = template.get("expiresAt") or template.get("expires_at")
            expires_at = _parse_float(expiry_raw, default=0.0) or None
        now = time.time()
        if expires_at is not None and now >= float(expires_at):
            local_prevalidation_ok = False
            stale_by_server = True
            head_hash, head_height, head_snapshot = await self._head_hash_height()
            live_theta_micro = _parse_int(
                head_snapshot.get("thetaMicro")
                or head_snapshot.get("thetaTargetMicro")
                or head_snapshot.get("theta_target_micro"),
                default=0,
            ) or None
            reason = "stale template (template_expired)"
            self._log.warning(
                "stratum_block_candidate_rejected",
                extra={
                    "job_id": job.job_id,
                    "source_job_id": job.source_job_id,
                    "template_id": job.template_id,
                    "reason": reason,
                    "template_height": template_height or None,
                    "template_parent_hash": template_parent_hash,
                    "current_head_hash": head_hash,
                    "current_head_height": head_height,
                    "current_head_theta_micro": live_theta_micro,
                },
            )
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason=reason,
                share_hash_hex="0x" + candidate_hash.digest.hex(),
                share_target_int=int(share_target_int),
                block_target_int=block_target,
                template_height=template_height or None,
                template_parent_hash=template_parent_hash,
                head_hash=head_hash,
                head_height=head_height,
                stale_by_server=stale_by_server,
                local_prevalidation_ok=local_prevalidation_ok,
                nonce=nonce_int,
                digest_int=digest_int,
                frozen_theta_micro=theta_micro,
                share_threshold_micro=share_threshold_micro,
                live_theta_micro=live_theta_micro,
                head_changed=bool(head_hash and template_parent_hash and template_parent_hash != head_hash),
            )
            return False, reason, True, tx_count

        head_hash, head_height, head_snapshot = await self._head_hash_height()
        live_theta_micro = _parse_int(
            head_snapshot.get("thetaMicro")
            or head_snapshot.get("thetaTargetMicro")
            or head_snapshot.get("theta_target_micro"),
            default=0,
        ) or None
        stale_reasons: list[str] = []
        # Mirror miner.submitBlock staleness predicates locally before the RPC call.
        # This keeps Stratum behavior aligned with `animica miner mine-blocks` and
        # prevents avoidable node-side "stale template" rejects under tip churn.
        if template_parent_hash and head_hash and template_parent_hash != head_hash:
            stale_reasons.append("new_head")
        if template_height > 0 and head_height is not None:
            expected = int(head_height) + 1
            if int(template_height) != expected:
                stale_reasons.append(
                    f"height_mismatch(template={template_height},head={head_height})"
                )
        if stale_reasons:
            local_prevalidation_ok = False
            stale_by_server = True
            reason = f"stale template ({';'.join(stale_reasons)})"
            self._log.warning(
                "stratum_block_candidate_rejected",
                extra={
                    "job_id": job.job_id,
                    "source_job_id": job.source_job_id,
                    "template_id": job.template_id,
                    "reason": reason,
                    "template_height": template_height or None,
                    "template_parent_hash": template_parent_hash,
                    "current_head_hash": head_hash,
                    "current_head_height": head_height,
                    "current_head_theta_micro": live_theta_micro,
                },
            )
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason=reason,
                share_hash_hex="0x" + candidate_hash.digest.hex(),
                share_target_int=int(share_target_int),
                block_target_int=block_target,
                template_height=template_height or None,
                template_parent_hash=template_parent_hash,
                head_hash=head_hash,
                head_height=head_height,
                stale_by_server=stale_by_server,
                local_prevalidation_ok=local_prevalidation_ok,
                nonce=nonce_int,
                digest_int=digest_int,
                frozen_theta_micro=theta_micro,
                share_threshold_micro=share_threshold_micro,
                live_theta_micro=live_theta_micro,
                head_changed=bool(head_hash and template_parent_hash and template_parent_hash != head_hash),
            )
            return False, reason, True, tx_count

        payload = build_submit_block_payload(template, nonce=nonce_int)
        if bool(
            template.get("disable_block_time_limits")
            or template.get("disableBlockTimeLimits")
        ):
            payload["disable_block_time_limits"] = True
        try:
            result: Json = await self._rpc_call("miner.submitBlock", payload)
        except RpcError as exc:
            error_reason = f"rpc:{exc.code}:{exc}"
            error_data = getattr(exc, "data", None)
            stale_reason = ""
            if isinstance(error_data, dict):
                if str(error_data.get("reason") or "") == "stale_template":
                    stale_reason = str(error_data.get("detail") or "stale_template")
            if not stale_reason and "stale template" in str(exc).lower():
                stale_reason = "stale_template"
            stale_by_server = bool(stale_reason)
            self._log.warning(
                "stratum_block_submit_result",
                extra={
                    "job_id": job.job_id,
                    "template_id": job.template_id,
                    "accepted": False,
                    "is_block": True,
                    "reason": error_reason,
                    "stale_template": stale_by_server,
                    "node_rejection": {"code": exc.code, "data": error_data},
                },
            )
            self._log.warning(
                "stratum_block_candidate_rejected",
                extra={
                    "job_id": job.job_id,
                    "template_id": job.template_id,
                    "reason": error_reason,
                    "template_height": template_height or None,
                },
            )
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason=error_reason,
                share_hash_hex="0x" + candidate_hash.digest.hex(),
                share_target_int=int(share_target_int),
                block_target_int=block_target,
                template_height=template_height or None,
                template_parent_hash=template_parent_hash,
                head_hash=head_hash,
                head_height=head_height,
                stale_by_server=stale_by_server,
                local_prevalidation_ok=local_prevalidation_ok,
                node_rejection={"code": exc.code, "data": error_data},
                nonce=nonce_int,
                digest_int=digest_int,
                frozen_theta_micro=theta_micro,
                share_threshold_micro=share_threshold_micro,
                live_theta_micro=live_theta_micro,
                head_changed=bool(head_hash and template_parent_hash and template_parent_hash != head_hash),
            )
            return False, error_reason, is_block, tx_count

        accepted = False
        updated_reason: Optional[str] = None
        is_duplicate = False
        if isinstance(result, dict):
            accepted = bool(result.get("accepted", False))
            is_duplicate = bool(result.get("duplicate", False))
            updated_reason = result.get("reason")
        elif isinstance(result, bool):
            accepted = result

        if is_block and is_duplicate:
            is_block = False

        if not accepted:
            updated_reason = updated_reason or "block rejected"
            if isinstance(result, dict):
                stale_by_server = str(result.get("reason") or "") == "stale_template"
            self._log.warning(
                "stratum_block_submit_result",
                extra={
                    "job_id": job.job_id,
                    "template_id": job.template_id,
                    "accepted": False,
                    "is_block": bool(is_block),
                    "reason": updated_reason,
                    "stale_template": stale_by_server,
                    "node_rejection": result if isinstance(result, dict) else {"result": result},
                },
            )
            self._log.warning(
                "stratum_block_candidate_rejected",
                extra={
                    "job_id": job.job_id,
                    "template_id": job.template_id,
                    "reason": updated_reason,
                    "template_height": template_height or None,
                },
            )
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason=updated_reason,
                share_hash_hex="0x" + candidate_hash.digest.hex(),
                share_target_int=int(share_target_int),
                block_target_int=block_target,
                template_height=template_height or None,
                template_parent_hash=template_parent_hash,
                head_hash=head_hash,
                head_height=head_height,
                stale_by_server=stale_by_server,
                local_prevalidation_ok=local_prevalidation_ok,
                node_rejection=result if isinstance(result, dict) else {"result": result},
                nonce=nonce_int,
                digest_int=digest_int,
                frozen_theta_micro=theta_micro,
                share_threshold_micro=share_threshold_micro,
                live_theta_micro=live_theta_micro,
                head_changed=bool(head_hash and template_parent_hash and template_parent_hash != head_hash),
            )
        else:
            self._log.info(
                "stratum_block_submit_result",
                extra={
                    "job_id": job.job_id,
                    "source_job_id": job.source_job_id,
                    "template_id": job.template_id,
                    "accepted": True,
                    "is_block": bool(is_block),
                    "reason": updated_reason,
                    "duplicate": is_duplicate,
                    "tx_count": tx_count,
                    "height": template_height or None,
                },
            )
            self._log.info(
                "stratum_block_candidate_accepted",
                extra={
                    "job_id": job.job_id,
                    "source_job_id": job.source_job_id,
                    "template_id": job.template_id,
                    "tx_count": tx_count,
                    "height": template_height or None,
                },
            )

        return accepted, updated_reason, is_block, tx_count

    def _encode_share_payload(self, job: MiningJob, params: Json) -> Json:
        hs = params.get("hashshare") or {}
        nonce: Any = None
        for candidate in (
            hs.get("nonce"),
            hs.get("n"),
            hs.get("nonce_hex"),
            hs.get("nonceHex"),
        ):
            if candidate is None or candidate == "":
                continue
            nonce = candidate
            break
        if nonce is None:
            raise ValueError("hashshare.nonce is required")
        proof = params.get("proof") or hs or {}
        submit_job_id = str(job.source_job_id or job.job_id)
        payload: Json = {
            "jobId": submit_job_id,
            "header": job.header,
            "nonce": nonce,
            "mixSeed": (job.hints or {}).get("mixSeed")
            or hs.get("mix")
            or hs.get("mixSeed"),
            "proof": proof,
            "height": job.height,
        }
        if "d_ratio" in params:
            payload["d_ratio"] = params["d_ratio"]
        return payload

    async def validate_and_submit_share(
        self, job: MiningJob, submit_params: Json
    ) -> Tuple[bool, Optional[str], bool, int]:
        if not _is_frozen_job(job):
            job = freeze_mining_job(job, fallback_chain_id=self._chain_id)
        if looks_like_block_template(job.raw):
            return await self._validate_and_submit_template_block(job, submit_params)

        stratum_job = StratumJob(
            job_id=job.job_id,
            header=job.header,
            share_target=job.share_target,
            theta_micro=job.issued_theta_micro or job.theta_micro,
            hints=job.hints,
            target=job.target,
            sign_bytes=job.sign_bytes,
            height=job.height,
            parent_hash=job.parent_hash,
            parent_height=job.parent_height,
            chain_id=job.chain_id,
            raw=job.raw,
        )
        ok, reason, is_block, tx_count = await self._validator.validate(
            stratum_job, submit_params
        )
        if not ok:
            head_hash, head_height, _ = await self._head_hash_height()
            self._log.warning(
                "stratum_share_rejected",
                extra={
                    "worker": submit_params.get("_worker") or submit_params.get("worker"),
                    "session_id": submit_params.get("_session_id"),
                    "job_id": job.job_id,
                    "source_job_id": job.source_job_id,
                    "address": submit_params.get("_address"),
                    "reason": reason,
                    "current_head_height": head_height,
                    "current_head_hash": head_hash,
                    "template_height": job.height,
                    "template_parent_hash": job.parent_hash,
                    "stale_by_server_state": False,
                    "local_prevalidation_ok": False,
                },
            )
            return ok, reason, is_block, tx_count

        payload = self._encode_share_payload(job, submit_params)
        self._log.debug(
            "stratum_submit_share_target_compare",
            extra={
                "job_id": job.job_id,
                "source_job_id": job.source_job_id,
                "template_id": job.template_id,
                "share_hash": None,
                "share_target": job.share_target,
                "share_ratio": submit_params.get("d_ratio") or job.share_target,
                "theta_micro": job.issued_theta_micro or job.theta_micro,
                "pass": True,
            },
        )
        try:
            result: Json = await self._rpc_call("miner.submitWork", payload)
        except RpcError as exc:
            head_hash, head_height, _ = await self._head_hash_height()
            self._log.warning(
                "stratum_share_submit_result",
                extra={
                    "job_id": job.job_id,
                    "template_id": job.template_id,
                    "accepted": False,
                    "is_block": bool(is_block),
                    "reason": f"rpc:{exc.code}:{exc}",
                    "stale_template": "stale" in str(exc).lower(),
                    "node_rejection": {"code": exc.code, "data": getattr(exc, "data", None)},
                },
            )
            self._log.warning(
                "stratum_share_rejected",
                extra={
                    "worker": submit_params.get("_worker") or submit_params.get("worker"),
                    "session_id": submit_params.get("_session_id"),
                    "job_id": job.job_id,
                    "source_job_id": job.source_job_id,
                    "address": submit_params.get("_address"),
                    "reason": f"rpc:{exc.code}:{exc}",
                    "current_head_height": head_height,
                    "current_head_hash": head_hash,
                    "template_height": job.height,
                    "template_parent_hash": job.parent_hash,
                    "stale_by_server_state": "stale" in str(exc).lower(),
                    "local_prevalidation_ok": True,
                    "node_rejection": {"code": exc.code, "data": getattr(exc, "data", None)},
                },
            )
            return False, f"rpc:{exc.code}:{exc}", is_block, tx_count

        accepted = False
        updated_reason: Optional[str] = None
        is_duplicate = False
        if isinstance(result, dict):
            accepted = bool(result.get("accepted", False))
            is_duplicate = bool(result.get("duplicate", False))
            updated_reason = result.get("reason") or reason
        elif isinstance(result, bool):
            accepted = result
            updated_reason = reason

        # If it's a block but it's a duplicate, don't count it as a block
        if is_block and is_duplicate:
            is_block = False

        if not accepted:
            self._log.warning(
                "stratum_share_submit_result",
                extra={
                    "job_id": job.job_id,
                    "template_id": job.template_id,
                    "accepted": False,
                    "is_block": bool(is_block),
                    "reason": updated_reason,
                    "stale_template": str(updated_reason or "").startswith("stale"),
                    "node_rejection": result if isinstance(result, dict) else {"result": result},
                },
            )
            head_hash, head_height, _ = await self._head_hash_height()
            self._log.warning(
                "stratum_share_rejected",
                extra={
                    "worker": submit_params.get("_worker") or submit_params.get("worker"),
                    "session_id": submit_params.get("_session_id"),
                    "job_id": job.job_id,
                    "source_job_id": job.source_job_id,
                    "address": submit_params.get("_address"),
                    "reason": updated_reason,
                    "current_head_height": head_height,
                    "current_head_hash": head_hash,
                    "template_height": job.height,
                    "template_parent_hash": job.parent_hash,
                    "stale_by_server_state": str(updated_reason or "").startswith("stale"),
                    "local_prevalidation_ok": True,
                    "node_rejection": result if isinstance(result, dict) else {"result": result},
                },
            )
        else:
            log_success = self._log.info if bool(is_block) else self._log.debug
            log_success(
                "stratum_share_submit_result",
                extra={
                    "job_id": job.job_id,
                    "template_id": job.template_id,
                    "accepted": True,
                    "is_block": bool(is_block),
                    "reason": updated_reason,
                    "duplicate": is_duplicate,
                    "tx_count": tx_count,
                },
            )
            log_success(
                "stratum_share_accepted",
                extra={
                    "worker": submit_params.get("_worker") or submit_params.get("worker"),
                    "session_id": submit_params.get("_session_id"),
                    "job_id": job.job_id,
                    "source_job_id": job.source_job_id,
                    "template_id": job.template_id,
                    "is_block": bool(is_block),
                    "tx_count": tx_count,
                },
            )

        return accepted, updated_reason, is_block, tx_count
