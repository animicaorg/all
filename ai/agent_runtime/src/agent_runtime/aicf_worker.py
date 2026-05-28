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

    def register(self) -> None:
        self.client.register_worker(
            address=self.address, tiers=self.tiers,
            hardware=self.profile.to_dict(),
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
            try:
                job = self.client.claim_next_job(address=self.address,
                                                  tiers=self.tiers)
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

    def close(self) -> None:
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
