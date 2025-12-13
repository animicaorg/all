
from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cbor2
import httpx
import typer

# IMPORTANT: allow either name (repo has had both)
try:
    from pq.py.sign import pq_sign_detached as pq_sign_detached  # type: ignore
except Exception:
    from pq.py.sign import sign_detached as pq_sign_detached  # type: ignore

app = typer.Typer(add_completion=False, help="Transactions")

ANM_DECIMALS = 1_000_000_000  # 1 ANM = 1e9 base units
DEFAULT_GAS_LIMIT = 21_000
DEFAULT_MAX_FEE = 1

DEFAULT_WALLET_FILE = Path.home() / ".animica" / "wallets.json"


@dataclass(frozen=True)
class RpcError(Exception):
    method: str
    code: int
    message: str
    data: Any = None

    def __str__(self) -> str:
        return f"RPC error {self.code}: {self.message} | data={self.data} (method={self.method})"


def _rpc_post(url: str, method: str, params: List[Any]) -> Any:
    payload = {"jsonrpc": "2.0", "id": random.randint(1, 10**9), "method": method, "params": params}
    r = httpx.post(url, json=payload, timeout=30.0)
    r.raise_for_status()
    j = r.json()
    if "error" in j and j["error"] is not None:
        err = j["error"]
        raise RpcError(
            method=method,
            code=int(err.get("code", -1)),
            message=str(err.get("message", "")),
            data=err.get("data"),
        )
    return j.get("result")


def _rpc_call(url: str, method: str, params: List[Any]) -> Any:
    """
    Call method; if node renamed a method, try known aliases.
    """
    aliases = {
        "state.getTransactionCount": ["state.getNonce", "state.getTxCount", "state.nonce"],
        "tx.gasPrice": ["fee.gasPrice", "fee.getGasPrice", "tx.maxFee", "fee.maxFee"],
    }
    try:
        return _rpc_post(url, method, params)
    except RpcError as e:
        if e.code != -32601:
            raise
        for alt in aliases.get(method, []):
            try:
                return _rpc_post(url, alt, params)
            except RpcError as e2:
                if e2.code == -32601:
                    continue
                raise
        raise


def _canonical_cbor(obj: Any) -> bytes:
    # Deterministic map ordering is critical for signatures to verify on-node.
    return cbor2.dumps(obj, canonical=True)


def _load_wallets(wallet_file: Path) -> List[Dict[str, Any]]:
    if not wallet_file.exists():
        raise typer.Exit(code=2)
    try:
        return json.loads(wallet_file.read_text())
    except Exception as e:
        typer.echo(f"Error reading wallet file {wallet_file}: {e}", err=True)
        raise typer.Exit(code=2)


def _find_wallet_entry(wallets: List[Dict[str, Any]], address: str) -> Dict[str, Any]:
    for w in wallets:
        if str(w.get("address", "")).strip() == address.strip():
            return w
    raise typer.BadParameter(f"Address not found in wallets.json: {address}")


def _hex_to_bytes(h: str) -> bytes:
    h2 = h.strip().lower()
    if h2.startswith("0x"):
        h2 = h2[2:]
    return bytes.fromhex(h2)


def _resolve_rpc_url(cli_rpc_url: Optional[str]) -> str:
    if cli_rpc_url:
        return cli_rpc_url
    # default testnet local per your logs
    return os.environ.get("ANIMICA_RPC_URL", "http://127.0.0.1:18546/rpc")


def _resolve_chain_id(url: str, cli_chain_id: Optional[int]) -> Tuple[int, str]:
    if cli_chain_id is not None:
        return int(cli_chain_id), "cli override"
    cid = _rpc_call(url, "chain.getChainId", [])
    return int(cid), "node:chain.getChainId"


def _get_nonce(url: str, addr: str) -> Tuple[int, str]:
    n = _rpc_call(url, "state.getNonce", [addr])
    return int(n), "state.getNonce"


def _pick_max_fee(url: str) -> Tuple[int, str]:
    # If node doesn't expose a fee method, we fall back to a conservative default.
    try:
        v = _rpc_call(url, "tx.maxFee", [])
        return int(v), "tx.maxFee"
    except Exception:
        return DEFAULT_MAX_FEE, f"default({DEFAULT_MAX_FEE})"


@app.command("send")
def send(
    from_addr: str = typer.Option(..., "--from"),
    to_addr: str = typer.Option(..., "--to"),
    value: str = typer.Option(..., "--value", help="Amount in ANM (human units)."),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url"),
    chain_id: Optional[int] = typer.Option(None, "--chain-id"),
    wallet_file: Path = typer.Option(DEFAULT_WALLET_FILE, "--wallet-file"),
    gas_limit: int = typer.Option(DEFAULT_GAS_LIMIT, "--gas-limit"),
    max_fee: Optional[int] = typer.Option(None, "--max-fee"),
    verbose: bool = typer.Option(False, "-v", "--verbose"),
) -> None:
    url = _resolve_rpc_url(rpc_url)
    resolved_chain_id, chain_src = _resolve_chain_id(url, chain_id)

    if verbose:
        typer.echo("")
        typer.echo("CHAIN CONTEXT DEBUG")
        typer.echo(f"  rpc_url: {url}")
        typer.echo(f"  chain_id: {resolved_chain_id}")
        typer.echo(f"  chain_id_source: {chain_src}")
        typer.echo("")

    wallets = _load_wallets(wallet_file)
    sender_entry = _find_wallet_entry(wallets, from_addr)

    alg_name = str(sender_entry.get("alg_name", "")).strip()
    alg_id = int(sender_entry.get("alg_id", 0))

    pk = _hex_to_bytes(str(sender_entry.get("public_key_hex", "")))
    sk = _hex_to_bytes(str(sender_entry.get("secret_key_hex", "")))

    nonce_val, nonce_src = _get_nonce(url, from_addr)
    fee_val, fee_src = _pick_max_fee(url) if max_fee is None else (int(max_fee), "cli override")

    if verbose:
        typer.echo(f"nonce: using {nonce_src}")
        typer.echo(f"maxFee: using {fee_src} => {fee_val}")
        typer.echo("")

    # value in base units
    try:
        human = int(value)
    except Exception:
        raise typer.BadParameter("--value must be an integer ANM amount for now")

    value_units = human * ANM_DECIMALS

    body: Dict[str, Any] = {
        "chainId": int(resolved_chain_id),
        "from": str(from_addr),
        "to": str(to_addr),
        "nonce": int(nonce_val),
        "value": int(value_units),
        "gasLimit": int(gas_limit),
        "maxFee": int(fee_val),
        "data": b"",
    }

    # Deterministic signable bytes (canonical CBOR)
    signable = _canonical_cbor(body)

    # Sign
    domain = "tx"
    prehash = "sha3-256"

    if verbose:
        typer.echo("PQ SIGNATURE DEBUG")
        typer.echo(f"  algorithm: {alg_name} (id={alg_id})")
        typer.echo(f"  domain: {domain}")
        typer.echo(f"  prehash: {prehash}")
        typer.echo(f"  chain_id_in_pq: {resolved_chain_id}")
        typer.echo(f"  pubkey_len: {len(pk)} bytes")
        typer.echo(f"  message_len: {len(signable)} bytes")
        typer.echo(f"  message_prefix: {signable[:16].hex()}")
        typer.echo("")

    sig_env = pq_sign_detached(
        signable,
        alg_name if alg_name else alg_id,
        sk,
        pk=pk,
        domain=domain,
        chain_id=int(resolved_chain_id),
        prehash=prehash,  # type: ignore
        context=b"",
    )

    # Raw tx envelope (canonical CBOR for deterministic node parsing too)
    raw_tx = _canonical_cbor({"sig": sig_env, "tx": body})
    raw_hex = "0x" + raw_tx.hex()

    # Broadcast
    try:
        tx_hash = _rpc_call(url, "tx.sendRawTransaction", [raw_hex])
        typer.echo("=== Transaction Submitted ===")
        typer.echo(f"Tx Hash: {tx_hash}")
        typer.echo(f"From: {from_addr}")
        typer.echo(f"To:   {to_addr}")
        typer.echo(f"Value: {human} ANM")
    except RpcError as e:
        typer.echo("=== Transaction Failed ===", err=True)
        typer.echo(f"Method:  {e.method}", err=True)
        typer.echo(f"Code:    {e.code}", err=True)
        typer.echo(f"Message: {e.message}", err=True)
        raise typer.Exit(code=1)

