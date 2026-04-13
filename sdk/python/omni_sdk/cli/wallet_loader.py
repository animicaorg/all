from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

import typer

from ..address import from_pubkey
from ..wallet.signer import PQSigner

ALG_BY_ID = {
    0x1001: "dilithium3",
    0x1002: "sphincs_shake_128s",
}

ALG_ALIASES = {
    "dilithium": "dilithium3",
    "dilithium-3": "dilithium3",
    "dilithium3": "dilithium3",
    "ml-dsa-65": "dilithium3",
    "mldsa65": "dilithium3",
    "sphincs": "sphincs_shake_128s",
    "sphincs+": "sphincs_shake_128s",
    "sphincs+-shake-128s": "sphincs_shake_128s",
    "sphincs_shake_128s": "sphincs_shake_128s",
}

_ANIMICA_RESERVED_TOPLEVEL_KEYS = {
    "format",
    "version",
    "wallets",
    "default",
    "default_label",
    "default_address",
    "created_at",
    "updated_at",
}


@dataclass(frozen=True)
class LoadedWalletSigner:
    signer: PQSigner
    sender: str
    source: str
    source_kind: str
    selected_label: Optional[str]
    selected_address: Optional[str]


def wallet_store_default_path() -> Path:
    env_path = os.getenv("ANIMICA_WALLETS_FILE")
    if env_path and env_path.strip():
        return Path(env_path).expanduser()
    return Path.home() / ".animica" / "wallets.json"


def normalize_alg_name(raw_name: str) -> str:
    normalized = ALG_ALIASES.get(raw_name.strip().lower(), raw_name.strip().lower())
    if normalized not in ("dilithium3", "sphincs_shake_128s"):
        raise typer.BadParameter(
            f"unsupported algorithm '{raw_name}' "
            "(supported: dilithium3, sphincs_shake_128s)"
        )
    return normalized


def parse_alg_id(raw_value: Any) -> Optional[int]:
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        value = raw_value.strip().lower()
        if not value:
            return None
        try:
            return int(value, 16) if value.startswith("0x") else int(value)
        except Exception as exc:  # noqa: BLE001
            raise typer.BadParameter(f"invalid wallet alg_id '{raw_value}'") from exc
    try:
        return int(raw_value)
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"invalid wallet alg_id '{raw_value}'") from exc


def _normalize_hex(value: str, *, field: str) -> str:
    raw = value.strip()
    if raw.startswith(("0x", "0X")):
        raw = raw[2:]
    if not raw:
        raise typer.BadParameter(f"{field} cannot be empty")
    if len(raw) % 2 != 0:
        raise typer.BadParameter(f"{field} must contain an even number of hex characters")
    try:
        bytes.fromhex(raw)
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"{field} must be valid hex") from exc
    return raw


def _sender_from_signer(signer: PQSigner) -> str:
    return signer.address or from_pubkey(signer.public_key, alg_id=signer.alg_id, hrp="anim")


def _read_wallet_json(path: Path) -> Any:
    expanded = path.expanduser()
    try:
        raw_text = expanded.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"wallet file not found: {expanded}") from exc
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"failed to read wallet file {expanded}: {exc}") from exc

    try:
        return json.loads(raw_text)
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"invalid JSON in wallet file {expanded}: {exc}") from exc


def _looks_like_animica_wallet_store(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    if str(raw.get("format") or "").strip().lower() == "animica.wallets":
        return True
    if isinstance(raw.get("wallets"), list):
        return True
    return "default_address" in raw or "default_label" in raw


def _looks_like_sdk_keystore_entries(raw: Any) -> bool:
    if isinstance(raw, list):
        return True
    if not isinstance(raw, dict):
        return False
    if isinstance(raw.get("wallets"), list):
        return False

    if any(
        key in raw
        for key in (
            "address",
            "label",
            "public_key_hex",
            "publicKeyHex",
            "pubkey",
            "pk",
            "secret_key_hex",
            "secretKeyHex",
            "alg_id",
            "algId",
            "alg_name",
            "algName",
        )
    ):
        return True

    dict_values = [value for value in raw.values() if isinstance(value, Mapping)]
    return bool(dict_values)


def _looks_like_sdk_encrypted_keystore(raw: Any) -> bool:
    if not isinstance(raw, dict):
        return False
    if "ciphertext" not in raw:
        return False
    py_like = (
        isinstance(raw.get("kdf"), str)
        and isinstance(raw.get("aead"), str)
        and "salt" in raw
        and "nonce" in raw
    )
    rs_like = isinstance(raw.get("kdf"), Mapping) and isinstance(raw.get("aead"), Mapping)
    ts_like = isinstance(raw.get("crypto"), Mapping)
    return bool(py_like or rs_like or ts_like)


def _entry_label(entry: Dict[str, Any], *, fallback: Optional[str] = None) -> str:
    label = str(entry.get("label") or fallback or "").strip()
    return label


def _entry_address(entry: Dict[str, Any]) -> str:
    return str(entry.get("address") or "").strip()


def _extract_entries_from_animica_wallet_store(raw: Dict[str, Any]) -> Tuple[list[Dict[str, Any]], Optional[str], Optional[str]]:
    default_label = None
    if isinstance(raw.get("default"), str):
        default_label = str(raw.get("default")).strip() or None
    elif isinstance(raw.get("default_label"), str):
        default_label = str(raw.get("default_label")).strip() or None

    default_address = None
    if isinstance(raw.get("default_address"), str):
        default_address = str(raw.get("default_address")).strip() or None

    entries_raw: list[Any]
    if isinstance(raw.get("wallets"), list):
        entries_raw = list(raw.get("wallets") or [])
    else:
        entries_raw = []
        for key, value in raw.items():
            if key in _ANIMICA_RESERVED_TOPLEVEL_KEYS:
                continue
            if not isinstance(value, Mapping):
                continue
            item = dict(value)
            item.setdefault("label", str(key))
            entries_raw.append(item)

    entries = [dict(item) for item in entries_raw if isinstance(item, Mapping)]
    if not entries:
        raise typer.BadParameter("wallet store has no wallet entries")
    return entries, default_label, default_address


def _extract_entries_from_sdk_keystore(raw: Any) -> Tuple[list[Dict[str, Any]], Optional[str], Optional[str]]:
    default_label = None
    default_address = None

    entries_raw: list[Any]
    if isinstance(raw, list):
        entries_raw = list(raw)
    elif isinstance(raw, dict):
        if isinstance(raw.get("default"), str):
            default_label = str(raw.get("default")).strip() or None
        if isinstance(raw.get("default_address"), str):
            default_address = str(raw.get("default_address")).strip() or None

        if any(
            key in raw
            for key in (
                "address",
                "label",
                "public_key_hex",
                "publicKeyHex",
                "pubkey",
                "pk",
                "secret_key_hex",
                "secretKeyHex",
            )
        ):
            entries_raw = [dict(raw)]
        else:
            entries_raw = []
            for key, value in raw.items():
                if key in _ANIMICA_RESERVED_TOPLEVEL_KEYS:
                    continue
                if not isinstance(value, Mapping):
                    continue
                item = dict(value)
                item.setdefault("label", str(key))
                entries_raw.append(item)
    else:
        raise typer.BadParameter("unsupported SDK keystore format; expected object or list")

    entries = [dict(item) for item in entries_raw if isinstance(item, Mapping)]
    if not entries:
        raise typer.BadParameter("SDK keystore has no wallet entries")
    return entries, default_label, default_address


def _find_entry_by_label(entries: Sequence[Dict[str, Any]], label: str, *, path: Path) -> Dict[str, Any]:
    needle = label.strip().lower()
    if not needle:
        raise typer.BadParameter("--label cannot be empty")
    matches = []
    for item in entries:
        item_label = _entry_label(item).lower()
        if item_label == needle:
            matches.append(item)
    if not matches:
        raise typer.BadParameter(f"wallet label '{label}' not found in {path}")
    if len(matches) > 1:
        raise typer.BadParameter(f"wallet label '{label}' is ambiguous in {path}")
    return matches[0]


def _find_entry_by_address(entries: Sequence[Dict[str, Any]], address: str, *, path: Path) -> Dict[str, Any]:
    needle = address.strip().lower()
    if not needle:
        raise typer.BadParameter("--address cannot be empty")
    matches = []
    for item in entries:
        item_address = _entry_address(item).lower()
        if item_address == needle:
            matches.append(item)
    if not matches:
        raise typer.BadParameter(f"wallet address '{address}' not found in {path}")
    if len(matches) > 1:
        raise typer.BadParameter(f"wallet address '{address}' is ambiguous in {path}")
    return matches[0]


def _resolve_default_entry_strict(
    entries: Sequence[Dict[str, Any]],
    *,
    default_label: Optional[str],
    default_address: Optional[str],
    path: Path,
) -> Dict[str, Any]:
    if not default_label and not default_address:
        raise typer.BadParameter(
            "wallet store does not define a default wallet; pass --label or --address"
        )

    selected: Optional[Dict[str, Any]] = None

    if default_label:
        candidate = _find_entry_by_label(entries, default_label, path=path)
        selected = candidate

    if default_address:
        candidate = _find_entry_by_address(entries, default_address, path=path)
        if selected is None:
            selected = candidate
        elif selected is not candidate:
            raise typer.BadParameter(
                "wallet store default label/address point to different wallets; "
                "pass --label or --address explicitly"
            )

    if selected is None:
        raise typer.BadParameter(
            "wallet store has no usable default wallet; pass --label or --address"
        )
    return selected


def _resolve_default_entry_compat(
    entries: Sequence[Dict[str, Any]],
    *,
    default_label: Optional[str],
    default_address: Optional[str],
    path: Path,
) -> Optional[Dict[str, Any]]:
    if default_label or default_address:
        try:
            return _resolve_default_entry_strict(
                entries,
                default_label=default_label,
                default_address=default_address,
                path=path,
            )
        except typer.BadParameter:
            pass
    return None


def _wallet_alg_name(entry: Dict[str, Any]) -> str:
    name_raw = entry.get("alg_name") or entry.get("algName")
    alg_id = parse_alg_id(entry.get("alg_id", entry.get("algId", entry.get("alg"))))
    name_from_id = ALG_BY_ID.get(int(alg_id)) if alg_id is not None else None
    if isinstance(name_raw, str) and name_raw.strip():
        normalized_name = normalize_alg_name(name_raw)
        if name_from_id and name_from_id != normalized_name:
            raise typer.BadParameter(
                "wallet algorithm mismatch: "
                f"alg_id {alg_id} maps to {name_from_id}, but alg_name is {name_raw!r}"
            )
        return normalized_name
    if name_from_id:
        return name_from_id
    raise typer.BadParameter(
        "wallet entry missing supported alg_id/alg_name "
        "(expected dilithium3 or sphincs_shake_128s)"
    )


def _wallet_key_bytes(
    entry: Dict[str, Any],
    *,
    field_name: str,
    aliases: Sequence[str],
) -> Optional[bytes]:
    for key in aliases:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return bytes.fromhex(_normalize_hex(value, field=f"wallet.{field_name}"))
    return None


def _build_signer_from_entry(
    entry: Dict[str, Any],
    *,
    entry_identifier: str,
    alg_override: Optional[str],
) -> PQSigner:
    wallet_alg = _wallet_alg_name(entry)
    if alg_override:
        normalized_override = normalize_alg_name(alg_override)
        if normalized_override != wallet_alg:
            raise typer.BadParameter(
                f"--alg={normalized_override} does not match wallet algorithm {wallet_alg}"
            )

    secret_key = _wallet_key_bytes(
        entry,
        field_name="secret_key_hex",
        aliases=(
            "secret_key_hex",
            "secretKeyHex",
            "private_key_hex",
            "privateKeyHex",
            "sk",
        ),
    )
    if secret_key is None:
        raise typer.BadParameter(
            f"wallet entry '{entry_identifier}' has no usable private signing material "
            "(expected secret_key_hex)"
        )

    public_key = _wallet_key_bytes(
        entry,
        field_name="public_key_hex",
        aliases=("public_key_hex", "publicKeyHex", "pubkey", "pk"),
    )

    if public_key is not None:
        try:
            return PQSigner.from_keypair(
                alg_name=wallet_alg,
                secret_key=secret_key,
                public_key=public_key,
            )
        except Exception as exc:  # noqa: BLE001
            raise typer.BadParameter(
                f"failed to create signer from wallet entry '{entry_identifier}': {exc}"
            ) from exc

    if len(secret_key) == 32:
        try:
            return PQSigner.from_seed(wallet_alg, seed=secret_key)
        except Exception as exc:  # noqa: BLE001
            raise typer.BadParameter(
                f"failed to create signer from seed material in wallet entry "
                f"'{entry_identifier}': {exc}"
            ) from exc

    raise typer.BadParameter(
        f"wallet entry '{entry_identifier}' is missing public_key_hex and does not contain "
        "a 32-byte seed"
    )


def load_signer_from_wallet_source(
    *,
    path: Path,
    label: Optional[str],
    address: Optional[str],
    alg_override: Optional[str],
    strict_default_selection: bool,
) -> LoadedWalletSigner:
    expanded = path.expanduser()
    raw = _read_wallet_json(expanded)

    is_animica = _looks_like_animica_wallet_store(raw)
    is_sdk_legacy = _looks_like_sdk_keystore_entries(raw)
    is_sdk_encrypted = _looks_like_sdk_encrypted_keystore(raw)
    if is_sdk_encrypted:
        # Avoid classifying encrypted keystores as legacy entry maps.
        is_sdk_legacy = False

    detected_formats = sum(1 for flag in (is_animica, is_sdk_legacy, is_sdk_encrypted) if flag)
    if detected_formats == 0:
        raise typer.BadParameter(
            f"unsupported wallet file format in {expanded}; expected Animica wallet store or SDK keystore entries"
        )
    if detected_formats > 1:
        raise typer.BadParameter(
            f"ambiguous wallet file format in {expanded}; could match multiple formats"
        )

    if label and address:
        raise typer.BadParameter("pass only one selector: --label or --address")

    if is_sdk_encrypted:
        raise typer.BadParameter(
            "encrypted SDK keystore format is not supported by deploy signing; "
            "use an Animica wallet store (wallets.json) or a keystore entry file with signer material"
        )

    if is_animica:
        entries, default_label, default_address = _extract_entries_from_animica_wallet_store(raw)
        source_kind = "wallet-store"
    else:
        entries, default_label, default_address = _extract_entries_from_sdk_keystore(raw)
        source_kind = "keystore"

    selected_entry: Optional[Dict[str, Any]] = None
    selected_label: Optional[str] = None
    selected_address: Optional[str] = None

    if label:
        selected_entry = _find_entry_by_label(entries, label, path=expanded)
        selected_label = _entry_label(selected_entry) or label
    elif address:
        selected_entry = _find_entry_by_address(entries, address, path=expanded)
        selected_address = _entry_address(selected_entry) or address
        selected_label = _entry_label(selected_entry) or None
    elif strict_default_selection:
        selected_entry = _resolve_default_entry_strict(
            entries,
            default_label=default_label,
            default_address=default_address,
            path=expanded,
        )
        selected_label = _entry_label(selected_entry) or None
        selected_address = _entry_address(selected_entry) or None
    else:
        selected_entry = _resolve_default_entry_compat(
            entries,
            default_label=default_label,
            default_address=default_address,
            path=expanded,
        )
        if selected_entry is None:
            if len(entries) == 1:
                selected_entry = entries[0]
            else:
                raise typer.BadParameter(
                    "wallet file has multiple entries; pass --label or --address"
                )
        selected_label = _entry_label(selected_entry) or None
        selected_address = _entry_address(selected_entry) or None

    entry_id = selected_label or selected_address or "selected"
    signer = _build_signer_from_entry(
        selected_entry,
        entry_identifier=entry_id,
        alg_override=alg_override,
    )

    wallet_address = _entry_address(selected_entry)
    if not wallet_address:
        raise typer.BadParameter(f"wallet entry '{entry_id}' is missing address")

    signer_address = _sender_from_signer(signer)
    if wallet_address.lower() != signer_address.lower():
        raise typer.BadParameter(
            "wallet address mismatch: "
            f"wallet entry '{entry_id}' has {wallet_address}, "
            f"derived signer address is {signer_address}"
        )

    source_ref = selected_label or selected_address or "selected"
    return LoadedWalletSigner(
        signer=signer,
        sender=signer_address,
        source=f"wallet:{source_ref}@{expanded}",
        source_kind=source_kind,
        selected_label=selected_label,
        selected_address=wallet_address,
    )
