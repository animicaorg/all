"""AICF protocol client.

Submits inference jobs to the AICF protocol via the Animica JSON-RPC API,
polls for completion, streams tokens back to the caller, and reports
settlement to the wallet layer.

The RPC method names mirror what's exposed by aicf/protocol/rpc.py in the
monorepo. We do not import that module directly — the client speaks pure
JSON-RPC over httpx, so it works both against an in-process Animica node
and against a remote endpoint.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Mapping, Optional

import httpx

from agent_runtime.errors import AICFError, ProviderUnavailable


# --------------------------------------------------------------------------- #
# Data types                                                                  #
# --------------------------------------------------------------------------- #

@dataclass
class JobSpec:
    """Inputs to aicf.protocol.submitInferenceJob."""

    prompt: str
    tier_preferred: Optional[str] = None     # tiny | small | flagship | large
    job_kind: str = "AI"                     # protocol kind label
    max_output_tokens: int = 1024
    temperature: float = 0.2
    top_p: float = 0.95
    stop: list[str] = field(default_factory=list)
    # Extra metadata persisted with the job (for analytics / fraud detection).
    metadata: dict[str, Any] = field(default_factory=dict)
    # Pipeline routing knobs. None means "use the node's default" so old
    # nodes that don't know these fields keep working (the AICFClient
    # omits them from the wire payload when None). When set:
    #   - mode: "auto" lets the node decide pipeline vs race based on
    #     registered workers; "pipeline" forces a pipeline job;
    #     "race" forces the legacy single-worker race path.
    #   - stages: number of pipeline stages to fan out to (default 2
    #     server-side when mode is pipeline).
    #   - decode_mode: "streaming" runs the per-token loop where each
    #     decoded token traverses every stage with KV cache state
    #     preserved (animica >= 0.1.67). "prefill_only" runs the legacy
    #     prefill-parallel path where the final stage decodes locally.
    mode: Optional[str] = None
    stages: Optional[int] = None
    decode_mode: Optional[str] = None


@dataclass
class JobQuote:
    """Result of aicf.estimateJobCost."""

    estimated_cost_animica: float
    estimated_latency_ms: int
    routing_tier: str
    candidate_providers: int
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class JobSubmitResult:
    job_id: str
    accepted_tier: str
    expected_provider_id: str
    raw: dict[str, Any] = field(default_factory=dict)
    # What the node actually picked after auto-routing. ``mode`` will
    # be "race" (winner-takes-all replication) or "pipeline" (N-stage
    # chained inference). ``stages`` is non-zero only for pipeline.
    mode: str = ""
    stages: int = 0


@dataclass
class JobChunk:
    """Streaming chunk from aicf.streamJob."""

    text: str
    is_final: bool = False
    token_count: int = 0
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class JobResult:
    """Final settlement record from aicf.settleJob."""

    job_id: str
    text: str
    actual_cost_animica: float
    provider_id: str
    settled: bool
    latency_ms: int
    raw: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Client                                                                      #
# --------------------------------------------------------------------------- #

class AICFClient:
    """Thin JSON-RPC client for the AICF protocol surface."""

    def __init__(self, endpoint: str, *, timeout_sec: float = 300.0,
                 poll_interval_ms: int = 250, max_retries: int = 3,
                 retry_backoff_ms: Optional[list[int]] = None) -> None:
        if not endpoint:
            raise AICFError("ENDPOINT_REQUIRED",
                            "AICF endpoint URL must be non-empty")
        self.endpoint = endpoint.rstrip("/")
        self.timeout_sec = timeout_sec
        self.poll_interval_ms = poll_interval_ms
        self.max_retries = max_retries
        self.retry_backoff_ms = retry_backoff_ms or [500, 1500, 3500]
        self._http = httpx.Client(timeout=timeout_sec)
        self._req_id = 0

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "AICFClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # -------------------- internal -------------------- #

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _rpc(self, method: str, params: Any) -> Any:
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": params,
        }
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self._http.post(self.endpoint, json=body)
                r.raise_for_status()
                data = r.json()
                if "error" in data and data["error"] is not None:
                    err = data["error"]
                    code = str(err.get("code", "AICF_ERR"))
                    msg = str(err.get("message", ""))
                    raise AICFError(code, msg, detail={"data": err.get("data")})
                return data.get("result")
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                delay_ms = self.retry_backoff_ms[
                    min(attempt, len(self.retry_backoff_ms) - 1)
                ]
                time.sleep(delay_ms / 1000.0)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                # 5xx — the node was momentarily busy or a job hit a stale
                # worker. Retry with backoff instead of surfacing a raw 503 to
                # the user. 4xx are client errors — raise immediately.
                if status >= 500 and attempt < self.max_retries:
                    last_exc = exc
                    delay_ms = self.retry_backoff_ms[
                        min(attempt, len(self.retry_backoff_ms) - 1)
                    ]
                    time.sleep(delay_ms / 1000.0)
                    continue
                raise AICFError(
                    "HTTP_ERROR",
                    f"AICF endpoint returned {status}",
                    detail={"body": exc.response.text[:1024]},
                ) from exc
        raise ProviderUnavailable(
            "distributed-aicf",
            f"endpoint {self.endpoint!r} not reachable: {last_exc}",
        )

    # -------------------- public API -------------------- #

    def ping(self) -> bool:
        """Quick reachability check. Returns False on any transport error.

        Uses chain.getHead as the liveness probe — it's served by every
        running node regardless of AICF init state. The old probe
        (aicf.protocol.getStatus) was never registered, and aicf.getStatus
        requires AICF state to be initialized which fresh nodes don't have.
        Both produced false-negative "endpoint unreachable" verdicts.
        """
        try:
            self._rpc("chain.getHead", {})
            return True
        except (AICFError, ProviderUnavailable):
            return False

    def estimate_cost(self, spec: JobSpec) -> JobQuote:
        raw = self._rpc("aicf.estimateJobCost", {
            "prompt_tokens": _approximate_token_count(spec.prompt),
            "max_output_tokens": spec.max_output_tokens,
            "tier_preferred": spec.tier_preferred,
            "job_kind": spec.job_kind,
        })
        if not isinstance(raw, dict):
            raise AICFError("BAD_RESPONSE",
                            "aicf.estimateJobCost returned non-object")
        return JobQuote(
            estimated_cost_animica=float(raw.get("cost_animica", 0.0)),
            estimated_latency_ms=int(raw.get("latency_ms", 0)),
            routing_tier=str(raw.get("tier", spec.tier_preferred or "")),
            candidate_providers=int(raw.get("providers", 0)),
            raw=raw,
        )

    def submit(self, spec: JobSpec, *, signed_payment: Mapping[str, Any]
               ) -> JobSubmitResult:
        """Submit an inference job paired with a signed payment txn.

        ``signed_payment`` is the output of wallet.sign_payment(); it carries
        the txn hex + meta the AICF protocol verifies before accepting.
        """
        spec_payload: dict[str, Any] = {
            "prompt": spec.prompt,
            "tier_preferred": spec.tier_preferred,
            "job_kind": spec.job_kind,
            "max_output_tokens": spec.max_output_tokens,
            "temperature": spec.temperature,
            "top_p": spec.top_p,
            "stop": spec.stop,
            "metadata": spec.metadata,
        }
        # Only forward pipeline knobs when explicitly set so older nodes
        # that don't recognise the keys aren't sent fields they ignore
        # (defensive — modern nodes drop unknown keys silently, but the
        # wire shape matches what the server expects either way).
        if spec.mode is not None:
            spec_payload["mode"] = spec.mode
        if spec.stages is not None:
            spec_payload["stages"] = int(spec.stages)
        if spec.decode_mode is not None:
            spec_payload["decode_mode"] = spec.decode_mode
        raw = self._rpc("aicf.submitInferenceJob", {
            "spec": spec_payload,
            "payment": dict(signed_payment),
        })
        if not isinstance(raw, dict) or "job_id" not in raw:
            raise AICFError("BAD_RESPONSE",
                            "aicf.submitInferenceJob missing job_id")
        return JobSubmitResult(
            job_id=str(raw["job_id"]),
            accepted_tier=str(raw.get("accepted_tier", "")),
            expected_provider_id=str(raw.get("provider_id", "")),
            raw=raw,
            mode=str(raw.get("mode", "")),
            stages=int(raw.get("stages") or 0),
        )

    def stream(self, job_id: str,
               on_chunk: Optional[Callable[[JobChunk], None]] = None
               ) -> Iterator[JobChunk]:
        """Yield JobChunks as the provider produces output.

        Uses long-polling against ``aicf.streamJob`` — each call returns the
        next chunk (or sentinel on completion). Falls back to polling
        ``aicf.jobStatus`` when the streaming method is unavailable.
        """
        cursor = 0
        deadline = time.time() + self.timeout_sec
        while time.time() < deadline:
            try:
                raw = self._rpc("aicf.streamJob", {
                    "job_id": job_id,
                    "cursor": cursor,
                })
            except AICFError as err:
                if err.code in {"METHOD_NOT_FOUND", "NOT_IMPLEMENTED"}:
                    yield from self._poll_fallback(job_id, on_chunk=on_chunk)
                    return
                raise
            if not isinstance(raw, dict):
                raise AICFError("BAD_RESPONSE",
                                "aicf.streamJob returned non-object")
            text = str(raw.get("text", ""))
            is_final = bool(raw.get("final", False))
            cursor = int(raw.get("next_cursor", cursor + len(text)))
            chunk = JobChunk(text=text, is_final=is_final,
                             token_count=int(raw.get("token_count", 0)),
                             raw=raw)
            if on_chunk is not None:
                on_chunk(chunk)
            if text or is_final:
                yield chunk
            if is_final:
                return
            time.sleep(self.poll_interval_ms / 1000.0)
        raise AICFError("TIMEOUT",
                        f"job {job_id} did not complete in {self.timeout_sec}s")

    def _poll_fallback(self, job_id: str, *,
                       on_chunk: Optional[Callable[[JobChunk], None]]
                       ) -> Iterator[JobChunk]:
        deadline = time.time() + self.timeout_sec
        last_len = 0
        while time.time() < deadline:
            raw = self._rpc("aicf.jobStatus", {"job_id": job_id})
            if not isinstance(raw, dict):
                raise AICFError("BAD_RESPONSE",
                                "aicf.jobStatus returned non-object")
            text = str(raw.get("text", ""))
            state = str(raw.get("state", "running"))
            if len(text) > last_len:
                new_text = text[last_len:]
                last_len = len(text)
                chunk = JobChunk(text=new_text, is_final=False)
                if on_chunk:
                    on_chunk(chunk)
                yield chunk
            if state in {"completed", "failed"}:
                final = JobChunk(text="", is_final=True,
                                 raw={"final_state": state})
                if on_chunk:
                    on_chunk(final)
                yield final
                return
            time.sleep(self.poll_interval_ms / 1000.0)
        raise AICFError("TIMEOUT", f"job {job_id} poll timeout")

    def settle(self, job_id: str) -> JobResult:
        raw = self._rpc("aicf.settleJob", {"job_id": job_id})
        if not isinstance(raw, dict):
            raise AICFError("BAD_RESPONSE",
                            "aicf.settleJob returned non-object")
        return JobResult(
            job_id=job_id,
            text=str(raw.get("text", "")),
            actual_cost_animica=float(raw.get("cost_animica", 0.0)),
            provider_id=str(raw.get("provider_id", "")),
            settled=bool(raw.get("settled", False)),
            latency_ms=int(raw.get("latency_ms", 0)),
            raw=raw,
        )

    # -------------------- worker side -------------------- #

    def register_worker(self, *, address: str, tiers: list[str],
                        hardware: Mapping[str, Any],
                        direct_endpoint: str = "") -> dict[str, Any]:
        params: dict[str, Any] = {
            "address": address,
            "tiers": tiers,
            "hardware": dict(hardware),
        }
        if direct_endpoint:
            params["direct_endpoint"] = direct_endpoint
        return self._rpc("aicf.workerRegister", params)

    def worker_status(self, address: str) -> dict[str, Any]:
        return self._rpc("aicf.workerStatus", {"address": address})

    def claim_next_job(self, *, address: str,
                       tiers: list[str]) -> Optional[dict[str, Any]]:
        raw = self._rpc("aicf.workerClaimNextJob", {
            "address": address,
            "tiers": tiers,
        })
        if not raw:
            return None
        return raw if isinstance(raw, dict) else None

    def submit_worker_result(self, *, address: str, job_id: str,
                             text: str, latency_ms: int,
                             attestation: Mapping[str, Any]) -> dict[str, Any]:
        return self._rpc("aicf.workerSubmitResult", {
            "address": address,
            "job_id": job_id,
            "text": text,
            "latency_ms": latency_ms,
            "attestation": dict(attestation),
        })

    # -------------------- pipeline -------------------- #

    def pipeline_claim_stage(self, *, address: str,
                             job_id: Optional[str] = None
                             ) -> Optional[dict[str, Any]]:
        """Claim the next available pipeline stage for this worker.

        Returns the stage descriptor (job_id, stage_index, prompt,
        upstream_stage, is_final_stage, lease_expires_at) or ``None`` if
        nothing is waiting to be picked up.
        """
        params: dict[str, Any] = {"address": address}
        if job_id:
            params["job_id"] = job_id
        raw = self._rpc("aicf.pipelineClaimStage", params)
        if not raw:
            return None
        return raw if isinstance(raw, dict) else None

    def pipeline_get_upstream_activation(self, *, job_id: str,
                                         stage_index: int) -> dict[str, Any]:
        return self._rpc("aicf.pipelineGetUpstreamActivation", {
            "job_id": job_id,
            "stage_index": int(stage_index),
        })

    def pipeline_submit_stage_result(
        self, *, address: str, job_id: str, stage_index: int,
        output_b64: Optional[str], output_text: Optional[str],
        meta: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._rpc("aicf.pipelineSubmitStageResult", {
            "address": address,
            "job_id": job_id,
            "stage_index": int(stage_index),
            "output_b64": output_b64,
            "output_text": output_text,
            "meta": dict(meta),
        })

    def pipeline_job_status(self, job_id: str) -> dict[str, Any]:
        return self._rpc("aicf.pipelineJobStatus", {"job_id": job_id})

    def pipeline_get_stage_info(self, *, job_id: str,
                                stage_index: int) -> dict[str, Any]:
        return self._rpc("aicf.pipelineGetStageInfo", {
            "job_id": job_id,
            "stage_index": int(stage_index),
        })

    # -------------------- streaming-decode helpers -------------------- #

    def pipeline_feed_token(self, *, job_id: str,
                            token_id: int) -> dict[str, Any]:
        return self._rpc("aicf.pipelineFeedToken", {
            "job_id": job_id,
            "token_id": int(token_id),
        })

    def pipeline_get_next_token_step(self, *, job_id: str
                                     ) -> dict[str, Any]:
        return self._rpc("aicf.pipelineGetNextTokenStep", {"job_id": job_id})

    def pipeline_submit_decode_step(
        self, *, address: str, job_id: str, stage_index: int,
        step_index: int, output_b64: Optional[str],
        output_text: Optional[str], meta: Mapping[str, Any],
    ) -> dict[str, Any]:
        return self._rpc("aicf.pipelineSubmitDecodeStep", {
            "address": address,
            "job_id": job_id,
            "stage_index": int(stage_index),
            "step_index": int(step_index),
            "output_b64": output_b64,
            "output_text": output_text,
            "meta": dict(meta),
        })

    def pipeline_get_upstream_step(
        self, *, job_id: str, stage_index: int, step_index: int,
    ) -> dict[str, Any]:
        return self._rpc("aicf.pipelineGetUpstreamStep", {
            "job_id": job_id,
            "stage_index": int(stage_index),
            "step_index": int(step_index),
        })


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _approximate_token_count(text: str) -> int:
    """Cheap token-count estimate. ~4 chars/token average for BPE."""
    if not text:
        return 0
    return max(1, len(text) // 4)
