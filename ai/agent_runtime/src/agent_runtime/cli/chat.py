"""`animica chat` — an agentic coding assistant in your terminal.

    animica chat                       start a session in the current directory
    animica chat "fix the bug in x"    one shot, then exit
    animica chat -p auto-edit          start with edits pre-approved
    animica chat --continue            pick up the last session

It reads and edits files, runs commands, and asks before anything with a
consequence. The working directory is wherever you started it — nothing to
configure.

Why this file is a third of its old size
----------------------------------------
It was 1373 lines, 22 slash commands and 15 flags, and most of that was machinery
for choosing between three model providers and settling each turn's cost against a
wallet: `--rpc-url`, `--wallet`, `--wallet-label`, `--yolo`,
`--require-distributed`, `--decode-mode`, `--max-cost`, plus `/balance`,
`/provider`, `/tier`, `/tiers`, `/decode`, `/status`, `/cwd`.

None of it earns its place once the model is kimi-k3 on the public endpoint: that
path needs no wallet, costs nothing, and has one tier. So all of it is gone. The
two things that were actually load-bearing — the approval modes and the swarm —
stayed.

Tools are available on every turn now, instead of behind a separate `/agent`
command. Asking what a function does and asking for it to be fixed should not be
two different modes of the same program.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from agent_runtime.agentic import (
    DEFAULT_PERMISSION_MODE,
    load_project_context,
    PERMISSION_MODES,
    PermissionPolicy,
    ToolSpec,
    category_of,
    run_agent_loop,
)
from agent_runtime.provider_hosted import HostedProvider
from agent_runtime.providers import TurnRequest

# `allow_interspersed_args` matters more than it looks. This Typer is mounted as a
# GROUP by the parent CLI (`app.add_typer(chat.app, name="chat")`), and a Click
# group stops at the first non-option token and treats the rest as a subcommand —
# so `animica chat "fix this" -p auto-edit` failed with "No such command '-p'"
# while `animica chat -p auto-edit "fix this"` worked. Nobody types the flags
# first, so the natural form has to be the one that works.
app = typer.Typer(add_completion=False, no_args_is_help=False,
                  context_settings={"allow_interspersed_args": True},
                  help="An agentic coding assistant in your terminal.")

MAX_OUTPUT_TOKENS = 2048


def _enable_line_editing() -> None:
    """Give `input()` arrow-key history and emacs editing.

    Importing readline is all it takes — CPython's `input()` uses it once present.
    Without it, arrow-up prints `^[[A` and there is no way to fix a typo mid-line,
    which is the first thing anyone tries in a REPL. History persists so the last
    session's prompts are still there tomorrow.
    """
    try:
        import readline
    except ImportError:
        return          # Windows without pyreadline; the REPL still works
    hist = Path(os.environ.get("ANIMICA_DATA_DIR", "~/.animica")).expanduser() / "chat_history"
    try:
        hist.parent.mkdir(parents=True, exist_ok=True)
        if hist.exists():
            readline.read_history_file(str(hist))
    except OSError:
        pass
    readline.set_history_length(2000)
    import atexit
    atexit.register(lambda: _write_history(hist))


def _write_history(path) -> None:
    try:
        import readline
        readline.write_history_file(str(path))
    except Exception:      # noqa: BLE001 — never fail an exit over history
        pass


# --------------------------------------------------------------------------- #
# Sessions — local files, no wallet, no chain                                 #
# --------------------------------------------------------------------------- #
#
# Transcripts used to persist to the DA layer, so remembering a session needed a
# wallet AND a reachable node. For a CLI whose whole point is working without
# either, that was the wrong trade. A session is now a JSON file in your own home
# directory.

def _sessions_dir() -> Path:
    p = Path(os.environ.get("ANIMICA_DATA_DIR", "~/.animica")).expanduser() / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _session_path(sid: str) -> Path:
    return _sessions_dir() / f"{sid}.json"


def _new_session_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def _save_session(sid: str, cwd: str, turns: list[dict]) -> None:
    try:
        _session_path(sid).write_text(json.dumps(
            {"id": sid, "cwd": cwd, "updated": int(time.time()), "turns": turns},
            indent=1), encoding="utf-8")
    except OSError:
        pass          # a session we cannot save is not worth failing a turn over


def _load_session(sid: str) -> Optional[dict]:
    try:
        return json.loads(_session_path(sid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _list_sessions(limit: int = 20) -> list[dict]:
    out = []
    for f in sorted(_sessions_dir().glob("*.json"), reverse=True)[:limit]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        first = next((t.get("content", "") for t in d.get("turns", [])
                      if t.get("role") == "user"), "")
        out.append({"id": d.get("id", f.stem), "cwd": d.get("cwd", "?"),
                    "turns": len(d.get("turns", [])),
                    "title": (first or "").strip().replace("\n", " ")[:56]})
    return out


def _latest_session_id() -> Optional[str]:
    s = _list_sessions(1)
    return s[0]["id"] if s else None


def _sessions_table(rows: list[dict]) -> Table:
    t = Table(box=None)
    t.add_column("id", style="bold cyan")
    t.add_column("turns", justify="right")
    t.add_column("where", style="dim")
    t.add_column("first message")
    for r in rows:
        t.add_row(r["id"], str(r["turns"]), r["cwd"], r["title"])
    return t



# The model is shown tool results wrapped in [TOOL_RESULT] … [/TOOL_RESULT], and it
# sometimes echoes that wrapper back inside its final answer. Rendering it verbatim
# put raw protocol markup in front of the user, so it is stripped from what gets
# displayed — never from what the model sees, which still needs the delimiters to
# know where a result begins and ends.
_PROTOCOL_NOISE = re.compile(
    r"\[/?TOOL_(?:CALL|RESULT)\]|^\s*```(?:json)?\s*$", re.MULTILINE)


MAX_PIPED_BYTES = 24_000


def _read_piped_stdin() -> str:
    """Return piped stdin, if this invocation has any.

    `cat build.log | animica chat "why did this fail"` is one of the two or three
    things a CLI agent is actually for, and without this the pipe is silently
    discarded — the model answers about nothing and looks broken. Capped, because
    a 50MB log would spend the whole context window before the question arrives.
    """
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    try:
        data = sys.stdin.read(MAX_PIPED_BYTES + 1)
    except Exception:  # noqa: BLE001 — a closed or undecodable pipe is not fatal
        return ""
    if len(data) > MAX_PIPED_BYTES:
        data = data[:MAX_PIPED_BYTES] + "\n… (input truncated)"
    return data.strip()


def _with_piped_input(prompt: str, piped: str) -> str:
    return (f"{prompt}\n\nThe following was piped in on stdin:\n\n"
            f"```\n{piped}\n```")


def _clean_answer(text: str) -> str:
    if not text:
        return ""
    out = _PROTOCOL_NOISE.sub("", text)
    # Collapse the blank lines the removal leaves behind.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()

# --------------------------------------------------------------------------- #
# Approval prompt                                                             #
# --------------------------------------------------------------------------- #

_WHAT_IT_DOES = {
    "write": "will change files on disk",
    "destroy": "will DELETE",
    "exec": "will run code on this machine with your permissions",
    "net": "will send a request off this machine",
}


def _prompter(console: Console):
    def ask(tool: ToolSpec, args: dict) -> str:
        cat = category_of(tool)
        colour = "red" if cat in ("exec", "destroy") else "yellow"
        console.print(f"\n[{colour}]⚠ {tool.name} — "
                      f"{_WHAT_IT_DOES.get(cat, 'needs permission')}[/{colour}]")
        for k, v in args.items():
            sv = str(v)
            if len(sv) > 400:
                sv = sv[:400] + f"… (+{len(sv) - 400} more)"
            console.print(f"  [bold]{k}:[/bold] {sv}")
        widen = ("all edits from now on" if cat in ("write", "net")
                 else "EVERYTHING from now on, including shell")
        try:
            a = input(f"[y]es / [s]ession / [a]lways ({widen}) / [n]o: ").strip().lower()
        except EOFError:
            return "deny"       # a closed stdin is not consent
        return {"y": "allow", "yes": "allow", "s": "allow_session",
                "session": "allow_session", "a": "allow_mode",
                "always": "allow_mode"}.get(a, "deny")
    return ask


# --------------------------------------------------------------------------- #
# The model                                                                   #
# --------------------------------------------------------------------------- #

# Set when a streamer actually wrote to the terminal during this turn, so the
# caller knows whether re-rendering the answer would duplicate it.
_streamed_recently = [False]


class _ProseStreamer:
    """Write model output to the terminal as it arrives, minus the protocol.

    A turn takes 50–145 seconds on this network, because inference comes from
    volunteer miners. Without this the whole wait is a spinner, which is
    indistinguishable from a hang — the single worst thing about using the CLI.

    Most agentic turns are a `[TOOL_CALL]` block rather than prose, and streaming
    raw tool JSON at someone is noise, so emission stops at `[TOOL_CALL]` and
    resumes after `[/TOOL_CALL]`. The `· tool name` line already reports the call.
    Tags can straddle chunk boundaries, so a short tail is held back rather than
    printing half a marker.
    """

    OPEN = "[TOOL_CALL]"
    CLOSE = "[/TOOL_CALL]"
    _HOLD = max(len(OPEN), len(CLOSE))

    def __init__(self, on_first, out=None) -> None:
        self.on_first = on_first
        self.out = out if out is not None else sys.stdout
        self.pending = ""
        self.muted = False
        self.wrote = False
        # Scoped to this turn, not the whole request. What the caller needs to know
        # is whether the FINAL turn reached the terminal — an earlier iteration
        # having printed something is not a reason to withhold the answer.
        _streamed_recently[0] = False

    def feed(self, chunk: str) -> None:
        self.pending += chunk
        while self.pending:
            if self.muted:
                i = self.pending.find(self.CLOSE)
                if i == -1:
                    # Keep only enough to recognise a split closing tag.
                    self.pending = self.pending[-self._HOLD:]
                    return
                self.pending = self.pending[i + len(self.CLOSE):]
                self.muted = False
                continue
            i = self.pending.find(self.OPEN)
            if i == -1:
                if len(self.pending) <= self._HOLD:
                    return          # might be the start of a tag; wait for more
                self._emit(self.pending[:-self._HOLD])
                self.pending = self.pending[-self._HOLD:]
                return
            self._emit(self.pending[:i])
            self.pending = self.pending[i + len(self.OPEN):]
            self.muted = True

    def _emit(self, text: str) -> None:
        if not text:
            return
        if not self.wrote:
            # Models routinely open a turn with a blank line or two before a tool
            # call. Printing those pushes the output down the screen for nothing,
            # and counting them as "streamed" would suppress the real answer.
            text = text.lstrip()
            if not text:
                return
            self.wrote = True
            _streamed_recently[0] = True
            self.on_first()
        self.out.write(text)
        self.out.flush()

    def finish(self) -> None:
        if not self.muted and self.pending.strip():
            self._emit(self.pending)
        self.pending = ""
        if self.wrote:
            self.out.write("\n")
            self.out.flush()


def _make_submit(provider: HostedProvider, *, stream_to=None):
    """Adapt the provider to what `run_agent_loop` wants: prompt -> (text, cost, ms).

    Cost is always 0.0 — kimi-k3 on the public endpoint is free, which is precisely
    why every wallet and cost flag could be deleted.
    """
    def submit(prompt: str) -> tuple[str, float, int]:
        streamer = stream_to() if callable(stream_to) else None
        r = provider.serve(TurnRequest(
            prompt=prompt,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            stream_callback=(streamer.feed if streamer else None),
        ))
        if streamer:
            streamer.finish()
        return r.text, 0.0, r.latency_ms
    return submit


# --------------------------------------------------------------------------- #
# Session                                                                     #
# --------------------------------------------------------------------------- #

class Session:
    def __init__(self, console: Console, policy: PermissionPolicy,
                 provider: HostedProvider, cwd: str, sid: str,
                 max_iterations: int) -> None:
        self.console = console
        self.policy = policy
        self.provider = provider
        self.cwd = cwd
        self.sid = sid
        self.max_iterations = max_iterations
        self.turns: list[dict] = []
        self.done = False

    # -- slash commands --------------------------------------------------
    def handle(self, line: str) -> bool:
        if not line.startswith("/"):
            return False
        cmd, _, rest = line[1:].partition(" ")
        fn = getattr(self, f"cmd_{cmd}", None)
        if fn is None:
            self.console.print(f"[yellow]no such command: /{cmd}[/yellow] (try /help)")
            return True
        fn(rest.strip())
        return True

    def cmd_help(self, _: str) -> None:
        t = Table(show_header=False, box=None, pad_edge=False)
        t.add_column(style="bold cyan", no_wrap=True)
        t.add_column()
        for row in [
            ("/mode [name]", "how tool calls are approved: plan, manual, auto-edit, auto"),
            ("/swarm <task>", "split across parallel agents, review each, merge"),
            ("/sessions", "list saved sessions"),
            ("/resume [id]", "reopen a session (default: the most recent)"),
            ("/new", "start a fresh session"),
            ("/licence [key]", "show your tier, or store a Pro key"),
            ("/clear", "clear the screen and the conversation"),
            ("/exit", "leave (Ctrl+D works too)"),
        ]:
            t.add_row(*row)
        self.console.print(t)

    def cmd_exit(self, _: str) -> None:
        self.done = True

    cmd_quit = cmd_exit
    cmd_q = cmd_exit

    def cmd_clear(self, _: str) -> None:
        self.turns = []
        self.console.clear()
        self.console.print("[dim]cleared[/dim]")

    def cmd_mode(self, rest: str) -> None:
        want = rest.strip().lower()
        if not want:
            t = Table(show_header=False, box=None)
            t.add_column(style="bold", no_wrap=True)
            t.add_column(style="bold cyan", no_wrap=True)
            t.add_column()
            for name, spec in PERMISSION_MODES.items():
                t.add_row("→" if name == self.policy.mode else " ", name, spec["blurb"])
            self.console.print(t)
            return
        try:
            new = PermissionPolicy.normalize_mode(want)
        except ValueError as exc:
            self.console.print(f"[yellow]{exc}[/yellow]")
            return
        # Widening to full auto is the one direction that can cost a repository.
        if new == "auto" and self.policy.mode != "auto":
            self.console.print("[red]auto approves every tool call, including shell "
                               "and delete, with no further prompts.[/red]")
            try:
                if input("type 'auto' to confirm: ").strip().lower() != "auto":
                    self.console.print("[dim]unchanged[/dim]")
                    return
            except EOFError:
                self.console.print("[dim]unchanged[/dim]")
                return
        self.policy.mode = new
        self.console.print(f"mode: [bold cyan]{self.policy.label}[/bold cyan] "
                           f"— {self.policy.blurb}")

    def cmd_sessions(self, _: str) -> None:
        rows = _list_sessions()
        if not rows:
            self.console.print("[dim]no saved sessions yet[/dim]")
            return
        self.console.print(_sessions_table(rows))
        self.console.print("[dim]/resume <id>[/dim]")

    def cmd_resume(self, rest: str) -> None:
        sid = rest.strip() or _latest_session_id() or ""
        d = _load_session(sid) if sid else None
        if not d:
            self.console.print(f"[yellow]no session {sid or '(none saved)'}[/yellow]")
            return
        self.sid, self.turns = d["id"], d.get("turns", [])
        self.console.print(f"resumed [bold cyan]{self.sid}[/bold cyan] — "
                           f"{len(self.turns)} turn(s)")

    def cmd_new(self, _: str) -> None:
        self.sid, self.turns = _new_session_id(), []
        self.console.print(f"new session [bold cyan]{self.sid}[/bold cyan]")

    def cmd_licence(self, rest: str) -> None:
        from agent_runtime import entitlements as E
        if rest.strip():
            E.write_licence(rest.strip())
            self.console.print(f"stored in {E.licence_path()} (mode 0600)")
        ent = E.resolve()
        self.console.print(f"tier: [bold cyan]{ent.describe()}[/bold cyan]")
        if not ent.is_pro:
            self.console.print(f"[dim]{E.agent_tasks_used_today()}"
                               f"/{ent.agent_tasks_per_day} agentic requests used today "
                               f"· {E.PRICING_URL}[/dim]")

    cmd_license = cmd_licence

    def cmd_swarm(self, rest: str) -> None:
        task = rest.strip()
        if not task:
            self.console.print("[yellow]usage: /swarm <task>[/yellow]")
            return
        from agent_runtime import entitlements as E
        from agent_runtime.orchestrator import (PRO_MAX_AGENTS, max_agents_for,
                                                run_swarm)
        ent = E.resolve()
        verdict = E.check_agent_task(ent)
        if not verdict.allowed:
            self._refuse(verdict)
            return
        width = max_agents_for(ent)
        self.console.print(f"[bold]swarm[/bold] · {width} agents · "
                           f"{self.policy.label} · {self.cwd}")
        if not ent.is_pro:
            self.console.print(f"[dim]free tier runs {width}; a paid plan runs "
                               f"{PRO_MAX_AGENTS} — {E.PRICING_URL}[/dim]")

        def on_event(kind: str, f: dict) -> None:
            if kind == "phase":
                self.console.print(f"[cyan]▸ {f.get('name')}[/cyan]")
            elif kind == "plan":
                for i, st in enumerate(f.get("subtasks") or []):
                    self.console.print(f"   {i + 1}. {st}")
            elif kind == "agent_done":
                self.console.print(f"   {'✓' if f.get('completed') else '·'} "
                                   f"agent {f['index'] + 1}")
            elif kind == "agent_error":
                self.console.print(f"   [red]✗ agent {f['index'] + 1}: "
                                   f"{f.get('error')}[/red]")
            elif kind == "verdict":
                v = f.get("verdict")
                colour = "green" if v == "confirmed" else "yellow"
                self.console.print(f"   [{colour}]{v}[/{colour}] "
                                   f"· subtask {f['index'] + 1}")

        r = run_swarm(task=task, submit_turn=_make_submit(self.provider),
                      policy=self.policy, permission_prompter=_prompter(self.console),
                      cwd=self.cwd, max_agents=width,
                      max_iterations=verdict.iterations, on_event=on_event)
        E.record_agent_task()
        self.console.print()
        self.console.print(Markdown(_clean_answer(r.synthesis)
                                    or "_the swarm produced nothing_"))
        refuted = sum(1 for x in r.results if x.verdict == "refuted")
        self.console.print(f"[dim]{len(r.surviving)}/{len(r.results)} survived"
                           + (f", {refuted} refuted by review" if refuted else "")
                           + f" · {r.wall_ms / 1000:.0f}s[/dim]")

    def _refuse(self, verdict) -> None:
        self.console.print(f"[yellow]{verdict.reason}[/yellow]")
        if verdict.upgrade_hint:
            self.console.print(f"[dim]{verdict.upgrade_hint}[/dim]")

    # -- a normal turn ---------------------------------------------------
    def ask(self, text: str) -> None:
        """Every turn is agentic, operating on the directory we started in."""
        from agent_runtime import entitlements as E
        ent = E.resolve()
        verdict = E.check_agent_task(ent, requested_iterations=self.max_iterations)
        if not verdict.allowed:
            self._refuse(verdict)
            return

        self.turns.append({"role": "user", "content": text})
        history = [{"role": t["role"], "content": t["content"]}
                   for t in self.turns[:-1]]
        _streamed_recently[0] = False

        def on_iteration(turn) -> None:
            # ParsedToolCall is (name, arguments, raw) — not (tool, args).
            call = turn.tool_call
            if not call:
                return
            args = getattr(call, "arguments", None) or {}
            hint = args.get("path") or args.get("command") or args.get("pattern") or ""
            self.console.print(f"[dim]  · {call.name}"
                               f"{' ' + str(hint)[:70] if hint else ''}[/dim]")

        # A spinner that runs for the whole turn is indistinguishable from a hang at
        # this network's latency, so it exists only until the first token lands and
        # the model's own words take over.
        status = self.console.status("[dim]thinking…[/dim]", spinner="dots")
        status.start()
        stopped = {"yes": False}

        def stop_status() -> None:
            if not stopped["yes"]:
                stopped["yes"] = True
                status.stop()

        try:
            result = run_agent_loop(
                user_task=text,
                submit_turn=_make_submit(self.provider,
                                         stream_to=lambda: _ProseStreamer(stop_status)),
                policy=self.policy,
                permission_prompter=_prompter(self.console),
                on_iteration=lambda t: (stop_status(), on_iteration(t)),
                cwd=self.cwd,
                max_iterations=verdict.iterations,
                # Free path, so the iteration cap is the real bound. A cost cap
                # here would be theatre.
                max_cost=float(10 ** 9),
                initial_history=history,
            )
        except BaseException:
            # Ctrl-C, or a network failure the REPL will report and carry on from.
            # Either way this turn has no reply, and leaving the user message in
            # place would send two user messages in a row on the next one — and
            # save a half turn into the session.
            self.turns.pop()
            raise
        finally:
            stop_status()
        E.record_agent_task()

        answer = _clean_answer(result.final_text)
        # The final turn already streamed to the terminal, so re-rendering it would
        # print everything twice. Markdown is only worth it when nothing was
        # streamed — a turn that ended on a tool call, or one the model spent
        # entirely on suppressed protocol.
        if not answer:
            self.console.print("\n[yellow]no answer — try rephrasing, or ask it to "
                               "summarise what it found[/yellow]")
        elif not _streamed_recently[0]:
            self.console.print()
            self.console.print(Markdown(answer))
        self.turns.append({"role": "assistant", "content": answer})
        _save_session(self.sid, self.cwd, self.turns)
        if result.stop_reason not in ("done", "no_tool_call"):
            self.console.print(f"[dim]stopped: {result.stop_reason}[/dim]")


# --------------------------------------------------------------------------- #
# Entry point                                                                 #
# --------------------------------------------------------------------------- #

@app.callback(invoke_without_command=True)
def main(
    prompt: Optional[str] = typer.Argument(
        None, help="Ask one thing and exit. Omit for an interactive session."),
    permission_mode: str = typer.Option(
        DEFAULT_PERMISSION_MODE, "--permission-mode", "-p",
        help="plan (reads only) · manual (ask before writes, shell, delete) · "
             "auto-edit (edits apply, shell asks) · auto (everything). "
             "Switch during a session with /mode."),
    max_iterations: int = typer.Option(
        12, "--max-iterations", help="Tool-call rounds per request."),
    continue_last: bool = typer.Option(
        False, "--continue", "-c", help="Resume the most recent session."),
    session: Optional[str] = typer.Option(
        None, "--session", help="Resume a specific session id."),
    list_sessions: bool = typer.Option(
        False, "--sessions", help="List saved sessions and exit."),
) -> None:
    console = Console()

    if list_sessions:
        rows = _list_sessions()
        console.print(_sessions_table(rows) if rows else "no saved sessions")
        raise typer.Exit()

    try:
        policy = PermissionPolicy(permission_mode)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    provider = HostedProvider()
    ok, why = provider.is_available()
    if not ok:
        console.print(f"[red]the Animica network is unreachable: {why}[/red]")
        console.print("[dim]it serves kimi-k3 with no key — check your connection, "
                      "or try again shortly.[/dim]")
        raise typer.Exit(code=4)

    cwd = os.getcwd()
    s = Session(console, policy, provider, cwd, _new_session_id(), max_iterations)

    if session or continue_last:
        s.cmd_resume(session or "")

    # One shot: `animica chat "do the thing"`, optionally `… | animica chat "…"`.
    if prompt:
        piped = _read_piped_stdin()
        if piped:
            prompt = _with_piped_input(prompt, piped)
            # stdin is now spent, so `_prompter` can only answer "deny" — say so
            # rather than letting every write fail as if the model had refused.
            if policy.mode in ("manual", "auto-edit"):
                console.print("[dim]reading stdin, so approval prompts will be "
                              "declined — pass -p auto-edit or -p auto to let it "
                              "act.[/dim]")
        s.ask(prompt)
        if s.turns:
            _save_session(s.sid, s.cwd, s.turns)
        raise typer.Exit()

    _enable_line_editing()
    # Say which project file is steering it. Instructions that change how the agent
    # behaves must not be invisible — someone debugging odd behaviour should be able
    # to see that a repo's AGENTS.md is in play without reading the source.
    ctx_name, _ = load_project_context(cwd)
    lines = [f"[bold]animica[/bold] · kimi-k3 · {policy.label} mode", f"[dim]{cwd}[/dim]"]
    if ctx_name:
        lines.append(f"[dim]following {ctx_name}[/dim]")
    console.print(Panel("\n".join(lines), border_style="blue", padding=(0, 1)))
    console.print("[dim]/help for commands · Ctrl+D to exit[/dim]\n")

    while not s.done:
        try:
            line = input("› ").strip()
        except EOFError:
            console.print()
            break
        except KeyboardInterrupt:
            console.print("\n[dim]^C — /exit to leave[/dim]")
            continue
        if not line:
            continue
        if s.handle(line):
            continue
        try:
            s.ask(line)
        except KeyboardInterrupt:
            console.print("\n[dim]stopped[/dim]")
        except Exception as exc:  # noqa: BLE001 — one bad turn must not end the session
            console.print(f"[red]{type(exc).__name__}: {exc}[/red]")

    if s.turns:
        _save_session(s.sid, s.cwd, s.turns)
        console.print(f"[dim]saved as {s.sid} · `animica chat -c` continues it[/dim]")
