from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .agent import AgentRunner
from .credits import EnaCreditsAdapter
from .datasets import DatasetManager
from .ingest import Crawler, Fetcher, export_jsonl, extract_local_path, load_seed_file, records_from_fetch
from .jobs import JobManager, WorkerEngine
from .models import EnaConfigModel, JobSpec, JobType, TaskSpec
from .providers import ProviderError, create_embedding_provider, create_model_provider
from .receipts import validate_receipt
from .retrieval import IndexManager, SKIP_DIRS
from .store import EnaStore
from .training import TrainingManager
from .text import stable_id, utc_now_iso


class EnaOperator:
    def __init__(self, store: EnaStore, config: EnaConfigModel):
        self.store = store
        self.config = config
        self.agent = AgentRunner(config=config, store=store)
        self.index = IndexManager(store, config)
        self.datasets = DatasetManager(store, config)
        self.jobs = JobManager(store, config)
        self.worker = WorkerEngine(store, config)
        self.training = TrainingManager(store, config)
        self.credits = EnaCreditsAdapter(store, config)

    def scrape_url(
        self,
        url: str,
        *,
        depth: int = 0,
        max_requests: int = 1,
        include_sitemap: bool = False,
        out: Optional[Path] = None,
        index_after: bool = False,
        index_name: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        fetcher = Fetcher(self.config.network)
        if depth > 0:
            records = Crawler(fetcher).crawl([url], max_depth=depth, max_requests=max_requests, discover_sitemaps=include_sitemap)
        else:
            records = records_from_fetch(fetcher.fetch(url))
        return self._persist_records(
            kind="scrape_records",
            records=records,
            out=out or (Path(self.config.storage.datasets_dir) / f"{stable_id('scrape', url)}.jsonl"),
            metadata={"url": url, "depth": depth, "max_requests": max_requests},
            index_after=index_after,
            index_name=index_name,
            embedding_provider=embedding_provider,
        )

    def scrape_batch(
        self,
        seed_file: Path,
        *,
        depth: int = 0,
        max_requests: int = 25,
        include_sitemap: bool = False,
        out: Optional[Path] = None,
        index_after: bool = False,
        index_name: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        urls = load_seed_file(seed_file)
        fetcher = Fetcher(self.config.network)
        if depth > 0:
            records = Crawler(fetcher).crawl(urls, max_depth=depth, max_requests=max_requests, discover_sitemaps=include_sitemap)
        else:
            records: List[Dict[str, Any]] = []
            for url in urls[:max_requests]:
                records.extend(records_from_fetch(fetcher.fetch(url)))
        return self._persist_records(
            kind="scrape_batch_records",
            records=records,
            out=out or (Path(self.config.storage.datasets_dir) / f"{seed_file.stem}.batch.jsonl"),
            metadata={"seed_file": str(seed_file.resolve()), "depth": depth, "max_requests": max_requests},
            index_after=index_after,
            index_name=index_name,
            embedding_provider=embedding_provider,
        )

    def scrape_crawl(
        self,
        root_url: str,
        *,
        depth: int = 2,
        max_requests: int = 50,
        include_sitemap: bool = False,
        out: Optional[Path] = None,
        index_after: bool = False,
        index_name: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        records = Crawler(Fetcher(self.config.network)).crawl(
            [root_url],
            max_depth=depth,
            max_requests=max_requests,
            discover_sitemaps=include_sitemap,
        )
        return self._persist_records(
            kind="crawl_records",
            records=records,
            out=out or (Path(self.config.storage.datasets_dir) / f"{stable_id('crawl', root_url)}.jsonl"),
            metadata={"root_url": root_url, "depth": depth, "max_requests": max_requests},
            index_after=index_after,
            index_name=index_name,
            embedding_provider=embedding_provider,
        )

    def ingest_file(
        self,
        path: Path,
        *,
        out: Optional[Path] = None,
        index_after: bool = False,
        index_name: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved = path.resolve()
        if resolved.suffix == ".jsonl":
            rows = [json.loads(line) for line in resolved.read_text(encoding="utf-8").splitlines() if line.strip()]
            return self._persist_records(
                kind="ingest_records",
                records=rows,
                out=out or (Path(self.config.storage.datasets_dir) / f"{resolved.stem}.ingest.jsonl"),
                metadata={"path": str(resolved)},
                index_after=index_after,
                index_name=index_name,
                embedding_provider=embedding_provider,
            )
        rows = extract_local_path(resolved)
        return self._persist_records(
            kind="ingest_records",
            records=rows,
            out=out or (Path(self.config.storage.datasets_dir) / f"{resolved.stem}.ingest.jsonl"),
            metadata={"path": str(resolved)},
            index_after=index_after,
            index_name=index_name,
            embedding_provider=embedding_provider,
        )

    def ingest_dir(
        self,
        path: Path,
        *,
        out: Optional[Path] = None,
        index_after: bool = False,
        index_name: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved = path.resolve()
        rows: List[Dict[str, Any]] = []
        for candidate in sorted(resolved.rglob("*")):
            if candidate.is_dir():
                continue
            if any(part in SKIP_DIRS for part in candidate.parts):
                continue
            try:
                rows.extend(extract_local_path(candidate))
            except Exception:
                continue
        return self._persist_records(
            kind="ingest_records",
            records=rows,
            out=out or (Path(self.config.storage.datasets_dir) / f"{resolved.name}.dir.ingest.jsonl"),
            metadata={"path": str(resolved)},
            index_after=index_after,
            index_name=index_name,
            embedding_provider=embedding_provider,
        )

    def extract_schema(
        self,
        sources: Sequence[str],
        *,
        schema: Dict[str, Any],
        instruction: str = "Extract the requested fields from the provided source.",
        model_provider: Optional[str] = None,
        model_name: Optional[str] = None,
        out: Optional[Path] = None,
    ) -> Dict[str, Any]:
        provider = create_model_provider(self.config, provider_name=model_provider)
        if model_name:
            provider.config = provider.config.model_copy(update={"model": model_name})
        records = self._records_from_sources(sources)
        output_rows: List[Dict[str, Any]] = []
        for index, record in enumerate(records, start=1):
            text = record.get("content_text") or record.get("summary") or record.get("answer") or ""
            if not text:
                continue
            extracted = provider.extract(text, schema, instruction=instruction)
            output_rows.append(
                {
                    "item_id": stable_id("extract", str(index), record.get("canonical_url") or record.get("url") or ""),
                    "source": record.get("canonical_url") or record.get("url") or record.get("path"),
                    "title": record.get("title"),
                    "extracted": extracted,
                    "provenance": record.get("provenance", {}),
                }
            )
        return self._persist_records(
            kind="schema_extract_records",
            records=output_rows,
            out=out or (Path(self.config.storage.datasets_dir) / f"{stable_id('extract_schema', *sources)}.jsonl"),
            metadata={"sources": list(sources), "schema": schema, "instruction": instruction, "model_provider": provider.provider_name},
        )

    def build_dataset(
        self,
        inputs: Sequence[Path],
        *,
        raw_out: Path,
        task_type: str = "summarize",
        dedupe: bool = True,
        split: bool = False,
        manifest_path: Optional[Path] = None,
    ) -> Dict[str, Any]:
        manifest = self.datasets.build_dataset(
            [item.resolve() for item in inputs],
            raw_out=raw_out,
            task_type=task_type,
            dedupe=dedupe,
            split=split,
            manifest_path=manifest_path,
        )
        artifact = None
        if manifest_path is not None and manifest_path.exists():
            artifact = self.store.put_artifact(
                "dataset_build_manifest",
                manifest_path.read_text(encoding="utf-8"),
                metadata={"inputs": [str(item.resolve()) for item in inputs]},
                suffix=".json",
            )
        return {
            **manifest,
            "artifact_id": artifact.artifact_id if artifact else None,
            "manifest_path": str(manifest_path.resolve()) if manifest_path else None,
        }

    def summarize(
        self,
        query: str,
        *,
        sources: Optional[Sequence[str]] = None,
        index_name: Optional[str] = None,
        model_provider: Optional[str] = None,
        model_name: Optional[str] = None,
        embedding_provider: Optional[str] = None,
        out: Optional[Path] = None,
    ) -> Dict[str, Any]:
        hits = []
        if sources:
            records = self._records_from_sources(sources)
            temp_jsonl = Path(self.config.storage.datasets_dir) / f"{stable_id('summary', query, *sources)}.jsonl"
            export_jsonl(records, temp_jsonl)
            build = self.index.index_jsonl_records(temp_jsonl, index_name=index_name, reset=True, embedding_provider_name=embedding_provider)
            index_name = build["index_name"]
        hits = self.index.search(query, index_name=index_name, strategy="hybrid", embedding_provider_name=embedding_provider)
        passages = [hit.excerpt for hit in hits]
        provider = create_model_provider(self.config, provider_name=model_provider)
        if model_name:
            provider.config = provider.config.model_copy(update={"model": model_name})
        summary = provider.summarize(query, passages) if passages else "No relevant evidence found."
        payload = {
            "query": query,
            "summary": summary,
            "citations": [hit.model_dump(mode="json") for hit in hits],
            "index_name": index_name,
            "created_at": utc_now_iso(),
        }
        if out is not None:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            payload["output_path"] = str(out.resolve())
        artifact = self.store.put_artifact("summary_result", json.dumps(payload, indent=2, ensure_ascii=False), metadata={"query": query}, suffix=".json")
        payload["artifact_id"] = artifact.artifact_id
        return payload

    def list_artifacts(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        return [artifact.model_dump(mode="json") for artifact in self.store.list_artifacts(limit=limit)]

    def show_artifact(self, artifact_id: str) -> Dict[str, Any]:
        artifact = self.store.get_artifact(artifact_id)
        if artifact is None:
            raise ValueError(f"artifact not found: {artifact_id}")
        preview = None
        path = Path(artifact.path)
        if path.exists():
            preview = path.read_text(encoding="utf-8", errors="ignore")[:2000]
        return {"artifact": artifact.model_dump(mode="json"), "preview": preview}

    def verify_artifact(self, artifact_id: str) -> Dict[str, Any]:
        return self.store.verify_artifact(artifact_id)

    def list_runs(self, *, limit: int = 50) -> List[Dict[str, Any]]:
        return [session.model_dump(mode="json") for session in self.store.list_sessions(limit=limit)]

    def show_run(self, session_id: str) -> Dict[str, Any]:
        session = self.store.get_session(session_id)
        if session is None:
            raise ValueError(f"run not found: {session_id}")
        return {
            "session": session.model_dump(mode="json"),
            "traces": [trace.model_dump(mode="json") for trace in self.store.list_traces(session_id)],
        }

    def doctor(self, *, check_model: bool = False, check_embeddings: bool = False) -> Dict[str, Any]:
        provider_checks: List[Dict[str, Any]] = []
        for name, provider_config in sorted(self.config.model_providers.items()):
            row: Dict[str, Any] = {
                "provider_name": name,
                "provider": provider_config.provider,
                "model": provider_config.model,
                "enabled": provider_config.enabled,
                "configured": bool(provider_config.model),
                "base_url": provider_config.base_url or provider_config.endpoint,
            }
            if provider_config.api_key_env_vars:
                row["api_key_present"] = any(bool(os.getenv(env_name)) for env_name in provider_config.api_key_env_vars)
            if check_model and provider_config.enabled:
                try:
                    row["test"] = create_model_provider(self.config, provider_name=name).test()
                except Exception as exc:  # noqa: BLE001
                    row["error"] = str(exc)
            provider_checks.append(row)

        embedding_checks: List[Dict[str, Any]] = []
        for name, provider_config in sorted(self.config.embedding_providers.items()):
            row = {
                "provider_name": name,
                "provider": provider_config.provider,
                "model": provider_config.model,
                "enabled": provider_config.enabled,
                "base_url": provider_config.base_url or provider_config.endpoint,
            }
            if check_embeddings and provider_config.enabled:
                try:
                    row["test"] = create_embedding_provider(self.config, provider_name=name).test()
                except Exception as exc:  # noqa: BLE001
                    row["error"] = str(exc)
            embedding_checks.append(row)

        storage_paths = [
            self.config.storage.home,
            self.config.storage.db_path,
            self.config.storage.artifacts_dir,
            self.config.storage.datasets_dir,
            self.config.storage.indexes_dir,
            self.config.storage.sessions_dir,
            self.config.storage.logs_dir,
            self.config.storage.manifests_dir,
            self.config.default_output_dir,
            self.config.aicf_db_path,
        ]
        directories = [{"path": str(path), "exists": Path(path).exists()} for path in storage_paths if path]
        ok = all(item["exists"] for item in directories)
        return {
            "ok": ok,
            "defaults": {
                "model_provider": self.config.default_model_provider,
                "embedding_provider": self.config.default_embedding_provider,
                "worker_id": self.config.default_worker_id,
                "miner_address": self.config.default_miner_address,
            },
            "directories": directories,
            "providers": provider_checks,
            "embeddings": embedding_checks,
            "counts": {
                "artifacts": len(self.store.list_artifacts(limit=500)),
                "indexes": len(self.store.list_indexes()),
                "jobs": len(self.jobs.list()),
                "receipts": len(self.store.list_receipts(limit=500)),
                "runs": len(self.store.list_sessions(limit=500)),
                "training_runs": len(self.store.list_training_runs(limit=500)),
            },
        }

    def verify(self, *, run_demo: bool = False) -> Dict[str, Any]:
        receipts = self.store.list_receipts(limit=100)
        receipt_checks = [validate_receipt(receipt) for receipt in receipts]
        artifact_checks = [self.store.verify_artifact(item.artifact_id) for item in self.store.list_artifacts(limit=50)]
        payload = {
            "ok": all(item.get("ok", False) for item in receipt_checks + artifact_checks) if (receipt_checks or artifact_checks) else True,
            "receipt_checks": receipt_checks,
            "artifact_checks": artifact_checks,
            "doctor": self.doctor(),
        }
        if run_demo:
            payload["demo"] = self.demo()
            payload["ok"] = payload["ok"] and payload["demo"].get("ok", False)
        return payload

    def demo(self, *, work_dir: Optional[Path] = None) -> Dict[str, Any]:
        base = (work_dir or Path(tempfile.mkdtemp(prefix="animica-ena-demo-"))).resolve()
        docs_dir = base / "docs"
        docs_dir.mkdir(parents=True, exist_ok=True)
        (docs_dir / "sync.md").write_text(
            "# Sync\n\nSync downloads headers first, validates ancestry, and fetches state as needed.\n",
            encoding="utf-8",
        )
        (docs_dir / "finality.md").write_text(
            "# Finality\n\nFinality confirms the chain head is stable enough to build on.\n",
            encoding="utf-8",
        )

        indexed = self.index.index_path(docs_dir, index_name="demo_docs", reset=True)
        search = [hit.model_dump(mode="json") for hit in self.index.search("stable chain head", index_name="demo_docs", strategy="keyword")]
        ask = self.agent.run(TaskSpec(task="What is finality?", context_paths=[str(docs_dir)], output_format="json"))
        job = self.jobs.create(JobSpec(job_type=JobType.EXTRACT, input_payload={}, sources=[str(docs_dir / "sync.md")], allowed_actions=["extract"]))
        finished = self.worker.execute(job)
        dataset_manifest = self.build_dataset([Path(finished.result["output_path"])], raw_out=base / "demo.raw.jsonl", manifest_path=base / "dataset_manifest.json")
        training_manifest_path = base / "train_manifest.json"
        train_prepare = self.training.prepare(
            Path(dataset_manifest["final_dataset_path"]),
            out_path=training_manifest_path,
            base_model="tiny-local-model",
            backend="command",
            auto_split=True,
            launcher={
                "command": [
                    sys.executable,
                    "-c",
                    "import json, pathlib, sys; out = pathlib.Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True); (out / 'metrics.json').write_text(json.dumps({'loss': 0.05, 'status': 'ok'}))",
                    "{output_dir}",
                ]
            },
        )
        training_run = self.training.run(training_manifest_path)
        return {
            "ok": True,
            "work_dir": str(base),
            "index": indexed,
            "search": search,
            "ask": ask,
            "job": finished.model_dump(mode="json"),
            "dataset": dataset_manifest,
            "training_manifest": train_prepare,
            "training_run": training_run.model_dump(mode="json"),
        }

    def credits_show(self, miner_address: Optional[str] = None, *, limit: int = 20) -> Dict[str, Any]:
        return self.credits.show(miner_address=miner_address, limit=limit)

    def mining_status(self, miner_address: Optional[str] = None) -> Dict[str, Any]:
        status = self.credits.mining_status(miner_address=miner_address)
        jobs = self.jobs.list()
        status["job_counts"] = {
            "proposed": len([job for job in jobs if job.status.value == "proposed"]),
            "claimed": len([job for job in jobs if job.status.value == "claimed"]),
            "running": len([job for job in jobs if job.status.value == "running"]),
            "verified": len([job for job in jobs if job.status.value == "verified"]),
        }
        return status

    def _records_from_sources(self, sources: Sequence[str]) -> List[Dict[str, Any]]:
        fetcher = Fetcher(self.config.network)
        rows: List[Dict[str, Any]] = []
        for source in sources:
            if source.startswith(("http://", "https://")):
                rows.extend(records_from_fetch(fetcher.fetch(source)))
            else:
                rows.extend(extract_local_path(Path(source).resolve()))
        return rows

    def _persist_records(
        self,
        *,
        kind: str,
        records: Sequence[Dict[str, Any]],
        out: Path,
        metadata: Dict[str, Any],
        index_after: bool = False,
        index_name: Optional[str] = None,
        embedding_provider: Optional[str] = None,
    ) -> Dict[str, Any]:
        out.parent.mkdir(parents=True, exist_ok=True)
        export_jsonl(records, out)
        artifact = self.store.put_artifact(kind, out.read_text(encoding="utf-8"), metadata=metadata, suffix=".jsonl")
        dataset = self.datasets.register(out, kind=kind, metadata={"artifact_id": artifact.artifact_id, **metadata})
        payload = {
            "artifact_id": artifact.artifact_id,
            "dataset_id": dataset.dataset_id,
            "output_path": str(out.resolve()),
            "rows": len(records),
        }
        if index_after:
            payload["index"] = self.index.index_jsonl_records(
                out,
                index_name=index_name,
                reset=True,
                embedding_provider_name=embedding_provider,
            )
        return payload
