from __future__ import annotations

import json
from typing import Any, Mapping, MutableMapping

from core.utils.address import address_to_bytes
from core.utils.hash import sha3_256

DEPLOYMENT_TYPE_PYTHON_VM_PACKAGE = "python_vm_package"
DEPLOY_ADDRESS_DOMAIN_V1 = b"animica/deploy/python_vm_package/v1"
ADDRESS_LEN = 32


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(obj, Mapping) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(text, 16) if text.startswith(("0x", "0X")) else int(text)
        except Exception:
            return None
    try:
        return int(value)
    except Exception:
        return None


def _as_bytes(value: Any) -> bytes | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith(("0x", "0X")):
            try:
                return bytes.fromhex(text[2:])
            except Exception:
                return None
        if text.startswith("anim") or text.startswith("system:"):
            try:
                return address_to_bytes(text)
            except Exception:
                return None
        if all(c in "0123456789abcdefABCDEF" for c in text):
            try:
                return bytes.fromhex(text)
            except Exception:
                return None
        return text.encode("utf-8")
    if isinstance(value, Mapping):
        try:
            return json.dumps(
                value, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        except Exception:
            return None
    return None


def _to_hex32(raw: bytes | None) -> str | None:
    if not raw:
        return None
    if len(raw) < ADDRESS_LEN:
        raw = raw.rjust(ADDRESS_LEN, b"\x00")
    if len(raw) > ADDRESS_LEN:
        raw = raw[-ADDRESS_LEN:]
    return "0x" + raw.hex()


def _normalize_address_bytes(value: Any) -> bytes | None:
    raw = _as_bytes(value)
    if raw is None:
        return None
    if len(raw) < ADDRESS_LEN:
        raw = raw.rjust(ADDRESS_LEN, b"\x00")
    if len(raw) > ADDRESS_LEN:
        raw = raw[-ADDRESS_LEN:]
    return raw


def _payload_value(tx: Any) -> Any:
    payload = _get(tx, "payload")
    if payload is None:
        payload = _get(_get(tx, "unsigned"), "payload")
    if payload is None:
        payload = _get(_get(tx, "tx"), "payload")
    if payload is None:
        payload = _get(_get(tx, "body"), "payload")
    if isinstance(payload, Mapping):
        inner = payload.get("v")
        if isinstance(inner, Mapping):
            return inner
    return payload


def _extract_deploy_code_and_manifest(tx: Any) -> tuple[bytes | None, bytes | None]:
    payload = _payload_value(tx)
    if isinstance(payload, Mapping):
        code = _as_bytes(payload.get("code"))
        manifest = _as_bytes(payload.get("manifest"))
        if code is not None or manifest is not None:
            return code, manifest

    code = _as_bytes(_get(tx, "code", "init_code", "bytecode"))
    manifest = _as_bytes(_get(tx, "manifest"))
    return code, manifest


def _extract_tx_kind(tx: Any) -> str | None:
    kind = _get(tx, "kind", "tx_kind", "type", "txType")
    if kind is None:
        kind = _get(_get(tx, "unsigned"), "kind")
    if kind is None:
        kind = _get(_get(tx, "tx"), "kind")
    if kind is None:
        kind = _get(_get(tx, "body"), "kind")

    if isinstance(kind, int):
        if kind == 1:
            return "deploy"
        if kind == 0:
            return "transfer"
        if kind == 2:
            return "call"
    if isinstance(kind, str):
        normalized = kind.strip().lower().replace("_", "").replace("-", "")
        if normalized in {"deploy", "contractcreate", "create"}:
            return "deploy"
        if normalized in {"transfer", "payment"}:
            return "transfer"
        if normalized in {"call", "invoke"}:
            return "call"
    return None


def _is_empty_to_value(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        trimmed = value.strip().lower()
        if trimmed == "" or trimmed == "0x" or trimmed == "0x0":
            return True
        if trimmed.startswith("0x"):
            return all(ch == "0" for ch in trimmed[2:])
        return False
    raw = _as_bytes(value)
    if raw is None:
        return False
    return not any(raw)


def is_python_vm_package_deploy_tx(tx: Any) -> bool:
    kind = _extract_tx_kind(tx)
    if kind == "deploy":
        return True

    code, manifest = _extract_deploy_code_and_manifest(tx)
    if code is not None and len(code) > 0:
        return True
    if manifest is not None and len(manifest) > 0:
        return True

    payload = _payload_value(tx)
    data = _as_bytes(_get(payload, "data", default=_get(tx, "data", "input")))
    to_value = _get(payload, "to", default=_get(tx, "to", "recipient"))
    return bool(data) and _is_empty_to_value(to_value)


def _extract_sender_bytes(tx: Any, sender: Any = None) -> bytes | None:
    if sender is not None:
        return _normalize_address_bytes(sender)

    value = _get(tx, "sender", "from", "from_addr", "frm")
    if value is None:
        value = _get(_get(tx, "unsigned"), "sender", "from", "from_addr", "frm")
    if value is None:
        value = _get(_get(tx, "tx"), "sender", "from", "from_addr", "frm")
    if value is None:
        value = _get(_get(tx, "body"), "sender", "from", "from_addr", "frm")
    return _normalize_address_bytes(value)


def _extract_chain_id(tx: Any, chain_id: Any = None) -> int:
    if chain_id is not None:
        parsed = _as_int(chain_id)
        return int(parsed or 0)
    value = _get(tx, "chain_id", "chainId")
    if value is None:
        value = _get(_get(tx, "unsigned"), "chain_id", "chainId")
    if value is None:
        value = _get(_get(tx, "tx"), "chain_id", "chainId")
    if value is None:
        value = _get(_get(tx, "body"), "chain_id", "chainId")
    parsed = _as_int(value)
    return int(parsed or 0)


def _extract_nonce_fields(tx: Any) -> tuple[int | None, int | None, int | None, bytes]:
    nonce = _as_int(_get(tx, "nonce"))
    valid_after = _as_int(_get(tx, "valid_after", "validAfter"))
    valid_until = _as_int(_get(tx, "valid_until", "validUntil"))
    salt = _as_bytes(_get(tx, "salt")) or b""

    unsigned = _get(tx, "unsigned")
    if unsigned is not None:
        if nonce is None:
            nonce = _as_int(_get(unsigned, "nonce"))
        if valid_after is None:
            valid_after = _as_int(_get(unsigned, "valid_after", "validAfter"))
        if valid_until is None:
            valid_until = _as_int(_get(unsigned, "valid_until", "validUntil"))
        if not salt:
            salt = _as_bytes(_get(unsigned, "salt")) or b""

    tx_obj = _get(tx, "tx")
    if tx_obj is not None:
        if nonce is None:
            nonce = _as_int(_get(tx_obj, "nonce"))
        if valid_after is None:
            valid_after = _as_int(_get(tx_obj, "valid_after", "validAfter"))
        if valid_until is None:
            valid_until = _as_int(_get(tx_obj, "valid_until", "validUntil"))
        if not salt:
            salt = _as_bytes(_get(tx_obj, "salt")) or b""

    return nonce, valid_after, valid_until, salt


def _u128be(value: int | None) -> bytes:
    v = max(0, int(value or 0))
    return v.to_bytes(16, "big", signed=False)


def derive_python_vm_package_address(
    *,
    sender: bytes,
    chain_id: int,
    code_hash: bytes,
    manifest_hash: bytes,
    nonce: int | None = None,
    valid_after: int | None = None,
    valid_until: int | None = None,
    salt: bytes = b"",
) -> bytes:
    """
    Deterministically derive created address for Python VM package deployments.
    """
    digest = sha3_256
    h = bytearray()
    h.extend(DEPLOY_ADDRESS_DOMAIN_V1)
    h.extend(_u128be(chain_id))
    h.extend(sender[-ADDRESS_LEN:].rjust(ADDRESS_LEN, b"\x00"))
    if nonce is not None:
        h.extend(b"\x01")
        h.extend(_u128be(nonce))
    else:
        h.extend(b"\x02")
        h.extend(_u128be(valid_after))
        h.extend(_u128be(valid_until))
        h.extend(len(salt).to_bytes(2, "big", signed=False))
        h.extend(salt)
    h.extend(code_hash)
    h.extend(manifest_hash)
    return digest(bytes(h))


def build_python_vm_package_deploy_metadata(
    tx: Any,
    *,
    tx_hash: str | bytes | None = None,
    sender: Any = None,
    block_hash: str | bytes | None = None,
    block_number: int | None = None,
    tx_index: int | None = None,
    status: Any = None,
    chain_id: int | None = None,
) -> dict[str, Any] | None:
    if not is_python_vm_package_deploy_tx(tx):
        return None

    sender_bytes = _extract_sender_bytes(tx, sender)
    if sender_bytes is None:
        return None

    code, manifest = _extract_deploy_code_and_manifest(tx)
    code_hash = sha3_256(code or b"")
    manifest_hash = sha3_256(manifest or b"")

    nonce, valid_after, valid_until, salt = _extract_nonce_fields(tx)
    resolved_chain_id = _extract_chain_id(tx, chain_id)
    created = derive_python_vm_package_address(
        sender=sender_bytes,
        chain_id=resolved_chain_id,
        code_hash=code_hash,
        manifest_hash=manifest_hash,
        nonce=nonce,
        valid_after=valid_after,
        valid_until=valid_until,
        salt=salt,
    )
    contract_address = _to_hex32(created)
    if contract_address is None:
        return None

    tx_hash_hex = _to_hex32(_as_bytes(tx_hash)) if not isinstance(tx_hash, str) else tx_hash
    block_hash_hex = _to_hex32(_as_bytes(block_hash)) if not isinstance(block_hash, str) else block_hash
    status_int = _as_int(status)

    out: dict[str, Any] = {
        "txHash": tx_hash_hex,
        "sender": _to_hex32(sender_bytes),
        "contractAddress": contract_address,
        "createdAddress": contract_address,
        "deploymentType": DEPLOYMENT_TYPE_PYTHON_VM_PACKAGE,
        "codeHash": "0x" + code_hash.hex(),
        "manifestHash": "0x" + manifest_hash.hex(),
        "blockHash": block_hash_hex,
        "blockNumber": int(block_number) if block_number is not None else None,
        "transactionIndex": int(tx_index) if tx_index is not None else None,
        "status": status_int if status_int is not None else status,
    }
    return {k: v for k, v in out.items() if v is not None}


def canonical_contract_address(metadata: Mapping[str, Any] | None) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get("contractAddress") or metadata.get("createdAddress")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def copy_deploy_metadata_fields(
    target: MutableMapping[str, Any],
    metadata: Mapping[str, Any] | None,
    *,
    include_created_alias: bool = True,
) -> None:
    if not isinstance(metadata, Mapping):
        return
    contract_address = canonical_contract_address(metadata)
    if contract_address:
        target["contractAddress"] = contract_address
        if include_created_alias:
            target["createdAddress"] = contract_address

    for key in ("deploymentType", "codeHash", "manifestHash", "sender"):
        value = metadata.get(key)
        if value is not None:
            target[key] = value

    if "status" in metadata and metadata.get("status") is not None and target.get("status") is None:
        target["status"] = metadata.get("status")
