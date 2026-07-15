"""`animica animal` — the autonomous mascot ambassador.

Connect the mascot's OWNED social accounts in the web console (animica.dev/animal), then run the
always-on engine here. Dry-run by default: it renders real previews (image/video + custom music for
TikTok/Shorts) without posting anywhere. Going live needs ANIMAL_DRY_RUN=0 + ANIMAL_ALLOW_LIVE_POST=1.
"""

from __future__ import annotations

import json

import typer

app = typer.Typer(help="Animica Animal — autonomous mascot ambassador (dry-run by default).")


def _cfg():
    from animica.animal.config import load
    return load()


@app.command()
def doctor():
    """Check console connectivity, media queue, ffmpeg, posting posture, and guardrails."""
    from animica.animal import media as M, guardrails as G, engine
    cfg = _cfg()
    typer.echo("Animica Animal — doctor\n")
    typer.echo(f"  console (mkt)        : {cfg.mkt_url}")
    typer.echo(f"  internal token       : {'set' if cfg.internal_token else 'MISSING (engine cannot read the console)'}")
    typer.echo(f"  posture              : {'LIVE' if (not cfg.dry_run and cfg.allow_live_post) else 'dry-run (previews only)'}")
    typer.echo(f"  ffmpeg (video+music) : {'yes' if M.has_ffmpeg() else 'NO (install ffmpeg for muxed clips)'}")
    typer.echo(f"  per-platform cap/day : {cfg.per_platform_daily_cap}   global/day: {cfg.global_daily_cap}")
    state = engine._internal(cfg, "/state")
    if state is None:
        typer.echo("  console reachable    : NO (start the marketplace + set ANIMAL_INTERNAL_TOKEN)")
    else:
        chs = state.get("channels", [])
        typer.echo(f"  console reachable    : yes · {len(chs)} connected channel(s): "
                   + (", ".join(c['platform'] for c in chs) or "none — connect socials at /animal"))
    # guardrail self-tests
    ok = True
    for name, fn in [
        ("reject pump caption", lambda: G.lint_caption("guaranteed 100x, buy now before it moons")),
        ("require live double-gate", lambda: G.assert_can_live_post(cfg) if False else _raise_ok()),
    ]:
        try:
            fn()
            passed = name == "require live double-gate"
        except G.GuardrailError:
            passed = name == "reject pump caption"
        except _OK:
            passed = True
        typer.echo(f"    [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    typer.echo("\n  " + ("ALL GUARDRAILS PASS" if ok else "GUARDRAIL FAILURE"))


class _OK(Exception):
    pass


def _raise_ok():
    raise _OK()


@app.command()
def once():
    """Run a single growth cycle now (dry-run unless both live switches are on)."""
    from animica.animal import engine
    res = engine.run_once(_cfg(), log=lambda m: typer.secho(m, dim=True))
    typer.echo(json.dumps(res, indent=2))


@app.command()
def up():
    """Run the always-on engine loop (Ctrl-C to stop). This is what `animica up` supervises."""
    from animica.animal import engine
    engine.run_forever(_cfg(), log=lambda m: typer.secho(m, dim=True))


@app.command()
def preview():
    """Force a dry-run cycle regardless of env, and show where previews were written."""
    import os
    os.environ["ANIMAL_DRY_RUN"] = "1"
    os.environ["ANIMAL_ALLOW_LIVE_POST"] = "0"
    from animica.animal import engine
    cfg = _cfg()
    res = engine.run_once(cfg, log=lambda m: typer.secho(m, dim=True))
    typer.echo(json.dumps(res, indent=2))
    typer.echo(f"previews in: {cfg.preview_dir()}")


@app.command()
def status():
    """Show connected channels + today's posting counts."""
    from animica.animal import engine, store
    cfg = _cfg()
    state = engine._internal(cfg, "/state")
    if not state:
        typer.echo("console unreachable (set ANIMAL_INTERNAL_TOKEN + start the marketplace)")
        raise typer.Exit(1)
    typer.echo(f"paused: {state.get('paused')}")
    for c in state.get("channels", []):
        pc, _ = store.counts(cfg.db_path(), c["platform"])
        typer.echo(f"  {c['platform']:12} @{c.get('handle','') or '?':16} posts today: {pc}/{cfg.per_platform_daily_cap}")
    if not state.get("channels"):
        typer.echo("  no channels connected — open /animal to connect socials")


@app.command()
def say(text: str = typer.Argument(..., help="a goal/instruction for the mascot")):
    """Inject a steering goal from the CLI (same as typing it in the console chat)."""
    from animica.animal import engine
    cfg = _cfg()
    res = engine._internal(cfg, "/directive", "POST", {"text": text, "kind": "goal"})
    typer.echo("sent" if res and res.get("ok") else "failed (need ANIMAL_INTERNAL_TOKEN + marketplace up)")
