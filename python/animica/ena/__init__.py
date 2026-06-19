"""
animica.ena
===========

ENA — the CLI-first agent, retrieval, useful-work, and training orchestration
layer for Animica.

The :class:`ENA` facade wires a loaded :class:`~animica.ena.config.ENAConfig`
to the SQLite store and the service objects (jobs, agent, retrieval, training)
so the CLI (``animica ena ...``) and the HTTP service share one code path.

Heavy/optional dependencies (transformers, datasets, PyYAML) are imported
lazily inside the modules that need them, so ``import animica.ena`` works on a
standard-library-only install.
"""

from __future__ import annotations

from typing import Any, Optional

from .config import ENAConfig, init_config, load_config
from .errors import ENAError
from .models import ENA_CONFIG_VERSION

__all__ = ["ENA", "ENAConfig", "ENAError", "load_config", "init_config",
           "ENA_CONFIG_VERSION", "__version__"]

__version__ = ENA_CONFIG_VERSION


class ENA:
    """High-level facade binding config + store + services."""

    def __init__(self, config_path: Optional[str] = None,
                 cfg: Optional[ENAConfig] = None) -> None:
        self.cfg = cfg or load_config(config_path)
        from .store import Store
        self.store = Store(self.cfg.db_path())
        from .jobs import JobService
        from .agent import Agent
        from .demand import DemandService
        from .walletconnect import WalletConnectService
        from .pool import PoolService
        from .curriculum import CurriculumService
        from .tools import DynamicToolRegistry
        self.jobs = JobService(self.cfg, self.store)
        self.agent = Agent(self.cfg, self.store)
        self.demand = DemandService(self.cfg, self.store, self.jobs)
        self.walletconnect = WalletConnectService(self.cfg, self.store)
        # Governed registry of dynamically-proposed tools (human-approved before
        # they become active/teachable). See animica.ena.tools.
        self.tools = DynamicToolRegistry(self.cfg)
        # Drives the self-adapting Curriculum Flywheel (opt-in per pool); the
        # pool fires its best-effort rotation hook on each promotion. Approved
        # dynamic tools become teachable through the registry.
        self.curriculum = CurriculumService(self.cfg, self.store, self.jobs,
                                            self.agent, tools=self.tools)
        self.pool = PoolService(self.cfg, self.store, self.jobs, self.demand,
                                curriculum=self.curriculum)

    # -- retrieval --------------------------------------------------------
    def build_index(self, paths: list[str], *, name: str,
                    embedding_provider: Optional[str] = None, **kw) -> dict[str, Any]:
        from . import retrieval
        from .providers import build_embedding_adapter
        ecfg = self.cfg.embedding_provider(embedding_provider)
        embedder = build_embedding_adapter(ecfg)
        return retrieval.build_index(self.store, paths, index_name=name,
                                     embedder=embedder, embedding_provider=ecfg.provider,
                                     embedding_model=ecfg.model, **kw)

    def search(self, query: str, *, mode: str = "hybrid",
               index: Optional[str] = None,
               embedding_provider: Optional[str] = None, limit: int = 8) -> list[dict[str, Any]]:
        from . import retrieval
        from .providers import build_embedding_adapter
        embedder = build_embedding_adapter(self.cfg.embedding_provider(embedding_provider))
        return retrieval.search(self.store, query, mode=mode, index=index,
                                embedder=embedder, limit=limit)

    # -- models -----------------------------------------------------------
    def list_model_providers(self) -> list[dict[str, Any]]:
        return [mp.to_dict() for mp in self.cfg.model_providers.values()]

    def test_model(self, provider: Optional[str] = None) -> dict[str, Any]:
        from .providers import build_model_adapter
        return build_model_adapter(self.cfg.model_provider(provider)).test()

    def test_embedding(self, provider: Optional[str] = None) -> dict[str, Any]:
        from .providers import build_embedding_adapter
        return build_embedding_adapter(self.cfg.embedding_provider(provider)).test()

    # -- training ---------------------------------------------------------
    def train_prepare(self, **kw) -> dict[str, Any]:
        from . import training
        return training.prepare(self.cfg, **kw)

    def train_run(self, *, manifest_path: str, backend: Optional[str] = None) -> dict[str, Any]:
        from . import training
        return training.run(self.cfg, self.store, manifest_path=manifest_path, backend=backend)

    def train_eval(self, **kw) -> dict[str, Any]:
        from . import training
        return training.evaluate(self.cfg, self.store, **kw)

    def run_status(self, run_id: str) -> dict[str, Any]:
        from . import training
        return training.status(self.store, run_id)

    def list_runs(self, limit: int = 200) -> list[dict[str, Any]]:
        from . import training
        return training.list_runs(self.store, limit=limit)

    def export_run(self, run_id: str, out: Optional[str] = None) -> dict[str, Any]:
        from . import training
        return training.export_run(self.store, run_id, out)

    # -- training data ----------------------------------------------------
    def list_datasets(self) -> list[dict[str, Any]]:
        return self.store.list_datasets()

    def contribute_dataset(self, *, name: Optional[str] = None,
                           kind: str = "contributed", rows: Optional[list] = None,
                           url: Optional[str] = None, curate: bool = True,
                           contributor: Optional[str] = None) -> dict[str, Any]:
        """Accept community training data: inline JSONL ``rows`` (curated +
        registered) or a ``url`` (queued as a scrape job for the fleet)."""
        from . import datasets as d
        from .models import new_uuid, now_ts
        if url:
            job = self.jobs.create("scrape", {"url": url}, requester=contributor)
            return {"mode": "url", "job_id": job["job_id"], "status": job["status"],
                    "note": "queued as a scrape job; run a worker to fetch it"}
        if not rows:
            from .errors import DatasetError
            raise DatasetError("contribute requires either rows or url")
        ddir = self.cfg.artifacts_dir() / "contributed"
        ddir.mkdir(parents=True, exist_ok=True)
        raw = ddir / f"{new_uuid()[:12]}.jsonl"
        d.write_jsonl(raw, rows)
        path = raw
        curated = None
        if curate:
            norm = ddir / (raw.stem + ".norm.jsonl")
            clean = ddir / (raw.stem + ".clean.jsonl")
            d.normalize(raw, norm)
            curated = d.dedupe(norm, clean)
            path = clean
        rec = d.ingest(path, kind, self.store)
        rec["name"] = name or rec["dataset_id"]
        rec["contributor"] = contributor
        if curated:
            rec["curated"] = {"clean_rows": curated["rows"], "removed": curated["removed"]}
        self.store.upsert_dataset(rec)
        return {"mode": "rows", **rec}

    # -- memory -----------------------------------------------------------
    def memory_add(self, text: str, source: Optional[str] = None) -> dict[str, Any]:
        from .models import new_uuid, now_ts
        mem = {"memory_id": "mem-" + new_uuid()[:16], "text": text,
               "source": source, "created_at": now_ts(), "metadata": {}}
        self.store.add_memory(mem)
        return mem

    def memory_query(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return self.store.query_memory(query, limit=limit)

    # -- stats (dashboards / website) -------------------------------------
    def stats(self) -> dict[str, Any]:
        s = self.store.stats()
        s["recent_jobs"] = self.store.recent_jobs(10)
        s["leaderboard"] = self.store.leaderboard(10)
        s["model_providers"] = list(self.cfg.model_providers.keys())
        from .payments import nano_to_anm
        s["demand_enabled"] = self.cfg.demand_enabled()
        s["anm_funded_total"] = round(nano_to_anm(self.store.total_funded_nano()), 6)
        s["jobs_awaiting_payment"] = s.get("jobs_by_status", {}).get("awaiting_payment", 0)
        s["generated_at"] = __import__("time").time()
        return s

    # -- worker -----------------------------------------------------------
    def worker(self, *, worker_id: str, types: Optional[list[str]] = None,
               endpoint: Optional[str] = None, poll_interval: float = 3.0):
        from .worker import ENAWorker
        if endpoint:
            return ENAWorker(worker_id=worker_id, endpoint=endpoint, types=types,
                             poll_interval=poll_interval)
        return ENAWorker(worker_id=worker_id, ena=self, types=types,
                         poll_interval=poll_interval)

    # -- service ----------------------------------------------------------
    def serve(self, host: str = "127.0.0.1", port: int = 8787) -> None:
        from . import service
        service.serve(self, host=host, port=port)

    def serve_model(self, pool_id: str, *, worker_id: str,
                    host: str = "127.0.0.1", port: int = 8799,
                    address: Optional[str] = None,
                    endpoint: Optional[str] = None) -> None:
        """Serve a pool's promoted checkpoint over an OpenAI-compatible API
        (serve-while-train); credits served tokens to the pool's server ledger.

        With ``endpoint`` set, the pool lives on a remote coordinator: the
        promoted checkpoint is fetched + cached locally and served-token credits
        are reported back over HTTP.
        """
        from . import serving
        if endpoint:
            from .remote import RemotePool, RemotePoolFacade, _ServingENA
            cache = self.cfg.artifacts_dir() / "served-cache"
            facade = RemotePoolFacade(RemotePool(endpoint), cache)
            serving.serve(_ServingENA(self, facade), pool_id, host=host, port=port,
                          worker_id=worker_id, address=address)
            return
        serving.serve(self, pool_id, host=host, port=port,
                      worker_id=worker_id, address=address)

    def train_shard_once(self, pool_id: str, worker_id: str, *,
                         address: Optional[str] = None,
                         backend: Optional[str] = None,
                         endpoint: Optional[str] = None) -> Optional[dict[str, Any]]:
        """Claim one pool shard, train it, and submit the checkpoint.

        Returns None when no shard is available; otherwise a result dict. Training
        failures (e.g. no torch on a CPU box) are reported, not raised, so a
        train-loop keeps running. With ``endpoint`` set, the pool lives on a
        remote coordinator: the shard is claimed + its data downloaded over HTTP,
        trained locally, then the checkpoint is uploaded and submitted back.
        """
        if endpoint:
            return self._train_shard_once_remote(
                pool_id, worker_id, address=address, backend=backend,
                endpoint=endpoint)
        import json as _json
        from . import training
        try:  # register as active so rounds size to all active miners
            self.pool.heartbeat(pool_id, worker_id)
        except Exception:  # noqa: BLE001
            pass
        claimed = self.pool.claim_shard(pool_id, worker_id)
        if not claimed:
            return None
        shard_id = claimed["shard_id"]
        manifest = claimed["manifest"]
        mpath = (self.cfg.artifacts_dir() / "pools" / pool_id /
                 f"{shard_id}-manifest.json")
        mpath.parent.mkdir(parents=True, exist_ok=True)
        mpath.write_text(_json.dumps(manifest), encoding="utf-8")
        run = training.run(self.cfg, self.store, manifest_path=str(mpath),
                           backend=backend or manifest.get("backend"))
        if run.get("status") != "completed":
            try:  # free the shard so another worker can take it right away
                self.pool.release_shard(pool_id, shard_id, worker_id)
            except Exception:  # noqa: BLE001
                pass
            return {"shard_id": shard_id, "status": "train_failed",
                    "error": run.get("error")}
        metrics = dict(run.get("metrics") or {})
        try:
            pool = self.pool.get(pool_id)
            ev_rows = self.curriculum._eval_rows(pool)
            topics = ((pool.get("metadata") or {}).get("curriculum") or {}
                      ).get("topics_seed") or []
            metrics.update(self._checkpoint_eval_metrics(
                manifest.get("base_model"), run.get("output_dir"), ev_rows, topics))
        except Exception:  # noqa: BLE001 - eval is best-effort
            pass
        res = self.pool.submit_shard(
            pool_id, shard_id, worker_id=worker_id, run_id=run["run_id"],
            checkpoint_path=run.get("output_dir"), metrics=metrics,
            miner_address=address)
        return {"shard_id": shard_id, "status": "submitted", **res}

    def _checkpoint_eval_metrics(self, base_model, checkpoint_path,
                                 eval_rows, topics) -> dict[str, Any]:
        """Best-effort trainer-side checkpoint eval → metrics the coordinator's
        gate + topic discovery consume. Empty dict on any failure / no GPU."""
        from pathlib import Path as _P
        out: dict[str, Any] = {}
        try:
            if not (checkpoint_path and _P(checkpoint_path).exists() and eval_rows):
                return out
            rep = self.curriculum.evaluate_checkpoint_detailed(
                base_model, checkpoint_path, eval_rows, topics or [])
            if rep.get("match_rate") is not None:
                out["eval_match_rate"] = rep["match_rate"]
                out["eval_topic_match_rate"] = rep.get("topic_match_rate") or {}
        except Exception:  # noqa: BLE001
            pass
        return out

    def _train_shard_once_remote(self, pool_id: str, worker_id: str, *,
                                 address: Optional[str], backend: Optional[str],
                                 endpoint: str) -> Optional[dict[str, Any]]:
        import json as _json
        import logging
        from pathlib import Path
        from . import training
        from .remote import RemotePool, tar_adapter_b64, write_b64_file
        log = logging.getLogger("animica.ena.trainer")

        rc = RemotePool(endpoint)
        try:  # register as active so rounds size to all active miners
            rc.heartbeat(pool_id, worker_id)
        except Exception:  # noqa: BLE001
            pass
        claimed = rc.claim(pool_id, worker_id)
        if not claimed:
            return None
        shard_id = claimed["shard_id"]
        manifest = dict(claimed.get("manifest") or {})
        pdir = self.cfg.artifacts_dir() / "pools" / pool_id
        pdir.mkdir(parents=True, exist_ok=True)

        # The claimed manifest points at the *coordinator's* shard file, which we
        # can't read. Pull the rows over HTTP, write them locally, and re-point
        # every dataset field in the manifest at the local copy.
        data = rc.download_shard(shard_id)
        local_data = pdir / (data.get("filename") or f"{shard_id}.jsonl")
        write_b64_file(data["content_b64"], local_data)
        manifest["train_dataset"] = str(local_data)
        manifest["train_sha256"] = data.get("sha256") or manifest.get("train_sha256")
        if isinstance(manifest.get("train"), dict):
            manifest["train"] = {**manifest["train"], "path": str(local_data)}
        out_dir = str(pdir / f"{shard_id}-output")
        manifest["output_dir"] = out_dir
        mpath = pdir / f"{shard_id}-manifest.json"
        mpath.write_text(_json.dumps(manifest), encoding="utf-8")

        run = training.run(self.cfg, self.store, manifest_path=str(mpath),
                           backend=backend or manifest.get("backend"))
        if run.get("status") != "completed":
            try:  # free the shard on the coordinator for the next worker
                rc.release(pool_id, shard_id, worker_id)
            except Exception:  # noqa: BLE001
                pass
            return {"shard_id": shard_id, "status": "train_failed",
                    "error": run.get("error")}

        # Best-effort: upload the trained weights so the coordinator can merge a
        # real adapter. Training still earns (receipt) if the upload fails.
        coord_ckpt: Optional[str] = None
        metrics = dict(run.get("metrics") or {})
        out = run.get("output_dir")
        if out and Path(out).exists():
            try:
                up = rc.upload_checkpoint(pool_id, shard_id, tar_adapter_b64(out))
                coord_ckpt = up.get("checkpoint_path")
            except Exception as exc:  # noqa: BLE001
                log.warning("[trainer] checkpoint upload failed (%s); "
                            "submitting receipt only", exc)
            # GPU trainer-side eval: score the checkpoint on the shared eval
            # split so the gate + topic discovery get a real number with zero
            # coordinator-side model load.
            try:
                ev = rc.download_eval(pool_id)
                metrics.update(self._checkpoint_eval_metrics(
                    manifest.get("base_model"), out, ev.get("rows") or [],
                    ev.get("topics") or []))
            except Exception as exc:  # noqa: BLE001
                log.warning("[trainer] checkpoint eval failed (%s); "
                            "submitting without a score", exc)

        res = rc.submit(pool_id, shard_id, worker_id=worker_id,
                        run_id=run["run_id"], checkpoint_path=coord_ckpt,
                        metrics=metrics, miner_address=address)
        return {"shard_id": shard_id, "status": "submitted", **(res or {})}

    # -- coding agent -----------------------------------------------------
    def code(self, task: str, *, workdir: str = ".", base_url: Optional[str] = None,
             model: Optional[str] = None, api_key_env: Optional[str] = None,
             model_provider: Optional[str] = None, max_steps: int = 24) -> dict[str, Any]:
        """Run the ENA-native coding agent on a task, against an OpenAI-compatible
        model (the pool's served model when ``base_url`` is given)."""
        from .coder import build_coder
        agent = build_coder(self.cfg, workdir=workdir, base_url=base_url, model=model,
                            api_key_env=api_key_env, model_provider=model_provider,
                            store=self.store, max_steps=max_steps)
        return agent.run_task(task)
