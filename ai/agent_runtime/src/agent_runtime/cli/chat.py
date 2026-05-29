"""`animica chat` — interactive REPL.

Behavior:

1. On start, prints wallet address + balance + AICF endpoint + the current
   provider cascade status.
2. Each user line goes through the provider cascade. Streaming responses
   are echoed live; the final turn record is appended to history.
3. Slash commands: /help /quit /balance /history /save /provider /tier
   /clear /status.
4. When `distributed-aicf` is the active provider, every turn shows a
   pre-flight cost preview the user can accept or reject (Y/n) unless
   `--yolo` was passed.
5. On Ctrl+C the REPL aborts the current turn but stays alive; Ctrl+D
   (EOF) exits cleanly.

Exit codes:
   0  clean exit
   2  config error
   3  wallet error
   4  no provider available
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Callable, Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from agent_runtime.agentic import (
    AgentTurn,
    PermissionPolicy,
    ToolSpec,
    run_agent_loop,
)
from agent_runtime.config import load_config
from agent_runtime.errors import (
    AgentRuntimeError,
    ConfigError,
    ProviderUnavailable,
    WalletError,
)
from agent_runtime.providers import ProviderCascade, TurnRequest, TurnResult
from agent_runtime.chat_persist import (
    ChatPersistError,
    Turn,
    append_exchange,
    list_threads_local,
    new_thread_id,
    read_thread,
    remember_thread,
    ThreadInfo,
    derive_namespace,
)


app = typer.Typer(
    add_completion=False,
    help="Open an interactive AICF-paid chat session with the Animica agent.",
    no_args_is_help=False,
)


# --------------------------------------------------------------------------- #
# History persistence                                                         #
# --------------------------------------------------------------------------- #

def _history_dir() -> Path:
    home = Path(os.environ.get("ANIMICA_DATA_DIR", "~/.animica")).expanduser()
    p = home / "agent_runtime" / "history"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _save_history(turns: list[dict]) -> Path:
    p = _history_dir() / f"chat-{int(time.time())}.json"
    p.write_text(json.dumps(turns, indent=2, sort_keys=False), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# Slash commands                                                              #
# --------------------------------------------------------------------------- #

class _SlashHandler:
    def __init__(self, console: Console, cascade: ProviderCascade,
                 state: dict) -> None:
        self.console = console
        self.cascade = cascade
        self.state = state

    def dispatch(self, line: str) -> bool:
        """Return True if line was a slash command (handled)."""
        if not line.startswith("/"):
            return False
        cmd, _, rest = line[1:].partition(" ")
        rest = rest.strip()
        method = getattr(self, f"_cmd_{cmd}", None)
        if method is None:
            self.console.print(f"[yellow]unknown command: /{cmd}[/yellow] "
                                f"(try /help)")
            return True
        method(rest)
        return True

    def _cmd_help(self, _: str) -> None:
        t = Table(title="Slash commands", show_header=False, box=None)
        t.add_column(style="bold cyan")
        t.add_column()
        for line in [
            ("/help",          "Show this list."),
            ("/quit",          "Exit the chat REPL."),
            ("/balance",       "Refresh and print wallet balance."),
            ("/history",       "List the turns in this session."),
            ("/save",          "Save the session transcript to "
                                "~/.animica/agent_runtime/history/."),
            ("/provider <n>",  "Force a specific provider for the next turn "
                                "(distributed-aicf | local-flagship | offline)."),
            ("/tier <id>",     "Prefer a specific tier (tiny|small|flagship|large)."),
            ("/clear",         "Clear the screen and forget history."),
            ("/status",        "Show provider cascade status."),
            ("/agent <task>",  "Run an agentic task with tool calls "
                                "(read/write/edit/bash, gated). Operates "
                                "in the current /cwd — point it at a repo "
                                "(e.g. /cwd /root/animica/apps/animica-chat) "
                                "to have the agent edit that codebase."),
            ("/cwd [path]",    "Show or set the agent's working directory. "
                                "With no argument, prints the current cwd; "
                                "with a path, changes where /agent runs."),
            ("/tiers",         "Show available tiers, the models behind "
                                "each, and which one your next turn will "
                                "use."),
            ("/threads",       "List your saved chat threads (from the DA "
                                "layer + local index)."),
            ("/resume <id>",   "Resume an existing chat thread by id; "
                                "fetches transcript from the DA layer."),
            ("/new",           "Start a fresh chat thread, abandoning the "
                                "current one (it stays in DA / resumable)."),
            ("/decode <m>",    "Set the pipeline decode mode for the next "
                                "turn (streaming | prefill_only). 'streaming' "
                                "routes every decoded token through every "
                                "pipeline stage; 'prefill_only' runs the "
                                "legacy single-worker decode."),
        ]:
            t.add_row(*line)
        self.console.print(t)

    def _cmd_quit(self, _: str) -> None:
        self.state["exit"] = True

    def _cmd_balance(self, _: str) -> None:
        # Force a re-load by clearing the cached wallet info on the
        # distributed-aicf provider, then re-asking is_available.
        for p in self.cascade._providers:    # type: ignore[attr-defined]
            if hasattr(p, "_wallet"):
                p._wallet = None              # type: ignore[attr-defined]
        for row in self.cascade.provider_status():
            self.console.print(
                f"  {row['name']:<20}  {row['available']:<3}  {row['reason']}",
            )

    def _cmd_history(self, _: str) -> None:
        turns: list[TurnResult] = self.state.get("turns", [])
        if not turns:
            self.console.print("[dim]no turns yet[/dim]")
            return
        for i, t in enumerate(turns, 1):
            self.console.print(
                f"  [{i}] {t.provider:<18} tier={t.tier or '-':<8} "
                f"cost={t.cost_animica:.6f} latency={t.latency_ms}ms",
            )

    def _cmd_save(self, _: str) -> None:
        turns: list[TurnResult] = self.state.get("turns", [])
        records = [
            {
                "user": e["user"],
                "assistant": e["assistant"],
                "provider": e["meta"]["provider"],
                "tier": e["meta"]["tier"],
                "cost_animica": e["meta"]["cost_animica"],
                "latency_ms": e["meta"]["latency_ms"],
                "fallback_reasons": e["meta"]["fallback_reasons"],
            }
            for e in self.state.get("transcript", [])
        ]
        path = _save_history(records)
        self.console.print(f"[green]saved {len(records)} turn(s) to {path}[/green]")

    def _cmd_provider(self, rest: str) -> None:
        if rest not in {"distributed-aicf", "local-flagship", "offline"}:
            self.console.print(
                "[yellow]provider must be one of "
                "distributed-aicf / local-flagship / offline[/yellow]",
            )
            return
        self.state["require_provider"] = rest
        self.console.print(f"[cyan]forced provider for next turn: {rest}[/cyan]")

    def _cmd_tier(self, rest: str) -> None:
        if not rest:
            self.state["tier_preferred"] = None
            self.console.print("[cyan]tier preference cleared[/cyan]")
            return
        self.state["tier_preferred"] = rest
        self.console.print(f"[cyan]tier preference: {rest}[/cyan]")

    def _cmd_clear(self, _: str) -> None:
        self.state["turns"] = []
        self.state["transcript"] = []
        self.console.clear()

    def _cmd_status(self, _: str) -> None:
        for row in self.cascade.provider_status():
            color = "green" if row["available"] == "yes" else "red"
            self.console.print(
                f"  [{color}]{row['name']:<20} {row['available']:<3}[/{color}] "
                f"{row['reason']}",
            )

    def _cmd_tiers(self, _: str) -> None:
        """Show available tiers, model picks per tier, and what the next
        turn will use. Combines local model auto-detect with whatever the
        provider cascade can report about the network."""
        # Local detect: what models the miner-side code would pick.
        try:
            from animica.stratum_pool.aicf_inference import (
                discover_models, resolve_tier_model, _TIER_RANGES,
            )
            tiers = [t for t, _, _ in _TIER_RANGES]
            picks = {t: resolve_tier_model(t) for t in tiers}
            discovered = discover_models()
        except Exception as exc:
            self.console.print(
                f"[yellow]could not load local tier ladder: {exc}[/yellow]"
            )
            tiers, picks, discovered = [], {}, []

        # Network view: cascade providers (distributed-aicf / local-flagship
        # / offline). Reflects what your next turn would route through.
        self.console.print("[bold]Providers available to your chat:[/bold]")
        for row in self.cascade.provider_status():
            color = "green" if row["available"] == "yes" else "red"
            self.console.print(
                f"  [{color}]{row['name']:<20} {row['available']:<3}[/{color}] "
                f"{row['reason']}",
            )

        if tiers:
            self.console.print("")
            self.console.print(
                "[bold]Tiers and the model picked per tier "
                "(set with [cyan]/tier <id>[/cyan]):[/bold]"
            )
            preferred = self.state.get("tier_preferred")
            for t in tiers:
                marker = " [cyan](preferred for next turn)[/cyan]" if t == preferred else ""
                self.console.print(f"  [bold]{t:<9s}[/bold] -> {picks[t]}{marker}")

        if discovered:
            self.console.print("")
            self.console.print(
                f"[dim]auto-detected from local cache / bundles "
                f"({len(discovered)} entries; "
                f"`animica miner models` for full list):[/dim]"
            )
            for m in discovered[:6]:
                sz = f"{m.approx_billions:>5.1f}B" if m.approx_billions else "    ?"
                self.console.print(f"  [dim]{m.source:<14s} {sz}  {m.identifier}[/dim]")
            if len(discovered) > 6:
                self.console.print(f"  [dim]... and {len(discovered) - 6} more[/dim]")

    def _cmd_threads(self, _: str) -> None:
        wallet_addr = self.state.get("wallet_address") or ""
        if not wallet_addr:
            self.console.print(
                "[yellow]no wallet address detected; threads are keyed on "
                "your wallet so they can't be listed without one[/yellow]"
            )
            return
        entries = list_threads_local(wallet_addr)
        if not entries:
            self.console.print("[dim]no saved threads yet[/dim]")
            return
        t = Table(title="Your chat threads", show_header=True, box=None)
        t.add_column("id", style="bold cyan")
        t.add_column("title")
        t.add_column("turns", justify="right")
        t.add_column("last seen", style="dim")
        from datetime import datetime
        active = self.state.get("thread_id")
        for e in entries[:20]:
            mark = "→ " if e.thread_id == active else "  "
            last = datetime.fromtimestamp(e.last_ts).strftime("%Y-%m-%d %H:%M") if e.last_ts else "-"
            t.add_row(f"{mark}{e.thread_id}", e.title or "(untitled)",
                      str(e.turn_count), last)
        self.console.print(t)
        if len(entries) > 20:
            self.console.print(f"[dim]... and {len(entries) - 20} more[/dim]")

    def _cmd_resume(self, rest: str) -> None:
        thread_id = rest.strip()
        if not thread_id:
            self.console.print("[yellow]usage: /resume <thread-id>[/yellow]")
            return
        wallet_addr = self.state.get("wallet_address") or ""
        if not wallet_addr:
            self.console.print("[yellow]no wallet address; cannot resume[/yellow]")
            return
        rpc_url = self.state.get("rpc_url") or ""
        try:
            turns = read_thread(rpc_url, wallet_addr, thread_id)
        except ChatPersistError as exc:
            self.console.print(f"[red]could not read thread: {exc}[/red]")
            return
        if not turns:
            self.console.print(
                f"[yellow]thread {thread_id!r} has no turns on the DA layer; "
                f"starting fresh under that id[/yellow]"
            )
        self.state["thread_id"] = thread_id
        self.state["thread_sequence"] = (
            max((t.sequence for t in turns), default=-1) + 1
        )
        # Rebuild the rolling history that's passed to the next provider call.
        history: list[dict] = self.state.setdefault("history", [])
        history.clear()
        for t in turns:
            history.append({"role": t.role, "content": t.content})
        self.console.print(
            f"[green]resumed thread {thread_id} — {len(turns)} turn(s) "
            f"loaded[/green]"
        )
        # Replay a short tail so the user sees where they left off.
        for t in turns[-6:]:
            label = "you" if t.role == "user" else "assistant"
            self.console.print(f"  [dim]{label}>[/dim] {t.content[:200]}")
        # Cache in the local index so /threads keeps showing it.
        remember_thread(wallet_addr, ThreadInfo(
            thread_id=thread_id,
            wallet_address=wallet_addr,
            namespace=derive_namespace(wallet_addr, thread_id),
            title=(turns[0].content[:60].strip() if turns else ""),
            first_ts=turns[0].ts if turns else 0,
            last_ts=turns[-1].ts if turns else 0,
            turn_count=len(turns),
        ))

    def _cmd_new(self, _: str) -> None:
        tid = new_thread_id()
        self.state["thread_id"] = tid
        self.state["thread_sequence"] = 0
        history = self.state.setdefault("history", [])
        history.clear()
        self.console.print(f"[cyan]started new thread: {tid}[/cyan]")

    def _cmd_decode(self, rest: str) -> None:
        rest = (rest or "").strip().lower()
        if not rest:
            current = self.state.get("decode_mode") or "streaming"
            self.console.print(
                f"[cyan]current decode mode: {current}[/cyan]\n"
                f"[dim]set with /decode streaming  or  /decode prefill_only[/dim]"
            )
            return
        valid = {"streaming", "prefill_only", "prefill"}
        if rest not in valid:
            self.console.print(
                f"[yellow]unknown decode mode: {rest!r} "
                f"(valid: streaming | prefill_only)[/yellow]"
            )
            return
        # Normalise the legacy shorthand "prefill" → "prefill_only".
        mode = "prefill_only" if rest == "prefill" else rest
        self.state["decode_mode"] = mode
        self.console.print(f"[cyan]decode mode set to: {mode}[/cyan]")

    def _cmd_cwd(self, rest: str) -> None:
        """Show or set the agent's working directory.

        `/cwd` with no argument prints the current path. `/cwd <path>` switches
        the agent's working directory so subsequent /agent invocations operate
        on that codebase. Tilde expansion and relative paths are honored.
        """
        target = rest.strip()
        if not target:
            self.console.print(
                f"[cyan]agent cwd:[/cyan] "
                f"{self.state.get('agent_cwd') or os.getcwd()}"
            )
            return
        resolved = os.path.abspath(os.path.expanduser(target))
        if not os.path.isdir(resolved):
            self.console.print(f"[red]not a directory: {resolved}[/red]")
            return
        self.state["agent_cwd"] = resolved
        self.console.print(f"[green]agent cwd set to:[/green] {resolved}")

    def _cmd_agent(self, rest: str) -> None:
        """Run a single agentic task: `/agent <task description>`.

        The model can call read_file / write_file / edit_file / bash etc.
        Non-safe tools prompt for confirmation per call unless --yolo-tools
        is set on the chat session.
        """
        task = rest.strip()
        if not task:
            self.console.print(
                "[yellow]usage: /agent <task description>[/yellow]"
            )
            return
        policy: PermissionPolicy = self.state.get("agent_policy") or PermissionPolicy()
        max_iters = int(self.state.get("agent_max_iters") or 10)
        max_cost = float(self.state.get("agent_max_cost") or 0.5)
        cwd = self.state.get("agent_cwd") or os.getcwd()
        run_one_agent_task(
            console=self.console,
            cascade=self.cascade,
            task=task,
            policy=policy,
            max_iterations=max_iters,
            max_cost=max_cost,
            cwd=cwd,
            tier_preferred=self.state.get("tier_preferred"),
        )


# --------------------------------------------------------------------------- #
# Agentic driver                                                              #
# --------------------------------------------------------------------------- #


def _permission_prompter(console: Console):
    """Build a prompter that asks the user about non-safe tool calls."""
    def _prompt(tool: ToolSpec, args: dict) -> str:
        console.print(
            f"\n[yellow]⚠ tool {tool.name} requires permission[/yellow]"
        )
        # Pretty-print the arguments — but truncate long content fields
        # so the prompt fits a terminal.
        pretty = {}
        for k, v in args.items():
            sv = str(v)
            if len(sv) > 400:
                sv = sv[:400] + f"... (+{len(sv)-400} more)"
            pretty[k] = sv
        for k, v in pretty.items():
            console.print(f"  [bold]{k}:[/bold] {v}")
        try:
            ans = input("Allow? [y]es / [s]ession / [n]o: ").strip().lower()
        except EOFError:
            return "deny"
        if ans in {"y", "yes"}:
            return "allow"
        if ans in {"s", "session"}:
            return "allow_session"
        return "deny"
    return _prompt


def _build_agent_submit(
    cascade: ProviderCascade,
    *,
    tier_preferred: Optional[str],
    yolo: bool,
    console: Optional[Console] = None,
    max_output_tokens: int = 256,
) -> Callable[[str], tuple[str, float, int]]:
    """Wrap ProviderCascade.serve so the agent loop sees a tiny callable.

    Tool-call turns only need ~50-120 tokens (one [TOOL_CALL] block plus a
    rationale line), so we cap output well below the chat default. On CPU
    miners this is the difference between sub-30s per turn and the 300s
    chat timeout firing mid-generation.
    """
    def _submit(prompt: str) -> tuple[str, float, int]:
        req = TurnRequest(
            prompt=prompt,
            tier_preferred=tier_preferred,
            history=[],          # the agent already serialized history into prompt
            yolo=True,           # agent steps must not block on cost prompts
            max_output_tokens=max_output_tokens,
        )
        result: TurnResult = cascade.serve(req)
        if console is not None:
            # Surface provider routing + fallback chain so the agent driver
            # can show WHY a step was answered by offline-stub vs the real
            # remote miner. Without this the agent's log shows zero-cost
            # offline answers with no obvious explanation.
            if result.provider != "distributed-aicf" or result.fallback_reasons:
                console.print(
                    f"  [dim]→ routed via {result.provider} (tier={result.tier or '-'})"
                    + (f"; fallbacks: {'; '.join(result.fallback_reasons)}"
                       if result.fallback_reasons else "")
                    + "[/dim]"
                )
        return result.text, float(result.cost_animica or 0.0), int(result.latency_ms or 0)
    return _submit


def run_one_agent_task(
    *,
    console: Console,
    cascade: ProviderCascade,
    task: str,
    policy: PermissionPolicy,
    max_iterations: int,
    max_cost: float,
    cwd: str,
    tier_preferred: Optional[str] = None,
) -> None:
    """Run a single agent task to completion; print a per-iteration trace."""
    console.print(
        f"\n[bold cyan]── agentic task ──[/bold cyan] {task}\n"
        f"[dim]cwd={cwd}  max_iterations={max_iterations}  "
        f"max_cost={max_cost} ANIMICA[/dim]"
    )

    def _on_iteration(turn: AgentTurn) -> None:
        header = f"[bold blue][{turn.iteration}][/bold blue] "
        if turn.tool_call is not None:
            args_inline = json.dumps(
                turn.tool_call.arguments, default=str
            )
            if len(args_inline) > 200:
                args_inline = args_inline[:200] + f"... (+{len(args_inline)-200})"
            console.print(
                f"{header}[magenta]tool:[/magenta] {turn.tool_call.name} "
                f"{args_inline}",
            )
            if turn.tool_result is not None:
                # Print first 400 chars of result so the trace is useful but
                # not overwhelming.
                preview = turn.tool_result
                if len(preview) > 400:
                    preview = preview[:400] + f"\n  ... (+{len(turn.tool_result)-400} more)"
                console.print(
                    "  [dim]result:[/dim]\n" +
                    "\n".join("  " + ln for ln in preview.splitlines())
                )
        else:
            preview = turn.assistant_text.strip()
            if len(preview) > 800:
                preview = preview[:800] + f"\n... (+{len(turn.assistant_text)-800} more)"
            console.print(f"{header}[green]final answer:[/green] {preview}")
        console.print(
            f"  [dim]cost={turn.cost_animica:.6f} ANIMICA  "
            f"latency={turn.latency_ms}ms[/dim]"
        )

    submit = _build_agent_submit(
        cascade, tier_preferred=tier_preferred, yolo=True, console=console,
    )
    prompter = _permission_prompter(console)
    try:
        result = run_agent_loop(
            user_task=task,
            submit_turn=submit,
            policy=policy,
            permission_prompter=prompter,
            on_iteration=_on_iteration,
            cwd=cwd,
            max_iterations=max_iterations,
            max_cost=max_cost,
        )
    except KeyboardInterrupt:
        console.print("\n[yellow](agent interrupted by user)[/yellow]")
        return
    except Exception as exc:  # noqa: BLE001
        console.print(f"\n[red]agent error: {exc}[/red]")
        return

    color = "green" if result.completed else "yellow"
    console.print(
        f"\n[bold {color}]── agentic task finished "
        f"({result.stop_reason}, {len(result.turns)} step(s), "
        f"total cost {result.total_cost:.6f} ANIMICA) ──[/bold {color}]"
    )
    if result.final_text:
        console.print(result.final_text)


# --------------------------------------------------------------------------- #
# REPL                                                                       #
# --------------------------------------------------------------------------- #

def _print_banner(console: Console, cascade: ProviderCascade,
                  endpoint: str, *, decode_mode: str = "streaming") -> None:
    rows = cascade.provider_status()
    primary = next((r for r in rows if r["available"] == "yes"), None)
    primary_name = primary["name"] if primary else "none"
    body_lines = [
        f"endpoint:  {endpoint}",
        f"primary:   {primary_name}",
        f"decode:    {decode_mode}  (toggle with /decode)",
        "",
    ]
    for r in rows:
        mark = "[green]✓[/green]" if r["available"] == "yes" else "[red]✗[/red]"
        body_lines.append(f"  {mark}  {r['name']:<20}  {r['reason']}")
    panel = Panel("\n".join(body_lines),
                  title="animica chat", border_style="cyan")
    console.print(panel)
    console.print("type /help for commands; Ctrl+D to exit.\n")


def _confirm_cost(console: Console, prompt: str) -> bool:
    """Read y/n confirmation for paid turns."""
    console.print(prompt, end="")
    try:
        ans = input(" [Y/n] ").strip().lower()
    except EOFError:
        return False
    return ans in {"", "y", "yes"}


def _run_repl(cfg, rpc_url: str, wallet_path: Optional[str],
              yolo: bool, console: Console,
              wallet_label: Optional[str] = None,
              *,
              agent_policy: Optional[PermissionPolicy] = None,
              agent_max_iters: int = 10,
              agent_max_cost: float = 0.5,
              agent_cwd: Optional[str] = None,
              resume_thread_id: Optional[str] = None,
              decode_mode_default: str = "streaming") -> int:
    try:
        cascade = ProviderCascade(cfg, rpc_url=rpc_url,
                                  wallet_path=wallet_path,
                                  wallet_label=wallet_label)
    except (ConfigError, WalletError) as exc:
        console.print(f"[red]{exc.render()}[/red]")
        return 2

    _print_banner(console, cascade, rpc_url, decode_mode=decode_mode_default)

    # Best-effort: pull the wallet address from the distributed-aicf
    # provider so we can persist turns under it. Falls back to "" which
    # disables persistence (chat still works, just no DA write / resume).
    wallet_address = ""
    try:
        for p in cascade._providers:  # type: ignore[attr-defined]
            if p.name == "distributed-aicf":
                wi = p._wallet_info()  # type: ignore[attr-defined]
                wallet_address = wi.address or ""
                break
    except Exception:    # noqa: BLE001 — persistence is best-effort
        wallet_address = ""

    thread_id = resume_thread_id or new_thread_id()
    state: dict = {
        "turns": [],
        "transcript": [],
        "exit": False,
        "require_provider": None,
        "tier_preferred":
            cfg.model_catalog["routing"].get("default_tier", "small"),
        "agent_policy": agent_policy or PermissionPolicy(),
        "agent_max_iters": agent_max_iters,
        "agent_max_cost": agent_max_cost,
        "agent_cwd": agent_cwd or os.getcwd(),
        # Persistence keys read by the /threads /resume /new commands and
        # by the post-turn writer below.
        "wallet_address": wallet_address,
        "rpc_url": rpc_url,
        "thread_id": thread_id,
        "thread_sequence": 0,
        "history": [],
        # Pipeline decode-mode for this chat session. Defaults to
        # "streaming" so each new token round-trips through every
        # pipeline stage (per-token KV-cache parallelism, animica >=
        # 0.1.67). Set with the --decode-mode CLI flag, /decode
        # slash command, or ANIMICA_AICF_CHAT_DECODE_MODE env.
        "decode_mode": decode_mode_default,
    }
    slash = _SlashHandler(console, cascade, state)
    history: list[dict[str, str]] = state["history"]

    # If the user asked to resume a thread, hydrate it from DA before the
    # first prompt so the model sees the conversation so far.
    if resume_thread_id and wallet_address:
        try:
            prior = read_thread(rpc_url, wallet_address, resume_thread_id)
        except ChatPersistError as exc:
            console.print(f"[yellow]could not load thread {resume_thread_id}: {exc}[/yellow]")
            prior = []
        if prior:
            for t in prior:
                history.append({"role": t.role, "content": t.content})
            state["thread_sequence"] = prior[-1].sequence + 1
            console.print(
                f"[green]resumed thread {resume_thread_id} — "
                f"{len(prior)} prior turn(s) loaded[/green]"
            )
            for t in prior[-4:]:
                label = "you" if t.role == "user" else "assistant"
                console.print(f"  [dim]{label}>[/dim] {t.content[:200]}")
        else:
            console.print(
                f"[dim]no prior turns under thread {resume_thread_id}; "
                f"starting fresh under that id[/dim]"
            )
    elif wallet_address:
        console.print(
            f"[dim]thread {thread_id} (new); turns will persist to DA. "
            f"`/threads` to list, `/resume <id>` to switch, `/new` for a "
            f"fresh thread.[/dim]"
        )

    try:
        while not state["exit"]:
            try:
                line = input("you> ").rstrip("\n")
            except EOFError:
                console.print()
                break
            except KeyboardInterrupt:
                console.print("\n[dim](interrupted; next prompt)[/dim]")
                continue
            if not line.strip():
                continue
            if slash.dispatch(line):
                continue

            require_provider = state["require_provider"]
            state["require_provider"] = None
            req = TurnRequest(
                prompt=line,
                tier_preferred=state["tier_preferred"],
                history=history,
                require_provider=require_provider,
                yolo=yolo,
                decode_mode=state.get("decode_mode") or None,
            )

            # Pre-flight: when paying via distributed-aicf, show cost.
            if (not yolo and require_provider in (None, "distributed-aicf")
                and any(p.name == "distributed-aicf" and p.is_available()[0]
                        for p in cascade._providers)):       # type: ignore
                try:
                    # is_available already prepared the wallet; pull a fresh quote.
                    dist = next(p for p in cascade._providers
                                if p.name == "distributed-aicf")
                    quote = dist.client.estimate_cost(   # type: ignore[attr-defined]
                        __import__("agent_runtime.aicf_client", fromlist=[""])
                        .JobSpec(prompt=line,
                                 tier_preferred=state["tier_preferred"]))
                    wallet = dist._wallet_info()         # type: ignore[attr-defined]
                    if not _confirm_cost(
                        console,
                        f"[dim]quote: ~{quote.estimated_cost_animica:.6f} ANIMICA "
                        f"(balance {wallet.balance_animica:.6f}); proceed?",
                    ):
                        console.print("[dim]turn cancelled[/dim]")
                        continue
                except Exception:    # noqa: BLE001 — preview is best-effort
                    pass

            # Stream the response.
            console.print("[bold]assistant>[/bold] ", end="")
            text_buf: list[str] = []

            def relay(chunk: str, is_final: bool) -> None:
                text_buf.append(chunk)
                console.out(chunk, end="", style="white")
                if is_final:
                    console.print()

            req.stream_callback = relay
            try:
                result: TurnResult = cascade.serve(req)
            except ProviderUnavailable as exc:
                console.print(f"\n[red]provider unavailable: {exc.reason}[/red]")
                continue
            except AgentRuntimeError as exc:
                console.print(f"\n[red]{exc.render()}[/red]")
                continue
            if not text_buf:    # provider did not stream; print final once.
                console.print(result.text)
            # Append to history.
            history.append({"role": "user", "content": line})
            history.append({"role": "assistant", "content": result.text})
            state["turns"].append(result)

            # Persist the exchange to DA so any client owned by the same
            # wallet (CLI, studio.animica.org, mobile) can resume the
            # thread later. Best-effort: a persistence failure logs a
            # one-liner and the chat keeps going — the user already paid
            # for the inference.
            persist_addr = state.get("wallet_address") or ""
            if persist_addr:
                try:
                    seq = int(state.get("thread_sequence", 0))
                    append_exchange(
                        rpc_url,
                        wallet_address=persist_addr,
                        thread_id=state["thread_id"],
                        user_text=line,
                        assistant_text=result.text,
                        next_sequence=seq,
                        provider=result.provider,
                        tier=result.tier,
                        cost_animica=result.cost_animica,
                        latency_ms=result.latency_ms,
                    )
                    state["thread_sequence"] = seq + 2
                except ChatPersistError as exc:
                    console.print(
                        f"  [yellow]thread persist failed: {exc}[/yellow]"
                    )
            state["transcript"].append({
                "user": line, "assistant": result.text,
                "meta": {
                    "provider": result.provider,
                    "tier": result.tier,
                    "cost_animica": result.cost_animica,
                    "latency_ms": result.latency_ms,
                    "fallback_reasons": result.fallback_reasons,
                    "effective_mode": result.effective_mode,
                },
            })
            # Per-turn footer line.
            footer = (
                f"  [dim]provider={result.provider}  tier={result.tier or '-'}"
                f"  cost={result.cost_animica:.6f} ANIMICA"
                f"  latency={result.latency_ms}ms[/dim]"
            )
            # Surface the node's actual routing decision (race vs N-stage
            # pipeline) + the decode mode so users see when streaming
            # parallelism kicked in vs when the node fell back to race
            # because not enough pipeline workers were registered.
            routing_mode = (result.metadata or {}).get("routing_mode") or ""
            stages_n = int((result.metadata or {}).get("pipeline_stages") or 0)
            decode_mode_used = (result.metadata or {}).get("decode_mode") or ""
            if routing_mode:
                if routing_mode == "pipeline" and stages_n > 0:
                    footer += (
                        f"  [dim]routing=pipeline×{stages_n}"
                        f" decode={decode_mode_used or '-'}[/dim]"
                    )
                elif routing_mode == "race":
                    footer += "  [dim]routing=race[/dim]"
                else:
                    footer += f"  [dim]routing={routing_mode}[/dim]"
            if result.fallback_reasons:
                footer += (f"  [yellow]fallbacks="
                            f"{'; '.join(result.fallback_reasons)}[/yellow]")
            console.print(footer)
    finally:
        cascade.close()
    return 0


# --------------------------------------------------------------------------- #
# Typer entrypoint                                                            #
# --------------------------------------------------------------------------- #

@app.callback(invoke_without_command=True)
def main(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc-url",
        help="Override AICF endpoint URL (default: from integration.yaml + "
              "active network).",
    ),
    wallet: Optional[str] = typer.Option(
        None, "--wallet",
        help="Wallet file path, label, or bech32 address. When not a file, "
              "the value is looked up in ~/.animica/wallets.json by label / "
              "address / public_key_hex. Default: bundle's selected wallet, "
              "then pinned wallet under ~/.animica/wallets/.",
    ),
    wallet_label: Optional[str] = typer.Option(
        None, "--wallet-label",
        help="Label of the wallet to use inside a v2 bundle (e.g. 'hot'). "
              "Defaults to the bundle's 'default', else the first entry.",
    ),
    yolo: bool = typer.Option(
        False, "--yolo",
        help="Skip per-turn cost confirmation; submit jobs immediately.",
    ),
    require_distributed: bool = typer.Option(
        False, "--require-distributed",
        help="Refuse to fall back to local-flagship or offline.",
    ),
    decode_mode: str = typer.Option(
        "streaming", "--decode-mode",
        help="Pipeline decode mode for this chat session. 'streaming' "
              "(default) routes each decoded token through every "
              "pipeline stage with per-stage KV-cache state. "
              "'prefill_only' runs the legacy prefill-parallel path "
              "(final stage decodes locally). Toggle in-session via "
              "the /decode slash command.",
    ),
    # ---- Agentic mode flags ----
    agentic: Optional[str] = typer.Option(
        None, "--agentic",
        help="Run a single agentic task (Claude Code style: read/write/edit/"
              "bash/grep tools, gated). Pass the task description as the "
              "argument; exits after the task completes.",
    ),
    yolo_tools: bool = typer.Option(
        False, "--yolo-tools",
        help="Auto-allow every tool call in agentic mode (CAREFUL — the "
              "model can run rm -rf or overwrite files unattended).",
    ),
    read_only: bool = typer.Option(
        False, "--read-only",
        help="In agentic mode, refuse all non-safe tools (write/edit/bash/...).",
    ),
    max_iterations: int = typer.Option(
        10, "--max-iterations",
        help="Agentic loop iteration cap.",
    ),
    max_cost: float = typer.Option(
        0.5, "--max-cost",
        help="Agentic loop cost cap in ANIMICA. Loop stops once exceeded.",
    ),
    cwd: Optional[str] = typer.Option(
        None, "--cwd",
        help="Working directory the agent sees (default: current directory).",
    ),
    # ---- Chat persistence flags ----
    thread: Optional[str] = typer.Option(
        None, "--thread",
        help="Resume the given chat thread by id. The transcript is "
              "fetched from the DA layer (namespace derived from your "
              "wallet + this id) and replayed before the first prompt. "
              "Without this flag, a fresh thread id is generated.",
    ),
    list_threads: bool = typer.Option(
        False, "--list-threads",
        help="Print the locally-cached list of your saved threads and exit. "
              "(Each turn is persisted to DA, so any client owned by the "
              "same wallet can resume — this list is just the resume picker.)",
    ),
) -> None:
    """Interactive AICF-paid chat REPL."""
    console = Console()
    try:
        cfg = load_config()
    except ConfigError as exc:
        console.print(f"[red]{exc.render()}[/red]")
        raise typer.Exit(code=2)

    network = os.environ.get("ANIMICA_NETWORK") or _resolve_active_network()
    endpoint_map = cfg.integration["aicf"]["endpoint"]
    endpoint = rpc_url or endpoint_map.get(network) or endpoint_map.get(
        "mainnet")
    if not endpoint:
        console.print(
            "[red]no AICF endpoint configured for network "
            f"{network!r}[/red]")
        raise typer.Exit(code=2)

    # User-friendly fallback: if the configured endpoint is the local
    # node (the integration default) and there's no local node listening,
    # auto-redirect to the public mainnet RPC so a fresh `pip install
    # animica` + `animica chat` works with zero setup. An explicit
    # --rpc-url or ANIMICA_RPC_URL override always wins.
    if (not rpc_url
            and not os.environ.get("ANIMICA_RPC_URL")
            and network == "mainnet"
            and ("127.0.0.1" in endpoint or "localhost" in endpoint)):
        if not _endpoint_reachable(endpoint):
            public = "https://rpc.animica.org/rpc"
            console.print(
                f"[dim]local node at {endpoint} not reachable — "
                f"falling back to public RPC {public}[/dim]"
            )
            endpoint = public

    if require_distributed:
        os.environ["ANIMICA_CHAT_REQUIRE_DISTRIBUTED"] = "1"
    if yolo:
        os.environ["ANIMICA_CHAT_YOLO"] = "1"

    # One-shot agentic mode: run the task, print transcript, exit.
    if agentic:
        try:
            cascade = ProviderCascade(cfg, rpc_url=endpoint,
                                      wallet_path=wallet,
                                      wallet_label=wallet_label)
        except (ConfigError, WalletError) as exc:
            console.print(f"[red]{exc.render()}[/red]")
            raise typer.Exit(code=2)
        policy = PermissionPolicy(yolo=yolo_tools, read_only=read_only)
        run_one_agent_task(
            console=console,
            cascade=cascade,
            task=agentic,
            policy=policy,
            max_iterations=max_iterations,
            max_cost=max_cost,
            cwd=cwd or os.getcwd(),
            tier_preferred=cfg.model_catalog["routing"].get(
                "default_tier", "small"
            ),
        )
        cascade.close()
        raise typer.Exit(code=0)

    # `--list-threads` is a one-shot — print and exit, no REPL.
    if list_threads:
        # Resolve the wallet address the same way _run_repl does (cheaper
        # than spinning up the full cascade — we just need an address).
        addr = ""
        try:
            from agent_runtime.wallet import load_wallet_info
            wi = load_wallet_info(
                wallet_path=wallet, rpc_url=endpoint,
                network=network, wallet_label=wallet_label,
            )
            addr = wi.address or ""
        except Exception as exc:    # noqa: BLE001
            console.print(f"[yellow]could not load wallet: {exc}[/yellow]")
        if not addr:
            console.print("[red]no wallet address; cannot list threads[/red]")
            raise typer.Exit(code=3)
        entries = list_threads_local(addr)
        if not entries:
            console.print("[dim]no saved threads for this wallet[/dim]")
            raise typer.Exit(code=0)
        t = Table(title=f"Chat threads for {addr[:14]}…", show_header=True, box=None)
        t.add_column("id", style="bold cyan")
        t.add_column("title")
        t.add_column("turns", justify="right")
        t.add_column("last seen", style="dim")
        from datetime import datetime
        for e in entries[:50]:
            last = datetime.fromtimestamp(e.last_ts).strftime("%Y-%m-%d %H:%M") if e.last_ts else "-"
            t.add_row(e.thread_id, e.title or "(untitled)", str(e.turn_count), last)
        console.print(t)
        console.print(
            f"[dim]resume any of these with `animica chat --thread <id>`[/dim]"
        )
        raise typer.Exit(code=0)

    # Normalise CLI shorthand: "prefill" → "prefill_only", everything
    # else trusted as-is so future modes (e.g. "speculative") don't
    # require a CLI patch.
    decode_default = (decode_mode or "streaming").strip().lower()
    if decode_default == "prefill":
        decode_default = "prefill_only"
    rc = _run_repl(
        cfg, rpc_url=endpoint, wallet_path=wallet,
        yolo=yolo, console=console, wallet_label=wallet_label,
        agent_policy=PermissionPolicy(yolo=yolo_tools, read_only=read_only),
        agent_max_iters=max_iterations, agent_max_cost=max_cost,
        agent_cwd=cwd or os.getcwd(),
        resume_thread_id=thread,
        decode_mode_default=decode_default,
    )
    raise typer.Exit(code=rc)


def _endpoint_reachable(url: str, timeout_sec: float = 1.5) -> bool:
    """Cheap liveness probe used to decide whether to fall back to the
    public RPC when the configured local node isn't running. We post a
    tiny chain.getHead RPC and accept any well-formed JSON-RPC reply
    (success OR a structured error) as 'reachable'. Anything else —
    refused TCP, timeout, HTML 4xx/5xx page — is treated as unreachable.
    """
    import json as _json
    import urllib.request
    import urllib.error
    body = _json.dumps({"jsonrpc": "2.0", "id": 0,
                         "method": "chain.getHead", "params": {}}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
                                  headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            ct = (resp.headers.get("content-type") or "").lower()
            if "application/json" not in ct:
                return False
            payload = _json.loads(resp.read().decode("utf-8"))
            return isinstance(payload, dict) and (
                "result" in payload or "error" in payload
            )
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def _resolve_active_network() -> str:
    """Find the active network via the existing animica CLI state, with
    safe fallback to mainnet. No regression on chain behavior."""
    try:
        from animica.cli.state import get_cli_state    # type: ignore
        state = get_cli_state()
        return str(state.get("active_network") or "mainnet")
    except Exception:    # noqa: BLE001
        return os.environ.get("ANIMICA_NETWORK", "mainnet")


# Console-script entrypoint (used by pyproject.toml::project.scripts).
def main_console() -> None:    # pragma: no cover — thin wrapper
    app()


# Module-execution entrypoint: `python -m agent_runtime.cli.chat`.
if __name__ == "__main__":
    app()
