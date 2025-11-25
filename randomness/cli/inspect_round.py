"""
randomness.cli.inspect_round
----------------------------

Inspect a randomness round: window timings, counts, and pending proofs.

Examples:
  # Inspect the current round (pretty text):
  omni rand inspect-round

  # Inspect a specific round:
  omni rand inspect-round --round 1234

  # Output normalized JSON instead of text:
  omni rand inspect-round --json

  # Show detailed pending proofs:
  omni rand inspect-round --verbose

Environment:
  OMNI_RPC_URL / ANIMICA_RPC_URL : JSON-RPC endpoint (default: http://127.0.0.1:8545)

RPCs this tool expects (best effort / tolerant to field shape):
  - rand.getRound([roundId?]) -> {
        round, status, schedule|window: {
          commitOpen|commit_open|commit_start,
          commitClose|commit_close|commit_end,
          revealOpen|reveal_open|reveal_start,
          revealClose|reveal_close|reveal_end,
          vdfDeadline|vdf_deadline
        },
        counts|stats: { commits, reveals, proofs },
        pendingProofs|vdf.pending: [ { provider?, submittedTs?, status? } ],
        beacon? (present after sealed)
    }
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple
from datetime import datetime, timezone

# Optional deps guard
try:
    import typer  # type: ignore
    import requests  # type: ignore
except Exception as e:  # pragma: no cover
    raise SystemExit(
        "This command requires optional dependencies.\n"
        "Install with: pip install typer[all] requests\n"
        f"Import error: {e}"
    )

_DEFAULT_RPC = os.getenv("OMNI_RPC_URL") or os.getenv("ANIMICA_RPC_URL") or "http://127.0.0.1:8545"

app = typer.Typer(
    name="omni-rand-inspect-round",
    help="Show window timings, counts, and pending proofs for a randomness round.",
    no_args_is_help=False,
    add_completion=False,
)

# -----------------------
# RPC helpers
# -----------------------

def _rpc_call(url: str, method: str, params: Optional[Sequence[Any]] = None, timeout: float = 30.0) -> Dict[str, Any]:
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params or [])}
    try:
        r = requests.post(url, json=body, timeout=timeout)
    except Exception as e:
        raise SystemExit(f"RPC POST failed: {e}")
    if r.status_code != 200:
        raise SystemExit(f"RPC error HTTP {r.status_code}: {r.text}")
    try:
        data = r.json()
    except Exception:
        raise SystemExit(f"RPC response not JSON: {r.text}")
    if "error" in data and data["error"]:
        raise SystemExit(f"RPC error: {json.dumps(data['error'], indent=2)}")
    result = data.get("result")
    if result is None:
        raise SystemExit("RPC returned no result.")
    if not isinstance(result, dict):
        # Normalize very minimal implementations
        return {"round": result}
    return result

# -----------------------
# Normalization helpers
# -----------------------

def _pick(d: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None

def _norm_ts(v: Any) -> Optional[int]:
    """
    Accept seconds or milliseconds epoch. Return seconds (int) or None.
    """
    if v is None:
        return None
    try:
        iv = int(v)
    except Exception:
        return None
    # Heuristic: ms if very large
    if iv > 10**12:
        iv //= 1000
    return iv

def _normalize_round(result: Dict[str, Any]) -> Dict[str, Any]:
    # Round id/status
    round_id = _pick(result, "round", "id", "roundId")
    status = _pick(result, "status", "phase", "state")

    # Schedule / window fields
    sched_src = _pick(result, "schedule", "window") or {}
    commit_open  = _norm_ts(_pick(sched_src, "commitOpen", "commit_open", "commitStart", "commit_start"))
    commit_close = _norm_ts(_pick(sched_src, "commitClose", "commit_close", "commitEnd", "commit_end"))
    reveal_open  = _norm_ts(_pick(sched_src, "revealOpen", "reveal_open", "revealStart", "reveal_start"))
    reveal_close = _norm_ts(_pick(sched_src, "revealClose", "reveal_close", "revealEnd", "reveal_end"))
    vdf_deadline = _norm_ts(_pick(sched_src, "vdfDeadline", "vdf_deadline", "finalize", "finalize_deadline"))

    # Counts
    counts_src = _pick(result, "counts", "stats") or {}
    commits = int(_pick(counts_src, "commits") or _pick(result, "commitCount") or 0)
    reveals = int(_pick(counts_src, "reveals") or _pick(result, "revealCount") or 0)
    proofs  = int(_pick(counts_src, "proofs")  or _pick(result, "vdfProofCount") or 0)

    # Pending proofs
    pending = _pick(result, "pendingProofs", "pending_proofs")
    if pending is None:
        vdf_obj = _pick(result, "vdf") or {}
        pending = _pick(vdf_obj, "pending")
    if not isinstance(pending, list):
        pending = []

    beacon_present = bool(_pick(result, "beacon"))

    schedule = {
        "commitOpen": commit_open,
        "commitClose": commit_close,
        "revealOpen": reveal_open,
        "revealClose": reveal_close,
        "vdfDeadline": vdf_deadline,
    }
    counts = {"commits": commits, "reveals": reveals, "proofs": proofs}

    return {
        "round": round_id,
        "status": status,
        "schedule": schedule,
        "counts": counts,
        "pendingProofs": pending,
        "beaconPresent": beacon_present,
    }

# -----------------------
# Time/format helpers
# -----------------------

def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def _fmt_dt(ts: Optional[int], tz_local: bool = True) -> str:
    if ts is None:
        return "-"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    if tz_local:
        dt = dt.astimezone()  # local
    return dt.isoformat(timespec="seconds")

def _fmt_delta(secs: int) -> str:
    sign = "in " if secs >= 0 else ""
    s = abs(secs)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h:
        body = f"{h}h{m:02d}m{sec:02d}s"
    elif m:
        body = f"{m}m{sec:02d}s"
    else:
        body = f"{sec}s"
    return f"{sign}{body}" if sign else f"{body} ago"

def _phase(now: int, schedule: Dict[str, Optional[int]], beacon_present: bool) -> Tuple[str, str, Optional[str]]:
    co = schedule.get("commitOpen")
    cc = schedule.get("commitClose")
    ro = schedule.get("revealOpen")
    rc = schedule.get("revealClose")
    vd = schedule.get("vdfDeadline")
    if co and now < co:
        return ("PRECOMMIT", f"opens {_fmt_delta(co - now)}", "commitOpen")
    if cc and now < cc:
        return ("COMMITTING", f"closes {_fmt_delta(cc - now)}", "commitClose")
    if ro and now < ro:
        return ("GAP", f"reveal opens {_fmt_delta(ro - now)}", "revealOpen")
    if rc and now < rc:
        return ("REVEALING", f"closes {_fmt_delta(rc - now)}", "revealClose")
    if vd and now < vd:
        return ("FINALIZING", f"VDF deadline {_fmt_delta(vd - now)}", "vdfDeadline")
    return ("SEALED" if beacon_present else "WAITING_SEAL", "awaiting beacon or next round", None)

def _print_text(norm: Dict[str, Any], tz_local: bool, verbose: bool) -> None:
    now = _now_ts()
    round_id = norm.get("round", "-")
    status = norm.get("status") or "-"
    schedule = norm.get("schedule") or {}
    counts = norm.get("counts") or {}
    pending: List[Dict[str, Any]] = norm.get("pendingProofs") or []
    beacon_present = bool(norm.get("beaconPresent"))

    phase, next_hint, _ = _phase(now, schedule, beacon_present)

    typer.echo(f"Round: {round_id}")
    typer.echo(f"Status: {status} | Phase: {phase} ({next_hint})")
    typer.echo("")
    typer.echo("Windows:")
    co = schedule.get("commitOpen");  cc = schedule.get("commitClose")
    ro = schedule.get("revealOpen");  rc = schedule.get("revealClose")
    vd = schedule.get("vdfDeadline")
    for label, ts in [
        ("Commit Open ", co),
        ("Commit Close", cc),
        ("Reveal Open ", ro),
        ("Reveal Close", rc),
        ("VDF Deadline", vd),
    ]:
        when = _fmt_dt(ts, tz_local)
        rel = _fmt_delta((ts or now) - now) if ts else "-"
        typer.echo(f"  {label}: {when:>25}  ({rel})")

    typer.echo("")
    typer.echo("Counts:")
    typer.echo(f"  Commits: {counts.get('commits', 0)}")
    typer.echo(f"  Reveals: {counts.get('reveals', 0)}")
    typer.echo(f"  VDF proofs: {counts.get('proofs', 0)}")
    typer.echo(f"  Pending proofs: {len(pending)}")

    if verbose and pending:
        typer.echo("")
        typer.echo("Pending proofs:")
        for i, p in enumerate(pending, 1):
            who = p.get("provider") or p.get("prover") or p.get("id") or "unknown"
            ts  = _norm_ts(_pick(p, "submittedTs", "ts", "time"))
            when = _fmt_dt(ts, tz_local) if ts else "-"
            st = p.get("status") or "-"
            typer.echo(f"  {i:>2}. provider={who}  submitted={when}  status={st}")

# -----------------------
# CLI
# -----------------------

@app.command("inspect-round")
def cmd_inspect_round(
    rpc: str = typer.Option(_DEFAULT_RPC, "--rpc", help=f"JSON-RPC endpoint (default: {_DEFAULT_RPC})"),
    round_id: Optional[int] = typer.Option(None, "--round", "-r", help="Round id to fetch (default: current)"),
    json_out: bool = typer.Option(False, "--json", help="Print normalized JSON instead of text."),
    pretty: bool = typer.Option(True, "--pretty/--no-pretty", help="Pretty-print JSON."),
    tz: str = typer.Option("local", "--tz", help="Time zone for display: local|utc", case_sensitive=False),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed pending proofs list."),
    out: Optional[str] = typer.Option(None, "--out", help="Write (normalized JSON) to file."),
) -> None:
    """
    Show window timings (commit/reveal/VDF), aggregate counts, and pending proofs for a round.
    """
    params: Sequence[Any] = [round_id] if round_id is not None else []
    result = _rpc_call(rpc, "rand.getRound", params)
    norm = _normalize_round(result)

    if json_out or out:
        text = json.dumps(norm, indent=2 if pretty else None)
        if out:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
        if json_out and not out:
            typer.echo(text)
        if json_out:
            raise typer.Exit(0)

    # Text view
    tz_local = (tz.lower() != "utc")
    _print_text(norm, tz_local=tz_local, verbose=verbose)
    raise typer.Exit(0)


def main() -> None:  # pragma: no cover
    try:
        app(standalone_mode=False, prog_name="omni rand inspect-round")
    except SystemExit as e:
        raise e
    except KeyboardInterrupt:
        typer.echo("", err=True)
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
