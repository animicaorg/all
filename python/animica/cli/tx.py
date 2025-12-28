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

from pq.py.sign import build_sign_bytes, pq_sign_detached, verify_detached  # type: ignore
from animica.config import load_network_config
from animica.cli.rpc_guard import guard_bootstrap_rpc
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, resolve_timeout

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


def _rpc(
    url: str,
    method: str,
    params: list[Any] | None = None,
    timeout: Optional[float] = None,
) -> Any:
    payload = {"jsonrpc": "2.0", "id": int(time.time() * 1000) % 1_000_000, "method": method, "params": params or []}
    resolved_timeout = resolve_timeout("RPC timeout", timeout, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)
    r = requests.post(url, json=payload, timeout=resolved_timeout)
    r.raise_for_status()
    out = r.json()
    if "error" in out and out["error"] is not None:
        err = out["error"]
        raise RpcError(code=int(err.get("code", -1)), message=str(err.get("message", "RPC error")), data=err.get("data"))
    return out.get("result")


def _resolve_rpc_url(rpc_url: Optional[str]) -> str:
    if rpc_url and rpc_url.strip():
        return rpc_url.strip()
    env_url = os.environ.get("ANIMICA_RPC_URL")
    if env_url and env_url.strip():
        return env_url.strip()
    return load_network_config().rpc_url


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


def _address_to_32_bytes(address: str) -> bytes:
    """
    Convert address to canonical 32-byte format for tx bodies.
    
    Args:
        address: Bech32 address (anim1...) or hex address (0x...)
        
    Returns:
        32-byte digest (for bech32) or padded hex bytes
    """
    from pq.py.address import decode_address
    
    address = address.strip()
    
    # Bech32 address → extract 32-byte digest
    if address.lower().startswith("anim"):
        rec = decode_address(address)
        digest = bytes(rec.digest) if isinstance(rec.digest, list) else rec.digest
        # Return only the 32-byte digest (not alg_id prefix)
        return digest[:32].ljust(32, b"\x00")
    
    # Hex address → decode and pad/truncate to 32 bytes
    if address.startswith("0x"):
        address = address[2:]
    
    addr_bytes = bytes.fromhex(address)
    if len(addr_bytes) < 32:
        addr_bytes = addr_bytes.ljust(32, b"\x00")
    elif len(addr_bytes) > 32:
        addr_bytes = addr_bytes[:32]
    
    return addr_bytes


def _format_insufficient_funds_error(e: RpcError) -> None:
    """Format and display an insufficient funds error in a user-friendly way."""
    data = e.data or {}
    required = data.get("required", "?")
    available = data.get("available", "?")
    shortfall = data.get("shortfall", "?")
    
    # Convert to ANM if possible (1 ANM = 1e9 base units)
    try:
        required_anm = int(required) / ANM_BASE_UNITS if required != "?" else "?"
        available_anm = int(available) / ANM_BASE_UNITS if available != "?" else "?"
        shortfall_anm = int(shortfall) / ANM_BASE_UNITS if shortfall != "?" else "?"
    except (ValueError, TypeError):
        required_anm = required
        available_anm = available
        shortfall_anm = shortfall
    
    console.print("\n[bold red]Error: Insufficient Balance[/bold red]")
    console.print(f"  Requested: {required_anm} ANM ({required} base units)")
    console.print(f"  Available: {available_anm} ANM ({available} base units)")
    console.print(f"  Shortfall: {shortfall_anm} ANM ({shortfall} base units)")
    console.print("\n[yellow]Tip:[/yellow] You need to obtain more ANM before sending this transaction.")


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


def _get_chain_identity(rpc_url: str) -> dict:
    try:
        ident = _rpc(rpc_url, "chain.getChainIdentity", [])
        if isinstance(ident, dict):
            return ident
    except Exception:
        pass
    return {"chainId": _get_chain_id(rpc_url), "forkId": None}


def _get_nonce(rpc_url: str, addr: str) -> int:
    # Try pending nonce first (includes mempool transactions)
    # This ensures back-to-back sends use incrementing nonces
    methods = [
        ("state.getPendingNonce", [addr]),
        ("state.getNonce", [addr, "pending"]),
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
    raise RuntimeError("Could not determine nonce from node (tried state.getPendingNonce, state.getNonce and fallbacks)")


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
    # Convert addresses to canonical 32-byte format (digest bytes, not bech32 strings)
    from_bytes = _address_to_32_bytes(from_addr)
    to_bytes = _address_to_32_bytes(to_addr)
    
    return {
        "to": to_bytes,
        "from": from_bytes,
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
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using remote bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
    ),
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
    rpc = _resolve_rpc_url(rpc_url)
    guard_bootstrap_rpc(rpc, allow_remote=allow_remote_rpc, method="tx.sendRawTransaction")

    # Resolve chain identity
    chain_identity = _get_chain_identity(rpc)
    cid = int(chain_id) if chain_id is not None else int(chain_identity.get("chainId"))
    fork_id = chain_identity.get("forkId")

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
        fork_id=fork_id,
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
                "fork_id_in_pq": fork_id,
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
        pk=pk,
        domain=domain,
        chain_id=cid,
        fork_id=fork_id,
        prehash=prehash,  # type: ignore[arg-type]
    )

    try:
        local_ok = verify_detached(
            body_bytes,
            pq,
            pk,
            domain=domain,
            chain_id=cid,
            fork_id=fork_id,
            prehash=prehash,  # type: ignore[arg-type]
        )
    except Exception as e:
        raise RuntimeError(f"Local PQ verify failed before broadcast: {e}") from e

    if not local_ok:
        raise RuntimeError(
            "Local PQ verify failed before broadcast (sign-bytes mismatch)."
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
        console.print(
            {
                "raw_len": len(raw),
                "raw_prefix": raw[:24].hex(),
                "raw_hex": raw_hex,
            }
        )

    # Submit (with one compatibility fallback)
    try:
        tx_hash = _rpc(rpc, "tx.sendRawTransaction", [raw_hex])
    except RpcError as e:
        # Handle insufficient funds error with user-friendly formatting
        if e.code == -32013:  # AnimicaCode.INSUFFICIENT_FUNDS
            _format_insufficient_funds_error(e)
            raise typer.Exit(code=1)
        # Some nodes use alternate method naming
        if e.code in (-32601,):
            tx_hash = _rpc(rpc, "tx_sendRawTransaction", [raw_hex])
        else:
            raise

    # Verify tx is actually in mempool
    tx_in_mempool = False
    try:
        pending = _rpc(rpc, "mempool.getPending", [])
        if isinstance(pending, list) and tx_hash in pending:
            tx_in_mempool = True
    except RpcError as e:
        # mempool.getPending may not be available, try mempool.explain
        if verbose:
            console.print(f"[dim]mempool.getPending not available (code={e.code}), trying mempool.explain...[/dim]")
        try:
            explain = _rpc(rpc, "mempool.explain", [tx_hash])
            if isinstance(explain, dict):
                status = explain.get("status")
                if status != "not_found":
                    tx_in_mempool = True
                elif verbose:
                    console.print(f"[yellow]mempool.explain status: {status}[/yellow]")
        except RpcError as e2:
            if verbose:
                console.print(f"[dim]mempool.explain also failed (code={e2.code})[/dim]")

    if not tx_in_mempool:
        console.print("\n[bold red]=== ERROR: Transaction Not in Mempool ===[/bold red]")
        console.print(f"TX hash: {tx_hash}")
        console.print("")
        console.print("The RPC accepted the transaction but it is NOT in the mempool.")
        console.print("Possible reasons:")
        console.print("  • Nonce gap (tx nonce is too high)")
        console.print("  • Fee too low (below minimum gas price)")
        console.print("  • Gas limit too high (exceeds block limit)")
        console.print("  • Mempool full (tx evicted)")
        console.print("")
        console.print("The transaction will NOT be mined. Please check:")
        console.print(f"  animica tx get {tx_hash}")
        console.print(f"  animica state get-nonce {from_addr}")
        console.print(f"  animica mempool list")
        raise typer.Exit(code=1)

    console.print("\n[bold green]=== Transaction Sent ===[/bold green]")
    console.print({"tx_hash": tx_hash})
    if verbose:
        console.print("\n[bold]TX BODY[/bold]")
        console.print(Pretty(body))
