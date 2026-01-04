from __future__ import annotations

import hashlib
import json
import os
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
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
DEFAULT_TX_TTL_BLOCKS = 120

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


@dataclass
class ChainIdentityResolution:
    identity: dict[str, Any]
    source: str
    rpc_reachable: bool
    attempts: list[str]


class ChainIdentityResolutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        rpc_reachable: bool,
        attempts: list[str],
    ) -> None:
        super().__init__(message)
        self.rpc_reachable = rpc_reachable
        self.attempts = attempts


class NonceResolutionError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        rpc_reachable: bool,
        attempts: list[str],
        nonce_source: str,
    ) -> None:
        super().__init__(message)
        self.rpc_reachable = rpc_reachable
        self.attempts = attempts
        self.nonce_source = nonce_source


class ValidityWindowResolutionError(RuntimeError):
    pass


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


def _get_mempool_status(rpc: str, tx_hash: str, *, verbose: bool = False) -> tuple[bool, dict[str, Any] | None]:
    mempool_status: dict[str, Any] | None = None
    try:
        status = _rpc(rpc, "mempool.getStatus", [tx_hash])
        if isinstance(status, dict):
            mempool_status = status
            state = status.get("state")
            known = status.get("known")
            if known is True and state in {"pending", "staged"}:
                return True, mempool_status
            return False, mempool_status
    except RpcError as e:
        if verbose:
            console.print(f"[dim]mempool.getStatus not available (code={e.code}), trying mempool.getPending...[/dim]")
    try:
        pending = _rpc(rpc, "mempool.getPending", [])
        if isinstance(pending, list) and tx_hash in pending:
            return True, mempool_status
    except RpcError as e2:
        if verbose:
            console.print(f"[dim]mempool.getPending failed (code={e2.code}), trying mempool.explain...[/dim]")
        try:
            explain = _rpc(rpc, "mempool.explain", [tx_hash])
            if isinstance(explain, dict):
                status = explain.get("status")
                if status != "not_found":
                    return True, mempool_status
                if verbose:
                    console.print(f"[yellow]mempool.explain status: {status}[/yellow]")
        except RpcError as e3:
            if verbose:
                console.print(f"[dim]mempool.explain also failed (code={e3.code})[/dim]")
    return False, mempool_status


def _nonce_mismatch_from_status(status: dict[str, Any] | None) -> tuple[str | None, int | None, int | None]:
    if not isinstance(status, dict):
        return None, None, None
    reason = status.get("reason")
    details = status.get("details")
    if isinstance(details, dict):
        expected = details.get("expected") or details.get("expected_nonce")
        got = details.get("got") or details.get("got_nonce")
    else:
        expected = None
        got = None
    expected = _coerce_int(expected)
    got = _coerce_int(got)
    return reason, expected, got


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


def _is_rpc_unreachable(exc: Exception) -> bool:
    request_exc = getattr(requests, "RequestException", Exception)
    return isinstance(exc, request_exc)


def _get_chain_id_from_rpc(rpc_url: str) -> tuple[int | None, list[str], bool]:
    attempts: list[str] = []
    rpc_reachable = True
    for m in ("chain.getChainId", "chain_id", "net_version", "eth_chainId", "chainId"):
        try:
            v = _rpc(rpc_url, m, [])
            cid = _coerce_int(v)
            if cid is not None:
                attempts.append(f"{m} -> {cid}")
                return cid, attempts, rpc_reachable
            attempts.append(f"{m} returned {v!r} (unusable)")
        except Exception as exc:
            if _is_rpc_unreachable(exc):
                rpc_reachable = False
            attempts.append(f"{m} failed: {exc}")
    return None, attempts, rpc_reachable


def _resolve_local_chain_identity(
    chain_id_override: Optional[int],
) -> tuple[dict[str, Any] | None, str | None]:
    if chain_id_override is not None:
        return {"chainId": int(chain_id_override), "forkId": None}, "CLI --chain-id"

    env_chain_id = os.environ.get("ANIMICA_CHAIN_ID")
    if env_chain_id:
        parsed = _coerce_int(env_chain_id)
        if parsed is not None:
            return {"chainId": parsed, "forkId": None}, "ANIMICA_CHAIN_ID"

    network_hint = os.environ.get("ANIMICA_NETWORK")
    if not network_hint:
        try:
            from animica.config import _get_cli_state_network

            network_hint = _get_cli_state_network()
        except Exception:
            network_hint = None
    if network_hint:
        try:
            cfg = load_network_config(network_hint)
            return (
                {"chainId": int(cfg.chain_id), "forkId": None},
                f"network config ({cfg.name})",
            )
        except Exception:
            return None, None

    return None, None


def _get_chain_identity(
    rpc_url: str,
    *,
    chain_id_override: Optional[int] = None,
) -> ChainIdentityResolution:
    attempts: list[str] = []
    rpc_reachable = True

    try:
        ident = _rpc(rpc_url, "chain.getChainIdentity", [])
        if isinstance(ident, dict) and _coerce_int(ident.get("chainId")) is not None:
            attempts.append("chain.getChainIdentity -> success")
            return ChainIdentityResolution(
                identity=ident,
                source="rpc:chain.getChainIdentity",
                rpc_reachable=True,
                attempts=attempts,
            )
        attempts.append("chain.getChainIdentity returned invalid payload")
    except Exception as exc:
        if _is_rpc_unreachable(exc):
            rpc_reachable = False
        attempts.append(f"chain.getChainIdentity failed: {exc}")

    cid, rpc_attempts, rpc_ok = _get_chain_id_from_rpc(rpc_url)
    attempts.extend(rpc_attempts)
    rpc_reachable = rpc_reachable and rpc_ok
    if cid is not None:
        return ChainIdentityResolution(
            identity={"chainId": cid, "forkId": None},
            source="rpc:chainId",
            rpc_reachable=rpc_reachable,
            attempts=attempts,
        )

    local_identity, source = _resolve_local_chain_identity(chain_id_override)
    if local_identity is not None and source is not None:
        return ChainIdentityResolution(
            identity=local_identity,
            source=source,
            rpc_reachable=rpc_reachable,
            attempts=attempts,
        )

    raise ChainIdentityResolutionError(
        "RPC unreachable and no local chain identity found. "
        "Pass --chain-id/--network or set ANIMICA_CHAIN_ID/ANIMICA_NETWORK.",
        rpc_reachable=rpc_reachable,
        attempts=attempts,
    )


_NONCE_CACHE: dict[tuple[str, str], int] = {}

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover - windows/non-posix environments
    fcntl = None  # type: ignore[assignment]


@contextmanager
def _nonce_lock(address: str):
    if fcntl is None:
        yield
        return
    lock_dir = Path(os.path.expanduser("~/.animica/locks"))
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe_addr = "".join(ch if ch.isalnum() else "_" for ch in address)
    lock_path = lock_dir / f"nonce-{safe_addr}.lock"
    with open(lock_path, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _coerce_int(value: Any) -> int | None:
    if isinstance(value, dict):
        for key in ("nonce", "result", "value"):
            if key in value:
                return _coerce_int(value[key])
        return None
    if isinstance(value, int):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith(("0x", "0X")):
            try:
                return int(text, 16)
            except ValueError:
                return None
        if text.isdigit():
            return int(text)
    return None


def _probe_nonce_methods(
    rpc_url: str,
    methods: list[tuple[str, list[Any]]],
    *,
    verbose: bool = False,
) -> tuple[int | None, list[str], bool]:
    attempts: list[str] = []
    rpc_reachable = False
    for method, params in methods:
        attempts.append(method)
        try:
            v = _rpc(rpc_url, method, params)
            rpc_reachable = True
            parsed = _coerce_int(v)
            if parsed is not None:
                return parsed, attempts, rpc_reachable
            if verbose:
                console.print(f"[dim]_probe_nonce_methods: {method} returned unparseable value {v}[/dim]")
        except RpcError as exc:
            rpc_reachable = True
            if verbose:
                console.print(f"[dim]_probe_nonce_methods: {method} RPC error: {exc}[/dim]")
        except Exception as exc:
            if verbose:
                console.print(f"[dim]_probe_nonce_methods: {method} failed: {exc}[/dim]")
            continue
    return None, attempts, rpc_reachable


def _confirmed_nonce_methods(addr: str) -> list[tuple[str, list[Any]]]:
    return [
        ("state.getNonce", [addr, "latest"]),
        ("state.getNonce", [addr]),
        ("tx.getTransactionCount", [addr]),
        ("state.getTransactionCount", [addr]),
        ("state.getNonce", [{"address": addr}]),
    ]


def _pending_nonce_methods(addr: str) -> list[tuple[str, list[Any]]]:
    return [
        ("state.getNextNonce", [addr]),
        ("state.getNonce", [addr, "pending"]),
        ("state.getPendingNonce", [addr]),
        ("state_getNextNonce", [addr]),
        ("state_getPendingNonce", [addr]),
        ("tx.getTransactionCount", [addr]),
        ("state.getTransactionCount", [addr]),
    ]


def _pending_nonce_from_mempool(rpc_url: str, addr: str, confirmed_nonce: int) -> int | None:
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
            tx_nonce = _coerce_int(entry.get("nonce"))
            if tx_nonce is None:
                continue
            if highest_pending_nonce is None or tx_nonce > highest_pending_nonce:
                highest_pending_nonce = tx_nonce

    if highest_pending_nonce is None:
        return None
    return max(confirmed_nonce, highest_pending_nonce + 1)


def _get_confirmed_nonce(rpc_url: str, addr: str, *, verbose: bool = False) -> tuple[int | None, list[str], bool]:
    return _probe_nonce_methods(rpc_url, _confirmed_nonce_methods(addr), verbose=verbose)


def _get_pending_nonce(rpc_url: str, addr: str, *, verbose: bool = False) -> tuple[int | None, list[str], bool]:
    pending_nonce, attempts, rpc_reachable = _probe_nonce_methods(
        rpc_url,
        _pending_nonce_methods(addr),
        verbose=verbose,
    )
    if pending_nonce is not None:
        return pending_nonce, attempts, rpc_reachable

    confirmed_nonce, confirmed_attempts, confirmed_reachable = _get_confirmed_nonce(
        rpc_url, addr, verbose=verbose
    )
    attempts = attempts + confirmed_attempts
    rpc_reachable = rpc_reachable or confirmed_reachable
    if confirmed_nonce is None:
        return None, attempts, rpc_reachable

    pending_from_mempool = _pending_nonce_from_mempool(rpc_url, addr, confirmed_nonce)
    if pending_from_mempool is None:
        return confirmed_nonce, attempts, rpc_reachable
    return pending_from_mempool, attempts, rpc_reachable


def _get_next_nonce(
    rpc_url: str,
    addr: str,
    *,
    nonce_source: str = "confirmed",
    verbose: bool = False,
) -> int:
    """
    Fetch the next usable nonce from the RPC server.

    This queries confirmed or pending nonce methods (with fallbacks) and
    optionally uses pending data when confirmed nonce methods are unavailable.

    Args:
        rpc_url: The RPC endpoint URL
        addr: The sender address
        nonce_source: confirmed or pending
        verbose: If True, log query attempts
        
    Returns:
        The next usable nonce
    """
    nonce_source = nonce_source.lower().strip() if nonce_source else "confirmed"
    if nonce_source not in {"confirmed", "pending"}:
        raise ValueError("nonce_source must be 'confirmed' or 'pending'")

    if nonce_source == "confirmed":
        confirmed_nonce, confirmed_attempts, confirmed_reachable = _get_confirmed_nonce(
            rpc_url, addr, verbose=verbose
        )
        if confirmed_nonce is not None:
            return confirmed_nonce
        pending_nonce, pending_attempts, pending_reachable = _get_pending_nonce(
            rpc_url, addr, verbose=verbose
        )
        if pending_nonce is not None:
            console.print(
                "[yellow]Confirmed nonce unavailable; using pending nonce from node "
                "(may be unsafe if node is buggy).[/yellow]"
            )
            return pending_nonce
        raise NonceResolutionError(
            "Could not determine confirmed nonce from node.",
            rpc_reachable=confirmed_reachable or pending_reachable,
            attempts=confirmed_attempts + pending_attempts,
            nonce_source=nonce_source,
        )

    pending_nonce, pending_attempts, pending_reachable = _get_pending_nonce(
        rpc_url, addr, verbose=verbose
    )
    if pending_nonce is not None:
        return pending_nonce
    confirmed_nonce, confirmed_attempts, confirmed_reachable = _get_confirmed_nonce(
        rpc_url, addr, verbose=verbose
    )
    if confirmed_nonce is not None:
        console.print(
            "[yellow]Pending nonce unavailable; using confirmed nonce from node.[/yellow]"
        )
        return confirmed_nonce
    raise NonceResolutionError(
        "Could not determine pending nonce from node.",
        rpc_reachable=confirmed_reachable or pending_reachable,
        attempts=pending_attempts + confirmed_attempts,
        nonce_source=nonce_source,
    )


def _next_nonce(
    rpc_url: str,
    addr: str,
    *,
    refresh: bool = False,
    verbose: bool = False,
    nonce_source: str = "confirmed",
) -> int:
    """
    Get the next nonce for an address, with caching.
    
    Args:
        rpc_url: The RPC endpoint URL
        addr: The sender address
        refresh: If True, skip cache and query RPC
        verbose: If True, log nonce resolution details
        
    Returns:
        The next nonce to use
    """
    base = _get_next_nonce(rpc_url, addr, verbose=verbose, nonce_source=nonce_source)
    key = (rpc_url, addr)
    cached = _NONCE_CACHE.get(key)
    
    if not refresh and cached is not None and cached >= base:
        # Use cached + 1 if we have a higher cached value
        result = cached + 1
        if verbose:
            console.print(f"[dim]_next_nonce: using cached+1: {result} (base={base}, cached={cached})[/dim]")
        _NONCE_CACHE[key] = result
        return result
    
    # Use base from RPC and cache it
    if verbose:
        console.print(f"[dim]_next_nonce: using RPC base: {base} (cached={cached}, refresh={refresh})[/dim]")
    _NONCE_CACHE[key] = base
    return base


def _print_nonce_resolution_error(exc: NonceResolutionError) -> None:
    console.print(f"\n[bold red]Error:[/bold red] {exc}")
    if exc.attempts:
        seen: set[str] = set()
        ordered_attempts = []
        for method in exc.attempts:
            if method not in seen:
                seen.add(method)
                ordered_attempts.append(method)
        console.print(
            f"[dim]RPC reachable: {exc.rpc_reachable}. Tried: {', '.join(ordered_attempts)}[/dim]"
        )
    console.print(
        "[yellow]Tip:[/yellow] Upgrade the node, ensure state.getNonce/state.getNextNonce is enabled, "
        "or pass --nonce manually (or use --nonce-source pending)."
    )


def _extract_nonce_mismatch(data: Any, *, verbose: bool = False) -> tuple[str | None, int | None, int | None]:
    """
    Extract nonce mismatch information from RPC error data.
    
    Handles two error structures:
    1. Wrapped mempool errors: {"mempoolError": {"reason": ..., "context": {...}}}
    2. Direct context: {"reason": ..., "expected_nonce": ..., "got_nonce": ...}
    
    Args:
        data: The error data dictionary from RPC response
        verbose: If True, log extraction details
        
    Returns:
        Tuple of (reason, expected_nonce, got_nonce)
    """
    if not isinstance(data, dict):
        if verbose:
            console.print(f"[dim]_extract_nonce_mismatch: data is not dict, got {type(data).__name__}[/dim]")
        return None, None, None
    
    reason = None
    expected = None
    got = None
    
    # Try wrapped mempoolError first (RPC layer wraps mempool errors)
    mempool_error = data.get("mempoolError")
    if isinstance(mempool_error, dict):
        reason = mempool_error.get("reason")
        context = mempool_error.get("context")
        if isinstance(context, dict):
            expected = context.get("expected_nonce") or context.get("expected")
            got = context.get("got_nonce") or context.get("got")
        if verbose:
            console.print(f"[dim]_extract_nonce_mismatch: from mempoolError: reason={reason}, expected={expected}, got={got}[/dim]")
    else:
        # Try direct context (mempool.getStatus or older error formats)
        reason = data.get("reason")
        expected = data.get("expected") or data.get("expected_nonce") or data.get("highest")
        got = data.get("got") or data.get("got_nonce")
        if verbose:
            console.print(f"[dim]_extract_nonce_mismatch: from direct context: reason={reason}, expected={expected}, got={got}[/dim]")
    
    expected = _coerce_int(expected)
    got = _coerce_int(got)
    
    return reason, expected, got


def _format_nonce_mismatch(
    reason: str | None,
    expected: int | None,
    got: int | None,
    *,
    rpc_url: str | None = None,
    addr: str | None = None,
    verbose: bool = False,
) -> None:
    label = "nonce mismatch"
    if reason in {"nonce_too_low", "nonce_gap", "nonce_too_high", "bad_nonce"}:
        label = reason.replace("_", " ")
    console.print(f"\n[bold red]Nonce error:[/bold red] {label}")
    if expected is not None or got is not None:
        console.print(f"  Expected: {expected if expected is not None else '?'}")
        console.print(f"  Got:      {got if got is not None else '?'}")

    confirmed_nonce: int | None = None
    pending_nonce: int | None = None
    if rpc_url and addr:
        try:
            confirmed_nonce, _, _ = _get_confirmed_nonce(rpc_url, addr, verbose=verbose)
        except Exception as exc:
            if verbose:
                console.print(f"[dim]Failed to fetch confirmed nonce: {exc}[/dim]")
        try:
            pending_nonce, _, _ = _get_pending_nonce(rpc_url, addr, verbose=verbose)
        except Exception as exc:
            if verbose:
                console.print(f"[dim]Failed to fetch pending nonce: {exc}[/dim]")
        if confirmed_nonce is not None:
            console.print(f"  Chain nonce (confirmed): {confirmed_nonce}")
        if pending_nonce is not None:
            console.print(f"  Pending nonce:          {pending_nonce}")

    suggested = expected
    if suggested is None:
        suggested = pending_nonce if pending_nonce is not None else confirmed_nonce
    if suggested is not None:
        console.print(f"\n[yellow]Suggestion:[/yellow] retry with [bold]--nonce {suggested}[/bold].")

    if reason in {"nonce_gap", "nonce_too_high"}:
        console.print(
            "[yellow]Note:[/yellow] Your account has missing intermediate nonces. "
            "Wait for earlier transactions to land or clear pending transactions before retrying."
        )

    console.print("\n[yellow]Tip:[/yellow] Refresh nonce with:")
    console.print("  animica rpc call state.getNextNonce '<address>'")
    console.print("or")
    console.print("  animica rpc call state.getNextNonce '[\"<address>\"]'")
    console.print("\n[yellow]Note:[/yellow] The CLI will automatically retry with the correct nonce if --nonce is not specified.")


def _next_retry_nonce(
    rpc_url: str, addr: str, *, expected: int | None, got: int | None, verbose: bool = False
) -> int:
    """
    Determine the nonce to use for a retry after a nonce mismatch error.
    
    Fetches a fresh pending nonce from RPC and uses the max of (expected, fresh_pending)
    to avoid sending a stale nonce if the mempool advanced between attempts.
    
    Args:
        rpc_url: The RPC endpoint URL
        addr: The sender address
        expected: The expected nonce from the error (if available)
        got: The nonce that was rejected (for logging)
        verbose: If True, log detailed nonce resolution steps
    
    Returns:
        The nonce to use for the retry attempt
    """
    # Always fetch a fresh pending nonce to avoid stale values
    if verbose:
        console.print(f"[dim]Fetching fresh pending nonce from RPC (rejected: {got}, expected from error: {expected})[/dim]")
    
    fresh_pending = _get_next_nonce(rpc_url, addr, verbose=verbose, nonce_source="pending")
    
    if expected is not None:
        # Use the max of (expected, fresh_pending) to handle cases where
        # the mempool advanced between the error and the retry
        retry_nonce = max(int(expected), fresh_pending)
        if verbose:
            console.print(f"[dim]Retry nonce: max(expected={expected}, fresh_pending={fresh_pending}) = {retry_nonce}[/dim]")
        elif retry_nonce != expected:
            console.print(f"[dim]Using fresh pending nonce {retry_nonce} (error expected: {expected})[/dim]")
        else:
            console.print(f"[dim]Using expected nonce from error: {expected}[/dim]")
        return retry_nonce
    
    # No expected nonce in error, use fresh pending
    if verbose:
        console.print(f"[dim]No expected nonce in error, using fresh pending: {fresh_pending}[/dim]")
    else:
        console.print(f"[dim]Using fresh pending nonce: {fresh_pending}[/dim]")
    
    return fresh_pending


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


def _extract_head_height(status: Any) -> int | None:
    if isinstance(status, dict):
        for key in (
            "head_height",
            "headHeight",
            "height",
            "blockHeight",
            "currentBlock",
            "best_block_height",
            "bestBlockHeight",
        ):
            value = _coerce_int(status.get(key))
            if value is not None:
                return value
        return None
    return _coerce_int(status)


def _get_head_height(rpc_url: str) -> int | None:
    try:
        head = _rpc(rpc_url, "chain.getHead", [])
    except Exception:
        return None
    if isinstance(head, dict):
        return _coerce_int(head.get("height") or head.get("number") or head.get("head_height"))
    return _coerce_int(head)


def _resolve_validity_window(
    rpc_url: str,
    *,
    valid_from: Optional[int],
    valid_until: Optional[int],
    ttl_blocks: Optional[int],
    head_height_hint: Optional[int],
    verbose: bool = False,
) -> tuple[int, int]:
    ttl_value = int(ttl_blocks) if ttl_blocks is not None else DEFAULT_TX_TTL_BLOCKS
    if ttl_value <= 0:
        raise ValueError("TTL must be a positive block count.")

    resolved_from = int(valid_from) if valid_from is not None else None
    resolved_until = int(valid_until) if valid_until is not None else None
    if resolved_from is not None and resolved_from < 0:
        raise ValueError("--valid-from must be ≥ 0.")
    if resolved_until is not None and resolved_until < 0:
        raise ValueError("--valid-until must be ≥ 0.")

    if resolved_from is None or resolved_until is None:
        head_height = head_height_hint
        if head_height is None:
            head_height = _get_head_height(rpc_url)
        if head_height is None:
            raise ValidityWindowResolutionError(
                "Cannot infer validity window without RPC; pass --valid-from/--valid-until (or --ttl)."
            )
        if resolved_from is None:
            resolved_from = head_height
        if resolved_until is None:
            resolved_until = resolved_from + max(1, ttl_value)

    if resolved_until <= resolved_from:
        resolved_until = resolved_from + 1
        if verbose:
            console.print(
                f"[yellow]Adjusted valid-until to {resolved_until} to be > valid-from ({resolved_from}).[/yellow]"
            )

    return resolved_from, resolved_until


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
    valid_after: int,
    valid_until: int,
    salt: bytes,
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
        "validAfter": int(valid_after),
        "validUntil": int(valid_until),
        "salt": bytes(salt),
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


def _ensure_node_ready_for_tx(rpc: str) -> int | None:
    try:
        status = _rpc(rpc, "sync.getStatus", [{"source": "refresh"}])
    except Exception:
        try:
            status = _rpc(rpc, "sync.getStatus", [])
        except Exception:
            return None

    head_height = _extract_head_height(status)

    if not isinstance(status, dict):
        return head_height
    allowed, _info = assess_tx_submission_readiness(status)
    if allowed:
        return head_height

    phase = status.get("phase") or status.get("state")
    phase_name = str(phase).upper() if phase is not None else ""
    if status.get("synchronized") is False or phase_name:
        console.print("\n[bold red]Node is still syncing; transaction submission is unavailable.[/bold red]")
        console.print(Pretty(status))
        console.print("\n[yellow]Tip:[/yellow] Wait for sync to complete or run `animica sync status`.")
        raise typer.Exit(code=1)

    return head_height


@app.command("send")
def send(
    from_addr: str = typer.Option(..., "--from", help="Sender address (anim1...)"),
    to_addr: str = typer.Option(..., "--to", help="Recipient address (anim1... )"),
    value: Optional[str] = typer.Option(None, "--value", help="Amount in ANM (whole/decimal)"),
    value_nanm: Optional[int] = typer.Option(
        None, "--value-nanm", help="Amount in base units (nANM). Overrides --value."
    ),
    nonce: str = typer.Option("auto", "--nonce", help="Nonce override (default: auto)"),
    nonce_source: str = typer.Option(
        "confirmed", "--nonce-source", help="Nonce source: confirmed|pending (default: confirmed)"
    ),
    valid_from: Optional[int] = typer.Option(
        None, "--valid-from", help="First valid block height (default: current head height)"
    ),
    valid_until: Optional[int] = typer.Option(
        None, "--valid-until", help="Last valid block height (default: head height + TTL)"
    ),
    ttl_blocks: Optional[int] = typer.Option(
        None, "--ttl", help=f"Validity window TTL in blocks (default: {DEFAULT_TX_TTL_BLOCKS})"
    ),
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
    head_height_hint = _ensure_node_ready_for_tx(rpc)
    _warn_if_unsynced(rpc)

    # Resolve chain identity
    try:
        chain_resolution = _get_chain_identity(rpc, chain_id_override=chain_id)
    except ChainIdentityResolutionError as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}")
        if exc.attempts:
            console.print(
                f"[dim]RPC reachable: {exc.rpc_reachable}. Tried: {', '.join(exc.attempts)}[/dim]"
            )
        raise typer.Exit(code=1) from exc
    chain_identity = chain_resolution.identity
    cid = int(chain_id) if chain_id is not None else int(chain_identity.get("chainId"))
    fork_id = chain_identity.get("forkId")
    if chain_resolution.source != "rpc:chain.getChainIdentity":
        console.print(
            f"[yellow]RPC chain identity unavailable; using chainId={cid} "
            f"from {chain_resolution.source}.[/yellow]"
        )
        if chain_resolution.attempts:
            console.print(
                f"[dim]RPC reachable: {chain_resolution.rpc_reachable}. "
                f"Tried: {', '.join(chain_resolution.attempts)}[/dim]"
            )

    # Nonce + fee defaults
    nonce_value: Optional[int] = None
    if nonce.lower().strip() != "auto":
        try:
            nonce_value = int(nonce)
        except ValueError as exc:
            raise typer.BadParameter("Nonce must be 'auto' or an integer value.") from exc
    nonce_source = nonce_source.lower().strip()
    if nonce_source not in {"confirmed", "pending"}:
        raise typer.BadParameter("Nonce source must be 'confirmed' or 'pending'.")
    nonce_source_label = "override" if nonce_value is not None else nonce_source
    fee = int(max_fee) if max_fee is not None else _get_default_max_fee(rpc)

    try:
        valid_after, valid_until = _resolve_validity_window(
            rpc,
            valid_from=valid_from,
            valid_until=valid_until,
            ttl_blocks=ttl_blocks,
            head_height_hint=head_height_hint,
            verbose=verbose,
        )
    except ValidityWindowResolutionError as exc:
        console.print(f"\n[bold red]Error:[/bold red] {exc}")
        raise typer.Exit(code=1) from exc
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    salt = os.urandom(16)

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

    max_attempts = 3 if nonce_value is None else 1
    next_nonce_value: Optional[int] = None
    tx_hash = None
    last_body = None
    last_nonce = None
    nonce_lock = _nonce_lock(from_addr) if nonce_value is None else nullcontext()

    with nonce_lock:
        for attempt in range(max_attempts):
            try:
                if nonce_value is not None:
                    attempt_nonce = int(nonce_value)
                elif next_nonce_value is not None:
                    attempt_nonce = next_nonce_value
                    next_nonce_value = None
                else:
                    attempt_nonce = _next_nonce(
                        rpc,
                        from_addr,
                        refresh=(attempt > 0),
                        verbose=verbose,
                        nonce_source=nonce_source,
                    )
            except NonceResolutionError as exc:
                _print_nonce_resolution_error(exc)
                raise typer.Exit(code=1) from exc

            body = _build_tx_body(
                chain_id=cid,
                from_addr=from_addr,
                to_addr=to_addr,
                nonce=attempt_nonce,
                value_base_units=value_base,
                gas_limit=gas_limit,
                max_fee=fee,
                data=b"",
                valid_after=valid_after,
                valid_until=valid_until,
                salt=salt,
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
                console.print(
                    {
                        "rpc_url": rpc,
                        "chain_id": cid,
                        "chain_id_source": "cli override" if chain_id is not None else "node:chain.getChainId",
                    }
                )
                console.print("")
                console.print(f"nonce: using {nonce_source_label} => {attempt_nonce}")
                console.print(f"maxFee: using {'override' if max_fee is not None else 'default'} => {fee}")
                console.print(f"valid_from: {valid_after}")
                console.print(f"valid_until: {valid_until}")
                console.print(f"ttl_blocks: {valid_until - valid_after}")
                console.print(f"salt_len: {len(salt)}")
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
            def _extract_send_hash(result: Any) -> str:
                if isinstance(result, str):
                    return result
                if isinstance(result, dict):
                    for key in ("tx_hash", "hash", "txHash", "transactionHash"):
                        value = result.get(key)
                        if isinstance(value, str):
                            return value
                raise ValueError(f"Unexpected tx.sendRawTransaction result: {result!r}")

            try:
                send_result = _rpc(rpc, "tx.sendRawTransaction", [raw_hex])
                tx_hash = _extract_send_hash(send_result)
                if isinstance(send_result, dict):
                    accepted = send_result.get("accepted_to_mempool")
                    persisted = send_result.get("persisted_to_chain")
                    hint = send_result.get("hint")
                    if accepted and not persisted and hint:
                        console.print(f"[yellow]{hint}[/yellow]")
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
                reason, expected, got = _extract_nonce_mismatch(e.data, verbose=verbose)
                if nonce_value is None and reason in {"nonce_too_low", "nonce_gap"} and attempt + 1 < max_attempts:
                    # Invalidate cache on nonce mismatch to prevent stale cached+1 reuse
                    cache_key = (rpc, from_addr)
                    if cache_key in _NONCE_CACHE:
                        if verbose:
                            console.print(f"[dim]Invalidating nonce cache (was {_NONCE_CACHE[cache_key]})[/dim]")
                        del _NONCE_CACHE[cache_key]
                    try:
                        next_nonce_value = _next_retry_nonce(
                            rpc, from_addr, expected=expected, got=got, verbose=verbose
                        )
                    except NonceResolutionError as exc:
                        _print_nonce_resolution_error(exc)
                        raise typer.Exit(code=1) from exc
                    console.print(
                        f"[yellow]nonce mismatch (reason={reason}), retrying with nonce={next_nonce_value}[/yellow]"
                    )
                    continue
                if reason in {"nonce_too_low", "nonce_gap"}:
                    _format_nonce_mismatch(
                        reason,
                        expected,
                        got,
                        rpc_url=rpc,
                        addr=from_addr,
                        verbose=verbose,
                    )
                    raise typer.Exit(code=1)
                # Some nodes use alternate method naming
                if e.code in (-32601,):
                    send_result = _rpc(rpc, "tx_sendRawTransaction", [raw_hex])
                    tx_hash = _extract_send_hash(send_result)
                else:
                    _format_rpc_error(e)
                    raise typer.Exit(code=1) from e

            tx_in_mempool, mempool_status = _get_mempool_status(rpc, tx_hash, verbose=verbose)
            if tx_in_mempool:
                last_body = body
                last_nonce = attempt_nonce
                # Update cache with the successful nonce for future transactions
                cache_key = (rpc, from_addr)
                _NONCE_CACHE[cache_key] = attempt_nonce
                if verbose:
                    console.print(f"[dim]Updated nonce cache to {attempt_nonce} after successful submission[/dim]")
                break

            reason, expected, got = _nonce_mismatch_from_status(mempool_status)
            if nonce_value is None and reason in {"nonce_too_low", "nonce_gap"} and attempt + 1 < max_attempts:
                # Invalidate cache on nonce mismatch to prevent stale cached+1 reuse
                cache_key = (rpc, from_addr)
                if cache_key in _NONCE_CACHE:
                    if verbose:
                        console.print(f"[dim]Invalidating nonce cache (was {_NONCE_CACHE[cache_key]})[/dim]")
                    del _NONCE_CACHE[cache_key]
                try:
                    next_nonce_value = _next_retry_nonce(
                        rpc, from_addr, expected=expected, got=got, verbose=verbose
                    )
                except NonceResolutionError as exc:
                    _print_nonce_resolution_error(exc)
                    raise typer.Exit(code=1) from exc
                console.print(
                    f"[yellow]nonce mismatch (reason={reason}), retrying with nonce={next_nonce_value}[/yellow]"
                )
                continue

            console.print("\n[bold red]=== ERROR: Transaction Not in Mempool ===[/bold red]")
            console.print(f"TX hash: {tx_hash}")
            if mempool_status:
                console.print("Mempool status:")
                console.print(Pretty(mempool_status))
            if reason in {"nonce_too_low", "nonce_gap"}:
                _format_nonce_mismatch(
                    reason,
                    expected,
                    got,
                    rpc_url=rpc,
                    addr=from_addr,
                    verbose=verbose,
                )
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
            console.print(f"  animica rpc call state.getNextNonce {from_addr}  # Check account nonce")
            raise typer.Exit(code=1)

    if tx_hash is None or last_body is None or last_nonce is None:
        raise typer.Exit(code=1)

    _maybe_force_sync(rpc, verbose=verbose)

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
            "nonce": last_nonce,
            "chain_id": cid,
            "rpc_url": rpc,
            "mempool_state": "pending",
        }
    )
    if verbose:
        console.print("\n[bold]TX BODY[/bold]")
        console.print(Pretty(last_body))


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
