"""`animica animal` — the autonomous mascot ambassador.

Connect the mascot's OWNED social accounts in the web console (animica.dev/animal), then run the
always-on engine here. Dry-run by default: it renders real previews (image/video + custom music for
TikTok/Shorts) without posting anywhere. Going live needs ANIMAL_DRY_RUN=0 + ANIMAL_ALLOW_LIVE_POST=1.
"""

from __future__ import annotations

import json
import os

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


class _LiveHeartbeat:
    """Background thread that reports live status to the console every ~20s (viewers, uptime),
    and posts a final live:false on stop so the badge goes offline cleanly."""

    def __init__(self, yt, watch_url: str, character: str, period: float = 20.0):
        import threading
        self.yt, self.watch_url, self.character, self.period = yt, watch_url, character, period
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, name="anm-live-heartbeat", daemon=True)
        self._started = None

    def _post(self, live: bool):
        import time
        from animica.animal import engine
        cfg = _cfg()
        uptime = int(time.time() - self._started) if self._started else 0
        try:
            viewers = self.yt.viewers() if live else 0
        except Exception:
            viewers = 0
        engine._internal(cfg, "/live", "POST", {
            "live": live, "watchUrl": self.watch_url, "viewers": viewers,
            "uptime": uptime, "character": self.character,
        }, timeout=10)

    def _run(self):
        import time
        self._started = time.time()
        while not self._stop.is_set():
            try:
                self._post(True)
            except Exception:
                pass
            self._stop.wait(self.period)

    def start(self):
        self._t.start()

    def stop(self):
        self._stop.set()
        try:
            self._post(False)  # flip the badge offline immediately
        except Exception:
            pass


@app.command()
def stream(
    preview: str = typer.Option("", help="Render to this local MP4 instead of going live (no YouTube needed)."),
    seconds: float = typer.Option(0.0, help="Stop after N seconds (0 = run until Ctrl-C / forever)."),
    rtmp: str = typer.Option("", help="YouTube RTMP ingest URL rtmp://a.rtmp.youtube.com/live2/<key> (manual)."),
    youtube: bool = typer.Option(False, "--youtube", help="Auto-create a 24/7 YouTube broadcast from the connected account."),
    record_dir: str = typer.Option("", help="Where to write 1-hour VOD segment files (auto with --youtube)."),
    channel: str = typer.Option("Animica Animal", help="Channel name shown on the overlay."),
    width: int = typer.Option(1280), height: int = typer.Option(720), fps: int = typer.Option(24),
    voice: str = typer.Option("animalese", help="animalese | piper | off"),
    music: str = typer.Option("auto", help="auto | <path to an audio file> | off"),
    bitrate_k: int = typer.Option(4500, help="Video bitrate in kbps."),
):
    """Run the 24/7 interactive AI livestream (YouTube), or render a local preview MP4.

    Preview (no account needed):  animica animal stream --preview out.mp4 --seconds 15
    Go live (after connecting YouTube at animica.dev/animal):  animica animal stream --youtube
    """
    from animica.animal.stream import config as SC
    from animica.animal.stream.brain import Brain, make_line_provider
    from animica.animal.stream.contract import StreamConfig
    from animica.animal.stream.pipeline import StreamPipeline

    log = lambda m: typer.secho(m, dim=True)  # noqa: E731
    char = SC.load_character()
    cfg = StreamConfig(width=width, height=height, fps=fps, voice=voice, music=music,
                       bitrate_k=bitrate_k, preview_path=preview, rtmp_url=rtmp,
                       record_dir=record_dir, channel_name=channel)

    chat_source = chat_sink = None
    yt = None
    go_youtube = youtube or (not preview and not rtmp)
    if go_youtube:
        from animica.animal.stream.youtube import YouTubeLive
        yt = YouTubeLive.from_console(log=log)
        if yt is None:
            typer.secho("YouTube not connected. Open animica.dev/animal → connect YouTube, then rerun.", fg="red")
            raise typer.Exit(2)
        # Reuse the same broadcast across restarts so the public watch URL stays stable.
        state_path = os.environ.get("ANIMAL_BROADCAST_STATE") or os.path.join(
            record_dir or "/root/anm-vods", ".broadcast.json")
        # Go-live is resilient to a YouTube Data-API quota 403: a 24/7 stream that restarts
        # (crash/reboot) after the day's quota is spent CANNOT create or even verify a
        # broadcast until the quota resets (~midnight Pacific). Crashing out here just makes
        # systemd hit its start-limit and give the stream up for good; instead WAIT and retry
        # so the stream self-heals the moment the quota resets. Non-quota errors still fail fast.
        import time as _t
        _golive_deadline = _t.time() + float(os.environ.get("ANIMAL_GOLIVE_MAX_WAIT_S", str(26 * 3600)))
        info = None
        while info is None:
            try:
                info = yt.ensure_live(title=f"{char.name} — 24/7 Animica Live",
                                      record_dir=record_dir, state_path=state_path)
            except Exception as e:
                m = str(e)
                if not ("403" in m and "quota" in m.lower()) or _t.time() >= _golive_deadline:
                    raise
                wait = min(1800.0, max(60.0, yt._seconds_until_quota_reset(_t.time())))
                typer.secho(
                    f"YouTube quota exhausted — can't go live yet; waiting {int(wait)}s then "
                    f"retrying (the stream resumes automatically when the quota resets).",
                    fg="yellow")
                _t.sleep(wait)
        cfg.rtmp_url = info["rtmp_url"]
        cfg.record_dir = record_dir or info["record_dir"]
        chat_source, chat_sink = yt.chat_source(), yt.chat_sink()
        typer.secho(f"● LIVE: {info['watch_url']}", fg="green")

    brain = Brain(cfg, char, chat_source=chat_source, chat_sink=chat_sink,
                  rag=SC_rag_for(char), log=log)
    pipe = StreamPipeline(cfg, char, net_provider=SC.default_net_provider,
                          line_provider=make_line_provider(brain), log=log)

    uploader = None
    if yt is not None and cfg.record_dir:
        from animica.animal.stream.segments import SegmentUploader
        uploader = SegmentUploader(yt, cfg.record_dir, char, log=log)
        uploader.start()

    # Heartbeat the live status to the console so animica.dev/animal + the homepage can show
    # "● LIVE" with a watch link, viewer count and uptime. Best-effort; never blocks the stream.
    hb = None
    if yt is not None:
        hb = _LiveHeartbeat(yt, info["watch_url"], char.name)
        hb.start()

    try:
        rc = pipe.run(max_seconds=seconds)
    except KeyboardInterrupt:
        pipe.stop()
        rc = 0
    finally:
        if hb:
            hb.stop()
        if uploader:
            uploader.stop()
        # 24/7 default: DON'T end the broadcast on a restart — that is exactly what churns
        # the watch URL. ensure_live() reuses it next start. Only end when explicitly asked
        # (operator teardown) via ANIMAL_END_ON_STOP=1.
        if yt is not None and os.environ.get("ANIMAL_END_ON_STOP") == "1":
            yt.end()
    if preview and rc == 0:
        typer.secho(f"wrote {preview}", fg="green")
    raise typer.Exit(rc)


def SC_rag_for(char):
    """Return a RAG query callable for this character's knowledge base, or None."""
    try:
        from animica.animal.stream.knowledge import rag_for
        return rag_for(char)
    except Exception:
        return None
