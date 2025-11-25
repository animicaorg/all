from __future__ import annotations

"""
aicf.cli.payouts_inspect
------------------------

Inspect AICF payouts:
- Pending payouts that are not yet settled.
- Settled payouts (optionally grouped by batch/epoch/provider).

This CLI is backend-adaptive. It looks for common list/query APIs across:
- aicf.economics.payouts (preferred)
- aicf.economics.settlement
- aicf.treasury.rewards (fallback for settled credits)

If a storage class is available, it tries to open it with --db.
If only modules are present, it tries well-known list_* functions.

Examples
--------
# Show both pending and settled (top 50 each)
python -m aicf.cli.payouts_inspect --db sqlite:///aicf_dev.db

# Only pending, filter by provider, JSON
python -m aicf.cli.payouts_inspect pending --provider P123 --json

# Settled payouts in epoch 42, with totals per provider
python -m aicf.cli.payouts_inspect settled --epoch 42 --sum-by provider

# Settled payouts with min amount filter and limit
python -m aicf.cli.payouts_inspect settled --min-amount 10 --limit 200
"""

import json
import shutil
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import typer

app = typer.Typer(
    name="payouts-inspect",
    add_completion=False,
    no_args_is_help=True,
    help="Inspect pending and settled payouts in the AICF economics/treasury subsystems.",
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
    # best-effort attribute scrape
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


def _fmt_amt(a: Optional[float]) -> str:
    if a is None:
        return "-"
    try:
        f = float(a)
    except Exception:
        return str(a)
    # Max 6 decimals, strip trailing zeros
    s = f"{f:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _fmt_ms(ms: Optional[int]) -> str:
    if not ms:
        return "-"
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


def _ensure_list(x: Any) -> List[Any]:
    if x is None:
        return []
    if isinstance(x, (list, tuple, set)):
        return list(x)
    return [x]


def _import_backend(db_uri: Optional[str]) -> Any:
    """
    Try multiple modules/classes to obtain a payouts/settlement reader.
    Preference order:
      1) aicf.economics.payouts (PayoutStore-like)
      2) aicf.economics.settlement
      3) aicf.treasury.rewards (settled credits)
    """
    # 1) payouts
    try:
        from aicf.economics import payouts as pout_mod  # type: ignore
        for cls_name in ("PayoutStore", "Payouts", "PayoutStorage", "Store"):
            cls = getattr(pout_mod, cls_name, None)
            if cls is None:
                continue
            for ctor in (
                lambda: cls(db_uri=db_uri),
                lambda: cls(db_uri),
                lambda: getattr(cls, "open")(db_uri),
            ):
                try:
                    return ctor()
                except Exception:
                    continue
        # If class open fails, return module (might have list_* functions)
        return pout_mod
    except ModuleNotFoundError:
        pass
    except Exception:
        pass

    # 2) settlement
    try:
        from aicf.economics import settlement as set_mod  # type: ignore
        for cls_name in ("SettlementStore", "Settlement", "Store"):
            cls = getattr(set_mod, cls_name, None)
            if cls is None:
                continue
            for ctor in (
                lambda: cls(db_uri=db_uri),
                lambda: cls(db_uri),
                lambda: getattr(cls, "open")(db_uri),
            ):
                try:
                    return ctor()
                except Exception:
                    continue
        return set_mod
    except ModuleNotFoundError:
        pass
    except Exception:
        pass

    # 3) treasury rewards (settled-only fallback)
    try:
        from aicf.treasury import rewards as rew_mod  # type: ignore
        return rew_mod
    except ModuleNotFoundError:
        pass
    except Exception:
        pass

    return None


# -------------------- normalization --------------------

def _normalize_payout(d: Dict[str, Any]) -> Dict[str, Any]:
    n: Dict[str, Any] = {}
    n["payout_id"] = d.get("payout_id") or d.get("id") or d.get("pid")
    n["job_id"] = d.get("job_id") or d.get("jid") or d.get("task_id")
    n["provider_id"] = d.get("provider_id") or d.get("pid") or d.get("provider")
    n["miner_id"] = d.get("miner_id") or d.get("miner") or None
    n["kind"] = (d.get("kind") or d.get("work_kind") or d.get("type") or "AI").upper()
    n["epoch"] = d.get("epoch")
    n["height"] = d.get("height") or d.get("block_height") or None
    n["amount_total"] = d.get("amount_total") or d.get("amount") or d.get("reward") or 0
    # splits
    n["amount_provider"] = d.get("amount_provider") or d.get("provider_share") or None
    n["amount_treasury"] = d.get("amount_treasury") or d.get("treasury_share") or None
    n["amount_miner"] = d.get("amount_miner") or d.get("miner_share") or None
    # status/settlement
    status = d.get("status") or d.get("state")
    if isinstance(status, str):
        status = status.upper()
    n["status"] = status or ("SETTLED" if d.get("settled") or d.get("settled_height") else "PENDING")
    n["batch_id"] = d.get("batch_id") or d.get("settlement_id") or d.get("sid") or None
    n["created_ms"] = d.get("created_ms") or d.get("ts_ms") or d.get("created_at_ms") or 0
    n["settled_ms"] = d.get("settled_ms") or d.get("confirmed_ms") or 0
    n["settled_height"] = d.get("settled_height") or None
    return n


def _normalize_batch(d: Dict[str, Any]) -> Dict[str, Any]:
    n: Dict[str, Any] = {}
    n["batch_id"] = d.get("batch_id") or d.get("id") or d.get("sid")
    n["epoch"] = d.get("epoch")
    n["height"] = d.get("height") or d.get("block_height")
    n["total_amount"] = d.get("total_amount") or d.get("amount") or 0
    n["payouts"] = _ensure_list(d.get("payouts"))
    n["created_ms"] = d.get("created_ms") or d.get("ts_ms") or 0
    return n


# -------------------- fetchers --------------------

def _fetch_pending(backend: Any, limit: int, provider: Optional[str], epoch: Optional[int], min_amount: Optional[float]) -> List[Dict[str, Any]]:
    """
    Try a sequence of methods to retrieve pending payouts.
    """
    methods: Tuple[Tuple[str, Dict[str, Any]], ...] = (
        ("list_pending", {"limit": limit, "provider_id": provider, "epoch": epoch}),
        ("list_payouts", {"limit": limit, "status": "pending", "provider_id": provider, "epoch": epoch}),
        ("pending_payouts", {"limit": limit, "provider_id": provider, "epoch": epoch}),
        ("get_pending", {"limit": limit}),
        ("query", {"status": "pending", "limit": limit}),
    )
    items: List[Any] = []
    for name, kwargs in methods:
        fn = getattr(backend, name, None)
        if callable(fn):
            try:
                res = fn(**{k: v for k, v in kwargs.items() if v is not None})  # type: ignore[misc]
                if isinstance(res, dict) and "items" in res:
                    items = list(res["items"])  # type: ignore[index]
                elif isinstance(res, (list, tuple)):
                    items = list(res)
                else:
                    items_attr = getattr(res, "items", None) or getattr(res, "records", None)
                    if items_attr is not None:
                        items = list(items_attr)
                    else:
                        continue
                break
            except Exception:
                continue
    rows = [_normalize_payout(_to_dict(x)) for x in items]
    # ensure status tagged as PENDING
    for r in rows:
        if not r.get("status"):
            r["status"] = "PENDING"
    # filters
    if min_amount is not None:
        rows = [r for r in rows if float(r.get("amount_total") or 0) >= float(min_amount)]
    if provider:
        rows = [r for r in rows if str(r.get("provider_id") or "") == provider]
    if epoch is not None:
        rows = [r for r in rows if (r.get("epoch") == epoch)]
    # sort newest first by created_ms
    rows.sort(key=lambda r: int(r.get("created_ms") or 0), reverse=True)
    return rows[:limit]


def _fetch_settled(backend: Any, limit: int, provider: Optional[str], epoch: Optional[int], min_amount: Optional[float]) -> List[Dict[str, Any]]:
    """
    Try methods to retrieve settled payouts. If only batches are available, flatten.
    """
    # First, direct settled payouts
    methods_direct: Tuple[Tuple[str, Dict[str, Any]], ...] = (
        ("list_settled", {"limit": limit, "provider_id": provider, "epoch": epoch}),
        ("list_payouts", {"limit": limit, "status": "settled", "provider_id": provider, "epoch": epoch}),
        ("settled_payouts", {"limit": limit}),
        ("get_settled", {"limit": limit}),
        ("query", {"status": "settled", "limit": limit}),
    )
    items: List[Any] = []
    for name, kwargs in methods_direct:
        fn = getattr(backend, name, None)
        if callable(fn):
            try:
                res = fn(**{k: v for k, v in kwargs.items() if v is not None})  # type: ignore[misc]
                if isinstance(res, dict) and "items" in res:
                    items = list(res["items"])  # type: ignore[index]
                elif isinstance(res, (list, tuple)):
                    items = list(res)
                else:
                    items_attr = getattr(res, "items", None) or getattr(res, "records", None)
                    if items_attr is not None:
                        items = list(items_attr)
                    else:
                        continue
                break
            except Exception:
                continue

    if not items:
        # Try batches and flatten
        batch_methods: Tuple[Tuple[str, Dict[str, Any]], ...] = (
            ("list_batches", {"limit": limit, "epoch": epoch}),
            ("recent_batches", {"limit": limit}),
            ("list_settlements", {"limit": limit, "epoch": epoch}),
        )
        batches: List[Any] = []
        for name, kwargs in batch_methods:
            fn = getattr(backend, name, None)
            if callable(fn):
                try:
                    res = fn(**{k: v for k, v in kwargs.items() if v is not None})  # type: ignore[misc]
                    if isinstance(res, dict) and "items" in res:
                        batches = list(res["items"])  # type: ignore[index]
                    elif isinstance(res, (list, tuple)):
                        batches = list(res)
                    else:
                        items_attr = getattr(res, "items", None) or getattr(res, "records", None)
                        if items_attr is not None:
                            batches = list(items_attr)
                        else:
                            continue
                    break
                except Exception:
                    continue
        # Flatten batches → payouts
        for b in batches:
            bd = _normalize_batch(_to_dict(b))
            for p in bd["payouts"]:
                pd = _normalize_payout(_to_dict(p))
                if not pd.get("batch_id"):
                    pd["batch_id"] = bd["batch_id"]
                if not pd.get("epoch"):
                    pd["epoch"] = bd["epoch"]
                items.append(pd)
    rows = [_normalize_payout(_to_dict(x)) for x in items]
    # Set status
    for r in rows:
        if not r.get("status"):
            r["status"] = "SETTLED"
    # filters
    if min_amount is not None:
        rows = [r for r in rows if float(r.get("amount_total") or 0) >= float(min_amount)]
    if provider:
        rows = [r for r in rows if str(r.get("provider_id") or "") == provider]
    if epoch is not None:
        rows = [r for r in rows if (r.get("epoch") == epoch)]
    # sort by settled time then height
    rows.sort(
        key=lambda r: (
            int(r.get("settled_ms") or 0),
            int(r.get("settled_height") or r.get("height") or 0),
        ),
        reverse=True,
    )
    return rows[:limit]


# -------------------- printing --------------------

def _print_pending_table(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        typer.echo("No pending payouts.")
        return
    width = _width()
    cols = [
        ("AMOUNT", 12, lambda r: _pad(_fmt_amt(r.get("amount_total")), 12)),
        ("KIND", 7, lambda r: _pad(str(r.get("kind") or "-"), 7)),
        ("PAYOUT", 12, lambda r: _pad(_short(str(r.get("payout_id")), 12), 12)),
        ("PROVIDER", 12, lambda r: _pad(_short(str(r.get("provider_id")), 12), 12)),
        ("JOB", 12, lambda r: _pad(_short(str(r.get("job_id")), 12), 12)),
        ("EPOCH", 7, lambda r: _pad(str(r.get("epoch") or "-"), 7)),
        ("HEIGHT", 8, lambda r: _pad(str(r.get("height") or "-"), 8)),
        ("AGE", 6, lambda r: _pad(_fmt_ms(_now_ms() - int(r.get("created_ms") or 0)), 6)),
        ("STATUS", 10, lambda r: _pad(str(r.get("status") or "PENDING"), 10)),
    ]
    used = sum(w for _, w, _ in cols)
    if used + 2 < width:
        extra = width - used - 2
        cols[3] = ("PROVIDER", cols[3][1] + extra, cols[3][2])  # widen provider
    header = " ".join(_pad(n, w) for n, w, _ in cols)
    typer.secho(header, bold=True)
    for r in rows:
        line = " ".join(_pad(fn(r), w) for _, w, fn in cols)
        typer.echo(line)


def _print_settled_table(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        typer.echo("No settled payouts.")
        return
    width = _width()
    cols = [
        ("AMOUNT", 12, lambda r: _pad(_fmt_amt(r.get("amount_total")), 12)),
        ("KIND", 7, lambda r: _pad(str(r.get("kind") or "-"), 7)),
        ("BATCH", 12, lambda r: _pad(_short(str(r.get("batch_id")), 12), 12)),
        ("PAYOUT", 12, lambda r: _pad(_short(str(r.get("payout_id")), 12), 12)),
        ("PROVIDER", 12, lambda r: _pad(_short(str(r.get("provider_id")), 12), 12)),
        ("EPOCH", 7, lambda r: _pad(str(r.get("epoch") or "-"), 7)),
        ("HGT", 6, lambda r: _pad(str(r.get("settled_height") or r.get("height") or "-"), 6)),
        ("WHEN", 6, lambda r: _pad(_fmt_ms(_now_ms() - int(r.get("settled_ms") or 0)), 6)),
    ]
    used = sum(w for _, w, _ in cols)
    if used + 2 < width:
        extra = width - used - 2
        cols[4] = ("PROVIDER", cols[4][1] + extra, cols[4][2])
    header = " ".join(_pad(n, w) for n, w, _ in cols)
    typer.secho(header, bold=True)
    for r in rows:
        line = " ".join(_pad(fn(r), w) for _, w, fn in cols)
        typer.echo(line)


def _print_totals(rows: List[Dict[str, Any]], by: Optional[str]) -> None:
    if not rows:
        return
    if not by:
        total = sum(float(r.get("amount_total") or 0) for r in rows)
        typer.secho(f"Total: { _fmt_amt(total) }", bold=True)
        return
    buckets: Dict[str, float] = {}
    key = by
    for r in rows:
        k = str(r.get(key) or "-")
        buckets[k] = buckets.get(k, 0.0) + float(r.get("amount_total") or 0)
    width = _width()
    name_w = max(10, min(40, width - 18))
    header = _pad(by.upper(), name_w) + " " + _pad("TOTAL", 12)
    typer.secho(header, bold=True)
    for k, v in sorted(buckets.items(), key=lambda kv: kv[1], reverse=True):
        typer.echo(_pad(k, name_w) + " " + _pad(_fmt_amt(v), 12))


# -------------------- commands --------------------

def _ensure_backend_or_exit(db: Optional[str]) -> Any:
    backend = _import_backend(db)
    if backend is None:
        typer.secho(
            "Payouts backend not found. Provide --db and ensure aicf.economics.* modules are available.",
            fg=typer.colors.RED,
        )
        raise typer.Exit(2)
    return backend


@app.command("pending")
def cmd_pending(
    db: Optional[str] = typer.Option(None, "--db", help="Economics/Treasury DB URI (e.g., sqlite:///aicf_dev.db)."),
    limit: int = typer.Option(100, min=1, max=10000, help="Max number of payouts to show."),
    provider: Optional[str] = typer.Option(None, "--provider", help="Filter by provider id."),
    epoch: Optional[int] = typer.Option(None, "--epoch", help="Filter by epoch."),
    min_amount: Optional[float] = typer.Option(None, "--min-amount", help="Filter payouts >= this amount."),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
    sum_by: Optional[str] = typer.Option(None, "--sum-by", help="Aggregate totals by field (provider|epoch|kind)."),
) -> None:
    backend = _ensure_backend_or_exit(db)
    rows = _fetch_pending(backend, limit=limit, provider=provider, epoch=epoch, min_amount=min_amount)
    if json_out:
        typer.echo(json.dumps(rows, indent=2, sort_keys=True))
        return
    _print_pending_table(rows)
    if sum_by:
        _print_totals(rows, by=sum_by)


@app.command("settled")
def cmd_settled(
    db: Optional[str] = typer.Option(None, "--db", help="Economics/Treasury DB URI (e.g., sqlite:///aicf_dev.db)."),
    limit: int = typer.Option(100, min=1, max=10000, help="Max number of payouts to show."),
    provider: Optional[str] = typer.Option(None, "--provider", help="Filter by provider id."),
    epoch: Optional[int] = typer.Option(None, "--epoch", help="Filter by epoch."),
    min_amount: Optional[float] = typer.Option(None, "--min-amount", help="Filter payouts >= this amount."),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
    sum_by: Optional[str] = typer.Option(None, "--sum-by", help="Aggregate totals by field (provider|epoch|kind|batch_id)."),
) -> None:
    backend = _ensure_backend_or_exit(db)
    rows = _fetch_settled(backend, limit=limit, provider=provider, epoch=epoch, min_amount=min_amount)
    if json_out:
        typer.echo(json.dumps(rows, indent=2, sort_keys=True))
        return
    _print_settled_table(rows)
    if sum_by:
        _print_totals(rows, by=sum_by)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    db: Optional[str] = typer.Option(None, "--db", help="Economics/Treasury DB URI (e.g., sqlite:///aicf_dev.db)."),
    limit: int = typer.Option(50, min=1, max=10000, help="Max rows for each section when no subcommand is given."),
    json_out: bool = typer.Option(False, "--json", help="Output as JSON."),
) -> None:
    """
    If no subcommand is provided, show both pending and settled sections.
    """
    if ctx.invoked_subcommand is not None:
        return
    backend = _ensure_backend_or_exit(db)
    pending = _fetch_pending(backend, limit=limit, provider=None, epoch=None, min_amount=None)
    settled = _fetch_settled(backend, limit=limit, provider=None, epoch=None, min_amount=None)
    if json_out:
        typer.echo(json.dumps({"pending": pending, "settled": settled}, indent=2, sort_keys=True))
        return
    typer.secho("Pending payouts:", bold=True)
    _print_pending_table(pending)
    typer.echo("")
    typer.secho("Settled payouts:", bold=True)
    _print_settled_table(settled)


def get_app() -> typer.Typer:
    return app


if __name__ == "__main__":
    app()
