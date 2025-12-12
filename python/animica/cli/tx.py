# python/animica/cli/tx.py
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import typer

from animica.config import load_network_config
from animica.tx.signing import build_signable_tx_bytes

app = typer.Typer(help="Transaction operations (build/sign/send)")

BASE_UNITS_PER_ANM = 1_000_000_000  # 1 ANM = 1e9 base units (matches faucet output)

# -----------------------------
# RPC
# -----------------------------


class RpcError(RuntimeError):
    def __init__(self, method: str, code: int, message: str, data: Any = None):
        super().__init__(f"RPC error {code}: {message}")
        self.method = method
        self.code = code
        self.message = message
        self.data = data


def _rpc_url(rpc_url: Optional[str]) -> str:
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
    env = os.environ.get("ANIMICA_RPC_URL")
    if env and env.strip():
        return env.strip()
    return load_network_config().rpc_url


def _rpc_call(rpc_url: str, method: str, params: Any) -> Any:
    import requests

    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    try:
        r = requests.post(rpc_url, json=payload, timeout=12)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"RPC transport error calling {method}: {e}") from e

    if isinstance(data, dict) and data.get("error"):
        err = data["error"] or {}
        raise RpcError(
            method=method,
            code=int(err.get("code", -32000)),
            message=str(err.get("message", "Unknown error")),
            data=err.get("data"),
        )

    return data.get("result")


def _is_method_not_found(e: Exception) -> bool:
    if not isinstance(e, RpcError):
        return False
    if e.code == -32601:
        return True
    # some servers put it only in message/data
    if "Method not found" in (e.message or ""):
        return True
    if isinstance(e.data, dict) and e.data.get("method"):
        # still treat as missing if server says so
        return e.code == -32601
    return False


def _try_methods(
    rpc_url: str,
    candidates: List[Tuple[str, Any]],
    *,
    verbose: bool = False,
    label: str = "rpc",
) -> Tuple[Optional[Any], Optional[str]]:
    last_err: Optional[Exception] = None
    for method, params in candidates:
        try:
            res = _rpc_call(rpc_url, method, params)
            if verbose:
                typer.echo(f"{label}: using {method}", err=True)
            return res, method
        except Exception as e:
            last_err = e
            if _is_method_not_found(e):
                continue
            # other errors (bad params, internal, etc.) — still try next, but keep last
            continue
    if verbose and last_err is not None:
        typer.echo(f"{label}: all candidates failed; last error: {last_err}", err=True)
    return None, None


# -----------------------------
# Chain / fee / nonce
# -----------------------------


def _resolve_chain_id(rpc_url: str, cfg_chain_id: Optional[int], cli_chain_id: Optional[int]) -> Tuple[int, str]:
    if cli_chain_id is not None:
        return int(cli_chain_id), "CLI/env"

    res, used = _try_methods(
        rpc_url,
        [
            ("chain.getChainId", []),
            ("net_version", []),
            ("eth_chainId", []),
        ],
        label="chainId",
    )
    if res is not None:
        if isinstance(res, str):
            return (int(res, 16) if res.startswith("0x") else int(res)), f"node:{used}"
        return int(res), f"node:{used}"

    if cfg_chain_id is not None:
        return int(cfg_chain_id), "network config"

    return 1, "default"


def _parse_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip()
        if s.startswith("0x"):
            return int(s, 16)
        return int(s)
    return int(v)


def _extract_nonce_from_obj(obj: Any) -> Optional[int]:
    if obj is None:
        return None
    if isinstance(obj, dict):
        for k in ("nonce", "txCount", "transactionCount", "transaction_count"):
            if k in obj and obj[k] is not None:
                return _parse_int(obj[k])
        # sometimes nested
        for k in ("account", "state", "info"):
            if k in obj:
                n = _extract_nonce_from_obj(obj[k])
                if n is not None:
                    return n
        return None
    return _parse_int(obj)


def _get_nonce(rpc_url: str, address: str, *, verbose: bool = False) -> Tuple[int, str]:
    res, used = _try_methods(
        rpc_url,
        [
            ("state.getNonce", [address]),
            ("state.getAccountNonce", [address]),
            ("account.getNonce", [address]),
            ("tx.getNonce", [address]),
            ("state.getAccount", [address]),
            ("state.getAccountInfo", [address]),
            ("eth_getTransactionCount", [address, "latest"]),
            # legacy (this is the one that currently fails on your node):
            ("state.getTransactionCount", [address]),
        ],
        verbose=verbose,
        label="nonce",
    )
    n = _extract_nonce_from_obj(res)
    if n is None:
        # last resort: assume 0
        return 0, used or "default(0)"
    return int(n), used or "unknown"


def _get_gas_price_base(rpc_url: str, *, verbose: bool = False) -> Tuple[int, str]:
    res, used = _try_methods(
        rpc_url,
        [
            ("state.suggestGasPrice", []),
            ("tx.suggestGasPrice", []),
            ("tx.gasPrice", []),
            ("eth_gasPrice", []),
        ],
        verbose=verbose,
        label="gasPrice",
    )
    gp = _parse_int(res)
    if gp is None:
        return 1, used or "default(1)"
    return int(gp), used or "unknown"


def _to_base_units(amount_anm: float) -> int:
    d = (Decimal(str(amount_anm)) * Decimal(BASE_UNITS_PER_ANM)).quantize(Decimal("1"), rounding=ROUND_DOWN)
    return int(d)


# -----------------------------
# Wallet helpers (reuse existing wallet store format)
# -----------------------------


def _get_wallet_path(wallet_file: Optional[Path]) -> Path:
    if wallet_file is not None:
        return wallet_file
    env = os.environ.get("ANIMICA_WALLETS_FILE")
    if env:
        return Path(env)
    return Path.home() / ".animica" / "wallets.json"


@dataclass(frozen=True)
class WalletEntry:
    address: str
    alg_id: int
    alg_name: str
    public_key: bytes
    secret_key: bytes


def _load_wallet_entry(identifier: str, wallet_file: Optional[Path]) -> WalletEntry:
    """
    Supports the repo's CLI wallet store via animica.cli.wallet helpers if present.
    Falls back to a simple JSON reader for common shapes:
      - {"wallets":[{label,address,alg_id,alg_name,public_key_hex,secret_key_hex},...]}
      - [{...}, {...}]
    """
    wp = _get_wallet_path(wallet_file)
    if not wp.exists():
        raise RuntimeError(f"Wallet store not found at {wp}")

    # Prefer repo helpers if available (most compatible)
    try:
        from animica.cli.wallet import _find_wallet, _load_store  # type: ignore

        store = _load_store(wp)
        w = _find_wallet(store, identifier=identifier)
        pk_hex = getattr(w, "public_key_hex")
        sk_hex = getattr(w, "secret_key_hex")
        alg_id = int(getattr(w, "alg_id"))
        alg_name = str(getattr(w, "alg_name"))
        return WalletEntry(
            address=str(getattr(w, "address")),
            alg_id=alg_id,
            alg_name=alg_name,
            public_key=bytes.fromhex(str(pk_hex).removeprefix("0x")),
            secret_key=bytes.fromhex(str(sk_hex).removeprefix("0x")),
        )
    except Exception:
        pass

    raw = json.loads(wp.read_text(encoding="utf-8"))
    items = raw.get("wallets") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise RuntimeError("Unrecognized wallets.json format")

    for it in items:
        if not isinstance(it, dict):
            continue
        if it.get("address") == identifier or it.get("label") == identifier:
            pk_hex = str(it.get("public_key_hex") or it.get("publicKeyHex") or it.get("public_key") or it.get("publicKey"))
            sk_hex = str(it.get("secret_key_hex") or it.get("secretKeyHex") or it.get("secret_key") or it.get("secretKey"))
            alg_id = int(it.get("alg_id", it.get("algId", 0xFFFF)))
            alg_name = str(it.get("alg_name", it.get("algName", "")))
            return WalletEntry(
                address=str(it["address"]),
                alg_id=alg_id,
                alg_name=alg_name,
                public_key=bytes.fromhex(pk_hex.removeprefix("0x")),
                secret_key=bytes.fromhex(sk_hex.removeprefix("0x")),
            )

    raise RuntimeError(f"Wallet not found for: {identifier}")


# -----------------------------
# PQ signing (flexible)
# -----------------------------


def _pq_sign_tx_bytes(
    msg: bytes,
    *,
    wallet: WalletEntry,
    chain_id: int,
    verbose: bool = False,
) -> Tuple[bytes, str, str]:
    """
    Returns (sig_bytes, domain_str, prehash_str)
    Tries repo pq.py sign_detached; if signature object returned, extracts .sig
    """
    domain = "tx"
    prehash = "sha3-512"

    try:
        import pq.py.sign as sign_mod  # type: ignore

        fn = getattr(sign_mod, "sign_detached", None) or getattr(sign_mod, "sign", None)
        if fn is None:
            raise RuntimeError("pq.py.sign missing sign_detached/sign")

        attempts = [
            ((), dict(msg=msg, alg=wallet.alg_name, sk=wallet.secret_key, domain=domain, chain_id=chain_id, prehash=prehash)),
            ((), dict(msg=msg, alg=wallet.alg_id, sk=wallet.secret_key, domain=domain, chain_id=chain_id, prehash=prehash)),
            ((msg, wallet.alg_name, wallet.secret_key), dict(domain=domain, chain_id=chain_id, prehash=prehash)),
            ((msg, wallet.alg_id, wallet.secret_key), dict(domain=domain, chain_id=chain_id, prehash=prehash)),
        ]

        out: Any = None
        last: Optional[Exception] = None
        for args, kwargs in attempts:
            try:
                out = fn(*args, **kwargs)
                break
            except Exception as e:
                last = e
                continue
        if out is None:
            raise RuntimeError(f"pq.py signing failed: {last}")

        if isinstance(out, (bytes, bytearray, memoryview)):
            sig_bytes = bytes(out)
        elif hasattr(out, "sig"):
            sig_bytes = bytes(getattr(out, "sig"))
            # if signature object carries domain/prehash, prefer it
            domain = str(getattr(out, "domain", domain))
            prehash = str(getattr(out, "prehash", prehash))
        else:
            raise RuntimeError(f"Unexpected pq.py signing return type: {type(out)}")

        if verbose:
            typer.echo("", err=True)
            typer.echo("PQ SIGNATURE DEBUG", err=True)
            typer.echo(f"  algorithm: {wallet.alg_name} (id={wallet.alg_id})", err=True)
            typer.echo(f"  pubkey_len: {len(wallet.public_key)} bytes", err=True)
            typer.echo(f"  sig_len: {len(sig_bytes)} bytes", err=True)
            typer.echo(f"  message_len: {len(msg)} bytes", err=True)
            typer.echo(f"  message_prefix: {msg[:16].hex()}", err=True)
            typer.echo(f"  chain_id: {chain_id}", err=True)
            typer.echo("", err=True)

        return sig_bytes, domain, prehash
    except Exception as e:
        raise RuntimeError(f"PQ signing unavailable/failed: {e}") from e


# -----------------------------
# CBOR helpers
# -----------------------------


def _cbor_dumps(obj: Any) -> bytes:
    try:
        import cbor2  # type: ignore
    except Exception as e:
        raise RuntimeError("Missing dependency cbor2. Install it: pip install cbor2") from e
    return cbor2.dumps(obj, canonical=True)


# -----------------------------
# Command: tx send
# -----------------------------


@app.command("send")
def send(
    from_addr: str = typer.Option(..., "--from", help="Sender address or wallet label"),
    to_addr: str = typer.Option(..., "--to", help="Recipient address"),
    value: float = typer.Option(..., "--value", help="Amount to transfer (in ANM)"),
    gas_limit: Optional[int] = typer.Option(None, "--gas", help="Gas limit (default 21000)"),
    max_fee: Optional[int] = typer.Option(None, "--max-fee", help="Max fee (base units); auto if omitted"),
    nonce: Optional[int] = typer.Option(None, "--nonce", help="Nonce; auto if omitted"),
    chain_id: Optional[int] = typer.Option(None, "--chain-id", envvar="ANIMICA_CHAIN_ID", help="Chain id override"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", envvar="ANIMICA_RPC_URL", help="RPC URL override"),
    wallet_file: Optional[Path] = typer.Option(None, "--wallet-file", envvar="ANIMICA_WALLETS_FILE", help="Wallet store path"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose debug"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Build+sign, do not broadcast"),
) -> None:
    cfg = load_network_config()
    url = _rpc_url(rpc_url)
    resolved_chain_id, chain_id_source = _resolve_chain_id(url, getattr(cfg, "chain_id", None), chain_id)

    if verbose:
        typer.echo("", err=True)
        typer.echo("CHAIN CONTEXT DEBUG", err=True)
        typer.echo(f"  network: {cfg.name}", err=True)
        typer.echo(f"  rpc_url: {url}", err=True)
        typer.echo(f"  chain_id: {resolved_chain_id}", err=True)
        typer.echo(f"  chain_id_source: {chain_id_source}", err=True)
        typer.echo("", err=True)

    wallet = _load_wallet_entry(from_addr, wallet_file)

    # ✅ FIX: do NOT hard-require state.getTransactionCount; try fallbacks
    if nonce is None:
        nonce, nonce_method = _get_nonce(url, wallet.address, verbose=verbose)
    else:
        nonce_method = "CLI"

    if gas_limit is None:
        gas_limit = 21000

    if max_fee is None:
        max_fee, fee_method = _get_gas_price_base(url, verbose=verbose)
    else:
        fee_method = "CLI"

    # Build tx body matching animica.tx.signing canonical field set
    body: Dict[str, Any] = {
        "chainId": int(resolved_chain_id),
        "from": str(wallet.address),
        "to": str(to_addr.strip()),
        "nonce": int(nonce),
        "value": int(_to_base_units(value)),
        "gasLimit": int(gas_limit),
        "maxFee": int(max_fee),
        "data": b"",  # bytes (deterministic)
    }

    # Signable bytes (canonical CBOR(body))
    msg = build_signable_tx_bytes(body, chain_id=resolved_chain_id)

    sig_bytes, sig_domain, sig_prehash = _pq_sign_tx_bytes(msg, wallet=wallet, chain_id=resolved_chain_id, verbose=verbose)

    # Envelope: include pubkey because address cannot embed a 1952-byte PQ pubkey
    sig_obj = {
        "algId": int(wallet.alg_id),
        "alg": int(wallet.alg_id),
        "domain": sig_domain,
        "prehash": sig_prehash,
        "pub": wallet.public_key,
        "sig": sig_bytes,
    }
    envelope = {"body": body, "sig": sig_obj}
    raw_tx = _cbor_dumps(envelope)
    raw_hex = "0x" + raw_tx.hex()

    if dry_run:
        typer.echo("=== Dry-Run Mode ===")
        typer.echo(f"nonce: {nonce} (via {nonce_method})")
        typer.echo(f"maxFee: {max_fee} (via {fee_method})")
        typer.echo(f"raw_tx_bytes: {len(raw_tx)}")
        typer.echo(f"RAW_TX={raw_hex}")
        return

    # Broadcast: try the two most common param shapes
    try:
        try:
            result = _rpc_call(url, "tx.sendRawTransaction", [raw_hex])
        except RpcError as e:
            # some servers want {"raw_tx": "..."} instead
            if e.code in (-32602, -32601) or "params" in (e.message or "").lower():
                result = _rpc_call(url, "tx.sendRawTransaction", [{"raw_tx": raw_hex}])
            else:
                raise

        typer.echo("✓ Transaction submitted!")
        typer.echo(f"Result: {result}")
    except RpcError as e:
        typer.echo("=== Transaction Failed ===", err=True)
        typer.echo(f"Method:  {e.method}", err=True)
        typer.echo(f"Code:    {e.code}", err=True)
        typer.echo(f"Message: {e.message}", err=True)
        if e.data is not None:
            typer.echo(f"Data:    {e.data}", err=True)
        raise typer.Exit(1)
    except Exception as e:
        typer.echo(f"Error broadcasting transaction: {e}", err=True)
        raise typer.Exit(1)
