from __future__ import annotations

"""
aicf.cli.queue_list
-------------------

List items in the AICF queue:
- queued/pending jobs (with computed priority when possible)
- active leases

This CLI is backend-adaptive:
- If aicf.queue.storage is available, it will try common list methods.
- If not, it will fall back to any module exposing list-like helpers.
- If nothing can be imported, it prints a helpful message.

Examples
--------
# Show both queued jobs and leases (pretty table)
python -m aicf.cli.queue_list --db sqlite:///aicf_dev.db

# Only jobs, return as JSON
python -m aicf.cli.queue_list jobs --db sqlite:///aicf_dev.db --json --limit 50

# Only leases
python -m aicf.cli.queue_list leases --db sqlite:///aicf_dev.db
"""

import json
import math
import shutil
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import typer

app = typer.Typer(
    name="queue-list",
    add_completion=False,
    no_args_is_help=True,
    help="List queued jobs and active leases from the AICF queue backend.",
)

# -------------------- utils --------------------


def _now_ms() -> int:
    return int(time.time() * 1000)


def _to_dict(x: Any) -> Dict[str, Any]:
    if x is None:
        return {}
    if isinstance(x, dict):
        return x
    if is_dataclass(x):
        try:
            return asdict(x)  # type: ignore[arg-type]
        except Exception:
            pass
    # try attribute dict
    d: Dict[str, Any] = {}
    for k in dir(x):
        if k.startswith("_"):
            continue
        try:
            v = getattr(x, k)
        except Exception:
            continue
        if callable(v):
            continue
        d[k] = v
    if not d and hasattr(x, "__dict__"):
        try:
            return dict(x.__dict__)  # type: ignore[attr-defined]
        except Exception:
            pass
    return d


def _width(default: int = 100) -> int:
    try:
        return shutil.get_terminal_size((default, 20)).columns
    except Exception:
        return default


def _pad(s: str, n: int) -> str:
    if len(s) <= n:
        return s + " " * (n - len(s))
    if n <= 1:
        return s[:n]
    if n <= 4:
        return s[:n]
    return s[: n - 1] + "…"


def _short(x: Optional[str], n: int = 10) -> str:
    if not x:
        return "-"
    if len(x) <= n:
        return x
    return x[: n - 1] + "…"


def _fmt_age(ms: int) -> str:
    s = max(0, ms) / 1000.0
    if s < 60:
        return f"{int(s)}s"
    m = s / 60.0
    if m < 60:
        return f"{int(m)}m"
    h = m / 60.0
    if h < 24:
        return f"{int(h)}h"
    d = h / 24.0
    return f"{int(d)}d"


def _fmt_num(n: Optional[float]) -> str:
    if n is None:
        return "-"
    if isinstance(n, int):
        return f"{n}"
    # clamp to 3 decimals sensibly
    return f"{n:.3f}".rstrip("0").rstrip(".")


def _call_first(
    obj: Any, names: Sequence[str], *args, **kwargs
) -> Tuple[Optional[str], Any]:
    last_exc: Optional[Exception] = None
    for nm in names:
        fn = getattr(obj, nm, None)
        if callable(fn):
            try:
                return nm, fn(*args, **kwargs)
            except Exception as e:
                last_exc = e
                continue
    return None, last_exc


def _import_queue_backend(db_uri: Optional[str]) -> Optional[Any]:
    """
    Try to obtain a queue backend object or module.
    If a storage class exists, attempt to open it with db_uri.
    """
    if not db_uri:
        return None
    # Primary: aicf.queue.storage
    try:
        from aicf.queue import storage as qmod  # type: ignore

        for cls_name in ("JobQueue", "Queue", "Storage", "QueueStorage"):
            cls = getattr(qmod, cls_name, None)
            if cls is None:
                continue
            # try common constructors
            for ctor in (
                lambda: cls(db_uri=db_uri),
                lambda: cls(db_uri),
                lambda: getattr(cls, "open")(db_uri),
            ):
                try:
                    return ctor()
                except Exception:
                    continue
        return qmod
    except ModuleNotFoundError:
        pass
    except Exception:
        # import failed for other reason, keep falling back
        pass

    # Fallback: any integration dispatcher/receiver exposing list helpers
    try:
        from aicf.queue import dispatcher as dmod  # type: ignore

        return dmod
    except ModuleNotFoundError:
        pass

    try:
        from aicf.queue import assignment as amod  # type: ignore

        return amod
    except ModuleNotFoundError:
        pass

    return None


def _compute_priority(record: Dict[str, Any]) -> float:
    """
    Compute/approximate a priority score. Prefer official function if available.
    Fallback: fee * (priority_multiplier) + age_weight
    """
    # Try official scorer if present
    try:
        from aicf.queue.priority import compute_priority  # type: ignore

        return float(compute_priority(record))
    except Exception:
        pass

    fee = float(record.get("fee") or 0.0)
    mult = float(record.get("priority") or 1.0)
    created_ms = int(record.get("created_ms") or record.get("ts_ms") or _now_ms())
    age_ms = max(0, _now_ms() - created_ms)
    age_weight = age_ms / 1000.0  # +1 per second as a simple tiebreaker
    return fee * mult + age_weight


def _normalize_job(obj: Any) -> Dict[str, Any]:
    d = _to_dict(obj)
    # unify key naming
    d["job_id"] = d.get("job_id") or d.get("id") or d.get("jid") or d.get("jobId")
    kind = d.get("kind") or d.get("job_kind") or d.get("type")
    if isinstance(kind, str):
        kind = kind.upper()
    d["kind"] = kind
    d["requester_id"] = (
        d.get("requester_id")
        or d.get("requestor_id")
        or d.get("owner")
        or d.get("account")
    )
    d["fee"] = d.get("fee") or d.get("tip") or d.get("reward")
    d["priority"] = d.get("priority") or d.get("prio") or 1.0
    d["created_ms"] = (
        d.get("created_ms")
        or d.get("ts_ms")
        or d.get("created_at_ms")
        or d.get("created")
        or 0
    )
    d["ttl_ms"] = d.get("ttl_ms") or d.get("ttl") or 0
    d["status"] = d.get("status") or d.get("state") or "PENDING"
    d["spec"] = d.get("spec") or {}
    d["requirements"] = d.get("requirements") or {}
    # compute fields
    d["_age_ms"] = max(0, _now_ms() - int(d["created_ms"] or 0))
    d["_expires_in_ms"] = (
        max(0, (int(d["created_ms"] or 0) + int(d["ttl_ms"] or 0)) - _now_ms())
        if d.get("ttl_ms")
        else 0
    )
    d["_score"] = _compute_priority(d)
    return d


def _normalize_lease(obj: Any) -> Dict[str, Any]:
    d = _to_dict(obj)
    d["lease_id"] = d.get("lease_id") or d.get("id") or d.get("lid")
    d["job_id"] = d.get("job_id") or d.get("jid") or d.get("jobId")
    d["provider_id"] = d.get("provider_id") or d.get("pid") or d.get("provider")
    d["renewals"] = d.get("renewals") or d.get("renews") or 0
    d["issued_ms"] = d.get("issued_ms") or d.get("start_ms") or d.get("ts_ms") or 0
    d["expires_ms"] = d.get("expires_ms") or d.get("deadline_ms") or 0
    d["_ttl_ms"] = max(0, int(d["expires_ms"] or 0) - _now_ms())
    return d


def _fetch_jobs(
    backend: Any, limit: int, kinds: Optional[List[str]]
) -> List[Dict[str, Any]]:
    # Try common list methods in order
    candidates = (
        ("list_pending", {"limit": limit}),
        ("list_queued", {"limit": limit}),
        ("list_jobs", {"limit": limit, "state": "pending"}),
        ("peek", {"n": limit}),
        ("top_n", {"n": limit}),
        ("top", {"n": limit}),
        ("inspect_queue", {"limit": limit}),
        ("dump", {"limit": limit}),
    )
    jobs: List[Any] = []
    for name, kwargs in candidates:
        meth = getattr(backend, name, None)
        if callable(meth):
            try:
                res = meth(**kwargs)  # type: ignore[misc]
                if isinstance(res, dict) and "items" in res:
                    jobs = list(res["items"])  # type: ignore[index]
                elif isinstance(res, (list, tuple)):
                    jobs = list(res)
                else:
                    # maybe an object with .items or .records
                    items = getattr(res, "items", None) or getattr(res, "records", None)
                    if items is not None:
                        jobs = list(items)
                    else:
                        continue
                break
            except Exception:
                continue

    # Normalize and filter
    norm = [_normalize_job(j) for j in jobs]
    if kinds:
        kinds_up = {k.upper() for k in kinds}
        norm = [j for j in norm if (str(j.get("kind") or "").upper() in kinds_up)]
    # Sort by computed score descending
    norm.sort(key=lambda r: float(r.get("_score", 0.0)), reverse=True)
    return norm[:limit]


def _fetch_leases(backend: Any, limit: int) -> List[Dict[str, Any]]:
    candidates = (
        ("list_leases", {"limit": limit}),
        ("active_leases", {"limit": limit}),
        ("get_active_leases", {"limit": limit}),
        ("leases", {}),
        ("inspect_leases", {"limit": limit}),
    )
    leases: List[Any] = []
    for name, kwargs in candidates:
        meth = getattr(backend, name, None)
        if callable(meth):
            try:
                res = meth(**kwargs)  # type: ignore[misc]
                if isinstance(res, (list, tuple)):
                    leases = list(res)
                elif isinstance(res, dict) and "items" in res:
                    leases = list(res["items"])  # type: ignore[index]
                else:
                    items = getattr(res, "items", None) or getattr(res, "records", None)
                    if items is not None:
                        leases = list(items)
                    else:
                        continue
                break
            except Exception:
                continue
    return [_normalize_lease(l) for l in leases][:limit]


def _print_jobs_table(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        typer.echo("No queued jobs.")
        return
    width = _width()
    # Column widths (sum should be <= width)
    cols = [
        ("PRI", 8, lambda r: _fmt_num(round(float(r.get("_score", 0.0)), 3))),
        ("KIND", 7, lambda r: _pad(str(r.get("kind") or "-"), 7)),
        ("JOB_ID", 14, lambda r: _short(str(r.get("job_id")), 14)),
        ("REQ", 10, lambda r: _short(str(r.get("requester_id")), 10)),
        ("FEE", 8, lambda r: _fmt_num(r.get("fee"))),
        ("MULT", 6, lambda r: _fmt_num(r.get("priority"))),
        ("AGE", 6, lambda r: _fmt_age(int(r.get("_age_ms", 0)))),
        ("TTL", 6, lambda r: _fmt_age(int(r.get("_expires_in_ms", 0)))),
        ("STATUS", 10, lambda r: _short(str(r.get("status")), 10)),
    ]
    # Adjust last column to fill
    used = sum(w for _, w, _ in cols)
    if used + 2 < width:
        extra = width - used - 2
        # widen REQ a bit
        cols[3] = ("REQ", cols[3][1] + extra, cols[3][2])

    header = " ".join(_pad(n, w) for n, w, _ in cols)
    typer.secho(header, bold=True)
    for r in rows:
        line = " ".join(_pad(fn(r), w) for _, w, fn in cols)
        typer.echo(line)


def _print_leases_table(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        typer.echo("No active leases.")
        return
    width = _width()
    cols = [
        ("LEASE", 12, lambda r: _short(str(r.get("lease_id")), 12)),
        ("JOB_ID", 14, lambda r: _short(str(r.get("job_id")), 14)),
        ("PROVIDER", 12, lambda r: _short(str(r.get("provider_id")), 12)),
        ("TTL", 6, lambda r: _fmt_age(int(r.get("_ttl_ms", 0)))),
        ("RENEW", 5, lambda r: str(int(r.get("renewals", 0)))),
    ]
    used = sum(w for _, w, _ in cols)
    if used + 2 < width:
        extra = width - used - 2
        cols[2] = ("PROVIDER", cols[2][1] + extra, cols[2][2])

    header = " ".join(_pad(n, w) for n, w, _ in cols)
    typer.secho(header, bold=True)
    for r in rows:
        line = " ".join(_pad(fn(r), w) for _, w, fn in cols)
        typer.echo(line)


def _ensure_backend(db: Optional[str]) -> Any:
    backend = _import_queue_backend(db)
    if backend is None:
        typer.secho(
            "Queue backend not found. Provide --db and ensure aicf.queue.* modules are available.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(2)
    return backend


# -------------------- commands --------------------


@app.command("jobs")
def list_jobs(
    db: Optional[str] = typer.Option(
        None, "--db", help="Queue DB URI (e.g., sqlite:///aicf_dev.db)."
    ),
    limit: int = typer.Option(
        100, min=1, max=10000, help="Max number of jobs to show."
    ),
    kind: List[str] = typer.Option(
        None, "--kind", help="Filter by kind, e.g. AI or QUANTUM (repeatable)."
    ),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    backend = _ensure_backend(db)
    rows = _fetch_jobs(backend, limit=limit, kinds=kind or None)
    if json_out:
        typer.echo(json.dumps(rows, indent=2, sort_keys=True))
    else:
        _print_jobs_table(rows)


@app.command("leases")
def list_leases(
    db: Optional[str] = typer.Option(
        None, "--db", help="Queue DB URI (e.g., sqlite:///aicf_dev.db)."
    ),
    limit: int = typer.Option(
        100, min=1, max=10000, help="Max number of leases to show."
    ),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    backend = _ensure_backend(db)
    rows = _fetch_leases(backend, limit=limit)
    if json_out:
        typer.echo(json.dumps(rows, indent=2, sort_keys=True))
    else:
        _print_leases_table(rows)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    db: Optional[str] = typer.Option(
        None, "--db", help="Queue DB URI (e.g., sqlite:///aicf_dev.db)."
    ),
    limit: int = typer.Option(50, min=1, max=10000, help="Max rows per section."),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """
    If no subcommand is provided, show both jobs and leases.
    """
    if ctx.invoked_subcommand is not None:
        return
    backend = _ensure_backend(db)
    jobs = _fetch_jobs(backend, limit=limit, kinds=None)
    leases = _fetch_leases(backend, limit=limit)

    if json_out:
        typer.echo(
            json.dumps({"jobs": jobs, "leases": leases}, indent=2, sort_keys=True)
        )
        return

    typer.secho("Queued jobs (by priority):", bold=True)
    _print_jobs_table(jobs)
    typer.echo("")
    typer.secho("Active leases:", bold=True)
    _print_leases_table(leases)


def get_app() -> typer.Typer:
    return app


if __name__ == "__main__":
    app()
