"""Animica L2 CLI (spec §40) — status, payments, bridge, batches, benchmarking.

Read-only commands talk JSON-RPC to the L2 endpoint (``--rpc-url`` /
``ANIMICA_L2_RPC_URL``, default ``http://127.0.0.1:8551``). Value-moving
commands build a canonical :class:`l2.tx.L2Tx`, sign it locally with ML-DSA-65,
and submit the raw envelope via ``l2_sendRawTransaction`` — the private key
never leaves this process.

Key handling: ``--key`` accepts a 32-byte hex seed and is intended for
devnet/testing. For real funds, keys should come from the Animica wallet
(``animica wallet``/``animica key``) keystore integration rather than a seed on
the command line — a seed in shell history is a compromised seed.

Amounts are ANM decimals by default (1 ANM = 10**9 nanos, exact integer
conversion, floats are never used); pass ``--nanos`` to give raw integer nanos.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from decimal import Decimal, InvalidOperation
from typing import Any, List, Optional, Tuple

import typer

from .node import rpc_call

app = typer.Typer(help="Animica L2 — instant ANM payments layer (status, send, bridge, batches, bench).")

L2_RPC_ENV = "ANIMICA_L2_RPC_URL"
DEFAULT_L2_RPC_URL = "http://127.0.0.1:8551"
KEY_ENV = "ANIMICA_L2_KEY"

# Lifecycle states after which a tx can no longer change (plus L1_FINALIZED,
# which is the terminal success state).
_TERMINAL_STATES = {"FAILED", "REVERTED", "L1_FINALIZED"}

_BENCH_WORKLOADS = ("transfers", "hot", "payments", "inference", "agent", "batch")


# ── plumbing ─────────────────────────────────────────────────────────────────


def _resolve_l2_rpc_url(rpc_url: Optional[str]) -> str:
    """CLI flag > ANIMICA_L2_RPC_URL > default. Empty strings count as unset."""
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
    env_url = os.environ.get(L2_RPC_ENV)
    if env_url and env_url.strip():
        return env_url.strip()
    return DEFAULT_L2_RPC_URL


def _rpc(method: str, params: Optional[list] = None, rpc_url: Optional[str] = None) -> Any:
    """One JSON-RPC call; converts failures into a clean CLI error."""
    url = _resolve_l2_rpc_url(rpc_url)
    try:
        return asyncio.run(rpc_call(method, params or [], rpc_url=url))
    except typer.Exit:
        raise
    except Exception as exc:
        typer.echo(f"Error: RPC {method} failed against {url}: {exc}", err=True)
        typer.echo(
            "Hint: is the L2 node running? Set ANIMICA_L2_RPC_URL or pass --rpc-url.",
            err=True,
        )
        raise typer.Exit(1)


def _rpc_quiet(method: str, params: Optional[list] = None, rpc_url: Optional[str] = None) -> Any:
    """RPC call that returns None on failure instead of exiting (for polling)."""
    url = _resolve_l2_rpc_url(rpc_url)
    try:
        return asyncio.run(rpc_call(method, params or [], rpc_url=url))
    except Exception:
        return None


def _pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True)


def _hex32(b: bytes) -> str:
    return "0x" + b.hex()


def _parse_address(value: str, *, what: str = "address") -> bytes:
    """Accept a 32-byte account key as 0x-hex / bare hex, or bech32m anim1…."""
    s = (value or "").strip()
    if not s:
        typer.echo(f"Error: empty {what}.", err=True)
        raise typer.Exit(1)
    if s.startswith("anim1"):
        try:
            from pq.py.address import decode_address

            rec = decode_address(s)
            digest = bytes(rec.digest)
        except Exception as exc:
            typer.echo(f"Error: could not decode {what} {s!r}: {exc}", err=True)
            raise typer.Exit(1)
        if len(digest) != 32:
            typer.echo(f"Error: {what} digest is not 32 bytes.", err=True)
            raise typer.Exit(1)
        return digest
    hex_part = s[2:] if s.lower().startswith("0x") else s
    try:
        raw = bytes.fromhex(hex_part)
    except ValueError:
        typer.echo(f"Error: {what} must be anim1… bech32m or 32-byte hex, got {s!r}.", err=True)
        raise typer.Exit(1)
    if len(raw) != 32:
        typer.echo(f"Error: {what} must be exactly 32 bytes ({len(raw)} given).", err=True)
        raise typer.Exit(1)
    return raw


def _parse_amount(value: str, *, nanos: bool, what: str = "amount") -> int:
    """Exact integer nanos from an ANM decimal (or raw nanos with --nanos)."""
    s = (value or "").strip().replace("_", "")
    try:
        if nanos:
            amount = int(s, 10)
        else:
            from l2.constants import NANOS_PER_ANM

            d = Decimal(s) * Decimal(NANOS_PER_ANM)
            if d != d.to_integral_value():
                typer.echo(
                    f"Error: {what} {value!r} is finer than 1 nano (1e-9 ANM).", err=True
                )
                raise typer.Exit(1)
            amount = int(d)
    except typer.Exit:
        raise
    except (InvalidOperation, ValueError):
        typer.echo(f"Error: could not parse {what} {value!r}.", err=True)
        raise typer.Exit(1)
    if amount <= 0:
        typer.echo(f"Error: {what} must be positive.", err=True)
        raise typer.Exit(1)
    return amount


def _format_anm(nanos_value: int) -> str:
    from l2.constants import NANOS_PER_ANM

    return str(Decimal(nanos_value) / Decimal(NANOS_PER_ANM))


def _load_keypair(key: Optional[str]) -> Tuple[bytes, bytes, bytes]:
    """(sk, pk, l2_address) from a 32-byte hex seed (--key / ANIMICA_L2_KEY)."""
    seed_hex = (key or os.environ.get(KEY_ENV) or "").strip()
    if not seed_hex:
        typer.echo(
            "Error: no signing key. Pass --key <32-byte-hex-seed> or set "
            f"{KEY_ENV}. (Devnet/testing only — production keys belong in the "
            "Animica wallet keystore, not on the command line.)",
            err=True,
        )
        raise typer.Exit(1)
    if seed_hex.lower().startswith("0x"):
        seed_hex = seed_hex[2:]
    try:
        seed = bytes.fromhex(seed_hex)
    except ValueError:
        typer.echo("Error: --key must be hex.", err=True)
        raise typer.Exit(1)
    if len(seed) != 32:
        typer.echo(f"Error: --key seed must be 32 bytes ({len(seed)} given).", err=True)
        raise typer.Exit(1)
    from l2 import tx as l2tx
    from pq.py.algs import ml_dsa_65

    sk, pk = ml_dsa_65.keypair(seed)
    return sk, pk, l2tx.address_from_pubkey(pk)


def _resolve_chain_id(l2_chain_id: Optional[int], rpc_url: Optional[str]) -> int:
    if l2_chain_id is not None:
        return int(l2_chain_id)
    cid = _rpc("l2_chainId", [], rpc_url)
    return int(cid)


def _resolve_nonce(sender: bytes, nonce: Optional[int], rpc_url: Optional[str]) -> int:
    if nonce is not None:
        return int(nonce)
    res = _rpc("l2_getNonce", [_hex32(sender)], rpc_url)
    return int(res["pendingNonce"])


def _fill_fee(tx: Any, fee_override: Optional[int]) -> int:
    """Set tx.fee to the protocol-required fee (or a user ceiling >= it).

    The signed fee is a sender-authorized ceiling; execution only takes the
    marginal schedule fee. FeeSchedule is consensus-fixed, so computing it
    locally matches the sequencer exactly. The fee field itself is part of the
    fee-bearing body (varint), so iterate to a fixed point.
    """
    from l2.fees import FeeSchedule

    sched = FeeSchedule()
    if fee_override is not None:
        tx.fee = int(fee_override)
        required = sched.fee_for(tx)
        if tx.fee < required:
            typer.echo(
                f"Error: --fee {fee_override} is below the required fee "
                f"{required} nanos for this tx.",
                err=True,
            )
            raise typer.Exit(1)
        return tx.fee
    for _ in range(6):
        required = sched.fee_for(tx)
        if required == tx.fee:
            break
        tx.fee = required
    return tx.fee


def _sign(tx: Any, sk: bytes, pk: bytes) -> bytes:
    from pq.py.algs import ml_dsa_65

    tx.pubkey = pk
    tx.signature = ml_dsa_65.sign(sk, tx.signing_hash())
    return tx.encode()


def _submit_raw(raw: bytes, rpc_url: Optional[str]) -> str:
    return str(_rpc("l2_sendRawTransaction", [_hex32(raw)], rpc_url))


def _await_lifecycle(txid_hex: str, rpc_url: Optional[str], wait_s: float) -> None:
    """Poll l2_getTransaction, printing each lifecycle transition."""
    deadline = time.time() + max(0.0, wait_s)
    last: Optional[str] = None
    while True:
        rec = _rpc_quiet("l2_getTransaction", [txid_hex], rpc_url)
        if isinstance(rec, dict):
            status = rec.get("status")
            if status and status != last:
                line = f"  lifecycle: {status}"
                batch = rec.get("batch")
                if isinstance(batch, int) and batch >= 0:
                    line += f" (batch {batch})"
                reason = rec.get("reason")
                if reason:
                    line += f" — {reason}"
                typer.echo(line)
                last = status
            if status in _TERMINAL_STATES:
                return
        if time.time() >= deadline:
            return
        time.sleep(0.2)


def _check_from_matches(from_addr: Optional[str], sender: bytes) -> None:
    if from_addr is None:
        return
    given = _parse_address(from_addr, what="--from address")
    if given != sender:
        typer.echo(
            f"Error: --from {_hex32(given)} does not match the address derived "
            f"from --key ({_hex32(sender)}). L2 requires the pubkey to hash to "
            "the sender, so a mismatched key cannot sign for that account.",
            err=True,
        )
        raise typer.Exit(1)


# ── read-only commands ───────────────────────────────────────────────────────


@app.command("status")
def status(
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar=L2_RPC_ENV, help="L2 JSON-RPC endpoint."),
    json_output: bool = typer.Option(False, "--json", help="Emit one combined JSON document."),
) -> None:
    """L2 node, sequencer, and throughput status."""
    node_status = _rpc("l2_status", [], rpc_url)
    seq_status = _rpc("l2_getSequencerStatus", [], rpc_url)
    tps = _rpc("l2_getTPS", [], rpc_url)
    if json_output:
        typer.echo(_pretty({"status": node_status, "sequencer": seq_status, "tps": tps}))
        return
    typer.secho("Animica L2 status", bold=True)
    typer.echo(f"  enabled:         {node_status.get('enabled')}")
    typer.echo(f"  mode:            {node_status.get('mode')}")
    typer.echo(f"  l2 chain id:     {node_status.get('l2ChainId')}")
    typer.echo(f"  settlement:      {node_status.get('settlementMode')}")
    typer.echo(f"  head batch:      {node_status.get('headBatch')}")
    typer.echo(f"  state root:      {node_status.get('stateRoot')}")
    typer.echo(f"  pending txs:     {seq_status.get('pending')}")
    typer.echo(f"  sig backend:     {seq_status.get('sigBackend')} x{seq_status.get('sigWorkers')} workers")
    bridge = node_status.get("bridge")
    if isinstance(bridge, dict):
        typer.echo("  bridge:")
        for k in sorted(bridge):
            typer.echo(f"    {k}: {bridge[k]}")
    typer.echo("  throughput:")
    for k in sorted(tps):
        typer.echo(f"    {k}: {tps[k]}")


@app.command("balance")
def balance(
    address: str = typer.Argument(..., help="Account: anim1… bech32m or 32-byte 0x-hex."),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar=L2_RPC_ENV, help="L2 JSON-RPC endpoint."),
    json_output: bool = typer.Option(False, "--json", help="Emit JSON."),
) -> None:
    """Instant L2 ANM balance and nonce for an address."""
    addr = _parse_address(address)
    res = _rpc("l2_getBalance", [_hex32(addr)], rpc_url)
    if json_output:
        typer.echo(_pretty(res))
        return
    nanos_bal = int(res["balance"])
    typer.echo(f"address:       {res['address']}")
    typer.echo(f"balance:       {_format_anm(nanos_bal)} ANM ({nanos_bal} nanos)")
    typer.echo(f"nonce:         {res['nonce']}")
    typer.echo(f"pending nonce: {res['pendingNonce']}")


@app.command("batch")
def batch(
    number: int = typer.Argument(..., help="Batch number."),
    data: bool = typer.Option(False, "--data", help="Also fetch the DA blob (0x-hex)."),
    verify: bool = typer.Option(False, "--verify", help="Re-verify the batch's DA blob against its proof."),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar=L2_RPC_ENV, help="L2 JSON-RPC endpoint."),
) -> None:
    """Batch header by number (optionally its DA blob and a trustless re-check)."""
    header = _rpc("l2_getBatch", [number], rpc_url)
    if header is None:
        typer.echo(f"Batch {number} not found.", err=True)
        raise typer.Exit(1)
    out: dict = {"header": header}
    if data:
        out["data"] = _rpc("l2_getBatchData", [number], rpc_url)
    if verify:
        out["verify"] = _rpc("l2_verifyBatch", [number], rpc_url)
    typer.echo(_pretty(out))


@app.command("proof")
def proof(
    number: int = typer.Argument(..., help="Batch number."),
    verify: bool = typer.Option(False, "--verify", help="Also re-verify the batch from its DA blob."),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar=L2_RPC_ENV, help="L2 JSON-RPC endpoint."),
) -> None:
    """Proof status for a batch."""
    res = _rpc("l2_getProofStatus", [number], rpc_url)
    if verify:
        res = {"proof": res, "verify": _rpc("l2_verifyBatch", [number], rpc_url)}
    typer.echo(_pretty(res))


@app.command("sync")
def sync(
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar=L2_RPC_ENV, help="L2 JSON-RPC endpoint."),
) -> None:
    """L2 head/sync status and throughput snapshot."""
    res = _rpc("l2_getSyncStatus", [], rpc_url)
    tps = _rpc("l2_getTPS", [], rpc_url)
    typer.echo(_pretty({"sync": res, "tps": tps}))


@app.command("tx")
def tx_status(
    txid: str = typer.Argument(..., help="Transaction id (0x-hex)."),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar=L2_RPC_ENV, help="L2 JSON-RPC endpoint."),
) -> None:
    """Lifecycle status + receipt of an L2 tx by id."""
    rec = _rpc("l2_getTransaction", [txid], rpc_url)
    receipt = _rpc_quiet("l2_getReceipt", [txid], rpc_url)
    typer.echo(_pretty({"transaction": rec, "receipt": receipt}))


# ── value-moving commands ────────────────────────────────────────────────────


@app.command("send")
def send(
    to: str = typer.Option(..., "--to", help="Recipient: anim1… or 32-byte 0x-hex."),
    amount: str = typer.Option(..., "--amount", help="Amount in ANM (decimal), or nanos with --nanos."),
    key: Optional[str] = typer.Option(
        None,
        "--key",
        envvar=KEY_ENV,
        help="ML-DSA-65 32-byte hex seed (devnet/testing; production keys live in the wallet keystore).",
    ),
    from_addr: Optional[str] = typer.Option(
        None, "--from", help="Expected sender address; must match the --key-derived address."
    ),
    nanos: bool = typer.Option(False, "--nanos", help="Interpret --amount as raw integer nanos."),
    memo: Optional[str] = typer.Option(None, "--memo", help="Optional memo (UTF-8, max 256 bytes)."),
    pay: bool = typer.Option(False, "--pay", help="Send as a PAY tx (Animica-Pay memo semantics) instead of TRANSFER."),
    fee: Optional[int] = typer.Option(None, "--fee", help="Fee ceiling in nanos (default: exact protocol fee)."),
    nonce: Optional[int] = typer.Option(None, "--nonce", help="Explicit nonce (default: pending nonce from RPC)."),
    expiry: int = typer.Option(0, "--expiry", help="L2 batch height after which the tx is invalid (0 = none)."),
    l2_chain_id: Optional[int] = typer.Option(None, "--l2-chain-id", help="Override L2 chain id (default: from RPC)."),
    wait: float = typer.Option(3.0, "--wait", help="Seconds to poll the tx lifecycle after submission."),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar=L2_RPC_ENV, help="L2 JSON-RPC endpoint."),
) -> None:
    """Sign an L2 transfer locally and submit it to the sequencer."""
    from l2 import tx as l2tx
    from l2.constants import MAX_MEMO_BYTES, SigScheme, TxType

    recipient = _parse_address(to, what="--to address")
    amount_nanos = _parse_amount(amount, nanos=nanos)
    memo_bytes = (memo or "").encode("utf-8")
    if len(memo_bytes) > MAX_MEMO_BYTES:
        typer.echo(f"Error: memo exceeds {MAX_MEMO_BYTES} bytes.", err=True)
        raise typer.Exit(1)

    sk, pk, sender = _load_keypair(key)
    _check_from_matches(from_addr, sender)
    chain_id = _resolve_chain_id(l2_chain_id, rpc_url)
    tx_nonce = _resolve_nonce(sender, nonce, rpc_url)

    tx = l2tx.L2Tx(
        version=1,
        l2_chain_id=chain_id,
        tx_type=TxType.PAY if pay else TxType.TRANSFER,
        sender=sender,
        nonce=tx_nonce,
        fee=0,
        expiry=expiry,
        payload=l2tx.TransferPayload(recipient, amount_nanos, memo_bytes),
        sig_scheme=SigScheme.ML_DSA_65,
    )
    tx_fee = _fill_fee(tx, fee)
    raw = _sign(tx, sk, pk)

    typer.echo(f"sender:  {_hex32(sender)}")
    typer.echo(f"to:      {_hex32(recipient)}")
    typer.echo(f"amount:  {_format_anm(amount_nanos)} ANM ({amount_nanos} nanos)")
    typer.echo(f"fee:     {tx_fee} nanos   nonce: {tx_nonce}   chain: {chain_id}")
    txid = _submit_raw(raw, rpc_url)
    typer.secho(f"txid:    {txid}", fg=typer.colors.GREEN, bold=True)
    if wait > 0:
        _await_lifecycle(txid, rpc_url, wait)


@app.command("send-many")
def send_many(
    pay: List[str] = typer.Option(
        [], "--pay", help="Recipient payment as ADDR:AMOUNT (repeatable)."
    ),
    file: Optional[str] = typer.Option(
        None, "--file", help="File with one 'ADDR AMOUNT' (or 'ADDR,AMOUNT') per line; # comments allowed."
    ),
    key: Optional[str] = typer.Option(None, "--key", envvar=KEY_ENV, help="ML-DSA-65 32-byte hex seed."),
    nanos: bool = typer.Option(False, "--nanos", help="Interpret amounts as raw integer nanos."),
    fee: Optional[int] = typer.Option(None, "--fee", help="Fee ceiling in nanos (default: exact protocol fee)."),
    nonce: Optional[int] = typer.Option(None, "--nonce", help="Explicit nonce."),
    expiry: int = typer.Option(0, "--expiry", help="L2 batch height after which the tx is invalid (0 = none)."),
    l2_chain_id: Optional[int] = typer.Option(None, "--l2-chain-id", help="Override L2 chain id."),
    wait: float = typer.Option(3.0, "--wait", help="Seconds to poll the tx lifecycle after submission."),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar=L2_RPC_ENV, help="L2 JSON-RPC endpoint."),
) -> None:
    """One BATCH_PAYMENT tx: a single signature authorizes many payouts."""
    from l2 import tx as l2tx
    from l2.constants import MAX_BATCH_PAYMENT_RECIPIENTS, SigScheme, TxType

    entries: List[Tuple[str, str]] = []
    for item in pay:
        if ":" not in item:
            typer.echo(f"Error: --pay expects ADDR:AMOUNT, got {item!r}.", err=True)
            raise typer.Exit(1)
        addr_s, amount_s = item.rsplit(":", 1)
        entries.append((addr_s.strip(), amount_s.strip()))
    if file:
        try:
            with open(file, "r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#"):
                        continue
                    parts = stripped.replace(",", " ").split()
                    if len(parts) != 2:
                        typer.echo(f"Error: {file}:{lineno}: expected 'ADDR AMOUNT'.", err=True)
                        raise typer.Exit(1)
                    entries.append((parts[0], parts[1]))
        except OSError as exc:
            typer.echo(f"Error: could not read {file}: {exc}", err=True)
            raise typer.Exit(1)
    if not entries:
        typer.echo("Error: no payments given (use --pay ADDR:AMOUNT and/or --file).", err=True)
        raise typer.Exit(1)
    if len(entries) > MAX_BATCH_PAYMENT_RECIPIENTS:
        typer.echo(
            f"Error: {len(entries)} recipients exceeds the protocol limit of "
            f"{MAX_BATCH_PAYMENT_RECIPIENTS} per BATCH_PAYMENT.",
            err=True,
        )
        raise typer.Exit(1)

    payments: List[Tuple[bytes, int]] = []
    total = 0
    for addr_s, amount_s in entries:
        recipient = _parse_address(addr_s, what="recipient")
        amount_nanos = _parse_amount(amount_s, nanos=nanos, what=f"amount for {addr_s}")
        payments.append((recipient, amount_nanos))
        total += amount_nanos

    sk, pk, sender = _load_keypair(key)
    chain_id = _resolve_chain_id(l2_chain_id, rpc_url)
    tx_nonce = _resolve_nonce(sender, nonce, rpc_url)

    tx = l2tx.L2Tx(
        version=1,
        l2_chain_id=chain_id,
        tx_type=TxType.BATCH_PAYMENT,
        sender=sender,
        nonce=tx_nonce,
        fee=0,
        expiry=expiry,
        payload=l2tx.BatchPaymentPayload(payments),
        sig_scheme=SigScheme.ML_DSA_65,
    )
    tx_fee = _fill_fee(tx, fee)
    raw = _sign(tx, sk, pk)

    typer.echo(f"sender:     {_hex32(sender)}")
    typer.echo(f"recipients: {len(payments)}")
    typer.echo(f"total:      {_format_anm(total)} ANM ({total} nanos)")
    typer.echo(f"fee:        {tx_fee} nanos   nonce: {tx_nonce}   chain: {chain_id}")
    txid = _submit_raw(raw, rpc_url)
    typer.secho(f"txid:       {txid}", fg=typer.colors.GREEN, bold=True)
    if wait > 0:
        _await_lifecycle(txid, rpc_url, wait)


# ── bridge commands ──────────────────────────────────────────────────────────


@app.command("deposit")
def deposit(
    deposit_id: Optional[str] = typer.Argument(
        None, help="Deposit id (0x-hex) to look up. Omit for how-to + bridge info."
    ),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar=L2_RPC_ENV, help="L2 JSON-RPC endpoint."),
) -> None:
    """Look up an L1→L2 deposit, or show how deposits work."""
    if deposit_id:
        rec = _rpc("l2_getDeposit", [deposit_id], rpc_url)
        if rec is None:
            typer.echo(f"Deposit {deposit_id} not found (not yet observed on L1?).", err=True)
            raise typer.Exit(1)
        typer.echo(_pretty(rec))
        return
    node_status = _rpc("l2_status", [], rpc_url)
    bridge = node_status.get("bridge") if isinstance(node_status, dict) else None
    typer.secho("L1 → L2 deposits", bold=True)
    typer.echo(
        "  1. Send ANM on L1 to the bridge address; the deposit locks there.\n"
        "  2. Wait for L1 finality — deposits are only credited once FINALIZED,\n"
        "     so an L1 reorg can never create unbacked L2 ANM.\n"
        "  3. The sequencer mints a DEPOSIT_CLAIM crediting your L2 account.\n"
        "  Track a specific deposit with: animica l2 deposit <deposit-id>"
    )
    bridge_addr = os.environ.get("ANIMICA_L2_BRIDGE_ADDRESS")
    if not bridge_addr:
        try:
            from l2.config import L2Config

            bridge_addr = L2Config.from_env().bridge_address or None
        except Exception:
            bridge_addr = None
    if bridge_addr:
        typer.echo(f"  bridge address: {bridge_addr}")
    if isinstance(bridge, dict):
        typer.echo("  bridge state:")
        for k in sorted(bridge):
            typer.echo(f"    {k}: {bridge[k]}")


@app.command("withdraw")
def withdraw(
    to_l1: str = typer.Option(..., "--to-l1", help="L1 recipient: anim1… or 32-byte 0x-hex."),
    amount: str = typer.Option(..., "--amount", help="Amount in ANM (decimal), or nanos with --nanos."),
    key: Optional[str] = typer.Option(None, "--key", envvar=KEY_ENV, help="ML-DSA-65 32-byte hex seed."),
    nanos: bool = typer.Option(False, "--nanos", help="Interpret --amount as raw integer nanos."),
    fee: Optional[int] = typer.Option(None, "--fee", help="Fee ceiling in nanos (default: exact protocol fee)."),
    nonce: Optional[int] = typer.Option(None, "--nonce", help="Explicit nonce."),
    expiry: int = typer.Option(0, "--expiry", help="L2 batch height after which the tx is invalid (0 = none)."),
    l2_chain_id: Optional[int] = typer.Option(None, "--l2-chain-id", help="Override L2 chain id."),
    wait: float = typer.Option(3.0, "--wait", help="Seconds to poll the tx lifecycle after submission."),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar=L2_RPC_ENV, help="L2 JSON-RPC endpoint."),
) -> None:
    """Burn ANM on L2 and unlock it as claimable on L1 (normal withdrawal)."""
    from l2 import tx as l2tx
    from l2.constants import SigScheme, TxType

    l1_recipient = _parse_address(to_l1, what="--to-l1 address")
    amount_nanos = _parse_amount(amount, nanos=nanos)
    sk, pk, sender = _load_keypair(key)
    chain_id = _resolve_chain_id(l2_chain_id, rpc_url)
    tx_nonce = _resolve_nonce(sender, nonce, rpc_url)

    tx = l2tx.L2Tx(
        version=1,
        l2_chain_id=chain_id,
        tx_type=TxType.WITHDRAW,
        sender=sender,
        nonce=tx_nonce,
        fee=0,
        expiry=expiry,
        payload=l2tx.WithdrawPayload(l1_recipient, amount_nanos),
        sig_scheme=SigScheme.ML_DSA_65,
    )
    tx_fee = _fill_fee(tx, fee)
    raw = _sign(tx, sk, pk)

    typer.echo(f"sender:       {_hex32(sender)}")
    typer.echo(f"l1 recipient: {_hex32(l1_recipient)}")
    typer.echo(f"amount:       {_format_anm(amount_nanos)} ANM ({amount_nanos} nanos)")
    typer.echo(f"fee:          {tx_fee} nanos   nonce: {tx_nonce}   chain: {chain_id}")
    txid = _submit_raw(raw, rpc_url)
    typer.secho(f"txid:         {txid}", fg=typer.colors.GREEN, bold=True)
    from l2.bridge import Bridge

    nullifier = Bridge.make_nullifier(bytes.fromhex(txid[2:]))
    typer.echo(f"nullifier:    {_hex32(nullifier)}")
    typer.echo(
        "After the containing batch finalizes on L1, fetch the claim data with "
        "l2_getWithdrawalProof using the nullifier above."
    )
    if wait > 0:
        _await_lifecycle(txid, rpc_url, wait)


@app.command("force-withdraw")
def force_withdraw(
    to_l1: str = typer.Option(..., "--to-l1", help="L1 recipient: anim1… or 32-byte 0x-hex."),
    amount: str = typer.Option(..., "--amount", help="Amount in ANM (decimal), or nanos with --nanos."),
    key: Optional[str] = typer.Option(None, "--key", envvar=KEY_ENV, help="ML-DSA-65 32-byte hex seed."),
    nanos: bool = typer.Option(False, "--nanos", help="Interpret --amount as raw integer nanos."),
    fee: Optional[int] = typer.Option(None, "--fee", help="Fee ceiling in nanos (default: exact protocol fee)."),
    nonce: Optional[int] = typer.Option(
        None, "--nonce", help="Explicit nonce (required if the L2 RPC is unreachable — the escape hatch exists for exactly that case)."
    ),
    expiry: int = typer.Option(0, "--expiry", help="L2 batch height after which the tx is invalid (0 = none)."),
    l2_chain_id: Optional[int] = typer.Option(
        None, "--l2-chain-id", help="L2 chain id (required if the L2 RPC is unreachable)."
    ),
    out: Optional[str] = typer.Option(None, "--out", help="Write the raw escape-hatch payload (hex) to this file."),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar=L2_RPC_ENV, help="L2 JSON-RPC endpoint."),
) -> None:
    """Build the L1 escape-hatch payload: a signed L2 withdrawal to embed in an
    L1 tx to the bridge address. The sequencer MUST include it within the forced
    window or the L2 halts for withdrawals — this works even if the sequencer
    censors you or the L2 RPC is down (pass --nonce and --l2-chain-id then)."""
    from l2 import tx as l2tx
    from l2.constants import SigScheme, TxType

    l1_recipient = _parse_address(to_l1, what="--to-l1 address")
    amount_nanos = _parse_amount(amount, nanos=nanos)
    sk, pk, sender = _load_keypair(key)

    # Best-effort RPC for chain id / nonce; the whole point of the escape hatch
    # is working without a cooperative sequencer, so explicit flags win.
    if l2_chain_id is not None:
        chain_id = int(l2_chain_id)
    else:
        cid = _rpc_quiet("l2_chainId", [], rpc_url)
        if cid is None:
            typer.echo(
                "Error: L2 RPC unreachable and no --l2-chain-id given. Pass "
                "--l2-chain-id (and --nonce) to build the payload offline.",
                err=True,
            )
            raise typer.Exit(1)
        chain_id = int(cid)
    if nonce is not None:
        tx_nonce = int(nonce)
    else:
        res = _rpc_quiet("l2_getNonce", [_hex32(sender)], rpc_url)
        if not isinstance(res, dict):
            typer.echo(
                "Error: L2 RPC unreachable and no --nonce given. Pass --nonce "
                "(your last confirmed nonce) to build the payload offline.",
                err=True,
            )
            raise typer.Exit(1)
        tx_nonce = int(res["pendingNonce"])

    tx = l2tx.L2Tx(
        version=1,
        l2_chain_id=chain_id,
        tx_type=TxType.WITHDRAW,
        sender=sender,
        nonce=tx_nonce,
        fee=0,
        expiry=expiry,
        payload=l2tx.WithdrawPayload(l1_recipient, amount_nanos),
        sig_scheme=SigScheme.ML_DSA_65,
    )
    tx_fee = _fill_fee(tx, fee)
    raw = _sign(tx, sk, pk)
    request_id = hashlib.sha3_256(b"animica.l2.forced.v1" + raw).digest()

    bridge_addr = os.environ.get("ANIMICA_L2_BRIDGE_ADDRESS")
    if not bridge_addr:
        try:
            from l2.config import L2Config

            bridge_addr = L2Config.from_env().bridge_address or None
        except Exception:
            bridge_addr = None

    typer.secho("Forced-withdrawal escape-hatch payload built.", bold=True)
    typer.echo(f"sender:       {_hex32(sender)}")
    typer.echo(f"l1 recipient: {_hex32(l1_recipient)}")
    typer.echo(f"amount:       {_format_anm(amount_nanos)} ANM ({amount_nanos} nanos)")
    typer.echo(f"fee:          {tx_fee} nanos   nonce: {tx_nonce}   chain: {chain_id}")
    typer.echo(f"txid:         {_hex32(tx.txid())}")
    typer.echo(f"request id:   {_hex32(request_id)}")
    if out:
        try:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(_hex32(raw) + "\n")
        except OSError as exc:
            typer.echo(f"Error: could not write {out}: {exc}", err=True)
            raise typer.Exit(1)
        typer.echo(f"payload:      written to {out} ({len(raw)} bytes)")
    else:
        typer.echo(f"payload:      {_hex32(raw)}")
    typer.echo("")
    typer.echo("Submit this payload as the data of an L1 transaction to the bridge address:")
    typer.echo(f"  bridge address: {bridge_addr or '(set ANIMICA_L2_BRIDGE_ADDRESS / see network params)'}")
    typer.echo(
        "  e.g. animica tx send --to <bridge> --data <payload>\n"
        "The sequencer must include the withdrawal within the forced-inclusion "
        "window; validators enforce it, so this exit needs no sequencer cooperation."
    )


# ── bench / doctor ───────────────────────────────────────────────────────────


def _bench_mainnet_guard(i_understand: bool) -> None:
    if os.environ.get("ANIMICA_NETWORK", "").strip().lower() == "mainnet" and not i_understand:
        typer.echo(
            "Refusing to bench while ANIMICA_NETWORK=mainnet. The local bench "
            "runs an ephemeral in-memory devnet sequencer and cannot touch "
            "mainnet state, but rerun with --i-understand to acknowledge the "
            "environment, or unset ANIMICA_NETWORK.",
            err=True,
        )
        raise typer.Exit(1)


def _run_local_bench(
    workload: str,
    count: int,
    workers: int,
    accounts_n: Optional[int],
    seed: int,
    contention: int,
) -> Any:
    """One local bench run. Without --accounts this is exactly l2.bench.run_once;
    with --accounts it mirrors run_once against the same fixed interfaces but
    with an explicit sender-pool size. Both use an ephemeral in-memory devnet
    sequencer (L2_CHAIN_ID_DEVNET) — mainnet is unreachable by construction."""
    from l2 import bench as l2bench

    if accounts_n is None:
        return l2bench.run_once(workload, count, workers, seed=seed, contention=contention)

    from l2.batch import ClosurePolicy
    from l2.constants import L2_CHAIN_ID_DEVNET, SettlementMode, TxType
    from l2.crypto import get_verifier, reset_verifier_for_tests
    from l2.sequencer import Sequencer, SequencerConfig

    accounts = l2bench.make_accounts(min(accounts_n, 256), seed)
    reset_verifier_for_tests()
    cfg = SequencerConfig(
        l2_chain_id=L2_CHAIN_ID_DEVNET,
        settlement_mode=SettlementMode.VALIDITY,
        exec_workers=workers,
        closure=ClosurePolicy(max_txs=10**9, max_bytes=10**12, max_age_ms=0),
    )
    seq = Sequencer(cfg, None, verifier=get_verifier(workers=workers))
    for kp in accounts:
        seq.credit_genesis(kp.addr, 10**18)
    txs = l2bench.build_workload(workload, count, accounts, seed=seed, contention=contention)
    effective_ops = 0
    for t in txs:
        if t.tx_type == TxType.BATCH_PAYMENT:
            effective_ops += len(t.payload.payments)
        else:
            effective_ops += 1

    t0 = time.perf_counter()
    admitted = 0
    per_latency: List[float] = []
    for t in txs:
        s = time.perf_counter()
        try:
            seq.submit(t)
            admitted += 1
        except Exception:
            pass
        per_latency.append((time.perf_counter() - s) * 1000)
    closed = seq.tick(force_close=True)
    dt = time.perf_counter() - t0
    per_latency.sort()

    def pct(p: float) -> float:
        if not per_latency:
            return 0.0
        return per_latency[min(len(per_latency) - 1, int(len(per_latency) * p))]

    return l2bench.BenchResult(
        workload=workload,
        count=admitted,
        workers=workers,
        presigned=True,
        seconds=dt,
        tps=admitted / dt if dt else 0.0,
        effective_ops_per_sec=effective_ops / dt if dt else 0.0,
        sig_backend=seq.verifier.backend_name,
        p50_ms=pct(0.50),
        p95_ms=pct(0.95),
        p99_ms=pct(0.99),
        compressed_bytes=len(closed.da_blob) if closed else 0,
        raw_bytes=sum(len(t.encode()) for t in txs),
    )


_BENCH_COLUMNS = (
    ("workload", 10),
    ("count", 8),
    ("workers", 7),
    ("tps", 10),
    ("eff ops/s", 10),
    ("p50 ms", 8),
    ("p95 ms", 8),
    ("p99 ms", 8),
    ("seconds", 8),
    ("backend", 8),
)


def _print_bench_table(results: List[Any]) -> None:
    header = "  ".join(name.ljust(width) for name, width in _BENCH_COLUMNS)
    typer.secho(header, bold=True)
    typer.echo("-" * len(header))
    for r in results:
        row = (
            r.workload.ljust(10),
            str(r.count).ljust(8),
            str(r.workers).ljust(7),
            f"{r.tps:.1f}".ljust(10),
            f"{r.effective_ops_per_sec:.1f}".ljust(10),
            f"{r.p50_ms:.2f}".ljust(8),
            f"{r.p95_ms:.2f}".ljust(8),
            f"{r.p99_ms:.2f}".ljust(8),
            f"{r.seconds:.3f}".ljust(8),
            r.sig_backend.ljust(8),
        )
        typer.echo("  ".join(row))


@app.command("bench")
def bench(
    tx_type: str = typer.Option(
        "transfers",
        "--tx-type",
        "-t",
        help=f"Workload: one of {', '.join(_BENCH_WORKLOADS)}.",
    ),
    count: int = typer.Option(1000, "--count", "-n", help="Transactions per run."),
    workers: int = typer.Option(0, "--workers", "-w", help="Worker threads (0 = auto: cpu_count - 1)."),
    accounts: Optional[int] = typer.Option(
        None, "--accounts", help="Sender-account pool size (max 256 real PQ keys; default min(count, 256))."
    ),
    duration: Optional[float] = typer.Option(
        None, "--duration", help="Repeat runs until this many seconds have elapsed; report the aggregate."
    ),
    tps: Optional[float] = typer.Option(
        None, "--tps", help="Target TPS (informational — recorded and compared, does not shape load)."
    ),
    contention: int = typer.Option(64, "--contention", help="Hot-account/batch-size knob for hot/batch workloads."),
    seed: int = typer.Option(0, "--seed", help="Deterministic seed for accounts + workload RNG."),
    local: bool = typer.Option(
        True,
        "--local/--no-local",
        help="Local: ephemeral in-memory devnet sequencer. --no-local: submit the workload to a running DEVNET node via RPC.",
    ),
    i_understand: bool = typer.Option(
        False, "--i-understand", help="Acknowledge running the (devnet-only) bench while ANIMICA_NETWORK=mainnet."
    ),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar=L2_RPC_ENV, help="L2 JSON-RPC endpoint (--no-local only)."),
) -> None:
    """Benchmark the real L2 pipeline (sig verify → execute → SMT → DA → proof).

    Never touches mainnet: local runs use an ephemeral in-memory sequencer on
    the devnet chain id; remote runs refuse any target whose l2_chainId is not
    the devnet id."""
    if tx_type not in _BENCH_WORKLOADS:
        typer.echo(f"Error: --tx-type must be one of {', '.join(_BENCH_WORKLOADS)}.", err=True)
        raise typer.Exit(1)
    if count <= 0:
        typer.echo("Error: --count must be positive.", err=True)
        raise typer.Exit(1)
    _bench_mainnet_guard(i_understand)
    if workers <= 0:
        workers = max(1, (os.cpu_count() or 2) - 1)

    summary: dict = {
        "workload": tx_type,
        "count": count,
        "workers": workers,
        "accounts": accounts,
        "seed": seed,
        "contention": contention,
        "local": local,
        "target_tps": tps,
    }

    if local:
        from l2.constants import L2_CHAIN_ID_DEVNET

        summary["l2_chain_id"] = L2_CHAIN_ID_DEVNET
        results: List[Any] = []
        started = time.perf_counter()
        run_seed = seed
        while True:
            results.append(
                _run_local_bench(tx_type, count, workers, accounts, run_seed, contention)
            )
            run_seed += 1
            if duration is None or (time.perf_counter() - started) >= duration:
                break
        _print_bench_table(results)
        total_txs = sum(r.count for r in results)
        total_secs = sum(r.seconds for r in results)
        agg_tps = total_txs / total_secs if total_secs else 0.0
        summary.update(
            {
                "runs": [r.to_json() for r in results],
                "total_txs": total_txs,
                "total_seconds": total_secs,
                "aggregate_tps": agg_tps,
                "sig_backend": results[-1].sig_backend,
            }
        )
        if tps is not None:
            met = agg_tps >= tps
            summary["target_met"] = met
            color = typer.colors.GREEN if met else typer.colors.YELLOW
            typer.secho(
                f"target: {tps:.1f} tps — achieved {agg_tps:.1f} tps ({'met' if met else 'NOT met'})",
                fg=color,
            )
        typer.echo(json.dumps(summary))
        return

    # ── remote mode: submit the workload to a running devnet node ────────────
    from l2 import bench as l2bench
    from l2.constants import L2_CHAIN_ID_DEVNET, L2_CHAIN_ID_MAINNET

    remote_chain = int(_rpc("l2_chainId", [], rpc_url))
    if remote_chain == L2_CHAIN_ID_MAINNET:
        typer.echo(
            "Refusing: the target node reports the MAINNET L2 chain id. "
            "`animica l2 bench` never submits load to mainnet — no override exists.",
            err=True,
        )
        raise typer.Exit(1)
    if remote_chain != L2_CHAIN_ID_DEVNET:
        typer.echo(
            f"Refusing: remote bench requires the devnet L2 chain id "
            f"{L2_CHAIN_ID_DEVNET}; the target reports {remote_chain}. Workload "
            "signatures bind the devnet chain id, so any other target would "
            "reject them (and benching a non-devnet chain is unsafe).",
            err=True,
        )
        raise typer.Exit(1)
    summary["l2_chain_id"] = remote_chain

    typer.echo(f"building + signing {count} {tx_type} txs (seed {seed})...")
    bench_accounts = l2bench.make_accounts(min(accounts or count, 256), seed)
    txs = l2bench.build_workload(tx_type, count, bench_accounts, seed=seed, contention=contention)
    raws = [_hex32(t.encode()) for t in txs]
    typer.echo(
        "note: senders must hold devnet L2 balances for admission to succeed; "
        "rejected counts below include unfunded senders."
    )

    chunk = 500
    accepted = 0
    rejected: List[dict] = []
    t0 = time.perf_counter()
    for i in range(0, len(raws), chunk):
        res = _rpc("l2_sendRawTransactions", [raws[i : i + chunk]], rpc_url)
        accepted += int(res.get("count", 0))
        rejected.extend(res.get("rejected") or [])
    dt = time.perf_counter() - t0
    ingress_tps = accepted / dt if dt else 0.0

    typer.secho("remote ingress result", bold=True)
    typer.echo(f"  accepted: {accepted}/{len(raws)}   rejected: {len(rejected)}")
    typer.echo(f"  seconds:  {dt:.3f}   ingress tps: {ingress_tps:.1f}")
    for rej in rejected[:5]:
        typer.echo(f"  reject sample: #{rej.get('index')}: {rej.get('error')}")
    summary.update(
        {
            "accepted": accepted,
            "rejected": len(rejected),
            "seconds": dt,
            "ingress_tps": ingress_tps,
        }
    )
    if tps is not None:
        summary["target_met"] = ingress_tps >= tps
    typer.echo(json.dumps(summary))


@app.command("doctor")
def doctor(
    workers: int = typer.Option(0, "--workers", "-w", help="Worker threads for the self-benchmark (0 = auto)."),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar=L2_RPC_ENV, help="L2 JSON-RPC endpoint (reachability check)."),
    skip_bench: bool = typer.Option(False, "--skip-bench", help="Skip the 200-tx self-benchmark."),
) -> None:
    """Diagnose the local L2 environment: signature backend, config, and a
    200-tx self-benchmark through the real pipeline."""
    from l2.config import L2Config
    from l2.crypto import get_verifier

    typer.secho("Animica L2 doctor", bold=True)

    # Signature backend (this is the throughput-defining component).
    verifier = get_verifier()
    liboqs_native = verifier.backend_name == "liboqs"
    typer.echo(f"  sig backend:        {verifier.backend_name}")
    typer.echo(f"  sig workers:        {verifier.workers}")
    typer.echo(f"  liboqs native path: {'ACTIVE' if liboqs_native else 'inactive (pure-python fallback)'}")
    if not liboqs_native:
        typer.secho(
        "  warning: pure-python ML-DSA-65 verification is orders of magnitude "
        "slower than liboqs; install liboqs-python for production throughput.",
            fg=typer.colors.YELLOW,
        )
    try:
        import oqs  # noqa: F401

        typer.echo("  liboqs-python:      importable")
    except Exception as exc:
        typer.echo(f"  liboqs-python:      NOT importable ({exc})")

    # Config as the node would read it.
    try:
        cfg = L2Config.from_env()
        typer.echo(f"  l2 enabled (env):   {cfg.enabled}")
        typer.echo(f"  l2 chain id:        {cfg.l2_chain_id}")
        typer.echo(f"  settlement mode:    {cfg.settlement_mode.value}")
        typer.echo(f"  data dir:           {cfg.data_dir}")
        typer.echo(f"  rpc/p2p ports:      {cfg.rpc_port}/{cfg.p2p_port}")
        typer.echo(f"  exec/sig/proof wrk: {cfg.exec_workers}/{cfg.sig_workers}/{cfg.proof_workers}")
        typer.echo(
            f"  batch closure:      max_txs={cfg.batch_max_txs} max_ms={cfg.batch_max_ms} "
            f"max_bytes={cfg.batch_max_bytes}"
        )
    except Exception as exc:
        typer.secho(f"  config:             FAILED to load ({exc})", fg=typer.colors.RED)

    # Node reachability (informational; doctor is about the local environment).
    url = _resolve_l2_rpc_url(rpc_url)
    node_status = _rpc_quiet("l2_status", [], rpc_url)
    if isinstance(node_status, dict):
        typer.echo(
            f"  node at {url}: reachable (head batch {node_status.get('headBatch')}, "
            f"settlement {node_status.get('settlementMode')})"
        )
    else:
        typer.echo(f"  node at {url}: unreachable (local checks continue)")

    if skip_bench:
        return

    # 200-tx self-benchmark through the real pipeline (ephemeral devnet).
    bench_workers = workers if workers > 0 else max(1, (os.cpu_count() or 2) - 1)
    typer.echo(f"  self-benchmark:     200 transfers, {bench_workers} workers ...")
    try:
        from l2.bench import run_once

        result = run_once("transfers", 200, bench_workers, seed=0)
    except Exception as exc:
        typer.secho(f"  self-benchmark:     FAILED ({exc})", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    typer.echo(
        f"  self-benchmark:     {result.tps:.1f} tps end-to-end "
        f"(p50 {result.p50_ms:.2f} ms, p95 {result.p95_ms:.2f} ms, "
        f"backend {result.sig_backend})"
    )
    typer.echo(json.dumps({"doctor_bench": result.to_json(), "liboqs_native": liboqs_native}))
