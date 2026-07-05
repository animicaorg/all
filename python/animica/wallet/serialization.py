from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MAX_WALLETS_FILE_SIZE = 5 * 1024 * 1024

try:
    from pq.py.address import validate_address
except Exception:  # pragma: no cover
    validate_address = None

# At-rest secret encryption (ANM-C07). Additive/opt-in: default behavior is
# unchanged and plaintext wallets keep loading/saving exactly as before.
from .at_rest import (
    decrypt_store_secrets,
    is_encrypted_secret,
    resolve_passphrase,
    store_is_encrypted,
)


class WalletParseError(ValueError):
    pass


@dataclass
class WalletImportResult:
    store: Dict[str, Any]
    warnings: List[str]
    failures: List[str]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_iso(value: Any, fallback: Optional[str] = None) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return fallback or _utc_now_iso()


def _normalize_hex(value: Any, *, field: str) -> str:
    if not isinstance(value, str):
        raise WalletParseError(f"{field} must be a string")
    raw = value.strip().lower()
    if raw.startswith("0x"):
        raw = raw[2:]
    if not raw:
        raise WalletParseError(f"{field} cannot be empty")
    if len(raw) % 2 != 0:
        raise WalletParseError(f"{field} must have even-length hex")
    try:
        bytes.fromhex(raw)
    except ValueError as exc:
        raise WalletParseError(f"{field} must be valid hex") from exc
    return raw


def _validate_address_soft(address: Any, warnings: List[str]) -> str:
    if not isinstance(address, str) or not address.strip():
        raise WalletParseError("address is required")
    value = address.strip()
    if validate_address is not None:
        try:
            validate_address(value, expect_hrp="anim")
        except Exception as exc:
            raise WalletParseError(f"invalid bech32 address '{value}': {exc}") from exc
    elif not value.startswith("anim1"):
        warnings.append(f"Address '{value}' does not start with anim1 (soft warning)")
    return value


def _fingerprint(pub_hex: str) -> str:
    d = hashlib.sha256(bytes.fromhex(pub_hex)).hexdigest()
    return d[:16]


def _wallet_from_legacy(raw: Dict[str, Any], warnings: List[str], idx: int) -> Dict[str, Any]:
    label = str(raw.get("label") or raw.get("name") or raw.get("alias") or f"wallet-{idx + 1}").strip()
    if not label:
        label = f"wallet-{idx + 1}"

    address = _validate_address_soft(raw.get("address") or raw.get("addr"), warnings)

    alg_id_raw = raw.get("alg_id", raw.get("algId", raw.get("alg")))
    alg_name = raw.get("alg_name") or raw.get("algName")
    alg_id = None
    if alg_id_raw is not None:
        try:
            alg_id = int(alg_id_raw)
        except Exception as exc:
            raise WalletParseError(f"wallet[{idx}] invalid alg_id '{alg_id_raw}'") from exc
    if alg_id is None and isinstance(alg_name, str):
        low = alg_name.lower()
        if "dilith" in low:
            alg_id = 0x1001
        elif "sphincs" in low:
            alg_id = 0x1002
    if alg_id is None:
        raise WalletParseError(f"wallet[{idx}] missing alg_id/alg_name")
    if not alg_name:
        alg_name = f"alg-{alg_id}"

    pub_raw = (
        raw.get("public_key_hex")
        or raw.get("publicKeyHex")
        or raw.get("pubkey")
        or raw.get("pub")
        or raw.get("pk")
    )
    public_key_hex = _normalize_hex(pub_raw, field=f"wallet[{idx}].public_key_hex")

    wallet: Dict[str, Any] = {
        "label": label,
        "address": address,
        "alg_id": int(alg_id),
        "alg_name": str(alg_name),
        "public_key_hex": public_key_hex,
        "pub_fingerprint": _fingerprint(public_key_hex),
        "created_at": _as_iso(raw.get("created_at") or raw.get("createdAt")),
    }

    secret_raw = raw.get("secret_key_hex") or raw.get("secretKeyHex")
    if isinstance(secret_raw, str) and secret_raw.strip() and secret_raw.strip() != "0x":
        wallet["secret_key_hex"] = _normalize_hex(secret_raw, field=f"wallet[{idx}].secret_key_hex")

    # At-rest encryption (ANM-C07): preserve the encrypted secret envelope so
    # it round-trips through parse -> canonical save. Never contains plaintext.
    secret_enc = raw.get("secret_key_enc")
    if is_encrypted_secret(secret_enc):
        wallet["secret_key_enc"] = dict(secret_enc)

    if isinstance(raw.get("private_key_enc"), str) and raw.get("private_key_enc").strip():
        wallet["private_key_enc"] = raw.get("private_key_enc").strip()
    if isinstance(raw.get("keystore"), dict):
        wallet["keystore"] = raw.get("keystore")
    if isinstance(raw.get("meta"), dict):
        wallet["meta"] = raw.get("meta")
    if isinstance(raw.get("pending_txs"), list):
        wallet["pending_txs"] = [dict(item) for item in raw.get("pending_txs") if isinstance(item, dict)]

    return wallet


def parse_wallets_text(text: str, *, source: str = "<memory>") -> WalletImportResult:
    warnings: List[str] = []
    failures: List[str] = []
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise WalletParseError(f"Invalid JSON in {source} at line {exc.lineno} column {exc.colno}: {exc.msg}") from exc

    created_at = _utc_now_iso()
    updated_at = created_at
    default_label = None
    records: List[Tuple[str, Dict[str, Any]]] = []

    if isinstance(raw, dict) and raw.get("format") == "animica.wallets" and int(raw.get("version", 0)) == 2:
        wallets_raw = raw.get("wallets")
        created_at = _as_iso(raw.get("created_at"), created_at)
        updated_at = _as_iso(raw.get("updated_at"), created_at)
        default_label = raw.get("default")
        if not isinstance(wallets_raw, list):
            raise WalletParseError("wallets must be a list")
        iterable = wallets_raw
    elif isinstance(raw, dict) and isinstance(raw.get("wallets"), list):
        iterable = raw["wallets"]
        default_label = raw.get("default") or raw.get("default_label")
        warnings.append("Imported legacy wallets store (missing format/version 2)")
    elif isinstance(raw, list):
        iterable = raw
        warnings.append("Imported legacy wallets array")
    elif isinstance(raw, dict):
        iterable = []
        for k, v in raw.items():
            if isinstance(v, dict):
                vv = dict(v)
                vv.setdefault("label", k)
                iterable.append(vv)
        if iterable:
            warnings.append("Imported legacy object-map wallets format")
        else:
            raise WalletParseError("Unsupported wallets.json structure")
    else:
        raise WalletParseError("Unsupported wallets.json top-level type")

    for idx, item in enumerate(iterable):
        if not isinstance(item, dict):
            failures.append(f"wallet[{idx}] must be object")
            continue
        try:
            wallet = _wallet_from_legacy(item, warnings, idx)
            records.append((wallet["label"], wallet))
        except Exception as exc:
            failures.append(str(exc))

    wallets = [w for _, w in sorted(records, key=lambda t: t[0].lower())]

    store = {
        "format": "animica.wallets",
        "version": 2,
        "created_at": created_at,
        "updated_at": updated_at,
        "wallets": wallets,
        "default": default_label,
    }
    return WalletImportResult(store=store, warnings=warnings, failures=failures)


def canonical_json_dumps(store: Dict[str, Any]) -> str:
    text = json.dumps(store, indent=2, ensure_ascii=False)
    return text + "\n"


def load_store_canonical(path: Path, *, passphrase: Optional[str] = None) -> WalletImportResult:
    """Load and canonicalize the wallet store from ``path``.

    Backward compatible: plaintext stores load exactly as before. For stores
    that opted into at-rest encryption (ANM-C07), if a passphrase is available
    (the explicit ``passphrase`` arg, else ``ANIMICA_WALLET_PASSPHRASE`` /
    ``ANIMICA_WALLET_PASSPHRASE_FILE``) the encrypted secrets are transparently
    decrypted *in memory* so downstream signing keeps working. Without a
    passphrase, encrypted entries stay sealed (address/pubkey still load) so
    read-only operations continue to function — fail-closed, non-fatal.
    """
    if not path.exists():
        now = _utc_now_iso()
        return WalletImportResult(
            store={
                "format": "animica.wallets",
                "version": 2,
                "created_at": now,
                "updated_at": now,
                "wallets": [],
                "default": None,
            },
            warnings=[],
            failures=[],
        )

    size = path.stat().st_size
    if size > MAX_WALLETS_FILE_SIZE:
        raise WalletParseError(f"Refusing to load {path}: file too large ({size} bytes)")
    result = parse_wallets_text(path.read_text(encoding="utf-8"), source=str(path))

    if store_is_encrypted(result.store):
        secret = resolve_passphrase(passphrase)
        if secret:
            result.store = decrypt_store_secrets(result.store, secret)
        else:
            result.warnings.append(
                "Wallet store is encrypted at rest; set ANIMICA_WALLET_PASSPHRASE "
                "to unlock secret keys (read-only operations still work)."
            )
    return result


def _next_label(label: str, taken: set[str]) -> str:
    if label not in taken:
        return label
    i = 2
    while f"{label}-{i}" in taken:
        i += 1
    return f"{label}-{i}"


def merge_imported_wallets(existing: Dict[str, Any], imported: Dict[str, Any], *, mode: str = "merge") -> Dict[str, Any]:
    if mode == "replace":
        out = dict(imported)
        out["updated_at"] = _utc_now_iso()
        return out

    out = dict(existing)
    existing_wallets = [dict(w) for w in existing.get("wallets", []) if isinstance(w, dict)]
    taken_labels = {str(w.get("label")) for w in existing_wallets if w.get("label")}
    taken_addresses = {str(w.get("address")) for w in existing_wallets if w.get("address")}

    for wallet in imported.get("wallets", []):
        if not isinstance(wallet, dict):
            continue
        entry = dict(wallet)
        label = str(entry.get("label") or "wallet")
        address = str(entry.get("address") or "")
        if address in taken_addresses:
            label = _next_label(label, taken_labels)
            entry["label"] = label
        elif label in taken_labels:
            label = _next_label(label, taken_labels)
            entry["label"] = label
        taken_labels.add(label)
        taken_addresses.add(address)
        existing_wallets.append(entry)

    out["wallets"] = sorted(existing_wallets, key=lambda w: str(w.get("label", "")).lower())
    out["updated_at"] = _utc_now_iso()
    return out


def export_canonical_store(store: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(store)
    out["format"] = "animica.wallets"
    out["version"] = 2
    out["created_at"] = _as_iso(out.get("created_at"))
    out["updated_at"] = _utc_now_iso()
    wallets = [dict(w) for w in out.get("wallets", []) if isinstance(w, dict)]
    # ANM-C07: if an entry has a valid encrypted secret envelope, the canonical
    # on-disk form must NOT carry plaintext. A transparent-decrypt read
    # (load_store_canonical) populates secret_key_hex in memory; stripping it
    # here guarantees a subsequent save never re-materializes the plaintext.
    for w in wallets:
        if is_encrypted_secret(w.get("secret_key_enc")):
            w.pop("secret_key_hex", None)
            w.pop("secretKeyHex", None)
    out["wallets"] = sorted(wallets, key=lambda w: str(w.get("label", "")).lower())
    return out
