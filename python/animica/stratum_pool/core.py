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
- ``mining.share_submitter.ShareSubmitter`` wraps JSON-RPC calls to the node
  (``miner.submitShare`` / ``miner.submitBlock``) with retries and the
  ``_default_share_encoder`` that turns a FoundShare-like object into RPC
  payloads. It uses ``JsonRpcClient.call(method, params)`` synchronously over
  HTTP.

This module reuses those components directly: we build ``StratumJob`` objects
from templates delivered by the node's ``miner.getWork`` RPC, validate shares
with ``ShareValidator`` and forward accepted shares to the node using
``ShareSubmitter`` so PoW validation stays inside the existing mining code.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

from mining.share_submitter import JsonRpcClient, RpcError, ShareResult, ShareSubmitter, SubmitterConfig
from mining.stratum_server import ShareValidator, StratumJob


Json = Dict[str, Any]


@dataclass
class MiningJob:
    job_id: str
    header: Json
    theta_micro: int
    share_target: float
    height: int
    hints: Optional[Json] = None
    raw: Json = field(default_factory=dict)


class MiningCoreAdapter:
    def __init__(self, rpc_url: str, chain_id: int, pool_address: str, *, logger: Optional[logging.Logger] = None) -> None:
        self._rpc = JsonRpcClient(rpc_url)
        self._submitter = ShareSubmitter(SubmitterConfig(rpc_url=rpc_url), logger=logger)
        self._validator = ShareValidator()
        self._chain_id = chain_id
        self._pool_address = pool_address
        self._log = logger or logging.getLogger("animica.stratum_pool.core")

    async def _rpc_call(self, method: str, params: Any) -> Any:
        return await asyncio.to_thread(self._rpc.call, method, params)

    async def get_new_job(self) -> MiningJob:
        last_exc: Optional[Exception] = None
        work: Optional[Json] = None
        metadata = {"chainId": self._chain_id}
        params_variants = []
        if self._pool_address:
            params_variants.append([{**metadata, "address": self._pool_address}])
        params_variants.append([metadata])
        params_variants.append([])
        for method in ("miner.getWork", "mining.getWork", "getWork", "miner.requestWork"):
            for params in params_variants:
                try:
                    work = await self._rpc_call(method, params)
                    if work:
                        break
                except RpcError as exc:
                    last_exc = exc
                    if exc.code == -32601:  # Method not found; try next name
                        break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
            if work:
                break
        if work is None:
            raise RuntimeError(f"unable to fetch work: {last_exc}")

        header = work.get("header") or {}
        job_id = str(
            work.get("jobId")
            or work.get("job_id")
            or work.get("headerHash")
            or header.get("hash")
            or uuid.uuid4().hex
        )
        theta_micro = int(work.get("thetaMicro") or work.get("theta_target_micro") or work.get("thetaTargetMicro") or 0)
        share_target = float(work.get("shareTarget") or work.get("share_target") or work.get("share_target_fraction") or 0.0)
        height = int(work.get("height") or header.get("number") or header.get("height") or 0)
        hints = work.get("hints") or {}

        return MiningJob(
            job_id=job_id,
            header=header,
            theta_micro=theta_micro,
            share_target=share_target,
            height=height,
            hints=hints,
            raw=work,
        )

    def _encode_share_payload(self, job: MiningJob, params: Json) -> Json:
        hs = params.get("hashshare") or {}
        nonce = hs.get("nonce") or hs.get("n") or hs.get("nonce_hex") or hs.get("nonceHex")
        if nonce is None:
            raise ValueError("hashshare.nonce is required")
        proof = params.get("proof") or hs or {}
        payload: Json = {
            "header": job.header,
            "nonce": nonce,
            "mixSeed": (job.hints or {}).get("mixSeed") or hs.get("mix") or hs.get("mixSeed"),
            "proof": proof,
            "height": job.height,
        }
        if "d_ratio" in params:
            payload["d_ratio"] = params["d_ratio"]
        return payload

    async def validate_and_submit_share(
        self, job: MiningJob, submit_params: Json
    ) -> Tuple[bool, Optional[str], bool, int]:
        stratum_job = StratumJob(
            job_id=job.job_id,
            header=job.header,
            share_target=job.share_target,
            theta_micro=job.theta_micro,
            hints=job.hints,
        )
        ok, reason, is_block, tx_count = await self._validator.validate(stratum_job, submit_params)
        if not ok:
            return ok, reason, is_block, tx_count

        payload = self._encode_share_payload(job, submit_params)
        result: ShareResult = await asyncio.to_thread(self._submitter.submit_share_once, payload)
        accepted = bool(result.get("accepted", False))
        updated_reason = result.get("reason") or reason
        return accepted, updated_reason, is_block, tx_count
