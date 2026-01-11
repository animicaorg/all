from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from omni_sdk.address import from_pubkey
from omni_sdk.wallet.signer import PQSigner, create_signer_from_keypair

DEFAULT_KDF_PARAMS = {
    "name": "scrypt",
    "n": 2**14,
    "r": 8,
    "p": 1,
    "length": 32,
}


class WalletStoreError(RuntimeError):
    pass


@dataclass
class AccountRecord:
    label: str
    address: str
    alg_id: int
    alg_name: str
    public_key_hex: str
    secret_key_hex: str
    created_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "address": self.address,
            "alg_id": self.alg_id,
            "alg_name": self.alg_name,
            "created_at": self.created_at,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "address": self.address,
            "alg_id": self.alg_id,
            "alg_name": self.alg_name,
            "public_key_hex": self.public_key_hex,
            "secret_key_hex": self.secret_key_hex,
            "created_at": self.created_at,
        }


@dataclass
class WalletVault:
    accounts: list[AccountRecord]
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "accounts": [acct.to_dict() for acct in self.accounts],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WalletVault":
        accounts: list[AccountRecord] = []
        for entry in payload.get("accounts", []):
            accounts.append(
                AccountRecord(
                    label=entry.get("label", ""),
                    address=entry["address"],
                    alg_id=int(entry["alg_id"]),
                    alg_name=entry["alg_name"],
                    public_key_hex=entry["public_key_hex"],
                    secret_key_hex=entry["secret_key_hex"],
                    created_at=entry["created_at"],
                )
            )
        created_at = payload.get("created_at") or _now_iso()
        updated_at = payload.get("updated_at") or created_at
        return cls(accounts=accounts, created_at=created_at, updated_at=updated_at)


class WalletStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._vault: WalletVault | None = None
        self._key: bytes | None = None
        self._kdf_params: dict[str, Any] | None = None

    @property
    def is_locked(self) -> bool:
        return self._vault is None or self._key is None

    @property
    def is_initialized(self) -> bool:
        return self._path.exists()

    def lock(self) -> None:
        self._vault = None
        self._key = None
        self._kdf_params = None

    def unlock(self, password: str) -> dict[str, Any]:
        if not password:
            raise WalletStoreError("Password is required.")
        if not self._path.exists():
            self._kdf_params = {
                **DEFAULT_KDF_PARAMS,
                "salt": base64.b64encode(os.urandom(16)).decode("utf-8"),
            }
            key = self._derive_key(password, self._kdf_params)
            vault = WalletVault(accounts=[], created_at=_now_iso(), updated_at=_now_iso())
            self._vault = vault
            self._key = key
            self._save()
            return {"initialized": True}

        payload = json.loads(self._path.read_text(encoding="utf-8"))
        self._kdf_params = _validate_kdf_payload(payload.get("kdf") or {})
        key = self._derive_key(password, self._kdf_params)
        try:
            vault_payload = _decrypt_payload(payload, key)
        except InvalidTag as exc:
            raise WalletStoreError("Invalid password or corrupted wallet.") from exc
        self._vault = WalletVault.from_dict(vault_payload)
        self._key = key
        return {"initialized": False}

    def list_accounts(self) -> list[dict[str, Any]]:
        vault = self._require_unlocked()
        return [acct.to_public_dict() for acct in vault.accounts]

    def create_account(self, label: str | None = None) -> dict[str, Any]:
        vault = self._require_unlocked()
        signer = PQSigner.from_seed("dilithium3")
        public = signer.public_key
        secret = _normalize_dilithium3_secret_key(signer.secret_key, signer.alg_name)
        address = signer.address or from_pubkey(public, signer.alg_id)
        entry = AccountRecord(
            label=label or f"Account {len(vault.accounts) + 1}",
            address=address,
            alg_id=signer.alg_id,
            alg_name=signer.alg_name,
            public_key_hex=public.hex(),
            secret_key_hex=secret.hex(),
            created_at=_now_iso(),
        )
        vault.accounts.append(entry)
        vault.updated_at = _now_iso()
        self._save()
        return entry.to_public_dict()

    def import_account(self, label: str | None, secret: str) -> dict[str, Any]:
        vault = self._require_unlocked()
        if not secret:
            raise WalletStoreError("Secret is required.")
        payload = _parse_secret_input(secret)
        secret_key_hex = payload["secret_key_hex"]
        public_key_hex = payload["public_key_hex"]
        alg_name = payload.get("alg_name") or "dilithium3"

        try:
            secret_key = bytes.fromhex(secret_key_hex)
            public_key = bytes.fromhex(public_key_hex)
        except ValueError as exc:
            raise WalletStoreError("Secret keys must be hex.") from exc
        secret_key = _normalize_dilithium3_secret_key(secret_key, alg_name)

        signer = create_signer_from_keypair(alg_name, secret_key, public_key)
        address = payload.get("address") or signer.address or from_pubkey(public_key, signer.alg_id)

        entry = AccountRecord(
            label=label or payload.get("label") or f"Account {len(vault.accounts) + 1}",
            address=address,
            alg_id=signer.alg_id,
            alg_name=signer.alg_name,
            public_key_hex=public_key.hex(),
            secret_key_hex=secret_key.hex(),
            created_at=payload.get("created_at") or _now_iso(),
        )
        vault.accounts.append(entry)
        vault.updated_at = _now_iso()
        self._save()
        return entry.to_public_dict()

    def export_account(self, address: str) -> dict[str, Any]:
        vault = self._require_unlocked()
        for acct in vault.accounts:
            if acct.address == address:
                return acct.to_dict()
        raise WalletStoreError("Account not found.")

    def _require_unlocked(self) -> WalletVault:
        if self._vault is None or self._key is None:
            raise WalletStoreError("Wallet is locked.")
        return self._vault

    def _derive_key(self, password: str, kdf_payload: dict[str, Any]) -> bytes:
        if kdf_payload.get("name") != "scrypt":
            raise WalletStoreError("Unsupported KDF.")
        salt_b64 = kdf_payload.get("salt")
        if not salt_b64:
            raise WalletStoreError("Missing KDF salt.")
        salt = base64.b64decode(salt_b64)
        kdf = Scrypt(
            salt=salt,
            length=int(kdf_payload.get("length", 32)),
            n=int(kdf_payload.get("n", DEFAULT_KDF_PARAMS["n"])),
            r=int(kdf_payload.get("r", DEFAULT_KDF_PARAMS["r"])),
            p=int(kdf_payload.get("p", DEFAULT_KDF_PARAMS["p"])),
        )
        return kdf.derive(password.encode("utf-8"))

    def _save(self) -> None:
        if self._vault is None or self._key is None or self._kdf_params is None:
            raise WalletStoreError("Wallet is locked.")
        payload = self._vault.to_dict()
        encrypted = _encrypt_payload(payload, self._key, self._kdf_params)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(encrypted, indent=2), encoding="utf-8")
        _ensure_strict_permissions(self._path)


def _normalize_dilithium3_secret_key(secret: bytes, alg_name: str) -> bytes:
    if alg_name != "dilithium3":
        return secret
    if len(secret) == 4032:
        return secret[:4000]
    return secret


def _parse_secret_input(secret: str) -> dict[str, Any]:
    raw = secret.strip()
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise WalletStoreError("Invalid secret JSON.") from exc
        return _filter_secret_payload(payload)

    if ":" in raw:
        parts = [part.strip() for part in raw.split(":") if part.strip()]
        if len(parts) < 2:
            raise WalletStoreError("Secret must include secret and public keys.")
        secret_key_hex = parts[0]
        public_key_hex = parts[1]
        alg_name = parts[2] if len(parts) > 2 else "dilithium3"
        return {
            "secret_key_hex": secret_key_hex,
            "public_key_hex": public_key_hex,
            "alg_name": alg_name,
        }

    raise WalletStoreError(
        "Secret must be JSON export or 'secret_key_hex:public_key_hex[:alg]'."
    )


def _filter_secret_payload(payload: dict[str, Any]) -> dict[str, Any]:
    data = {
        "label": payload.get("label"),
        "address": payload.get("address"),
        "alg_name": payload.get("alg_name"),
        "created_at": payload.get("created_at"),
        "secret_key_hex": payload.get("secret_key_hex"),
        "public_key_hex": payload.get("public_key_hex"),
    }
    missing = [key for key in ("secret_key_hex", "public_key_hex") if not data.get(key)]
    if missing:
        raise WalletStoreError("Secret JSON missing secret/public key.")
    return data


def _encrypt_payload(payload: dict[str, Any], key: bytes, kdf_payload: dict[str, Any]) -> dict[str, Any]:
    nonce = os.urandom(12)
    plaintext = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    cipher = AESGCM(key).encrypt(nonce, plaintext, None)
    return {
        "version": 1,
        "kdf": {
            **{k: v for k, v in kdf_payload.items() if k != "salt"},
            "salt": kdf_payload["salt"],
        },
        "cipher": {"name": "aes-256-gcm", "nonce": base64.b64encode(nonce).decode("utf-8")},
        "ciphertext": base64.b64encode(cipher).decode("utf-8"),
    }


def _decrypt_payload(payload: dict[str, Any], key: bytes) -> dict[str, Any]:
    cipher_info = payload.get("cipher") or {}
    if cipher_info.get("name") not in {"aes-256-gcm", "aesgcm"}:
        raise WalletStoreError("Unsupported cipher.")
    nonce_b64 = cipher_info.get("nonce")
    cipher_b64 = payload.get("ciphertext")
    if not nonce_b64 or not cipher_b64:
        raise WalletStoreError("Malformed vault ciphertext.")
    nonce = base64.b64decode(nonce_b64)
    cipher = base64.b64decode(cipher_b64)
    plaintext = AESGCM(key).decrypt(nonce, cipher, None)
    return json.loads(plaintext.decode("utf-8"))


def _validate_kdf_payload(payload: dict[str, Any]) -> dict[str, Any]:
    kdf_name = payload.get("name")
    if kdf_name != "scrypt":
        raise WalletStoreError("Unsupported KDF.")
    if not payload.get("salt"):
        raise WalletStoreError("Missing KDF salt.")
    return {
        "name": "scrypt",
        "salt": payload["salt"],
        "n": int(payload.get("n", DEFAULT_KDF_PARAMS["n"])),
        "r": int(payload.get("r", DEFAULT_KDF_PARAMS["r"])),
        "p": int(payload.get("p", DEFAULT_KDF_PARAMS["p"])),
        "length": int(payload.get("length", DEFAULT_KDF_PARAMS["length"])),
    }


def _ensure_strict_permissions(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except PermissionError:
        pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
