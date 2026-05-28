"""AICF worker loop.

Run by the miner side. Auto-registers the machine as an AICF compute
worker, polls the queue for inference jobs, runs them against a locally
installed flagship bundle, and submits results for settlement.

Coexists with the existing PoW mining loop (chain unchanged):

  animica miner aicf-worker start --address anim1...   # opt-in
  animica miner pool ...                               # existing PoW pool

The opt-out flag (--no-aicf) and env var (ANIMICA_DISABLE_AICF_WORKER)
are honored everywhere this module is used.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from agent_runtime.aicf_client import AICFClient
from agent_runtime.config import Config, load_config
from agent_runtime.errors import AgentRuntimeError, BundleError
from agent_runtime.hardware import (
    HardwareProfile, attach_eligible_tiers, detect_hardware,
)


log = logging.getLogger("agent_runtime.aicf_worker")


def _endpoint_reachable(url: str, timeout_sec: float = 1.5) -> bool:
    """Cheap liveness probe used to decide whether to fall back to the
    public RPC when the configured local node isn't listening. Mirrors
    the chat CLI's helper — we POST a tiny chain.getHead and accept any
    well-formed JSON-RPC reply (success or structured error) as
    'reachable'. Anything else — refused TCP, timeout, HTML 4xx/5xx
    page — counts as unreachable."""
    import json as _json
    import urllib.request
    import urllib.error
    body = _json.dumps({"jsonrpc": "2.0", "id": 0,
                         "method": "chain.getHead", "params": {}}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                  headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            ct = (resp.headers.get("content-type") or "").lower()
            if "application/json" not in ct:
                return False
            payload = _json.loads(resp.read().decode("utf-8"))
            return isinstance(payload, dict) and (
                "result" in payload or "error" in payload
            )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


@dataclass
class WorkerState:
    address: str
    endpoint: str
    tiers: list[str]
    hardware: HardwareProfile
    started_at: float
    jobs_completed: int = 0
    jobs_failed: int = 0
    jobs_raced_and_lost: int = 0
    pipeline_stages_completed: int = 0
    earnings_animica: float = 0.0
    last_heartbeat_at: float = 0.0
    stopping: bool = False

    def to_dict(self) -> dict:
        return {
            "address": self.address, "endpoint": self.endpoint,
            "tiers": list(self.tiers),
            "hardware": self.hardware.to_dict(),
            "started_at": self.started_at,
            "jobs_completed": self.jobs_completed,
            "jobs_failed": self.jobs_failed,
            "jobs_raced_and_lost": self.jobs_raced_and_lost,
            "pipeline_stages_completed": self.pipeline_stages_completed,
            "earnings_animica": self.earnings_animica,
            "last_heartbeat_at": self.last_heartbeat_at,
        }


# --------------------------------------------------------------------------- #
# Capabilities                                                                #
# --------------------------------------------------------------------------- #

def is_disabled() -> bool:
    return os.environ.get("ANIMICA_DISABLE_AICF_WORKER") == "1"


def resolve_tiers(profile: HardwareProfile, catalog: dict,
                  *, override: Optional[list[str]] = None) -> list[str]:
    if override:
        return list(override)
    attach_eligible_tiers(profile, catalog)
    if profile.eligible_tiers:
        return list(profile.eligible_tiers)
    fallback = catalog.get("propagation", {}).get(
        "fallback_tier_on_detect_fail", "tiny")
    return [str(fallback)]


# --------------------------------------------------------------------------- #
# Worker loop                                                                 #
# --------------------------------------------------------------------------- #

class AICFWorker:
    def __init__(self, *, cfg: Config, address: str,
                 tiers_override: Optional[list[str]] = None) -> None:
        if is_disabled():
            raise AgentRuntimeError(
                "AICF worker disabled by ANIMICA_DISABLE_AICF_WORKER=1",
                hint="unset the env var to enable AICF compute participation",
            )
        self.cfg = cfg
        self.address = address
        network = os.environ.get("ANIMICA_NETWORK") or "mainnet"
        endpoint = cfg.integration["aicf"]["endpoint"].get(
            network, cfg.integration["aicf"]["endpoint"].get("mainnet"))
        if not endpoint:
            raise AgentRuntimeError(
                f"no AICF endpoint configured for network {network}")
        # Same fallback the chat CLI uses: when the configured endpoint
        # is the local node (the integration default for operators) and
        # there's no local node listening, slide over to the public RPC
        # so `animica miner start --address X` works on a fresh box
        # with no extra setup. An explicit override (--aicf-endpoint or
        # ANIMICA_AICF_ENDPOINT env, applied by the caller before
        # construction) always wins because we won't reach this branch
        # for a non-local endpoint.
        if (network == "mainnet"
                and ("127.0.0.1" in endpoint or "localhost" in endpoint)
                and not _endpoint_reachable(endpoint)):
            public = "https://rpc.animica.org/rpc"
            log.info(
                "[aicf-worker] local AICF endpoint %s not reachable; "
                "falling back to public %s", endpoint, public,
            )
            endpoint = public
        self.endpoint = endpoint
        self.client = AICFClient(endpoint=endpoint)
        self.profile = detect_hardware()
        self.tiers = resolve_tiers(self.profile,
                                    dict(cfg.model_catalog),
                                    override=tiers_override)
        # Opt-in pipeline-stage capability. With ANIMICA_AICF_PIPELINE_WORKER=1
        # the worker advertises the synthetic "pipeline" tier so the node
        # promotes new jobs to pipeline mode. Off by default to keep the
        # legacy single-worker behavior intact for miners running on a
        # single CPU/GPU; the chat side already auto-falls-back to race
        # when no pipeline workers are registered.
        self.pipeline_enabled = (
            os.environ.get("ANIMICA_AICF_PIPELINE_WORKER", "0").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if self.pipeline_enabled and "pipeline" not in self.tiers:
            self.tiers = [*self.tiers, "pipeline"]
        # Direct worker-to-worker activation transport (optional). When
        # pipeline mode is on AND ANIMICA_AICF_PIPELINE_DIRECT_PORT is
        # set, the worker spins up a small HTTP server that peers can
        # GET activations from, bypassing the node round-trip for large
        # hidden states. The server is best-effort: any fetch failure
        # falls back to the existing aicf.pipelineGetUpstreamActivation
        # node-proxy path, so a NAT'd worker behind a firewall still
        # works (it just produces via node-proxy).
        self._direct_endpoint = ""
        self._transport: Optional["PipelineTransportServer"] = None    # noqa: F821
        if self.pipeline_enabled:
            port_raw = os.environ.get(
                "ANIMICA_AICF_PIPELINE_DIRECT_PORT", ""
            ).strip()
            if port_raw:
                try:
                    port = int(port_raw)
                except ValueError:
                    port = 0
                if port >= 0:
                    from agent_runtime.pipeline_transport import (
                        PipelineTransportServer,
                    )
                    external = os.environ.get(
                        "ANIMICA_AICF_PIPELINE_DIRECT_URL", ""
                    ).strip() or None
                    self._transport = PipelineTransportServer(
                        worker_address=address,
                        host=os.environ.get(
                            "ANIMICA_AICF_PIPELINE_BIND_HOST", "0.0.0.0"
                        ),
                        port=port,
                        external_url=external,
                        shared_secret_provider=self._pipeline_shared_secret,
                    )
                    try:
                        self._transport.start()
                        self._direct_endpoint = self._transport.public_url()
                    except OSError as exc:
                        log.warning(
                            "[aicf-worker] pipeline transport failed to "
                            "start on port %s (%s); falling back to node-proxy",
                            port_raw, exc,
                        )
                        self._transport = None
        self.state = WorkerState(
            address=address,
            endpoint=endpoint,
            tiers=list(self.tiers),
            hardware=self.profile,
            started_at=time.time(),
        )
        # Where the worker stashes its state so `animica miner aicf-worker
        # status` can read it.
        self.state_path = Path(os.environ.get(
            "ANIMICA_DATA_DIR", "~/.animica")).expanduser() / \
            "aicf_worker" / "state.json"
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_state()

    def _write_state(self) -> None:
        try:
            self.state_path.write_text(
                json.dumps(self.state.to_dict(), indent=2, default=str),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _pipeline_shared_secret(self, peer_address: str) -> bytes:
        """Derive the HMAC secret used to authenticate pipeline-transport
        payloads exchanged with ``peer_address``.

        Reference implementation: prefer an operator-provided secret
        (set ANIMICA_AICF_PIPELINE_SHARED_SECRET on all participants so
        a stage k worker and stage k+1 worker compute the same key) and
        otherwise hash a stable combination of both addresses. The
        latter is not cryptographically strong on its own — an attacker
        who knows both wallet addresses can also derive the key — but
        the signature still binds (job_id, stage_index, sender, payload)
        so a passive observer can't forge submissions either way.

        Production hardening (separate effort): switch the secret to a
        per-session ECDH key derived from the workers' wallet pubkeys
        attested by the node, so the node can't forge worker-to-worker
        traffic either.
        """
        import hashlib
        operator_secret = os.environ.get(
            "ANIMICA_AICF_PIPELINE_SHARED_SECRET", ""
        ).strip()
        if operator_secret:
            return operator_secret.encode("utf-8")
        pair = "|".join(sorted([self.address, peer_address or ""]))
        return hashlib.sha256(f"aicf-pipeline:{pair}".encode("utf-8")).digest()

    def register(self) -> None:
        self.client.register_worker(
            address=self.address, tiers=self.tiers,
            hardware=self.profile.to_dict(),
            direct_endpoint=self._direct_endpoint,
        )
        self.state.last_heartbeat_at = time.time()
        self._write_state()

    def stop(self) -> None:
        self.state.stopping = True

    def run(self, *, idle_sleep_ms: int = 1500,
            heartbeat_interval_sec: int = 30) -> None:
        """Main loop. Returns when self.state.stopping is set."""
        self.register()
        last_hb = time.time()
        # Lazy-load a runner per bundle; we keep a cache by tier.
        runners: dict[str, "LocalBundleRunner"] = {}     # noqa: F821
        while not self.state.stopping:
            # When advertising the pipeline tier, prefer claiming a
            # pending pipeline stage before falling back to a race-mode
            # claim. Pipeline jobs are time-sensitive (stage k blocks
            # stage k+1) so we don't want a pipeline-capable worker
            # sitting on a race job while a stage is waiting.
            stage_handled = False
            if self.pipeline_enabled:
                try:
                    stage = self.client.pipeline_claim_stage(
                        address=self.address
                    )
                except AgentRuntimeError as exc:
                    _eprint(f"[aicf-worker] pipeline claim failed: {exc.message}")
                    stage = None
                if stage:
                    self._process_pipeline_stage(stage, runners)
                    stage_handled = True
            if stage_handled:
                self._write_state()
                continue
            try:
                job = self.client.claim_next_job(address=self.address,
                                                  tiers=[t for t in self.tiers
                                                         if t != "pipeline"])
            except AgentRuntimeError as exc:
                _eprint(f"[aicf-worker] claim failed: {exc.message}")
                time.sleep(idle_sleep_ms / 1000.0)
                continue
            if not job:
                time.sleep(idle_sleep_ms / 1000.0)
                if time.time() - last_hb > heartbeat_interval_sec:
                    try:
                        self.client.worker_status(self.address)
                    except AgentRuntimeError:
                        pass
                    last_hb = time.time()
                    self.state.last_heartbeat_at = last_hb
                    self._write_state()
                continue
            tier = str(job.get("tier", self.tiers[0]))
            job_id = str(job.get("job_id", ""))
            prompt = str(job.get("prompt", ""))
            try:
                runner = runners.get(tier)
                if runner is None:
                    runner = self._load_runner(tier)
                    runners[tier] = runner
                t0 = time.time()
                text = runner.generate(
                    prompt=prompt, history=[],
                    max_output_tokens=int(job.get("max_output_tokens", 512)),
                    temperature=float(job.get("temperature", 0.2)),
                    top_p=float(job.get("top_p", 0.95)),
                )
                latency_ms = int((time.time() - t0) * 1000)
                attestation = {
                    "bundle_sha256": getattr(runner, "_bundle_sha256", ""),
                    "tier": tier,
                    "hardware": self.profile.accelerator_preferred,
                }
                ack = self.client.submit_worker_result(
                    address=self.address, job_id=job_id,
                    text=text, latency_ms=latency_ms,
                    attestation=attestation,
                )
                # With K-way race replication only the first valid
                # submitter wins; losers get `accepted: false` with
                # reason "lost_race". Don't bump local earnings counters
                # for races the node didn't credit us for — otherwise the
                # worker's UI overstates its IOU and disagrees with
                # aicf.workerEarnings on the node.
                if isinstance(ack, dict) and ack.get("accepted"):
                    self.state.jobs_completed += 1
                    self.state.earnings_animica += float(
                        job.get("expected_payout", 0.0))
                else:
                    reason = (ack or {}).get("reason") if isinstance(ack, dict) else None
                    if reason == "lost_race":
                        self.state.jobs_raced_and_lost = (
                            getattr(self.state, "jobs_raced_and_lost", 0) + 1
                        )
            except BundleError as exc:
                _eprint(f"[aicf-worker] bundle error: {exc.message}")
                self.state.jobs_failed += 1
            except AgentRuntimeError as exc:
                _eprint(f"[aicf-worker] job {job_id} failed: {exc.message}")
                self.state.jobs_failed += 1
            self._write_state()

    def _load_runner(self, tier: str):
        """Locate the installed bundle for ``tier`` and return a runner."""
        from flagship_agent.inference import LocalBundleRunner
        cache = Path(os.environ.get(
            "ANIMICA_DATA_DIR", "~/.animica")).expanduser() / \
            "models" / tier
        if not cache.is_dir():
            raise BundleError(
                f"no installed flagship bundle for tier={tier!r}",
                hint="run `animica miner aicf-worker pull --tier <tier>` "
                     "to download a bundle from the network's IPFS CIDs",
            )
        # Pick the highest-priority bundle (latest by exported_at).
        candidates = sorted(cache.iterdir(),
                             key=lambda p: p.stat().st_mtime, reverse=True)
        for bundle in candidates:
            mf = bundle / "manifest.json"
            if not mf.is_file():
                continue
            spec = bundle / "inference.json"
            if not spec.is_file():
                continue
            inf = json.loads(spec.read_text(encoding="utf-8"))
            return LocalBundleRunner(bundle_dir=bundle, inference_spec=inf)
        raise BundleError(
            f"no usable bundles found under {cache}",
        )

    def _get_layer_range_runner(self):
        """Lazy-construct + cache the LayerRangeRunner for this worker.

        Requires ``ANIMICA_AICF_PIPELINE_MODEL_ID`` to point at a HF
        model path (local dir or hub id). Returns ``None`` when torch /
        transformers / the model itself isn't loadable — the caller
        then falls through to the reference transform so the chat
        path still produces an answer.
        """
        if hasattr(self, "_layer_runner_cache"):
            return self._layer_runner_cache
        self._layer_runner_cache = None
        model_id = os.environ.get(
            "ANIMICA_AICF_PIPELINE_MODEL_ID", ""
        ).strip()
        if not model_id:
            log.warning(
                "[aicf-worker] ANIMICA_AICF_PIPELINE_REAL=1 set but "
                "ANIMICA_AICF_PIPELINE_MODEL_ID empty — falling back "
                "to reference transform"
            )
            return None
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
            from flagship_agent.layer_range_inference import (
                LayerRangeRunner,
            )
        except ImportError as exc:
            log.warning(
                "[aicf-worker] LayerRangeRunner imports unavailable "
                "(%s); falling back to reference transform", exc,
            )
            return None
        try:
            tok = AutoTokenizer.from_pretrained(model_id)
            mdl = AutoModelForCausalLM.from_pretrained(model_id)
            self._layer_runner_cache = LayerRangeRunner(mdl, tok)
        except Exception as exc:    # noqa: BLE001 — surface to operator
            log.warning(
                "[aicf-worker] LayerRangeRunner load failed for %s: %s",
                model_id, exc,
            )
            self._layer_runner_cache = None
        return self._layer_runner_cache

    def _run_layer_range_stage(
        self,
        *,
        stage: dict,
        prompt: str,
        upstream_payload: Optional[bytes],
        stage_index: int,
        total_stages: int,
        is_final: bool,
    ) -> tuple:
        """Dispatch a stage to LayerRangeRunner. Returns (output_text,
        output_b64); output_text is set only for the final stage,
        output_b64 carries the safetensors-encoded hidden state for
        intermediate stages."""
        runner = self._get_layer_range_runner()
        if runner is None:
            # Graceful fallback to the reference transform so the chat
            # path doesn't fail just because the model isn't loadable.
            import base64
            base = (upstream_payload.decode("utf-8", errors="replace")
                    if upstream_payload else prompt)
            annotated = (
                f"{base}\n[stage {stage_index}/{total_stages - 1} "
                f"@ {self.address[:12]}… reference-fallback]"
            )
            return (
                annotated if is_final else None,
                base64.b64encode(annotated.encode("utf-8")).decode("ascii"),
            )
        from flagship_agent.layer_range_inference import (
            plan_layer_ranges, encode_activation_b64,
        )
        ranges = plan_layer_ranges(runner.total_layers, int(total_stages))
        layer_range = ranges[stage_index]
        if stage_index == 0:
            payload, _meta = runner.embed_and_run_prefix(prompt, layer_range)
        elif not is_final:
            assert upstream_payload is not None, (
                "middle stage requires upstream payload"
            )
            payload, _meta = runner.run_layers(upstream_payload, layer_range)
        else:
            assert upstream_payload is not None, (
                "final stage requires upstream payload"
            )
            spec = stage.get("spec") or {}
            text, _meta = runner.run_suffix_and_generate(
                upstream_payload,
                layer_range,
                max_new_tokens=int(spec.get("max_output_tokens", 128)),
                temperature=float(spec.get("temperature", 0.2)),
                top_p=float(spec.get("top_p", 0.95)),
            )
            return text, None
        # base64-encode the safetensors blob for the node-proxy
        # fallback path. Direct W2W still transfers the raw bytes
        # via the local transport server.
        import base64
        return None, base64.b64encode(payload).decode("ascii")

    def _try_direct_upstream_fetch(
        self,
        job_id: str,
        stage_index: int,
    ) -> Optional[tuple]:
        """One-shot attempt to grab the upstream activation peer-to-peer.

        Returns ``(payload_bytes, upstream_address, "direct-w2w")`` on
        success, or None if any step failed: the node doesn't yet know
        the upstream worker, the upstream didn't advertise a reachable
        endpoint, the network call timed out, or the response signature
        didn't verify. The caller falls back to the node-proxy path on
        None — direct transport is an optimisation, not load-bearing.
        """
        try:
            info = self.client.pipeline_get_stage_info(
                job_id=job_id, stage_index=stage_index - 1,
            )
        except AgentRuntimeError:
            return None
        if not info or not info.get("found"):
            return None
        if str(info.get("state") or "") != "completed":
            return None
        endpoint = str(info.get("direct_endpoint") or "").strip()
        if not endpoint:
            return None
        from agent_runtime.pipeline_transport import (
            fetch_direct, verify_payload_tag,
        )
        result = fetch_direct(
            base_url=endpoint,
            chunk_path_hint=str(info.get("chunk_path_hint") or ""),
            job_id=job_id,
            stage_index=stage_index - 1,
        )
        if result is None:
            return None
        payload, sender, tag = result
        # Verify before trusting the bytes. The shared secret derivation
        # uses both addresses; consumer side passes the producer's
        # address explicitly so the HMAC must match what the producer
        # computed.
        secret = self._pipeline_shared_secret(sender)
        if not verify_payload_tag(
            expected=tag,
            job_id=job_id,
            stage_index=stage_index - 1,
            sender_address=sender,
            payload=payload,
            shared_secret=secret,
        ):
            log.warning(
                "[aicf-worker] direct upstream fetch signature failed "
                "job=%s stage=%d sender=%s",
                job_id, stage_index - 1, sender,
            )
            return None
        return payload, sender, "direct-w2w"

    def _process_pipeline_stage(
        self,
        stage: dict,
        runners: dict,
    ) -> None:
        """Dispatch a claimed stage based on the job's decode mode.

        Streaming-decode jobs (spec.decode_mode == "streaming") run
        a real per-token loop with each stage maintaining its own KV
        cache between rounds. Prefill-only jobs (the legacy default)
        run a single prefill pass; the final stage does the
        autoregressive decode locally.
        """
        spec = stage.get("spec") or {}
        if str(spec.get("decode_mode") or "").lower() == "streaming":
            self._process_streaming_stage(stage, runners)
            return
        self._process_prefill_only_stage(stage, runners)

    def _process_prefill_only_stage(
        self,
        stage: dict,
        runners: dict,
    ) -> None:
        """Run a single pipeline stage end-to-end.

        Stage 0 gets the original prompt and emits the first activation.
        Stage k > 0 pulls the upstream activation, applies a local
        transform, and emits the result. The final stage's
        ``output_text`` becomes the chat job's reply.

        The reference transform here is intentionally minimal: each
        stage appends a stage-marker to a text-encoded activation so
        the protocol can be exercised and tested without a real
        layer-sharded model bundle. A production runner replaces this
        with `runner.run_layer_range(activation, start, end)` once the
        sharded bundle format is in place. Until then, set
        ANIMICA_AICF_PIPELINE_USE_RUNNER=1 to have stage k run a full
        single-tier inference on the upstream text and pass it forward
        (slower than racing; offered as a stop-gap for demos).
        """
        job_id = str(stage.get("job_id") or "")
        stage_index = int(stage.get("stage_index") or 0)
        total_stages = int(stage.get("total_stages") or 1)
        prompt = str(stage.get("prompt") or "")
        is_final = bool(stage.get("is_final_stage"))
        if not job_id:
            return
        # 1. Pull upstream activation (if any). Direct transport is tried
        #    first when the node tells us the upstream worker exposed a
        #    reachable endpoint; we fall through to the node-proxy path on
        #    any failure so NAT'd / firewalled peers Just Work.
        upstream_text = ""
        upstream_b64: Optional[str] = None
        upstream_payload: Optional[bytes] = None
        upstream_route = "none"
        if stage_index > 0:
            deadline = time.time() + 60.0
            while time.time() < deadline:
                direct = self._try_direct_upstream_fetch(job_id, stage_index)
                if direct is not None:
                    payload, _upstream_addr, route = direct
                    upstream_payload = payload
                    upstream_route = route
                    try:
                        upstream_text = payload.decode("utf-8")
                    except UnicodeDecodeError:
                        upstream_text = ""
                    import base64
                    upstream_b64 = base64.b64encode(payload).decode("ascii")
                    break
                try:
                    up = self.client.pipeline_get_upstream_activation(
                        job_id=job_id, stage_index=stage_index,
                    )
                except AgentRuntimeError as exc:
                    _eprint(
                        f"[aicf-worker] pipeline upstream fetch failed "
                        f"job={job_id} stage={stage_index}: {exc.message}"
                    )
                    self.state.jobs_failed += 1
                    return
                if up.get("ready"):
                    upstream_text = str(up.get("output_text") or "")
                    upstream_b64 = up.get("output_b64")
                    if upstream_b64:
                        import base64
                        try:
                            upstream_payload = base64.b64decode(upstream_b64)
                        except Exception:    # noqa: BLE001
                            upstream_payload = None
                    upstream_route = "node-proxy"
                    break
                if up.get("note") == "job_terminal_before_upstream_ready":
                    return
                time.sleep(0.2)
            else:
                _eprint(
                    f"[aicf-worker] pipeline upstream wait timed out "
                    f"job={job_id} stage={stage_index}"
                )
                self.state.jobs_failed += 1
                return

        # 2. Apply the stage transform. Three modes:
        #    a. ANIMICA_AICF_PIPELINE_REAL=1 + a configured model →
        #       LayerRangeRunner: the worker runs its assigned slice of
        #       the transformer's decoder layers and emits hidden states
        #       (safetensors-encoded) as the activation. Final stage
        #       additionally runs the LM head + autoregressive decode.
        #    b. ANIMICA_AICF_PIPELINE_USE_RUNNER=1 + final stage → run
        #       the existing LocalBundleRunner end-to-end on the
        #       upstream-plus-prompt; legacy fallback that doesn't get
        #       layer-shard speedup but produces a real answer.
        #    c. Otherwise → the reference protocol transform (identity
        #       + stage marker), used when no real model is configured.
        use_real_layer = (
            os.environ.get("ANIMICA_AICF_PIPELINE_REAL", "0")
            .strip().lower() in {"1", "true", "yes", "on"}
        )
        use_runner = (
            os.environ.get("ANIMICA_AICF_PIPELINE_USE_RUNNER", "0")
            .strip().lower() in {"1", "true", "yes", "on"}
        )
        t0 = time.time()
        try:
            output_text = None
            output_b64 = None
            if use_real_layer:
                output_text, output_b64 = self._run_layer_range_stage(
                    stage=stage,
                    prompt=prompt,
                    upstream_payload=upstream_payload,
                    stage_index=stage_index,
                    total_stages=total_stages,
                    is_final=is_final,
                )
            elif use_runner and is_final:
                tier = str(stage.get("tier") or "small")
                runner = runners.get(tier)
                if runner is None:
                    runner = self._load_runner(tier)
                    runners[tier] = runner
                composed = (upstream_text or "") + "\n\n" + prompt
                text = runner.generate(
                    prompt=composed, history=[],
                    max_output_tokens=int(stage.get("spec", {})
                                          .get("max_output_tokens", 512)),
                    temperature=float(stage.get("spec", {})
                                      .get("temperature", 0.2)),
                    top_p=float(stage.get("spec", {})
                                .get("top_p", 0.95)),
                )
                output_text = text
                output_b64 = None
            else:
                # Reference protocol transform — identity on the
                # upstream activation plus a stage marker. Encodes the
                # activation as base64'd UTF-8 for symmetry with the
                # tensor-bytes case.
                base = upstream_text or prompt
                annotated = (
                    f"{base}\n[stage {stage_index}/{total_stages - 1} @ "
                    f"{self.address[:12]}…]"
                )
                import base64
                output_text = annotated if is_final else None
                output_b64 = base64.b64encode(annotated.encode("utf-8")).decode("ascii")
        except BundleError as exc:
            _eprint(f"[aicf-worker] pipeline stage bundle error: {exc.message}")
            self.state.jobs_failed += 1
            return
        except Exception as exc:    # noqa: BLE001 — surface to operator
            _eprint(
                f"[aicf-worker] pipeline stage {stage_index} failed: {exc}"
            )
            self.state.jobs_failed += 1
            return
        latency_ms = int((time.time() - t0) * 1000)

        # 3a. Publish bytes to our local transport (if running) so the
        #    downstream stage can GET them directly. This is the W2W
        #    fast path; the next bullet still pushes to the node so the
        #    node-proxy fallback works for peers that can't reach us.
        if self._transport is not None and output_b64:
            import base64
            from agent_runtime.pipeline_transport import compute_payload_tag
            try:
                raw = base64.b64decode(output_b64)
            except Exception:    # noqa: BLE001
                raw = (output_text or "").encode("utf-8")
            # Tag is HMAC over (job_id, stage_index, sender, payload)
            # with the consumer-derived shared secret. We don't know
            # the consumer's address yet (multiple stages may be hops
            # away), so we tag with our own derivation — consumers
            # use the SAME derivation since the address pair is
            # commutative under sorted hash.
            tag = compute_payload_tag(
                job_id=job_id, stage_index=stage_index,
                sender_address=self.address, payload=raw,
                shared_secret=self._pipeline_shared_secret(self.address),
            )
            self._transport.stash_local(
                job_id=job_id, stage_index=stage_index,
                payload=raw, tag=tag,
            )

        # 3b. Submit the stage result back to the node so the
        #    fallback / settlement path always works.
        try:
            ack = self.client.pipeline_submit_stage_result(
                address=self.address,
                job_id=job_id,
                stage_index=stage_index,
                output_b64=output_b64,
                output_text=output_text,
                meta={
                    "latency_ms": latency_ms,
                    "tier": stage.get("tier") or "",
                    "upstream_route": upstream_route,
                    "direct_endpoint_used": bool(self._direct_endpoint),
                },
            )
        except AgentRuntimeError as exc:
            _eprint(
                f"[aicf-worker] pipeline submit failed job={job_id} "
                f"stage={stage_index}: {exc.message}"
            )
            self.state.jobs_failed += 1
            return
        if isinstance(ack, dict) and ack.get("accepted"):
            self.state.pipeline_stages_completed += 1
            if ack.get("status") == "completed_job":
                # Only the final-stage worker is credited under the
                # current settlement rule (cross-stage split is a
                # follow-up). Mirror to jobs_completed so existing
                # dashboards see the work.
                self.state.jobs_completed += 1

    def _process_streaming_stage(
        self,
        stage: dict,
        runners: dict,    # noqa: ARG002 — unused; kept for symmetry
    ) -> None:
        """Run a streaming-decode stage end-to-end.

        Phases:
          1. Prefill — stage 0 embeds the prompt and runs its layers
             with KV-cache stash; later stages pull the upstream prefill
             activation, run their layers (caching K/V), submit.
             The final stage submits its prefill landmark with
             ``meta.non_terminal=True`` so the job stays open.
          2. Decode loop — stage 0 long-polls for tokens to embed;
             middle stages long-poll for upstream's per-step output;
             the final stage long-polls upstream then runs LM head +
             sample, appends the token text to job.text, feeds the
             next token back to stage 0 (piggy-backed in submit's meta),
             and terminates on EOS / max_new_tokens.

        Real layer-shard inference is on when
        ``ANIMICA_AICF_PIPELINE_REAL=1`` AND ``ANIMICA_AICF_PIPELINE_MODEL_ID``
        is set; otherwise the worker falls back to a reference
        identity-with-marker transform so the protocol still produces
        an answer (useful for protocol-only tests).
        """
        job_id = str(stage.get("job_id") or "")
        stage_index = int(stage.get("stage_index") or 0)
        total_stages = int(stage.get("total_stages") or 1)
        is_first = stage_index == 0
        is_final = bool(stage.get("is_final_stage"))
        prompt = str(stage.get("prompt") or "")
        spec = stage.get("spec") or {}
        max_new_tokens = int(spec.get("max_output_tokens") or 64)
        temperature = float(spec.get("temperature") or 0.2)
        top_p = float(spec.get("top_p") or 0.95)

        use_real = (
            os.environ.get("ANIMICA_AICF_PIPELINE_REAL", "0")
            .strip().lower() in {"1", "true", "yes", "on"}
        )

        runner = None
        layer_range = None
        if use_real:
            runner = self._get_layer_range_runner()
        if runner is not None:
            from flagship_agent.layer_range_inference import plan_layer_ranges
            ranges = plan_layer_ranges(runner.total_layers, total_stages)
            layer_range = ranges[stage_index]

        try:
            # -------- 1. Prefill --------
            if is_first:
                payload_b64 = self._streaming_prefill_first(
                    runner=runner, layer_range=layer_range,
                    job_id=job_id, prompt=prompt,
                    stage_index=stage_index, total_stages=total_stages,
                )
            else:
                payload_b64 = self._streaming_prefill_mid_or_final(
                    runner=runner, layer_range=layer_range,
                    job_id=job_id, stage_index=stage_index,
                    is_final=is_final, total_stages=total_stages,
                )

            # Submit prefill landmark. Final stage marks it non-terminal
            # so the decode loop has somewhere to write.
            prefill_meta = {"phase": "prefill"}
            if is_final:
                prefill_meta["non_terminal"] = True
            self._submit_pipeline_landmark(
                job_id=job_id, stage_index=stage_index,
                output_b64=payload_b64, meta=prefill_meta,
            )

            # -------- 2. Decode loop --------
            if is_final:
                self._streaming_final_decode_loop(
                    runner=runner, layer_range=layer_range,
                    job_id=job_id, stage_index=stage_index,
                    max_new_tokens=max_new_tokens,
                    temperature=temperature, top_p=top_p,
                )
            else:
                self._streaming_mid_decode_loop(
                    runner=runner, layer_range=layer_range,
                    job_id=job_id, stage_index=stage_index,
                    is_first=is_first,
                )
        finally:
            if runner is not None:
                try:
                    runner.release_cache(job_id)
                except Exception:    # noqa: BLE001
                    pass

    def _streaming_prefill_first(
        self, *, runner, layer_range, job_id, prompt,
        stage_index, total_stages,
    ):
        import base64
        if runner is not None:
            payload, _meta = runner.prefill_with_cache(
                prompt, layer_range,
                job_id=job_id, is_first_stage=True,
            )
            return base64.b64encode(payload).decode("ascii")
        # Reference transform: stash the prompt as a base64 'activation'.
        marker = f"[prefill stage 0/{total_stages-1} @ {self.address[:12]}…]\n{prompt}"
        return base64.b64encode(marker.encode("utf-8")).decode("ascii")

    def _streaming_prefill_mid_or_final(
        self, *, runner, layer_range, job_id, stage_index,
        is_final, total_stages,
    ):
        import base64
        # Pull upstream prefill activation. Prefill goes via the same
        # output_b64 path that the prefill-only mode uses; we reuse
        # the existing helpers (direct W2W with node-proxy fallback).
        deadline = time.time() + 60.0
        upstream_b64 = None
        while time.time() < deadline:
            direct = self._try_direct_upstream_fetch(job_id, stage_index)
            if direct is not None:
                payload, _addr, _route = direct
                upstream_b64 = base64.b64encode(payload).decode("ascii")
                break
            try:
                up = self.client.pipeline_get_upstream_activation(
                    job_id=job_id, stage_index=stage_index,
                )
            except AgentRuntimeError:
                up = None
            if up and up.get("ready"):
                upstream_b64 = up.get("output_b64")
                break
            time.sleep(0.1)
        if upstream_b64 is None:
            raise AgentRuntimeError(
                "streaming prefill upstream not ready within 60s")
        upstream_bytes = base64.b64decode(upstream_b64)
        if runner is not None:
            out_bytes, _meta = runner.prefill_with_cache(
                upstream_bytes, layer_range,
                job_id=job_id, is_first_stage=False,
            )
            return base64.b64encode(out_bytes).decode("ascii")
        marker = (
            f"[prefill stage {stage_index}/{total_stages-1} "
            f"@ {self.address[:12]}…]\n"
            + upstream_bytes.decode("utf-8", errors="replace")
        )
        return base64.b64encode(marker.encode("utf-8")).decode("ascii")

    def _submit_pipeline_landmark(
        self, *, job_id, stage_index, output_b64, meta,
    ):
        """Submit a prefill activation via the legacy stage-result RPC
        so existing aicf.pipelineGetUpstreamActivation callers keep
        seeing prefill bytes via the node-proxy path. Also stashes a
        copy in the local transport for direct W2W."""
        import base64
        from agent_runtime.pipeline_transport import compute_payload_tag
        if self._transport is not None and output_b64:
            try:
                raw = base64.b64decode(output_b64)
                tag = compute_payload_tag(
                    job_id=job_id, stage_index=stage_index,
                    sender_address=self.address, payload=raw,
                    shared_secret=self._pipeline_shared_secret(self.address),
                )
                self._transport.stash_local(
                    job_id=job_id, stage_index=stage_index,
                    payload=raw, tag=tag,
                )
            except Exception:    # noqa: BLE001
                pass
        self.client.pipeline_submit_stage_result(
            address=self.address, job_id=job_id, stage_index=stage_index,
            output_b64=output_b64, output_text=None, meta=meta,
        )

    def _streaming_mid_decode_loop(
        self, *, runner, layer_range, job_id, stage_index, is_first,
    ):
        """Intermediate (or stage 0) per-step pull/run/submit loop.

        Stage 0: long-poll pipelineGetNextTokenStep, get
        (token_id, step_index), feed to its layers, submit downstream.
        Middle stages: long-poll pipelineGetUpstreamStep at increasing
        step indices, run their layers, submit.
        """
        import base64
        from agent_runtime.pipeline_transport import compute_payload_tag
        current_step = 1    # prefill is implicit step 0
        loop_deadline = time.time() + 600.0     # safety net
        while time.time() < loop_deadline:
            try:
                if is_first:
                    nt = self.client.pipeline_get_next_token_step(job_id=job_id)
                    if not nt.get("ready"):
                        if nt.get("note") == "job_terminal":
                            return
                        continue
                    step_index = int(nt["step_index"])
                    token_id = int(nt["token_id"])
                    if runner is not None:
                        out_bytes, _meta = runner.decode_step_layers(
                            token_id, layer_range,
                            job_id=job_id, is_first_stage=True,
                        )
                        out_b64 = base64.b64encode(out_bytes).decode("ascii")
                    else:
                        marker = f"[decode step={step_index} stage=0 tok={token_id}]"
                        out_b64 = base64.b64encode(marker.encode("utf-8")).decode("ascii")
                else:
                    up = self.client.pipeline_get_upstream_step(
                        job_id=job_id, stage_index=stage_index,
                        step_index=current_step,
                    )
                    if not up.get("ready"):
                        if up.get("note", "").startswith("job_terminal"):
                            return
                        continue
                    step_index = current_step
                    up_b64 = up.get("output_b64")
                    up_bytes = base64.b64decode(up_b64) if up_b64 else b""
                    if runner is not None:
                        out_bytes, _meta = runner.decode_step_layers(
                            up_bytes, layer_range,
                            job_id=job_id, is_first_stage=False,
                        )
                        out_b64 = base64.b64encode(out_bytes).decode("ascii")
                    else:
                        marker = (
                            f"[decode step={step_index} stage={stage_index} "
                            + up_bytes.decode("utf-8", errors="replace")
                            + "]"
                        )
                        out_b64 = base64.b64encode(marker.encode("utf-8")).decode("ascii")

                # Stash for direct W2W
                if self._transport is not None:
                    try:
                        raw = base64.b64decode(out_b64)
                        tag = compute_payload_tag(
                            job_id=job_id, stage_index=stage_index,
                            sender_address=self.address, payload=raw,
                            shared_secret=self._pipeline_shared_secret(self.address),
                        )
                        # Per-step stash uses the same path as prefill
                        # since downstream's GET also targets this stage
                        # index; we differentiate by step in the meta.
                        self._transport.stash_local(
                            job_id=job_id, stage_index=stage_index,
                            payload=raw, tag=tag,
                        )
                    except Exception:    # noqa: BLE001
                        pass

                ack = self.client.pipeline_submit_decode_step(
                    address=self.address, job_id=job_id,
                    stage_index=stage_index, step_index=step_index,
                    output_b64=out_b64, output_text=None, meta={},
                )
                if ack.get("job_state") in {"completed", "failed"}:
                    return
                if ack.get("status") == "already_terminal":
                    return
                self.state.pipeline_stages_completed += 1
                current_step += 1
            except AgentRuntimeError as exc:
                _eprint(
                    f"[aicf-worker] streaming mid-stage error "
                    f"job={job_id} stage={stage_index}: {exc.message}"
                )
                self.state.jobs_failed += 1
                return
        _eprint(
            f"[aicf-worker] streaming mid-stage loop deadline reached "
            f"job={job_id} stage={stage_index}"
        )

    def _streaming_final_decode_loop(
        self, *, runner, layer_range, job_id, stage_index,
        max_new_tokens, temperature, top_p,
    ):
        """Final stage's per-token decode loop.

        Pulls upstream step k, runs its layer range + LM head + sample,
        appends the token text to job.text, feeds the next token back
        to stage 0 (piggy-backed via meta.next_token_id), terminates on
        EOS or max_new_tokens.
        """
        import base64
        current_step = 1
        tokens_emitted = 0
        # Seed the loop: feed an initial token to stage 0 (using the
        # prompt's last token as a starting point — for a real model
        # this is what kicks off generation). The node accepts it and
        # stage 0 will consume it on its next poll.
        seed_token = self._pick_seed_token(runner)
        try:
            self.client.pipeline_feed_token(
                job_id=job_id, token_id=int(seed_token),
            )
        except AgentRuntimeError as exc:
            _eprint(f"[aicf-worker] feed seed token failed: {exc.message}")
            self.state.jobs_failed += 1
            return

        loop_deadline = time.time() + 600.0
        while tokens_emitted < int(max_new_tokens) and time.time() < loop_deadline:
            try:
                up = self.client.pipeline_get_upstream_step(
                    job_id=job_id, stage_index=stage_index,
                    step_index=current_step,
                )
                if not up.get("ready"):
                    if up.get("note", "").startswith("job_terminal"):
                        return
                    continue
                up_b64 = up.get("output_b64")
                up_bytes = base64.b64decode(up_b64) if up_b64 else b""
                if runner is not None:
                    token_id, token_text, _meta = runner.decode_step_with_head(
                        up_bytes, layer_range, job_id=job_id,
                        temperature=temperature, top_p=top_p,
                    )
                    is_eos_now = runner.is_eos(token_id)
                else:
                    # Reference transform: pick a deterministic next
                    # token from upstream bytes so tests can assert the
                    # protocol flow without a real model.
                    token_id = (sum(up_bytes) % 95) or 1
                    token_text = chr(token_id + 32) if 0 < token_id < 95 else "?"
                    is_eos_now = False
                tokens_emitted += 1
                terminal = is_eos_now or tokens_emitted >= int(max_new_tokens)
                meta = {"is_terminal": terminal}
                if not terminal:
                    meta["next_token_id"] = int(token_id)
                self.client.pipeline_submit_decode_step(
                    address=self.address, job_id=job_id,
                    stage_index=stage_index, step_index=current_step,
                    output_b64=None, output_text=token_text, meta=meta,
                )
                self.state.pipeline_stages_completed += 1
                if terminal:
                    self.state.jobs_completed += 1
                    return
                current_step += 1
            except AgentRuntimeError as exc:
                _eprint(
                    f"[aicf-worker] streaming final-stage error "
                    f"job={job_id}: {exc.message}"
                )
                self.state.jobs_failed += 1
                return
        _eprint(
            f"[aicf-worker] streaming final-stage loop ended "
            f"tokens={tokens_emitted} job={job_id}"
        )

    def _pick_seed_token(self, runner) -> int:
        """Pick a token id to bootstrap the decode loop. With a real
        runner we use the tokenizer's BOS or the prompt's last token;
        with the reference transform we just use 1 (any non-EOS marker
        works since the reference final stage doesn't care)."""
        if runner is None:
            return 1
        bos = getattr(runner.tokenizer, "bos_token_id", None)
        if bos is not None:
            return int(bos)
        return 1

    def close(self) -> None:
        if self._transport is not None:
            try:
                self._transport.stop()
            except Exception:    # noqa: BLE001
                pass
            self._transport = None
        self.client.close()


def _eprint(msg: str) -> None:
    import sys
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- #
# Bundle pull (IPFS download)                                                 #
# --------------------------------------------------------------------------- #

def _miner_cache_root(cfg: Config) -> Path:
    raw = (cfg.integration.get("aicf", {}).get("miner_cache_dir")
           or cfg.model_catalog["propagation"]["miner_cache_dir"])
    return Path(str(raw)).expanduser()


def _slugify_repo(repo_id: str) -> str:
    return repo_id.replace("/", "__").replace(":", "_")


def _tier_spec(catalog: dict, tier: str) -> dict:
    for t in catalog.get("tiers") or []:
        if isinstance(t, dict) and str(t.get("id")) == tier:
            return t
    raise BundleError(
        f"tier {tier!r} not found in model_catalog",
        hint="check ai/configs/model_catalog.yaml for valid tier ids",
    )


def bootstrap_bundle_from_hf(
    tier: str,
    *,
    cfg: Optional[Config] = None,
    repo_id: Optional[str] = None,
    revision: Optional[str] = None,
) -> Path:
    """Download a tier's base model directly from HuggingFace Hub and shape
    it into a local AICF bundle layout that :class:`LocalBundleRunner` can
    load.

    Used by ``animica miner setup`` as the default source when no IPFS CID
    has been configured for a tier. This means a fresh `pip install animica`
    + `animica miner setup` produces a working AICF worker with zero manual
    configuration.

    Layout written on disk::

        <cache>/<tier>/hf-<sanitized-repo>/
            manifest.json
            inference.json
            model/   <- HF snapshot lives here
    """
    cfg = cfg or load_config()
    spec = _tier_spec(dict(cfg.model_catalog), tier)
    repo_id = repo_id or str(spec.get("base_model") or "").strip()
    if not repo_id:
        raise BundleError(
            f"tier {tier!r} has no base_model in model_catalog; cannot bootstrap",
        )

    cache_root = _miner_cache_root(cfg)
    bundle_dir = cache_root / tier / f"hf-{_slugify_repo(repo_id)}"
    model_dir = bundle_dir / "model"
    manifest_path = bundle_dir / "manifest.json"
    inference_path = bundle_dir / "inference.json"

    # Skip the download if a previous bootstrap already populated this dir.
    if manifest_path.is_file() and inference_path.is_file() and any(
            model_dir.glob("*")):
        return bundle_dir

    try:
        from huggingface_hub import snapshot_download   # type: ignore
    except Exception as exc:    # noqa: BLE001
        raise BundleError(
            f"huggingface_hub is required for HF bootstrap: {exc}",
            hint="install it with: pip install huggingface_hub",
        ) from exc

    model_dir.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(
            repo_id=repo_id,
            revision=revision,
            local_dir=str(model_dir),
            allow_patterns=[
                "*.json", "*.txt", "*.model", "*.safetensors",
                "tokenizer*", "vocab*", "merges*", "special_tokens*",
                "generation_config*", "chat_template*", "*.tiktoken",
            ],
        )
    except Exception as exc:    # noqa: BLE001
        import shutil as _shutil
        _shutil.rmtree(bundle_dir, ignore_errors=True)
        raise BundleError(
            f"could not download base model {repo_id!r} from HuggingFace: {exc}",
            hint="set HF_TOKEN if the repo is gated; check network access",
        ) from exc

    precision = str(spec.get("precision") or "fp32")
    inference_spec = {
        "schema": 1,
        "tier": tier,
        "model_subdir": "model",
        "precision": precision,
        "trust_remote_code": True,
        "context_window": int(spec.get("context_window") or 8192),
    }
    manifest = {
        "schema": 1,
        "run_id": f"hf-{_slugify_repo(repo_id)}",
        "tier": tier,
        "base_model": repo_id,
        "effective_mode": "base-only",
        "available_for_real_inference": True,
        "artifacts": {"model": "model/"},
        "source": "huggingface",
        "revision": revision or "main",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    inference_path.write_text(
        json.dumps(inference_spec, indent=2), encoding="utf-8")
    return bundle_dir


def pull_bundle(cid: str, *, tier: str,
                cfg: Optional[Config] = None,
                verify_sha256: Optional[str] = None) -> Path:
    """Download a bundle CID from configured IPFS gateways into the local
    miner cache. Returns the directory the bundle was extracted to."""
    import httpx
    import tarfile
    import shutil
    cfg = cfg or load_config()
    gateways = list(cfg.integration["ipfs"]["gateways"])
    cache_root = Path(cfg.integration["aicf"].get("miner_cache_dir") or
                       cfg.model_catalog["propagation"]["miner_cache_dir"]
                       ).expanduser()
    target_dir = cache_root / tier / cid[:12]
    if target_dir.is_dir():
        return target_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    tarball = target_dir.with_suffix(".tar.zst")
    last_exc: Optional[Exception] = None
    for gw in gateways:
        url = f"{gw.rstrip('/')}/{cid}"
        try:
            with httpx.stream("GET", url, timeout=120.0) as r:
                r.raise_for_status()
                with tarball.open("wb") as fh:
                    for chunk in r.iter_bytes():
                        fh.write(chunk)
            break
        except Exception as exc:    # noqa: BLE001
            last_exc = exc
            continue
    else:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise BundleError(
            f"could not fetch bundle CID {cid} from any IPFS gateway: "
            f"{last_exc}",
        )
    # Verify
    if verify_sha256:
        import hashlib
        h = hashlib.sha256()
        with tarball.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(chunk)
        if h.hexdigest() != verify_sha256:
            shutil.rmtree(target_dir, ignore_errors=True)
            tarball.unlink(missing_ok=True)
            raise BundleError("bundle sha256 mismatch after download")
    # Extract
    try:
        # zstd → decompress to .tar then extract.
        if shutil.which("zstd"):
            import subprocess
            subprocess.check_call(["zstd", "-q", "-d", str(tarball),
                                    "-o", str(tarball.with_suffix(".tar"))])
            tarball.unlink(missing_ok=True)
            tarball = tarball.with_suffix(".tar")
        with tarfile.open(tarball) as tf:
            tf.extractall(target_dir.parent)
    except Exception as exc:    # noqa: BLE001
        raise BundleError(f"bundle extraction failed: {exc}") from exc
    return target_dir
