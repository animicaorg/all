
from __future__ import annotations

import json
import os
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Standalone Click is an optional import: Typer vendors its own Click, so the
# `click` distribution is not guaranteed to be installed (e.g. the minimal
# Windows wallet bundle ships Typer but not standalone Click). The only use of
# `_click` below is a redundant fallback to `typer.get_current_context`, which
# already covers the same case, so degrade gracefully when it is absent.
try:
    import click as _click
except ModuleNotFoundError:  # pragma: no cover - depends on install profile
    _click = None
import typer

from animica.cli.rpc_guard import guard_bootstrap_rpc
from animica.config import load_network_config
from animica.cli.paths import ensure_file_dir, secure_file
from animica.coin import format_amount
from animica.cli.aicf_utils import safe_json_encode
from animica.wallet.serialization import (
    WalletParseError,
    canonical_json_dumps,
    export_canonical_store,
    load_store_canonical,
    merge_imported_wallets,
    parse_wallets_text,
)
from .timeouts import DEFAULT_RPC_TIMEOUT, RPC_TIMEOUT_ENV, resolve_timeout

try:
    from pq.py.address import address_from_pubkey, validate_address
    from pq.py.keygen import keygen_sig, DILITHIUM3_ID
    from pq.py.registry import default_signature_alg, name_of, normalize_alg_name, require_sig  # type: ignore
    HAVE_PQ = True
except Exception:
    HAVE_PQ = False
    DILITHIUM3_ID = 0x1001  # Fallback constant if pq.py not available

# Fallbacks when PQ package is not available
if not HAVE_PQ:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except Exception:
        Ed25519PrivateKey = None

    def default_signature_alg():
        class _Alg:
            alg_id = 0xFFFF
            name = "ed25519-fallback"
        return _Alg()

    def name_of(alg_id: int) -> str:  # pragma: no cover
        return "ed25519-fallback" if alg_id == 0xFFFF else f"0x{alg_id:04x}"


WALLET_FILE_ENV = "ANIMICA_WALLETS_FILE"
_RPC_ENV = "ANIMICA_RPC_URL"
_ALLOW_SECRET_ENV = "ANIMICA_ALLOW_SECRET"
log = logging.getLogger(__name__)

# Wallet availability accounting model:
# - balance_confirmed: authoritative confirmed chain-state balance from RPC
# - pending_outgoing: sum of reserve_amount for locally-tracked active outbound txs
# - available_balance: balance_confirmed - pending_outgoing
# reserve_amount is expected to be (value + fee_reserved) and must be counted once.
_ACTIVE_PENDING_STATUSES = {
    "reserved",
    "broadcast",
    "pending",
    "mempool_accepted",
    "in_block_pending_confirm",
}


def _debug_tx_balance_event(*, tx_hash: str | None, address: str | None, delta: int, reason: str, callsite: str) -> None:
    if os.getenv("ANIMICA_DEBUG_TX", "0") != "1":
        return
    log.info(
        "tx_balance_event",
        extra={
            "tx_hash": tx_hash,
            "address": address,
            "delta": int(delta),
            "reason": reason,
            "callsite": callsite,
        },
    )

BALANCE_METHODS = [
    "state.getBalance",
    "state_getBalance",
    "chain_getBalance",
    "eth_getBalance",
]

app = typer.Typer(
    help=(
        "Wallet helper for creating, listing, and inspecting Animica addresses. "
        "For testnet funds use `animica faucet request <address>`; the wallet CLI"
        " does not request funds."
    )
)


@app.command("request")
def wallet_request_alias() -> None:
    """Guide users to the faucet when they try `animica wallet request`."""

    typer.echo(
        "Wallet funds are requested via `animica faucet request <address>`; "
        "the wallet command does not contact the faucet.",
        err=True,
    )
    raise typer.Exit(code=1)


@dataclass
class WalletEntry:
    label: str
    address: str
    alg_id: int
    alg_name: str
    public_key_hex: str
    secret_key_hex: str
    created_at: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _get_default_wallet_path() -> Path:
    return Path.home() / ".animica" / "wallets.json"


def _wallet_file_path(wallet_file: Optional[Path]) -> Path:
    if wallet_file is not None:
        return Path(wallet_file)
    env_path = os.environ.get(WALLET_FILE_ENV)
    if env_path:
        return Path(env_path)
    return _get_default_wallet_path()


def _load_store(wallet_file: Path) -> Dict[str, Any]:
    """Load the wallet store, with automatic backup-driven recovery.

    If the canonical file is missing or unparsable AND at least one valid
    ``.bak.*`` snapshot exists in the same directory, we refuse to return
    an empty store — that path is how a single bad save destroys every
    key. Instead we promote the newest valid backup to the canonical
    location and load from that. The previous bad file is renamed to
    ``.corrupt.<UTC-ts>`` for forensics; we never silently delete user
    key material.

    Disable this rescue with ANIMICA_WALLET_DISABLE_BACKUP_RECOVERY=1
    if you genuinely want a fresh empty store (e.g. fresh node bootstrap
    on a host that happens to have stale backups around).
    """
    try:
        parsed = load_store_canonical(wallet_file)
    except WalletParseError as exc:
        if os.environ.get("ANIMICA_WALLET_DISABLE_BACKUP_RECOVERY", "").strip():
            raise RuntimeError(str(exc)) from exc
        rescued = _try_recover_from_backup(wallet_file, reason=str(exc))
        if rescued is not None:
            return rescued
        raise RuntimeError(str(exc)) from exc
    if parsed.failures:
        if not os.environ.get("ANIMICA_WALLET_DISABLE_BACKUP_RECOVERY", "").strip():
            rescued = _try_recover_from_backup(
                wallet_file, reason="; ".join(parsed.failures)
            )
            if rescued is not None:
                return rescued
        raise RuntimeError("Failed to parse wallet file:\n" + "\n".join(parsed.failures))
    # Heuristic: if the canonical file just got recreated empty (zero
    # wallets) but backups exist with non-empty wallet lists, this is
    # almost certainly the "volume swapped under us" scenario from the
    # CEX incident — promote the most recent non-empty backup so we
    # don't accept the wipe as authoritative.
    wallets = parsed.store.get("wallets") if isinstance(parsed.store, dict) else None
    if isinstance(wallets, list) and len(wallets) == 0:
        if not os.environ.get("ANIMICA_WALLET_DISABLE_BACKUP_RECOVERY", "").strip():
            rescued = _try_recover_from_backup(
                wallet_file,
                reason="canonical store is empty; checking backups",
                require_more_wallets_than=0,
            )
            if rescued is not None:
                return rescued
    return parsed.store


def _try_recover_from_backup(
    wallet_file: Path,
    *,
    reason: str,
    require_more_wallets_than: int | None = None,
) -> Optional[Dict[str, Any]]:
    """Promote the newest valid ``.bak.*`` snapshot if recovery is warranted.

    Returns the rescued store on success, or None if no usable backup was
    found. When ``require_more_wallets_than`` is set, only a backup with
    strictly more wallet entries than that count is considered — used by
    the "canonical is empty but backups have data" path so we don't
    overwrite a legitimately empty new store with a stale snapshot.
    """
    parent = wallet_file.parent
    try:
        backups = sorted(
            parent.glob(f"{wallet_file.name}.bak.*"),
            key=lambda p: p.name,
            reverse=True,
        )
    except OSError:
        return None
    log = logging.getLogger(__name__)
    for backup in backups:
        try:
            parsed = load_store_canonical(backup)
        except WalletParseError:
            continue
        if parsed.failures:
            continue
        backup_wallets = parsed.store.get("wallets") if isinstance(parsed.store, dict) else None
        n = len(backup_wallets) if isinstance(backup_wallets, list) else 0
        if require_more_wallets_than is not None and n <= require_more_wallets_than:
            continue
        log.warning(
            "wallet store rescue: promoting %s -> %s (reason: %s, wallets=%d)",
            backup.name, wallet_file.name, reason, n,
        )
        try:
            if wallet_file.exists():
                from datetime import datetime as _dt, timezone as _tz
                stamp = _dt.now(_tz.utc).strftime("%Y%m%dT%H%M%S%f")
                wallet_file.rename(
                    parent / f"{wallet_file.name}.corrupt.{stamp}"
                )
        except OSError as exc:
            log.warning("could not preserve corrupt file: %s", exc)
        try:
            import shutil
            shutil.copy2(backup, wallet_file)
            try:
                secure_file(wallet_file)
            except Exception:
                pass
        except Exception as exc:  # noqa: BLE001
            log.warning("failed to copy backup into place: %s", exc)
            return None
        return parsed.store
    return None


def _save_store(wallet_file: Path, store: Dict[str, Any]) -> None:
    """Persist the wallet store with atomic-rename + dated backups.

    The naive `write_text` path used previously could destroy the store
    in two ways:
      1. A partial write (process killed / disk full) left the file
         truncated, after which the next load failed and any subsequent
         save replaced what was left with the in-memory view — losing
         every wallet that hadn't been loaded into RAM.
      2. A volume-name change in docker-compose silently swapped the
         backing storage, and a fresh save into the now-empty file
         meant the previous wallets vanished with no recovery path.

    We now:
      - Serialize the new content to canonical JSON.
      - Copy the current file (if any) to wallets.json.bak.<UTC-ts>.
      - Trim backups to ANIMICA_WALLET_BACKUP_KEEP (default 20), keeping
        the newest by name (timestamps sort lexicographically).
      - Write to wallets.json.tmp.<pid>, fsync it, and rename over the
        target so concurrent readers always see either the old or the
        new file — never a half-written one.

    Operators can override the keep count with ANIMICA_WALLET_BACKUP_KEEP=N
    or disable backups entirely with ANIMICA_WALLET_BACKUP_KEEP=0.

    The atomic-rename semantics rely on the temp file being on the same
    filesystem as the target. Since both live in the wallet_file's
    parent directory this is always true.
    """
    ensure_file_dir(wallet_file, sensitive=True)
    serialized = canonical_json_dumps(export_canonical_store(store))

    parent = wallet_file.parent
    suffix = wallet_file.suffix or ".json"
    # Backup the previous file before overwrite — only if it has content.
    # Empty / missing files are skipped: there's nothing to lose, and a
    # backup of "" would just clutter the directory.
    try:
        existing_size = wallet_file.stat().st_size if wallet_file.exists() else 0
    except OSError:
        existing_size = 0
    if existing_size > 0:
        try:
            keep = int(os.environ.get("ANIMICA_WALLET_BACKUP_KEEP", "20"))
        except ValueError:
            keep = 20
        if keep > 0:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            backup_path = parent / f"{wallet_file.name}.bak.{stamp}"
            try:
                import shutil
                shutil.copy2(wallet_file, backup_path)
                try:
                    secure_file(backup_path)
                except Exception:
                    pass
            except Exception as exc:  # noqa: BLE001
                # Never block a save on backup failure — the canonical
                # path is what matters for not losing keys. Log instead.
                logging.getLogger(__name__).warning(
                    "wallet backup failed: %s (continuing save)", exc
                )

            # Trim old backups beyond the retention count.
            try:
                backups = sorted(
                    parent.glob(f"{wallet_file.name}.bak.*"),
                    key=lambda p: p.name,
                    reverse=True,
                )
                for stale in backups[keep:]:
                    try:
                        stale.unlink()
                    except OSError:
                        pass
            except Exception:
                pass

    # Atomic write: temp file in the same dir, fsync, then os.replace.
    tmp_path = parent / f"{wallet_file.name}.tmp.{os.getpid()}"
    try:
        with tmp_path.open("w", encoding="utf-8") as fh:
            fh.write(serialized)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        try:
            secure_file(tmp_path)
        except Exception:
            pass
        os.replace(tmp_path, wallet_file)
    except BaseException:
        # Best-effort cleanup so we don't leave a stray temp file behind
        # if write/replace was interrupted.
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    try:
        secure_file(wallet_file)
    except Exception:
        pass


def _entry_from_dict(entry: Dict[str, Any]) -> WalletEntry:
    alg_id = int(entry.get("alg_id", default_signature_alg().alg_id))
    try:
        alg_name = entry.get("alg_name") or name_of(alg_id)
    except Exception:
        alg_name = entry.get("alg_name") or f"0x{alg_id:04x}"

    return WalletEntry(
        label=entry.get("label") or "",
        address=entry["address"],
        alg_id=alg_id,
        alg_name=alg_name,
        public_key_hex=entry["public_key_hex"],
        secret_key_hex=entry.get("secret_key_hex") or entry.get("private_key_enc") or "",
        created_at=entry["created_at"],
    )


def _find_wallet(store: Dict[str, Any], *, identifier: str) -> WalletEntry:
    for entry in store.get("wallets", []):
        if (
            entry.get("address") == identifier
            or entry.get("label") == identifier
            or entry.get("public_key_hex") == identifier
        ):
            return _entry_from_dict(entry)
    typer.echo(f"Wallet not found: {identifier}", err=True)
    raise typer.Exit(code=1)


def _find_wallet_raw(store: Dict[str, Any], *, identifier: str) -> Dict[str, Any]:
    wallets = store.get("wallets", [])
    identifier_lower = identifier.lower()
    for entry in wallets:
        if (
            entry.get("address", "").lower() == identifier_lower
            or entry.get("label", "").lower() == identifier_lower
            or entry.get("public_key_hex", "").lower() == identifier_lower
        ):
            return entry
    typer.echo(f"Wallet not found: {identifier}", err=True)
    raise typer.Exit(code=1)


def _resolve_rpc_url(rpc_url: Optional[str]) -> Tuple[str, str]:
    if rpc_url and rpc_url.strip():
        return rpc_url.strip(), "cli"
    for key in ("OMNI_RPC_URL", "OMNI_SDK_RPC_URL", _RPC_ENV):
        env_url = os.environ.get(key)
        if env_url and env_url.strip():
            return env_url.strip(), f"env:{key}"
    return load_network_config().rpc_url, "network_config"


def _request_rpc(method: str, params: Optional[List[Any]], rpc_url: str) -> Any:
    try:
        from omni_sdk.rpc.http import RpcClient  # type: ignore

        timeout = resolve_timeout("RPC timeout", None, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)
        client = RpcClient(rpc_url, timeout=timeout)
        return client.request(method, params)
    except Exception:
        import httpx

        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}
        timeout = resolve_timeout("RPC timeout", None, env_var=RPC_TIMEOUT_ENV, default=DEFAULT_RPC_TIMEOUT)
        resp = httpx.post(rpc_url, json=payload, timeout=timeout)
        resp.raise_for_status()
        parsed = resp.json()
        if "error" in parsed:
            raise RuntimeError(parsed.get("error"))
        return parsed.get("result")


def _wallet_confirmations_required() -> int:
    try:
        return max(1, int(os.environ.get("ANIMICA_WALLET_CONFIRMATIONS_REQUIRED", "1")))
    except Exception:
        return 1


def _refresh_pending_txs(
    pending: list[dict[str, Any]],
    rpc_endpoint: str,
) -> Tuple[list[dict[str, Any]], bool]:
    def _status_token(value: Any) -> str:
        return str(value or "").strip().lower()

    def _status_int(value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                if text.startswith(("0x", "0X")):
                    return int(text, 16)
                return int(text)
            except Exception:
                return None
        return None

    def _normalize_status_payload(status_payload: dict[str, Any]) -> str:
        token = _status_token(status_payload.get("status"))
        if not token:
            token = _status_token(status_payload.get("state"))

        if token in {"pending", "pending_mempool", "mempool_accepted", "broadcast", "reserved"}:
            return "mempool_accepted"
        if token in {"in_block_pending_confirm", "included", "included_block"}:
            return "in_block_pending_confirm"
        if token in {"confirmed", "finalized", "final", "success", "succeeded", "applied", "mined"}:
            return "confirmed"
        if token in {"reorged_out", "reorged"}:
            return "reorged_out"
        if token in {"failed", "rejected", "dropped", "evicted", "not_found"}:
            return "dropped"

        confirmations = _status_int(status_payload.get("confirmations"))
        if confirmations is not None and confirmations > 0:
            return "confirmed"

        if bool(status_payload.get("finalized")):
            return "confirmed"

        included_height = _status_int(status_payload.get("included_height"))
        if included_height is None:
            included_height = _status_int(status_payload.get("includedHeight"))
        if included_height is None:
            included_height = _status_int(status_payload.get("blockNumber"))
        if included_height is not None and included_height >= 0:
            return "in_block_pending_confirm"

        return token or _status_token(status_payload.get("status"))

    changed = False
    now = datetime.now(timezone.utc).isoformat()

    for entry in pending:
        tx_hash = entry.get("tx_hash")
        if not tx_hash:
            continue
        try:
            status = _request_rpc("tx.getStatus", [tx_hash], rpc_endpoint)
        except Exception:
            continue
        if isinstance(status, str):
            status = {"status": status}
        if not isinstance(status, dict):
            continue
        new_state = _normalize_status_payload(status)
        confirmations = _status_int(status.get("confirmations"))
        included_height = _status_int(status.get("included_height"))
        if included_height is None:
            included_height = _status_int(status.get("includedHeight"))
        if included_height is None:
            included_height = _status_int(status.get("blockNumber"))
        entry["updated_at"] = now
        if confirmations is not None:
            entry["confirmations"] = confirmations
        if included_height is not None:
            entry["included_height"] = included_height
        if new_state == "confirmed":
            if entry.get("status") != "confirmed":
                entry["status"] = "confirmed"
                changed = True
            continue
        if new_state == "in_block_pending_confirm":
            if entry.get("status") != "in_block_pending_confirm":
                entry["status"] = "in_block_pending_confirm"
                changed = True
            continue
        if new_state in {"pending", "mempool_accepted", "broadcast", "reserved"}:
            if entry.get("status") != "mempool_accepted":
                entry["status"] = "mempool_accepted"
                changed = True
            continue
        if new_state == "reorged_out":
            if entry.get("status") != "reorged_out":
                entry["status"] = "reorged_out"
                changed = True
            continue
        if new_state in {"not_found", "dropped", "failed", "rejected", "evicted"}:
            if entry.get("status") != "dropped":
                entry["status"] = "dropped"
                drop_reason = _status_token(
                    status.get("reason") or status.get("error") or status.get("details") or new_state
                )
                entry["drop_reason"] = drop_reason or "not_found"
                changed = True

    return pending, changed


class BalanceQueryError(Exception):
    """Raised when balance cannot be fetched from the node."""


def _parse_balance(result: Any) -> int:
    if isinstance(result, str):
        try:
            if result.startswith("0x"):
                return int(result, 16)
            return int(result)
        except ValueError as exc:  # pragma: no cover - defensive
            raise BalanceQueryError(f"Invalid balance string: {result}") from exc
    if isinstance(result, (int, float)):
        return int(result)
    raise BalanceQueryError(f"Unexpected balance response type: {type(result)}")


def get_balance(address: str, rpc_url: str) -> int:
    """Fetch balance for an address using available RPC methods."""

    errors: List[str] = []
    for method in BALANCE_METHODS:
        try:
            result = _request_rpc(method, [address], rpc_url)
            if result is None:
                raise BalanceQueryError("Empty balance response")
            return _parse_balance(result)
        except Exception as exc:  # pragma: no cover - varied environments
            errors.append(f"{method}: {exc}")
            continue
    raise BalanceQueryError("; ".join(errors) or "Balance RPC failed")


def _is_dilithium3_alg(alg_name: str) -> bool:
    """Check if algorithm name refers to Dilithium3/ML-DSA-65."""
    name_lower = alg_name.lower().replace("_", "-").replace(" ", "")
    return name_lower in ("dilithium3",)


def _normalize_dilithium3_secret_key(secret: bytes, alg_name: str) -> bytes:
    """
    Normalize Dilithium3 secret key to canonical 4000-byte format.
    
    Ensures new wallets store canonical keys while maintaining backward
    compatibility with legacy 4032-byte keys from liboqs.
    
    Args:
        secret: Secret key bytes
        alg_name: Algorithm name (e.g., "dilithium3")
    
    Returns:
        Canonical secret key (4000 bytes for dilithium3, unchanged otherwise)
    """
    if not _is_dilithium3_alg(alg_name):
        return secret
    
    sk_len = len(secret)
    
    # Already canonical
    if sk_len == 4000:
        return secret
    
    # Legacy liboqs format - normalize to canonical
    if sk_len == 4032:
        return secret[:4000]
    
    # Unexpected length - return as-is and let signing code handle it
    return secret


def _resolve_signature_alg(requested: Optional[str]) -> Any:
    if not requested:
        return default_signature_alg()
    normalized = normalize_alg_name(requested)
    try:
        return require_sig(normalized)
    except Exception as exc:
        raise typer.BadParameter(f"Unknown signature algorithm: {requested}") from exc


def _generate_entry(
    label: str,
    *,
    allow_fallback: bool,
    alg_info: Any,
    allow_default_fallback: bool,
) -> WalletEntry:
    if allow_fallback:
        os.environ.setdefault("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
        os.environ.setdefault("ANIMICA_UNSAFE_PQ_FAKE", "1")

    resolved_alg_id = alg_info.alg_id
    resolved_alg_name = alg_info.name

    if HAVE_PQ:
        try:
            kp = keygen_sig(resolved_alg_id)

            # HARD SAFETY CHECKS: refuse fake PQ wallets.
            public = kp.public_key
            secret = kp.secret_key

            if public == secret:
                raise RuntimeError("Refusing wallet: PQ keygen produced sk==pk (fake/broken)")
            
            # Validate key sizes against expected algorithm metadata
            # Some algorithms like SPHINCS+ have equal-sized keys (pk=64, sk=64)
            expected_pk_size = alg_info.pubkey_size
            expected_sk_size = alg_info.seckey_size
            
            if len(public) != expected_pk_size or len(secret) != expected_sk_size:
                raise RuntimeError(
                    f"Refusing wallet: key sizes don't match algorithm spec. "
                    f"Got pk={len(public)} sk={len(secret)}, "
                    f"expected pk={expected_pk_size} sk={expected_sk_size} for {alg_info.name}"
                )
            
            # For algorithms where sk should be larger than pk, enforce that
            # (but allow equal sizes for algorithms like SPHINCS+ where this is normal)
            if expected_sk_size > expected_pk_size and len(secret) <= len(public):
                raise RuntimeError(
                    f"Refusing wallet: suspicious PQ sizes pk={len(public)} sk={len(secret)}"
                )

            address = kp.address
            resolved_alg_id = kp.alg_id
            resolved_alg_name = kp.alg_name
            
            # Normalize Dilithium3 keys to canonical format for storage
            secret = _normalize_dilithium3_secret_key(secret, resolved_alg_name)

        except NotImplementedError as e:
            # If default algorithm is not available (e.g., SPHINCS without liboqs),
            # try Dilithium3 which has pure-Python fallback support
            if allow_default_fallback and resolved_alg_id != DILITHIUM3_ID:
                try:
                    kp = keygen_sig(DILITHIUM3_ID)
                    
                    # Get Dilithium3 algorithm info for validation
                    dilithium3_info = require_sig(DILITHIUM3_ID)
                    
                    # HARD SAFETY CHECKS
                    public = kp.public_key
                    secret = kp.secret_key
                    if public == secret:
                        raise RuntimeError("Refusing wallet: PQ keygen produced sk==pk (fake/broken)")
                    
                    # Validate key sizes against expected algorithm metadata
                    expected_pk_size = dilithium3_info.pubkey_size
                    expected_sk_size = dilithium3_info.seckey_size
                    
                    if len(public) != expected_pk_size or len(secret) != expected_sk_size:
                        raise RuntimeError(
                            f"Refusing wallet: key sizes don't match algorithm spec. "
                            f"Got pk={len(public)} sk={len(secret)}, "
                            f"expected pk={expected_pk_size} sk={expected_sk_size} for {dilithium3_info.name}"
                        )
                    
                    # For algorithms where sk should be larger than pk, enforce that
                    if expected_sk_size > expected_pk_size and len(secret) <= len(public):
                        raise RuntimeError(
                            f"Refusing wallet: suspicious PQ sizes pk={len(public)} sk={len(secret)}"
                        )
                    
                    address = kp.address
                    resolved_alg_id = kp.alg_id
                    resolved_alg_name = kp.alg_name
                    secret = _normalize_dilithium3_secret_key(secret, resolved_alg_name)
                except Exception:
                    # Dilithium3 also failed, decide based on allow_fallback
                    if not allow_fallback:
                        raise e
                    # If allow_fallback, continue to the next except handler
                    raise
            elif not allow_fallback:
                raise
            os.environ.setdefault("ANIMICA_ALLOW_PQ_PURE_FALLBACK", "1")
            os.environ.setdefault("ANIMICA_UNSAFE_PQ_FAKE", "1")
            from pq.py.algs import pure_python_fallbacks as pq_fallbacks  # type: ignore

            secret, public = pq_fallbacks.fallback_sig_keypair(resolved_alg_name)
            address = address_from_pubkey(public, resolved_alg_id)

    else:
        if Ed25519PrivateKey is None:
            raise RuntimeError("PQ not available and cryptography fallback not installed")

        from cryptography.hazmat.primitives import serialization

        sk = Ed25519PrivateKey.generate()
        pk = sk.public_key()
        public = pk.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        secret = sk.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        address = "anim1" + public.hex()
        resolved_alg_name = alg_info.name

    return WalletEntry(
        label=label,
        address=address,
        alg_id=resolved_alg_id,
        alg_name=resolved_alg_name,
        public_key_hex=public.hex(),
        secret_key_hex=secret.hex(),
        created_at=datetime.now(timezone.utc).isoformat(),
    )


@app.callback()
def _configure(
    ctx: typer.Context,
    wallet_file: Optional[Path] = typer.Option(
        None,
        "--wallet-file",
        help="Override wallet store location (default: ~/.animica/wallets.json)",
        envvar=WALLET_FILE_ENV,
    ),
) -> None:
    if ctx.obj is None:
        ctx.obj = {}
    ctx.obj["wallet_file"] = wallet_file


def _current_wallet_file() -> Optional[Path]:
    try:
        ctx = typer.get_current_context(silent=True)
        if ctx and isinstance(getattr(ctx, "obj", None), dict):
            return ctx.obj.get("wallet_file")
    except Exception:
        pass
    if _click is not None:
        try:
            ctx = _click.get_current_context(silent=True)
            if ctx and isinstance(getattr(ctx, "obj", None), dict):
                return ctx.obj.get("wallet_file")
        except Exception:
            pass
    return None


@app.command("path")
def wallet_path(json_output: bool = typer.Option(False, "--json", help="Return path info as JSON")) -> None:
    """Show the wallet store path and how it was resolved.

    This helps users locate their ``wallets.json`` file when moving between
    machines or debugging custom locations.
    """

    ctx_wallet_file = _current_wallet_file()
    resolved = _wallet_file_path(ctx_wallet_file)

    if ctx_wallet_file:
        source = "cli"
    elif os.environ.get(WALLET_FILE_ENV):
        source = "env"
    else:
        source = "default"

    info = {
        "path": str(resolved),
        "exists": resolved.exists(),
        "source": source,
        "env_var": WALLET_FILE_ENV,
        "default_path": str(_get_default_wallet_path()),
    }

    if json_output:
        typer.echo(json.dumps(info, indent=2))
        return

    typer.echo(f"Wallet store path: {resolved}")
    source_hint = (
        "--wallet-file"
        if source == "cli"
        else f"${WALLET_FILE_ENV}"
        if source == "env"
        else "default ~/.animica/wallets.json"
    )
    typer.echo(f"Source: {source} ({source_hint})")
    typer.echo(f"Exists: {'yes' if info['exists'] else 'no (created on first write)'}")
    typer.echo(f"Default: {_get_default_wallet_path()}")
    typer.echo(f"Override: --wallet-file or set {WALLET_FILE_ENV}")


@app.command("create")
def create(
    label: str = typer.Option(..., "--label", help="Label for the new wallet"),
    alg: Optional[str] = typer.Option(
        None,
        "--alg",
        help=(
            "Signature algorithm. Default: ml_dsa_65 (real FIPS 204, recommended). "
            "Legacy stubs dilithium3/sphincs_shake_128s are accepted only with "
            "ANIMICA_ALLOW_LEGACY_STUB_KEYGEN=1 and should not be used for new wallets."
        ),
    ),
    allow_insecure_fallback: bool = typer.Option(
        False,
        "--allow-insecure-fallback",
        help="Use pure-Python PQ fallbacks when native libs are unavailable (dev/test only)",
    ),
) -> None:
    if not allow_insecure_fallback:
        from animica.cli.pq_utils import check_pq_signing_available, get_pq_missing_error_message

        ok, msg = check_pq_signing_available()
        if not ok:
            typer.echo(get_pq_missing_error_message(), err=True)
            if msg:
                typer.echo(f"\nAdditional info: {msg}", err=True)
            typer.echo("\nTo create a dev-only wallet, use --allow-insecure-fallback", err=True)
            raise typer.Exit(1)

    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)

    alg_info = _resolve_signature_alg(alg)
    entry = _generate_entry(
        label,
        allow_fallback=allow_insecure_fallback,
        alg_info=alg_info,
        allow_default_fallback=alg is None,
    )

    if HAVE_PQ:
        validate_address(entry.address, expect_hrp="anim")
    else:
        typer.echo("Warning: PQ not available; skipping address validation")

    if any(e.get("address") == entry.address for e in store.get("wallets", [])):
        typer.echo("Wallet already exists", err=True)
        raise typer.Exit(code=1)

    store.setdefault("wallets", []).append(entry.to_dict())
    _save_store(path, store)

    typer.echo("=== Wallet created ===")
    typer.echo(f"Label:   {entry.label}")
    typer.echo(f"Address: {entry.address}")
    typer.echo(f"Alg:     {entry.alg_name} (0x{entry.alg_id:04x})")
    typer.echo(f"Store:   {path}")


@app.command("list")
def list_wallets() -> None:  # noqa: A001
    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    wallets: List[Dict[str, Any]] = store.get("wallets", [])
    default_addr = store.get("default_address") or store.get("default")

    typer.echo("Idx Default Label             Address                              Alg")
    typer.echo("--- ------- ----------------  -----------------------------------  ----------------")
    for idx, entry in enumerate(wallets):
        marker = "*" if entry.get("address") == default_addr else " "
        label = (entry.get("label") or "").ljust(16)
        address = entry.get("address") or ""
        alg_name = entry.get("alg_name") or ""
        typer.echo(f"{idx:>3} {marker} {label}  {address:<35}  {alg_name}")


@app.command()
def show(
    identifier: Optional[str] = typer.Argument(None, help="Address (bech32), label, or public key hex"),
    address: Optional[str] = typer.Option(None, "--address", help="(Deprecated) use positional argument"),
    rpc_url: Optional[str] = typer.Option(None, "--rpc-url", help="Animica JSON-RPC endpoint", envvar=_RPC_ENV),
    allow_remote_rpc: bool = typer.Option(
        False,
        "--allow-remote-rpc",
        help="Allow using bootstrap RPC (requires ANIMICA_I_UNDERSTAND_REMOTE_RISK=1)",
    ),
    show_secret: bool = typer.Option(False, "--show-secret", help="Include secret key in output (WARNING: sensitive)"),
    i_know_what_im_doing: bool = typer.Option(
        False,
        "--i-know-what-im-doing",
        help="Acknowledge the risk before printing secret keys",
    ),
) -> None:
    lookup_id = identifier or address
    if not lookup_id:
        typer.echo("Error: Missing wallet identifier", err=True)
        raise typer.Exit(code=1)

    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    raw_entry = _find_wallet_raw(store, identifier=lookup_id)
    entry = _entry_from_dict(raw_entry)

    balance_confirmed: Optional[int] = None
    balance_source = "chain"
    head_info: Optional[Dict[str, Any]] = None
    queried_at: Optional[str] = None
    confirmations_required = _wallet_confirmations_required()
    pending_entries = raw_entry.get("pending_txs", [])
    if not isinstance(pending_entries, list):
        pending_entries = []
        raw_entry["pending_txs"] = pending_entries

    rpc_endpoint, rpc_source = _resolve_rpc_url(rpc_url)
    guard_bootstrap_rpc(rpc_endpoint, allow_remote=allow_remote_rpc, method="state.getBalance")

    # Get head info
    try:
        head_result = _request_rpc("chain.getHead", [], rpc_endpoint)
        if head_result and isinstance(head_result, dict):
            head_info = {
                "height": head_result.get("height"),
                "hash": head_result.get("hash"),
                "rpc_url": rpc_endpoint,
            }
        queried_at = datetime.now(timezone.utc).isoformat()
    except Exception as exc:
        typer.echo(f"Warning: Failed to fetch head info: {exc}", err=True)

    # Get balance directly from chain
    try:
        balance_confirmed = get_balance(entry.address, rpc_endpoint)
    except Exception as exc:
        typer.echo(f"Error: Failed to fetch balance from chain: {exc}", err=True)
        raise typer.Exit(code=1)

    pending_entries, pending_changed = _refresh_pending_txs(pending_entries, rpc_endpoint)
    if pending_changed:
        for idx, candidate in enumerate(store.get("wallets", [])):
            if candidate.get("address") == entry.address:
                store["wallets"][idx]["pending_txs"] = pending_entries
                break
        _save_store(path, store)

    output = entry.to_dict()

    # Secret handling
    if show_secret:
        env_allow = os.environ.get(_ALLOW_SECRET_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
        if not env_allow or not i_know_what_im_doing:
            typer.echo(
                "Refusing to display secret: set ANIMICA_ALLOW_SECRET=1 and pass --i-know-what-im-doing.",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo("WARNING: Displaying secret key. Keep this information secure!", err=True)
    else:
        output.pop("secret_key_hex", None)

    output["balance"] = balance_confirmed
    output["balance_confirmed"] = balance_confirmed
    output["balance_confirmed_formatted"] = (
        format_amount(balance_confirmed) if balance_confirmed is not None else None
    )
    output["balance_source"] = balance_source
    output["rpc_url"] = rpc_endpoint
    output["rpc_source"] = rpc_source
    output["confirmations_required"] = confirmations_required
    output["pending_txs"] = pending_entries
    reserved_outgoing = 0
    pending_outgoing_count = 0
    for pending in pending_entries:
        status = pending.get("status")
        if status in _ACTIVE_PENDING_STATUSES:
            reserve_amount = pending.get("reserve_amount")
            try:
                reserved_outgoing += int(reserve_amount or 0)
            except Exception:
                continue
            pending_outgoing_count += 1
            _debug_tx_balance_event(
                tx_hash=pending.get("tx_hash"),
                address=entry.address,
                delta=-int(reserve_amount or 0),
                reason="WALLET_VIEW_ADJUST",
                callsite="wallet.show",
            )
    output["pending_outgoing"] = reserved_outgoing
    output["pending_outgoing_count"] = pending_outgoing_count
    if balance_confirmed is not None:
        output["available_balance"] = max(0, balance_confirmed - reserved_outgoing)
        output["available_balance_formatted"] = format_amount(output["available_balance"])
    
    # Add head info if available
    if head_info is not None:
        output["head"] = head_info
    if queried_at is not None:
        output["queried_at"] = queried_at
    
    # Use safe_json_encode to handle BigInt values properly
    typer.echo(safe_json_encode(output))


@app.command()
def export(
    identifier: Optional[str] = typer.Argument(None, help="Address (bech32), label, or public key hex"),
    address: Optional[str] = typer.Option(None, "--address", help="(Deprecated) use positional argument"),
    out: Path = typer.Option(..., "--out", help="Destination JSON file"),
) -> None:
    # Export canonical wallets.json (v2) for full store round-tripping.
    lookup_id = identifier or address

    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)

    if lookup_id:
        _ = _find_wallet(store, identifier=lookup_id)

    payload = export_canonical_store(store)
    ensure_file_dir(out, sensitive=True)
    out.write_text(canonical_json_dumps(payload), encoding="utf-8")
    secure_file(out)
    typer.echo(f"Exported to {out}")


@app.command(name="import")
def import_(  # noqa: A001
    in_: Path = typer.Option(..., "--in", "--file", help="JSON file to import"),
    merge: bool = typer.Option(True, "--merge/--no-merge", help="Merge imported wallets with existing wallets"),
    replace: bool = typer.Option(False, "--replace", help="Replace existing wallets with imported set"),
    allow_partial: bool = typer.Option(False, "--allow-partial", help="Import valid wallets even if some entries fail"),
    label: Optional[str] = typer.Option(None, "--label", help="Legacy single-wallet import label override"),
    force: bool = typer.Option(False, "--force", help="Legacy alias for --replace when importing single wallet"),
    password: Optional[str] = typer.Option(None, "--password", help="Reserved for encrypted imports"),
) -> None:
    if password:
        typer.echo("Note: --password was provided; encrypted payload import is not required for current file.")

    if replace and merge:
        merge = False

    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    existing = _load_store(path)

    try:
        raw_text = in_.read_text(encoding="utf-8")
        imported = parse_wallets_text(raw_text, source=str(in_))
    except WalletParseError as exc:
        typer.echo(f"Import failed: {exc}", err=True)
        raise typer.Exit(code=1)

    if label and len(imported.store.get("wallets", [])) == 1:
        imported.store["wallets"][0]["label"] = label

    mode = "replace" if (replace or force) else "merge"
    if imported.failures and not allow_partial:
        typer.echo("Import rejected due to invalid wallets:", err=True)
        for item in imported.failures:
            typer.echo(f"  - {item}", err=True)
        raise typer.Exit(code=1)

    if not merge and mode != "replace":
        mode = "replace"

    next_store = merge_imported_wallets(existing, imported.store, mode=mode)
    _save_store(path, next_store)

    for warning in imported.warnings:
        typer.echo(f"Warning: {warning}", err=True)
    if imported.failures:
        typer.echo(f"Warning: skipped {len(imported.failures)} invalid wallet(s)", err=True)

    typer.echo(f"Imported {len(imported.store.get('wallets', []))} wallet(s) from {in_}")


@app.command(name="set-default")
def set_default(
    identifier: Optional[str] = typer.Argument(None, help="Address (bech32), label, or public key hex"),
    address: Optional[str] = typer.Option(None, "--address", help="(Deprecated) use positional argument"),
) -> None:
    lookup_id = identifier or address
    if not lookup_id:
        typer.echo("Error: Missing wallet identifier", err=True)
        raise typer.Exit(code=1)

    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    entry = _find_wallet(store, identifier=lookup_id)

    store["default_address"] = entry.address
    store["default"] = entry.label
    _save_store(path, store)
    typer.echo(f"Default wallet set to {entry.address}")


@app.command()
def env() -> None:  # noqa: A001
    ctx_wallet_file = _current_wallet_file()
    path = _wallet_file_path(ctx_wallet_file)
    store = _load_store(path)
    default_address = store.get("default_address")
    if not default_address:
        typer.echo("No default wallet set; use `animica wallet set-default ...`", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"export ANIMICA_DEFAULT_ADDRESS={default_address}")


@app.command(name="new")
def new_alias(label: str = typer.Option(..., "--label")) -> None:
    # Call the real command function directly, so pass concrete values for every
    # parameter — otherwise the unsupplied typer.Option defaults leak through as
    # OptionInfo objects (e.g. `alg` -> "Unknown signature algorithm: <OptionInfo>").
    create(label=label, alg=None, allow_insecure_fallback=True)


if __name__ == "__main__":  # pragma: no cover
    app()
