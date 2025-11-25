from __future__ import annotations

"""
aicf.cli.slash
--------------

Devnet utility to simulate provider faults and trigger a slashing action.

This tool is *backend-adaptive*:
- Prefers `aicf.sla.slash_engine` (SlashEngine/Engine/Slasher) if present.
- Falls back to `aicf.registry.penalties` + `aicf.registry.staking` primitives.
- Reads reason codes from `aicf.registry.penalties` or `aicf.aitypes.events` if available.
- If an economics slashing rules module exists, defaults are pulled from it.

Examples
--------
# Slash provider by explicit amount (units), reason traps_miss
python -m aicf.cli.slash apply --db sqlite:///aicf_dev.db --provider P123 \
  --reason traps_miss --amount 25

# Slash by 2.5% of stake and jail for 1 day with a note, JSON evidence from file
python -m aicf.cli.slash apply --provider P123 --percent 2.5 --jail \
  --cooldown 86400 --reason qos_breach --evidence @evidence.json --note "QoS SLO missed"

# Quick helpers for common simulated faults
python -m aicf.cli.slash traps-miss --provider P123 --percent 1.0
python -m aicf.cli.slash no-attestation --provider P123 --amount 50 --jail

# Explore available reason codes and defaults
python -m aicf.cli.slash list-reasons
"""

import json
import os
import sys
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional, Tuple

import typer

app = typer.Typer(
    name="slash",
    add_completion=False,
    no_args_is_help=True,
    help="Simulate faults and apply slashing to providers (devnet/test tooling).",
)

# -------------------- utils --------------------

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


def _read_evidence_arg(evidence: Optional[str]) -> Optional[Dict[str, Any]]:
    if not evidence:
        return None
    s = evidence.strip()
    if s.startswith("@"):
        path = s[1:]
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    try:
        return json.loads(s)
    except Exception:
        # Store raw string if not JSON
        return {"raw": s}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _percent_to_fraction(percent: Optional[float]) -> Optional[float]:
    if percent is None:
        return None
    return float(percent) / 100.0


def _fmt_amount(x: Optional[float]) -> str:
    if x is None:
        return "-"
    s = f"{float(x):.6f}".rstrip("0").rstrip(".")
    return s if s else "0"


# -------------------- dynamic imports --------------------

def _import_module(name: str) -> Optional[Any]:
    try:
        mod = __import__(name, fromlist=["*"])
        return mod
    except ModuleNotFoundError:
        return None
    except Exception:
        return None


def _open_engine(db_uri: Optional[str]) -> Optional[Any]:
    """
    Try to obtain a slashing engine instance or module with callable methods.
    Order:
      aicf.sla.slash_engine (SlashEngine/Engine/Slasher)
      aicf.registry.penalties (module-only fallback)
    """
    mod = _import_module("aicf.sla.slash_engine")
    if mod:
        for cls_name in ("SlashEngine", "Engine", "Slasher"):
            cls = getattr(mod, cls_name, None)
            if cls is None:
                continue
            # try common constructors
            for ctor in (
                lambda: cls(db_uri=db_uri),
                lambda: cls(db_uri),
                lambda: getattr(cls, "open")(db_uri),
                lambda: cls(),  # last resort
            ):
                try:
                    return ctor()
                except Exception:
                    continue
        # fall back to module-level functions
        return mod
    return _import_module("aicf.registry.penalties")


def _staking_api() -> Optional[Any]:
    return _import_module("aicf.registry.staking")


def _registry_api() -> Optional[Any]:
    return _import_module("aicf.registry.registry")


def _slashing_rules_api() -> Optional[Any]:
    return _import_module("aicf.economics.slashing_rules")


def _events_types_api() -> Optional[Any]:
    return _import_module("aicf.aitypes.events")


# -------------------- reason codes & defaults --------------------

def _known_reason_map() -> Dict[str, Any]:
    """
    Collect known reason codes from penalties module or events types.
    Return mapping: canonical_name -> enum_value_or_string
    """
    mapping: Dict[str, Any] = {}
    pen = _import_module("aicf.registry.penalties")
    if pen:
        # look for Reason enum or constants
        reason = getattr(pen, "Reason", None)
        if reason:
            # Enum-like
            for k in dir(reason):
                if k.startswith("_"):
                    continue
                v = getattr(reason, k)
                name = str(k).lower()
                mapping[name] = v
        else:
            # constants style: REASON_TRAPS_MISS, etc.
            for k in dir(pen):
                if k.startswith("REASON_"):
                    name = k[len("REASON_") :].lower()
                    mapping[name] = getattr(pen, k)
    # also try types.events if present
    ev = _events_types_api()
    if ev:
        se = getattr(ev, "SlashEvent", None)
        # nothing to enumerate here unless it exposes REASONS
        reasons = getattr(ev, "REASONS", None)
        if isinstance(reasons, dict):
            for k, v in reasons.items():
                mapping[str(k).lower()] = v
    # If empty, seed with common names mapped to strings
    if not mapping:
        for name in ("traps_miss", "qos_breach", "latency_breach", "availability_downtime", "no_attestation",
                     "proof_invalid", "job_expired", "lease_lost"):
            mapping[name] = name
    return mapping


def _resolve_reason(s: str) -> Any:
    m = _known_reason_map()
    key = s.strip().lower().replace("-", "_")
    # accept a few aliases
    aliases = {
        "traps-miss": "traps_miss",
        "qos": "qos_breach",
        "latency": "latency_breach",
        "downtime": "availability_downtime",
        "no-attestation": "no_attestation",
    }
    key = aliases.get(key, key)
    return m.get(key, key)


def _default_fraction_for_reason(reason: Any) -> Optional[float]:
    """
    Ask slashing_rules or penalties for a default fraction for the given reason.
    """
    sr = _slashing_rules_api()
    if sr:
        # look for FRACTION_DEFAULTS dict or function
        d = getattr(sr, "FRACTION_DEFAULTS", None)
        if isinstance(d, dict):
            # reason may be enum; try direct and name
            if reason in d:
                try:
                    return float(d[reason])
                except Exception:
                    pass
            rname = getattr(reason, "name", None)
            if rname and rname in d:
                try:
                    return float(d[rname])
                except Exception:
                    pass
        fn = getattr(sr, "default_fraction", None)
        if callable(fn):
            try:
                val = fn(reason)  # type: ignore[misc]
                if val is not None:
                    return float(val)
            except Exception:
                pass
    pen = _import_module("aicf.registry.penalties")
    if pen:
        d = getattr(pen, "DEFAULT_FRACTIONS", None)
        if isinstance(d, dict):
            if reason in d:
                try:
                    return float(d[reason])
                except Exception:
                    pass
            rname = getattr(reason, "name", None)
            if rname and rname in d:
                try:
                    return float(d[rname])
                except Exception:
                    pass
    return None


# -------------------- stake & amounts --------------------

def _get_stake_amount(provider_id: str) -> Optional[float]:
    """
    Query current stake for provider from staking or registry.
    """
    st = _staking_api()
    if st:
        for fn_name in ("get_stake", "stake_of", "balance_of", "read_stake"):
            fn = getattr(st, fn_name, None)
            if callable(fn):
                try:
                    val = fn(provider_id)  # type: ignore[misc]
                    return float(val)
                except Exception:
                    continue
    reg = _registry_api()
    if reg:
        for fn_name in ("get", "get_provider", "read", "fetch"):
            fn = getattr(reg, fn_name, None)
            if callable(fn):
                try:
                    rec = fn(provider_id)  # type: ignore[misc]
                    d = _to_dict(rec)
                    if "stake" in d:
                        return float(d["stake"])
                except Exception:
                    continue
    return None


def _resolve_amount(amount: Optional[float], percent: Optional[float], reason: Any, provider_id: str) -> Tuple[Optional[float], Optional[float]]:
    """
    Return (amount, fraction) where amount is absolute units, fraction is [0,1].
    Preference: explicit amount > explicit percent > default fraction (if stake known).
    """
    if amount is not None:
        return float(amount), None
    if percent is not None:
        return None, _percent_to_fraction(percent)
    # default from rules
    frac = _default_fraction_for_reason(reason)
    if frac is not None:
        return None, float(frac)
    return None, None


# -------------------- apply slashing --------------------

def _build_event(provider_id: str, reason: Any, amount: Optional[float], fraction: Optional[float],
                 jail: bool, cooldown: Optional[int], evidence: Optional[Dict[str, Any]],
                 note: Optional[str]) -> Any:
    # Try to construct a proper SlashEvent dataclass if available.
    ev_mod = _events_types_api()
    if ev_mod and hasattr(ev_mod, "SlashEvent"):
        try:
            SE = getattr(ev_mod, "SlashEvent")
            return SE(
                provider_id=provider_id,
                reason=reason,
                amount=amount,
                fraction=fraction,
                jail=jail,
                cooldown_s=cooldown,
                evidence=evidence or {},
                note=note,
                ts_ms=_now_ms(),
            )
        except Exception:
            pass
    # Fallback: plain dict
    return {
        "provider_id": provider_id,
        "reason": reason,
        "amount": amount,
        "fraction": fraction,
        "jail": jail,
        "cooldown_s": cooldown,
        "evidence": evidence or {},
        "note": note,
        "ts_ms": _now_ms(),
    }


def _apply_with_engine(engine: Any, event: Any, provider_id: str, reason: Any,
                       amount: Optional[float], fraction: Optional[float],
                       jail: bool, cooldown: Optional[int], evidence: Optional[Dict[str, Any]],
                       note: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Try a sequence of method shapes on the engine.
    Return normalized result dict or None.
    """
    candidates = [
        ("apply_slash", dict(provider_id=provider_id, reason=reason, amount=amount, fraction=fraction,
                             jail=jail, cooldown_s=cooldown, evidence=evidence, note=note)),
        ("apply", dict(provider_id=provider_id, reason=reason, amount=amount, fraction=fraction,
                       jail=jail, cooldown_s=cooldown, evidence=evidence, note=note)),
        ("slash", dict(provider_id=provider_id, reason=reason, amount=amount, fraction=fraction,
                       jail=jail, cooldown_s=cooldown, evidence=evidence, note=note)),
        ("penalize", dict(provider_id=provider_id, reason=reason, amount=amount, fraction=fraction,
                          jail=jail, cooldown_s=cooldown, evidence=evidence, note=note)),
        ("emit", dict(event=event)),
        ("emit_slash", dict(event=event)),
    ]
    for name, kwargs in candidates:
        fn = getattr(engine, name, None)
        if callable(fn):
            try:
                res = fn(**{k: v for k, v in kwargs.items() if v is not None})  # type: ignore[misc]
                return _to_dict(res)
            except Exception:
                continue
    return None


def _apply_with_primitives(provider_id: str, reason: Any, amount: Optional[float], fraction: Optional[float],
                           jail: bool, cooldown: Optional[int]) -> Optional[Dict[str, Any]]:
    """
    Primitive fallback: reduce stake directly using staking API;
    apply jail/cooldown via penalties if available.
    """
    result: Dict[str, Any] = {"provider_id": provider_id, "reason": str(getattr(reason, "name", reason))}
    st = _staking_api()
    if fraction is not None:
        stake = _get_stake_amount(provider_id)
        if stake is not None:
            amount = (amount or 0.0) + stake * float(fraction)
    if amount is not None and st:
        for fn_name in ("reduce_stake", "slash", "decrease", "slash_amount"):
            fn = getattr(st, fn_name, None)
            if callable(fn):
                try:
                    fn(provider_id, float(amount))  # type: ignore[misc]
                    result["slashed_amount"] = float(amount)
                    break
                except Exception:
                    continue
    # penalties: jail/cooldown
    pen = _import_module("aicf.registry.penalties")
    if pen and (jail or cooldown):
        for fn_name in ("jail", "apply_cooldown", "set_cooldown"):
            fn = getattr(pen, fn_name, None)
            if callable(fn):
                try:
                    if fn_name == "jail" and jail:
                        fn(provider_id)  # type: ignore[misc]
                        result["jailed"] = True
                    elif cooldown:
                        fn(provider_id, int(cooldown))  # type: ignore[misc]
                        result["cooldown_s"] = int(cooldown)
                except Exception:
                    continue
    return result


def _apply_slash(db: Optional[str], provider_id: str, reason_in: str,
                 amount: Optional[float], percent: Optional[float], points: Optional[int],
                 jail: bool, cooldown: Optional[int], evidence: Optional[str],
                 note: Optional[str], dry_run: bool) -> Dict[str, Any]:
    reason = _resolve_reason(reason_in)
    evid_obj = _read_evidence_arg(evidence)
    amt, frac = _resolve_amount(amount, percent, reason, provider_id)

    summary: Dict[str, Any] = {
        "provider_id": provider_id,
        "reason": str(getattr(reason, "name", reason)),
        "requested_amount": amt,
        "requested_fraction": frac,
        "points": points,
        "jail": jail,
        "cooldown_s": cooldown,
        "note": note,
        "dry_run": dry_run,
    }

    if dry_run:
        # Try to compute implied amount from fraction if we can fetch stake
        if frac is not None and summary.get("implied_amount") is None:
            stake = _get_stake_amount(provider_id)
            if stake is not None:
                summary["implied_amount"] = float(stake) * float(frac)
        return summary

    engine = _open_engine(db)
    event = _build_event(provider_id, reason, amt, frac, jail, cooldown, evid_obj, note)

    # Try engine first
    res = None
    if engine:
        res = _apply_with_engine(engine, event, provider_id, reason, amt, frac, jail, cooldown, evid_obj, note)

    # Fallback to primitives
    if res is None:
        res = _apply_with_primitives(provider_id, reason, amt, frac, jail, cooldown)

    # Attach evidence/note echoes
    result = {**summary, **(res or {})}
    if evid_obj:
        result["evidence"] = evid_obj
    return result


# -------------------- CLI commands --------------------

COMMON_OPTIONS = [
    typer.Option(None, "--db", help="Optional DB URI for engine/backends (e.g., sqlite:///aicf_dev.db)."),
    typer.Option(..., "--provider", help="Provider ID to slash."),
    typer.Option(None, "--reason", help="Reason code (e.g., traps_miss, qos_breach, latency_breach, availability_downtime, no_attestation)."),
    typer.Option(None, "--amount", help="Absolute amount to slash (units)."),
    typer.Option(None, "--percent", help="Percent of current stake to slash (e.g., 1.5 for 1.5%)."),
    typer.Option(None, "--points", help="Penalty points (if supported by backend)."),
    typer.Option(False, "--jail/--no-jail", help="Jail provider (if supported)."),
    typer.Option(None, "--cooldown", help="Cooldown in seconds."),
    typer.Option(None, "--evidence", help="Evidence JSON or @file.json."),
    typer.Option(None, "--note", help="Free-form note to attach."),
    typer.Option(False, "--dry-run", help="Do not apply; show computed parameters only."),
    typer.Option(False, "--json", help="Output result as JSON."),
]

def _apply_command(
    db: Optional[str],
    provider: str,
    reason: Optional[str],
    amount: Optional[float],
    percent: Optional[float],
    points: Optional[int],
    jail: bool,
    cooldown: Optional[int],
    evidence: Optional[str],
    note: Optional[str],
    dry_run: bool,
    json_out: bool,
) -> None:
    if not reason:
        typer.secho("Missing --reason. Use `list-reasons` to see options.", fg=typer.colors.RED)
        raise typer.Exit(2)

    res = _apply_slash(
        db=db,
        provider_id=provider,
        reason_in=reason,
        amount=amount,
        percent=percent,
        points=points,
        jail=jail,
        cooldown=cooldown,
        evidence=evidence,
        note=note,
        dry_run=dry_run,
    )
    if json_out:
        typer.echo(json.dumps(res, indent=2, sort_keys=True))
        return

    typer.secho("Slash applied (or dry-run):", bold=True)
    for k in ("provider_id", "reason", "requested_amount", "requested_fraction", "points", "jail", "cooldown_s", "note", "implied_amount", "slashed_amount"):
        if k in res and res[k] is not None:
            v = res[k]
            if k in ("requested_amount", "implied_amount", "slashed_amount"):
                v = _fmt_amount(v)
            elif k == "requested_fraction":
                v = f"{float(v)*100:.4f}%"
            typer.echo(f"- {k}: {v}")


@app.command("apply")
def cmd_apply(
    db: Optional[str] = COMMON_OPTIONS[0],
    provider: str = COMMON_OPTIONS[1],
    reason: Optional[str] = COMMON_OPTIONS[2],
    amount: Optional[float] = COMMON_OPTIONS[3],
    percent: Optional[float] = COMMON_OPTIONS[4],
    points: Optional[int] = COMMON_OPTIONS[5],
    jail: bool = COMMON_OPTIONS[6],
    cooldown: Optional[int] = COMMON_OPTIONS[7],
    evidence: Optional[str] = COMMON_OPTIONS[8],
    note: Optional[str] = COMMON_OPTIONS[9],
    dry_run: bool = COMMON_OPTIONS[10],
    json_out: bool = COMMON_OPTIONS[11],
) -> None:
    """
    Apply a slash with explicit reason/amount/percent options.
    """
    _apply_command(db, provider, reason, amount, percent, points, jail, cooldown, evidence, note, dry_run, json_out)


@app.command("traps-miss")
def cmd_traps_miss(
    provider: str = typer.Option(..., "--provider", help="Provider ID to slash."),
    db: Optional[str] = typer.Option(None, "--db", help="Optional DB URI."),
    percent: Optional[float] = typer.Option(1.0, "--percent", help="Default: 1.0%% of stake."),
    amount: Optional[float] = typer.Option(None, "--amount", help="Absolute amount to slash."),
    jail: bool = typer.Option(False, "--jail/--no-jail"),
    cooldown: Optional[int] = typer.Option(None, "--cooldown", help="Cooldown in seconds."),
    evidence: Optional[str] = typer.Option(None, "--evidence", help="Evidence JSON or @file."),
    note: Optional[str] = typer.Option("Simulated traps miss", "--note"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    _apply_command(db, provider, "traps_miss", amount, percent, None, jail, cooldown, evidence, note, dry_run, json_out)


@app.command("qos-breach")
def cmd_qos_breach(
    provider: str = typer.Option(..., "--provider"),
    db: Optional[str] = typer.Option(None, "--db"),
    percent: Optional[float] = typer.Option(0.5, "--percent", help="Default: 0.5%% of stake."),
    amount: Optional[float] = typer.Option(None, "--amount"),
    jail: bool = typer.Option(False, "--jail/--no-jail"),
    cooldown: Optional[int] = typer.Option(None, "--cooldown"),
    evidence: Optional[str] = typer.Option(None, "--evidence"),
    note: Optional[str] = typer.Option("Simulated QoS breach", "--note"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    _apply_command(db, provider, "qos_breach", amount, percent, None, jail, cooldown, evidence, note, dry_run, json_out)


@app.command("latency")
def cmd_latency_breach(
    provider: str = typer.Option(..., "--provider"),
    db: Optional[str] = typer.Option(None, "--db"),
    percent: Optional[float] = typer.Option(0.25, "--percent", help="Default: 0.25%% of stake."),
    amount: Optional[float] = typer.Option(None, "--amount"),
    jail: bool = typer.Option(False, "--jail/--no-jail"),
    cooldown: Optional[int] = typer.Option(None, "--cooldown"),
    evidence: Optional[str] = typer.Option(None, "--evidence"),
    note: Optional[str] = typer.Option("Simulated latency breach", "--note"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    _apply_command(db, provider, "latency_breach", amount, percent, None, jail, cooldown, evidence, note, dry_run, json_out)


@app.command("no-attestation")
def cmd_no_attestation(
    provider: str = typer.Option(..., "--provider"),
    db: Optional[str] = typer.Option(None, "--db"),
    percent: Optional[float] = typer.Option(2.0, "--percent", help="Default: 2.0%% of stake."),
    amount: Optional[float] = typer.Option(None, "--amount"),
    jail: bool = typer.Option(True, "--jail/--no-jail", help="Default: jail"),
    cooldown: Optional[int] = typer.Option(86400, "--cooldown", help="Default: 1 day."),
    evidence: Optional[str] = typer.Option(None, "--evidence"),
    note: Optional[str] = typer.Option("Simulated missing/invalid attestation", "--note"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    _apply_command(db, provider, "no_attestation", amount, percent, None, jail, cooldown, evidence, note, dry_run, json_out)


@app.command("downtime")
def cmd_availability_downtime(
    provider: str = typer.Option(..., "--provider"),
    db: Optional[str] = typer.Option(None, "--db"),
    percent: Optional[float] = typer.Option(0.75, "--percent", help="Default: 0.75%% of stake."),
    amount: Optional[float] = typer.Option(None, "--amount"),
    jail: bool = typer.Option(False, "--jail/--no-jail"),
    cooldown: Optional[int] = typer.Option(None, "--cooldown"),
    evidence: Optional[str] = typer.Option(None, "--evidence"),
    note: Optional[str] = typer.Option("Simulated availability downtime", "--note"),
    dry_run: bool = typer.Option(False, "--dry-run"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    _apply_command(db, provider, "availability_downtime", amount, percent, None, jail, cooldown, evidence, note, dry_run, json_out)


@app.command("list-reasons")
def cmd_list_reasons(json_out: bool = typer.Option(False, "--json", help="Output JSON.")) -> None:
    m = _known_reason_map()
    rows: List[Dict[str, Any]] = []
    for k, v in sorted(m.items()):
        row: Dict[str, Any] = {"reason": k}
        # default fraction if available
        df = _default_fraction_for_reason(v)
        if df is not None:
            row["default_percent"] = round(df * 100.0, 6)
        rows.append(row)
    if json_out:
        typer.echo(json.dumps(rows, indent=2, sort_keys=True))
        return
    typer.secho("Known reason codes:", bold=True)
    for r in rows:
        rp = f" (default {r['default_percent']}%)" if "default_percent" in r else ""
        typer.echo(f"- {r['reason']}{rp}")


@app.callback(invoke_without_command=True)
def _main(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return
    typer.echo(app.get_help())


def get_app() -> typer.Typer:
    return app


if __name__ == "__main__":
    app()
