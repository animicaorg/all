from __future__ import annotations

import json
import os
import sys
import time
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass
from typing import Any, Dict, Optional
from datetime import datetime, timezone

import typer
from rich.console import Console
from rich.pretty import Pretty

from animica.tx.signing import ChainContext, pq_sign_tx, pq_verify_tx
from animica.config import load_network_config
from animica.cli.paths import ensure_file_dir, secure_file
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
    _debug_tx_event("cli_rpc_request", {"method": method, "url": url, "payload": payload})
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
    wallet_path = _wallet_store_path()
    data = _load_wallet_store(wallet_path)

    # wallets.json shape: {"wallets":[...]}
    entries = data.get("wallets")
    if not isinstance(entries, list):
        raise RuntimeError(f"Unexpected wallets.json format at {wallet_path}")

    for w in entries:
        if str(w.get("address")) == address:
            return _unlock_entry_secret(w, address)
    raise RuntimeError(f"Address not found in {wallet_path}: {address}")


def _unlock_entry_secret(entry: dict[str, Any], address: str) -> dict[str, Any]:
    """Return a wallet entry with a usable plaintext ``secret_key_hex`` (ANM-C07).

    Plaintext entries pass through unchanged. Encrypted entries are decrypted in
    memory using a passphrase from the environment or, if a TTY is attached, an
    interactive prompt. The persisted store is never mutated by this helper.
    """
    # Lazy import: animica.wallet.at_rest pulls in animica.wallet.payment, which
    # imports this module — importing it at module load would be circular.
    from animica.wallet import at_rest

    has_plain = bool(entry.get("secret_key_hex") or entry.get("secretKeyHex"))
    env = entry.get("secret_key_enc")
    if has_plain or not at_rest.is_encrypted_secret(env):
        return entry

    secret = at_rest.resolve_passphrase(None)
    if not secret and sys.stdin.isatty():
        import getpass

        try:
            secret = getpass.getpass(
                "Wallet passphrase (to unlock the encrypted secret key): "
            ) or None
        except Exception:
            secret = None
    if not secret:
        raise RuntimeError(
            "Wallet secret is encrypted at rest (ANM-C07); set "
            "ANIMICA_WALLET_PASSPHRASE (or ANIMICA_WALLET_PASSPHRASE_FILE) to "
            "unlock and sign."
        )
    try:
        unlocked = dict(entry)
        unlocked["secret_key_hex"] = at_rest.decrypt_secret_hex(env, secret, address=address)
        return unlocked
    except at_rest.WalletEncryptionError as exc:
        raise RuntimeError(
            f"Failed to decrypt wallet secret (wrong passphrase or corrupt data): {exc}"
        ) from exc


def _wallet_store_path() -> Path:
    return Path.home() / ".animica" / "wallets.json"


def _load_wallet_store(path: Path, *, passphrase: Optional[str] = None) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Wallet store not found at {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"Unexpected wallets.json format at {path}")
    if "wallets" not in data:
        raise RuntimeError(f"Malformed wallet store at {path}")
    # ANM-C07: transparently decrypt at-rest secrets when a passphrase is
    # available (arg or ANIMICA_WALLET_PASSPHRASE[_FILE]). Without one, encrypted
    # entries stay sealed and the signer path raises a clear, actionable error.
    from animica.wallet import at_rest  # lazy: avoid circular import

    if at_rest.store_is_encrypted(data):
        secret = at_rest.resolve_passphrase(passphrase)
        if secret:
            data = at_rest.decrypt_store_secrets(data, secret)
    return data


def _save_wallet_store(path: Path, store: dict[str, Any]) -> None:
    from animica.wallet import at_rest  # lazy: avoid circular import

    ensure_file_dir(path, sensitive=True)
    to_write = store
    # ANM-C07: never re-persist a transiently-decrypted plaintext secret next to
    # its encrypted envelope. If an entry has a valid envelope, strip any
    # in-memory plaintext before writing so the on-disk store stays encrypted.
    if at_rest.store_is_encrypted(store):
        to_write = dict(store)
        wallets = []
        for w in store.get("wallets", []):
            if isinstance(w, dict) and at_rest.is_encrypted_secret(w.get("secret_key_enc")):
                w = dict(w)
                w.pop("secret_key_hex", None)
                w.pop("secretKeyHex", None)
            wallets.append(w)
        to_write["wallets"] = wallets
    path.write_text(json.dumps(to_write, indent=2), encoding="utf-8")
    secure_file(path)


def _record_pending_tx(
    *,
    from_addr: str,
    to_addr: str,
    tx_hash: str,
    value_base: int,
    fee_base: int,
    chain_id: int,
    nonce: int,
    status: str,
) -> None:
    wallet_path = _wallet_store_path()
    store = _load_wallet_store(wallet_path)
    wallets = store.get("wallets", [])
    if not isinstance(wallets, list):
        raise RuntimeError(f"Malformed wallet store at {wallet_path}")
    target = None
    for entry in wallets:
        if str(entry.get("address")) == from_addr:
            target = entry
            break
    if target is None:
        raise RuntimeError(f"Address not found in {wallet_path}: {from_addr}")

    pending = target.setdefault("pending_txs", [])
    if not isinstance(pending, list):
        pending = []
        target["pending_txs"] = pending

    now = datetime.now(timezone.utc).isoformat()
    reserve_amount = int(value_base) + int(fee_base)
    entry_payload = {
        "tx_hash": tx_hash,
        "from": from_addr,
        "to": to_addr,
        "value": int(value_base),
        "fee_reserved": int(fee_base),
        "reserve_amount": reserve_amount,
        "nonce": int(nonce),
        "chain_id": int(chain_id),
        "status": status,
        "updated_at": now,
    }

    for existing in pending:
        if existing.get("tx_hash") == tx_hash:
            existing.update(entry_payload)
            break
    else:
        entry_payload["created_at"] = now
        pending.append(entry_payload)

    _save_wallet_store(wallet_path, store)


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




def _debug_tx_event(event: str, payload: dict[str, Any]) -> None:
    if os.getenv("ANIMICA_DEBUG_TX", "0") != "1":
        return
    safe_payload = dict(payload)
    for k in ("secret_key", "secret_key_hex", "signature", "sig", "pubkey", "public_key"):
        safe_payload.pop(k, None)
    try:
        console.print(json.dumps({"event": event, **safe_payload}, sort_keys=True, separators=(",", ":")))
    except Exception:
        pass

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
    data = e.data if isinstance(e.data, dict) else None
    mempool_error = data.get("mempoolError") if isinstance(data, dict) else None
    if isinstance(mempool_error, dict):
        reason_code = str(mempool_error.get("reason_code") or mempool_error.get("reason") or "admission_failed")
        reason = str(mempool_error.get("reason") or reason_code)
        message = str(mempool_error.get("message") or e.message)
        context = mempool_error.get("context") if isinstance(mempool_error.get("context"), dict) else {}
        hint = mempool_error.get("hint")
        console.print(f"Rejected: {reason_code} — {message}")
        tx_hash = context.get("tx_hash") or context.get("txHash")
        if tx_hash:
            console.print(f"Tx: {tx_hash}")
        if hint:
            console.print(f"Hint: {hint}")
        if reason_code == "internal_error":
            error_class = mempool_error.get("error_class") or context.get("error_class")
            if error_class:
                console.print(f"Error class: {error_class}")
            if context.get("trace_id"):
                console.print(f"Trace ID: {context.get('trace_id')}")
        console.print("Context:")
        console.print(Pretty(context))
        console.print("Debug now:")
        console.print("  animica node logs --network mainnet")
        console.print("  animica node logs --network devnet")
        console.print("  animica node logs --network local-devnet")
        console.print("  ANIMICA_DEBUG_TX=1 ANIMICA_DEBUG_MEMPOOL=1 animica tx send ...")
        for field in ("from", "to", "value", "nonce", "chain_id", "fee_reserved", "reserve_amount"):
            if field in context:
                console.print(f"  {field}: {context[field]}")
        hints = {
            "invalid_signature": "Check signer scheme and wallet keypair; retry with --debug-signing.",
            "nonce_too_low": "Retry without --nonce to auto-refresh pending nonce.",
            "nonce_gap": "Submit missing lower nonce transactions first.",
            "insufficient_funds": "Fund sender account for value + fee reserve.",
            "chain_id_mismatch": "Set --chain-id to the node's chain id.",
            "tx_already_known": "Transaction already in mempool; wait for inclusion.",
            "fee_too_low": "Increase --max-fee.",
        }
        if reason_code in hints:
            console.print(f"[yellow]Hint:[/yellow] {hints[reason_code]}")
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

    def _normalize_identity(raw: dict[str, Any]) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        chain_id = _coerce_int(raw.get("chainId") or raw.get("chain_id"))
        if chain_id is None:
            return None
        fork_id = _coerce_int(raw.get("forkId") or raw.get("fork_id"))
        normalized = dict(raw)
        normalized["chainId"] = chain_id
        if fork_id is not None:
            normalized["forkId"] = fork_id
        return normalized

    try:
        ident = _rpc(rpc_url, "chain.getChainIdentity", [])
        normalized = _normalize_identity(ident) if isinstance(ident, dict) else None
        if normalized is not None:
            attempts.append("chain.getChainIdentity -> success")
            return ChainIdentityResolution(
                identity=normalized,
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
    nonce_source: str = "pending",
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
    nonce_source: str = "pending",
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


def _coerce_fee_int(name: str, value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _extract_fee_quote(value: Any) -> tuple[int | None, int | None]:
    """
    Canonical tx fee model used by the CLI send pipeline:
      - gasLimit: integer execution unit bound
      - maxFee: integer per-unit price (compatible with legacy gasPrice quote names)

    Compatibility note: fee estimators may return either scalar price values or a quote
    object like {"limit": 21000, "price": 1}. This helper normalizes those shapes into
    scalar numeric fields for tx construction.
    """
    if isinstance(value, dict):
        limit = _coerce_fee_int("limit", value.get("limit") or value.get("gasLimit"))
        price = _coerce_fee_int(
            "price",
            value.get("price")
            or value.get("gasPrice")
            or value.get("maxFee")
            or value.get("max_fee"),
        )
        if limit is not None or price is not None:
            return limit, price
        return None, None

    scalar = _coerce_fee_int("price", value)
    if scalar is not None:
        return None, scalar
    return None, None


def _parse_gas_limit_override(value: Any) -> tuple[int | None, int | None]:
    """
    Parse --gas-limit/--gas input.

    Supports:
      - integer-like values (e.g. 21000, "21000")
      - quote-like dict/json payloads (e.g. '{"limit":21000,"price":1}')
        to preserve compatibility with callers that pass a fee quote object.
    Returns (limit, price_hint).
    """
    if value is None:
        return None, None

    candidate = value
    if isinstance(candidate, str):
        s = candidate.strip()
        if not s:
            return None, None
        if s.isdigit():
            return int(s), None
        if s.startswith("{") and s.endswith("}"):
            try:
                candidate = json.loads(s)
            except Exception:
                candidate = s

    if isinstance(candidate, dict):
        return _extract_fee_quote(candidate)

    parsed = _coerce_fee_int("gasLimit", candidate)
    return parsed, None


def _estimate_fee_quote(rpc_url: str) -> tuple[int | None, int | None]:
    for method_name in (
        "mempool.getFeeQuote",
        "fee.quote",
        "tx.estimateFee",
        "tx.gasPrice",
        "gasPrice",
        "fee.getGasPrice",
    ):
        try:
            result = _rpc(rpc_url, method_name, [])
        except Exception:
            continue
        limit, price = _extract_fee_quote(result)
        if limit is not None or price is not None:
            return limit, price
    return None, None


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
        # Prefer canonical chain head over sync snapshots when deriving default
        # validity windows. sync.getStatus can report optimistic/network tip data.
        head_height = _get_head_height(rpc_url)
        if head_height is None:
            head_height = head_height_hint
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
    # NOTE: v2 tx bodies omit nonce; validity window + salt are required instead.
    # Convert addresses to canonical 32-byte format (digest bytes, not bech32 strings)
    from_bytes = _address_to_32_bytes(from_addr)
    to_bytes = _address_to_32_bytes(to_addr)
    
    return {
        "to": to_bytes,
        "from": from_bytes,
        "value": int(value_base_units),
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




def _chain_context_from_identity(chain_identity: dict[str, Any], *, chain_id: int, domain: str, prehash: str) -> ChainContext:
    genesis_raw = chain_identity.get("genesisHash") or chain_identity.get("genesis") or ""
    genesis = b""
    if isinstance(genesis_raw, str) and genesis_raw.strip():
        try:
            genesis = _hex_to_bytes(genesis_raw)
        except Exception:
            genesis = b""
    network = str(chain_identity.get("network") or chain_identity.get("name") or "unknown")
    fork_id = _coerce_int(chain_identity.get("forkId") or chain_identity.get("fork_id"))
    return ChainContext(
        chain_id=int(chain_id),
        genesis_hash=genesis,
        network=network,
        fork_id=fork_id,
        domain=domain,
        prehash=prehash,
    )

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
    if not lag_known and phase and phase not in {"SYNCED", "TARGET_REACHED"}:
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


def _connected_peer_count(rpc: str, *, verbose: bool = False) -> int:
    """Best-effort lookup of currently connected peer count."""
    try:
        status = _rpc(rpc, "p2p.getStatus", [])
    except Exception as exc:
        if verbose:
            console.print(f"[dim]p2p.getStatus unavailable: {exc}[/dim]")
        return 0

    if not isinstance(status, dict):
        return 0

    for key in ("peers_total", "peer_count", "connected_peers"):
        value = status.get(key)
        try:
            if value is not None:
                return max(0, int(value))
        except Exception:
            continue
    return 0


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
    allowed, info = assess_tx_submission_readiness(status)
    if allowed:
        return head_height

    # Generate appropriate error message based on height status
    head_h = info.get("head_height")
    best_h = info.get("best_header_height")
    max_allowed_behind = info.get("max_allowed_behind")
    
    if head_h and best_h and head_h < best_h:
        blocks_behind = best_h - head_h
        if isinstance(max_allowed_behind, int) and max_allowed_behind >= 0:
            console.print("\n[bold red]Node is too far behind network tip; transaction submission is unavailable.[/bold red]")
            console.print(
                f"[red]Node is behind by {blocks_behind} block{'s' if blocks_behind != 1 else ''} "
                f"(head={head_h}, best={best_h}, allowed lag={max_allowed_behind}).[/red]"
            )
            console.print(
                f"\n[yellow]Tip:[/yellow] Wait until node is within {max_allowed_behind} block"
                f"{'s' if max_allowed_behind != 1 else ''} of height {best_h}, then retry."
            )
        else:
            console.print(f"\n[bold red]Node is not at highest height; transaction submission is unavailable.[/bold red]")
            console.print(f"[red]Node is behind by {blocks_behind} block{'s' if blocks_behind != 1 else ''} (head={head_h}, best={best_h}).[/red]")
            console.print(f"\n[yellow]Tip:[/yellow] Wait for node to sync to height {best_h} before resubmitting.")
    else:
        phase = status.get("phase") or status.get("state")
        phase_name = str(phase).upper() if phase is not None else ""
        console.print("\n[bold red]Node is still syncing; transaction submission is unavailable.[/bold red]")
        if phase_name:
            console.print(f"[red]Sync phase: {phase_name}[/red]")
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
        "pending", "--nonce-source", help="Nonce source: pending|confirmed (default: pending)"
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
    gas_limit: Optional[str] = typer.Option("21000", "--gas-limit", "--gas", help="Gas limit (int) or fee quote JSON object"),
    gas_price: Optional[int] = typer.Option(
        None,
        "--gas-price",
        help="Gas price / fee price (base units, mapped to tx maxFee)",
    ),
    max_fee: Optional[int] = typer.Option(None, "--max-fee", help="Max fee (base units)"),
    domain: str = typer.Option(DEFAULT_DOMAIN, "--domain", help="PQ signing domain"),
    prehash: str = typer.Option(DEFAULT_PREHASH, "--prehash", help="Prehash: sha3-512 | sha3-256"),
    min_peers: Optional[int] = typer.Option(None, "--min-peers", help="Wait for min peer acks (PTL)"),
    wait_timeout: int = typer.Option(30, "--wait-timeout", help="Max wait time for peer acks (seconds)"),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Verbose debug output"),
    debug_signing: bool = typer.Option(False, "--debug-signing", help="Dump canonical sign-bytes debug info"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Simulate mempool admission without inserting"),
    secret_key_hex: Optional[str] = typer.Option(
        None, "--secret-key-hex", help="Secret key hex (for external wallets, bypasses wallets.json lookup)"
    ),
    public_key_hex: Optional[str] = typer.Option(
        None, "--public-key-hex", help="Public key hex (required with --secret-key-hex)"
    ),
    alg_id: Optional[int] = typer.Option(
        None, "--alg-id", help="Signature algorithm ID (4098 for Dilithium3, 65535 for Ed25519, or hex 0x1001/0xFFFF)"
    ),
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
    fork_id = _coerce_int(chain_identity.get("forkId") or chain_identity.get("fork_id"))
    chain_ctx = _chain_context_from_identity(chain_identity, chain_id=cid, domain=domain, prehash=prehash)
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
    gas_limit_override, gas_price_from_gas_limit = _parse_gas_limit_override(gas_limit)
    max_fee_override = int(max_fee) if max_fee is not None else None
    gas_price_override = int(gas_price) if gas_price is not None else None

    quote_limit, quote_price = _estimate_fee_quote(rpc)
    resolved_gas_limit = (
        gas_limit_override if gas_limit_override is not None else (quote_limit if quote_limit is not None else 21000)
    )

    resolved_max_fee: int
    if max_fee_override is not None:
        resolved_max_fee = max_fee_override
    elif gas_price_override is not None:
        resolved_max_fee = gas_price_override
    elif gas_price_from_gas_limit is not None:
        resolved_max_fee = gas_price_from_gas_limit
    elif quote_price is not None:
        resolved_max_fee = quote_price
    else:
        resolved_max_fee = _get_default_max_fee(rpc)

    # Defensive assertion: tx numeric fields must be scalar ints, never fee quote objects.
    if not isinstance(resolved_gas_limit, int):
        raise typer.BadParameter(
            f"BUG: gasLimit must be int; got {type(resolved_gas_limit).__name__}. Please update CLI."
        )
    if not isinstance(resolved_max_fee, int):
        raise typer.BadParameter(
            f"BUG: gasPrice/maxFee must be int; got {type(resolved_max_fee).__name__}. Please update CLI."
        )

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

    # Load wallet keys - either from external parameters or wallet file
    if secret_key_hex and public_key_hex:
        # Using external keys provided via command-line
        if not alg_id:
            raise typer.BadParameter(
                "--alg-id is required when using --secret-key-hex and --public-key-hex"
            )
        pk_hex = public_key_hex
        sk_hex = secret_key_hex
        used_alg_id = alg_id
    elif secret_key_hex or public_key_hex:
        # Partial external keys - error
        raise typer.BadParameter(
            "Both --secret-key-hex and --public-key-hex must be provided together"
        )
    else:
        # Load from wallet file (original behavior)
        try:
            w = _load_wallet_entry(from_addr)
        except FileNotFoundError:
            raise RuntimeError(
                f"Wallet file not found. Please create a wallet using 'animica wallet create' "
                f"or provide signing keys via --secret-key-hex and --public-key-hex options."
            )
        except RuntimeError as e:
            # Re-raise with more helpful message
            raise RuntimeError(
                f"{e}\n\nTip: If this address is from an external wallet, "
                f"provide signing keys using --secret-key-hex, --public-key-hex, and --alg-id options."
            )
        
        # ANM-M07: default a missing alg_id to ml_dsa_65 (0x1003, real FIPS 204),
        # never the forgeable dilithium3 stub (0x1001).
        used_alg_id = int(w.get("alg_id") or w.get("algId") or 0x1003)
        pk_hex = str(w.get("public_key_hex") or w.get("publicKeyHex") or "")
        sk_hex = str(w.get("secret_key_hex") or w.get("secretKeyHex") or "")

        if not sk_hex:
            from animica.wallet import at_rest  # lazy: avoid circular import

            if at_rest.is_encrypted_secret(w.get("secret_key_enc")):
                # Should have been unlocked by _load_wallet_entry; this is a
                # defensive backstop with an actionable message.
                raise RuntimeError(
                    "wallet secret is encrypted at rest; set "
                    "ANIMICA_WALLET_PASSPHRASE to unlock and sign"
                )
        if not pk_hex or not sk_hex:
            raise RuntimeError("wallet entry missing public_key_hex or secret_key_hex")

    pk = _hex_to_bytes(pk_hex)
    sk = _hex_to_bytes(sk_hex)

    max_attempts = 3 if nonce_value is None else 1
    next_nonce_value: Optional[int] = None
    tx_hash = None
    last_body = None
    last_nonce = None
    cli_trace_id = uuid.uuid4().hex
    tx_in_mempool = False
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
                gas_limit=resolved_gas_limit,
                max_fee=resolved_max_fee,
                data=b"",
                valid_after=valid_after,
                valid_until=valid_until,
                salt=salt,
            )
            body_bytes = _cbor(body)

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
                max_fee_source = (
                    "--max-fee"
                    if max_fee_override is not None
                    else "--gas-price"
                    if gas_price_override is not None
                    else "--gas-limit quote"
                    if gas_price_from_gas_limit is not None
                    else "fee quote"
                    if quote_price is not None
                    else "default"
                )
                console.print(f"gasLimit: using {'override' if gas_limit_override is not None else ('fee quote' if quote_limit is not None else 'default')} => {resolved_gas_limit}")
                console.print(f"maxFee: using {max_fee_source} => {resolved_max_fee}")
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
                        "algorithm_id": used_alg_id,
                        "domain": domain,
                        "prehash": prehash,
                        "chain_id_in_pq": cid,
                        "fork_id_in_pq": fork_id,
                        "genesis_len": len(chain_ctx.genesis_hash),
                        "network": chain_ctx.network,
                        "pubkey_len": len(pk),
                        "seckey_len": len(sk),
                        "message_len": len(body_bytes),
                        "message_prefix": body_bytes[:32].hex(),
                    }
                )

            _debug_tx_event("tx_build", {"trace_id": cli_trace_id, "chain_id": cid, "nonce": attempt_nonce, "from": from_addr, "to": to_addr, "value": value_base, "fee": resolved_max_fee, "body": body})
            # Sign
            pq = pq_sign_tx(body, sk, pk, used_alg_id, chain_ctx)

            _debug_tx_event("tx_sign", {"chain_id": cid, "nonce": attempt_nonce, "scheme_id": pq.alg_id})
            verify_result = pq_verify_tx(body, pq, pk, chain_ctx, from_addr=from_addr)
            if not verify_result.ok:
                console.print("[bold red]Local PQ verify failed before broadcast.[/bold red]")
                console.print(
                    {
                        "reason": verify_result.reason,
                        "sign_hash": verify_result.sign_hash_hex,
                        "preimage_prefix": verify_result.preimage_hex[:66] + "…",
                        "pub_fingerprint": verify_result.pub_fingerprint,
                        "scheme_id": pq.alg_id,
                        "sig_len": len(pq.sig),
                        "domain": chain_ctx.domain,
                        "prehash": chain_ctx.prehash,
                        "chain_id": chain_ctx.chain_id,
                        "network": chain_ctx.network,
                    }
                )
                raise RuntimeError("Local PQ verify failed before broadcast (signing mismatch).")

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
                send_method = "mempool.simulateAdmission" if dry_run else "tx.sendRawTransaction"
                _debug_tx_event("tx_submit", {"trace_id": cli_trace_id, "rpc": rpc, "method": send_method, "raw_len": len(raw), "rpc_params": [raw_hex]})
                send_result = _rpc(rpc, send_method, [raw_hex])
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
                    # Extract height information from error data if available
                    head_h = None
                    best_h = None
                    max_allowed_behind = None
                    if e.data is not None and isinstance(e.data, dict):
                        head_h = e.data.get("head_height")
                        best_h = e.data.get("best_header_height")
                        max_allowed_behind = e.data.get("max_allowed_behind")
                    
                    if head_h and best_h and head_h < best_h:
                        blocks_behind = best_h - head_h
                        if isinstance(max_allowed_behind, int) and max_allowed_behind >= 0:
                            console.print(
                                "\n[bold red]Node is too far behind network tip; transaction submission is unavailable.[/bold red]"
                            )
                            console.print(
                                f"[red]Node is behind by {blocks_behind} block{'s' if blocks_behind != 1 else ''} "
                                f"(head={head_h}, best={best_h}, allowed lag={max_allowed_behind}).[/red]"
                            )
                            console.print(
                                f"\n[yellow]Tip:[/yellow] Wait until node is within {max_allowed_behind} block"
                                f"{'s' if max_allowed_behind != 1 else ''} of height {best_h}, then retry."
                            )
                        else:
                            console.print(f"\n[bold red]Node is not at highest height; transaction submission is unavailable.[/bold red]")
                            console.print(f"[red]Node is behind by {blocks_behind} block{'s' if blocks_behind != 1 else ''} (head={head_h}, best={best_h}).[/red]")
                            console.print(f"\n[yellow]Tip:[/yellow] Wait for node to sync to height {best_h} before resubmitting.")
                    else:
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

            if dry_run:
                tx_in_mempool, mempool_status = False, None
            else:
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

            if verbose:
                console.print(
                    "[yellow]mempool status not observed; treating submission as accepted[/yellow]"
                )
            last_body = body
            last_nonce = attempt_nonce
            cache_key = (rpc, from_addr)
            _NONCE_CACHE[cache_key] = attempt_nonce
            break

    if tx_hash is None or last_body is None or last_nonce is None:
        raise typer.Exit(code=1)

    try:
        _record_pending_tx(
            from_addr=from_addr,
            to_addr=to_addr,
            tx_hash=tx_hash,
            value_base=value_base,
            fee_base=resolved_max_fee,
            chain_id=cid,
            nonce=last_nonce,
            status="mempool_accepted" if tx_in_mempool else "broadcast",
        )
    except Exception as exc:
        if verbose:
            console.print(f"[dim]Failed to record pending tx in wallet store: {exc}[/dim]")

    _maybe_force_sync(rpc, verbose=verbose)

    console.print("\n[bold green]=== Transaction Sent ===[/bold green]")
    console.print("Transaction Simulated" if dry_run else "Transaction Submitted")
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
    
    # Ensure optional peer-ack wait can cover all currently connected peers.
    effective_min_peers = min_peers
    if effective_min_peers is not None and effective_min_peers > 0:
        connected_peers = _connected_peer_count(rpc, verbose=verbose)
        if connected_peers > effective_min_peers:
            console.print(
                f"[yellow]Info:[/yellow] Raising --min-peers from {effective_min_peers} "
                f"to connected peer count ({connected_peers}) to enforce full mempool propagation."
            )
            effective_min_peers = connected_peers

    # Wait for PTL peer acknowledgments if requested
    if effective_min_peers is not None and effective_min_peers > 0:
        console.print(f"\n[bold]Waiting for {effective_min_peers} peer acknowledgments...[/bold]")
        start_wait = time.time()
        ack_count = 0
        
        while time.time() - start_wait < wait_timeout:
            try:
                # Use canonical ptl.replicationStatus method
                repl_result = _rpc(rpc, "ptl.replicationStatus", [{"txid": tx_hash}])
                if repl_result:
                    quorum = repl_result.get('quorum', {})
                    ack_count = quorum.get('observed_acks', 0)
                    local_status = repl_result.get('local_status', 'unknown')
                    
                    if ack_count >= effective_min_peers:
                        console.print(f"[green]✓ Received {ack_count} acknowledgments[/green]")
                        console.print(f"Status: {local_status}")
                        
                        peers = repl_result.get('peers', [])
                        if peers and verbose:
                            console.print("\nPeer receipts:")
                            for p in peers:
                                if p['status'] == 'ack':
                                    console.print(f"  [green]✓[/green] {p['peer_id']}")
                        break
                    
                    if verbose:
                        console.print(f"[dim]Acks: {ack_count}/{effective_min_peers} (waiting...)[/dim]")
                    
                    time.sleep(1)
                else:
                    if verbose:
                        console.print("[dim]PTL status not yet available[/dim]")
                    time.sleep(1)
                    
            except RpcError as e:
                if e.code == -32601:  # Method not found - PTL not enabled
                    console.print("[yellow]Warning: PTL not enabled on this node[/yellow]")
                    console.print("[yellow]To enable PTL replication:[/yellow]")
                    console.print("  1. Ensure node is running with PTL service enabled")
                    console.print("  2. Check ANIMICA_PTL_ENABLE environment variable")
                    console.print("  3. Or use legacy mempool-based propagation")
                    break
                if verbose:
                    console.print(f"[dim]Error checking replication: {e.message}[/dim]")
                time.sleep(1)
        
        if ack_count < effective_min_peers:
            elapsed = time.time() - start_wait
            console.print(f"[yellow]Warning: Only {ack_count}/{effective_min_peers} acks after {elapsed:.1f}s[/yellow]")
            console.print("[yellow]Transaction may still replicate. Use 'animica tx replicate' to check status.[/yellow]")

    
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


@app.command("pending")
def pending(
    limit: int = typer.Option(100, "--limit", help="Max number of transactions to show"),
    status_filter: Optional[str] = typer.Option(None, "--status", help="Filter by status (e.g., STORED, ATTESTED)"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="RPC URL (default: node)"),
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using remote bootstrap RPC",
    ),
):
    """
    List pending transactions from PTL.
    """
    rpc = _resolve_rpc_url(rpc_url)
    guard_bootstrap_rpc(rpc, allow_remote=allow_remote_rpc, method="tx.pending")
    
    params = {"limit": limit}
    if status_filter:
        params["status"] = status_filter
    
    try:
        result = _rpc(rpc, "tx.pending", [params])
    except RpcError as e:
        _format_rpc_error(e)
        raise typer.Exit(code=1) from e
    
    console.print("\n[bold]Pending Transactions[/bold]")
    txs = result.get("transactions", [])
    total = result.get("total", 0)
    
    if not txs:
        console.print("[yellow]No pending transactions[/yellow]")
        return
    
    console.print(f"Total: {total}\n")
    for tx in txs:
        console.print(f"  TxID: {tx.get('txid')}")
        console.print(f"  Status: {tx.get('status')}")
        console.print(f"  Acks: {tx.get('ack_count', 0)}")
        console.print(f"  Size: {tx.get('size')} bytes")
        console.print(f"  Fee: {tx.get('fee')}")
        console.print(f"  Received: {time.ctime(tx.get('received_at', 0))}")
        console.print()


@app.command("replicate")
def replicate(
    tx_hash: str = typer.Argument(..., help="Transaction hash (0x...)"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="RPC URL (default: node)"),
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using remote bootstrap RPC",
    ),
    json_output: bool = typer.Option(
        False,
        "--json",
        help="Output as JSON for scripting",
    ),
):
    """
    Show replication status with per-peer receipts.
    """
    rpc = _resolve_rpc_url(rpc_url)
    guard_bootstrap_rpc(rpc, allow_remote=allow_remote_rpc, method="ptl.replicationStatus")
    
    try:
        # Use canonical ptl.replicationStatus method
        result = _rpc(rpc, "ptl.replicationStatus", [{"txid": tx_hash}])
    except RpcError as e:
        if e.code == -32601:  # Method not found
            console.print("[yellow]PTL not enabled on this node[/yellow]")
            console.print("\n[yellow]To enable PTL replication:[/yellow]")
            console.print("  1. Ensure node is running with PTL service enabled")
            console.print("  2. Set ANIMICA_PTL_ENABLE=1 environment variable")
            console.print("  3. Check that the node has P2P connectivity")
            console.print("\n[dim]Legacy mempool-based propagation may still be active[/dim]")
            raise typer.Exit(code=1)
        _format_rpc_error(e)
        raise typer.Exit(code=1) from e
    
    if not result:
        console.print(f"[yellow]Transaction {tx_hash} not found in PTL[/yellow]")
        raise typer.Exit(code=1)
    
    if json_output:
        # Stable JSON output for scripting
        console.print(json.dumps(result, indent=2))
        return
    
    # Human-readable output
    console.print("\n[bold]Replication Status[/bold]")
    console.print(f"TxID: {result.get('tx_hash')}")
    console.print(f"Local Status: {result.get('local_status')}")
    
    quorum = result.get('quorum', {})
    acks = quorum.get('observed_acks', 0)
    required = quorum.get('required_acks', 0)
    quorum_met = quorum.get('quorum_met', False)
    
    quorum_status = "[green]✓[/green]" if quorum_met else "[red]✗[/red]"
    console.print(f"Quorum: {quorum_status} {acks}/{required} acknowledgments")
    
    if result.get('received_at'):
        console.print(f"Received: {time.ctime(result.get('received_at', 0))}")
    if result.get('updated_at'):
        console.print(f"Updated: {time.ctime(result.get('updated_at', 0))}")
    if result.get('expire_at'):
        console.print(f"Expires: {time.ctime(result.get('expire_at', 0))}")
    
    # Show mined info if available
    mined = result.get('mined')
    if mined:
        console.print(f"\n[bold green]Mined[/bold green]")
        console.print(f"  Block Height: {mined.get('block_height')}")
        if mined.get('finalized'):
            console.print(f"  Finalized at: {mined.get('finalized_height')}")
        else:
            console.print("  Not yet finalized")
    
    peers = result.get('peers', [])
    if peers:
        console.print(f"\n[bold]Peer Receipts ({len(peers)})[/bold]")
        for p in peers:
            status_map = {
                'ack': 'green',
                'seen': 'yellow',
                'missing': 'red',
                'failed': 'red',
            }
            status_color = status_map.get(p['status'], 'white')
            timestamp = p.get('first_seen_ts', 0)
            console.print(f"  [{status_color}]{p['status']}[/{status_color}] from {p['peer_id']} at {time.ctime(timestamp)}")
            if p.get('reason'):
                console.print(f"    Reason: {p['reason']}")
    else:
        console.print("\n[yellow]No peer receipts yet[/yellow]")
    
    # Show persistence info
    persistence = result.get('persistence', {})
    receipts_count = persistence.get('stored_receipts_count', 0)
    if receipts_count > 0:
        console.print(f"\n[dim]Persistence: {receipts_count} receipts stored ({persistence.get('store_backend', 'unknown')})[/dim]")


@app.command("troubleshoot")
def troubleshoot(
    tx_hash: str = typer.Argument(..., help="Transaction hash (0x...)"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="RPC URL (default: node)"),
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using remote bootstrap RPC",
    ),
):
    """
    Troubleshoot transaction replication issues.
    """
    rpc = _resolve_rpc_url(rpc_url)
    guard_bootstrap_rpc(rpc, allow_remote=allow_remote_rpc, method="ptl.replicationStatus")
    
    console.print(f"\n[bold]Troubleshooting Transaction {tx_hash}[/bold]\n")
    
    # Get transaction details
    try:
        tx_result = _rpc(rpc, "tx.get", [{"txid": tx_hash}])
    except RpcError as e:
        if e.code == -32601:  # Method not found
            console.print(f"[yellow]PTL not enabled on this node (tx.get not available)[/yellow]")
        else:
            console.print(f"[red]Failed to get transaction: {e.message}[/red]")
        tx_result = None
    
    if not tx_result:
        console.print("[yellow]Transaction not found in PTL[/yellow]")
        console.print("Possible causes:")
        console.print("  - Transaction was never submitted")
        console.print("  - Transaction expired (TTL exceeded)")
        console.print("  - Transaction was pruned after finalization")
        console.print("  - PTL service is not enabled on this node")
        raise typer.Exit(code=1)
    
    status = tx_result.get('status')
    console.print(f"Status: [bold]{status}[/bold]")
    
    # Check replication using canonical method
    try:
        repl_result = _rpc(rpc, "ptl.replicationStatus", [{"txid": tx_hash}])
    except RpcError as e:
        if e.code == -32601:
            console.print(f"[yellow]PTL replication not available (ptl.replicationStatus not found)[/yellow]")
            console.print("\n[yellow]To enable PTL replication:[/yellow]")
            console.print("  1. Ensure node is running with PTL service enabled")
            console.print("  2. Set ANIMICA_PTL_ENABLE=1 environment variable")
            console.print("  3. Check that the node has P2P connectivity")
        else:
            console.print(f"[red]Failed to get replication status: {e.message}[/red]")
        repl_result = None
    
    if repl_result:
        quorum = repl_result.get('quorum', {})
        ack_count = quorum.get('observed_acks', 0)
        min_acks = quorum.get('required_acks', 2)
        
        console.print(f"Acknowledgments: {ack_count}/{min_acks}")
        
        if ack_count < min_acks:
            console.print("\n[yellow]Insufficient peer acknowledgments[/yellow]")
            console.print("Recommendations:")
            console.print("  1. Check network connectivity: animica p2p peers")
            console.print("  2. Verify peer count is sufficient")
            console.print("  3. Wait for anti-entropy reconciliation (10s interval)")
            console.print("  4. Check debug.ptlPeers for peer state")
        
        peers = repl_result.get('peers', [])
        rejections = [p for p in peers if p['status'] in ('failed', 'reject')]
        
        if rejections:
            console.print(f"\n[red]Rejections ({len(rejections)})[/red]")
            for r in rejections:
                console.print(f"  Peer {r['peer_id']}: {r.get('reason', 'unknown')}")
            console.print("\nTransaction may be invalid. Check:")
            console.print("  - Nonce (should match next expected nonce)")
            console.print("  - Balance (should cover value + fee)")
            console.print("  - Validity window (should be within current height range)")
    
    # Check PTL stats
    try:
        stats_result = _rpc(rpc, "debug.ptlStats", [{}])
        console.print(f"\n[bold]PTL Stats[/bold]")
        console.print(f"Min peer acks required: {stats_result.get('min_peer_acks', 'N/A')}")
        console.print(f"TTL: {stats_result.get('ttl_seconds', 'N/A')}s")
        
        stats = stats_result.get('stats', {})
        if stats:
            console.print("\nTransactions by status:")
            for status_key, count in stats.items():
                console.print(f"  {status_key}: {count}")
    except RpcError:
        pass
    
    # Check peer state
    try:
        peers_result = _rpc(rpc, "debug.ptlPeers", [{}])
        peers = peers_result.get('peers', [])
        console.print(f"\n[bold]Connected Peers: {len(peers)}[/bold]")
        for p in peers[:5]:  # Show first 5 peers
            console.print(f"  {p.get('peer_node_id', 'unknown')}: "
                         f"{p.get('announced_count', 0)} announced, "
                         f"{p.get('wanted_count', 0)} wanted")
        if len(peers) > 5:
            console.print(f"  ... and {len(peers) - 5} more")
    except RpcError:
        pass
