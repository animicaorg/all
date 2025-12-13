from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import typer
from rich.console import Console
from rich.pretty import Pretty

from pq.py.sign import build_sign_bytes, pq_sign_detached  # type: ignore

console = Console()
app = typer.Typer(help="Transaction commands")

ANM_BASE_UNITS = 1_000_000_000  # 1 ANM = 1e9 base units (matches your debug math)
DEFAULT_DOMAIN = "tx"
DEFAULT_PREHASH = "sha3-512"

try:
    import cbor2  # type: ignore
except Exception as e:
    raise RuntimeError(
        "Missing dependency: cbor2. Run: pip install cbor2"
    ) from e

try:
    import requests  # type: ignore
except Exception as e:
    raise RuntimeError(
        "Missing dependency: requests. Run: pip install requests"
    ) from e


@dataclass
class RpcError(Exception):
    code: int
    message: str
    data: Any = None

    def __str__(self) -> str:
        return f"{self.code} {self.message} {self.data!r}"


def _rpc(url: str, method: str, params: list[Any] | None = None, timeout: float = 15.0) -> Any:
    payload = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 1_000_000, "method": method, "params": params or []}
    r = requests.post(url, json=payload, timeout=timeout)
    r.raise_for_status()
    out = r.json()
    if "error" in out and out["error"] is not None:
        err = out["error"]
        raise RpcError(code=int(err.get("code", -1)), message=str(err.get("message", "RPC error")), data=err.get("data"))
    return out.get("result")


def _cbor(obj: Any) -> bytes:
    # Canonical CBOR is critical for cross-impl signature verification.
    return cbor2.dumps(obj, canonical=True)


def _load_wallet_entry(address: str) -> dict[str, Any]:
    wallet_path = os.path.expanduser("~/.animica/wallets.json")
    with open(wallet_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # wallets.json shape: {"wallets":[...]} or just list
    entries = data.get("wallets") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise RuntimeError(f"Unexpected wallets.json format at {wallet_path}")

    for w in entries:
        if str(w.get("address")) == address:
            return w
    raise RuntimeError(f"Address not found in {wallet_path}: {address}")


def _hex_to_bytes(h: str) -> bytes:
    h = h.strip()
    if h.startswith("0x"):
        h = h[2:]
    return bytes.fromhex(h)


def _get_chain_id(rpc_url: str) -> int:
    for m in ("chain.getChainId", "chain_id", "net_version"):
        try:
            v = _rpc(rpc_url, m, [])
            if isinstance(v, str) and v.isdigit():
                return int(v)
            if isinstance(v, int):
                return int(v)
        except Exception:
            continue
    raise RuntimeError("Could not determine chain id from node")


def _get_nonce(rpc_url: str, addr: str) -> int:
    # Your node supports state.getNonce (per your logs). Keep fallbacks anyway.
    methods = [
        ("state.getNonce", [addr]),
        ("state.getNonce", [{"address": addr}]),
        ("state.getTransactionCount", [addr]),
        ("tx.getTransactionCount", [addr]),
    ]
    for m, p in methods:
        try:
            v = _rpc(rpc_url, m, p)
            if isinstance(v, int):
                return v
            if isinstance(v, str) and v.isdigit():
                return int(v)
        except Exception:
            continue
    raise RuntimeError("Could not determine nonce from node (tried state.getNonce and fallbacks)")


def _get_default_max_fee(rpc_url: str) -> int:
    # Many Animica nodes don't expose eth_gasPrice-style APIs; default to 1.
    for m in ("tx.gasPrice", "gasPrice", "fee.getGasPrice"):
        try:
            v = _rpc(rpc_url, m, [])
            if isinstance(v, int):
                return v
            if isinstance(v, str) and v.isdigit():
                return int(v)
        except Exception:
            continue
    return 1


def _build_tx_body(
    *,
    chain_id: int,
    from_addr: str,
    to_addr: str,
    nonce: int,
    value_base_units: int,
    gas_limit: int,
    max_fee: int,
    data: bytes,
) -> Dict[str, Any]:
    # Keep keys stable + canonical CBOR in _cbor().
    # IMPORTANT: do not omit fields; node-side canonicalization often assumes presence.
    return {
        "to": to_addr,
        "from": from_addr,
        "value": int(value_base_units),
        "nonce": int(nonce),
        "gasLimit": int(gas_limit),
        "maxFee": int(max_fee),
        "data": data,        # CBOR bstr
        "chainId": int(chain_id),
    }


def _build_raw_tx(
    *,
    body: Dict[str, Any],
    alg_id: int,
    pk: bytes,
    sig: bytes,
    domain: str,
    prehash: str,
    chain_id: int,
) -> bytes:
    # Signature envelope includes enough metadata for node-side reconstruction.
    sig_env = {
        "algId": int(alg_id),
        "pk": pk,
        "sig": sig,
        "domain": domain,
        "prehash": prehash,
        "chainId": int(chain_id),
    }
    return _cbor({"sig": sig_env, "body": body})


@app.command("send")
def send(
    from_addr: str = typer.Option(..., "--from", help="Sender address (anim1...)"),
    to_addr: str = typer.Option(..., "--to", help="Recipient address (anim1... )"),
    value: float = typer.Option(..., "--value", help="Amount in ANM (whole/decimal)"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="RPC URL (default: node)"),
    chain_id: Optional[int] = typer.Option(None, "--chain-id", help="Chain ID override"),
    gas_limit: int = typer.Option(21000, "--gas-limit", help="Gas limit"),
    max_fee: Optional[int] = typer.Option(None, "--max-fee", help="Max fee (base units)"),
    domain: str = typer.Option(DEFAULT_DOMAIN, "--domain", help="PQ signing domain"),
    prehash: str = typer.Option(DEFAULT_PREHASH, "--prehash", help="Prehash: sha3-512 | sha3-256"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose debug output"),
    debug_signing: bool = typer.Option(False, "--debug-signing", help="Dump canonical sign-bytes debug info"),
):
    """
    Send a raw transaction via tx.sendRawTransaction using PQ signature.
    """
    # Resolve RPC
    rpc = rpc_url or os.environ.get("ANIMICA_RPC_URL") or "http://127.0.0.1:18546/rpc"

    # Resolve chain id
    cid = int(chain_id) if chain_id is not None else _get_chain_id(rpc)

    # Nonce + fee defaults
    nonce = _get_nonce(rpc, from_addr)
    fee = int(max_fee) if max_fee is not None else _get_default_max_fee(rpc)

    # Value conversion
    value_base = int(round(value * ANM_BASE_UNITS))

    # Load wallet keys
    w = _load_wallet_entry(from_addr)

    alg_id = int(w.get("alg_id") or w.get("algId") or 0x1001)
    pk_hex = str(w.get("public_key_hex") or w.get("publicKeyHex") or "")
    sk_hex = str(w.get("secret_key_hex") or w.get("secretKeyHex") or "")

    if not pk_hex or not sk_hex:
        raise RuntimeError("wallet entry missing public_key_hex or secret_key_hex")

    pk = _hex_to_bytes(pk_hex)
    sk = _hex_to_bytes(sk_hex)

    body = _build_tx_body(
        chain_id=cid,
        from_addr=from_addr,
        to_addr=to_addr,
        nonce=nonce,
        value_base_units=value_base,
        gas_limit=gas_limit,
        max_fee=fee,
        data=b"",
    )
    body_bytes = _cbor(body)

    sign_bytes = build_sign_bytes(
        body_bytes,
        domain=domain,
        chain_id=cid,
        alg_id=alg_id,
        prehash=prehash,  # type: ignore[arg-type]
    )

    if verbose or debug_signing:
        console.print("\n[bold]CHAIN CONTEXT DEBUG[/bold]")
        console.print({"rpc_url": rpc, "chain_id": cid, "chain_id_source": "cli override" if chain_id is not None else "node:chain.getChainId"})
        console.print("")
        console.print(f"nonce: using state.getNonce => {nonce}")
        console.print(f"maxFee: using {'override' if max_fee is not None else 'default'} => {fee}")
        console.print("")
        console.print("[bold]PQ SIGNATURE DEBUG[/bold]")
        console.print(
            {
                "algorithm_id": alg_id,
                "domain": domain,
                "prehash": prehash,
                "chain_id_in_pq": cid,
                "pubkey_len": len(pk),
                "seckey_len": len(sk),
                "message_len": len(body_bytes),
                "message_prefix": body_bytes[:32].hex(),
                "sign_bytes_hash": hashlib.sha3_256(sign_bytes).hexdigest(),
                "sign_bytes_len": len(sign_bytes),
            }
        )

    # Sign
    pq = pq_sign_detached(
        body_bytes,
        alg=alg_id,
        sk=sk,
        domain=domain,
        chain_id=cid,
        prehash=prehash,  # type: ignore[arg-type]
    )

    raw = _build_raw_tx(
        body=body,
        alg_id=pq.alg_id,
        pk=pk,
        sig=pq.sig,
        domain=domain,
        prehash=prehash,
        chain_id=cid,
    )
    raw_hex = "0x" + raw.hex()

    if verbose:
        console.print("\n[bold]RAW TX[/bold]")
        console.print({"raw_len": len(raw), "raw_prefix": raw[:24].hex(), "raw_hex_prefix": raw_hex[:80] + "..."})

    # Submit (with one compatibility fallback)
    try:
        tx_hash = _rpc(rpc, "tx.sendRawTransaction", [raw_hex])
    except RpcError as e:
        # Some nodes use alternate method naming
        if e.code in (-32601,):
            tx_hash = _rpc(rpc, "tx_sendRawTransaction", [raw_hex])
        else:
            raise

    console.print("\n[bold green]=== Transaction Sent ===[/bold green]")
    console.print({"tx_hash": tx_hash})
    if verbose:
        console.print("\n[bold]TX BODY[/bold]")
        console.print(Pretty(body))
