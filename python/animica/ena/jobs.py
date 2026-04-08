from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from aicf.queue.jobkind import JobKind as AicfJobKind
from capabilities.jobs.id import derive_task_id_hex

from .credits import EnaCreditsAdapter
from .datasets import DatasetManager
from .ingest import Crawler, Fetcher, export_jsonl, extract_local_path, load_seed_file, records_from_fetch
from .models import EnaConfigModel, JobReceipt, JobRecord, JobSpec, JobStatus, JobType, VerificationCheck, VerificationRecord
from .providers import ProviderError, create_embedding_provider, create_model_provider
from .receipts import build_job_receipt, validate_receipt
from .retrieval import IndexManager, _chunk_path
from .store import EnaStore
from .text import keyword_terms, sha256_hex, sha3_hex, stable_id, summarize_passages, utc_now_iso


def _canonical_bytes(spec: JobSpec) -> bytes:
    spec_for_hash = spec.model_copy(update={"job_hash": ""})
    return spec_for_hash.canonical_json().encode("utf-8")


def _compute_job_hash(spec: JobSpec) -> str:
    return sha3_hex(_canonical_bytes(spec))


def _aicf_job_kind(job_type: JobType) -> AicfJobKind:
    if job_type in {
        JobType.SCRAPE,
        JobType.EXTRACT,
        JobType.CLEAN,
        JobType.DEDUPE,
        JobType.DATASET_BUILD,
        JobType.DATASET_CLEAN,
        JobType.TRAINING_RECORDS,
        JobType.SUMMARIZE,
    }:
        return AicfJobKind.DATA_CURATION
    if job_type in {JobType.CHUNK, JobType.EMBED, JobType.INDEX}:
        return AicfJobKind.RAG_INDEX_BUILD
    if job_type in {JobType.EVAL, JobType.VERIFY}:
        return AicfJobKind.EVAL_RUN
    if job_type in {JobType.LABEL, JobType.CLASSIFY}:
        return AicfJobKind.REWARD_MODEL_LABELING
    return AicfJobKind.DATA_CURATION


def _base_credit(job_type: JobType) -> int:
    return {
        JobType.SCRAPE: 120,
        JobType.EXTRACT: 120,
        JobType.CLEAN: 110,
        JobType.DEDUPE: 110,
        JobType.CHUNK: 110,
        JobType.LABEL: 150,
        JobType.CLASSIFY: 120,
        JobType.EMBED: 180,
        JobType.INDEX: 180,
        JobType.DATASET_BUILD: 160,
        JobType.EVAL: 160,
        JobType.VERIFY: 100,
        JobType.DATASET_CLEAN: 110,
        JobType.TRAINING_RECORDS: 130,
        JobType.TRAIN_PREPARE: 220,
        JobType.SUMMARIZE: 80,
    }.get(job_type, 100)


class JobManager:
    def __init__(self, store: EnaStore, config: EnaConfigModel):
        self.store = store
        self.config = config

    def propose(self, spec: JobSpec) -> JobRecord:
        working = spec.model_copy(deep=True)
        if not working.job_id:
            working.job_id = stable_id("job", working.canonical_json())
        working.job_hash = _compute_job_hash(working)
        aicf_task_id = derive_task_id_hex(
            chain_id=1,
            height=0,
            tx_hash=bytes.fromhex(sha256_hex(_canonical_bytes(working)))[:32],
            caller=working.created_by.encode("utf-8")[:32].ljust(32, b"0"),
            payload=working.model_dump(mode="json"),
        )
        record = JobRecord(
            job_id=working.job_id,
            job_hash=working.job_hash,
            job_type=working.job_type,
            spec=working,
            aicf_task_id=aicf_task_id,
        )
        self._save_record(record)
        self.store.add_job_event(
            record.job_id,
            "proposed",
            {
                "job_type": working.job_type.value,
                "job_hash": working.job_hash,
                "aicf_task_id": aicf_task_id,
            },
            record.created_at,
        )
        return record

    def create(self, spec: JobSpec) -> JobRecord:
        return self.propose(spec)

    def get(self, job_id: str) -> Optional[JobRecord]:
        row = self.store.get_job_row(job_id)
        if row is None:
            return None
        verification = None
        if row["verification_json"]:
            verification = VerificationRecord.model_validate(json.loads(row["verification_json"]))
        spec = JobSpec.model_validate(json.loads(row["spec_json"]))
        return JobRecord(
            job_id=row["job_id"],
            job_hash=row["job_hash"] or spec.job_hash,
            job_type=row["job_type"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            claimed_by=row["claimed_by"],
            aicf_task_id=row["aicf_task_id"],
            spec=spec,
            result=json.loads(row["result_json"]),
            verification=verification,
            reward=json.loads(row["reward_json"]),
        )

    def list(self, *, status: Optional[JobStatus] = None) -> List[JobRecord]:
        rows = self.store.list_job_rows(status.value if status else None)
        records: List[JobRecord] = []
        for row in rows:
            record = self.get(row["job_id"])
            if record is not None:
                records.append(record)
        return records

    def claim(self, worker_id: str, *, types: Optional[List[JobType]] = None) -> Optional[JobRecord]:
        allowed = {item.value for item in (types or [])}
        for job in self.list():
            if job.status != JobStatus.PROPOSED:
                continue
            if allowed and job.job_type.value not in allowed:
                continue
            job.status = JobStatus.CLAIMED
            job.claimed_by = worker_id
            job.updated_at = utc_now_iso()
            self._save_record(job)
            self.store.add_job_event(job.job_id, "claimed", {"worker_id": worker_id}, job.updated_at)
            return job
        return None

    def claim_job(self, job_id: str, worker_id: str) -> Optional[JobRecord]:
        job = self.get(job_id)
        if job is None or job.status != JobStatus.PROPOSED:
            return job
        job.status = JobStatus.CLAIMED
        job.claimed_by = worker_id
        job.updated_at = utc_now_iso()
        self._save_record(job)
        self.store.add_job_event(job.job_id, "claimed", {"worker_id": worker_id}, job.updated_at)
        return job

    def submit(self, job: JobRecord, result: Dict[str, Any]) -> JobRecord:
        job.status = JobStatus.SUBMITTED
        job.result = result
        job.updated_at = utc_now_iso()
        self._save_record(job)
        self.store.add_job_event(job.job_id, "submitted", result, job.updated_at)
        return job

    def complete(self, job: JobRecord, result: Dict[str, Any]) -> JobRecord:
        job.status = JobStatus.COMPLETED
        job.result = result
        job.updated_at = utc_now_iso()
        self._save_record(job)
        self.store.add_job_event(job.job_id, "completed", result, job.updated_at)
        return job

    def fail(self, job: JobRecord, message: str) -> JobRecord:
        job.status = JobStatus.FAILED
        job.result = {"error": message}
        job.updated_at = utc_now_iso()
        self._save_record(job)
        self.store.add_job_event(job.job_id, "failed", {"error": message}, job.updated_at)
        return job

    def verify(self, job: JobRecord) -> JobRecord:
        checks: List[VerificationCheck] = []
        passed = True
        result = job.result
        output_path = result.get("output_path")
        output_artifact_id = result.get("artifact_id")
        checks.append(
            VerificationCheck(
                name="artifact_present",
                passed=bool(output_artifact_id or output_path),
                detail=output_path or output_artifact_id,
            )
        )

        if output_path and Path(output_path).exists():
            size_ok = Path(output_path).stat().st_size > 0
            checks.append(
                VerificationCheck(
                    name="output_nonempty",
                    passed=size_ok,
                    detail=str(Path(output_path).stat().st_size),
                )
            )
            passed = passed and size_ok
        elif output_path:
            checks.append(VerificationCheck(name="output_exists", passed=False, detail=output_path))
            passed = False

        if job.job_type in {JobType.SCRAPE, JobType.EXTRACT} and output_path and Path(output_path).exists():
            rows = [json.loads(line) for line in Path(output_path).read_text(encoding="utf-8").splitlines() if line.strip()]
            provenance_ok = all(row.get("provenance") for row in rows)
            checks.append(VerificationCheck(name="provenance", passed=provenance_ok))
            if job.spec.sources:
                sources_ok = True
                allowed_domains = set(self.config.network.allow_domains)
                for row in rows:
                    source = row.get("canonical_url") or row.get("url") or ""
                    if not source:
                        continue
                    host = source.split("/")[2] if "://" in source else source
                    if allowed_domains and not any(host == domain or host.endswith(f".{domain}") for domain in allowed_domains):
                        sources_ok = False
                        break
                checks.append(VerificationCheck(name="domain_policy", passed=sources_ok))
                passed = passed and sources_ok
            passed = passed and provenance_ok

        if job.job_type in {
            JobType.CHUNK,
            JobType.LABEL,
            JobType.CLASSIFY,
            JobType.EMBED,
            JobType.CLEAN,
            JobType.DEDUPE,
            JobType.DATASET_BUILD,
            JobType.TRAINING_RECORDS,
            JobType.DATASET_CLEAN,
        } and output_path and Path(output_path).exists():
            row_count = sum(1 for line in Path(output_path).open("r", encoding="utf-8") if line.strip())
            checks.append(VerificationCheck(name="row_count_positive", passed=row_count > 0, detail=str(row_count)))
            passed = passed and row_count > 0

        if job.job_type == JobType.INDEX:
            checks.append(VerificationCheck(name="index_name_present", passed=bool(result.get("index_name"))))
            checks.append(
                VerificationCheck(
                    name="embedding_provider_present",
                    passed=bool(result.get("embedding_provider")),
                    detail=str(result.get("embedding_provider")),
                )
            )
            passed = passed and bool(result.get("index_name"))

        if job.job_type == JobType.EMBED:
            dims = result.get("dimensions", 0)
            checks.append(VerificationCheck(name="embedding_dimensions", passed=bool(dims), detail=str(dims)))
            passed = passed and bool(dims)

        if job.job_type in {JobType.LABEL, JobType.CLASSIFY}:
            checks.append(
                VerificationCheck(
                    name="labels_present",
                    passed=bool(result.get("labels")),
                    detail=json.dumps(result.get("labels", [])),
                )
            )
            passed = passed and bool(result.get("labels"))

        if job.job_type == JobType.SUMMARIZE:
            summary_ok = bool(result.get("summary"))
            checks.append(VerificationCheck(name="summary_present", passed=summary_ok))
            passed = passed and summary_ok

        if job.job_type == JobType.TRAIN_PREPARE and output_path and Path(output_path).exists():
            manifest = json.loads(Path(output_path).read_text(encoding="utf-8"))
            manifest_ok = bool(manifest.get("train") and manifest.get("base_model"))
            checks.append(VerificationCheck(name="manifest_shape", passed=manifest_ok))
            passed = passed and manifest_ok

        score = sum(1.0 for check in checks if check.passed) / max(len(checks), 1)
        verification = VerificationRecord(
            verification_id=stable_id("verify", job.job_id, str(time.time())),
            target_id=job.job_id,
            target_type="job",
            passed=passed,
            score=score,
            checks=checks,
            metadata={"job_type": job.job_type.value, "job_hash": job.job_hash or job.spec.job_hash},
        )
        job.verification = verification
        job.status = JobStatus.VERIFIED if passed else JobStatus.REJECTED
        job.updated_at = verification.created_at
        job.reward = self.score(job)
        self._save_record(job)
        self.store.add_job_event(job.job_id, "verified", verification.model_dump(mode="json"), verification.created_at)
        self.store.save_verification(verification)
        receipt = self.receipt(job.job_id, force=True)
        if receipt is not None:
            self.store.add_job_event(
                job.job_id,
                "receipt",
                {"receipt_hash": receipt.receipt_hash, "receipt_id": receipt.receipt_id},
                receipt.created_at,
            )
        return job

    def score(self, job: JobRecord) -> Dict[str, Any]:
        verification_score = job.verification.score if job.verification else 0.0
        credits = int(_base_credit(job.job_type) * verification_score)
        return {
            "credits": credits,
            "verification_score": verification_score,
            "aicf_job_kind": _aicf_job_kind(job.job_type).value,
            "reward_route": job.spec.reward_routing or {"worker": job.claimed_by, "pool": "aicf"},
            "credit_event_candidate": sha3_hex(f"{job.job_id}:{credits}:{job.aicf_task_id or ''}"),
        }

    def receipt(self, job_id: str, *, force: bool = False) -> Optional[JobReceipt]:
        existing = self.store.get_receipt(job_id)
        if existing is not None and not force:
            return existing
        job = self.get(job_id)
        if job is None:
            return None
        if job.status not in {JobStatus.SUBMITTED, JobStatus.COMPLETED, JobStatus.VERIFIED, JobStatus.REJECTED}:
            return None
        receipt = build_job_receipt(job, store=self.store)
        self.store.save_receipt(receipt)
        EnaCreditsAdapter(self.store, self.config).apply_receipt(receipt)
        return receipt

    def export_onchain(self, job_id: str) -> Optional[Dict[str, Any]]:
        receipt = self.receipt(job_id)
        if receipt is None:
            return None
        validation = validate_receipt(receipt)
        payload = {
            "receipt": receipt.model_dump(mode="json"),
            "validation": validation,
            "onchain": receipt.onchain_payload,
        }
        self.store.put_artifact(
            "job_onchain_export",
            json.dumps(payload, indent=2, ensure_ascii=False),
            metadata={"job_id": job_id, "receipt_hash": receipt.receipt_hash},
            suffix=".json",
        )
        return payload

    def _save_record(self, job: JobRecord) -> None:
        self.store.save_job(
            job.job_id,
            job_hash=job.job_hash or job.spec.job_hash,
            job_type=job.job_type.value,
            status=job.status.value,
            created_at=job.created_at,
            updated_at=job.updated_at,
            claimed_by=job.claimed_by,
            aicf_task_id=job.aicf_task_id,
            spec_json=job.spec.model_dump(mode="json"),
            result_json=job.result,
            verification_json=job.verification.model_dump(mode="json") if job.verification else None,
            reward_json=job.reward,
        )


class WorkerEngine:
    def __init__(self, store: EnaStore, config: EnaConfigModel):
        self.store = store
        self.config = config
        self.jobs = JobManager(store, config)
        self.index = IndexManager(store, config)
        self.datasets = DatasetManager(store, config)

    def run_claimed(self, worker_id: str, *, types: Optional[List[JobType]] = None, limit: int = 1) -> List[JobRecord]:
        completed: List[JobRecord] = []
        for _ in range(limit):
            job = self.jobs.claim(worker_id, types=types)
            if job is None:
                break
            completed.append(self.execute(job))
        return completed

    def execute(self, job: JobRecord) -> JobRecord:
        job.status = JobStatus.RUNNING
        job.updated_at = utc_now_iso()
        self.jobs._save_record(job)
        self.store.add_job_event(job.job_id, "running", {"worker_id": job.claimed_by}, job.updated_at)
        try:
            handler = {
                JobType.SCRAPE: self._run_scrape,
                JobType.EXTRACT: self._run_extract,
                JobType.CLEAN: self._run_dataset_clean,
                JobType.DEDUPE: self._run_dataset_clean,
                JobType.CHUNK: self._run_chunk,
                JobType.LABEL: self._run_label,
                JobType.CLASSIFY: self._run_label,
                JobType.EMBED: self._run_embed,
                JobType.INDEX: self._run_index,
                JobType.SUMMARIZE: self._run_summarize,
                JobType.EVAL: self._run_eval,
                JobType.VERIFY: self._run_verify,
                JobType.DATASET_BUILD: self._run_dataset_build,
                JobType.DATASET_CLEAN: self._run_dataset_clean,
                JobType.TRAINING_RECORDS: self._run_training_records,
                JobType.TRAIN_PREPARE: self._run_train_prepare,
            }.get(job.job_type)
            if handler is None:
                raise RuntimeError(f"unsupported job type: {job.job_type.value}")
            result = handler(job.spec)
            finished = self.jobs.complete(job, result)
            return self.jobs.verify(finished)
        except Exception as exc:  # noqa: BLE001
            return self.jobs.fail(job, str(exc))

    def _run_scrape(self, spec: JobSpec) -> Dict[str, Any]:
        fetcher = Fetcher(self.config.network)
        crawler = Crawler(fetcher)
        seeds = spec.sources[:]
        if spec.input_payload.get("seed_file"):
            seeds.extend(load_seed_file(Path(spec.input_payload["seed_file"])))
        if spec.input_payload.get("url"):
            seeds.append(spec.input_payload["url"])
        if not seeds:
            raise ValueError("scrape job requires at least one source URL")
        records = crawler.crawl(
            seeds,
            max_depth=int(spec.input_payload.get("max_depth", self.config.network.max_depth)),
            max_requests=int(spec.input_payload.get("max_requests", self.config.network.max_requests)),
        )
        out_path = Path(self.config.storage.datasets_dir) / f"{spec.job_id}.jsonl"
        export_jsonl(records, out_path)
        artifact = self.store.put_artifact(
            "scrape_records",
            out_path.read_text(encoding="utf-8"),
            metadata={"job_id": spec.job_id},
            suffix=".jsonl",
        )
        self.datasets.register(out_path, kind="scrape_records", metadata={"job_id": spec.job_id})
        return {"artifact_id": artifact.artifact_id, "output_path": str(out_path), "rows": len(records)}

    def _run_extract(self, spec: JobSpec) -> Dict[str, Any]:
        fetcher = Fetcher(self.config.network)
        records: List[Dict[str, Any]] = []
        for source in spec.sources:
            if source.startswith("http://") or source.startswith("https://"):
                records.extend(records_from_fetch(fetcher.fetch(source)))
            else:
                records.extend(extract_local_path(Path(source)))
        if spec.input_payload.get("path"):
            records.extend(extract_local_path(Path(spec.input_payload["path"])))
        out_path = Path(self.config.storage.datasets_dir) / f"{spec.job_id}.jsonl"
        export_jsonl(records, out_path)
        artifact = self.store.put_artifact(
            "extract_records",
            out_path.read_text(encoding="utf-8"),
            metadata={"job_id": spec.job_id},
            suffix=".jsonl",
        )
        self.datasets.register(out_path, kind="extract_records", metadata={"job_id": spec.job_id})
        return {"artifact_id": artifact.artifact_id, "output_path": str(out_path), "rows": len(records)}

    def _run_chunk(self, spec: JobSpec) -> Dict[str, Any]:
        target_value = spec.input_payload.get("path") or spec.input_payload.get("dataset") or (spec.sources[0] if spec.sources else "")
        if not target_value:
            raise ValueError("chunk job requires path or dataset")
        target = Path(target_value)
        chunk_rows: List[Dict[str, Any]] = []
        if target.suffix == ".jsonl":
            with target.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    text = record.get("content_text") or record.get("content") or record.get("answer") or ""
                    fake_path = Path(record.get("path") or f"{target.stem}-{line_number}.txt")
                    chunk_rows.extend(_chunk_path(fake_path, chunk_lines=int(spec.input_payload.get("chunk_lines", 80))))
        else:
            chunk_rows = _chunk_path(target, chunk_lines=int(spec.input_payload.get("chunk_lines", 80)))
        out_path = Path(self.config.storage.datasets_dir) / f"{spec.job_id}.chunks.jsonl"
        export_jsonl(chunk_rows, out_path)
        artifact = self.store.put_artifact(
            "chunk_records",
            out_path.read_text(encoding="utf-8"),
            metadata={"job_id": spec.job_id},
            suffix=".jsonl",
        )
        return {"artifact_id": artifact.artifact_id, "output_path": str(out_path), "rows": len(chunk_rows)}

    def _run_label(self, spec: JobSpec) -> Dict[str, Any]:
        labels = list(spec.input_payload.get("labels") or [])
        if not labels:
            raise ValueError("label job requires labels")
        model_provider_name = spec.input_payload.get("model_provider") or self.config.default_model_provider
        model = create_model_provider(self.config, provider_name=model_provider_name)
        rows = self._load_text_rows(spec)
        output_rows: List[Dict[str, Any]] = []
        for index, row in enumerate(rows, start=1):
            classified = model.classify(row["text"], labels)
            output_rows.append(
                {
                    "item_id": row["item_id"] or stable_id("label", spec.job_id, str(index)),
                    "text": row["text"],
                    "label": classified["label"],
                    "reason": classified["reason"],
                    "labels": labels,
                    "provenance": row["provenance"],
                }
            )
        out_path = Path(self.config.storage.datasets_dir) / f"{spec.job_id}.labels.jsonl"
        export_jsonl(output_rows, out_path)
        artifact = self.store.put_artifact(
            "label_records",
            out_path.read_text(encoding="utf-8"),
            metadata={"job_id": spec.job_id, "model_provider": model_provider_name},
            suffix=".jsonl",
        )
        return {
            "artifact_id": artifact.artifact_id,
            "output_path": str(out_path),
            "rows": len(output_rows),
            "labels": labels,
            "model_provider": model_provider_name,
        }

    def _run_embed(self, spec: JobSpec) -> Dict[str, Any]:
        provider_name = spec.input_payload.get("embedding_provider") or self.config.default_embedding_provider
        provider = create_embedding_provider(self.config, provider_name=provider_name)
        if not provider.capabilities().get("semantic"):
            raise ProviderError(f"embedding provider {provider_name} does not support semantic embeddings")
        rows = self._load_text_rows(spec)
        vectors = provider.embed_texts([row["text"] for row in rows])
        output_rows: List[Dict[str, Any]] = []
        for row, vector in zip(rows, vectors):
            output_rows.append(
                {
                    "item_id": row["item_id"],
                    "text": row["text"],
                    "embedding": vector,
                    "dimensions": len(vector),
                    "provenance": row["provenance"],
                }
            )
        out_path = Path(self.config.storage.datasets_dir) / f"{spec.job_id}.embeddings.jsonl"
        export_jsonl(output_rows, out_path)
        artifact = self.store.put_artifact(
            "embedding_records",
            out_path.read_text(encoding="utf-8"),
            metadata={"job_id": spec.job_id, "embedding_provider": provider_name},
            suffix=".jsonl",
        )
        dimensions = len(vectors[0]) if vectors else 0
        return {
            "artifact_id": artifact.artifact_id,
            "output_path": str(out_path),
            "rows": len(output_rows),
            "dimensions": dimensions,
            "embedding_provider": provider_name,
        }

    def _run_index(self, spec: JobSpec) -> Dict[str, Any]:
        target_value = spec.input_payload.get("path") or spec.input_payload.get("dataset") or (spec.sources[0] if spec.sources else "")
        if not target_value:
            raise ValueError("index job requires path or dataset")
        target = Path(target_value)
        provider_name = spec.input_payload.get("embedding_provider")
        if target.suffix == ".jsonl":
            result = self.index.index_jsonl_records(
                target,
                index_name=spec.input_payload.get("index_name"),
                reset=True,
                embedding_provider_name=provider_name,
            )
        else:
            result = self.index.index_path(
                target,
                index_name=spec.input_payload.get("index_name"),
                reset=True,
                embedding_provider_name=provider_name,
            )
        artifact = self.store.put_artifact(
            "index_result",
            json.dumps(result, indent=2),
            metadata={"job_id": spec.job_id},
            suffix=".json",
        )
        result["artifact_id"] = artifact.artifact_id
        return result

    def _run_summarize(self, spec: JobSpec) -> Dict[str, Any]:
        query = spec.input_payload.get("query") or " ".join(keyword_terms(json.dumps(spec.input_payload, ensure_ascii=False)))
        if spec.input_payload.get("path"):
            index_result = self.index.index_path(
                Path(spec.input_payload["path"]),
                reset=False,
                embedding_provider_name=spec.input_payload.get("embedding_provider"),
            )
            index_name = index_result["index_name"]
        else:
            index_name = spec.input_payload.get("index_name")
        strategy = spec.input_payload.get("search_mode") or "hybrid"
        hits = self.index.search(
            query,
            index_name=index_name,
            limit=int(spec.input_payload.get("limit", 6)),
            strategy=strategy,
            embedding_provider_name=spec.input_payload.get("embedding_provider"),
        )
        passages = [hit.excerpt for hit in hits]
        summary = ""
        model_provider_name = spec.input_payload.get("model_provider")
        if model_provider_name:
            try:
                model = create_model_provider(self.config, provider_name=model_provider_name)
                summary = model.summarize(query, passages)
            except Exception:
                summary = ""
        if not summary:
            summary_lines = summarize_passages(query, passages, max_sentences=5)
            summary = " ".join(summary_lines)
        payload = {
            "query": query,
            "summary": summary,
            "citations": [hit.model_dump(mode="json") for hit in hits],
            "search_mode": strategy,
        }
        out_path = Path(self.config.default_output_dir) / f"{spec.job_id}.summary.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        artifact = self.store.put_artifact(
            "summary",
            out_path.read_text(encoding="utf-8"),
            metadata={"job_id": spec.job_id},
            suffix=".json",
        )
        payload["artifact_id"] = artifact.artifact_id
        payload["output_path"] = str(out_path)
        return payload

    def _run_eval(self, spec: JobSpec) -> Dict[str, Any]:
        dataset_path = Path(spec.input_payload["dataset"])
        valid = 0
        exact_matches = 0
        total = 0
        for line in dataset_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            total += 1
            row = json.loads(line)
            prompt = row.get("prompt") or row.get("input_text")
            expected = row.get("expected") or row.get("output_text")
            output = row.get("answer") or row.get("output_text")
            if prompt:
                valid += 1
            if expected and output and str(expected).strip() == str(output).strip():
                exact_matches += 1
        summary = {
            "dataset": str(dataset_path),
            "rows": total,
            "valid_rows": valid,
            "exact_match_rate": (exact_matches / total) if total else 0.0,
        }
        out_path = Path(self.config.default_output_dir) / f"{spec.job_id}.eval.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        artifact = self.store.put_artifact(
            "eval_report",
            out_path.read_text(encoding="utf-8"),
            metadata={"job_id": spec.job_id},
            suffix=".json",
        )
        self.store.save_eval_run(
            stable_id("eval", spec.job_id),
            spec.input_payload.get("suite_name", "dataset_stats"),
            str(out_path),
            summary,
            utc_now_iso(),
        )
        summary["artifact_id"] = artifact.artifact_id
        summary["output_path"] = str(out_path)
        return summary

    def _run_verify(self, spec: JobSpec) -> Dict[str, Any]:
        target_job = self.jobs.get(spec.input_payload["job_id"])
        if target_job is None:
            raise ValueError("target job not found")
        verified = self.jobs.verify(target_job)
        return verified.verification.model_dump(mode="json") if verified.verification else {}

    def _run_dataset_clean(self, spec: JobSpec) -> Dict[str, Any]:
        input_path = Path(spec.input_payload["dataset"])
        out_path = Path(self.config.storage.datasets_dir) / f"{spec.job_id}.deduped.jsonl"
        return self.datasets.dedupe(input_path, out_path)

    def _run_dataset_build(self, spec: JobSpec) -> Dict[str, Any]:
        inputs = [Path(item) for item in spec.sources]
        if spec.input_payload.get("dataset"):
            inputs.append(Path(spec.input_payload["dataset"]))
        if spec.input_payload.get("path"):
            inputs.append(Path(spec.input_payload["path"]))
        if not inputs:
            raise ValueError("dataset build job requires one or more input paths")
        raw_out = Path(self.config.storage.datasets_dir) / f"{spec.job_id}.raw.jsonl"
        manifest_path = Path(self.config.storage.manifests_dir) / f"{spec.job_id}.dataset.json"
        manifest = self.datasets.build_dataset(
            inputs,
            raw_out=raw_out,
            task_type=spec.input_payload.get("task_type", "summarize"),
            dedupe=bool(spec.input_payload.get("dedupe", True)),
            split=bool(spec.input_payload.get("split", False)),
            manifest_path=manifest_path,
        )
        artifact = self.store.put_artifact(
            "dataset_build_manifest",
            manifest_path.read_text(encoding="utf-8"),
            metadata={"job_id": spec.job_id},
            suffix=".json",
        )
        manifest["artifact_id"] = artifact.artifact_id
        manifest["output_path"] = manifest["final_dataset_path"]
        return manifest

    def _run_training_records(self, spec: JobSpec) -> Dict[str, Any]:
        input_path = Path(spec.input_payload["dataset"])
        out_path = Path(self.config.storage.datasets_dir) / f"{spec.job_id}.train.jsonl"
        return self.datasets.normalize(input_path, out_path, task_type=spec.input_payload.get("task_type", "summarize"))

    def _run_train_prepare(self, spec: JobSpec) -> Dict[str, Any]:
        dataset_path = Path(spec.input_payload["dataset"])
        eval_dataset = Path(spec.input_payload["eval_dataset"]) if spec.input_payload.get("eval_dataset") else None
        out_path = Path(self.config.storage.manifests_dir) / f"{spec.job_id}.train_manifest.json"
        manifest = self.datasets.training_manifest(
            dataset_path,
            out_path=out_path,
            eval_dataset_path=eval_dataset,
            metadata={
                "job_id": spec.job_id,
                "base_model": spec.input_payload.get("base_model", "unknown"),
                "backend": spec.input_payload.get("backend", "command"),
            },
            base_model=spec.input_payload.get("base_model", "unknown"),
            backend=spec.input_payload.get("backend", "command"),
        )
        artifact = self.store.put_artifact(
            "train_manifest",
            out_path.read_text(encoding="utf-8"),
            metadata={"job_id": spec.job_id},
            suffix=".json",
        )
        manifest["artifact_id"] = artifact.artifact_id
        manifest["output_path"] = str(out_path)
        return manifest

    def _load_text_rows(self, spec: JobSpec) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if spec.input_payload.get("texts"):
            for index, text in enumerate(spec.input_payload.get("texts", []), start=1):
                rows.append(
                    {
                        "item_id": stable_id("text", spec.job_id, str(index)),
                        "text": str(text),
                        "provenance": {"input": "inline"},
                    }
                )
            return rows

        target_value = spec.input_payload.get("dataset") or spec.input_payload.get("path") or (spec.sources[0] if spec.sources else "")
        if not target_value:
            raise ValueError("job requires dataset, path, source, or texts")
        target = Path(target_value)
        if target.suffix == ".jsonl":
            text_field = spec.input_payload.get("text_field", "content_text")
            with target.open("r", encoding="utf-8") as handle:
                for line_number, line in enumerate(handle, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    text = record.get(text_field) or record.get("content") or record.get("answer") or ""
                    if text:
                        rows.append(
                            {
                                "item_id": record.get("item_id") or stable_id("row", spec.job_id, str(line_number)),
                                "text": str(text),
                                "provenance": {"path": str(target), "line_number": line_number},
                            }
                        )
        else:
            for record in extract_local_path(target):
                text = record.get("content_text") or record.get("summary") or record.get("answer") or ""
                if text:
                    rows.append(
                        {
                            "item_id": stable_id("path", spec.job_id, str(target)),
                            "text": str(text),
                            "provenance": {"path": str(target)},
                        }
                    )
        return rows
