from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query

from .agent import AgentRunner
from .config import load_ena_config
from .models import TaskSpec
from .store import EnaStore


def create_app(config_path: Optional[str] = None) -> FastAPI:
    config = load_ena_config(explicit_path=Path(config_path).expanduser() if config_path else None)
    store = EnaStore(config)
    runner = AgentRunner(config=config, store=store)

    app = FastAPI(
        title="Animica ENA",
        version=config.version,
        description="CLI-first agent runtime, useful-work queue, ingestion, retrieval, and training pipeline API.",
    )

    @app.get("/v1/health")
    def health() -> dict:
        return {"ok": True, "version": config.version, "workspace": str(config.workspace)}

    @app.get("/v1/config")
    def get_config() -> dict:
        return config.model_dump(mode="json")

    @app.get("/v1/sessions")
    def list_sessions(limit: int = Query(50, ge=1, le=500)) -> dict:
        return {"sessions": [session.model_dump(mode="json") for session in store.list_sessions(limit=limit)]}

    @app.get("/v1/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        session = store.get_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="session not found")
        return {
            "session": session.model_dump(mode="json"),
            "traces": [trace.model_dump(mode="json") for trace in store.list_traces(session_id)],
        }

    @app.get("/v1/artifacts")
    def list_artifacts(limit: int = Query(50, ge=1, le=500)) -> dict:
        return {"artifacts": [artifact.model_dump(mode="json") for artifact in store.list_artifacts(limit=limit)]}

    @app.get("/v1/datasets")
    def list_datasets() -> dict:
        return {"datasets": [dataset.model_dump(mode="json") for dataset in store.list_datasets()]}

    @app.get("/v1/indexes")
    def list_indexes() -> dict:
        return {"indexes": [item.model_dump(mode="json") for item in store.list_indexes()]}

    @app.get("/v1/jobs")
    def list_jobs(status: Optional[str] = None) -> dict:
        enum_status = None
        if status is not None:
            from .models import JobStatus

            enum_status = JobStatus(status)
        return {"jobs": [job.model_dump(mode="json") for job in runner.tools.jobs.list(status=enum_status)]}

    @app.get("/v1/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        job = runner.tools.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return {"job": job.model_dump(mode="json"), "events": store.list_job_events(job_id)}

    @app.get("/v1/jobs/{job_id}/receipt")
    def get_job_receipt(job_id: str) -> dict:
        receipt = runner.tools.jobs.receipt(job_id)
        if receipt is None:
            raise HTTPException(status_code=404, detail="job receipt not found")
        return {"receipt": receipt.model_dump(mode="json")}

    @app.get("/v1/receipts")
    def list_receipts(limit: int = Query(100, ge=1, le=500)) -> dict:
        return {"receipts": [receipt.model_dump(mode="json") for receipt in store.list_receipts(limit=limit)]}

    @app.get("/v1/memory")
    def search_memory(q: str = Query("", alias="query"), limit: int = Query(10, ge=1, le=100)) -> dict:
        return {"results": store.query_memory(q, limit=limit) if q else []}

    @app.get("/v1/evals")
    def list_evals() -> dict:
        return {"evals": store.list_eval_runs()}

    @app.get("/v1/training/runs")
    def list_training_runs(limit: int = Query(100, ge=1, le=500)) -> dict:
        return {"runs": [run.model_dump(mode="json") for run in store.list_training_runs(limit=limit)]}

    @app.get("/v1/training/runs/{run_id}")
    def get_training_run(run_id: str) -> dict:
        record = store.get_training_run(run_id)
        if record is None:
            raise HTTPException(status_code=404, detail="training run not found")
        return {"run": record.model_dump(mode="json")}

    @app.post("/v1/ask")
    def ask(spec: TaskSpec) -> dict:
        return runner.run(spec)

    return app
