"""`animica compute` — manage opt-in third-party GPU compute (Clore).

Off by default. `animica up` asks once on a GPU box; this command changes the
answer afterwards, or enrolls/removes the Clore agent directly.

    animica compute status       show current setting + enrollment state
    animica compute on           consent to renting this GPU on Clore
    animica compute off          withdraw consent and remove the agent
    animica compute enroll       install + link the Clore agent (root)
"""
from __future__ import annotations

import typer
from rich.console import Console

app = typer.Typer(help="Opt-in third-party GPU compute (Clore).", no_args_is_help=True)
console = Console()


@app.command("status")
def status() -> None:
    """Show whether third-party compute is enabled and enrolled."""
    from .compute_consent import _load, earnings_estimate, _fmt_usd
    from animica.compute.clore_agent import is_enrolled
    st = _load()
    consent = st.get("clore")
    console.print(f"consent   : {'ENABLED' if consent else 'off' if consent is not None else 'not asked yet'}")
    console.print(f"enrolled  : {'yes' if is_enrolled() else 'no'}")
    est = earnings_estimate(console)
    if est:
        console.print(f"estimate  : mining {_fmt_usd(est['mining_usd_day'])}/day  vs  "
                      f"Clore {_fmt_usd(est['compute_usd_day'])}/day (your 90%)")


@app.command("on")
def on() -> None:
    """Consent to renting this GPU on Clore (does not install the agent)."""
    from .compute_consent import set_consent
    set_consent(True)
    console.print("[green]compute enabled[/green] — run `animica up` (as root) to enroll, "
                  "or `animica compute enroll`.")


@app.command("off")
def off() -> None:
    """Withdraw consent and remove the Clore agent."""
    from .compute_consent import set_consent
    from animica.compute.clore_agent import unenroll, is_enrolled
    set_consent(False)
    if is_enrolled():
        unenroll(console)
    console.print("[green]compute disabled[/green].")


@app.command("enroll")
def enroll_cmd(
    token: str = typer.Option("", "--token", help="Clore init-token (else fetched from the pool)"),
    pool_host: str = typer.Option("pool.animica.org", "--pool-host"),
    address: str = typer.Option("", "--address", help="payout address, for pool token binding"),
    dry_run: bool = typer.Option(False, "--dry-run", help="print the commands, run nothing"),
) -> None:
    """Install and link the Clore agent. Requires prior consent and root."""
    from .compute_consent import _load
    from animica.compute.clore_agent import enroll, fetch_pool_token
    if not _load().get("clore"):
        console.print("[red]compute is not enabled — run `animica compute on` first.[/red]")
        raise typer.Exit(1)
    tok = token or fetch_pool_token(pool_host, "", address) or ""
    enroll(console, token=tok, assume_yes=True, dry_run=dry_run)
