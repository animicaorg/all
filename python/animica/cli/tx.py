"""
animica.cli.tx — Transaction subcommands.

Goal: make `animica tx send` work end-to-end without relying on pip-installed
`omni-sdk` being present *first*, and without tripping over str/bytes mistakes.

This implementation:
- Resolves network/rpc_url/chain_id via animica.config.load_network_config
- Builds deterministic sign-bytes using animica.tx.signing.build_signable_tx_bytes (CBOR canonical)
- Signs with pq.py when available (with flexible call signatures), otherwise errors
- Encodes a raw transaction envelope as canonical CBOR:
    {"body": <tx_body>, "sig": {"algId":..., "domain":"tx","prehash":"sha3-512","sig":<bytes>}}
- Broadcasts via JSON-RPC tx.sendRawTransaction with {"raw_tx": "0x..."} payload

If your node expects a different raw envelope, the RPC will tell you exactly what
it didn't like (and we can adjust the envelope to match).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import typer

from animica.coin import COIN_SYMBOL, format_amount, to_base_units
from animica.config import load_network_config

# Deterministic CBOR sign-bytes (matches canonical body ordering)
from animica.tx.signing import build_signable_tx_bytes

app = typer.Typer(help="Transaction building/signing/broadcasting", no_args_is_help=True)


# -----------------------------
# Wallet store (minimal reader)
# -----------------------------
DEFAULT_WALLET_STORE = Path.home() / ".animica" / "wallets.json"


@dataclass(frozen=True)
class WalletEntry:
  label: str
  address: str
  alg_id: int
  alg_name: str
  public_key: bytes
  secret_key: bytes


def _hex_to_bytes(v: str) -> bytes:
  v = v.strip()
  if v.startswith("0x"):
    v = v[2:]
  return bytes.fromhex(v)


def _load_wallet_entries(store_path: Path) -> list[WalletEntry]:
  if not store_path.exists():
    raise FileNotFoundError(f"Wallet store not found: {store_path}")

  raw = json.loads(store_path.read_text())
  if isinstance(raw, list):
    items = raw
  elif isinstance(raw, dict):
    # support a few shapes
    items = (
      raw.get("wallets")
      or raw.get("entries")
      or raw.get("accounts")
      or raw.get("items")
      or []
    )
    if not isinstance(items, list):
      items = []
  else:
    items = []

  out: list[WalletEntry] = []
  for i, it in enumerate(items):
    if not isinstance(it, dict):
      continue

    addr = (it.get("address") or it.get("addr") or "").strip()
    if not addr:
      continue

    label = (it.get("label") or it.get("name") or f"wallet-{i}").strip()

    # algorithm id/name fields vary a lot across builds
    alg_id = it.get("alg_id") or it.get("algorithm") or it.get("alg") or 0xFFFF
    try:
      alg_id = int(alg_id)
    except Exception:
      alg_id = 0xFFFF

    alg_name = (
      it.get("alg_name")
      or it.get("algorithm_name")
      or it.get("algorithmName")
      or str(it.get("alg") or "")
      or "unknown"
    )

    pk_hex = it.get("public_key_hex") or it.get("publicKeyHex") or it.get("public_key") or it.get("publicKey")
    sk_hex = it.get("secret_key_hex") or it.get("secretKeyHex") or it.get("secret_key") or it.get("secretKey")
    if not pk_hex or not sk_hex:
      # some stores nest keys
      keys = it.get("keys") or {}
      if isinstance(keys, dict):
        pk_hex = pk_hex or keys.get("public_key_hex") or keys.get("publicKeyHex")
        sk_hex = sk_hex or keys.get("secret_key_hex") or keys.get("secretKeyHex")

    if not pk_hex or not sk_hex:
      # can't sign from this entry
      continue

    try:
      pk = _hex_to_bytes(str(pk_hex))
      sk = _hex_to_bytes(str(sk_hex))
    except Exception:
      continue

    out.append(
      WalletEntry(
        label=label,
        address=addr,
        alg_id=alg_id,
        alg_name=str(alg_name),
        public_key=pk,
        secret_key=sk,
      )
    )

  return out


def _select_sender(entries: list[WalletEntry], sender: str) -> WalletEntry:
  s = sender.strip()

  # Allow numeric index (0-based) like: --from 0
  if s.isdigit():
    idx = int(s)
    if 0 <= idx < len(entries):
      return entries[idx]

  # Match by address
  for e in entries:
    if e.address == s:
      return e

  # Match by label
  for e in entries:
    if e.label == s:
      return e

  raise ValueError(f"Sender not found in wallet store: {sender}")


# -----------------------------
# JSON-RPC
# -----------------------------
def _rpc_request(rpc_url: str, method: str, params: Any) -> Any:
  try:
    import httpx
  except Exception as exc:
    raise RuntimeError("httpx is required for RPC calls. Install it with `pip install httpx`.") from exc

  payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
  try:
    resp = httpx.post(rpc_url, json=payload, timeout=20.0)
    resp.raise_for_status()
    data = resp.json()
  except Exception as exc:
    raise RuntimeError(f"RPC request failed to {rpc_url}: {exc}") from exc

  if "error" in data and data["error"]:
    err = data["error"]
    code = err.get("code", -32000)
    msg = err.get("message", "Unknown error")
    edata = err.get("data")
    raise RuntimeError(f"RPC error {code}: {msg}" + (f" | data={edata}" if edata is not None else ""))

  return data.get("result")


def _hex_int(x: Any) -> int:
  if x is None:
    return 0
  if isinstance(x, int):
    return x
  if isinstance(x, str):
    s = x.strip()
    if s.startswith("0x"):
      return int(s, 16)
    return int(s)
  return int(x)


# -----------------------------
# PQ signing (flexible)
# -----------------------------
def _pq_sign_detached(
  *,
  message: bytes,
  secret_key: bytes,
  public_key: bytes,
  alg_id: int,
  alg_name: str,
  chain_id: int,
  domain: str = "tx",
  prehash: str = "sha3-512",
  verbose: bool = False,
) -> bytes:
  """
  Try hard to sign using pq.py with unknown exact call signature.

  Returns raw signature bytes.
  """
  try:
    # Common exports in this repo
    from pq.py.sign import Signature  # type: ignore
    import pq.py.sign as sign_mod  # type: ignore
    from pq.py.verify import verify_detached  # type: ignore
  except Exception as exc:
    raise RuntimeError("PQ signing unavailable (pq.py not installed/working).") from exc

  fn = getattr(sign_mod, "sign_detached", None) or getattr(sign_mod, "sign", None)
  if fn is None:
    raise RuntimeError("pq.py.sign has no sign_detached/sign function")

  # Try a few call styles (kw + positional)
  attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = [
    ((), dict(message=message, secret_key=secret_key, alg_id=alg_id, domain=domain, prehash=prehash, chain_id=chain_id)),
    ((), dict(msg=message, sk=secret_key, alg_id=alg_id, domain=domain, prehash=prehash, chain_id=chain_id)),
    ((message, secret_key, alg_id, domain, prehash, chain_id), {}),
    ((secret_key, message, alg_id, domain, prehash, chain_id), {}),
    ((message, secret_key, alg_id), dict(domain=domain, prehash=prehash, chain_id=chain_id)),
    ((secret_key, message, alg_id), dict(domain=domain, prehash=prehash, chain_id=chain_id)),
  ]

  sig_bytes: Optional[bytes] = None
  last_err: Optional[Exception] = None

  for args, kwargs in attempts:
    try:
      out = fn(*args, **kwargs)
      if isinstance(out, bytes):
        sig_bytes = out
      elif isinstance(out, str):
        # accept hex output
        sig_bytes = _hex_to_bytes(out)
      elif hasattr(out, "sig"):
        sig_bytes = getattr(out, "sig")
      else:
        # best-effort: bytes(out) if possible
        sig_bytes = bytes(out)
      break
    except Exception as e:
      last_err = e
      continue

  if sig_bytes is None:
    raise RuntimeError(f"Unable to sign with pq.py ({last_err})")

  # Preflight verify using the same Signature envelope tx verification uses
  try:
    sig_obj = Signature(
      alg_id=alg_id,
      alg_name=alg_name,
      domain=domain,
      prehash=prehash,
      sig=sig_bytes,
      chain_id=chain_id,
    )
    ok = verify_detached(sig_obj, message, public_key, chain_id=chain_id)
    if not ok:
      raise RuntimeError("Local PQ signature verification failed (sign/verify mismatch).")
  except Exception as exc:
    raise RuntimeError(f"Local PQ verify failed: {exc}") from exc

  if verbose:
    typer.echo("")
    typer.echo("PQ SIGNATURE DEBUG")
    typer.echo(f"  algorithm: {alg_name} (id={alg_id})")
    typer.echo(f"  pubkey_len: {len(public_key)} bytes")
    typer.echo(f"  sig_len: {len(sig_bytes)} bytes")
    typer.echo(f"  message_len: {len(message)} bytes")
    typer.echo(f"  message_prefix: {message[:16].hex()}")
    typer.echo(f"  chain_id: {chain_id}")

  return sig_bytes


def _encode_raw_tx_envelope(body: dict[str, Any], sig_meta: dict[str, Any]) -> bytes:
  try:
    import cbor2
  except Exception as exc:
    raise RuntimeError("cbor2 is required (pip install cbor2).") from exc

  envelope = {
    "body": body,
    "sig": sig_meta,
  }
  # Canonical CBOR so the node can reproduce bytes deterministically
  return cbor2.dumps(envelope, canonical=True)


# -----------------------------
# CLI: tx send
# -----------------------------
@app.command("send")
def send(
  sender: str = typer.Option(..., "--from", help="Sender address or wallet index/label"),
  to: str = typer.Option(..., "--to", help="Recipient address"),
  value: float = typer.Option(..., "--value", help=f"Amount in {COIN_SYMBOL} (human units)"),
  wallet_store: Path = typer.Option(DEFAULT_WALLET_STORE, "--wallet-store", help="Path to wallets.json"),
  nonce: Optional[int] = typer.Option(None, "--nonce", help="Override account nonce"),
  gas_limit: Optional[int] = typer.Option(None, "--gas", help="Override gas limit"),
  max_fee: Optional[int] = typer.Option(None, "--max-fee", help="Override max fee (base units)"),
  data_hex: Optional[str] = typer.Option(None, "--data-hex", help="Hex calldata/data (0x.. or plain hex)"),
  rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="Override RPC URL", envvar="ANIMICA_RPC_URL"),
  chain_id: Optional[int] = typer.Option(None, "--chain-id", help="Override chain ID", envvar="ANIMICA_CHAIN_ID"),
  network: Optional[str] = typer.Option(None, "--network", help="Override network", envvar="ANIMICA_NETWORK"),
  verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose debug output"),
  dry_run: bool = typer.Option(False, "--dry-run", help="Build/sign but do not broadcast"),
) -> None:
  cfg = load_network_config(network)
  effective_rpc = (rpc_url.strip() if rpc_url and rpc_url.strip() else cfg.rpc_url)
  effective_chain_id = int(chain_id) if chain_id is not None else int(cfg.chain_id)

  if verbose:
    typer.echo("")
    typer.echo("CHAIN CONTEXT DEBUG")
    typer.echo(f"  network: {cfg.name}")
    typer.echo(f"  rpc_url: {effective_rpc}")
    typer.echo(f"  chain_id: {effective_chain_id}")
    typer.echo(f"  chain_id_source: {'cli/env override' if chain_id is not None else 'network config'}")
    typer.echo("")

  # Wallet lookup
  try:
    entries = _load_wallet_entries(wallet_store)
    entry = _select_sender(entries, sender)
  except Exception as exc:
    typer.secho(f"Error loading sender wallet: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)

  # Resolve nonce/gas/fee suggestions
  try:
    if nonce is None:
      nonce_res = _rpc_request(effective_rpc, "state.getTransactionCount", [entry.address])
      nonce = _hex_int(nonce_res)
    if gas_limit is None:
      # Conservative default if node doesn't support estimation
      gas_limit = 21_000
    if max_fee is None:
      gp = _rpc_request(effective_rpc, "state.suggestGasPrice", [])
      max_fee = _hex_int(gp) if gp is not None else 0
  except Exception as exc:
    typer.secho(f"Error fetching account/fee data: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)

  # Encode data
  data_bytes = b""
  if data_hex:
    try:
      data_bytes = _hex_to_bytes(data_hex)
    except Exception as exc:
      typer.secho(f"Invalid --data-hex: {exc}", fg=typer.colors.RED, err=True)
      raise typer.Exit(1)

  # Build tx body (matches animica.tx.signing canonical fields)
  body: dict[str, Any] = {
    "chainId": effective_chain_id,
    "from": entry.address,
    "to": to.strip(),
    "nonce": int(nonce),
    "value": int(to_base_units(value)),
    "gasLimit": int(gas_limit),
    "maxFee": int(max_fee),
    "data": data_bytes,  # MUST be bytes for CBOR
  }

  # Build signable bytes deterministically
  try:
    sign_bytes = build_signable_tx_bytes(body)
  except Exception as exc:
    typer.secho(f"Error building sign-bytes: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)

  # Sign
  try:
    sig_bytes = _pq_sign_detached(
      message=sign_bytes,
      secret_key=entry.secret_key,
      public_key=entry.public_key,
      alg_id=entry.alg_id,
      alg_name=entry.alg_name,
      chain_id=effective_chain_id,
      verbose=verbose,
    )
  except Exception as exc:
    typer.secho(f"Error signing transaction: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)

  sig_meta = {
    "algId": int(entry.alg_id),
    "domain": "tx",
    "prehash": "sha3-512",
    "sig": sig_bytes,
  }

  # Encode raw tx
  try:
    raw_tx = _encode_raw_tx_envelope(body, sig_meta)
  except Exception as exc:
    typer.secho(f"Error encoding raw tx: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)

  if dry_run:
    typer.secho("✓ Built and signed tx (dry-run; not broadcast)", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  from: {entry.address}")
    typer.echo(f"  to:   {body['to']}")
    typer.echo(f"  value: {format_amount(body['value'])}")
    typer.echo(f"  nonce: {body['nonce']}")
    typer.echo(f"  gasLimit: {body['gasLimit']}")
    typer.echo(f"  maxFee: {body['maxFee']}")
    typer.echo(f"  sign_bytes_len: {len(sign_bytes)}")
    typer.echo(f"  raw_tx_len: {len(raw_tx)}")
    typer.echo(f"  raw_tx_prefix: {raw_tx[:16].hex()}")
    raise typer.Exit(0)

  # Broadcast
  try:
    tx_hash = _rpc_request(
      effective_rpc,
      "tx.sendRawTransaction",
      {"raw_tx": "0x" + raw_tx.hex()},
    )
    typer.secho("✓ Transaction sent!", fg=typer.colors.GREEN, bold=True)
    typer.echo(f"  tx_hash: {tx_hash}")
    typer.echo(f"  from:    {entry.address}")
    typer.echo(f"  to:      {body['to']}")
    typer.echo(f"  value:   {format_amount(body['value'])}")
  except Exception as exc:
    typer.secho("=== Transaction Failed ===", fg=typer.colors.RED, bold=True)
    typer.secho(f"{exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


if __name__ == "__main__":
  app()
