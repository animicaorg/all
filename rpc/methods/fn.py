"""
rpc.methods.fn — Animica Studio function registry (aicf.fn.*)
=============================================================

The control-plane store for Studio function deployments. A Studio app's
``runner_remote.deploy()`` builds one deploy-meta dict per function and
ships it to the broker via ``aicf.fn.deploy``; the worker side later
resolves a function's image / entrypoint by name via ``aicf.fn.get``.

Deploy meta shape (produced by runner_remote.RemoteRunner.deploy):

    {
      "name":       "<function name>",     # registry key
      "app":        "<app name>",
      "image_ref":  "studio-img:...",
      "image":      {...},                  # Image.to_spec()
      "gpu":        "<str|null>",
      "timeout":    <int seconds>,
      "schedule":   <spec|null>,
      "entrypoint": "<module>:<qualname>",
    }

Methods exposed (reachable at POST /rpc):

    aicf.fn.deploy(meta)      -> {ok, name, version}
    aicf.fn.get({name})       -> the stored meta (+ name/version/created_at)
    aicf.fn.list()            -> {items: [...]}
    aicf.fn.delete({name})    -> {ok}

Storage: a single SQLite file at ``$AICF_DB_DIR/functions.sqlite3``
(defaults to ``~/.animica/aicf/functions.sqlite3``). Like the rest of the
AICF SQLite stores it lives in the shared AICF tree so it can be inspected
/ backed up out-of-band. ``deploy`` upserts by ``name`` and auto-increments
``version`` on every redeploy, retaining a full version history row-per-row
while ``get`` / ``list`` always surface the latest version of each name.

Dependency-light by design: stdlib ``sqlite3`` + ``json`` only. The module
holds one long-lived module-level connection (mirrors the "module-level
connection like work.py" brief); SQLite serializes its own writers and we
open with ``check_same_thread=False`` + WAL so the FastAPI worker pool can
share it. A module-level ``RLock`` guards the connection because sqlite3
connection objects are not safe to use concurrently from multiple threads.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from rpc.errors import InvalidParams
from rpc.methods import register

log = logging.getLogger("rpc.methods.fn")


# ---------------------------------------------------------------------------
# DB path resolution + schema
# ---------------------------------------------------------------------------

def _default_base_dir() -> Path:
    """Resolve the AICF data dir (``$AICF_DB_DIR`` or ``~/.animica/aicf``).

    Mirrors the path convention used by ``aicf.work.db`` so the function
    registry slots into the same on-disk tree as the rest of AICF.
    """
    override = os.environ.get("AICF_DB_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".animica" / "aicf"


def _default_db_path() -> Path:
    base = _default_base_dir()
    # Create the dir if missing — first deploy on a fresh node must not 500.
    base.mkdir(parents=True, exist_ok=True)
    return base / "functions.sqlite3"


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS functions (
    name        TEXT NOT NULL,
    version     INTEGER NOT NULL,
    meta_json   TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    PRIMARY KEY (name, version)
);
CREATE INDEX IF NOT EXISTS idx_functions_name ON functions(name);
"""


# ---------------------------------------------------------------------------
# Module-level connection (mirrors work.py's "module-level connection" brief)
# ---------------------------------------------------------------------------

_CONN: Optional[sqlite3.Connection] = None
_LOCK = threading.RLock()


def _connect() -> sqlite3.Connection:
    """Open (or return) the long-lived module-level connection.

    Idempotent: initialises the schema on first connect. WAL + busy_timeout
    keep the file safe to share across the FastAPI worker pool (and with an
    external reader inspecting the DB).
    """
    global _CONN
    if _CONN is not None:
        return _CONN
    with _LOCK:
        if _CONN is not None:
            return _CONN
        path = str(_default_db_path())
        conn = sqlite3.connect(
            path,
            timeout=10.0,
            isolation_level=None,      # autocommit; we never span statements
            check_same_thread=False,   # shared across the worker pool under _LOCK
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA_SQL)
        _CONN = conn
        log.info("aicf.fn function registry DB ready at %s", path)
        return _CONN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coerce_meta(meta: Any) -> Dict[str, Any]:
    """Bind the single ``meta`` param whether it arrived as a dict or a
    JSON string. JSON-RPC dispatch passes object params straight through as
    a dict; tolerate a stringified payload too."""
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except (ValueError, TypeError) as exc:
            raise InvalidParams(f"meta must be a JSON object: {exc}") from exc
    if not isinstance(meta, dict):
        raise InvalidParams("meta must be an object")
    return meta


def _row_to_meta(row: sqlite3.Row) -> Dict[str, Any]:
    """Re-hydrate a stored row into the deploy meta plus registry fields."""
    try:
        meta = json.loads(row["meta_json"])
    except (ValueError, TypeError):
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    out = dict(meta)
    out["name"] = row["name"]
    out["version"] = int(row["version"])
    out["created_at"] = int(row["created_at"])
    return out


# ---------------------------------------------------------------------------
# RPC handlers
# ---------------------------------------------------------------------------

def fn_deploy(meta: Any) -> Dict[str, Any]:
    """Upsert a function deploy-meta by name; auto-increment its version.

    Returns ``{"ok": True, "name": <name>, "version": <n>}``. Each deploy
    records a new ``(name, version)`` row, so the table retains the full
    redeploy history while ``get`` / ``list`` read the latest version.
    """
    meta = _coerce_meta(meta)
    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        raise InvalidParams("meta.name must be a non-empty string")
    name = name.strip()

    conn = _connect()
    with _LOCK:
        row = conn.execute(
            "SELECT MAX(version) AS v FROM functions WHERE name=?", (name,)
        ).fetchone()
        prev = int(row["v"]) if row is not None and row["v"] is not None else 0
        version = prev + 1
        conn.execute(
            "INSERT INTO functions (name, version, meta_json, created_at) "
            "VALUES (?,?,?,?)",
            (name, version, json.dumps(meta), int(time.time())),
        )
    log.info("aicf.fn.deploy name=%s version=%d", name, version)
    return {"ok": True, "name": name, "version": version}


def fn_get(name: str) -> Dict[str, Any]:
    """Return the latest stored deploy-meta for ``name``.

    Raises ``InvalidParams`` (not-found is a caller mistake) when the
    function has never been deployed.
    """
    if not isinstance(name, str) or not name.strip():
        raise InvalidParams("name must be a non-empty string")
    name = name.strip()
    conn = _connect()
    with _LOCK:
        row = conn.execute(
            "SELECT name, version, meta_json, created_at FROM functions "
            "WHERE name=? ORDER BY version DESC LIMIT 1",
            (name,),
        ).fetchone()
    if row is None:
        raise InvalidParams(f"function not found: {name}")
    return _row_to_meta(row)


def fn_list() -> Dict[str, Any]:
    """List the latest version of every deployed function.

    Returns ``{"items": [...]}`` ordered by most-recently-created first.
    Only the newest ``(name, version)`` row per name is surfaced.
    """
    conn = _connect()
    with _LOCK:
        # For each name pick the row with the highest version. A correlated
        # MAX(version) subquery keeps this to stdlib SQL with no window funcs.
        rows = conn.execute(
            "SELECT f.name AS name, f.version AS version, f.meta_json AS meta_json, "
            "       f.created_at AS created_at "
            "FROM functions f "
            "WHERE f.version = ("
            "    SELECT MAX(g.version) FROM functions g WHERE g.name = f.name"
            ") "
            "ORDER BY f.created_at DESC, f.name ASC"
        ).fetchall()
    items: List[Dict[str, Any]] = [_row_to_meta(r) for r in rows]
    return {"items": items}


def fn_delete(name: str) -> Dict[str, Any]:
    """Delete every version of ``name`` from the registry.

    Idempotent: deleting an unknown name still returns ``{"ok": True}`` so
    teardown scripts don't have to special-case the not-found path.
    """
    if not isinstance(name, str) or not name.strip():
        raise InvalidParams("name must be a non-empty string")
    name = name.strip()
    conn = _connect()
    with _LOCK:
        cur = conn.execute("DELETE FROM functions WHERE name=?", (name,))
        deleted = int(cur.rowcount or 0)
    log.info("aicf.fn.delete name=%s rows=%d", name, deleted)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Registration — mirrors rpc.methods.work's import-time install
# ---------------------------------------------------------------------------

_METHODS: Dict[str, Any] = {
    "aicf.fn.deploy": (fn_deploy, "Deploy (upsert) a Studio function; auto-increments version."),
    "aicf.fn.get":    (fn_get,    "Fetch the latest deploy meta for a function by name."),
    "aicf.fn.list":   (fn_list,   "List the latest version of every deployed function."),
    "aicf.fn.delete": (fn_delete, "Delete all versions of a function from the registry."),
}


def _install() -> int:
    count = 0
    for name, (fn, desc) in _METHODS.items():
        try:
            register(name, fn, desc=desc)
            count += 1
        except Exception as exc:  # noqa: BLE001
            log.exception("Failed to register %s: %s", name, exc)
    log.info("Registered %d aicf.fn.* methods", count)
    return count


# Import-time side effect — matches the convention of every other module in
# this package (rpc.methods.* register via decorators / install at import).
_install()
