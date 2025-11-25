"""
capabilities.cli.list_jobs
==========================

Inspect queued/leased/completed capability jobs and their statuses.

By default this inspects the *capabilities* job queue (SQLite/Rocks-backed)
but it can also try listing from AICF if that storage is available.

Backends (auto-detected unless --backend is specified):
  - queue : capabilities.jobs.queue (local job queue DB)
  - aicf  : aicf.queue.storage (provider scheduler DB), if installed

Examples:
  # List most recent 50 jobs from local queue DB
  python -m capabilities.cli list-jobs

  # Only AI jobs that are still queued or leased, with a wider column set
  python -m capabilities.cli list-jobs --kind ai --status queued --status leased --wide

  # Show machine-readable JSON
  python -m capabilities.cli list-jobs --json

  # Point at a specific queue DB file and sort oldest-first
  python -m capabilities.cli list-jobs --queue-db ./capabilities_jobs.db --desc false

Columns:
  You can customize which columns are shown with --columns "id,kind,status,priority,created_at,lease_expires,caller,height"

This module exposes `register(app)` for dynamic discovery by
capabilities/cli/__init__.py.
"""

from __future__ import annotations

import json
import os
import sys
import inspect
import datetime as _dt
from dataclasses import asdict, is_dataclass
from importlib import import_module
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    import typer  # type: ignore
except Exception as e:  # pragma: no cover
    raise ImportError("Typer is required. Install with: pip install typer[all]") from e

app = typer.Typer(
    name="list-jobs",
    help="Inspect capability jobs & statuses (queue and/or AICF).",
    add_completion=False,
)

COMMAND_NAME = "list-jobs"


# --------------------------- helpers ---------------------------

def _call_with_supported_kwargs(fn, **kwargs):
    """
    Introspect `fn` and call it with only the kwargs it accepts.
    This helps tolerate minor API differences across backends.
    """
    sig = None
    try:
        sig = inspect.signature(fn)
    except Exception:
        pass

    if sig is None:
        return fn(**kwargs)  # type: ignore

    accepted = {
        k: v
        for k, v in kwargs.items()
        if k in sig.parameters or any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    }
    return fn(**accepted)


def _to_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if is_dataclass(obj):
        try:
            return asdict(obj)  # type: ignore
        except Exception:
            return obj.__dict__  # type: ignore
    if isinstance(obj, Mapping):
        return dict(obj)
    if hasattr(obj, "__dict__"):
        return dict(obj.__dict__)  # type: ignore
    # Fallback: best-effort string
    return {"value": str(obj)}


def _parse_bool(s: Optional[str]) -> Optional[bool]:
    if s is None:
        return None
    t = s.strip().lower()
    if t in {"1", "true", "yes", "y"}:
        return True
    if t in {"0", "false", "no", "n"}:
        return False
    return None


def _fmt_ts(ts: Any) -> str:
    """
    Attempt to render various timestamp shapes:
    - int/float (epoch seconds)
    - ISO 8601 strings
    - datetime objects
    """
    if ts is None:
        return ""
    try:
        if isinstance(ts, (int, float)):
            return _dt.datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
        if isinstance(ts, str):
            # If it's already ISO-like, return as is; otherwise try parse as epoch
            if "T" in ts or "-" in ts or ":" in ts:
                return ts
            try:
                return _dt.datetime.fromtimestamp(float(ts)).isoformat(timespec="seconds")
            except Exception:
                return ts
        if isinstance(ts, _dt.datetime):
            return ts.isoformat(timespec="seconds")
    except Exception:
        pass
    return str(ts)


def _normalize_job(job: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalize heterogenous job records into a common set of keys.
    We keep original keys as well (merged) to support --columns for custom fields.
    Common keys attempted:
      id, task_id, kind, status, priority, created_at, updated_at, lease_expires,
      caller, model, circuit_size, shots, height, error
    """
    j = dict(job)  # shallow copy

    # id / task_id
    jid = j.get("id") or j.get("job_id") or j.get("task_id") or j.get("uuid")
    if jid is not None:
        j.setdefault("id", jid)
        j.setdefault("task_id", jid)

    # kind
    kind = j.get("kind") or j.get("job_kind") or j.get("type") or j.get("proof_kind")
    if isinstance(kind, dict) and "name" in kind:
        kind = kind["name"]
    j.setdefault("kind", kind)

    # status
    status = j.get("status") or j.get("state") or j.get("job_status")
    j.setdefault("status", status)

    # priority
    if "priority" not in j:
        j["priority"] = j.get("score") or j.get("pri")

    # timestamps
    for k in ("created_at", "updated_at", "lease_expires"):
        v = j.get(k) or j.get(k.replace("_", ""))
        if v is not None:
            j[k] = _fmt_ts(v)

    # caller
    if "caller" not in j:
        j["caller"] = j.get("requester") or j.get("owner") or j.get("account")

    # model / circuit
    if "model" not in j:
        j["model"] = j.get("device") or j.get("provider_model")
    if "shots" not in j:
        j["shots"] = j.get("nshots") or j.get("samples")

    # circuit size (best-effort; many schemas vary)
    if "circuit_size" not in j:
        circ = j.get("circuit") or j.get("payload") or {}
        try:
            if isinstance(circ, str):
                # maybe JSON
                import json as _json
                circ = _json.loads(circ)
            gates = circ.get("gates") if isinstance(circ, dict) else None
            if isinstance(gates, Sequence):
                j["circuit_size"] = len(gates)
        except Exception:
            pass

    # height hint
    if "height" not in j:
        j["height"] = j.get("block_height") or j.get("claimed_at_height") or j.get("resolved_height")

    # error (if any)
    if "error" not in j:
        j["error"] = j.get("last_error") or j.get("reason")

    return j


def _print_table(rows: List[Dict[str, Any]], columns: Sequence[str]) -> None:
    if not rows:
        typer.echo("(no jobs)")
        return
    # Compute widths
    widths = {c: len(c) for c in columns}
    for r in rows:
        for c in columns:
            v = r.get(c)
            s = "" if v is None else str(v)
            if len(s) > widths[c]:
                widths[c] = min(len(s), 120)  # cap very wide fields

    # Header
    line = "  ".join(f"{c.upper():<{widths[c]}}" for c in columns)
    typer.echo(line)
    typer.echo("-" * len(line))

    # Rows
    for r in rows:
        parts = []
        for c in columns:
            v = r.get(c)
            s = "" if v is None else str(v)
            if len(s) > 120:
                s = s[:117] + "..."
            parts.append(f"{s:<{widths[c]}}")
        typer.echo("  ".join(parts))


def _emit(rows: List[Dict[str, Any]], as_json: bool, columns: Sequence[str]) -> None:
    if as_json:
        sys.stdout.write(json.dumps(rows, separators=(",", ":"), sort_keys=True) + "\n")
    else:
        _print_table(rows, columns)


# --------------------------- backends ---------------------------

def _list_via_queue(
    queue_db: Path,
    kind: Optional[str],
    status: Optional[List[str]],
    caller: Optional[str],
    limit: int,
    offset: int,
    sort: str,
    desc: bool,
) -> List[Dict[str, Any]]:
    """
    capabilities.jobs.queue: JobQueue.list_jobs(...) or module-level list_jobs(...)
    """
    mod = import_module("capabilities.jobs.queue")

    # Open queue
    q = None
    JobQueue = getattr(mod, "JobQueue", None)
    if JobQueue is not None:
        q = JobQueue(str(queue_db))
    else:
        open_q = getattr(mod, "open_queue", None)
        q = open_q(str(queue_db)) if callable(open_q) else None

    # List jobs (prefer instance method)
    jobs = None
    if q is not None and hasattr(q, "list_jobs"):
        jobs = _call_with_supported_kwargs(
            getattr(q, "list_jobs"),
            kind=kind,
            status=status,
            caller=caller,
            limit=limit,
            offset=offset,
            sort=sort,
            desc=desc,
        )
    else:
        list_jobs_fn = getattr(mod, "list_jobs", None)
        if not callable(list_jobs_fn):
            raise RuntimeError("Queue backend does not expose list_jobs")
        jobs = _call_with_supported_kwargs(
            list_jobs_fn,
            q=q,
            kind=kind,
            status=status,
            caller=caller,
            limit=limit,
            offset=offset,
            sort=sort,
            desc=desc,
        )

    # Normalize result to list of dicts
    out: List[Dict[str, Any]] = []
    if isinstance(jobs, Iterable):
        for j in jobs:
            out.append(_normalize_job(_to_dict(j)))
    else:
        out.append(_normalize_job(_to_dict(jobs)))
    return out


def _list_via_aicf(
    store_db: Optional[Path],
    kind: Optional[str],
    status: Optional[List[str]],
    caller: Optional[str],
    limit: int,
    offset: int,
    sort: str,
    desc: bool,
) -> List[Dict[str, Any]]:
    """
    aicf.queue.storage: JobStore.list_jobs(...) or similar.
    If no explicit DB is given, will rely on the AICF module defaults.
    """
    mod = import_module("aicf.queue.storage")

    # Open store
    store = None
    JobStore = getattr(mod, "JobStore", None)
    open_store = getattr(mod, "open_store", None)
    if JobStore is not None and store_db is not None:
        store = JobStore(str(store_db))
    elif callable(open_store):
        store = _call_with_supported_kwargs(open_store, path=str(store_db) if store_db else None)
    else:
        # If neither exists, try a module-level getter
        get_store = getattr(mod, "get_store", None)
        store = get_store() if callable(get_store) else None

    # List jobs
    list_fn = getattr(store, "list_jobs", None) if store is not None else None
    if callable(list_fn):
        jobs = _call_with_supported_kwargs(
            list_fn,
            kind=kind,
            status=status,
            caller=caller,
            limit=limit,
            offset=offset,
            sort=sort,
            desc=desc,
        )
    else:
        # Try module-level function
        list_jobs_fn = getattr(mod, "list_jobs", None)
        if not callable(list_jobs_fn):
            raise RuntimeError("AICF storage backend does not expose list_jobs")
        jobs = _call_with_supported_kwargs(
            list_jobs_fn,
            store=store,
            kind=kind,
            status=status,
            caller=caller,
            limit=limit,
            offset=offset,
            sort=sort,
            desc=desc,
        )

    # Normalize
    out: List[Dict[str, Any]] = []
    if isinstance(jobs, Iterable):
        for j in jobs:
            out.append(_normalize_job(_to_dict(j)))
    else:
        out.append(_normalize_job(_to_dict(jobs)))
    return out


# --------------------------- CLI ---------------------------

@app.command("list-jobs")
def list_jobs_cmd(
    backend: Optional[str] = typer.Option(
        None,
        "--backend",
        help="Force backend: queue | aicf (default: auto, try queue then aicf).",
    ),
    queue_db: Path = typer.Option(
        Path(os.getenv("CAP_QUEUE_DB", "./capabilities_jobs.db")),
        "--queue-db",
        help="Path to capabilities job queue DB (for backend=queue).",
    ),
    aicf_db: Optional[Path] = typer.Option(
        None,
        "--aicf-db",
        help="Optional path to AICF store DB (if backend=aicf and your build supports it).",
    ),
    kind: Optional[str] = typer.Option(
        None, "--kind", help="Filter by job kind (ai|quantum)."
    ),
    status: Optional[List[str]] = typer.Option(
        None,
        "--status",
        help="Filter by status; repeat for multiple (e.g., --status queued --status leased).",
    ),
    caller: Optional[str] = typer.Option(
        None, "--caller", help="Filter by caller address substring."
    ),
    limit: int = typer.Option(50, "--limit", min=1, max=1000, help="Max rows to return."),
    offset: int = typer.Option(0, "--offset", min=0, help="Offset for pagination."),
    sort: str = typer.Option(
        "created_at",
        "--sort",
        help="Sort key if supported by backend (e.g., created_at, priority, id).",
    ),
    desc: bool = typer.Option(
        True,
        "--desc/--asc",
        help="Sort order (descending by default).",
    ),
    columns: Optional[str] = typer.Option(
        None,
        "--columns",
        help="Comma-separated columns to display (default depends on --wide).",
    ),
    wide: bool = typer.Option(
        False,
        "--wide",
        help="Show a wider set of columns.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Emit machine-readable JSON array."),
) -> None:
    """
    List capability jobs and print their statuses.
    """
    # choose default columns
    if columns:
        cols = [c.strip() for c in columns.split(",") if c.strip()]
    else:
        cols = (
            ["id", "kind", "status", "priority", "created_at", "lease_expires", "caller"]
            if not wide
            else ["id", "kind", "status", "priority", "created_at", "lease_expires", "caller", "model", "shots", "height", "error"]
        )

    rows: List[Dict[str, Any]] = []
    errors: List[str] = []

    def try_queue() -> Optional[List[Dict[str, Any]]]:
        try:
            return _list_via_queue(queue_db, kind, status, caller, limit, offset, sort, desc)
        except Exception as e:
            errors.append(f"queue: {e}")
            return None

    def try_aicf() -> Optional[List[Dict[str, Any]]]:
        try:
            return _list_via_aicf(aicf_db, kind, status, caller, limit, offset, sort, desc)
        except Exception as e:
            errors.append(f"aicf: {e}")
            return None

    if backend:
        backend = backend.strip().lower()
        if backend == "queue":
            rows = try_queue() or []
        elif backend == "aicf":
            rows = try_aicf() or []
        else:
            typer.echo(f"Unknown backend '{backend}'. Use queue|aicf.")
            raise typer.Exit(2)
    else:
        # auto: prefer local queue first
        rows = try_queue() or try_aicf() or []

    if not rows and errors and not json_out:
        typer.echo("No rows returned. Backend errors encountered:")
        for e in errors:
            typer.echo(f"  - {e}")

    _emit(rows, json_out, cols)


def register(root_app: "typer.Typer") -> None:
    """
    Hook for capabilities.cli to attach this command.
    """
    root_app.add_typer(app, name=COMMAND_NAME)
