"""SQLite persistence layer for Animica Studio Qt.

The :class:`Database` wraps a single :mod:`sqlite3` connection opened with
``check_same_thread=False`` and guards every access with a re-entrant lock so it
is safe to share across Qt threads/workers. JSON columns store structured fields.

Schema is created idempotently via ``CREATE TABLE IF NOT EXISTS`` (poor-man's
migrations). Tables: ``sessions``, ``messages``, ``projects``, ``settings``,
``tool_calls``, ``command_approvals``, ``files_index``.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Union

from .models import (
    ApprovalDecision,
    AutonomyLevel,
    CommandApproval,
    FileIndexEntry,
    Message,
    MessageRole,
    Project,
    Session,
    ToolCall,
    ToolStatus,
    new_id,
    now_ts,
)

SCHEMA_VERSION = 1


class Database:
    """Thread-safe SQLite wrapper exposing typed CRUD helpers."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._init_schema()

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        with self._lock:
            try:
                self._conn.commit()
            finally:
                self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Low-level helpers
    # ------------------------------------------------------------------ #
    @staticmethod
    def _bind(params: "Union[Mapping[str, Any], Iterable[Any]]") -> "Union[Mapping[str, Any], tuple[Any, ...]]":
        # sqlite3 accepts either a sequence (for ``?`` placeholders) or a mapping
        # (for ``:name`` placeholders). Preserve mappings; tuple-ify sequences.
        if isinstance(params, Mapping):
            return params
        return tuple(params)

    def _execute(self, sql: str, params: "Union[Mapping[str, Any], Iterable[Any]]" = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, self._bind(params))
            self._conn.commit()
            return cur

    def _query(self, sql: str, params: "Union[Mapping[str, Any], Iterable[Any]]" = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(sql, self._bind(params))
            return cur.fetchall()

    def _query_one(self, sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    @staticmethod
    def _loads(value: Any, default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _dumps(value: Any) -> str:
        return json.dumps(value or {})

    # ------------------------------------------------------------------ #
    # Schema
    # ------------------------------------------------------------------ #
    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE TABLE IF NOT EXISTS projects (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    root_path TEXT NOT NULL UNIQUE,
                    project_type TEXT DEFAULT 'unknown',
                    description TEXT DEFAULT '',
                    last_opened_at REAL,
                    created_at REAL,
                    metadata TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    title TEXT,
                    project_id TEXT,
                    model TEXT,
                    autonomy_level TEXT,
                    created_at REAL,
                    updated_at REAL,
                    metadata TEXT DEFAULT '{}',
                    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    name TEXT,
                    tool_call_id TEXT,
                    created_at REAL,
                    token_count INTEGER,
                    metadata TEXT DEFAULT '{}',
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS tool_calls (
                    id TEXT PRIMARY KEY,
                    session_id TEXT,
                    name TEXT NOT NULL,
                    arguments TEXT DEFAULT '{}',
                    status TEXT,
                    output TEXT DEFAULT '',
                    error TEXT,
                    risky INTEGER DEFAULT 0,
                    created_at REAL,
                    finished_at REAL
                );

                CREATE TABLE IF NOT EXISTS command_approvals (
                    id TEXT PRIMARY KEY,
                    project_id TEXT,
                    command TEXT NOT NULL,
                    category TEXT DEFAULT 'general',
                    decision TEXT,
                    always INTEGER DEFAULT 0,
                    created_at REAL
                );

                CREATE TABLE IF NOT EXISTS files_index (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    language TEXT DEFAULT '',
                    size INTEGER DEFAULT 0,
                    mtime REAL DEFAULT 0,
                    summary TEXT DEFAULT '',
                    symbols TEXT DEFAULT '[]',
                    content_excerpt TEXT DEFAULT '',
                    indexed_at REAL,
                    UNIQUE (project_id, path)
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_sessions_updated
                    ON sessions(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_toolcalls_session
                    ON tool_calls(session_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_files_project
                    ON files_index(project_id, path);
                CREATE INDEX IF NOT EXISTS idx_approvals_project
                    ON command_approvals(project_id);
                """
            )
            self._conn.commit()
            self.set_setting("__schema_version__", str(SCHEMA_VERSION))

    # ------------------------------------------------------------------ #
    # Settings (key/value)
    # ------------------------------------------------------------------ #
    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._query_one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        self._execute(
            "INSERT INTO settings(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_setting_json(self, key: str, default: Any = None) -> Any:
        raw = self.get_setting(key)
        return self._loads(raw, default)

    def set_setting_json(self, key: str, value: Any) -> None:
        self.set_setting(key, json.dumps(value))

    # ------------------------------------------------------------------ #
    # Projects
    # ------------------------------------------------------------------ #
    def upsert_project(self, project: Project) -> Project:
        self._execute(
            """
            INSERT INTO projects(id, name, root_path, project_type, description,
                                 last_opened_at, created_at, metadata)
            VALUES(:id, :name, :root_path, :project_type, :description,
                   :last_opened_at, :created_at, :metadata)
            ON CONFLICT(root_path) DO UPDATE SET
                name=excluded.name,
                project_type=excluded.project_type,
                description=excluded.description,
                last_opened_at=excluded.last_opened_at,
                metadata=excluded.metadata
            """,
            {
                "id": project.id,
                "name": project.name,
                "root_path": project.root_path,
                "project_type": project.project_type,
                "description": project.description,
                "last_opened_at": project.last_opened_at,
                "created_at": project.created_at,
                "metadata": self._dumps(project.metadata),
            },
        )
        existing = self.get_project_by_path(project.root_path)
        return existing or project

    def list_projects(self, limit: int = 100) -> list[Project]:
        rows = self._query(
            "SELECT * FROM projects ORDER BY last_opened_at DESC LIMIT ?", (limit,)
        )
        return [self._row_to_project(r) for r in rows]

    def get_project(self, project_id: str) -> Optional[Project]:
        row = self._query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        return self._row_to_project(row) if row else None

    def get_project_by_path(self, root_path: str) -> Optional[Project]:
        row = self._query_one("SELECT * FROM projects WHERE root_path = ?", (root_path,))
        return self._row_to_project(row) if row else None

    def delete_project(self, project_id: str) -> None:
        self._execute("DELETE FROM projects WHERE id = ?", (project_id,))

    def _row_to_project(self, r: sqlite3.Row) -> Project:
        return Project(
            id=r["id"],
            name=r["name"],
            root_path=r["root_path"],
            project_type=r["project_type"] or "unknown",
            description=r["description"] or "",
            last_opened_at=r["last_opened_at"] or 0.0,
            created_at=r["created_at"] or 0.0,
            metadata=self._loads(r["metadata"], {}),
        )

    # ------------------------------------------------------------------ #
    # Sessions
    # ------------------------------------------------------------------ #
    def create_session(self, session: Optional[Session] = None, **kwargs: Any) -> Session:
        session = session or Session(**kwargs)
        self._execute(
            """
            INSERT INTO sessions(id, title, project_id, model, autonomy_level,
                                 created_at, updated_at, metadata)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session.id,
                session.title,
                session.project_id,
                session.model,
                session.autonomy_level.value,
                session.created_at,
                session.updated_at,
                self._dumps(session.metadata),
            ),
        )
        return session

    def list_sessions(self, project_id: Optional[str] = None, limit: int = 200) -> list[Session]:
        if project_id is not None:
            rows = self._query(
                "SELECT * FROM sessions WHERE project_id = ? ORDER BY updated_at DESC LIMIT ?",
                (project_id, limit),
            )
        else:
            rows = self._query(
                "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
        return [self._row_to_session(r) for r in rows]

    def get_session(self, session_id: str) -> Optional[Session]:
        row = self._query_one("SELECT * FROM sessions WHERE id = ?", (session_id,))
        return self._row_to_session(row) if row else None

    def update_session(self, session_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {"title", "project_id", "model", "autonomy_level", "updated_at", "metadata"}
        sets, params = [], []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "autonomy_level" and isinstance(v, AutonomyLevel):
                v = v.value
            if k == "metadata" and isinstance(v, dict):
                v = self._dumps(v)
            sets.append(f"{k} = ?")
            params.append(v)
        if "updated_at" not in fields:
            sets.append("updated_at = ?")
            params.append(now_ts())
        params.append(session_id)
        self._execute(f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?", params)

    def touch_session(self, session_id: str) -> None:
        self._execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now_ts(), session_id))

    def delete_session(self, session_id: str) -> None:
        self._execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def _row_to_session(self, r: sqlite3.Row) -> Session:
        return Session(
            id=r["id"],
            title=r["title"] or "New Chat",
            project_id=r["project_id"],
            model=r["model"],
            autonomy_level=AutonomyLevel.from_value(r["autonomy_level"]),
            created_at=r["created_at"] or 0.0,
            updated_at=r["updated_at"] or 0.0,
            metadata=self._loads(r["metadata"], {}),
        )

    # ------------------------------------------------------------------ #
    # Messages
    # ------------------------------------------------------------------ #
    def add_message(self, message: Optional[Message] = None, **kwargs: Any) -> Message:
        message = message or Message(**kwargs)
        self._execute(
            """
            INSERT INTO messages(id, session_id, role, content, name, tool_call_id,
                                 created_at, token_count, metadata)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.id,
                message.session_id,
                message.role.value,
                message.content,
                message.name,
                message.tool_call_id,
                message.created_at,
                message.token_count,
                self._dumps(message.metadata),
            ),
        )
        self.touch_session(message.session_id)
        return message

    def list_messages(self, session_id: str, limit: int = 1000) -> list[Message]:
        rows = self._query(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
            (session_id, limit),
        )
        return [self._row_to_message(r) for r in rows]

    def delete_messages(self, session_id: str) -> None:
        self._execute("DELETE FROM messages WHERE session_id = ?", (session_id,))

    def _row_to_message(self, r: sqlite3.Row) -> Message:
        return Message(
            id=r["id"],
            session_id=r["session_id"],
            role=MessageRole.from_value(r["role"]),
            content=r["content"] or "",
            name=r["name"],
            tool_call_id=r["tool_call_id"],
            created_at=r["created_at"] or 0.0,
            token_count=r["token_count"],
            metadata=self._loads(r["metadata"], {}),
        )

    # ------------------------------------------------------------------ #
    # Tool calls
    # ------------------------------------------------------------------ #
    def log_tool_call(self, tool_call: ToolCall) -> ToolCall:
        self._execute(
            """
            INSERT INTO tool_calls(id, session_id, name, arguments, status, output,
                                   error, risky, created_at, finished_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                output=excluded.output,
                error=excluded.error,
                finished_at=excluded.finished_at
            """,
            (
                tool_call.id,
                tool_call.session_id,
                tool_call.name,
                self._dumps(tool_call.arguments),
                tool_call.status.value,
                tool_call.output,
                tool_call.error,
                1 if tool_call.risky else 0,
                tool_call.created_at,
                tool_call.finished_at,
            ),
        )
        return tool_call

    def list_tool_calls(self, session_id: str, limit: int = 500) -> list[ToolCall]:
        rows = self._query(
            "SELECT * FROM tool_calls WHERE session_id = ? ORDER BY created_at ASC LIMIT ?",
            (session_id, limit),
        )
        return [self._row_to_tool_call(r) for r in rows]

    def _row_to_tool_call(self, r: sqlite3.Row) -> ToolCall:
        return ToolCall(
            id=r["id"],
            session_id=r["session_id"] or "",
            name=r["name"],
            arguments=self._loads(r["arguments"], {}),
            status=ToolStatus(r["status"]) if r["status"] else ToolStatus.PENDING,
            output=r["output"] or "",
            error=r["error"],
            risky=bool(r["risky"]),
            created_at=r["created_at"] or 0.0,
            finished_at=r["finished_at"],
        )

    # ------------------------------------------------------------------ #
    # Command approvals
    # ------------------------------------------------------------------ #
    def add_command_approval(self, approval: CommandApproval) -> CommandApproval:
        self._execute(
            """
            INSERT INTO command_approvals(id, project_id, command, category,
                                          decision, always, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval.id,
                approval.project_id,
                approval.command,
                approval.category,
                approval.decision.value,
                1 if approval.always else 0,
                approval.created_at,
            ),
        )
        return approval

    def list_command_approvals(
        self, project_id: Optional[str] = None, only_always: bool = False
    ) -> list[CommandApproval]:
        clauses, params = [], []
        if project_id is not None:
            clauses.append("project_id = ?")
            params.append(project_id)
        if only_always:
            clauses.append("always = 1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._query(
            f"SELECT * FROM command_approvals {where} ORDER BY created_at DESC", params
        )
        return [self._row_to_approval(r) for r in rows]

    def find_always_allow(self, category: str, project_id: Optional[str] = None) -> bool:
        """Return True if an 'always allow' approval exists for this category/project."""
        row = self._query_one(
            "SELECT 1 FROM command_approvals "
            "WHERE always = 1 AND category = ? AND (project_id IS ? OR project_id = ?) LIMIT 1",
            (category, project_id, project_id),
        )
        return row is not None

    def _row_to_approval(self, r: sqlite3.Row) -> CommandApproval:
        return CommandApproval(
            id=r["id"],
            project_id=r["project_id"],
            command=r["command"],
            category=r["category"] or "general",
            decision=ApprovalDecision(r["decision"]) if r["decision"] else ApprovalDecision.APPROVE_ONCE,
            always=bool(r["always"]),
            created_at=r["created_at"] or 0.0,
        )

    # ------------------------------------------------------------------ #
    # File index
    # ------------------------------------------------------------------ #
    def upsert_file_index(self, entry: FileIndexEntry) -> FileIndexEntry:
        self._execute(
            """
            INSERT INTO files_index(id, project_id, path, language, size, mtime,
                                    summary, symbols, content_excerpt, indexed_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, path) DO UPDATE SET
                language=excluded.language,
                size=excluded.size,
                mtime=excluded.mtime,
                summary=excluded.summary,
                symbols=excluded.symbols,
                content_excerpt=excluded.content_excerpt,
                indexed_at=excluded.indexed_at
            """,
            (
                entry.id,
                entry.project_id,
                entry.path,
                entry.language,
                entry.size,
                entry.mtime,
                entry.summary,
                self._dumps(entry.symbols),
                entry.content_excerpt,
                entry.indexed_at,
            ),
        )
        return entry

    def search_file_index(
        self, project_id: str, query: str, limit: int = 50
    ) -> list[FileIndexEntry]:
        """Relevance-ranked keyword search over the file index.

        The previous implementation matched the *entire raw query* as one
        ``LIKE '%...%'`` substring, so a natural-language request ("build a
        FastAPI app with auth") matched no files and the agent edited with zero
        context. This tokenizes the query, scores each file (filename/symbol
        matches rank highest, then summary, then excerpt), and orders by score —
        with a most-recently-modified fallback so the agent always gets *some*
        relevant files to work from.
        """
        rows = self._query(
            "SELECT * FROM files_index WHERE project_id = ? LIMIT 20000",
            (project_id,),
        )
        entries = [self._row_to_file_index(r) for r in rows]
        tokens = _tokenize_query(query)
        if not tokens:
            entries.sort(key=lambda e: e.mtime, reverse=True)
            return entries[:limit]
        scored = [(_score_index_entry(e, tokens), e) for e in entries]
        scored = [(s, e) for s, e in scored if s > 0]
        if not scored:
            entries.sort(key=lambda e: e.mtime, reverse=True)
            return entries[: min(limit, 8)]
        scored.sort(key=lambda se: (se[0], se[1].mtime), reverse=True)
        return [e for _, e in scored[:limit]]

    def delete_file_index(self, project_id: str, path: str) -> None:
        """Remove a single file's index row (after delete/rename)."""
        self._execute(
            "DELETE FROM files_index WHERE project_id = ? AND path = ?",
            (project_id, path),
        )

    def list_file_index(self, project_id: str, limit: int = 5000) -> list[FileIndexEntry]:
        rows = self._query(
            "SELECT * FROM files_index WHERE project_id = ? ORDER BY path ASC LIMIT ?",
            (project_id, limit),
        )
        return [self._row_to_file_index(r) for r in rows]

    def clear_file_index(self, project_id: str) -> None:
        self._execute("DELETE FROM files_index WHERE project_id = ?", (project_id,))

    def _row_to_file_index(self, r: sqlite3.Row) -> FileIndexEntry:
        return FileIndexEntry(
            id=r["id"],
            project_id=r["project_id"],
            path=r["path"],
            language=r["language"] or "",
            size=r["size"] or 0,
            mtime=r["mtime"] or 0.0,
            summary=r["summary"] or "",
            symbols=self._loads(r["symbols"], []),
            content_excerpt=r["content_excerpt"] or "",
            indexed_at=r["indexed_at"] or 0.0,
        )


# --------------------------------------------------------------------------- #
# File-index search scoring (keyword relevance, no embeddings)
# --------------------------------------------------------------------------- #
_SEARCH_STOPWORDS = frozenset(
    {
        "the", "a", "an", "to", "of", "in", "on", "for", "and", "or", "with",
        "is", "are", "be", "this", "that", "it", "as", "at", "by", "from",
        "add", "make", "build", "create", "implement", "fix", "update", "change",
        "file", "files", "app", "code", "function", "please", "can", "you", "i",
        "me", "my", "use", "using", "new", "into", "so", "then", "all",
    }
)


def _tokenize_query(query: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tok in re.split(r"[^A-Za-z0-9_]+", (query or "").lower()):
        if len(tok) < 2 or tok in _SEARCH_STOPWORDS:
            continue
        if tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out[:16]


def _score_index_entry(entry: "FileIndexEntry", tokens: list[str]) -> int:
    path = (entry.path or "").lower()
    base = path.rsplit("/", 1)[-1]
    symbols = [s.lower() for s in (entry.symbols or [])]
    symbol_blob = " ".join(symbols)
    summary = (entry.summary or "").lower()
    excerpt = (entry.content_excerpt or "").lower()
    score = 0
    for tok in tokens:
        if tok in base:
            score += 6
        elif tok in path:
            score += 4
        if tok in symbols:
            score += 5
        elif tok in symbol_blob:
            score += 3
        if tok in summary:
            score += 2
        if tok in excerpt:
            score += 1
    return score


__all__ = ["Database", "SCHEMA_VERSION"]
