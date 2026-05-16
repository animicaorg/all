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
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from agent_runtime.config import load_config
from agent_runtime.errors import (
    AgentRuntimeError,
    ConfigError,
    ProviderUnavailable,
    WalletError,
)
from agent_runtime.providers import ProviderCascade, TurnRequest, TurnResult


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


# --------------------------------------------------------------------------- #
# REPL                                                                       #
# --------------------------------------------------------------------------- #

def _print_banner(console: Console, cascade: ProviderCascade,
                  endpoint: str) -> None:
    rows = cascade.provider_status()
    primary = next((r for r in rows if r["available"] == "yes"), None)
    primary_name = primary["name"] if primary else "none"
    body_lines = [
        f"endpoint:  {endpoint}",
        f"primary:   {primary_name}",
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
              wallet_label: Optional[str] = None) -> int:
    try:
        cascade = ProviderCascade(cfg, rpc_url=rpc_url,
                                  wallet_path=wallet_path,
                                  wallet_label=wallet_label)
    except (ConfigError, WalletError) as exc:
        console.print(f"[red]{exc.render()}[/red]")
        return 2

    _print_banner(console, cascade, rpc_url)
    state: dict = {
        "turns": [],
        "transcript": [],
        "exit": False,
        "require_provider": None,
        "tier_preferred":
            cfg.model_catalog["routing"].get("default_tier", "small"),
    }
    slash = _SlashHandler(console, cascade, state)
    history: list[dict[str, str]] = []
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

    if require_distributed:
        os.environ["ANIMICA_CHAT_REQUIRE_DISTRIBUTED"] = "1"
    if yolo:
        os.environ["ANIMICA_CHAT_YOLO"] = "1"

    rc = _run_repl(cfg, rpc_url=endpoint, wallet_path=wallet,
                   yolo=yolo, console=console, wallet_label=wallet_label)
    raise typer.Exit(code=rc)


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
