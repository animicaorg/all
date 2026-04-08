from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .models import (
    AgentTrace,
    ArtifactRecord,
    DatasetRecord,
    EnaConfigModel,
    IndexRecord,
    JobReceipt,
    SearchHit,
    SessionRecord,
    TrainingRunRecord,
    VerificationRecord,
)
from .text import cosine_similarity, normalize_text, sha256_hex, stable_id, text_score, utc_now_iso


def _json(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class EnaStore:
    def __init__(self, config: EnaConfigModel):
        self.config = config
        self.storage = config.storage
        self._ensure_dirs()
        self.conn = sqlite3.connect(str(self.storage.db_path))
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    def _ensure_dirs(self) -> None:
        paths = [
            self.storage.home,
            self.storage.db_path.parent if self.storage.db_path else None,
            self.storage.artifacts_dir,
            self.storage.datasets_dir,
            self.storage.indexes_dir,
            self.storage.sessions_dir,
            self.storage.logs_dir,
            self.storage.manifests_dir,
            self.config.default_output_dir,
        ]
        for path in paths:
            if path:
                Path(path).mkdir(parents=True, exist_ok=True)

    def _migrate(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts(
              artifact_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              path TEXT NOT NULL,
              sha256 TEXT NOT NULL,
              size_bytes INTEGER NOT NULL,
              created_at TEXT NOT NULL,
              source_uri TEXT,
              parent_artifact_id TEXT,
              metadata_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions(
              session_id TEXT PRIMARY KEY,
              task TEXT NOT NULL,
              status TEXT NOT NULL,
              autonomy TEXT NOT NULL,
              working_dir TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              summary TEXT,
              metadata_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS traces(
              session_id TEXT NOT NULL,
              step_index INTEGER NOT NULL,
              action TEXT NOT NULL,
              status TEXT NOT NULL,
              tool_name TEXT,
              input_json TEXT NOT NULL,
              output_json TEXT NOT NULL,
              created_at TEXT NOT NULL,
              PRIMARY KEY(session_id, step_index)
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS memory(
              memory_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              content TEXT NOT NULL,
              source TEXT,
              confidence REAL NOT NULL,
              created_at TEXT NOT NULL,
              metadata_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS search_indexes(
              index_name TEXT PRIMARY KEY,
              root TEXT NOT NULL,
              chunk_count INTEGER NOT NULL,
              source_count INTEGER NOT NULL,
              embedding_provider TEXT NOT NULL,
              embedding_model TEXT,
              retrieval_mode TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              metadata_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks(
              chunk_id TEXT PRIMARY KEY,
              index_name TEXT NOT NULL,
              source TEXT NOT NULL,
              title TEXT,
              content TEXT NOT NULL,
              embedding_json TEXT NOT NULL,
              metadata_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS datasets(
              dataset_id TEXT PRIMARY KEY,
              kind TEXT NOT NULL,
              path TEXT NOT NULL,
              row_count INTEGER NOT NULL,
              sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL,
              metadata_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS jobs(
              job_id TEXT PRIMARY KEY,
              job_hash TEXT NOT NULL DEFAULT '',
              job_type TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              claimed_by TEXT,
              aicf_task_id TEXT,
              spec_json TEXT NOT NULL,
              result_json TEXT NOT NULL,
              verification_json TEXT,
              reward_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS job_events(
              event_id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL,
              event TEXT NOT NULL,
              created_at TEXT NOT NULL,
              payload_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS receipts(
              receipt_id TEXT PRIMARY KEY,
              job_id TEXT NOT NULL,
              receipt_hash TEXT NOT NULL,
              created_at TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              onchain_payload_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS eval_runs(
              eval_id TEXT PRIMARY KEY,
              suite_name TEXT NOT NULL,
              path TEXT NOT NULL,
              created_at TEXT NOT NULL,
              summary_json TEXT NOT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS training_runs(
              run_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              backend TEXT NOT NULL,
              manifest_path TEXT NOT NULL,
              base_model TEXT NOT NULL,
              output_dir TEXT NOT NULL,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              command_json TEXT NOT NULL,
              checkpoint_paths_json TEXT NOT NULL,
              artifact_ids_json TEXT NOT NULL,
              metrics_json TEXT NOT NULL,
              eval_report_json TEXT NOT NULL,
              metadata_json TEXT NOT NULL,
              error TEXT
            )
            """
        )
        self._ensure_column("jobs", "job_hash", "TEXT NOT NULL DEFAULT ''")
        self.conn.commit()

    def _ensure_column(self, table: str, column: str, spec: str) -> None:
        rows = self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        if any(row["name"] == column for row in rows):
            return
        self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {spec}")

    def close(self) -> None:
        self.conn.close()

    def audit(self, event: str, payload: Dict[str, Any]) -> None:
        audit_path = Path(self.storage.logs_dir) / "audit.jsonl"
        row = {"event": event, "payload": payload, "created_at": utc_now_iso()}
        audit_path.parent.mkdir(parents=True, exist_ok=True)
        with audit_path.open("a", encoding="utf-8") as handle:
            handle.write(_json(row) + "\n")

    def put_artifact(
        self,
        kind: str,
        content: bytes | str,
        *,
        source_uri: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        suffix: str = ".json",
        parent_artifact_id: Optional[str] = None,
    ) -> ArtifactRecord:
        raw = content.encode("utf-8") if isinstance(content, str) else content
        sha = sha256_hex(raw)
        artifact_id = stable_id(kind, sha)
        path = Path(self.storage.artifacts_dir) / f"{artifact_id}{suffix}"
        path.write_bytes(raw)
        record = ArtifactRecord(
            artifact_id=artifact_id,
            kind=kind,
            path=str(path),
            sha256=sha,
            size_bytes=len(raw),
            source_uri=source_uri,
            parent_artifact_id=parent_artifact_id,
            metadata=metadata or {},
        )
        self.conn.execute(
            """
            INSERT OR REPLACE INTO artifacts(
              artifact_id, kind, path, sha256, size_bytes, created_at,
              source_uri, parent_artifact_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.artifact_id,
                record.kind,
                record.path,
                record.sha256,
                record.size_bytes,
                record.created_at,
                record.source_uri,
                record.parent_artifact_id,
                _json(record.metadata),
            ),
        )
        self.conn.commit()
        self.audit("artifact.put", {"artifact_id": record.artifact_id, "kind": kind})
        return record

    def list_artifacts(self, limit: int = 50) -> List[ArtifactRecord]:
        rows = self.conn.execute(
            "SELECT * FROM artifacts ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            ArtifactRecord(
                artifact_id=row["artifact_id"],
                kind=row["kind"],
                path=row["path"],
                sha256=row["sha256"],
                size_bytes=row["size_bytes"],
                created_at=row["created_at"],
                source_uri=row["source_uri"],
                parent_artifact_id=row["parent_artifact_id"],
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        ]

    def get_artifact(self, artifact_id: str) -> Optional[ArtifactRecord]:
        row = self.conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id = ?",
            (artifact_id,),
        ).fetchone()
        if row is None:
            return None
        return ArtifactRecord(
            artifact_id=row["artifact_id"],
            kind=row["kind"],
            path=row["path"],
            sha256=row["sha256"],
            size_bytes=row["size_bytes"],
            created_at=row["created_at"],
            source_uri=row["source_uri"],
            parent_artifact_id=row["parent_artifact_id"],
            metadata=json.loads(row["metadata_json"]),
        )

    def save_session(self, session: SessionRecord) -> SessionRecord:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO sessions(
              session_id, task, status, autonomy, working_dir, created_at,
              updated_at, summary, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.session_id,
                session.task,
                session.status,
                session.autonomy.value,
                session.working_dir,
                session.created_at,
                session.updated_at,
                session.summary,
                _json(session.metadata),
            ),
        )
        self.conn.commit()
        return session

    def get_session(self, session_id: str) -> Optional[SessionRecord]:
        row = self.conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return SessionRecord(
            session_id=row["session_id"],
            task=row["task"],
            status=row["status"],
            autonomy=row["autonomy"],
            working_dir=row["working_dir"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            summary=row["summary"],
            metadata=json.loads(row["metadata_json"]),
        )

    def list_sessions(self, limit: int = 50) -> List[SessionRecord]:
        rows = self.conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            SessionRecord(
                session_id=row["session_id"],
                task=row["task"],
                status=row["status"],
                autonomy=row["autonomy"],
                working_dir=row["working_dir"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                summary=row["summary"],
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        ]

    def add_trace(self, trace: AgentTrace) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO traces(
              session_id, step_index, action, status, tool_name, input_json,
              output_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trace.session_id,
                trace.step_index,
                trace.action,
                trace.status,
                trace.tool_name,
                _json(trace.input_payload),
                _json(trace.output_payload),
                trace.created_at,
            ),
        )
        self.conn.commit()

    def list_traces(self, session_id: str) -> List[AgentTrace]:
        rows = self.conn.execute(
            "SELECT * FROM traces WHERE session_id = ? ORDER BY step_index ASC",
            (session_id,),
        ).fetchall()
        return [
            AgentTrace(
                session_id=row["session_id"],
                step_index=row["step_index"],
                action=row["action"],
                status=row["status"],
                tool_name=row["tool_name"],
                input_payload=json.loads(row["input_json"]),
                output_payload=json.loads(row["output_json"]),
                created_at=row["created_at"],
            )
            for row in rows
        ]

    def add_memory(
        self,
        *,
        kind: str,
        content: str,
        source: Optional[str] = None,
        confidence: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        memory_id = stable_id("mem", kind, content, source or "")
        self.conn.execute(
            """
            INSERT OR REPLACE INTO memory(
              memory_id, kind, content, source, confidence, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
            """,
            (memory_id, kind, content, source, confidence, _json(metadata or {})),
        )
        self.conn.commit()
        return memory_id

    def query_memory(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM memory").fetchall()
        scored = []
        for row in rows:
            score = text_score(query, row["content"])
            if score:
                scored.append((score, row))
        scored.sort(key=lambda item: -item[0])
        results = []
        for score, row in scored[:limit]:
            results.append(
                {
                    "memory_id": row["memory_id"],
                    "kind": row["kind"],
                    "content": row["content"],
                    "source": row["source"],
                    "confidence": row["confidence"],
                    "score": score,
                    "metadata": json.loads(row["metadata_json"]),
                }
            )
        return results

    def save_index(self, record: IndexRecord) -> IndexRecord:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO search_indexes(
              index_name, root, chunk_count, source_count, embedding_provider,
              embedding_model, retrieval_mode, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.index_name,
                record.root,
                record.chunk_count,
                record.source_count,
                record.embedding_provider,
                record.embedding_model,
                record.retrieval_mode,
                record.updated_at,
                _json(record.metadata),
            ),
        )
        self.conn.commit()
        return record

    def get_index(self, index_name: str) -> Optional[IndexRecord]:
        row = self.conn.execute(
            "SELECT * FROM search_indexes WHERE index_name = ?",
            (index_name,),
        ).fetchone()
        if row is None:
            return None
        return IndexRecord(
            index_name=row["index_name"],
            root=row["root"],
            chunk_count=row["chunk_count"],
            source_count=row["source_count"],
            embedding_provider=row["embedding_provider"],
            embedding_model=row["embedding_model"],
            retrieval_mode=row["retrieval_mode"],
            updated_at=row["updated_at"],
            metadata=json.loads(row["metadata_json"]),
        )

    def list_indexes(self) -> List[IndexRecord]:
        rows = self.conn.execute(
            "SELECT * FROM search_indexes ORDER BY updated_at DESC"
        ).fetchall()
        return [
            IndexRecord(
                index_name=row["index_name"],
                root=row["root"],
                chunk_count=row["chunk_count"],
                source_count=row["source_count"],
                embedding_provider=row["embedding_provider"],
                embedding_model=row["embedding_model"],
                retrieval_mode=row["retrieval_mode"],
                updated_at=row["updated_at"],
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        ]

    def upsert_chunks(self, index_name: str, chunks: Iterable[Dict[str, Any]]) -> int:
        total = 0
        for chunk in chunks:
            content = normalize_text(chunk["content"])
            embedding = chunk.get("embedding") or []
            self.conn.execute(
                """
                INSERT OR REPLACE INTO chunks(
                  chunk_id, index_name, source, title, content, embedding_json,
                  metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chunk["chunk_id"],
                    index_name,
                    chunk["source"],
                    chunk.get("title"),
                    content,
                    _json(embedding),
                    _json(chunk.get("metadata", {})),
                    chunk.get("created_at") or utc_now_iso(),
                ),
            )
            total += 1
        self.conn.commit()
        return total

    def clear_index(self, index_name: str) -> None:
        self.conn.execute("DELETE FROM chunks WHERE index_name = ?", (index_name,))
        self.conn.execute("DELETE FROM search_indexes WHERE index_name = ?", (index_name,))
        self.conn.commit()

    def search_chunks(
        self,
        query: str,
        *,
        index_name: Optional[str] = None,
        limit: int = 8,
        query_embedding: Optional[Sequence[float]] = None,
        strategy: str = "hybrid",
        lexical_weight: float = 0.45,
        semantic_weight: float = 0.55,
    ) -> List[SearchHit]:
        if index_name:
            rows = self.conn.execute(
                "SELECT * FROM chunks WHERE index_name = ?",
                (index_name,),
            ).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM chunks").fetchall()

        scored: List[tuple[float, float, float, sqlite3.Row]] = []
        for row in rows:
            lexical_raw = text_score(query, row["content"])
            lexical_score = min(lexical_raw, 20.0) / 20.0 if lexical_raw > 0 else 0.0
            semantic_score = 0.0
            if query_embedding is not None:
                row_embedding = json.loads(row["embedding_json"])
                if row_embedding and len(row_embedding) == len(query_embedding):
                    semantic_score = max(cosine_similarity(query_embedding, row_embedding), 0.0)

            if strategy == "keyword":
                total_score = lexical_score
            elif strategy == "semantic":
                total_score = semantic_score
            else:
                total_score = lexical_weight * lexical_score + semantic_weight * semantic_score

            if total_score > 0:
                scored.append((total_score, lexical_score, semantic_score, row))

        scored.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]["chunk_id"]))
        results: List[SearchHit] = []
        for total_score, lexical_score, semantic_score, row in scored[:limit]:
            excerpt = row["content"][:240]
            results.append(
                SearchHit(
                    chunk_id=row["chunk_id"],
                    source=row["source"],
                    title=row["title"],
                    excerpt=excerpt,
                    score=total_score,
                    lexical_score=lexical_score,
                    semantic_score=semantic_score,
                    retrieval_mode=strategy,
                    metadata=json.loads(row["metadata_json"]),
                )
            )
        return results

    def save_dataset(self, dataset: DatasetRecord) -> DatasetRecord:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO datasets(
              dataset_id, kind, path, row_count, sha256, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                dataset.dataset_id,
                dataset.kind,
                dataset.path,
                dataset.row_count,
                dataset.sha256,
                dataset.created_at,
                _json(dataset.metadata),
            ),
        )
        self.conn.commit()
        return dataset

    def list_datasets(self) -> List[DatasetRecord]:
        rows = self.conn.execute("SELECT * FROM datasets ORDER BY created_at DESC").fetchall()
        return [
            DatasetRecord(
                dataset_id=row["dataset_id"],
                kind=row["kind"],
                path=row["path"],
                row_count=row["row_count"],
                sha256=row["sha256"],
                created_at=row["created_at"],
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        ]

    def save_job(
        self,
        job_id: str,
        *,
        job_hash: str,
        job_type: str,
        status: str,
        created_at: str,
        updated_at: str,
        claimed_by: Optional[str],
        aicf_task_id: Optional[str],
        spec_json: Dict[str, Any],
        result_json: Dict[str, Any],
        verification_json: Optional[Dict[str, Any]],
        reward_json: Dict[str, Any],
    ) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO jobs(
              job_id, job_hash, job_type, status, created_at, updated_at, claimed_by,
              aicf_task_id, spec_json, result_json, verification_json, reward_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                job_hash,
                job_type,
                status,
                created_at,
                updated_at,
                claimed_by,
                aicf_task_id,
                _json(spec_json),
                _json(result_json),
                _json(verification_json) if verification_json is not None else None,
                _json(reward_json),
            ),
        )
        self.conn.commit()

    def get_job_row(self, job_id: str) -> Optional[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()

    def list_job_rows(self, status: Optional[str] = None) -> List[sqlite3.Row]:
        if status:
            return self.conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY updated_at DESC",
                (status,),
            ).fetchall()
        return self.conn.execute("SELECT * FROM jobs ORDER BY updated_at DESC").fetchall()

    def add_job_event(self, job_id: str, event: str, payload: Dict[str, Any], created_at: str) -> None:
        self.conn.execute(
            "INSERT INTO job_events(job_id, event, created_at, payload_json) VALUES (?, ?, ?, ?)",
            (job_id, event, created_at, _json(payload)),
        )
        self.conn.commit()

    def list_job_events(self, job_id: str) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM job_events WHERE job_id = ? ORDER BY event_id ASC",
            (job_id,),
        ).fetchall()
        return [
            {
                "event": row["event"],
                "created_at": row["created_at"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def save_receipt(self, receipt: JobReceipt) -> JobReceipt:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO receipts(
              receipt_id, job_id, receipt_hash, created_at, payload_json, onchain_payload_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.receipt_id,
                receipt.job_id,
                receipt.receipt_hash,
                receipt.created_at,
                receipt.canonical_json(),
                _json(receipt.onchain_payload),
            ),
        )
        self.conn.commit()
        self.put_artifact(
            "job_receipt",
            receipt.canonical_json(),
            metadata={"job_id": receipt.job_id, "receipt_hash": receipt.receipt_hash},
            suffix=".json",
        )
        return receipt

    def get_receipt(self, job_id: str) -> Optional[JobReceipt]:
        row = self.conn.execute(
            "SELECT * FROM receipts WHERE job_id = ? ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        return JobReceipt.model_validate(json.loads(row["payload_json"]))

    def list_receipts(self, limit: int = 100) -> List[JobReceipt]:
        rows = self.conn.execute(
            "SELECT * FROM receipts ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [JobReceipt.model_validate(json.loads(row["payload_json"])) for row in rows]

    def save_verification(self, record: VerificationRecord) -> VerificationRecord:
        self.put_artifact(
            "verification_record",
            record.canonical_json(),
            metadata={"target_id": record.target_id, "target_type": record.target_type},
            suffix=".json",
        )
        return record

    def save_eval_run(self, eval_id: str, suite_name: str, path: str, summary: Dict[str, Any], created_at: str) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO eval_runs(eval_id, suite_name, path, created_at, summary_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (eval_id, suite_name, path, created_at, _json(summary)),
        )
        self.conn.commit()

    def list_eval_runs(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM eval_runs ORDER BY created_at DESC").fetchall()
        return [
            {
                "eval_id": row["eval_id"],
                "suite_name": row["suite_name"],
                "path": row["path"],
                "created_at": row["created_at"],
                "summary": json.loads(row["summary_json"]),
            }
            for row in rows
        ]

    def save_training_run(self, record: TrainingRunRecord) -> TrainingRunRecord:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO training_runs(
              run_id, status, backend, manifest_path, base_model, output_dir,
              created_at, updated_at, command_json, checkpoint_paths_json,
              artifact_ids_json, metrics_json, eval_report_json, metadata_json, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.run_id,
                record.status,
                record.backend,
                record.manifest_path,
                record.base_model,
                record.output_dir,
                record.created_at,
                record.updated_at,
                _json(record.command),
                _json(record.checkpoint_paths),
                _json(record.artifact_ids),
                _json(record.metrics),
                _json(record.eval_report),
                _json(record.metadata),
                record.error,
            ),
        )
        self.conn.commit()
        return record

    def get_training_run(self, run_id: str) -> Optional[TrainingRunRecord]:
        row = self.conn.execute(
            "SELECT * FROM training_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return TrainingRunRecord(
            run_id=row["run_id"],
            status=row["status"],
            backend=row["backend"],
            manifest_path=row["manifest_path"],
            base_model=row["base_model"],
            output_dir=row["output_dir"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            command=json.loads(row["command_json"]),
            checkpoint_paths=json.loads(row["checkpoint_paths_json"]),
            artifact_ids=json.loads(row["artifact_ids_json"]),
            metrics=json.loads(row["metrics_json"]),
            eval_report=json.loads(row["eval_report_json"]),
            metadata=json.loads(row["metadata_json"]),
            error=row["error"],
        )

    def list_training_runs(self, limit: int = 100) -> List[TrainingRunRecord]:
        rows = self.conn.execute(
            "SELECT * FROM training_runs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        results: List[TrainingRunRecord] = []
        for row in rows:
            results.append(
                TrainingRunRecord(
                    run_id=row["run_id"],
                    status=row["status"],
                    backend=row["backend"],
                    manifest_path=row["manifest_path"],
                    base_model=row["base_model"],
                    output_dir=row["output_dir"],
                    created_at=row["created_at"],
                    updated_at=row["updated_at"],
                    command=json.loads(row["command_json"]),
                    checkpoint_paths=json.loads(row["checkpoint_paths_json"]),
                    artifact_ids=json.loads(row["artifact_ids_json"]),
                    metrics=json.loads(row["metrics_json"]),
                    eval_report=json.loads(row["eval_report_json"]),
                    metadata=json.loads(row["metadata_json"]),
                    error=row["error"],
                )
            )
        return results
