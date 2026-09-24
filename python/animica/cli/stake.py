"""
animica stake — bond ANM into the PoS stake, and withdraw matured bonds.

    animica stake 1000 30 --name my-validator     # bond 1000 ANM for 30 days
    animica stake withdraw 250                    # withdraw 250 ANM of matured stake
    animica stake status                          # this wallet's bonds
    animica stake list                            # every staker on the network

Bonding moves spendable balance into a time-locked bond and makes the address
eligible to mint blocks, weighted by its share of total stake (see FORK_POS_MINTING).
A bond can only be withdrawn once its lock has expired; `status` shows what is
matured. The display name is a label only — it is never an identity, and block
production is always authorised by the signing key.
"""

from __future__ import annotations

import os
import secrets
import time
from typing import Any, Dict, Optional

import typer
from rich.console import Console
from rich.table import Table

from animica.cli.tx import (
    DEFAULT_DOMAIN,
    DEFAULT_PREHASH,
    DEFAULT_TX_TTL_BLOCKS,
    _build_raw_tx,
    _build_tx_body,
    _chain_context_from_identity,
    _get_chain_identity,
    _get_default_max_fee,
    _get_head_height,
    _load_wallet_entry,
    _resolve_rpc_url,
    _rpc,
    _unlock_entry_secret,
)
from animica.tx.signing import pq_sign_tx
from core.staking import encode_stake_data

console = Console()

class _StakeGroup(typer.core.TyperGroup):
    """
    Route bare arguments to `bond` so `animica stake 1000 30` works, while
    `withdraw` / `status` / `list` stay ordinary subcommands.

    Typer cannot do this with positional arguments on the group callback: click
    binds them first, so `animica stake list` parses "list" as the amount.
    """

    DEFAULT = "bond"

    def parse_args(self, ctx, args):
        if args and not args[0].startswith("-") and args[0] not in self.commands:
            args = [self.DEFAULT, *args]
        return super().parse_args(ctx, args)


app = typer.Typer(
    cls=_StakeGroup,
    help="Bond ANM into the PoS stake and withdraw matured bonds.",
    no_args_is_help=True,
)

NANM = 1_000_000_000  # 9 decimals: 1 ANM = 1e9 base units
TXKIND_STAKE = 9
TXKIND_UNSTAKE = 10
STAKE_GAS_LIMIT = 30_000


def _to_base_units(amount: str) -> int:
    """Parse an ANM amount into base units, rejecting sub-nANM precision."""
    try:
        from decimal import Decimal, InvalidOperation

        d = Decimal(str(amount))
    except (InvalidOperation, ValueError) as exc:
        raise typer.BadParameter(f"not a number: {amount!r}") from exc
    if d <= 0:
        raise typer.BadParameter("amount must be > 0")
    scaled = d * NANM
    if scaled != scaled.to_integral_value():
        raise typer.BadParameter("amount has more precision than 9 decimals (1 nANM)")
    return int(scaled)


def _fmt(base_units: Any) -> str:
    try:
        return f"{int(base_units) / NANM:,.9f}".rstrip("0").rstrip(".") + " ANM"
    except Exception:
        return str(base_units)


def _resolve_sender(address: Optional[str]) -> str:
    addr = address or os.environ.get("ANIMICA_STAKE_ADDRESS") or os.environ.get(
        "ANIMICA_MINER_ADDRESS"
    )
    if not addr:
        raise typer.BadParameter(
            "no address given; pass --address or set ANIMICA_STAKE_ADDRESS"
        )
    return addr


def _submit(
    *,
    kind: int,
    rpc_url: str,
    from_addr: str,
    amount_base: int,
    days: int = 0,
    name: str = "",
    dry_run: bool,
) -> Optional[str]:
    """Build, sign and broadcast a stake-family transaction."""
    entry = _unlock_entry_secret(_load_wallet_entry(from_addr), from_addr)
    pk = bytes.fromhex(entry["public_key_hex"])
    sk = bytes.fromhex(entry["secret_key_hex"])
    alg_id = int(entry.get("alg_id") or 0)

    resolution = _get_chain_identity(rpc_url)
    identity = resolution.identity if hasattr(resolution, "identity") else resolution
    chain_id = int((identity or {}).get("chainId") or 1)
    chain_ctx = _chain_context_from_identity(
        identity or {},
        chain_id=chain_id,
        domain=DEFAULT_DOMAIN,
        prehash=DEFAULT_PREHASH,
    )

    head = _get_head_height(rpc_url) or 0
    valid_after = int(head)
    valid_until = int(head) + DEFAULT_TX_TTL_BLOCKS

    body = _build_tx_body(
        chain_id=chain_id,
        from_addr=from_addr,
        # Stake txs move value into the protocol's stake account, not to a peer;
        # `to` is the sender so the body stays well-formed for every existing
        # validator, while `kind` is what actually routes execution.
        to_addr=from_addr,
        nonce=0,
        value_base_units=int(amount_base),
        gas_limit=STAKE_GAS_LIMIT,
        max_fee=_get_default_max_fee(rpc_url),
        # The stake intent rides in `data`. It is the only field that survives
        # the mempool's body normalisation (which drops unknown top-level keys)
        # AND is inside payload.v, which the signature covers — so the intent is
        # authenticated and cannot be added to a signed transfer in flight.
        data=encode_stake_data(int(kind), days=int(days or 0), name=name or ""),
        valid_after=valid_after,
        valid_until=valid_until,
        salt=secrets.token_bytes(16),
    )

    pq = pq_sign_tx(body, sk, pk, alg_id, chain_ctx)
    raw = _build_raw_tx(
        body=body,
        alg_id=pq.alg_id,
        pk=pk,
        sig=pq.sig,
        domain=DEFAULT_DOMAIN,
        prehash=DEFAULT_PREHASH,
        chain_id=chain_id,
    )
    raw_hex = "0x" + raw.hex()

    if dry_run:
        console.print("[yellow]dry run[/yellow] — not broadcasting")
        console.print({"kind": kind, "bytes": len(raw), "days": days,
                       "name": name, "value_base_units": amount_base,
                       "data": "0x" + body["data"].hex()})
        return None

    result = _rpc(rpc_url, "tx.sendRawTransaction", [raw_hex])
    tx_hash = result.get("hash") if isinstance(result, dict) else result
    return str(tx_hash) if tx_hash else None


@app.command("bond")
def bond(
    amount: str = typer.Argument(..., help="Amount of ANM to bond, e.g. 1000"),
    days: int = typer.Argument(..., help="Lock duration in days (1-3650)"),
    name: str = typer.Option("", "--name", "-n", help="Display label shown on the pool and explorer"),
    address: Optional[str] = typer.Option(None, "--address", "-a", help="Wallet address to stake from"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="Node RPC URL"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Build and sign without broadcasting"),
) -> None:
    """Bond ANM for a fixed term: `animica stake <amount> <days>`."""
    if days <= 0 or days > 3650:
        raise typer.BadParameter("days must be between 1 and 3650")
    if name:
        bad = [c for c in name if not (c.isalnum() or c in "-_. ")]
        if bad:
            raise typer.BadParameter(
                "name may only contain letters, digits, space, '-', '_' or '.'"
            )
        if len(name.encode("utf-8")) > 32:
            raise typer.BadParameter("name must be at most 32 bytes")

    rpc = _resolve_rpc_url(rpc_url)
    sender = _resolve_sender(address)
    base = _to_base_units(amount)
    unlock = time.time() + days * 86_400

    console.print(
        f"Bonding [bold]{_fmt(base)}[/bold] from {sender[:20]}… for [bold]{days}d[/bold]"
        f" (unlocks ~{time.strftime('%Y-%m-%d', time.gmtime(unlock))})"
        + (f" as [bold]{name}[/bold]" if name else "")
    )
    tx_hash = _submit(
        kind=TXKIND_STAKE,
        rpc_url=rpc,
        from_addr=sender,
        amount_base=base,
        days=int(days),
        name=name,
        dry_run=dry_run,
    )
    if tx_hash:
        console.print(f"[green]submitted[/green] {tx_hash}")
        console.print("Check with: [bold]animica stake status[/bold]")


@app.command("withdraw")
def withdraw(
    amount: str = typer.Argument(..., help="Amount of MATURED stake to withdraw, in ANM"),
    address: Optional[str] = typer.Option(None, "--address", "-a", help="Wallet address"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="Node RPC URL"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Build and sign without broadcasting"),
) -> None:
    """Withdraw matured stake back to spendable balance."""
    rpc = _resolve_rpc_url(rpc_url)
    sender = _resolve_sender(address)
    base = _to_base_units(amount)

    # Fail early and locally: the node would reject this anyway, but telling the
    # user how much is actually matured is far more useful than a revert.
    try:
        st = _rpc(rpc, "stake.get", {"address": sender})
        if isinstance(st, dict) and st.get("available"):
            matured = int(st.get("matured") or 0)
            if matured < base:
                console.print(
                    f"[red]Only {_fmt(matured)} is matured[/red] "
                    f"(staked {_fmt(st.get('staked'))}, locked {_fmt(st.get('locked'))})."
                )
                raise typer.Exit(code=1)
    except typer.Exit:
        raise
    except Exception:
        pass  # pre-check is advisory; the node remains the authority

    console.print(f"Withdrawing [bold]{_fmt(base)}[/bold] of matured stake to {sender[:20]}…")
    tx_hash = _submit(
        kind=TXKIND_UNSTAKE,
        rpc_url=rpc,
        from_addr=sender,
        amount_base=base,
        dry_run=dry_run,
    )
    if tx_hash:
        console.print(f"[green]submitted[/green] {tx_hash}")


@app.command("status")
def status(
    address: Optional[str] = typer.Option(None, "--address", "-a", help="Wallet address"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="Node RPC URL"),
) -> None:
    """Show this address's bonds: total, matured, and each bond's unlock date."""
    rpc = _resolve_rpc_url(rpc_url)
    sender = _resolve_sender(address)
    res = _rpc(rpc, "stake.get", {"address": sender})
    if not isinstance(res, dict) or not res.get("available"):
        console.print(f"[red]unavailable[/red]: {(res or {}).get('reason', 'unknown')}")
        raise typer.Exit(code=1)
    if not res.get("staking"):
        console.print(f"{sender[:24]}… is not staking.")
        return

    console.print(
        f"[bold]{res.get('name') or '(unnamed)'}[/bold]  {sender[:24]}…\n"
        f"  staked  {_fmt(res.get('staked'))}\n"
        f"  matured {_fmt(res.get('matured'))}  (withdrawable now)\n"
        f"  locked  {_fmt(res.get('locked'))}"
    )
    t = Table("bond", "amount", "unlocks")
    now = int(time.time())
    for i, b in enumerate(res.get("bonds") or [], 1):
        unlock = int(b.get("unlockAt") or 0)
        when = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(unlock))
        t.add_row(str(i), _fmt(b.get("amount")), when + ("  (matured)" if unlock <= now else ""))
    console.print(t)


@app.command("list")
def list_stakers(
    limit: int = typer.Option(25, "--limit", "-l", help="How many stakers to show"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="Node RPC URL"),
) -> None:
    """List every staker on the network, largest first."""
    rpc = _resolve_rpc_url(rpc_url)
    summary = _rpc(rpc, "stake.summary", {})
    res = _rpc(rpc, "stake.list", {"limit": limit})
    if not isinstance(res, dict) or not res.get("available"):
        console.print(f"[red]unavailable[/red]: {(res or {}).get('reason', 'unknown')}")
        raise typer.Exit(code=1)

    if isinstance(summary, dict) and summary.get("available"):
        console.print(
            f"[bold]{summary.get('consensus', 'hybrid-pow-pos')}[/bold] at height "
            f"{summary.get('height')} — PoS active: {summary.get('posActive')}\n"
            f"  {summary.get('stakerCount')} staker(s), {_fmt(summary.get('totalStaked'))} staked, "
            f"minimum {_fmt(summary.get('minStake'))}"
            + ("\n  [yellow]bootstrap validator active (no bonds yet)[/yellow]"
               if summary.get("bootstrapActive") else "")
        )

    stakers = res.get("stakers") or []
    if not stakers:
        console.print("No stakers yet — be the first: [bold]animica stake <amount> <days>[/bold]")
        return
    t = Table("#", "name", "address", "staked", "bonds")
    for i, s in enumerate(stakers, 1):
        t.add_row(
            str(i),
            s.get("name") or "(unnamed)",
            (s.get("address") or s.get("accountKey", ""))[:26] + "…",
            _fmt(s.get("staked")),
            str(s.get("bondCount", 0)),
        )
    console.print(t)
