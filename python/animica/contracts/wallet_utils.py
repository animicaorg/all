from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import typer


@dataclass(frozen=True)
class SignerResolution:
    signer: Any
    sender: str
    source: str
    source_kind: str


ALG_BY_ID = {
    0x1001: "dilithium3",
    0x1002: "sphincs_shake_128s",
}


def wallet_store_path(path: Optional[Path] = None) -> Path:
    if path is not None:
        return path.expanduser()
    env_value = os.environ.get("ANIMICA_WALLETS_FILE")
    if env_value and env_value.strip():
        return Path(env_value).expanduser()
    return Path.home() / ".animica" / "wallets.json"


def is_animica_address(value: str) -> bool:
    text = value.strip()
    if not text:
        return False
    try:
        from omni_sdk.address import validate

        return bool(validate(text, expected_hrp="anim"))
    except Exception:
        pass

    try:
        from pq.py.address import validate_address

        validate_address(text, expect_hrp="anim")
        return True
    except Exception:
        return False


def _load_wallet_entries(path: Path) -> list[dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise typer.BadParameter(f"wallet file not found: {path}") from exc
    except Exception as exc:  # noqa: BLE001
        raise typer.BadParameter(f"invalid wallet JSON at {path}: {exc}") from exc

    if isinstance(raw, dict) and isinstance(raw.get("wallets"), list):
        entries = raw["wallets"]
    elif isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        entries = []
        for key, value in raw.items():
            if not isinstance(value, dict):
                continue
            item = dict(value)
            item.setdefault("label", str(key))
            entries.append(item)
    else:
        raise typer.BadParameter(
            f"unsupported wallet format at {path}; expected object with 'wallets' list"
        )

    normalized: list[dict[str, Any]] = []
    for entry in entries:
        if isinstance(entry, dict):
            normalized.append(entry)
    return normalized


def resolve_wallet_address(identifier: str, *, wallet_file: Optional[Path] = None) -> str:
    text = identifier.strip()
    if not text:
        raise typer.BadParameter("wallet selector cannot be empty")

    if is_animica_address(text):
        return text

    path = wallet_store_path(wallet_file)
    entries = _load_wallet_entries(path)
    needle = text.lower()

    for entry in entries:
        label = str(entry.get("label") or "").strip().lower()
        address = str(entry.get("address") or "").strip()
        if not address:
            continue
        if needle == label or needle == address.lower():
            return address

    raise typer.BadParameter(
        f"wallet '{identifier}' not found in {path}; use 'animica wallet list' to inspect available labels"
    )


def _fallback_signer_from_wallet(
    *,
    path: Path,
    label: Optional[str],
    address: Optional[str],
    alg_override: Optional[str],
) -> SignerResolution:
    try:
        from omni_sdk.wallet.signer import PQSigner
    except Exception as exc:  # pragma: no cover - dependency gate
        raise typer.BadParameter(
            "contract deploy/send requires omni_sdk.wallet.signer.PQSigner"
        ) from exc

    entries = _load_wallet_entries(path)
    selected: Optional[dict[str, Any]] = None
    selector = label or address
    if label and address:
        raise typer.BadParameter("pass only one of label or address")

    if label:
        needle = label.strip().lower()
        for entry in entries:
            if str(entry.get("label") or "").strip().lower() == needle:
                selected = entry
                break
        if selected is None:
            raise typer.BadParameter(f"wallet label '{label}' not found in {path}")
    elif address:
        needle = address.strip().lower()
        for entry in entries:
            if str(entry.get("address") or "").strip().lower() == needle:
                selected = entry
                break
        if selected is None:
            raise typer.BadParameter(f"wallet address '{address}' not found in {path}")
    else:
        if len(entries) == 1:
            selected = entries[0]
        else:
            raise typer.BadParameter("wallet selector required (--from or --label)")

    secret_hex = str(selected.get("secret_key_hex") or selected.get("secretKeyHex") or "").strip()
    public_hex = str(selected.get("public_key_hex") or selected.get("publicKeyHex") or "").strip()
    sender = str(selected.get("address") or "").strip()
    if not sender:
        raise typer.BadParameter("selected wallet entry has no address")
    if not secret_hex or not public_hex:
        raise typer.BadParameter(
            "selected wallet entry does not contain secret/public key hex needed for signing"
        )

    alg_name = str(selected.get("alg_name") or selected.get("algName") or "").strip().lower()
    if not alg_name:
        alg_id = selected.get("alg_id") or selected.get("algId")
        try:
            alg_name = ALG_BY_ID.get(int(alg_id), "")
        except Exception:
            alg_name = ""
    if not alg_name:
        raise typer.BadParameter("selected wallet entry is missing supported alg_name/alg_id")
    if alg_override and alg_override.strip().lower() != alg_name:
        raise typer.BadParameter(
            f"wallet algorithm mismatch: wallet={alg_name}, override={alg_override}"
        )

    signer = PQSigner.from_keypair(
        alg_name=alg_name,
        secret_key=bytes.fromhex(secret_hex[2:] if secret_hex.startswith("0x") else secret_hex),
        public_key=bytes.fromhex(public_hex[2:] if public_hex.startswith("0x") else public_hex),
    )

    return SignerResolution(
        signer=signer,
        sender=sender,
        source=f"wallet:{selector or sender}@{path}",
        source_kind="wallet-store",
    )


def resolve_signer(
    *,
    from_value: Optional[str],
    label: Optional[str],
    wallet_file: Optional[Path] = None,
    alg_override: Optional[str] = None,
) -> SignerResolution:
    selector_from = from_value.strip() if from_value and from_value.strip() else None
    selector_label = label.strip() if label and label.strip() else None

    if selector_from and selector_label and selector_from != selector_label:
        raise typer.BadParameter("--from and --label refer to different wallets; choose one")

    selector = selector_label or selector_from
    if selector is None:
        raise typer.BadParameter("missing sender wallet; pass --from <label_or_address> or --label <wallet_label>")

    path = wallet_store_path(wallet_file)

    # Preferred path: reuse omni_sdk wallet loader when available.
    try:
        from omni_sdk.cli.wallet_loader import load_signer_from_wallet_source

        loaded = load_signer_from_wallet_source(
            path=path,
            label=(None if is_animica_address(selector) else selector),
            address=(selector if is_animica_address(selector) else None),
            alg_override=alg_override,
            strict_default_selection=False,
        )
        return SignerResolution(
            signer=loaded.signer,
            sender=loaded.sender,
            source=loaded.source,
            source_kind=loaded.source_kind,
        )
    except Exception:
        return _fallback_signer_from_wallet(
            path=path,
            label=(None if is_animica_address(selector) else selector),
            address=(selector if is_animica_address(selector) else None),
            alg_override=alg_override,
        )
