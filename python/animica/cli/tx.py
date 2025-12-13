from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer

try:
    import requests  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError("Missing dependency: requests (run setup.sh)") from e

try:
    import cbor2  # type: ignore
except Exception as e:  # pragma: no cover
    raise RuntimeError("Missing dependency: cbor2 (run setup.sh)") from e


app = typer.Typer(help="Transactions")


ANM_BASE_UNITS = 1_000_000_000  # 1 ANM = 1e9 base units (matches your faucet output)


class RpcError(RuntimeError):
    def __init__(self, method: str, code: int, message: str, data: Any = None) -> None:
        super().__init__(f"RPC error {code}: {message} | data={data} (method={method})")
        self.method = method
        self.code = code
        self.message = message
        self.data = data


def _rpc_post(url: str, method: str, params: List[Any]) -> Any:
    payload = {"jsonrpc": "2.0", "id": random.randint(1, 1_000_000_000), "method": method, "params": params}
    r = requests.post(url, json=payload, timeout=20)
    j = r.json()
    if "error" in j and j["error"] is not None:
        err = j["error"] or {}
        raise RpcError(method=method, code=int(err.get("code", -1)), message=str(err.get("message", "")), data=err.get("data"))
    return j.get("result")


def _rpc_call(url: str, method: str, params: List[Any]) -> Any:
    """
    Call method, with a couple of gentle fallbacks when nodes expose aliases.
    """
    try:
        return _rpc_post(url, method, params)
    except RpcError as e:
        if e.code != -32601:
            raise

        # Try simple alias forms
        candidates = []
        if "." in method:
            candidates.append(method.replace(".", "_"))
        # Some nodes might expose the final segment only (rare, but harmless to try)
        candidates.append(method.split(".")[-1])

        last: Optional[RpcError] = e
        for m in candidates:
            try:
                return _rpc_post(url, m, params)
            except RpcError as e2:
                last = e2
                if e2.code != -32601:
                    raise
        raise last  # type: ignore[misc]


@dataclass
class WalletKeys:
    alg_name: str
    alg_id: int
    public_key: bytes
    secret_key: bytes


def _default_wallet_file() -> Path:
    p = os.environ.get("ANIMICA_WALLET_FILE")
    if p:
        return Path(p).expanduser()
    return Path.home() / ".animica" / "wallets.json"


def _load_wallets(wallet_file: Path) -> List[Dict[str, Any]]:
    if not wallet_file.exists():
        raise RuntimeError(f"Wallet file not found: {wallet_file}")
    data = json.loads(wallet_file.read_text())
    if isinstance(data, dict) and "wallets" in data and isinstance(data["wallets"], list):
        return data["wallets"]
    if isinstance(data, list):
        return data
    raise RuntimeError(f"Unexpected wallet file format: {wallet_file}")


def _find_wallet_entry(wallets: List[Dict[str, Any]], address: str) -> Dict[str, Any]:
    for w in wallets:
        if w.get("address") == address:
            return w
    raise RuntimeError(f"Wallet address not found in wallets.json: {address}")


def _wallet_keys_from_entry(entry: Dict[str, Any]) -> WalletKeys:
    alg_name = str(entry.get("alg_name") or entry.get("alg") or "")
    alg_id = int(entry.get("alg_id") or entry.get("algId") or 0)
    pk_hex = str(entry.get("public_key_hex") or entry.get("publicKeyHex") or "")
    sk_hex = str(entry.get("secret_key_hex") or entry.get("secretKeyHex") or "")

    if not alg_name or not alg_id:
        raise RuntimeError("Wallet entry missing alg_name/alg_id")
    if not pk_hex or not sk_hex:
        raise RuntimeError("Wallet entry missing public_key_hex/secret_key_hex")

    pk = bytes.fromhex(pk_hex)
    sk = bytes.fromhex(sk_hex)
    return WalletKeys(alg_name=alg_name, alg_id=alg_id, public_key=pk, secret_key=sk)


def _parse_anm_to_base_units(value: str) -> int:
    """
    Accepts integers or decimals (as string), converts to base units.
    """
    try:
        d = Decimal(value)
    except InvalidOperation as e:
        raise RuntimeError(f"Invalid --value: {value}") from e
    if d < 0:
        raise RuntimeError("Value must be >= 0")
    units = int(d * Decimal(ANM_BASE_UNITS))
    return units


def _parse_hex_data(data_hex: Optional[str]) -> bytes:
    if not data_hex:
        return b""
    s = data_hex.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if s == "":
        return b""
    if len(s) % 2 != 0:
        s = "0" + s
    return bytes.fromhex(s)


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    if rpc_url:
        return rpc_url
    env = os.environ.get("ANIMICA_RPC_URL") or os.environ.get("ANIMICA_NODE_RPC_URL")
    if env:
        return env
    # default to your testnet local container mapping used in your logs
    return "http://127.0.0.1:18546/rpc"


def _resolve_chain_id(url: str, override: Optional[int]) -> Tuple[int, str]:
    if override is not None:
        return int(override), "cli override"
    try:
        cid = _rpc_call(url, "chain.getChainId", [])
        return int(cid), "node:chain.getChainId"
    except Exception:
        # fall back to env if node method missing
        env = os.environ.get("ANIMICA_CHAIN_ID")
        if env:
            return int(env), "env:ANIMICA_CHAIN_ID"
        return 2, "default(2)"


def _resolve_nonce(url: str, from_addr: str) -> Tuple[int, str]:
    """
    Prefer state.getNonce (present in your logs). Avoid state.getTransactionCount.
    """
    try:
        n = _rpc_call(url, "state.getNonce", [from_addr])
        return int(n), "state.getNonce"
    except RpcError as e:
        if e.code != -32601:
            raise
    # last-resort default (works for brand new accounts)
    return 0, "default(0)"


def _resolve_max_fee(url: str) -> Tuple[int, str]:
    """
    Your node currently doesn't expose gas.getGasPrice; default to 1.
    """
    return 1, "default(1)"


# --- PQ signing import (handles renames safely) -------------------------------
def _pq_import():
    # New API name
    try:
        from pq.py.sign import pq_sign_detached as _sign  # type: ignore
        from pq.py.sign import pq_verify_detached as _verify  # type: ignore
        return _sign, _verify
    except Exception:
        pass

    # Old API name
    try:
        from pq.py.sign import sign_detached as _sign  # type: ignore
        from pq.py.sign import verify_detached as _verify  # type: ignore
        return _sign, _verify
    except Exception as e:
        raise RuntimeError(
            "PQ signing module not importable. Ensure repo root is on PYTHONPATH and pq/py/sign.py exists."
        ) from e


pq_sign_detached, pq_verify_detached = _pq_import()


def _domain_candidates(alg_name: str) -> List[str]:
    # Try both the short domain and the older tag style seen in your logs.
    return [
        "tx",
        f"sig|{alg_name}|tx",
        f"sig|{alg_name.lower()}|tx",
    ]


def _prehash_candidates() -> List[str]:
    # sha3-256 was in your debug output; also try "none" if node expects raw sign bytes.
    return ["sha3-256", "none"]


@app.command("send")
def send(
    from_addr: str = typer.Option(..., "--from", help="Sender address"),
    to_addr: str = typer.Option(..., "--to", help="Recipient address"),
    value: str = typer.Option(..., "--value", help="Amount in ANM (supports decimals)"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="RPC URL (default: env ANIMICA_RPC_URL or testnet local)"),
    chain_id: Optional[int] = typer.Option(None, "--chain-id", help="Chain ID override"),
    gas_limit: int = typer.Option(21000, "--gas-limit", help="Gas limit"),
    max_fee: Optional[int] = typer.Option(None, "--max-fee", help="Max fee (base units)"),
    data: Optional[str] = typer.Option(None, "--data", help="Hex data (0x...)"),
    wallet_file: Path = typer.Option(_default_wallet_file(), "--wallet-file", help="Path to wallets.json"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose output"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Build/sign but do not broadcast"),
) -> None:
    url = _resolve_rpc_url(rpc_url)
    resolved_chain_id, chain_src = _resolve_chain_id(url, chain_id)

    if verbose:
        typer.echo("\nCHAIN CONTEXT DEBUG")
        typer.echo(f"  rpc_url: {url}")
        typer.echo(f"  chain_id: {resolved_chain_id}")
        typer.echo(f"  chain_id_source: {chain_src}\n")

    wallets = _load_wallets(wallet_file)
    sender_entry = _find_wallet_entry(wallets, from_addr)
    keys = _wallet_keys_from_entry(sender_entry)

    nonce_val, nonce_src = _resolve_nonce(url, from_addr)
    fee_val, fee_src = _resolve_max_fee(url)
    if max_fee is not None:
        fee_val, fee_src = int(max_fee), "cli override"

    value_units = _parse_anm_to_base_units(value)
    data_bytes = _parse_hex_data(data)

    if verbose:
        typer.echo(f"nonce: using {nonce_src} => {nonce_val}")
        typer.echo(f"maxFee: using {fee_src} => {fee_val}\n")

    # Transaction body (this is what gets signed)
    body: Dict[str, Any] = {
        "chainId": int(resolved_chain_id),
        "from": str(from_addr),
        "to": str(to_addr),
        "nonce": int(nonce_val),
        "value": int(value_units),
        "gasLimit": int(gas_limit),
        "maxFee": int(fee_val),
        "data": data_bytes,
    }

    # Deterministic CBOR for signing
    signable: bytes = cbor2.dumps(body, canonical=True)

    # Try multiple signing formats if node is picky
    attempts: List[Dict[str, Any]] = []
    last_err: Optional[Exception] = None

    for domain in _domain_candidates(keys.alg_name):
        for prehash in _prehash_candidates():
            try:
                sig = pq_sign_detached(
                    signable,
                    keys.alg_name,
                    keys.secret_key,
                    domain=domain,
                    chain_id=int(resolved_chain_id),
                    prehash=prehash,
                )

                # Optional local verify (if available)
                try:
                    ok = pq_verify_detached(
                        signable,
                        sig,
                        keys.alg_name,
                        keys.public_key,
                        domain=domain,
                        chain_id=int(resolved_chain_id),
                        prehash=prehash,
                    )
                    if ok is False:
                        raise RuntimeError("local pq_verify_detached returned False")
                except TypeError:
                    # Some verify implementations have a different signature; ignore if so
                    pass
                except Exception as ve:
                    # local verify failed; still record and continue
                    attempts.append({"domain": domain, "prehash": prehash, "error": f"local-verify: {ve}"})
                    continue

                # Signature envelope (keep keys SHORT; your raw CBOR starts with 'pk')
                sig_env: Dict[str, Any] = {
                    "pk": keys.public_key,
                    "sig": sig,
                    "alg": int(keys.alg_id),
                    "dom": str(domain),
                    "ph": str(prehash),
                }

                raw_obj = {"sig": sig_env, "tx": body}
                raw_tx = cbor2.dumps(raw_obj, canonical=True)
                raw_hex = "0x" + raw_tx.hex()

                if verbose:
                    typer.echo("PQ SIGNATURE DEBUG")
                    typer.echo(f"  algorithm: {keys.alg_name} (id={keys.alg_id})")
                    typer.echo(f"  domain: {domain}")
                    typer.echo(f"  prehash: {prehash}")
                    typer.echo(f"  chain_id_in_pq: {resolved_chain_id}")
                    typer.echo(f"  pubkey_len: {len(keys.public_key)} bytes")
                    typer.echo(f"  sig_len: {len(sig)} bytes")
                    typer.echo(f"  message_len: {len(signable)} bytes")
                    typer.echo(f"  message_prefix: {signable[:16].hex()}\n")

                if dry_run:
                    typer.echo("=== Dry Run ===")
                    typer.echo(f"Raw TX (hex): {raw_hex[:120]}... ({len(raw_tx)} bytes)")
                    typer.echo("Not broadcasting (--dry-run).")
                    return

                # Broadcast
                tx_hash = _rpc_call(url, "tx.sendRawTransaction", [raw_hex])
                typer.echo("=== Transaction Submitted ===")
                typer.echo(f"Tx Hash: {tx_hash}")
                typer.echo(f"From:    {from_addr}")
                typer.echo(f"To:      {to_addr}")
                typer.echo(f"Value:   {value} ANM")
                return

            except RpcError as re:
                last_err = re
                attempts.append({"domain": domain, "prehash": prehash, "rpc_error": {"code": re.code, "message": re.message}})
                # Retry only on signature-type failures
                if re.code == -32012:
                    continue
                raise
            except Exception as e:
                last_err = e
                attempts.append({"domain": domain, "prehash": prehash, "error": str(e)})
                continue

    # If we get here, all attempts failed
    if verbose:
        typer.echo("All signature attempts failed:")
        typer.echo(json.dumps(attempts, indent=2))

    if isinstance(last_err, RpcError):
        raise typer.Exit(code=1)

    raise RuntimeError(f"Failed to sign/broadcast tx after {len(attempts)} attempts. Last error: {last_err}")
