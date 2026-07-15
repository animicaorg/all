"""`animica growth` — the Animica Growth Engine.

Autonomous ecosystem analytics + consent-based marketing. Analyze real metrics, draft content,
run a double-opt-in newsletter (dry-run by default), and prepare directory listings. Every
outbound action is gated: dry-run + human approval by default; live email needs GROWTH_DRY_RUN=0
+ GROWTH_ALLOW_LIVE_SEND=1 + an approved campaign. No scraping, no autonomous external posting.
"""

from __future__ import annotations

import json

import typer

app = typer.Typer(help="Animica Growth Engine — analytics + consent-based marketing (dry-run by default).")
newsletter_app = typer.Typer(help="Consent-based newsletter (double opt-in; dry-run by default).")
listings_app = typer.Typer(help="Directory/aggregator listing kit + sanctioned submissions.")
app.add_typer(newsletter_app, name="newsletter")
app.add_typer(listings_app, name="listings")


def _cfg():
    from animica.growth.config import load
    return load()


@app.command()
def doctor():
    """Check data sources, safety config, and run the guardrail self-tests."""
    from animica.growth import collectors, guardrails as G
    cfg = _cfg()
    typer.echo("Animica Growth Engine — doctor\n")
    typer.echo(f"  dry_run              : {cfg.dry_run}  (live send {'ENABLED' if cfg.allow_live_send else 'off'})")
    typer.echo(f"  org postal address   : {'set' if cfg.org_postal.strip() else 'MISSING (required to send)'}")
    typer.echo(f"  internal token       : {'set' if cfg.internal_token else 'MISSING (needed to read confirmed list)'}")
    typer.echo(f"  SMTP                 : {'configured' if (cfg.smtp_user and cfg.smtp_pass) else 'not configured (dry-run outbox only)'}")
    typer.echo(f"  owned channels       : {', '.join(cfg.owned_channels)}")
    typer.echo(f"  effective daily cap  : {G.effective_daily_cap(cfg.email_per_day)}")
    # data sources
    snap = collectors.snapshot(cfg)
    typer.echo(f"\n  live metrics         : {snap['ok_count']}/{len(snap['metrics'])} OK"
               + (f"  (gaps: {', '.join(snap['unavailable'])})" if snap["unavailable"] else ""))
    # guardrail self-tests
    checks = []
    try:
        G.validate_email("a@b\r\nbcc: x@y.com")
        checks.append(("reject header-injection email", False))
    except G.GuardrailError:
        checks.append(("reject header-injection email", True))
    try:
        G.financial_lint("guaranteed 100x returns", strict=False)
        checks.append(("reject pump phrasing", False))
    except G.GuardrailError:
        checks.append(("reject pump phrasing", True))
    try:
        G.assert_can_spam({}, "body", "123 Main St", "a@b.com")
        checks.append(("require unsubscribe headers", False))
    except G.GuardrailError:
        checks.append(("require unsubscribe headers", True))
    typer.echo("\n  guardrail self-tests :")
    ok = True
    for name, passed in checks:
        typer.echo(f"    [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    typer.echo("\n  " + ("ALL GUARDRAILS PASS" if ok else "GUARDRAIL FAILURE — do not send"))
    raise typer.Exit(0 if ok else 1)


@app.command()
def analyze(json_out: bool = typer.Option(False, "--json")):
    """Collect real ecosystem metrics and print an honest analysis."""
    from animica.growth import collectors, analysis, store
    cfg = _cfg()
    snap = collectors.snapshot(cfg)
    prev = store.last_snapshot(cfg, before=snap["ts"])
    rep = analysis.analyze(snap, prev)
    typer.echo(json.dumps(rep, indent=2) if json_out else _fmt_analysis(rep))


@app.command()
def report():
    """Run a full (dry-run) growth cycle and print the markdown report."""
    from animica.growth import engine, report as R
    rpt = engine.run_cycle(_cfg(), log=lambda m: typer.secho(m, dim=True))
    typer.echo("\n" + R.to_markdown(rpt))


@app.command()
def run(send: bool = typer.Option(False, "--send-newsletter", help="also send the newsletter (still gated by dry-run/approval)"),
        by: str = typer.Option(None, "--by", help="approver handle when sending")):
    """Run one autonomous growth cycle (analyze → draft → preview; sending stays gated)."""
    from animica.growth import engine
    rpt = engine.run_cycle(_cfg(), send_newsletter=send, approver=by, log=lambda m: typer.secho(m, dim=True))
    typer.echo(json.dumps({k: rpt[k] for k in ("health_score", "newsletter", "drafts")}, indent=2))


@app.command()
def content():
    """Generate the first newsletter campaign + social drafts (saved to the draft store)."""
    from animica.growth import content as C, collectors, analysis, store
    cfg = _cfg()
    snap = collectors.snapshot(cfg); rep = analysis.analyze(snap)
    camp = C.nonkyc_welcome_campaign(cfg, rep); store.save_draft(cfg, camp)
    socs = C.draft_social_posts(cfg, rep)
    typer.echo(f"campaign draft: {camp['content_hash']}  ({camp['title']})")
    typer.echo(f"social drafts : {len(socs)} (queued for human approval)")
    typer.echo("preview the campaign with: animica growth newsletter preview")


# ---- newsletter ----
@newsletter_app.command("stats")
def nl_stats():
    """Show subscriber counts (confirmed/pending/unsubscribed/suppressed)."""
    from animica.growth import collectors
    cfg = _cfg()
    d = collectors._get_json(f"{cfg.mkt_url}/api/mkt/v1/newsletter/stats")
    typer.echo(json.dumps(d, indent=2) if d else "newsletter stats unavailable")


@newsletter_app.command("preview")
def nl_preview():
    """Build the first campaign and write a DRY-RUN copy per confirmed subscriber to the outbox."""
    from animica.growth import content as C, collectors, analysis, newsletter
    cfg = _cfg()
    snap = collectors.snapshot(cfg)
    camp = C.nonkyc_welcome_campaign(cfg, analysis.analyze(snap))
    res = newsletter.send_campaign(cfg, camp, log=lambda m: typer.secho(m, dim=True))
    typer.echo(json.dumps(res, indent=2))


@newsletter_app.command("approve")
def nl_approve(content_hash: str = typer.Argument(...), by: str = typer.Option(..., "--by", help="human approver handle")):
    """Record a human approval binding a campaign's content hash (required before a live send)."""
    from animica.growth import store
    store.record_approval(_cfg(), content_hash, by)
    typer.echo(f"approved {content_hash} by {by}")


@newsletter_app.command("send")
def nl_send(by: str = typer.Option(..., "--by", help="approver handle"),
            limit: int = typer.Option(None, "--limit", help="cap this send")):
    """Send the first campaign to CONFIRMED subscribers. Gated: needs GROWTH_DRY_RUN=0 +
    GROWTH_ALLOW_LIVE_SEND=1 + an approved campaign; otherwise writes a dry-run outbox copy."""
    from animica.growth import content as C, collectors, analysis, newsletter
    cfg = _cfg()
    camp = C.nonkyc_welcome_campaign(cfg, analysis.analyze(collectors.snapshot(cfg)))
    res = newsletter.send_campaign(cfg, camp, approver=by, limit=limit, log=lambda m: typer.secho(m, fg="green"))
    typer.echo(json.dumps(res, indent=2))


@newsletter_app.command("flush-outbox")
def nl_flush():
    """Deliver queued double-opt-in confirmation emails (gated; dry-run just reports)."""
    from animica.growth import newsletter
    typer.echo(json.dumps(newsletter.flush_outbox(_cfg(), log=lambda m: typer.secho(m, dim=True)), indent=2))


# ---- listings ----
@listings_app.command("prepare")
def li_prepare():
    """Build the accurate listing application + per-target instructions (writes listing-kit.json)."""
    from animica.growth import listings
    res = listings.prepare(_cfg())
    typer.echo(f"prepared application for {len(res['targets'])} targets → {res['kit_path']}")
    typer.echo(f"  {res['auto']} auto-submittable · {res['queued']} human-form (prepared + queued)")


@listings_app.command("submit")
def li_submit(do_submit: bool = typer.Option(False, "--submit", help="actually submit to sanctioned endpoints (default: dry-run)")):
    """Submit to targets that sanction automated submission; queue the rest for you to submit."""
    from animica.growth import listings
    res = listings.submit(_cfg(), do_submit=do_submit, log=lambda m: typer.echo(m))
    typer.echo(json.dumps(res, indent=2))


@listings_app.command("status")
def li_status():
    """Show the listing submission log."""
    from animica.growth.config import load
    import sqlite3
    cfg = load()
    with sqlite3.connect(cfg.db_path()) as c:
        rows = c.execute("SELECT target,method,status,detail,ts FROM listing_log ORDER BY ts DESC LIMIT 40").fetchall()
    for r in rows:
        typer.echo(f"  {r[0]:16} {r[2]:16} {r[3]}")
    if not rows:
        typer.echo("no listing activity yet — run `animica growth listings submit`")


def _fmt_analysis(rep: dict) -> str:
    out = [f"Ecosystem health: {rep['health_score']}%", "Facts:"]
    for f in rep.get("facts", []):
        out.append(f"  • {f['label']}: {f['value']} {f.get('unit','')}  (source {f['source']})")
    if rep.get("gaps"):
        out.append(f"Gaps (unavailable, not fabricated): {', '.join(rep['gaps'])}")
    if rep.get("opportunities"):
        out.append("Opportunities:")
        out += [f"  • {o}" for o in rep["opportunities"]]
    return "\n".join(out)
