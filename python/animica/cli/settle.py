"""`animica settle` — on-chain IOU settlement anchors (9.0.0, FORK_IOU_SETTLEMENT).

The settlement authority (the foundation treasury account, code-committed in
consensus/iou_settlement.py) discharges off-chain service IOUs (media miners,
dVPN exits, hosting pins, AICF workers) by posting a **settlement anchor**: an
ordinary signed TRANSFER from the treasury TO ITSELF carrying
``ANMSETL1{"v":1,"pay":[["anim1…", nANM], …]}`` in its data field. Blocks
at/after mainnet height 50,000 pay the anchored entries in-block, capped at
50 ANM (halving with emission) and CARVED from the miner subsidy.

Commands:
  status   chain height, fork height/activation, current pool cap, authority key
  build    validate a distribution and write an anchor JSON file
  post     sign + submit an anchor (MONEY-MOVING: it triggers consensus payouts)

`post` reuses the EXACT `animica tx send` build/sign/submit pipeline
(animica.cli.tx helpers + animica.tx.signing) — the only difference from
`tx send` is a non-empty TxTransfer data field, which the `send` command
hardcodes to b"" and this module passes to the underlying _build_tx_body().
"""

from __future__ import annotations

import base64
import json
import os
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, List, Optional, Tuple

import typer
from rich.console import Console

console = Console()
app = typer.Typer(help="On-chain IOU settlement anchors (authority = foundation treasury).")

ANM = 1_000_000_000  # 1 ANM = 1e9 nANM
DEFAULT_RPC = "https://rpc.animica.org/rpc"
MAINNET_CHAIN_ID = 1


# ---------------------------------------------------------------------------
# Reusable plumbing (imported by scripts/iou_settlement_worker.py — keep pure)
# ---------------------------------------------------------------------------


def resolve_rpc_url(rpc_url: Optional[str] = None) -> str:
    """CLI flag > ANIMICA_RPC_URL > https://rpc.animica.org/rpc."""
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
    env = os.environ.get("ANIMICA_RPC_URL")
    if env and env.strip():
        return env.strip()
    return DEFAULT_RPC


def rpc_call(rpc_url: str, method: str, params: Optional[list] = None) -> Any:
    from animica.cli.tx import _rpc

    return _rpc(rpc_url, method, params or [])


def get_chain_height(rpc_url: str) -> Optional[int]:
    from animica.cli.tx import _get_head_height

    return _get_head_height(rpc_url)


def load_consensus_params(chain_id: int = MAINNET_CHAIN_ID) -> dict:
    """Full params dict (spec/params.yaml) — the same loader block_import and
    the consensus tests use, so the cap here matches the cap consensus applies."""
    from core.chain.block_import import _load_full_params_dict

    return _load_full_params_dict(chain_id)


def get_fork_height(chain_id: int = MAINNET_CHAIN_ID) -> Optional[int]:
    from consensus.iou_settlement import FORK_IOU_SETTLEMENT
    from core.network_params import get_activation_height

    return get_activation_height(FORK_IOU_SETTLEMENT, chain_id=chain_id)


def current_pool_cap_nanm(
    height: int, chain_id: int = MAINNET_CHAIN_ID, params: Optional[dict] = None
) -> int:
    """How much ONE anchor can actually be paid at `height`, in nANM.

    Two regimes, and getting this wrong is expensive in both directions:

      * below FORK_SERVICE_CARVE — the 9.0.0 IOU pool, ``settlement_pool_cap``
        (50 ANM, decaying with the subsidy);
      * from FORK_SERVICE_CARVE (75,000 on mainnet) — the carve REPLACES that
        pool (``core/chain/block_import.py``: the IOU branch is gated on
        ``not is_fork_active(FORK_SERVICE_CARVE, …)``), and consensus caps an
        anchor at ``carve_amount(total_block_subsidy, 25)`` = **75 ANM**, not 50.

    Reporting the stale 50 ANM after the fork is not a safety problem — it
    under-claims, and consensus pays a smaller anchor in full — but it strands
    25 ANM of every block's carve in the treasury permanently, because the
    carve does NOT accumulate: whatever an anchor does not claim in the block
    it lands in is gone (``split_carve`` routes the residual to the treasury).

    This mirrors the same computation ``miner.getBlockTemplate`` does (the
    10.2.2 carve fix) so the client and the miner agree on one number. This is
    a CLIENT-side helper only — it is never imported by block application, so
    it cannot change consensus. The consensus function ``settlement_pool_cap``
    is deliberately left untouched.
    """
    from consensus.iou_settlement import settlement_pool_cap

    if params is None:
        params = load_consensus_params(chain_id)
    h = max(1, int(height))

    try:
        from consensus.rewards import compute_block_reward, service_carve_pct
        from consensus.service_carve import carve_amount

        pct = service_carve_pct(h, chain_id=int(chain_id))
        if pct > 0:
            # The carve is a share of the WHOLE block subsidy (miner + treasury
            # + aicf), not of the post-treasury miner slice — so sum every
            # coinbase output, exactly as block application does.
            total = sum(int(a) for _, a in compute_block_reward(int(chain_id), h, params))
            return carve_amount(total, pct)
    except Exception:
        # Fall through to the pre-fork pool. This direction is the safe one:
        # a smaller cap under-claims, it can never request more than consensus
        # will pay (which would be scaled pro-rata and stall the worker).
        pass

    return settlement_pool_cap(h, params)


def authority_address() -> str:
    from consensus.iou_settlement import SETTLEMENT_AUTHORITY_ADDRESSES

    return SETTLEMENT_AUTHORITY_ADDRESSES[0]


def wallet_holds_authority_key() -> bool:
    """True iff the local wallet store (~/.animica/wallets.json — the same store
    `animica tx send` signs from) holds key material for the settlement
    authority. Reads the raw store WITHOUT decrypting anything: a presence
    check must never unseal (or print) secret material."""
    from animica.cli.tx import _wallet_store_path

    path = _wallet_store_path()
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    target = authority_address()
    for w in data.get("wallets", []) or []:
        if isinstance(w, dict) and str(w.get("address")) == target:
            return bool(
                w.get("secret_key_hex") or w.get("secretKeyHex") or w.get("secret_key_enc")
            )
    return False


def parse_pay_specs(pays: List[str]) -> List[Tuple[str, int]]:
    """['anim1…=1.5', …] (amounts in ANM) → [(address, nANM), …]."""
    entries: List[Tuple[str, int]] = []
    for spec in pays:
        addr, sep, amt_s = spec.partition("=")
        addr, amt_s = addr.strip(), amt_s.strip()
        if not sep or not addr or not amt_s:
            raise ValueError(f"--pay expects anim1...=AMOUNT_ANM, got {spec!r}")
        try:
            dec = Decimal(amt_s.replace("_", ""))
        except InvalidOperation as exc:
            raise ValueError(f"invalid ANM amount in {spec!r}") from exc
        if dec <= 0:
            raise ValueError(f"amount must be positive in {spec!r}")
        base = dec * ANM
        if base != base.to_integral_value():
            raise ValueError(f"more than 9 decimal places in {spec!r}")
        entries.append((addr, int(base)))
    return entries


def encode_entries(entries: List[Tuple[str, int]]) -> bytes:
    """Strict-validate + encode via the CONSENSUS encoder (raises ValueError on
    anything consensus would ignore — the CLI must never post a dud anchor)."""
    from consensus.iou_settlement import encode_anchor_payload

    return encode_anchor_payload(entries)


def _fmt_anm(nanm: int) -> str:
    return f"{Decimal(int(nanm)) / ANM} ANM"


def authority_nonce_lock():
    """Cross-process nonce lock for the authority account (the same lock
    `animica tx send` holds while it picks a nonce and submits)."""
    from animica.cli import tx as txcli

    return txcli._nonce_lock(authority_address())


def prepare_anchor_tx(
    entries: List[Tuple[str, int]],
    *,
    rpc_url: Optional[str] = None,
    allow_remote: bool = False,
    verbose: bool = False,
) -> dict:
    """ALL fallible pre-broadcast work for one settlement anchor: validate +
    encode the payload, resolve chain identity, load the authority signer,
    pick fees/validity window/nonce, build, sign and locally verify the tx.
    Returns {"raw_hex": …, "rpc": …} ready for broadcast_anchor(). Nothing is
    submitted — callers can fail/retry here with no on-chain side effects.

    Callers that separate prepare from broadcast should hold
    authority_nonce_lock() across both (sign_and_submit_anchor does).

    This is the exact `animica tx send` pipeline: _get_chain_identity /
    _chain_context_from_identity → _load_wallet_entry → _next_nonce →
    _build_tx_body(data=payload) → pq_sign_tx + pq_verify_tx → _build_raw_tx.
    No signing/serialization is reimplemented here.
    """
    from animica.cli import tx as txcli
    from animica.cli.rpc_guard import guard_bootstrap_rpc
    from animica.tx.signing import pq_sign_tx, pq_verify_tx

    payload = encode_entries(entries)
    authority = authority_address()
    rpc = resolve_rpc_url(rpc_url)

    # Same remote-bootstrap gate `tx send` applies before submitting.
    guard_bootstrap_rpc(rpc, allow_remote=allow_remote, method="tx.sendRawTransaction")

    head_hint = txcli._ensure_node_ready_for_tx(rpc)
    resolution = txcli._get_chain_identity(rpc)
    chain_identity = resolution.identity
    cid = int(chain_identity.get("chainId"))
    chain_ctx = txcli._chain_context_from_identity(
        chain_identity,
        chain_id=cid,
        domain=txcli.DEFAULT_DOMAIN,
        prehash=txcli.DEFAULT_PREHASH,
    )

    # Wallet-store signer (handles at-rest encryption via passphrase env);
    # raises with an actionable message when the authority key is absent.
    w = txcli._load_wallet_entry(authority)
    used_alg_id = int(w.get("alg_id") or w.get("algId") or 0x1003)
    pk = txcli._hex_to_bytes(str(w.get("public_key_hex") or w.get("publicKeyHex") or ""))
    sk = txcli._hex_to_bytes(str(w.get("secret_key_hex") or w.get("secretKeyHex") or ""))
    if not pk or not sk:
        raise RuntimeError("authority wallet entry missing key material")

    quote_limit, quote_price = txcli._estimate_fee_quote(rpc)
    gas_limit = quote_limit if quote_limit is not None else 21000
    max_fee = quote_price if quote_price is not None else txcli._get_default_max_fee(rpc)
    valid_after, valid_until = txcli._resolve_validity_window(
        rpc,
        valid_from=None,
        valid_until=None,
        ttl_blocks=None,
        head_height_hint=head_hint,
        verbose=verbose,
    )

    nonce = txcli._next_nonce(rpc, authority, refresh=True, verbose=verbose)
    body = txcli._build_tx_body(
        chain_id=cid,
        from_addr=authority,
        to_addr=authority,  # self-transfer: the anchor's value is symbolic
        nonce=nonce,
        value_base_units=1,  # 1 nANM
        gas_limit=int(gas_limit),
        max_fee=int(max_fee),
        data=payload,  # ← the settlement anchor payload (ANMSETL1…)
        valid_after=valid_after,
        valid_until=valid_until,
        salt=os.urandom(16),
    )
    pq = pq_sign_tx(body, sk, pk, used_alg_id, chain_ctx)
    vr = pq_verify_tx(body, pq, pk, chain_ctx, from_addr=authority)
    if not vr.ok:
        raise RuntimeError(f"local PQ verify failed before broadcast: {vr.reason}")
    raw = txcli._build_raw_tx(
        body=body,
        alg_id=pq.alg_id,
        pk=pk,
        sig=pq.sig,
        domain=txcli.DEFAULT_DOMAIN,
        prehash=txcli.DEFAULT_PREHASH,
        chain_id=cid,
    )
    return {"raw_hex": "0x" + raw.hex(), "rpc": rpc}


def broadcast_anchor(prepared: dict) -> str:
    """Submit a prepare_anchor_tx() result; returns the tx hash. The ONLY
    on-chain side effect in this module lives here."""
    from animica.cli import tx as txcli

    result = txcli._rpc(prepared["rpc"], "tx.sendRawTransaction", [prepared["raw_hex"]])
    tx_hash: Optional[str] = None
    if isinstance(result, str):
        tx_hash = result
    elif isinstance(result, dict):
        for key in ("tx_hash", "hash", "txHash", "transactionHash"):
            if isinstance(result.get(key), str):
                tx_hash = result[key]
                break
    if not tx_hash:
        raise RuntimeError(f"unexpected tx.sendRawTransaction result: {result!r}")
    return tx_hash


def sign_and_submit_anchor(
    entries: List[Tuple[str, int]],
    *,
    rpc_url: Optional[str] = None,
    allow_remote: bool = False,
    verbose: bool = False,
) -> str:
    """Build, sign and submit ONE settlement anchor; returns the tx hash.

    MONEY-MOVING: the transfer itself moves only 1 nANM treasury→treasury (plus
    fee), but once FORK_IOU_SETTLEMENT is active its inclusion TRIGGERS real
    consensus payouts carved from the miner subsidy. Callers own the safety
    gates (height/cap/confirmation) — this function only refuses invalid
    payloads and missing keys.
    """
    with authority_nonce_lock():
        prepared = prepare_anchor_tx(
            entries, rpc_url=rpc_url, allow_remote=allow_remote, verbose=verbose
        )
        return broadcast_anchor(prepared)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def status(
    rpc_url: Optional[str] = typer.Option(
        None, "--rpc", "--rpc-url", help=f"RPC URL (default: ANIMICA_RPC_URL or {DEFAULT_RPC})"
    ),
    json_output: bool = typer.Option(False, "--json", help="Machine-readable output"),
):
    """Settlement status: chain height, fork activation, current pool cap,
    settlement authority and whether the local wallet can sign for it."""
    rpc = resolve_rpc_url(rpc_url)
    height = get_chain_height(rpc)
    fork_h = get_fork_height()
    active = height is not None and fork_h is not None and height >= fork_h
    cap_height = height if height is not None else (fork_h or 1)
    cap = current_pool_cap_nanm(cap_height)
    authority = authority_address()
    holds_key = wallet_holds_authority_key()

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "rpc": rpc,
                    "chainHeight": height,
                    "forkHeight": fork_h,
                    "forkActive": active,
                    "poolCapNanm": cap,
                    "poolCapAnm": str(Decimal(cap) / ANM),
                    "authorityAddress": authority,
                    "walletHoldsAuthorityKey": holds_key,
                }
            )
        )
        return

    console.print(f"rpc                 : {rpc}")
    console.print(f"chain height        : {height if height is not None else 'UNAVAILABLE'}")
    console.print(f"fork height         : {fork_h} (FORK_IOU_SETTLEMENT / vpn_relay_rewards)")
    if height is not None and fork_h is not None and not active:
        console.print(f"fork active         : no ({fork_h - height} blocks to activation)")
    else:
        console.print(f"fork active         : {'yes' if active else 'unknown'}")
    console.print(f"settlement pool cap : {_fmt_anm(cap)} per block (carved from miner subsidy)")
    console.print(f"authority address   : {authority}")
    console.print(f"authority key local : {'yes (wallet store can sign)' if holds_key else 'NO'}")


@app.command()
def build(
    pay: List[str] = typer.Option(
        ..., "--pay", help="Distribution entry anim1...=AMOUNT_ANM (repeatable)"
    ),
    out: Path = typer.Option(Path("anchor.json"), "--out", help="Output anchor JSON file"),
    allow_scaled: bool = typer.Option(
        False,
        "--allow-scaled",
        help="Allow a total over the current cap (consensus will scale outputs down pro-rata)",
    ),
    rpc_url: Optional[str] = typer.Option(None, "--rpc", "--rpc-url", help="RPC URL for cap lookup"),
):
    """Validate a distribution through the consensus encoder and write an
    anchor file: {entries, payload_b64, total_nanm}. No transaction is sent."""
    try:
        entries = parse_pay_specs(pay)
        payload = encode_entries(entries)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    total = sum(a for _, a in entries)
    rpc = resolve_rpc_url(rpc_url)
    height = get_chain_height(rpc)
    cap_height = height if height is not None else (get_fork_height() or 1)
    cap = current_pool_cap_nanm(cap_height)

    if total > cap and not allow_scaled:
        console.print(
            f"[bold red]Refusing:[/bold red] total {_fmt_anm(total)} exceeds the current "
            f"settlement pool cap {_fmt_anm(cap)}; consensus would scale every entry down "
            f"pro-rata. Split the distribution across blocks, or pass --allow-scaled."
        )
        raise typer.Exit(code=1)

    doc = {
        "entries": [[addr, amt] for addr, amt in entries],
        "payload_b64": base64.b64encode(payload).decode("ascii"),
        "total_nanm": total,
    }
    out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    console.print(f"anchor written      : {out}")
    console.print(f"entries             : {len(entries)}")
    for addr, amt in entries:
        console.print(f"  {addr}  {_fmt_anm(amt)}")
    console.print(f"total               : {_fmt_anm(total)} (cap {_fmt_anm(cap)})")
    if total > cap:
        console.print("[yellow]NOTE: over cap — consensus will scale outputs down pro-rata.[/yellow]")


def _load_anchor_file(path: Path) -> List[Tuple[str, int]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    raw_entries = doc.get("entries")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise ValueError(f"{path}: missing/empty 'entries'")
    entries: List[Tuple[str, int]] = []
    for e in raw_entries:
        if not isinstance(e, (list, tuple)) or len(e) != 2:
            raise ValueError(f"{path}: malformed entry {e!r}")
        entries.append((str(e[0]), int(e[1])))
    # Integrity: if the file carries a payload, it must match a re-encode of
    # the entries (detects hand-edits that would change what actually pays).
    payload = encode_entries(entries)
    stored = doc.get("payload_b64")
    if stored is not None and base64.b64decode(stored) != payload:
        raise ValueError(f"{path}: payload_b64 does not match entries (file edited?)")
    return entries


@app.command()
def post(
    file: Optional[Path] = typer.Option(None, "--file", help="Anchor JSON from `animica settle build`"),
    pay: Optional[List[str]] = typer.Option(
        None, "--pay", help="Distribution entry anim1...=AMOUNT_ANM (repeatable, alternative to --file)"
    ),
    rpc_url: Optional[str] = typer.Option(None, "--rpc", "--rpc-url", help="RPC URL"),
    yes: bool = typer.Option(False, "--yes", help="Confirm submission (required non-interactively)"),
    allow_scaled: bool = typer.Option(
        False, "--allow-scaled", help="Allow a total over the current cap (pro-rata scaled by consensus)"
    ),
    force_prefork: bool = typer.Option(
        False,
        "--force-prefork",
        help="DANGEROUS: post even though the fork height is not reached (anchor is an inert transfer)",
    ),
    allow_remote_rpc: bool = typer.Option(
        False, "--allow-remote-rpc", help="Allow submitting via the bootstrap RPC (see tx send)"
    ),
):
    """Sign and submit a settlement anchor from the foundation treasury wallet.

    The transfer itself is 1 nANM treasury→itself (plus fee), but at/after the
    fork height its inclusion TRIGGERS consensus payouts carved from the miner
    subsidy — treat this command as money-moving."""
    if (file is None) == (not pay):
        console.print("[bold red]Provide exactly one of --file or --pay.[/bold red]")
        raise typer.Exit(code=1)

    try:
        entries = _load_anchor_file(file) if file is not None else parse_pay_specs(pay or [])
        encode_entries(entries)  # strict consensus validation before any RPC
    except (ValueError, OSError) as exc:
        console.print(f"[bold red]Invalid anchor:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc

    total = sum(a for _, a in entries)
    rpc = resolve_rpc_url(rpc_url)
    height = get_chain_height(rpc)
    fork_h = get_fork_height()
    if height is None or fork_h is None:
        console.print(
            "[bold red]Refusing:[/bold red] cannot resolve chain height / fork height "
            f"(rpc={rpc}) — the safety gates need both."
        )
        raise typer.Exit(code=1)
    cap = current_pool_cap_nanm(max(height, fork_h))

    console.print(f"rpc            : {rpc}")
    console.print(f"chain height   : {height}   fork height: {fork_h}")
    console.print(f"pool cap       : {_fmt_anm(cap)} per block")
    console.print(f"distribution   : {len(entries)} entr{'y' if len(entries) == 1 else 'ies'}")
    for addr, amt in entries:
        console.print(f"  {addr}  {_fmt_anm(amt)}")
    console.print(f"total          : {_fmt_anm(total)}")

    # --- HARD SAFETY GATES ---------------------------------------------------
    if height < fork_h:
        if not force_prefork:
            console.print(
                f"[bold red]Refusing:[/bold red] chain height {height} is below the fork "
                f"height {fork_h}. A pre-fork anchor settles NOTHING (it is just an inert "
                f"transfer that burns a fee). Wait for activation, or pass --force-prefork."
            )
            raise typer.Exit(code=1)
        console.print(
            "[bold yellow]WARNING:[/bold yellow] posting BEFORE the fork height — this anchor "
            "is an INERT transfer to every current node and settles nothing on-chain."
        )
    if total > cap and not allow_scaled:
        console.print(
            f"[bold red]Refusing:[/bold red] total {_fmt_anm(total)} exceeds the current pool "
            f"cap {_fmt_anm(cap)}; consensus would pro-rata scale every entry down. Pass "
            f"--allow-scaled to accept scaling, or split across blocks."
        )
        raise typer.Exit(code=1)
    if total > cap:
        console.print(
            f"[yellow]NOTE: over cap — consensus pays at most {_fmt_anm(cap)} this block, "
            f"scaled pro-rata; the shortfall stays owed off-chain.[/yellow]"
        )

    if not yes:
        if not sys.stdin.isatty():
            console.print("[bold red]Refusing:[/bold red] non-interactive run requires --yes.")
            raise typer.Exit(code=1)
        typer.confirm("Post this settlement anchor (triggers consensus payouts)?", abort=True)

    try:
        tx_hash = sign_and_submit_anchor(
            entries, rpc_url=rpc, allow_remote=allow_remote_rpc
        )
    except Exception as exc:
        console.print(f"[bold red]Anchor submission failed:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]anchor posted[/green] tx_hash={tx_hash}")
    console.print("Track inclusion:  animica rpc call tx.getTransactionStatus " + f"'\"{tx_hash}\"'")
