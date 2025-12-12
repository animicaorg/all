from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx
import typer

from animica.config import load_network_config

app = typer.Typer(help="Build, sign, and broadcast Animica transactions.")

# ----------------------------
# Constants / helpers
# ----------------------------

BASE_UNITS_PER_ANM = 1_000_000_000  # 1 ANM = 1e9 base units (matches faucet output)

_RPC_ENV = "ANIMICA_RPC_URL"
_WALLET_FILE_ENV = "ANIMICA_WALLETS_FILE"


class RpcError(RuntimeError):
    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


def _pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, sort_keys=True, default=str)


def to_base_units(amount_anm: float) -> int:
    # Use Decimal to avoid float rounding surprises.
    d = (Decimal(str(amount_anm)) * Decimal(BASE_UNITS_PER_ANM)).quantize(
        Decimal("1"), rounding=ROUND_DOWN
    )
    return int(d)


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
    env_url = os.environ.get(_RPC_ENV)
    if env_url and env_url.strip():
        return env_url.strip()
    return load_network_config().rpc_url


def _request_rpc(method: str, params: list[Any], rpc_url: str) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        resp = httpx.post(rpc_url, json=payload, timeout=10.0)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        raise RuntimeError(f"RPC transport error calling {method}: {e}") from e

    if isinstance(data, dict) and "error" in data and data["error"] is not None:
        err = data["error"]
        raise RpcError(
            int(err.get("code", -32000)),
            str(err.get("message", "Unknown error")),
            err.get("data"),
        )

    return data.get("result")


def _rpc_chain_id(rpc_url: str) -> Optional[int]:
    # Node appears to support chain.getChainId (seen in debug output).
    for m in ("chain.getChainId", "net_version", "eth_chainId"):
        try:
            r = _request_rpc(m, [], rpc_url)
            if r is None:
                continue
            if isinstance(r, str):
                return int(r, 16) if r.startswith("0x") else int(r)
            return int(r)
        except Exception:
            continue
    return None


def resolve_chain_id(
    rpc_url: str, cli_chain_id: Optional[int], cfg_chain_id: Optional[int]
) -> Tuple[int, str]:
    if cli_chain_id is not None:
        return int(cli_chain_id), "CLI flag / env"
    detected = _rpc_chain_id(rpc_url)
    if detected is not None:
        return detected, "node auto-detect"
    if cfg_chain_id is not None:
        return int(cfg_chain_id), "network config"
    return 1, "default"


def _warn_if_unsafe_pq_mode() -> None:
    if os.environ.get("ANIMICA_UNSAFE_PQ_FAKE") == "1":
        typer.echo(
            "⚠️  ANIMICA_UNSAFE_PQ_FAKE=1 is set. This is for dev/testing only.",
            err=True,
        )


def _get_default_wallet_path() -> Path:
    return Path.home() / ".animica" / "wallets.json"


def _wallet_file_path(wallet_file: Optional[Path]) -> Path:
    if wallet_file is not None:
        return Path(wallet_file)
    env_path = os.environ.get(_WALLET_FILE_ENV)
    if env_path:
        return Path(env_path)
    return _get_default_wallet_path()


@dataclass
class WalletEntry:
    label: str
    address: str
    alg_id: int
    alg_name: str
    public_key_hex: str
    secret_key_hex: str


def _load_wallet_store(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise RuntimeError(
            f"Wallet store not found at {path}. Create one with: animica wallet create --label <name>"
        )
    return json.loads(path.read_text(encoding="utf-8"))


def _find_wallet_entry(store: Dict[str, Any], identifier: str) -> WalletEntry:
    for e in store.get("wallets", []):
        if e.get("address") == identifier or e.get("label") == identifier:
            return WalletEntry(
                label=str(e.get("label") or ""),
                address=str(e["address"]),
                alg_id=int(e.get("alg_id", 0xFFFF)),
                alg_name=str(e.get("alg_name") or ""),
                public_key_hex=str(e["public_key_hex"]),
                secret_key_hex=str(e["secret_key_hex"]),
            )
    raise RuntimeError(f"Wallet not found for: {identifier!r} (by address or label)")


def _resolve_sender(from_arg: str, wallet_file: Optional[Path]) -> Tuple[str, WalletEntry]:
    store_path = _wallet_file_path(wallet_file)
    store = _load_wallet_store(store_path)
    entry = _find_wallet_entry(store, from_arg)
    return entry.address, entry


def _resolve_destination(to_arg: str) -> str:
    addr = (to_arg or "").strip()
    if not addr.startswith("anim1"):
        raise RuntimeError(f"Invalid destination address: {addr!r}")
    # If pq.py validation exists, use it (but don’t hard-require it).
    try:
        from pq.py.address import validate_address  # type: ignore

        validate_address(addr, expect_hrp="anim")
    except ImportError:
        pass
    return addr


def debug_chain_context(network_name: str, rpc_url: str, chain_id: int, chain_id_source: str) -> None:
    typer.echo("", err=True)
    typer.echo("CHAIN CONTEXT DEBUG", err=True)
    typer.echo(f"  network: {network_name}", err=True)
    typer.echo(f"  rpc_url: {rpc_url}", err=True)
    typer.echo(f"  chain_id: {chain_id}", err=True)
    typer.echo(f"  chain_id_source: {chain_id_source}", err=True)
    typer.echo("", err=True)


def _get_nonce(rpc_url: str, address: str) -> int:
    # Your node does NOT have state.getTransactionCount; try sensible fallbacks.
    candidates = [
        ("state.getNonce", [address]),
        ("state.getAccountNonce", [address]),
        ("state.getTransactionCount", [address]),  # legacy
        ("eth_getTransactionCount", [address, "latest"]),
        ("state.getAccount", [address]),
        ("state.getAccountInfo", [address]),
    ]
    last_err: Optional[Exception] = None

    for method, params in candidates:
        try:
            r = _request_rpc(method, params, rpc_url)
            if r is None:
                continue
            if isinstance(r, dict):
                for k in ("nonce", "transactionCount", "txCount"):
                    if k in r and r[k] is not None:
                        return int(r[k], 16) if isinstance(r[k], str) and str(r[k]).startswith("0x") else int(r[k])
                continue
            if isinstance(r, str):
                return int(r, 16) if r.startswith("0x") else int(r)
            return int(r)
        except Exception as e:
            last_err = e
            continue

    if last_err:
        raise RuntimeError(f"Unable to fetch nonce for {address}: {last_err}")
    return 0


def _get_gas_price(rpc_url: str) -> int:
    # Base-unit gas price (your CLI treats 1 gwei == 1 base unit).
    candidates = [
        ("state.suggestGasPrice", []),
        ("tx.gasPrice", []),
        ("tx.suggestGasPrice", []),
        ("eth_gasPrice", []),
    ]
    for method, params in candidates:
        try:
            r = _request_rpc(method, params, rpc_url)
            if r is None:
                continue
            if isinstance(r, str):
                return int(r, 16) if r.startswith("0x") else int(r)
            return int(r)
        except Exception:
            continue
    return 1


def _canonical_cbor_dumps(obj: Any) -> bytes:
    try:
        import cbor2  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "Missing dependency 'cbor2'. Install it (pip install cbor2) or run ./setup.sh after updating it."
        ) from e

    return cbor2.dumps(obj, canonical=True)


def _pq_sign(alg_name: str, secret_key: bytes, message: bytes) -> bytes:
    # Prefer pq.py if present (keeps behavior consistent with the rest of the repo).
    try:
        from pq.py.signing import sign_detached  # type: ignore

        return sign_detached(secret_key, message, alg_name=alg_name)
    except Exception:
        pass

    # Fallback: oqs/liboqs-python
    try:
        import oqs  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "PQ signing requires 'oqs' (liboqs-python). Re-run setup with --with-pq or install oqs."
        ) from e

    oqs_name = alg_name
    # Common normalization: "dilithium3" -> "Dilithium3"
    if oqs_name.lower().startswith("dilithium"):
        oqs_name = oqs_name[:1].upper() + oqs_name[1:]
    with oqs.Signature(oqs_name) as s:
        return s.sign(message, secret_key)


def _debug_pq_signature(alg_name: str, alg_id: int, pub: bytes, sig: bytes, msg: bytes, chain_id: int) -> None:
    typer.echo("", err=True)
    typer.echo("PQ SIGNATURE DEBUG", err=True)
    typer.echo(f"  algorithm: {alg_name} (id={alg_id})", err=True)
    typer.echo(f"  pubkey_len: {len(pub)} bytes", err=True)
    typer.echo(f"  sig_len: {len(sig)} bytes", err=True)
    typer.echo(f"  message_len: {len(msg)} bytes", err=True)
    typer.echo(f"  message_prefix: {msg[:16].hex()}", err=True)
    typer.echo(f"  chain_id: {chain_id}", err=True)
    typer.echo("", err=True)


# ----------------------------
# Commands
# ----------------------------

@app.command()
def send(
    from_addr: str = typer.Option(..., "--from", help="Sender address or wallet label"),
    to_addr: str = typer.Option(..., "--to", help="Recipient address"),
    value: float = typer.Option(..., "--value", help="Amount to transfer (in ANM)"),
    gas: Optional[int] = typer.Option(None, "--gas", help="Gas limit (default 21000)"),
    gas_price: Optional[float] = typer.Option(
        None, "--gas-price", help="Gas price in gwei (1 gwei = 1 base unit; auto if omitted)"
    ),
    nonce: Optional[int] = typer.Option(None, "--nonce", help="Transaction nonce (auto-fetched if omitted)"),
    chain_id: Optional[int] = typer.Option(
        None, "--chain-id", help="Chain ID (auto / config if omitted)", envvar="ANIMICA_CHAIN_ID"
    ),
    raw_out: Optional[Path] = typer.Option(
        None, "--raw-out", help="Write a signing debug bundle (JSON) to this file (use with --dry-run)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Build/sign but do not broadcast"),
    wallet_file: Optional[Path] = typer.Option(None, "--wallet-file", help="Override wallet store location"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="Override RPC URL", envvar="ANIMICA_RPC_URL"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable verbose debugging"),
) -> None:
    """
    Build, sign, and broadcast a native value transfer transaction (no omni_sdk required).

    Wire format sent to tx.sendRawTransaction is canonical CBOR of:
      { "alg": <int>, "pub": <bytes>, "sig": <bytes>, "tx": <bytes> }

    Where "tx" is canonical CBOR of the transaction body.
    """
    if raw_out and not dry_run:
        raise typer.Exit("--raw-out only works with --dry-run")

    # PQ preflight (keep existing UX)
    try:
        from animica.cli.pq_utils import check_pq_signing_available, get_pq_missing_error_message  # type: ignore

        ok, err = check_pq_signing_available()
        if not ok:
            typer.echo(get_pq_missing_error_message(), err=True)
            if err:
                typer.echo(f"\nAdditional info: {err}", err=True)
            raise typer.Exit(1)
    except Exception:
        # If pq_utils ever goes missing, don’t brick tx send; rely on runtime errors below.
        pass

    _warn_if_unsafe_pq_mode()

    # Resolve addresses / wallet
    sender_address, wallet = _resolve_sender(from_addr, wallet_file)
    dest_address = _resolve_destination(to_addr)

    # Resolve chain context
    url = _resolve_rpc_url(rpc_url)
    cfg = load_network_config()
    resolved_chain_id, chain_id_source = resolve_chain_id(url, chain_id, getattr(cfg, "chain_id", None))

    if verbose:
        debug_chain_context(cfg.name, url, resolved_chain_id, chain_id_source)

    # Nonce / fees
    if nonce is None:
        nonce = _get_nonce(url, sender_address)
    if gas is None:
        gas = 21000
    if gas_price is None:
        gas_price = float(_get_gas_price(url))

    value_units = to_base_units(value)
    max_fee = int(Decimal(str(gas_price)))  # 1 gwei == 1 base unit in this CLI

    # Build tx body (keep keys consistent with existing debug output style)
    tx_body: Dict[str, Any] = {
        "to": dest_address,
        "from": sender_address,
        "value": value_units,
        "data": "0x",
        "gas": int(gas),
        "gasPrice": int(max_fee),
        "nonce": int(nonce),
        "chainId": int(resolved_chain_id),
    }

    tx_body_bytes = _canonical_cbor_dumps(tx_body)

    # Sign
    pub = bytes.fromhex(wallet.public_key_hex)
    sk = bytes.fromhex(wallet.secret_key_hex)

    alg_name = wallet.alg_name or "dilithium3"
    sig = _pq_sign(alg_name, sk, tx_body_bytes)

    if verbose:
        _debug_pq_signature(alg_name, wallet.alg_id, pub, sig, tx_body_bytes, resolved_chain_id)

    # Build signed envelope (CBOR bytes inside CBOR)
    envelope = {"alg": int(wallet.alg_id), "pub": pub, "sig": sig, "tx": tx_body_bytes}
    raw_bytes = _canonical_cbor_dumps(envelope)
    raw_hex = "0x" + raw_bytes.hex()

    if dry_run:
        bundle = {
            "rpc_url": url,
            "chain_id": resolved_chain_id,
            "tx_body": tx_body,
            "tx_body_cbor_hex": "0x" + tx_body_bytes.hex(),
            "envelope_cbor_hex": raw_hex,
            "pubkey_hex": "0x" + pub.hex(),
            "sig_hex": "0x" + sig.hex(),
            "alg_id": int(wallet.alg_id),
            "alg_name": alg_name,
        }
        if raw_out:
            raw_out.write_text(json.dumps(bundle, indent=2), encoding="utf-8")
            typer.echo(f"✓ Wrote dry-run bundle to {raw_out}")
        else:
            typer.echo(_pretty(bundle))
        return

    # Broadcast
    try:
        result = _request_rpc("tx.sendRawTransaction", [raw_hex], url)
        typer.echo("✓ Transaction submitted!")
        typer.echo(f"Result: {result}")
    except RpcError as e:
        # Surface useful method-not-found details etc.
        typer.echo("=== Transaction Failed ===", err=True)
        typer.echo("Method:  tx.sendRawTransaction", err=True)
        typer.echo(f"Code:    {e.code}", err=True)
        typer.echo(f"Message: {e.message}", err=True)
        if e.data is not None:
            typer.echo(f"Data:    {e.data}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error broadcasting tx: {e}", err=True)
        raise typer.Exit(1)
