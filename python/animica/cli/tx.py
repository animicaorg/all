from __future__ import annotations

import hashlib
import json
import os
import time
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from typing import Any, Dict, Optional

import typer
from rich.console import Console
from rich.pretty import Pretty

from pq.py.sign import build_sign_bytes, pq_sign_detached, verify_detached  # type: ignore
from animica.config import load_network_config
from animica.cli.rpc_guard import guard_bootstrap_rpc
from animica.sync.readiness import assess_tx_submission_readiness
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
    try:
        from core.utils.address import address_to_bytes
    except Exception:
        address_to_bytes = None  # type: ignore[assignment]
    from pq.py.address import decode_address
    
    address = address.strip()

    if address_to_bytes is not None:
        try:
            addr_bytes = address_to_bytes(address)
            if len(addr_bytes) != 32:
                if len(addr_bytes) < 32:
                    return addr_bytes.rjust(32, b"\x00")
                return addr_bytes[-32:]
            return addr_bytes
        except Exception:
            pass
    
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
        addr_bytes = addr_bytes.rjust(32, b"\x00")
    elif len(addr_bytes) > 32:
        addr_bytes = addr_bytes[-32:]
    
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


def _format_rpc_error(e: RpcError) -> None:
    console.print(f"\n[bold red]RPC Error {e.code}[/bold red]: {e.message}")
    if e.data is not None:
        console.print(Pretty(e.data))


def _parse_value_to_base_units(
    value: Optional[str],
    value_nanm: Optional[int],
) -> tuple[int, str]:
    if value is not None and value_nanm is not None:
        raise ValueError("Provide either --value (ANM) or --value-nanm, not both.")
    if value is None and value_nanm is None:
        raise ValueError("Missing amount: provide --value (ANM) or --value-nanm.")
    if value_nanm is not None:
        if value_nanm < 0:
            raise ValueError("--value-nanm must be non-negative.")
        return int(value_nanm), "nanm"
    value_str = str(value).strip().replace("_", "")
    try:
        dec = Decimal(value_str)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid ANM value: {value}") from exc
    if dec.is_signed():
        raise ValueError("--value must be non-negative.")
    base = dec * Decimal(ANM_BASE_UNITS)
    if base != base.to_integral_value():
        raise ValueError("ANM value has more than 9 decimal places (cannot convert to nANM).")
    return int(base), "anm"


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


_NONCE_CACHE: dict[tuple[str, str], int] = {}


def _get_nonce(rpc_url: str, addr: str) -> int:
    confirmed_nonce = None
    methods = [
        ("state.getNonce", [addr, "latest"]),
        ("state.getNonce", [addr]),
        ("state.getNonce", [{"address": addr}]),
        ("state.getTransactionCount", [addr]),
        ("tx.getTransactionCount", [addr]),
    ]
    for m, p in methods:
        try:
            v = _rpc(rpc_url, m, p)
            if isinstance(v, int):
                confirmed_nonce = v
                break
            if isinstance(v, str) and v.isdigit():
                confirmed_nonce = int(v)
                break
        except Exception:
            continue
    if confirmed_nonce is None:
        raise RuntimeError("Could not determine confirmed nonce from node (tried state.getNonce and fallbacks)")

    highest_pending_nonce: Optional[int] = None
    try:
        pending = _rpc(rpc_url, "mempool.getPending", [True])
    except Exception:
        pending = None

    if isinstance(pending, list):
        target_bytes = _address_to_32_bytes(addr)
        for entry in pending:
            if not isinstance(entry, dict):
                continue
            sender = entry.get("from") or entry.get("sender")
            if sender is None:
                continue
            try:
                if isinstance(sender, (bytes, bytearray)):
                    sender_bytes = bytes(sender)
                else:
                    sender_bytes = _address_to_32_bytes(str(sender))
            except Exception:
                continue
            if sender_bytes != target_bytes:
                continue
            tx_nonce = entry.get("nonce")
            if isinstance(tx_nonce, int):
                if highest_pending_nonce is None or tx_nonce > highest_pending_nonce:
                    highest_pending_nonce = tx_nonce

    if highest_pending_nonce is None:
        return confirmed_nonce
    return max(confirmed_nonce, highest_pending_nonce + 1)


def _next_nonce(rpc_url: str, addr: str) -> int:
    base = _get_nonce(rpc_url, addr)
    key = (rpc_url, addr)
    cached = _NONCE_CACHE.get(key)
    if cached is not None and cached >= base:
        base = cached + 1
    _NONCE_CACHE[key] = base
    return base


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


def _warn_if_unsynced(rpc: str, *, threshold: int = 5) -> bool:
    try:
        status = _rpc(rpc, "sync.getStatus", [])
    except Exception:
        return False

    if not isinstance(status, dict):
        return False

    phase = status.get("phase") or status.get("state")
    synchronized = status.get("synchronized")
    head_height = status.get("head_height")
    best_header_height = status.get("best_header_height")
    network_best = status.get("network_best_height")
    try:
        head_height = int(head_height) if head_height is not None else None
    except Exception:
        head_height = None
    try:
        best_header_height = int(best_header_height) if best_header_height is not None else None
    except Exception:
        best_header_height = None
    try:
        network_best = int(network_best) if network_best is not None else None
    except Exception:
        network_best = None

    if synchronized is True:
        return False

    behind = False
    lag_known = False
    if network_best is not None and head_height is not None:
        lag_known = True
        if network_best - head_height > threshold:
            behind = True
    if best_header_height is not None and head_height is not None:
        lag_known = True
        if best_header_height - head_height > threshold:
            behind = True
    if not lag_known and phase and phase not in {"SYNCED", "IDLE", "TARGET_REACHED"}:
        behind = True

    if behind:
        console.print(
            "[yellow]Warning:[/yellow] You are behind the network; mined blocks/tx confirmations may be reorged."
        )
    return behind


def _should_force_sync(status: dict) -> bool:
    phase = status.get("phase") or status.get("state")
    phase_name = str(phase).upper() if phase is not None else ""

    if status.get("synchronized") is True:
        return False

    if status.get("syncing") is True:
        return True

    if phase_name in {"HEADERS", "SYNCING_HEADERS", "BLOCKS", "SYNCING_BLOCKS", "BOOTSTRAP", "SYNCING"}:
        return True

    return False


def _maybe_force_sync(rpc: str, *, verbose: bool = False) -> None:
    try:
        status = _rpc(rpc, "sync.getStatus", [])
    except Exception:
        return

    if not isinstance(status, dict):
        return

    if not _should_force_sync(status):
        return

    try:
        _rpc(rpc, "sync.force", [])
        console.print("[yellow]Info:[/yellow] Triggered sync.force after transaction submission.")
    except RpcError as e:
        if e.code in (-32601,):
            if verbose:
                console.print("[dim]sync.force not supported by this node.[/dim]")
            return
        if verbose:
            console.print(f"[dim]sync.force failed (code={e.code}): {e.message}[/dim]")
    except Exception as exc:
        if verbose:
            console.print(f"[dim]sync.force failed: {exc}[/dim]")


def _ensure_node_ready_for_tx(rpc: str) -> None:
    try:
        status = _rpc(rpc, "sync.getStatus", [{"source": "refresh"}])
    except Exception:
        try:
            status = _rpc(rpc, "sync.getStatus", [])
        except Exception:
            return

    if not isinstance(status, dict):
        return
    allowed, _info = assess_tx_submission_readiness(status)
    if allowed:
        return

    phase = status.get("phase") or status.get("state")
    phase_name = str(phase).upper() if phase is not None else ""
    if status.get("synchronized") is False or phase_name:
        console.print("\n[bold red]Node is still syncing; transaction submission is unavailable.[/bold red]")
        console.print(Pretty(status))
        console.print("\n[yellow]Tip:[/yellow] Wait for sync to complete or run `animica sync status`.")
        raise typer.Exit(code=1)


@app.command("send")
def send(
    from_addr: str = typer.Option(..., "--from", help="Sender address (anim1...)"),
    to_addr: str = typer.Option(..., "--to", help="Recipient address (anim1... )"),
    value: Optional[str] = typer.Option(None, "--value", help="Amount in ANM (whole/decimal)"),
    value_nanm: Optional[int] = typer.Option(
        None, "--value-nanm", help="Amount in base units (nANM). Overrides --value."
    ),
    nonce: Optional[int] = typer.Option(None, "--nonce", help="Nonce override (default: auto)"),
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
    _ensure_node_ready_for_tx(rpc)
    _warn_if_unsynced(rpc)

    # Resolve chain identity
    chain_identity = _get_chain_identity(rpc)
    cid = int(chain_id) if chain_id is not None else int(chain_identity.get("chainId"))
    fork_id = chain_identity.get("forkId")

    # Nonce + fee defaults
    nonce_source = "override" if nonce is not None else "auto"
    nonce = int(nonce) if nonce is not None else _next_nonce(rpc, from_addr)
    fee = int(max_fee) if max_fee is not None else _get_default_max_fee(rpc)

    # Value conversion
    try:
        value_base, value_source = _parse_value_to_base_units(value, value_nanm)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

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
        console.print(f"nonce: using {nonce_source} => {nonce}")
        console.print(f"maxFee: using {'override' if max_fee is not None else 'default'} => {fee}")
        console.print(f"value_input: {value if value is not None else value_nanm} ({value_source})")
        console.print(f"value_base_units: {value_base}")
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
        if e.code == -32002:
            console.print("\n[bold red]Node is still syncing; transaction submission is unavailable.[/bold red]")
            if e.data is not None:
                console.print(Pretty(e.data))
            console.print("\n[yellow]Tip:[/yellow] Wait for sync to complete or run `animica sync status`.")
            raise typer.Exit(code=1)
        # Some nodes use alternate method naming
        if e.code in (-32601,):
            tx_hash = _rpc(rpc, "tx_sendRawTransaction", [raw_hex])
        else:
            _format_rpc_error(e)
            raise typer.Exit(code=1) from e

    _maybe_force_sync(rpc, verbose=verbose)

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
        console.print("  • Internal mempool error (transaction submitted but not persisted)")
        console.print("")
        console.print("The transaction will NOT be mined. Please check:")
        console.print("  animica mempool list                    # Check pending transactions")
        console.print(f"  animica rpc call state.getNonce '[\"{from_addr}\"]'  # Check account nonce")
        raise typer.Exit(code=1)

    console.print("\n[bold green]=== Transaction Sent ===[/bold green]")
    console.print("Transaction Submitted")
    console.print(f"Tx Hash: {tx_hash}")
    console.print("Transaction broadcast successfully")
    console.print(
        {
            "tx_hash": tx_hash,
            "from": from_addr,
            "to": to_addr,
            "value": value_base,
            "nonce": nonce,
            "chain_id": cid,
            "rpc_url": rpc,
        }
    )
    if verbose:
        console.print("\n[bold]TX BODY[/bold]")
        console.print(Pretty(body))


@app.command("status")
def status(
    tx_hash: str = typer.Argument(..., help="Transaction hash (0x...)"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="RPC URL (default: node)"),
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using remote bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
    ),
):
    """
    Show transaction status (mempool, inclusion, confirmations, reorg).
    """
    rpc = _resolve_rpc_url(rpc_url)
    guard_bootstrap_rpc(rpc, allow_remote=allow_remote_rpc, method="tx.getStatus")
    _warn_if_unsynced(rpc)

    try:
        result = _rpc(rpc, "tx.getStatus", [tx_hash])
    except RpcError as e:
        if e.code in (-32601,):
            result = _rpc(rpc, "tx.getTransactionStatus", [tx_hash])
        else:
            _format_rpc_error(e)
            raise typer.Exit(code=1) from e

    console.print("\n[bold]Transaction Status[/bold]")
    console.print(Pretty(result))
