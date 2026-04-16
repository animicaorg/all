from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urljoin, urlparse

from animica.cli.paths import ensure_file_dir, secure_file
from animica.cli.wallet import (
    _ACTIVE_PENDING_STATUSES,
    _entry_from_dict,
    _generate_entry,
    _load_store,
    _refresh_pending_txs,
    _request_rpc,
    _resolve_rpc_url,
    _resolve_signature_alg,
    _save_store,
    get_balance,
)
from animica.cli import tx as tx_cli
from animica.wallet.serialization import (
    WalletParseError,
    load_store_canonical,
    merge_imported_wallets,
    parse_wallets_text,
)

try:
    import requests  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("qt wallet bridge requires requests") from exc

try:
    from pq.py.address import validate_address
except Exception:  # pragma: no cover
    validate_address = None

try:
    from pq.py.registry import list_signature_algs  # type: ignore
except Exception:  # pragma: no cover
    list_signature_algs = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _wallet_id(entry: Dict[str, Any]) -> str:
    return str(entry.get("address") or entry.get("label") or "")


def _as_wallet_summary(entry: Dict[str, Any], *, default_label: str | None) -> Dict[str, Any]:
    created_at = entry.get("created_at") or _utc_now()
    return {
        "wallet_id": _wallet_id(entry),
        "label": entry.get("label") or "",
        "address": entry.get("address") or "",
        "algorithm_id": int(entry.get("alg_id") or 0),
        "algorithm": entry.get("alg_name") or "",
        "created_at": created_at,
        "is_default": bool(default_label and entry.get("label") == default_label),
        "public_key_hex": entry.get("public_key_hex") or "",
    }


def _load_wallet_store(path: str | Path) -> Dict[str, Any]:
    return _load_store(Path(path))


def _save_wallet_store(path: str | Path, store: Dict[str, Any]) -> None:
    _save_store(Path(path), store)


def _resolve_rpc_endpoint(rpc_url: str | None) -> str:
    resolved = _resolve_rpc_url(rpc_url)
    if isinstance(resolved, tuple):
        return str(resolved[0])
    return str(resolved)


def _ensure_store(path: str | Path) -> Dict[str, Any]:
    wallet_path = Path(path)
    parsed = load_store_canonical(wallet_path)
    if parsed.failures:
        raise RuntimeError("Failed to parse wallet store:\n" + "\n".join(parsed.failures))
    store = parsed.store
    if not wallet_path.exists():
        ensure_file_dir(wallet_path, sensitive=True)
        _save_wallet_store(wallet_path, store)
        secure_file(wallet_path)
    return store


def _find_entry(store: Dict[str, Any], wallet_id: str) -> Dict[str, Any]:
    normalized = wallet_id.strip().lower()
    for entry in store.get("wallets", []):
        if not isinstance(entry, dict):
            continue
        if _wallet_id(entry).lower() == normalized or str(entry.get("label") or "").lower() == normalized:
            return entry
    raise KeyError(f"wallet not found: {wallet_id}")


def _normalize_alg_name_for_ui(name: str) -> str:
    return name.replace("-", "_")


def supported_algorithms() -> Dict[str, Any]:
    supported: List[Dict[str, Any]] = []
    if list_signature_algs is None:
        supported = [
            {"name": "dilithium3", "display_name": "Dilithium3", "alg_id": 0x1001},
            {"name": "sphincs_shake_128s", "display_name": "SPHINCS+ Shake 128s", "alg_id": 0x1002},
        ]
    else:
        for alg in list_signature_algs():
            supported.append(
                {
                    "name": _normalize_alg_name_for_ui(str(alg.name)),
                    "display_name": str(alg.name),
                    "alg_id": int(alg.alg_id),
                }
            )
    supported.sort(key=lambda item: item["name"])
    return {"algorithms": supported}


def init_store(wallet_file: str) -> Dict[str, Any]:
    store = _ensure_store(wallet_file)
    wallets = [_as_wallet_summary(entry, default_label=store.get("default")) for entry in store.get("wallets", [])]
    return {"wallet_file": str(Path(wallet_file)), "wallets": wallets, "default": store.get("default")}


def list_wallets(wallet_file: str) -> Dict[str, Any]:
    store = _ensure_store(wallet_file)
    wallets = [_as_wallet_summary(entry, default_label=store.get("default")) for entry in store.get("wallets", [])]
    return {"wallet_file": str(Path(wallet_file)), "wallets": wallets, "default": store.get("default")}


def create_wallet(wallet_file: str, label: str, algorithm: str | None = None) -> Dict[str, Any]:
    store = _ensure_store(wallet_file)
    normalized_label = (label or "").strip()
    if not normalized_label:
        raise ValueError("wallet label cannot be empty")
    for entry in store.get("wallets", []):
        if str(entry.get("label") or "").strip().lower() == normalized_label.lower():
            raise ValueError(f"wallet label already exists: {normalized_label}")
    alg_info = _resolve_signature_alg((algorithm or "").replace("_", "-") or None)
    new_entry = _generate_entry(
        normalized_label,
        allow_fallback=False,
        alg_info=alg_info,
        allow_default_fallback=algorithm is None,
    ).to_dict()
    store.setdefault("wallets", []).append(new_entry)
    if not store.get("default"):
        store["default"] = new_entry["label"]
    store["updated_at"] = _utc_now()
    _save_wallet_store(wallet_file, store)
    return {"wallet": _as_wallet_summary(new_entry, default_label=store.get("default"))}


def rename_wallet(wallet_file: str, wallet_id: str, new_label: str) -> Dict[str, Any]:
    store = _ensure_store(wallet_file)
    normalized_label = (new_label or "").strip()
    if not normalized_label:
        raise ValueError("wallet label cannot be empty")
    target = _find_entry(store, wallet_id)
    for entry in store.get("wallets", []):
        if entry is target:
            continue
        if str(entry.get("label") or "").strip().lower() == normalized_label.lower():
            raise ValueError(f"wallet label already exists: {normalized_label}")
    old_label = target.get("label")
    target["label"] = normalized_label
    if store.get("default") == old_label:
        store["default"] = normalized_label
    store["updated_at"] = _utc_now()
    _save_wallet_store(wallet_file, store)
    return {"wallet": _as_wallet_summary(target, default_label=store.get("default"))}


def remove_wallet(wallet_file: str, wallet_id: str) -> Dict[str, Any]:
    store = _ensure_store(wallet_file)
    wallets = [entry for entry in store.get("wallets", []) if isinstance(entry, dict)]
    before = len(wallets)
    removed_label = None
    filtered: List[Dict[str, Any]] = []
    normalized = wallet_id.strip().lower()
    for entry in wallets:
        if _wallet_id(entry).lower() == normalized or str(entry.get("label") or "").lower() == normalized:
            removed_label = entry.get("label")
            continue
        filtered.append(entry)
    if len(filtered) == before:
        raise KeyError(f"wallet not found: {wallet_id}")
    store["wallets"] = filtered
    if store.get("default") == removed_label:
        store["default"] = filtered[0]["label"] if filtered else None
    store["updated_at"] = _utc_now()
    _save_wallet_store(wallet_file, store)
    return {"removed_wallet_id": wallet_id, "default": store.get("default")}


def set_default(wallet_file: str, wallet_id: str) -> Dict[str, Any]:
    store = _ensure_store(wallet_file)
    target = _find_entry(store, wallet_id)
    store["default"] = target.get("label")
    store["updated_at"] = _utc_now()
    _save_wallet_store(wallet_file, store)
    return {"wallet": _as_wallet_summary(target, default_label=store.get("default"))}


def export_public(wallet_file: str, wallet_id: str) -> Dict[str, Any]:
    store = _ensure_store(wallet_file)
    target = _find_entry(store, wallet_id)
    return {
        "wallet": {
            "wallet_id": _wallet_id(target),
            "label": target.get("label") or "",
            "address": target.get("address") or "",
            "alg_id": int(target.get("alg_id") or 0),
            "alg_name": target.get("alg_name") or "",
            "public_key_hex": target.get("public_key_hex") or "",
            "created_at": target.get("created_at") or _utc_now(),
        }
    }


def export_secret(wallet_file: str, wallet_id: str, destination: str) -> Dict[str, Any]:
    store = _ensure_store(wallet_file)
    target = dict(_find_entry(store, wallet_id))
    out = {
        "format": "animica.wallets",
        "version": 2,
        "created_at": _utc_now(),
        "updated_at": _utc_now(),
        "default": target.get("label"),
        "wallets": [target],
    }
    out_path = Path(destination)
    ensure_file_dir(out_path, sensitive=True)
    out_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    secure_file(out_path)
    return {"destination": str(out_path)}


def import_wallets(wallet_file: str, source_file: str, mode: str = "merge") -> Dict[str, Any]:
    wallet_path = Path(wallet_file)
    source_path = Path(source_file)
    if not source_path.exists():
        raise FileNotFoundError(f"wallet import source does not exist: {source_file}")
    existing = _ensure_store(wallet_path)
    imported = parse_wallets_text(source_path.read_text(encoding="utf-8"), source=str(source_path))
    if imported.failures:
        raise WalletParseError("\n".join(imported.failures))
    merged = merge_imported_wallets(existing, imported.store, mode=mode)
    _save_wallet_store(wallet_path, merged)
    return {
        "wallets": [_as_wallet_summary(entry, default_label=merged.get("default")) for entry in merged.get("wallets", [])],
        "warnings": imported.warnings,
        "default": merged.get("default"),
    }


def validate_wallet_address(address: str) -> Dict[str, Any]:
    value = (address or "").strip()
    if not value:
        return {"valid": False, "message": "address is empty"}
    if validate_address is None:
        return {"valid": value.startswith("anim1"), "message": "" if value.startswith("anim1") else "address must start with anim1"}
    try:
        validate_address(value, expect_hrp="anim")
        return {"valid": True, "message": ""}
    except Exception as exc:
        return {"valid": False, "message": str(exc)}


def wallet_overview(wallet_file: str, rpc_url: str | None = None) -> Dict[str, Any]:
    store = _ensure_store(wallet_file)
    rpc = _resolve_rpc_endpoint(rpc_url)
    results: List[Dict[str, Any]] = []
    changed = False
    head_info: Optional[Dict[str, Any]] = None
    sync_status: Any = None
    try:
        head_result = _request_rpc("chain.getHead", [], rpc)
        if isinstance(head_result, dict):
            head_info = {
                "height": head_result.get("height"),
                "hash": head_result.get("hash"),
            }
    except Exception:
        head_info = None
    try:
        sync_status = _request_rpc("sync.getStatus", [], rpc)
    except Exception:
        sync_status = None
    for idx, entry in enumerate(store.get("wallets", [])):
        if not isinstance(entry, dict):
            continue
        address = str(entry.get("address") or "")
        balance_confirmed = get_balance(address, rpc)
        pending_entries = entry.get("pending_txs", [])
        if not isinstance(pending_entries, list):
            pending_entries = []
        refreshed, pending_changed = _refresh_pending_txs(pending_entries, rpc)
        if pending_changed:
            store["wallets"][idx]["pending_txs"] = refreshed
            changed = True
        pending_outgoing = 0
        for pending in refreshed:
            if pending.get("status") in _ACTIVE_PENDING_STATUSES:
                try:
                    pending_outgoing += int(pending.get("reserve_amount") or 0)
                except Exception:
                    continue
        available = max(0, balance_confirmed - pending_outgoing)
        summary = _as_wallet_summary(entry, default_label=store.get("default"))
        summary.update(
            {
                "balance_confirmed": balance_confirmed,
                "balance_available": available,
                "balance_pending": pending_outgoing,
                "pending_txs": refreshed,
            }
        )
        results.append(summary)
    if changed:
        store["updated_at"] = _utc_now()
        _save_wallet_store(wallet_file, store)
    return {
        "wallets": results,
        "head": head_info,
        "sync": sync_status,
        "queried_at": _utc_now(),
    }


def _wallet_entry_by_address(store: Dict[str, Any], from_address: str) -> Dict[str, Any]:
    for entry in store.get("wallets", []):
        if str(entry.get("address") or "") == from_address:
            return entry
    raise KeyError(f"wallet not found for address: {from_address}")


def _coerce_int(value: Any) -> int | None:
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
        if text.startswith(("0x", "0X")):
            return int(text, 16)
        return int(text)
    return None


def _parse_amount_to_base_units(amount: str | int | float | Decimal) -> int:
    if isinstance(amount, int):
        return amount
    try:
        value = Decimal(str(amount).strip())
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid ANM amount: {amount}") from exc
    if value <= 0:
        raise ValueError("amount must be greater than zero")
    base = value * Decimal(tx_cli.ANM_BASE_UNITS)
    if base != base.to_integral_value():
        raise ValueError("amount has more than 9 decimal places")
    return int(base)


def _record_pending_tx(wallet_file: str, *, from_addr: str, to_addr: str, tx_hash: str, value_base: int, fee_base: int, chain_id: int, nonce: int, status: str) -> None:
    store = _ensure_store(wallet_file)
    target = _wallet_entry_by_address(store, from_addr)
    pending = target.setdefault("pending_txs", [])
    if not isinstance(pending, list):
        pending = []
        target["pending_txs"] = pending
    now = _utc_now()
    payload = {
        "tx_hash": tx_hash,
        "from": from_addr,
        "to": to_addr,
        "value": int(value_base),
        "fee_reserved": int(fee_base),
        "reserve_amount": int(value_base) + int(fee_base),
        "nonce": int(nonce),
        "chain_id": int(chain_id),
        "status": status,
        "updated_at": now,
    }
    for existing in pending:
        if existing.get("tx_hash") == tx_hash:
            existing.update(payload)
            break
    else:
        payload["created_at"] = now
        pending.append(payload)
    store["updated_at"] = now
    _save_wallet_store(wallet_file, store)


def _normalize_hash(tx_hash: str) -> str:
    return tx_hash if tx_hash.startswith("0x") else f"0x{tx_hash}"


def _extract_send_hash(result: Any) -> str:
    if isinstance(result, str):
        return _normalize_hash(result)
    if isinstance(result, dict):
        for key in ("tx_hash", "hash", "txHash", "transactionHash"):
            value = result.get(key)
            if isinstance(value, str):
                return _normalize_hash(value)
    raise ValueError(f"unexpected tx.sendRawTransaction result: {result!r}")


def _format_rpc_submit_error(exc: tx_cli.RpcError) -> str:
    """Return a concise, actionable error for tx.sendRawTransaction failures."""
    fallback = f"rpc error {exc.code}: {exc.message}"
    data = exc.data if isinstance(exc.data, dict) else {}
    mempool_error = data.get("mempoolError") if isinstance(data, dict) else None
    if not isinstance(mempool_error, dict):
        reason = str(data.get("reason") or "").strip()
        return f"transaction rejected by node: {reason}" if reason else fallback

    reason = str(mempool_error.get("reason_code") or mempool_error.get("reason") or "admission_failed")
    message = str(mempool_error.get("message") or exc.message or "").strip()
    context = mempool_error.get("context") if isinstance(mempool_error.get("context"), dict) else {}
    hint = str(mempool_error.get("hint") or "").strip()

    pieces: list[str] = [f"transaction rejected by node: {reason}"]
    if message and message.lower() not in {"rpc error", reason.lower()}:
        pieces.append(message)
    if hint:
        pieces.append(f"hint={hint}")

    expected = context.get("expected_nonce")
    got = context.get("got_nonce")
    if expected is not None and got is not None:
        pieces.append(f"expected_nonce={expected} got_nonce={got}")
    return " | ".join(pieces)


def _env_truthy(name: str, *, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    token = raw.strip().lower()
    if token in {"1", "true", "yes", "on"}:
        return True
    if token in {"0", "false", "no", "off"}:
        return False
    return default


def _is_local_rpc_endpoint(rpc_endpoint: str) -> bool:
    try:
        parsed = urlparse(rpc_endpoint)
    except Exception:
        return False
    host = (parsed.hostname or "").strip().lower()
    return host in {"127.0.0.1", "localhost", "::1"}


def _try_auto_mine_local_pending_tx(rpc_endpoint: str, payout_address: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {"attempted": False, "success": False, "error": None}
    if not _env_truthy("ANIMICA_QT_AUTO_MINE_LOCAL_TX", default=True):
        return result
    if not _is_local_rpc_endpoint(rpc_endpoint):
        return result

    result["attempted"] = True
    mine_payload = {
        "count": 1,
        "address": payout_address,
        "include_mempool": True,
        "allow_offline_mining": True,
    }
    try:
        tx_cli._rpc(rpc_endpoint, "miner.mine", [mine_payload])
        result["success"] = True
    except tx_cli.RpcError as exc:
        result["error"] = f"rpc {exc.code}: {exc.message}"
    except Exception as exc:
        result["error"] = str(exc)
    return result


def _pending_record_status(
    *,
    rpc_endpoint: str,
    tx_hash: str,
    in_mempool: bool,
) -> str:
    if in_mempool:
        return "mempool_accepted"
    try:
        status = tx_cli._rpc(rpc_endpoint, "tx.getStatus", [tx_hash])
    except Exception:
        return "broadcast"

    if not isinstance(status, dict):
        return "broadcast"
    token = str(status.get("state") or status.get("status") or "").strip().lower()
    if token in {"included_block", "confirmed", "finalized", "mined", "success", "succeeded", "applied"}:
        return "confirmed"
    if token in {"in_block_pending_confirm", "included"}:
        return "in_block_pending_confirm"
    if token in {"pending_mempool", "pending", "mempool_accepted"}:
        return "mempool_accepted"
    return "broadcast"


def _submit_signed_tx(
    *,
    wallet_file: str,
    rpc_url: str | None,
    from_address: str,
    to_address: str,
    value_base: int,
    gas_limit: int,
    max_fee: int | None,
    chain_id: int | None,
    nonce: int | None,
    valid_after: int | None,
    valid_until: int | None,
    ttl_blocks: int | None,
    data_bytes: bytes,
) -> Dict[str, Any]:
    store = _ensure_store(wallet_file)
    sender_entry = _wallet_entry_by_address(store, from_address)
    rpc = _resolve_rpc_endpoint(rpc_url)
    chain_resolution = tx_cli._get_chain_identity(rpc, chain_id_override=chain_id)
    resolved_chain_id = int(chain_id) if chain_id is not None else int(chain_resolution.identity.get("chainId"))
    chain_ctx = tx_cli._chain_context_from_identity(
        chain_resolution.identity,
        chain_id=resolved_chain_id,
        domain=tx_cli.DEFAULT_DOMAIN,
        prehash=tx_cli.DEFAULT_PREHASH,
    )
    overview = wallet_overview(wallet_file, rpc)
    available = 0
    for item in overview.get("wallets", []):
        if item.get("address") == from_address:
            available = int(item.get("balance_available") or 0)
            break
    quote_limit, quote_price = tx_cli._estimate_fee_quote(rpc)
    resolved_gas_limit = int(gas_limit or quote_limit or 21000)
    resolved_max_fee = int(max_fee or quote_price or tx_cli._get_default_max_fee(rpc))
    fee_reserve = int(resolved_gas_limit) * int(resolved_max_fee)
    total_required = int(value_base) + fee_reserve
    if available < total_required:
        raise RuntimeError(
            "insufficient available balance: "
            f"required={total_required} available={available} "
            f"(value={int(value_base)} gas_limit={resolved_gas_limit} max_fee={resolved_max_fee})"
        )
    if nonce is None:
        resolved_nonce = tx_cli._next_nonce(rpc, from_address, refresh=False, verbose=False, nonce_source="pending")
    else:
        resolved_nonce = int(nonce)
    resolved_valid_after, resolved_valid_until = tx_cli._resolve_validity_window(
        rpc,
        valid_from=valid_after,
        valid_until=valid_until,
        ttl_blocks=ttl_blocks,
        head_height_hint=None,
        verbose=False,
    )
    salt = os.urandom(16)
    body = tx_cli._build_tx_body(
        chain_id=resolved_chain_id,
        from_addr=from_address,
        to_addr=to_address,
        nonce=resolved_nonce,
        value_base_units=int(value_base),
        gas_limit=resolved_gas_limit,
        max_fee=resolved_max_fee,
        data=data_bytes,
        valid_after=resolved_valid_after,
        valid_until=resolved_valid_until,
        salt=salt,
    )
    pk = tx_cli._hex_to_bytes(str(sender_entry.get("public_key_hex") or ""))
    sk = tx_cli._hex_to_bytes(str(sender_entry.get("secret_key_hex") or ""))
    used_alg_id = int(sender_entry.get("alg_id") or 0x1001)
    pq = tx_cli.pq_sign_tx(body, sk, pk, used_alg_id, chain_ctx)
    verify = tx_cli.pq_verify_tx(body, pq, pk, chain_ctx, from_addr=from_address)
    if not verify.ok:
        raise RuntimeError(f"local signature verification failed: {verify.reason}")
    raw = tx_cli._build_raw_tx(
        body=body,
        alg_id=pq.alg_id,
        pk=pk,
        sig=pq.sig,
        domain=tx_cli.DEFAULT_DOMAIN,
        prehash=tx_cli.DEFAULT_PREHASH,
        chain_id=resolved_chain_id,
    )
    raw_hex = "0x" + raw.hex()
    try:
        send_result = tx_cli._rpc(rpc, "tx.sendRawTransaction", [raw_hex])
    except tx_cli.RpcError as exc:
        raise RuntimeError(_format_rpc_submit_error(exc)) from exc
    tx_hash = _extract_send_hash(send_result)
    in_mempool, mempool_status = tx_cli._get_mempool_status(rpc, tx_hash, verbose=False)
    record_status = _pending_record_status(rpc_endpoint=rpc, tx_hash=tx_hash, in_mempool=in_mempool)
    auto_mine = {
        "attempted": False,
        "success": False,
        "error": None,
    }
    if record_status in {"mempool_accepted", "broadcast"}:
        auto_mine = _try_auto_mine_local_pending_tx(rpc, from_address)
        if auto_mine.get("attempted"):
            in_mempool, mempool_status = tx_cli._get_mempool_status(rpc, tx_hash, verbose=False)
            record_status = _pending_record_status(rpc_endpoint=rpc, tx_hash=tx_hash, in_mempool=in_mempool)
    _record_pending_tx(
        wallet_file,
        from_addr=from_address,
        to_addr=to_address,
        tx_hash=tx_hash,
        value_base=value_base,
        fee_base=fee_reserve,
        chain_id=resolved_chain_id,
        nonce=resolved_nonce,
        status=record_status,
    )
    return {
        "tx_hash": tx_hash,
        "nonce": resolved_nonce,
        "chain_id": resolved_chain_id,
        "gas_limit": resolved_gas_limit,
        "max_fee": resolved_max_fee,
        "fee_reserve": fee_reserve,
        "mempool_admitted": in_mempool,
        "mempool_status": mempool_status,
        "wallet_record_status": record_status,
        "auto_mine_attempted": bool(auto_mine.get("attempted")),
        "auto_mine_success": bool(auto_mine.get("success")),
        "auto_mine_error": auto_mine.get("error"),
        "valid_after": resolved_valid_after,
        "valid_until": resolved_valid_until,
        "raw_transaction": raw_hex,
    }


def send_transaction(
    wallet_file: str,
    rpc_url: str | None,
    from_address: str,
    to_address: str,
    amount: str | int | float,
    *,
    gas_limit: int | None = None,
    max_fee: int | None = None,
    chain_id: int | None = None,
    nonce: int | None = None,
    valid_after: int | None = None,
    valid_until: int | None = None,
    ttl_blocks: int | None = None,
    data_hex: str | None = None,
) -> Dict[str, Any]:
    address_check = validate_wallet_address(to_address)
    if not address_check["valid"]:
        raise ValueError(address_check["message"] or "invalid recipient address")
    value_base = _parse_amount_to_base_units(amount)
    payload = (data_hex or "").strip()
    if payload.startswith("0x"):
        payload = payload[2:]
    data_bytes = bytes.fromhex(payload) if payload else b""
    return _submit_signed_tx(
        wallet_file=wallet_file,
        rpc_url=rpc_url,
        from_address=from_address,
        to_address=to_address,
        value_base=value_base,
        gas_limit=int(gas_limit or 21000),
        max_fee=max_fee,
        chain_id=chain_id,
        nonce=nonce,
        valid_after=valid_after,
        valid_until=valid_until,
        ttl_blocks=ttl_blocks,
        data_bytes=data_bytes,
    )


def transaction_status(rpc_url: str | None, tx_hash: str) -> Dict[str, Any]:
    rpc = _resolve_rpc_endpoint(rpc_url)
    normalized = _normalize_hash(tx_hash)
    try:
        result = tx_cli._rpc(rpc, "tx.getStatus", [normalized])
    except tx_cli.RpcError as exc:
        if exc.code == -32601:
            result = tx_cli._rpc(rpc, "tx.getTransactionStatus", [normalized])
        else:
            raise
    return {"tx_hash": normalized, "status": result}


def transaction_details(rpc_url: str | None, explorer_url: str | None, tx_hash: str) -> Dict[str, Any]:
    normalized = _normalize_hash(tx_hash)
    if explorer_url:
        response = requests.get(urljoin(explorer_url.rstrip("/") + "/", f"tx/{normalized}"), timeout=15)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
    rpc = _resolve_rpc_endpoint(rpc_url)
    tx = _request_rpc("tx.getTransactionByHash", [normalized], rpc)
    receipt = None
    try:
        receipt = _request_rpc("tx.getTransactionReceipt", [normalized], rpc)
    except Exception:
        receipt = None
    return {"hash": normalized, "transaction": tx, "receipt": receipt}


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def _classify_direction(item: Dict[str, Any], own_addresses: set[str]) -> str:
    from_addr = str(item.get("from") or "")
    to_addr = str(item.get("to") or "")
    if from_addr in own_addresses and to_addr in own_addresses:
        return "self"
    if from_addr in own_addresses:
        return "outgoing"
    if to_addr in own_addresses:
        return "incoming"
    return "external"


def _history_from_explorer(explorer_url: str, addresses: Iterable[str], limit: int = 100) -> List[Dict[str, Any]]:
    results: Dict[str, Dict[str, Any]] = {}
    for address in addresses:
        response = requests.get(
            urljoin(explorer_url.rstrip("/") + "/", "txs"),
            params={"address": address, "role": "any", "limit": limit},
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            tx_hash = str(item.get("hash") or item.get("tx_hash") or "")
            if not tx_hash:
                continue
            results[tx_hash] = item
    return list(results.values())


def fetch_history(
    wallet_file: str,
    rpc_url: str | None,
    explorer_url: str | None = None,
    *,
    wallet_id: str | None = None,
    direction: str | None = None,
    status: str | None = None,
    search: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    limit: int = 200,
) -> Dict[str, Any]:
    store = _ensure_store(wallet_file)
    addresses = [str(entry.get("address") or "") for entry in store.get("wallets", []) if isinstance(entry, dict)]
    if wallet_id:
        target = _find_entry(store, wallet_id)
        addresses = [str(target.get("address") or "")]
    own_addresses = set(addresses)
    records: Dict[str, Dict[str, Any]] = {}
    source_status = {"explorer": "unavailable", "pending": "ok"}

    for entry in store.get("wallets", []):
        if not isinstance(entry, dict):
            continue
        if entry.get("address") not in own_addresses:
            continue
        for pending in entry.get("pending_txs", []):
            if not isinstance(pending, dict):
                continue
            tx_hash = str(pending.get("tx_hash") or "")
            if not tx_hash:
                continue
            records[tx_hash] = {
                "hash": _normalize_hash(tx_hash),
                "time": pending.get("updated_at") or pending.get("created_at"),
                "from": pending.get("from"),
                "to": pending.get("to"),
                "amount": pending.get("value"),
                "fee": pending.get("fee_reserved"),
                "nonce": pending.get("nonce"),
                "chain_id": pending.get("chain_id"),
                "status": pending.get("status"),
                "block_height": pending.get("included_height"),
                "direction": "outgoing",
                "counterparty": pending.get("to"),
                "source": "wallet_store",
            }

    if explorer_url:
        try:
            for item in _history_from_explorer(explorer_url, own_addresses, limit=limit):
                tx_hash = str(item.get("hash") or item.get("tx_hash") or "")
                if not tx_hash:
                    continue
                record = {
                    "hash": _normalize_hash(tx_hash),
                    "time": item.get("time") or item.get("timestamp") or item.get("updated_at"),
                    "from": item.get("from"),
                    "to": item.get("to"),
                    "amount": item.get("value") or item.get("amount"),
                    "fee": item.get("fee") or item.get("maxFee") or item.get("gasUsed"),
                    "nonce": item.get("nonce"),
                    "chain_id": item.get("chainId") or item.get("chain_id"),
                    "status": item.get("status"),
                    "block_height": item.get("block", {}).get("height") if isinstance(item.get("block"), dict) else item.get("block_height"),
                    "direction": _classify_direction(item, own_addresses),
                    "counterparty": item.get("to") if str(item.get("from") or "") in own_addresses else item.get("from"),
                    "source": "explorer",
                }
                existing = records.get(tx_hash)
                if existing:
                    existing.update({k: v for k, v in record.items() if v not in (None, "", [])})
                else:
                    records[tx_hash] = record
            source_status["explorer"] = "ok"
        except Exception as exc:
            source_status["explorer"] = f"error: {exc}"

    text_filter = (search or "").strip().lower()
    start_dt = _parse_iso(start_time)
    end_dt = _parse_iso(end_time)
    filtered: List[Dict[str, Any]] = []
    for record in records.values():
        record_direction = str(record.get("direction") or "")
        record_status = str(record.get("status") or "")
        if direction and direction != "all" and record_direction != direction:
            continue
        if status and status != "all" and record_status.lower() != status.lower():
            continue
        haystack = " ".join(
            str(record.get(field) or "")
            for field in ("hash", "from", "to", "counterparty", "status")
        ).lower()
        if text_filter and text_filter not in haystack:
            continue
        record_time = _parse_iso(str(record.get("time") or ""))
        if start_dt and record_time and record_time < start_dt:
            continue
        if end_dt and record_time and record_time > end_dt:
            continue
        filtered.append(record)
    filtered.sort(key=lambda item: str(item.get("time") or ""), reverse=True)
    return {"items": filtered[:limit], "sources": source_status}


def _rpc_client(rpc_url: str):
    class _Client:
        def __init__(self, url: str) -> None:
            self._url = url

        def call(self, method: str, params: Optional[list | dict] = None) -> Any:
            return tx_cli._rpc(self._url, method, params if isinstance(params, list) else [params] if params else [])

    return _Client(rpc_url)


def contract_read(
    rpc_url: str | None,
    chain_id: int,
    contract_address: str,
    abi: Any,
    method: str,
    args: list[Any] | dict[str, Any] | None = None,
    sender: str | None = None,
    block: str | None = None,
) -> Dict[str, Any]:
    from omni_sdk.address import is_valid as is_valid_address  # type: ignore
    from omni_sdk.cli.call import _resolve_args  # type: ignore
    from omni_sdk.contracts.client import ContractClient  # type: ignore

    if not is_valid_address(contract_address):
        raise ValueError(f"invalid contract address: {contract_address}")
    rpc = _resolve_rpc_endpoint(rpc_url)
    client = _rpc_client(rpc)
    contract = ContractClient(rpc=client, address=contract_address, abi=abi, chain_id=int(chain_id))
    resolved_args = args
    if isinstance(resolved_args, dict):
        calldata = contract.encode_call_data(method, list(resolved_args.values()))
    else:
        calldata = contract.encode_call_data(method, list(resolved_args or []))
    candidates = [
        ("execution.simulateCall", [{"to": contract_address, "data": "0x" + calldata.hex(), "from": sender, "block": block}]),
        ("state.call", [{"to": contract_address, "data": "0x" + calldata.hex(), "from": sender, "block": block}]),
        ("vm.simulateCall", [contract_address, "0x" + calldata.hex(), sender, block]),
    ]
    last_error = None
    for method_name, params in candidates:
        try:
            raw = client.call(method_name, params)
            decoded = None
            if isinstance(raw, str) and raw.startswith("0x"):
                try:
                    decoded = contract.decode_return(method, bytes.fromhex(raw[2:]))
                except Exception:
                    decoded = raw
            else:
                decoded = raw
            return {"rpc_method": method_name, "result": decoded, "raw": raw, "calldata": "0x" + calldata.hex()}
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"contract read failed: {last_error}")


def raw_contract_read(
    rpc_url: str | None,
    contract_address: str,
    data_hex: str,
    sender: str | None = None,
    block: str | None = None,
) -> Dict[str, Any]:
    rpc = _resolve_rpc_endpoint(rpc_url)
    payload = data_hex[2:] if data_hex.startswith("0x") else data_hex
    if not payload:
        raise ValueError("raw payload is empty")
    params_candidates = [
        ("execution.simulateCall", [{"to": contract_address, "data": "0x" + payload, "from": sender, "block": block}]),
        ("state.call", [{"to": contract_address, "data": "0x" + payload, "from": sender, "block": block}]),
        ("vm.simulateCall", [contract_address, "0x" + payload, sender, block]),
    ]
    last_error = None
    for method_name, params in params_candidates:
        try:
            raw = tx_cli._rpc(rpc, method_name, params)
            return {"rpc_method": method_name, "raw": raw, "payload": "0x" + payload}
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"raw contract read failed: {last_error}")


def preview_contract_call(contract_address: str, abi: Any, method: str, args: list[Any] | dict[str, Any] | None = None) -> Dict[str, Any]:
    from omni_sdk.contracts.client import ContractClient  # type: ignore

    class _PreviewClient:
        def call(self, method: str, params: Optional[list | dict] = None) -> Any:
            raise RuntimeError("preview client does not support RPC calls")

    contract = ContractClient(rpc=_PreviewClient(), address=contract_address, abi=abi, chain_id=1)
    calldata = contract.encode_call_data(method, list(args.values()) if isinstance(args, dict) else list(args or []))
    return {"payload": "0x" + calldata.hex()}


def contract_write(
    wallet_file: str,
    rpc_url: str | None,
    from_address: str,
    contract_address: str,
    abi: Any,
    method: str,
    args: list[Any] | dict[str, Any] | None = None,
    *,
    chain_id: int,
    max_fee: int | None = None,
    gas_limit: int | None = None,
    nonce: int | None = None,
) -> Dict[str, Any]:
    from omni_sdk.address import is_valid as is_valid_address  # type: ignore
    from omni_sdk.contracts.client import ContractClient  # type: ignore

    if not is_valid_address(contract_address):
        raise ValueError(f"invalid contract address: {contract_address}")
    rpc = _resolve_rpc_endpoint(rpc_url)
    client = _rpc_client(rpc)
    contract = ContractClient(rpc=client, address=contract_address, abi=abi, chain_id=int(chain_id))
    calldata = contract.encode_call_data(method, list(args.values()) if isinstance(args, dict) else list(args or []))
    return _submit_signed_tx(
        wallet_file=wallet_file,
        rpc_url=rpc,
        from_address=from_address,
        to_address=contract_address,
        value_base=0,
        gas_limit=int(gas_limit or contract.suggest_gas_limit(calldata, kind="call")),
        max_fee=max_fee,
        chain_id=chain_id,
        nonce=nonce,
        valid_after=None,
        valid_until=None,
        ttl_blocks=None,
        data_bytes=calldata,
    )


def _success(result: Any) -> Dict[str, Any]:
    return {"ok": True, "result": result}


def _failure(exc: Exception) -> Dict[str, Any]:
    return {
        "ok": False,
        "error": {
            "type": exc.__class__.__name__,
            "message": str(exc),
        },
    }


def _dispatch(payload: Dict[str, Any]) -> Dict[str, Any]:
    op = payload.get("op")
    args = payload.get("args") or {}
    if op == "supported_algorithms":
        return _success(supported_algorithms())
    if op == "init_store":
        return _success(init_store(args["wallet_file"]))
    if op == "list_wallets":
        return _success(list_wallets(args["wallet_file"]))
    if op == "create_wallet":
        return _success(create_wallet(args["wallet_file"], args["label"], args.get("algorithm")))
    if op == "rename_wallet":
        return _success(rename_wallet(args["wallet_file"], args["wallet_id"], args["label"]))
    if op == "remove_wallet":
        return _success(remove_wallet(args["wallet_file"], args["wallet_id"]))
    if op == "set_default":
        return _success(set_default(args["wallet_file"], args["wallet_id"]))
    if op == "export_public":
        return _success(export_public(args["wallet_file"], args["wallet_id"]))
    if op == "export_secret":
        return _success(export_secret(args["wallet_file"], args["wallet_id"], args["destination"]))
    if op == "import_wallets":
        return _success(import_wallets(args["wallet_file"], args["source_file"], args.get("mode", "merge")))
    if op == "validate_address":
        return _success(validate_wallet_address(args["address"]))
    if op == "wallet_overview":
        return _success(wallet_overview(args["wallet_file"], args.get("rpc_url")))
    if op == "send_transaction":
        return _success(
            send_transaction(
                args["wallet_file"],
                args.get("rpc_url"),
                args["from_address"],
                args["to_address"],
                args["amount"],
                gas_limit=args.get("gas_limit"),
                max_fee=args.get("max_fee"),
                chain_id=args.get("chain_id"),
                nonce=args.get("nonce"),
                valid_after=args.get("valid_after"),
                valid_until=args.get("valid_until"),
                ttl_blocks=args.get("ttl_blocks"),
                data_hex=args.get("data_hex"),
            )
        )
    if op == "transaction_status":
        return _success(transaction_status(args.get("rpc_url"), args["tx_hash"]))
    if op == "transaction_details":
        return _success(transaction_details(args.get("rpc_url"), args.get("explorer_url"), args["tx_hash"]))
    if op == "fetch_history":
        return _success(
            fetch_history(
                args["wallet_file"],
                args.get("rpc_url"),
                args.get("explorer_url"),
                wallet_id=args.get("wallet_id"),
                direction=args.get("direction"),
                status=args.get("status"),
                search=args.get("search"),
                start_time=args.get("start_time"),
                end_time=args.get("end_time"),
                limit=int(args.get("limit", 200)),
            )
        )
    if op == "contract_read":
        return _success(
            contract_read(
                args.get("rpc_url"),
                int(args["chain_id"]),
                args["contract_address"],
                args["abi"],
                args["method"],
                args.get("args"),
                sender=args.get("sender"),
                block=args.get("block"),
            )
        )
    if op == "preview_contract_call":
        return _success(
            preview_contract_call(
                args["contract_address"],
                args["abi"],
                args["method"],
                args.get("args"),
            )
        )
    if op == "raw_contract_read":
        return _success(
            raw_contract_read(
                args.get("rpc_url"),
                args["contract_address"],
                args["data_hex"],
                sender=args.get("sender"),
                block=args.get("block"),
            )
        )
    if op == "contract_write":
        return _success(
            contract_write(
                args["wallet_file"],
                args.get("rpc_url"),
                args["from_address"],
                args["contract_address"],
                args["abi"],
                args["method"],
                args.get("args"),
                chain_id=int(args["chain_id"]),
                max_fee=args.get("max_fee"),
                gas_limit=args.get("gas_limit"),
                nonce=args.get("nonce"),
            )
        )
    raise ValueError(f"unknown bridge operation: {op}")


def main() -> int:
    try:
        raw = json.loads(sys.stdin.read())
        response = _dispatch(raw)
    except Exception as exc:  # pragma: no cover - exercised via CLI tests
        response = _failure(exc)
    print(json.dumps(response))
    return 0 if response.get("ok") else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
