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
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, Optional, Tuple

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
    nonce = (
        hashshare.get("nonce")
        or hashshare.get("n")
        or params.get("nonce")
        or params.get("nonce64")
        or params.get("n")
    )
    if nonce is None:
        raise ValueError("hashshare.nonce is required")
    return int_from_value(nonce, default=-1)


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
    raw: Json = field(default_factory=dict)


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

    async def get_head_snapshot(self) -> Json:
        last_exc: Optional[Exception] = None
        for method in ("chain.getHead", "chain_getHead"):
            try:
                result = await self._rpc_call(method, [])
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
        return (
            {"address": address, "include_mempool": True},
            {"payout_address": address, "include_mempool": True},
            [address],
        )

    async def get_new_job(self) -> MiningJob:
        last_exc: Optional[Exception] = None
        work: Optional[Json] = None

        if self._pool_address:
            for template_params in self._block_template_param_variants(self._pool_address):
                try:
                    template = await self._rpc_call(
                        "miner.getBlockTemplate",
                        template_params,
                    )
                    if isinstance(template, dict):
                        if template.get("enabled") is False:
                            reason = str(template.get("reason") or "disabled")
                            raise TemplateUnavailable(
                                reason,
                                wait_seconds=_parse_float(
                                    template.get("waitSeconds"), default=0.0
                                ),
                                head=(
                                    template.get("head")
                                    if isinstance(template.get("head"), dict)
                                    else {}
                                ),
                            )
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
        share_target = _parse_float(
            work.get("shareTarget")
            or work.get("share_target")
            or work.get("share_target_fraction")
            or 0.0
        )
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
        mempool_fingerprint = (
            _template_mempool_fingerprint(work, header)
            if looks_like_block_template(work)
            else None
        )

        return MiningJob(
            job_id=job_id,
            header=header,
            theta_micro=theta_micro,
            share_target=share_target,
            height=height,
            target=target,
            sign_bytes=sign_bytes,
            hints=hints,
            template_id=str(template_id) if template_id is not None else None,
            parent_hash=job_parent_hash,
            issued_at=issued_at if issued_at > 0 else None,
            expires_at=expires_at if expires_at > 0 else None,
            head_hash_at_issue=_normalize_hex(work.get("headHashAtIssue")),
            mempool_fingerprint=mempool_fingerprint,
            raw=work,
        )

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
    ) -> None:
        self._log.warning(
            "stratum_share_rejected",
            extra={
                "worker": submit_params.get("_worker") or submit_params.get("worker"),
                "session_id": submit_params.get("_session_id"),
                "job_id": job.job_id,
                "address": submit_params.get("_address"),
                "share_hash": share_hash_hex,
                "share_target": hex(int(share_target_int))
                if share_target_int > 0
                else "0x0",
                "block_target": hex(int(block_target_int))
                if block_target_int > 0
                else "0x0",
                "current_head_height": head_height,
                "current_head_hash": head_hash,
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
        template = job.raw if isinstance(job.raw, dict) else {}
        header_view = template.get("header") or job.header
        if not isinstance(header_view, dict):
            head_hash, head_height, _ = await self._head_hash_height()
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason="missing header template",
                share_hash_hex=None,
                share_target_int=0,
                block_target_int=0,
                template_height=None,
                template_parent_hash=None,
                head_hash=head_hash,
                head_height=head_height,
                stale_by_server=True,
                local_prevalidation_ok=False,
            )
            return False, "missing header template", False, 0

        nonce_int = _extract_submit_nonce(submit_params)
        if nonce_int < 0:
            head_hash, head_height, _ = await self._head_hash_height()
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason="invalid nonce",
                share_hash_hex=None,
                share_target_int=0,
                block_target_int=0,
                template_height=_parse_int(
                    header_view.get("height") or header_view.get("number"), default=0
                )
                or None,
                template_parent_hash=_template_parent_hash(template, header_view),
                head_hash=head_hash,
                head_height=head_height,
                stale_by_server=False,
                local_prevalidation_ok=False,
            )
            return False, "invalid nonce", False, 0

        try:
            candidate_hash = hash_candidate_header(header_view, nonce=nonce_int)
        except Exception as exc:  # noqa: BLE001
            head_hash, head_height, _ = await self._head_hash_height()
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason=f"invalid header template: {exc}",
                share_hash_hex=None,
                share_target_int=0,
                block_target_int=0,
                template_height=_parse_int(
                    header_view.get("height") or header_view.get("number"), default=0
                )
                or None,
                template_parent_hash=_template_parent_hash(template, header_view),
                head_hash=head_hash,
                head_height=head_height,
                stale_by_server=False,
                local_prevalidation_ok=False,
            )
            return False, f"invalid header template: {exc}", False, 0

        digest_int = candidate_hash.digest_int
        template_parent_hash = _template_parent_hash(template, header_view)
        template_height = _parse_int(
            header_view.get("height") or header_view.get("number"), default=0
        )
        self._log.info(
            "stratum_submit_job_matched",
            extra={
                "worker": submit_params.get("_worker") or submit_params.get("worker"),
                "session_id": submit_params.get("_session_id"),
                "job_id": job.job_id,
                "template_id": job.template_id,
                "template_height": template_height or None,
                "template_parent_hash": template_parent_hash,
                "template_issued_at": job.issued_at,
                "template_expires_at": job.expires_at,
                "share_hash": "0x" + candidate_hash.digest.hex(),
            },
        )
        theta_micro = _parse_int(
            template.get("thetaMicro")
            or header_view.get("thetaMicro")
            or header_view.get("thetaTargetMicro")
            or header_view.get("theta_target_micro")
            or job.theta_micro
            or 0
        )
        if theta_micro <= 0:
            head_hash, head_height, _ = await self._head_hash_height()
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason="missing thetaMicro",
                share_hash_hex="0x" + candidate_hash.digest.hex(),
                share_target_int=0,
                block_target_int=0,
                template_height=_parse_int(
                    header_view.get("height") or header_view.get("number"), default=0
                )
                or None,
                template_parent_hash=_template_parent_hash(template, header_view),
                head_hash=head_hash,
                head_height=head_height,
                stale_by_server=False,
                local_prevalidation_ok=False,
            )
            return False, "missing thetaMicro", False, 0

        share_ratio = float(job.share_target or 0.0)
        if share_ratio <= 0.0:
            share_ratio = 1.0
        share_target_int = 0
        try:
            from core.utils.pow import micro_threshold_to_target256

            share_target_int = micro_threshold_to_target256(
                max(1, int(theta_micro * share_ratio))
            )
        except Exception:
            share_target_int = 0

        share_ok = bool(share_target_int == 0 or digest_int <= int(share_target_int))
        block_target_preview = int_from_value(job.target or template.get("target"))
        self._log.info(
            "stratum_submit_share_target_compare",
            extra={
                "job_id": job.job_id,
                "template_id": job.template_id,
                "share_hash": "0x" + candidate_hash.digest.hex(),
                "share_target": hex(int(share_target_int))
                if share_target_int > 0
                else "0x0",
                "share_ratio": share_ratio,
                "theta_micro": theta_micro,
                "pass": share_ok,
            },
        )

        if not share_ok:
            head_hash, head_height, _ = await self._head_hash_height()
            self._log_share_reject(
                submit_params=submit_params,
                job=job,
                reason="low difficulty share",
                share_hash_hex="0x" + candidate_hash.digest.hex(),
                share_target_int=int(share_target_int),
                block_target_int=block_target_preview,
                template_height=_parse_int(
                    header_view.get("height") or header_view.get("number"), default=0
                )
                or None,
                template_parent_hash=_template_parent_hash(template, header_view),
                head_hash=head_hash,
                head_height=head_height,
                stale_by_server=False,
                local_prevalidation_ok=True,
            )
            return False, "low difficulty share", False, 0

        block_target = block_target_preview
        is_block = block_target > 0 and digest_int <= block_target
        self._log.info(
            "stratum_submit_block_target_compare",
            extra={
                "job_id": job.job_id,
                "template_id": job.template_id,
                "share_hash": "0x" + candidate_hash.digest.hex(),
                "block_target": hex(int(block_target)) if block_target > 0 else "0x0",
                "pass": is_block,
            },
        )
        tx_count = template_tx_count(template)
        if not is_block:
            self._log.info(
                "stratum_share_accepted",
                extra={
                    "worker": submit_params.get("_worker") or submit_params.get("worker"),
                    "session_id": submit_params.get("_session_id"),
                    "job_id": job.job_id,
                    "template_id": job.template_id,
                    "is_block": False,
                    "tx_count": 0,
                },
            )
            return True, None, False, 0

        local_prevalidation_ok = True
        stale_by_server = False
        head_hash: Optional[str] = None
        head_height: Optional[int] = None

        expires_at = job.expires_at
        if expires_at is None:
            expiry_raw = template.get("expiresAt") or template.get("expires_at")
            expires_at = _parse_float(expiry_raw, default=0.0) or None
        now = time.time()
        if expires_at is not None and now >= float(expires_at):
            local_prevalidation_ok = False
            stale_by_server = True
            head_hash, head_height, _ = await self._head_hash_height()
            reason = "stale template (template_expired)"
            self._log.warning(
                "stratum_block_candidate_rejected",
                extra={
                    "job_id": job.job_id,
                    "template_id": job.template_id,
                    "reason": reason,
                    "template_height": template_height or None,
                    "template_parent_hash": template_parent_hash,
                    "current_head_hash": head_hash,
                    "current_head_height": head_height,
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
            )
            return False, reason, True, tx_count

        head_hash, head_height, _ = await self._head_hash_height()
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
                    "template_id": job.template_id,
                    "reason": reason,
                    "template_height": template_height or None,
                    "template_parent_hash": template_parent_hash,
                    "current_head_hash": head_hash,
                    "current_head_height": head_height,
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
            )
            return False, reason, True, tx_count

        payload = build_submit_block_payload(template, nonce=nonce_int)
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
            )
        else:
            self._log.info(
                "stratum_block_submit_result",
                extra={
                    "job_id": job.job_id,
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
                    "template_id": job.template_id,
                    "tx_count": tx_count,
                    "height": template_height or None,
                },
            )

        return accepted, updated_reason, is_block, tx_count

    def _encode_share_payload(self, job: MiningJob, params: Json) -> Json:
        hs = params.get("hashshare") or {}
        nonce = (
            hs.get("nonce") or hs.get("n") or hs.get("nonce_hex") or hs.get("nonceHex")
        )
        if nonce is None:
            raise ValueError("hashshare.nonce is required")
        proof = params.get("proof") or hs or {}
        payload: Json = {
            "jobId": job.job_id,
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
        if looks_like_block_template(job.raw):
            return await self._validate_and_submit_template_block(job, submit_params)

        stratum_job = StratumJob(
            job_id=job.job_id,
            header=job.header,
            share_target=job.share_target,
            theta_micro=job.theta_micro,
            hints=job.hints,
            target=job.target,
            sign_bytes=job.sign_bytes,
            height=job.height,
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
        self._log.info(
            "stratum_submit_share_target_compare",
            extra={
                "job_id": job.job_id,
                "template_id": job.template_id,
                "share_hash": None,
                "share_target": job.share_target,
                "share_ratio": submit_params.get("d_ratio") or job.share_target,
                "theta_micro": job.theta_micro,
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
            self._log.info(
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
            self._log.info(
                "stratum_share_accepted",
                extra={
                    "worker": submit_params.get("_worker") or submit_params.get("worker"),
                    "session_id": submit_params.get("_session_id"),
                    "job_id": job.job_id,
                    "template_id": job.template_id,
                    "is_block": bool(is_block),
                    "tx_count": tx_count,
                },
            )

        return accepted, updated_reason, is_block, tx_count
