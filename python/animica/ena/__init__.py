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
        self.jobs = JobService(self.cfg, self.store)
        self.agent = Agent(self.cfg, self.store)
        self.demand = DemandService(self.cfg, self.store, self.jobs)
        self.walletconnect = WalletConnectService(self.cfg, self.store)

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
