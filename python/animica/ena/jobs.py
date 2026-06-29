"""
animica.ena.jobs
================

Useful-work job lifecycle: ``proposed → claimed → running → completed/submitted
→ verified/rejected → receipt → export-onchain``.

Every job carries a deterministic ``job_id`` (id of the canonical spec), a
``job_hash`` (SHA3 of the canonical spec with ``job_hash`` cleared), and an
``aicf_task_id`` (AICF-compatible task id over the spec payload), so local
receipts and downstream AICF queue integration are reproducible.

Local execution (``run``) is supported for the CPU-friendly job types; results
produced elsewhere are attached via ``submit``. Both paths converge on
``verify`` → ``receipt`` → ``export-onchain``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from . import datasets as ds
from . import receipts as rc
from . import retrieval
from .errors import JobError
from .models import (
    Schema, canonical_json, derive_aicf_task_id, hash_obj, new_uuid, now_ts, sha3_hex,
)

JOB_TYPES = (
    "scrape", "extract", "chunk", "label", "embed", "index", "summarize",
    "eval", "dataset_clean", "training_records", "train_prepare",
    "synthesize_qa",
)

# Job types whose executor calls a generative model and therefore MUST run on
# the WORKER's local runtime (LOCAL_INFERENCE_URL), never on the coordinator in
# its request path. The coordinator refuses to execute these in ``run`` unless
# the caller is the worker-local path (``allow_worker_local=True``); workers
# detect membership here and self-route to local execution + ``submit_result``.
WORKER_LOCAL_JOB_TYPES = frozenset({"synthesize_qa"})

# Lifecycle states (docs/ena/useful-work-jobs.md).
STATUS_PROPOSED = "proposed"
STATUS_CLAIMED = "claimed"
STATUS_RUNNING = "running"
STATUS_SUBMITTED = "submitted"
STATUS_COMPLETED = "completed"
STATUS_VERIFIED = "verified"
STATUS_REJECTED = "rejected"


def _canonical_spec(job_type: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"job_type": job_type, "params": params}


def derive_job_ids(job_type: str, params: dict[str, Any]) -> tuple[str, str, str]:
    """Return (job_id, job_hash, aicf_task_id) for a canonical spec."""
    spec = _canonical_spec(job_type, params)
    job_id = "ena-" + hash_obj(spec)[:32]
    job_hash = sha3_hex(canonical_json({**spec, "job_id": job_id, "job_hash": ""}))
    aicf_task_id = derive_aicf_task_id(spec)
    return job_id, job_hash, aicf_task_id


class JobService:
    def __init__(self, cfg, store) -> None:
        self.cfg = cfg
        self.store = store
        self._model = None
        self._embedder = None

    # -- lazy adapters ----------------------------------------------------
    def model(self, provider: Optional[str] = None):
        from .providers import build_model_adapter
        return build_model_adapter(self.cfg.model_provider(provider))

    def embedder(self, provider: Optional[str] = None):
        from .providers import build_embedding_adapter
        return build_embedding_adapter(self.cfg.embedding_provider(provider))

    def _artifacts_dir(self, job_id: str) -> Path:
        d = self.cfg.artifacts_dir() / job_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _write_artifact(self, job_id: str, name: str, content: str) -> dict[str, Any]:
        path = self._artifacts_dir(job_id) / name
        path.write_text(content, encoding="utf-8")
        return {"artifact_id": "art-" + new_uuid()[:16], "path": str(path),
                "name": name, "hash": sha3_hex(content)}

    # -- create -----------------------------------------------------------
    def create(self, job_type: str, params: dict[str, Any],
               requester: Optional[str] = None) -> dict[str, Any]:
        if job_type not in JOB_TYPES:
            raise JobError(f"unknown job type: {job_type}",
                          hint=f"one of: {', '.join(JOB_TYPES)}")
        job_id, job_hash, task_id = derive_job_ids(job_type, params)
        ts = now_ts()
        job = {
            "job_id": job_id, "job_hash": job_hash, "job_type": job_type,
            "aicf_task_id": task_id, "aicf_job_kind": rc.aicf_kind_for(job_type),
            "status": STATUS_PROPOSED, "params": params, "requester": requester,
            "worker_id": None, "provider_id": None,
            "result": None, "result_hash": None, "artifacts": [],
            "verification": None, "created_at": ts, "updated_at": ts,
        }
        self.store.upsert_job(job)
        return job

    def get(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if not job:
            raise JobError(f"job not found: {job_id}")
        return job

    def list(self, status: Optional[str] = None,
             job_type: Optional[str] = None) -> list[dict[str, Any]]:
        return self.store.list_jobs(status=status, job_type=job_type)

    def claim(self, worker_id: str,
              job_types: Optional[list[str]] = None) -> Optional[dict[str, Any]]:
        return self.store.claim_one_job(worker_id, job_types, STATUS_CLAIMED)

    # -- run --------------------------------------------------------------
    def run(self, job_id: Optional[str] = None, *, worker_id: Optional[str] = None,
            job_types: Optional[list[str]] = None,
            allow_worker_local: bool = False) -> dict[str, Any]:
        """Execute a job locally. With ``worker_id`` and no ``job_id``, claims
        the next eligible ``proposed`` job first.

        Worker-local job types (e.g. ``synthesize_qa``) call a generative model
        and must run on the worker, not on the coordinator in-request. This
        method refuses to execute them unless ``allow_worker_local=True`` (set
        only by the worker's local-exec path), so the coordinator's
        ``POST /jobs/<id>/run`` route can never peg its own model. The worker
        runs them locally and returns the result via ``submit_result``.
        """
        if job_id is None:
            if not worker_id:
                raise JobError("run needs either a job_id or a worker_id to claim")
            job = self.claim(worker_id, job_types)
            if not job:
                raise JobError("no claimable jobs")
        else:
            job = self.get(job_id)
            if job["status"] in (STATUS_PROPOSED,) and worker_id:
                job["worker_id"] = worker_id
        if job["job_type"] in WORKER_LOCAL_JOB_TYPES and not allow_worker_local:
            raise JobError(
                f"{job['job_type']} is worker-local: execute it on the worker's "
                f"own model and return results via POST /jobs/{job['job_id']}/submit-result",
                hint="upgrade the worker fleet to animica>=5.1.0 before emitting this job type")
        job["status"] = STATUS_RUNNING
        job["updated_at"] = now_ts()
        if worker_id:
            job["worker_id"] = worker_id
        self.store.upsert_job(job)

        executor = _EXECUTORS.get(job["job_type"])
        if executor is None:
            job["status"] = STATUS_REJECTED
            job["error"] = f"no local executor for {job['job_type']}"
            self.store.upsert_job(job)
            raise JobError(job["error"])
        try:
            result, artifacts = executor(self, job, job["params"])
        except JobError as exc:
            # An executor JobError (e.g. an SSRF/policy refusal raised by the
            # hardened scrape executor) is a clean terminal REJECT, not a stuck
            # 'running' job. Persist the rejected status + reason BEFORE re-raising
            # so the queue never leaks a job pinned at STATUS_RUNNING. (graft 1)
            job["status"] = STATUS_REJECTED
            job["error"] = getattr(exc, "message", str(exc))
            job["updated_at"] = now_ts()
            self.store.upsert_job(job)
            raise
        except Exception as exc:  # noqa: BLE001
            job["status"] = STATUS_REJECTED
            job["error"] = str(exc)
            job["updated_at"] = now_ts()
            self.store.upsert_job(job)
            raise JobError(f"job {job['job_id']} failed: {exc}") from exc
        job["result"] = result
        job["result_hash"] = hash_obj(result)
        job["artifacts"] = artifacts
        job["status"] = STATUS_COMPLETED
        job["updated_at"] = now_ts()
        self.store.upsert_job(job)
        return job

    def _maybe_stage(self, job: dict[str, Any]) -> None:
        """Curate a worker-local job's synthesized pairs into STAGING.

        Invoked from the result-landing path (``submit_result``) so synthesized
        pairs are curated identically whether they arrive from a remote fleet
        worker or a single-box worker (which finalizes via ``submit_result``
        too). Deterministic + model-free; never raises (a curation failure must
        not fail the job). Staging is safe — the live genome is only grown by the
        explicit, bounded :func:`curation.promote_staging`.
        """
        if job.get("job_type") not in WORKER_LOCAL_JOB_TYPES:
            return
        try:
            from . import curation
            job["curation"] = curation.stage_submission(self.cfg, job)
        except Exception as exc:  # noqa: BLE001
            job["curation"] = {"error": str(exc)}

    def submit(self, job_id: str, result_path: str) -> dict[str, Any]:
        job = self.get(job_id)
        p = Path(result_path)
        if not p.is_file():
            raise JobError(f"result file not found: {p}")
        try:
            import json
            result = json.loads(p.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise JobError(f"result must be JSON: {exc}") from exc
        art = self._write_artifact(job_id, "submitted_result.json",
                                   p.read_text(encoding="utf-8"))
        job["result"] = result
        job["result_hash"] = hash_obj(result)
        job["artifacts"] = (job.get("artifacts") or []) + [art]
        job["status"] = STATUS_SUBMITTED
        job["updated_at"] = now_ts()
        self.store.upsert_job(job)
        return job

    def submit_result(self, job_id: str, result: Optional[dict[str, Any]] = None,
                      worker_id: Optional[str] = None) -> dict[str, Any]:
        """Attach an **inline** result a worker computed locally (no file).

        This is the fast coordinator-side landing for worker-local jobs: the
        worker ran the model on its own hardware and POSTs the JSON result here,
        so the coordinator never executes the model and the worker never holds an
        HTTP read open across a long generation. For synthesis jobs the gated
        pairs are immediately curated into the STAGING dataset (model-free); the
        live genome is only grown by an explicit, bounded promote step.
        """
        job = self.get(job_id)
        result = result or {}
        if worker_id:
            job["worker_id"] = worker_id
        art = self._write_artifact(job_id, "submitted_result.json",
                                   canonical_json(result))
        job["result"] = result
        job["result_hash"] = hash_obj(result)
        job["artifacts"] = (job.get("artifacts") or []) + [art]
        job["status"] = STATUS_SUBMITTED
        job["updated_at"] = now_ts()
        self._maybe_stage(job)  # curate synthesized pairs into STAGING (model-free)
        self.store.upsert_job(job)
        return job

    # -- verify -----------------------------------------------------------
    def verify(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["status"] not in (STATUS_COMPLETED, STATUS_SUBMITTED):
            raise JobError(f"job {job_id} not in a verifiable state ({job['status']})")
        checks = _verify_checks(job)
        passed = all(c["passed"] for c in checks)
        verification = {
            "verification_id": "vf-" + new_uuid()[:16],
            "passed": passed, "checks": checks, "created_at": now_ts(),
        }
        verification["verification_hash"] = hash_obj(
            {"checks": checks, "passed": passed})
        job["verification"] = verification
        job["status"] = STATUS_VERIFIED if passed else STATUS_REJECTED
        job["updated_at"] = now_ts()
        self.store.upsert_job(job)
        return job

    # -- receipt + export -------------------------------------------------
    def receipt(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["status"] not in (STATUS_VERIFIED, STATUS_COMPLETED, STATUS_SUBMITTED, STATUS_REJECTED):
            raise JobError(f"job {job_id} has no receiptable state ({job['status']})")
        verification = job.get("verification") or {}
        artifacts = job.get("artifacts") or []
        receipt = rc.JobReceipt(
            receipt_id="rcpt-" + new_uuid()[:16],
            job_id=job["job_id"], job_hash=job["job_hash"], job_type=job["job_type"],
            job_status=job["status"], aicf_task_id=job["aicf_task_id"],
            aicf_job_kind=job.get("aicf_job_kind") or rc.aicf_kind_for(job["job_type"]),
            result_hash=job.get("result_hash") or "",
            requester=job.get("requester"), worker_id=job.get("worker_id"),
            provider_id=job.get("provider_id"),
            verification_id=verification.get("verification_id"),
            verification_hash=verification.get("verification_hash"),
            verification_passed=bool(verification.get("passed")),
            source_hashes=_source_hashes(job),
            artifact_ids=[a["artifact_id"] for a in artifacts],
            artifact_hashes=[a["hash"] for a in artifacts],
            score=float(verification.get("score", 1.0 if verification.get("passed") else 0.0)),
            score_components=verification.get("score_components", {}),
            reward=str(job.get("reward", job.get("params", {}).get("reward", "0"))),
        )
        rc.finalize_receipt(receipt)
        rec_dict = receipt.to_dict()
        self.store.upsert_receipt(rec_dict)
        return rec_dict

    def export_onchain(self, job_id: str) -> dict[str, Any]:
        rec = self.store.get_receipt_for_job(job_id)
        if rec is None:
            rec = self.receipt(job_id)
        return rc.build_export_envelope(rec)


# ---------------------------------------------------------------------------
# Executors  (self, job, params) -> (result_dict, artifacts_list)
# ---------------------------------------------------------------------------

def _exec_extract(self: JobService, job, params):
    source = params.get("source")
    if not source:
        raise JobError("extract requires --source")
    text = Path(source).read_text(encoding="utf-8", errors="replace")
    art = self._write_artifact(job["job_id"], "extracted.txt", text)
    return ({"source": source, "chars": len(text), "lines": text.count("\n") + 1,
             "preview": text[:280], "provenance": {"source": source}}, [art])


def _exec_scrape(self: JobService, job, params):
    """Fetch one URL into normalized raw text — through the 5.1.1 SSRF guard.

    Routes the fetch through :func:`animica.ena.web.fetch`, so the hostname is
    resolved + every IP validated against the private/reserved ranges, the
    connection is pinned to the validated IP (DNS-rebind defense), redirects are
    re-validated per hop, and the body is size-capped + content-type-allowlisted.
    The result is corpus SOURCE only (it still has to pass synthesis + the
    curation gate before anything can reach the genome).
    """
    from . import web
    url = params.get("source") or params.get("url")
    if not url:
        raise JobError("scrape requires --source/--url")
    try:
        res = web.fetch(url, self.cfg.network)
    except web.WebFetchError as exc:
        raise JobError(exc.message, hint=exc.hint) from exc
    rec = {"url": res.final_url, "text": res.text}
    art_path = self._artifacts_dir(job["job_id"]) / "raw.jsonl"
    ds.write_jsonl(art_path, [rec])
    art = {"artifact_id": "art-" + new_uuid()[:16], "path": str(art_path),
           "name": "raw.jsonl", "hash": ds.sha256_file(art_path)}
    return ({"url": res.final_url, "bytes": res.raw_bytes, "chars": len(res.text),
             "rows": 1, "content_type": res.content_type, "ip": res.ip,
             "hops": res.hops,
             "provenance": {"url": res.final_url, "requested_url": url,
                            "ip": res.ip, "content_type": res.content_type}}, [art])


def _exec_chunk(self: JobService, job, params):
    source = params.get("source") or params.get("dataset")
    if not source:
        raise JobError("chunk requires --source/--dataset")
    text = Path(source).read_text(encoding="utf-8", errors="replace")
    chunks = retrieval.chunk_text(text, max_chars=int(params.get("max_chars", 1200)))
    rows = [{"ordinal": i, "text": c} for i, c in enumerate(chunks)]
    art_path = self._artifacts_dir(job["job_id"]) / "chunks.jsonl"
    ds.write_jsonl(art_path, rows)
    art = {"artifact_id": "art-" + new_uuid()[:16], "path": str(art_path),
           "name": "chunks.jsonl", "hash": ds.sha256_file(art_path)}
    return ({"source": source, "row_count": len(rows)}, [art])


def _exec_embed(self: JobService, job, params):
    dataset = params.get("dataset") or params.get("source")
    if not dataset:
        raise JobError("embed requires --dataset")
    embedder = self.embedder(params.get("embedding_provider"))
    texts, rows = [], []
    for rec in ds.read_jsonl(dataset):
        t = rec.get("text") or rec.get("prompt") or rec.get("content") or ""
        if t:
            texts.append(str(t))
            rows.append(rec)
    vectors = embedder.embed(texts) if texts else []
    dim = len(vectors[0]) if vectors else 0
    out = [{"text": t, "embedding": v} for t, v in zip(texts, vectors)]
    art_path = self._artifacts_dir(job["job_id"]) / "embeddings.jsonl"
    ds.write_jsonl(art_path, out)
    art = {"artifact_id": "art-" + new_uuid()[:16], "path": str(art_path),
           "name": "embeddings.jsonl", "hash": ds.sha256_file(art_path)}
    return ({"dataset": dataset, "row_count": len(out), "embedding_dim": dim}, [art])


def _exec_index(self: JobService, job, params):
    paths = params.get("paths") or ([params["source"]] if params.get("source") else None)
    if not paths:
        raise JobError("index requires --source/--paths")
    name = params.get("name") or ("idx-" + job["job_id"][4:12])
    embedder = self.embedder(params.get("embedding_provider"))
    ecfg = self.cfg.embedding_provider(params.get("embedding_provider"))
    info = retrieval.build_index(self.store, paths, index_name=name, embedder=embedder,
                                 embedding_provider=ecfg.provider,
                                 embedding_model=ecfg.model)
    return (info, [])


def _exec_summarize(self: JobService, job, params):
    source = params.get("source")
    if not source:
        raise JobError("summarize requires --source")
    text = Path(source).read_text(encoding="utf-8", errors="replace")[:8000]
    model = self.model(params.get("model_provider"))
    summary = model.generate(f"Summarize the following:\n\n{text}", max_tokens=512)
    art = self._write_artifact(job["job_id"], "summary.txt", summary)
    return ({"source": source, "summary_chars": len(summary),
             "preview": summary[:280]}, [art])


def _exec_label(self: JobService, job, params):
    dataset = params.get("dataset") or params.get("source")
    labels = params.get("labels") or []
    if not dataset:
        raise JobError("label requires --dataset")
    if not labels:
        raise JobError("label requires at least one --label")
    model = self.model(params.get("model_provider"))
    out = []
    for rec in ds.read_jsonl(dataset):
        text = str(rec.get("text") or rec.get("prompt") or "")
        try:
            resp = model.generate(
                f"Classify the text into exactly one of {labels}. "
                f"Reply with only the label.\n\nText: {text[:1000]}",
                max_tokens=16, temperature=0.0)
            label = next((l for l in labels if l.lower() in resp.lower()), labels[0])
        except Exception:  # noqa: BLE001 - deterministic fallback
            label = labels[0]
        out.append({**rec, "label": label})
    art_path = self._artifacts_dir(job["job_id"]) / "labeled.jsonl"
    ds.write_jsonl(art_path, out)
    art = {"artifact_id": "art-" + new_uuid()[:16], "path": str(art_path),
           "name": "labeled.jsonl", "hash": ds.sha256_file(art_path)}
    return ({"dataset": dataset, "row_count": len(out), "labels": labels}, [art])


def _exec_dataset_clean(self: JobService, job, params):
    dataset = params.get("dataset") or params.get("source")
    if not dataset:
        raise JobError("dataset_clean requires --dataset")
    d = self._artifacts_dir(job["job_id"])
    norm = d / "normalized.jsonl"
    clean = d / "clean.jsonl"
    n = ds.normalize(dataset, norm)
    c = ds.dedupe(norm, clean)
    art = {"artifact_id": "art-" + new_uuid()[:16], "path": str(clean),
           "name": "clean.jsonl", "hash": ds.sha256_file(clean)}
    return ({"dataset": dataset, "normalized_rows": n["rows"],
             "clean_rows": c["rows"], "removed": c["removed"]}, [art])


def _exec_training_records(self: JobService, job, params):
    dataset = params.get("dataset") or params.get("source")
    if not dataset:
        raise JobError("training_records requires --dataset")
    out_path = self._artifacts_dir(job["job_id"]) / "training_records.jsonl"
    info = ds.normalize(dataset, out_path)
    art = {"artifact_id": "art-" + new_uuid()[:16], "path": str(out_path),
           "name": "training_records.jsonl", "hash": ds.sha256_file(out_path)}
    return ({"dataset": dataset, "row_count": info["rows"]}, [art])


def _exec_eval(self: JobService, job, params):
    dataset = params.get("dataset") or params.get("source")
    if not dataset:
        raise JobError("eval requires --dataset")
    rows = list(ds.read_jsonl(dataset))
    model = self.model(params.get("model_provider"))
    sample = rows[: int(params.get("limit", 5))]
    outputs = []
    for rec in sample:
        prompt = str(rec.get("prompt") or rec.get("text") or "")
        try:
            outputs.append(model.generate(prompt, max_tokens=128))
        except Exception as exc:  # noqa: BLE001
            outputs.append(f"<error: {exc}>")
    metrics = {"rows": len(rows), "sampled": len(sample),
               "non_empty_outputs": sum(1 for o in outputs if o.strip())}
    art = self._write_artifact(job["job_id"], "eval.json",
                               canonical_json({"metrics": metrics}))
    return ({"dataset": dataset, "metrics": metrics}, [art])


def _exec_train_prepare(self: JobService, job, params):
    from . import training
    dataset = params.get("dataset")
    if not dataset:
        raise JobError("train_prepare requires --dataset")
    out = self._artifacts_dir(job["job_id"]) / "train_manifest.json"
    manifest = training.prepare(
        self.cfg, dataset=dataset, out=str(out),
        base_model=params.get("base_model", "tiny-local-model"),
        backend=params.get("backend", "command"),
        auto_split=bool(params.get("auto_split", True)),
        launcher_command=params.get("launcher_command"),
        hyperparameters=params.get("hyperparameters"))
    art = {"artifact_id": "art-" + new_uuid()[:16], "path": str(out),
           "name": "train_manifest.json", "hash": ds.sha256_file(out)}
    return ({"dataset": dataset, "manifest": manifest}, [art])


def _exec_synthesize_qa(self: JobService, job, params):
    """Worker-local LLM synthesis: corpus chunk -> ``{prompt, response}`` pairs.

    Runs the worker's OWN model (``self.model`` resolves to LOCAL_INFERENCE_URL
    on a worker) over the inline ``params['corpus']``; it must only be reached
    via the worker's local-exec path (``run(..., allow_worker_local=True)`` or
    the worker calling :func:`animica.ena.synth.synthesize` directly), never the
    coordinator's in-request ``/run``. Best-effort: a weak local model yields
    zero pairs but the job still completes so the miner earns for the attempt.
    """
    from . import synth
    corpus = params.get("corpus") or params.get("source_chunk")
    if not corpus:
        raise JobError("synthesize_qa requires --corpus (inline source chunk)")
    model = self.model(params.get("model_provider"))
    pairs, meta = synth.synthesize(
        model, str(corpus),
        num_pairs=int(params.get("num_pairs", synth.DEFAULT_NUM_PAIRS)),
        max_tokens=int(params.get("max_tokens", synth.DEFAULT_MAX_TOKENS)),
        temperature=float(params.get("temperature", synth.DEFAULT_TEMPERATURE)),
        # synthesize UNDER the round the coordinator stamped on the job (or env);
        # the miner's generation is then driven by verifiable quantum entropy.
        beacon=params.get("quantum_beacon"),
        bind_id=job.get("job_id"))
    out_path = self._artifacts_dir(job["job_id"]) / "pairs.jsonl"
    ds.write_jsonl(out_path, pairs)
    art = {"artifact_id": "art-" + new_uuid()[:16], "path": str(out_path),
           "name": "pairs.jsonl", "hash": ds.sha256_file(out_path)}
    result = {"pairs": pairs, "pair_count": len(pairs), **meta}
    return (result, [art])


_EXECUTORS: dict[str, Callable] = {
    "extract": _exec_extract,
    "scrape": _exec_scrape,
    "chunk": _exec_chunk,
    "embed": _exec_embed,
    "index": _exec_index,
    "summarize": _exec_summarize,
    "label": _exec_label,
    "dataset_clean": _exec_dataset_clean,
    "training_records": _exec_training_records,
    "eval": _exec_eval,
    "train_prepare": _exec_train_prepare,
    "synthesize_qa": _exec_synthesize_qa,
}


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

def _source_hashes(job) -> list[str]:
    out = []
    params = job.get("params", {})
    for key in ("source", "dataset", "url"):
        v = params.get(key)
        if v and isinstance(v, str):
            out.append(sha3_hex(v))
    return out


def _verify_checks(job) -> list[dict[str, Any]]:
    result = job.get("result") or {}
    jt = job["job_type"]
    checks: list[dict[str, Any]] = []

    def add(name: str, passed: bool, detail: Any = None):
        checks.append({"check": name, "passed": bool(passed), "detail": detail})

    add("output_present", bool(result))
    add("result_hash_present", bool(job.get("result_hash")))

    if jt in ("scrape", "extract"):
        add("has_provenance", bool(result.get("provenance")))
    if jt in ("chunk", "label", "embed", "dataset_clean", "training_records"):
        rc_count = result.get("row_count", result.get("clean_rows"))
        add("row_count_positive", isinstance(rc_count, int) and rc_count > 0, rc_count)
    if jt == "embed":
        add("embedding_dim_positive", int(result.get("embedding_dim", 0)) > 0,
            result.get("embedding_dim"))
    if jt == "index":
        add("index_metadata", bool(result.get("index")) and int(result.get("chunks", 0)) >= 0)
    if jt == "label":
        add("labels_present", bool(result.get("labels")))
    if jt == "train_prepare":
        man = result.get("manifest") or {}
        add("manifest_shape", all(k in man for k in ("run_name", "backend", "base_model")))
    if jt == "synthesize_qa":
        # Permissive: attests the work ran and is well-shaped. Genome growth is
        # gated separately (curation), so a sparse chunk yielding 0 pairs is a
        # valid, paid useful-work result — it just won't grow the genome.
        add("pairs_is_list", isinstance(result.get("pairs"), list),
            result.get("pair_count"))
        add("has_provenance", bool(result.get("provenance")))
    return checks
