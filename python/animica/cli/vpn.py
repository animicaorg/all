"""
`animica vpn` — the Animica decentralized VPN.

HONEST FRAMING (enforced in the copy below):
  * this native client is the ONLY system-wide tunnel; the browser extension is a proxy.
  * single-hop — hides your traffic from your ISP/LAN, NOT from the exit operator; not Tor.
  * bandwidth rewards are IOUs (settlement deferred); nothing is paid on-chain today.
  * running an exit egresses third-party traffic from YOUR ip — opt-in, ToS-gated.
"""

from __future__ import annotations

import json as _json
import os
import time
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(name="vpn", help="Animica decentralized VPN (WireGuard + ANM rewards).",
                  no_args_is_help=True, add_completion=False)
exit_app = typer.Typer(name="exit", help="Run / manage a dVPN EXIT (opt-in, ToS-gated, OFF by default).",
                       no_args_is_help=True, add_completion=False)
app.add_typer(exit_app, name="exit")
console = Console()

_BANNER = ("[dim]Single-hop dVPN — hides traffic from your ISP/LAN, not the exit operator (not Tor). "
           "Rewards are IOUs (settlement deferred). The browser extension is a proxy, not this.[/dim]")


def _wallet_path() -> str:
    d = os.environ.get("ANIMICA_VPN_STATE", os.path.expanduser("~/.animica/vpn"))
    return os.path.join(d, "wallet.json")


def _wallet():
    from animica.vpn.crypto import Wallet
    return Wallet.load_or_create(_wallet_path())


def _emit(obj, as_json: bool):
    if as_json:
        console.print_json(_json.dumps(obj, default=str))
    return obj


# --------------------------------------------------------------------- client cmds

@app.command("exits")
def exits(region: Optional[str] = typer.Option(None, help="Filter by region, e.g. eu, us, asia"),
          country: Optional[str] = typer.Option(None, help="Filter by 2-letter country code"),
          json_out: bool = typer.Option(False, "--json")):
    """List available exit locations from the registry (you pick; the server only ranks)."""
    from animica.vpn.registry_client import RegistryClient
    reg = RegistryClient(_wallet())
    try:
        xs = reg.list_exits(region=region, country=country)
    except Exception as e:
        console.print(f"[red]registry error:[/red] {e}"); raise typer.Exit(1)
    if json_out:
        return _emit([x.__dict__ for x in xs], True)
    if not xs:
        console.print("[yellow]No exits available.[/yellow] The network may have no online exits yet.")
        console.print(_BANNER); return
    t = Table(title="Animica dVPN — available exit locations")
    for c in ("id", "location", "region", "load", "reputation", "browser-proxy", "online"):
        t.add_column(c)
    for x in xs:
        loc = ", ".join(p for p in (x.city, x.country) if p) or x.region
        t.add_row(x.id, loc, x.region, f"{x.load:.2f}", f"{x.reputation:.1f}",
                  "yes" if x.httpProxy else "no", "🟢" if x.online else "⚪")
    console.print(t); console.print(_BANNER)


@app.command("up")
@app.command("connect")
def up(exit_id: Optional[str] = typer.Argument(None, help="Exit id (omit to auto-pick by region/RTT)"),
       region: Optional[str] = typer.Option(None, help="Prefer this region"),
       country: Optional[str] = typer.Option(None),
       split_tunnel: Optional[str] = typer.Option(None, "--split-tunnel", help="Route only these CIDRs (comma-sep)"),
       dns: str = typer.Option("1.1.1.1", help="DNS forced through the tunnel"),
       killswitch: bool = typer.Option(True, "--killswitch/--no-killswitch"),
       json_out: bool = typer.Option(False, "--json")):
    """Bring up a system-wide WireGuard tunnel through a chosen exit."""
    from animica.vpn import client
    allowed = split_tunnel.replace(" ", "") if split_tunnel else "0.0.0.0/0"
    try:
        res = client.connect(_wallet(), exit_id=exit_id, region=region, country=country,
                             allowed_ips=allowed, dns=dns, killswitch=killswitch)
    except Exception as e:
        console.print(f"[red]connect failed:[/red] {e}"); raise typer.Exit(1)
    if json_out:
        return _emit(res, True)
    ex = res["exit"]
    loc = ", ".join(p for p in (ex.get("city"), ex.get("country")) if p) or ex.get("region")
    console.print(f"[green]● connected[/green] via [b]{loc}[/b] (exit {ex['id']}, {res['backend']} WireGuard)")
    console.print(f"  apparent IP: [b]{res.get('beforeIp')}[/b] → [b]{res.get('afterIp')}[/b] "
                  + ("[green](tunnelling)[/green]" if res.get("tunneling") else "[yellow](verify with `animica vpn doctor`)[/yellow]"))
    console.print(_BANNER)


@app.command("status")
def status(json_out: bool = typer.Option(False, "--json")):
    """Show tunnel state, transfer counters, and apparent IP."""
    from animica.vpn import client
    s = client.status()
    if json_out:
        return _emit(s, True)
    if not s.get("connected"):
        console.print("[dim]not connected[/dim]"); return
    console.print(f"[green]● connected[/green] session {s.get('sessionId')} · exit {s.get('exitId')}")
    console.print(f"  apparent IP: [b]{s.get('apparentIp')}[/b]  killswitch: {s.get('killswitch')}")
    for pub, xf in (s.get("transfer") or {}).items():
        console.print(f"  peer {pub[:12]}…  rx {xf['rx']:,}  tx {xf['tx']:,}")


@app.command("doctor")
def doctor():
    """Leak self-test — refuses to confirm 'connected' until all checks pass."""
    from animica.vpn import doctor as doc
    rep = doc.run()
    for c in rep.checks:
        console.print(f"  {'[green]OK[/green]' if c.ok else '[red]FAIL[/red]'} {c.name}: {c.detail}")
    if rep.healthy:
        console.print("[green]✔ tunnel healthy — no leaks detected[/green]")
    else:
        console.print("[red]✗ tunnel NOT verified — do not treat traffic as protected[/red]")
        raise typer.Exit(2)


@app.command("down")
@app.command("disconnect")
def down():
    """Tear down the tunnel, remove the killswitch, and report final usage."""
    from animica.vpn import client
    res = client.disconnect(_wallet())
    console.print(f"[dim]{res.get('status')}[/dim]")


@app.command("earnings")
def earnings(json_out: bool = typer.Option(False, "--json")):
    """Your exit IOU accrual + client usage. Rewards are deferred IOUs, not spendable ANM."""
    from animica.vpn.registry_client import RegistryClient
    try:
        d = RegistryClient(_wallet()).me_vpn()
    except Exception as e:
        console.print(f"[red]registry error:[/red] {e}"); raise typer.Exit(1)
    if json_out:
        return _emit(d, True)
    console.print_json(_json.dumps(d, default=str))
    console.print("[yellow]IOU — treasury-funded dVPN settlement is deferred (not yet a spendable balance).[/yellow]")


# --------------------------------------------------------------------- exit cmds

@exit_app.command("register")
def exit_register(region: str = typer.Option("unknown", help="Region tag, e.g. eu, us-east"),
                  country: str = typer.Option("", help="2-letter country code"),
                  city: str = typer.Option("", help="City label"),
                  label: str = typer.Option("animica-exit"),
                  accept_tos: bool = typer.Option(False, "--i-accept-exit-tos",
                                                  help="Accept the exit operator ToS + liability (required)")):
    """Accept the exit ToS (once) and publish this host as an exit descriptor."""
    from animica.vpn import tos
    w = _wallet()
    console.print(tos.TOS_TEXT)
    if not accept_tos:
        console.print("[red]Refusing:[/red] re-run with --i-accept-exit-tos to accept the terms above.")
        raise typer.Exit(1)
    acc = tos.build_acceptance(w, int(time.time()))
    path = tos.save_acceptance(acc)
    console.print(f"[green]ToS accepted[/green] and signed (record: {path}).")
    console.print("Now start the exit with: [b]animica vpn exit serve[/b]")


@exit_app.command("serve")
def exit_serve(region: str = typer.Option("unknown"),
               country: str = typer.Option(""),
               city: str = typer.Option(""),
               label: str = typer.Option("animica-exit"),
               port: int = typer.Option(51820, help="WireGuard UDP listen port (NOT 443)"),
               browser_proxy: bool = typer.Option(False, "--browser-proxy",
                                                  help="Also run an HTTP proxy for the extension's browser-proxy mode"),
               proxy_port: int = typer.Option(8443),
               capacity_mbps: int = typer.Option(100),
               i_am_not_the_validator: bool = typer.Option(False, "--i-am-not-the-validator",
                                                           help="Override co-location guard on a node host")):
    """Run the exit daemon (foreground). Off by default; requires accepted ToS + a free UDP port."""
    from animica.vpn.exit_daemon import ExitConfig, ExitDaemon
    w = _wallet()
    cfg = ExitConfig(region=region, country=country, city=city, label=label,
                     capacity_mbps=capacity_mbps, listen_port=port,
                     enable_browser_proxy=browser_proxy, browser_proxy_port=proxy_port,
                     allow_validator=i_am_not_the_validator)
    d = ExitDaemon(w, cfg)
    try:
        d.preflight()
        d.setup()
        exit_id = d.register()
    except Exception as e:
        console.print(f"[red]exit failed to start:[/red] {e}"); raise typer.Exit(1)
    console.print(f"[green]● exit online[/green] id={exit_id} region={region} udp={port}"
                  + (f" browser-proxy=:{proxy_port}" if browser_proxy else ""))
    console.print("[yellow]Third-party traffic egresses from this IP. Ctrl-C to stop.[/yellow]")
    try:
        d.run()
    except KeyboardInterrupt:
        console.print("\n[dim]stopping…[/dim]")
    finally:
        d.teardown()
        console.print("[dim]exit stopped, firewall + interface removed.[/dim]")


@exit_app.command("stop")
def exit_stop():
    """Force-remove any leftover exit interface + nft table (idempotent)."""
    from animica.vpn import WG_IFACE, nftables, wg
    wg.iface_del(WG_IFACE)
    nftables.remove_exit()
    console.print("[dim]exit interface + nft table removed (if present).[/dim]")
