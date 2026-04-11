from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

import cbor2

from ..tx import send as tx_send
from ..tx.signing import resolve_signing_context, sign_transaction_for_submission


def _rpc_req(rpc, method: str, params=None):
    params = [] if params is None else params
    if rpc is None:
        return None
    out = None
    if hasattr(rpc, "request"):
        out = rpc.request(method, params)
    elif hasattr(rpc, "call"):
        out = rpc.call(method, params)
    else:
        raise TypeError("rpc object has neither request() nor call()")

    # Some clients return full JSON-RPC envelopes; normalize to the inner result.
    if isinstance(out, Mapping):
        if "error" in out and out["error"] is not None:
            err = out["error"]
            if isinstance(err, Mapping):
                message = err.get("message") or "rpc error"
                code = err.get("code")
                raise RuntimeError(f"rpc error {code}: {message}")
            raise RuntimeError(f"rpc error: {err}")
        if "result" in out:
            return out.get("result")
    return out


def _coerce_bytes(value: Any, *, field: str) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    if isinstance(value, str):
        s = value.strip()
        if s.startswith("0x"):
            return bytes.fromhex(s[2:])
        return s.encode()
    raise TypeError(f"{field} must be bytes-like, got {type(value).__name__}")


def _address_to_account_key(value: str | bytes | bytearray | memoryview) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        if len(raw) != 32:
            raise ValueError(
                f"from_addr account key must be 32 bytes, got {len(raw)}"
            )
        return raw

    if not isinstance(value, str):
        raise TypeError(
            f"from_addr must be a bech32 address, 0x-hex, or 32-byte key; got {type(value).__name__}"
        )

    text = value.strip()
    if text.startswith(("0x", "0X")):
        raw = bytes.fromhex(text[2:])
        if len(raw) != 32:
            raise ValueError(
                f"from_addr hex account key must be 32 bytes, got {len(raw)}"
            )
        return raw

    try:
        from pq.py.address import decode_address

        record = decode_address(text)
        digest = bytes(record.digest) if isinstance(record.digest, list) else bytes(record.digest)
        raw = digest[:32].ljust(32, b"\x00")
        if len(raw) == 32:
            return raw
    except Exception:
        pass

    try:
        from ..address import parse as parse_address

        parsed = parse_address(text)
        pubkey_hash = parsed.get("pubkey_hash")
        if isinstance(pubkey_hash, (bytes, bytearray, memoryview)):
            raw = bytes(pubkey_hash)
            if len(raw) == 32:
                return raw
    except Exception as exc:
        raise ValueError(f"invalid from_addr '{value}': {exc}") from exc

    raise ValueError(f"invalid from_addr '{value}'")


def _manifest_to_bytes(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        out = bytes(value)
        if not out:
            raise ValueError("manifest must not be empty")
        return out
    if isinstance(value, Mapping):
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("manifest must not be empty")
        return text.encode("utf-8")
    raise TypeError(f"manifest must be mapping/bytes/string, got {type(value).__name__}")


def _split_package_bytes(package_bytes: bytes) -> tuple[bytes, bytes]:
    package = cbor2.loads(package_bytes)
    if not isinstance(package, Mapping):
        raise ValueError("package bytes must decode to a CBOR map")
    code = _coerce_bytes(package.get("code"), field="code")
    manifest = _manifest_to_bytes(package.get("manifest"))
    if not code:
        raise ValueError("package code must not be empty")
    if not manifest:
        raise ValueError("package manifest must not be empty")
    return code, manifest


def _coerce_int(value: Any) -> int | None:
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


def _infer_chain_height(rpc) -> int | None:
    if rpc is None:
        return None

    for method in ("chain.getHead", "chain.getInfo", "node.status", "chain.getStatus"):
        try:
            out = _rpc_req(rpc, method, [])
        except Exception:
            continue

        if isinstance(out, Mapping):
            for key in (
                "height",
                "head_height",
                "best_height",
                "block_height",
                "bestHeight",
                "headHeight",
                "blockHeight",
                "number",
            ):
                value = _coerce_int(out.get(key))
                if value is not None:
                    return value

            head = out.get("head")
            if isinstance(head, Mapping):
                for key in ("height", "number", "head_height", "headHeight"):
                    value = _coerce_int(head.get(key))
                    if value is not None:
                        return value
        else:
            value = _coerce_int(out)
            if value is not None:
                return value

    return None


def _resolve_validity_window_from_rpc(
    rpc,
    *,
    valid_after: int | None,
    valid_until: int | None,
    ttl_blocks: int = 120,
) -> Tuple[int, int]:
    ttl = max(1, int(ttl_blocks))
    resolved_after = int(valid_after) if valid_after is not None else None
    resolved_until = int(valid_until) if valid_until is not None else None

    if resolved_after is None or resolved_until is None:
        inferred_height = _infer_chain_height(rpc)
        if inferred_height is None:
            raise RuntimeError(
                "could not infer chain height for deploy validity window; "
                "pass valid_after and valid_until explicitly"
            )
        if resolved_after is None:
            resolved_after = int(inferred_height)
        if resolved_until is None:
            resolved_until = int(resolved_after + ttl)

    if resolved_until <= resolved_after:
        resolved_until = resolved_after + 1

    return int(resolved_after), int(resolved_until)


def _resolve_v2_salt(salt: bytes | bytearray | memoryview | str | None) -> bytes:
    if salt is None:
        return os.urandom(16)
    resolved = _coerce_bytes(salt, field="salt")
    if not resolved:
        raise ValueError("salt must not be empty")
    return resolved


def _load_json(path_like: str | Path) -> Any:
    return json.loads(Path(path_like).read_text())


def make_package_bytes(
    *,
    manifest: str | Path | Mapping[str, Any],
    code: str | Path | bytes,
) -> bytes:
    manifest_obj = manifest if isinstance(manifest, Mapping) else _load_json(manifest)
    if isinstance(code, (str, Path)) and Path(code).exists():
        code_bytes = Path(code).read_bytes()
    elif isinstance(code, bytes):
        code_bytes = code
    else:
        code_bytes = str(code).encode()

    package = {
        "manifest": manifest_obj,
        "code": code_bytes,
    }
    return cbor2.dumps(package, canonical=True)


def build_deploy_tx(
    *,
    from_addr: str,
    chain_id: int,
    nonce: int = 0,
    max_fee: int = 1,
    package_bytes: bytes,
    manifest_obj: Mapping[str, Any] | None = None,
    code_bytes: bytes | None = None,
    gas_limit: int | None = None,
    valid_after: int | None = None,
    valid_until: int | None = None,
    salt: bytes | None = None,
) -> Dict[str, Any]:
    deploy_code = bytes(code_bytes) if code_bytes is not None else None
    deploy_manifest = _manifest_to_bytes(manifest_obj) if manifest_obj is not None else None
    if deploy_code is None or deploy_manifest is None:
        unpacked_code, unpacked_manifest = _split_package_bytes(package_bytes)
        deploy_code = unpacked_code if deploy_code is None else deploy_code
        deploy_manifest = unpacked_manifest if deploy_manifest is None else deploy_manifest

    if gas_limit is None:
        gas_limit = max(58353, len(deploy_code) * 20)

    use_v2_window = (
        valid_after is not None or valid_until is not None or salt is not None
    )
    if use_v2_window and (
        valid_after is None or valid_until is None or salt is None
    ):
        raise ValueError(
            "v2 deploy tx requires valid_after, valid_until, and salt together"
        )

    tx_version = 2 if use_v2_window else 1
    tx: Dict[str, Any] = {
        "v": int(tx_version),
        "chainId": int(chain_id),
        "from": _address_to_account_key(from_addr),
        "gas": {"price": int(max_fee), "limit": int(gas_limit)},
        "payload": {
            "t": 1,
            "v": {
                "code": deploy_code,
                "manifest": deploy_manifest,
            },
        },
        "accessList": [],
    }
    if use_v2_window:
        tx["validAfter"] = int(valid_after)
        tx["validUntil"] = int(valid_until)
        tx["salt"] = _coerce_bytes(salt, field="salt")
    else:
        tx["nonce"] = int(nonce)
    return tx


def _extract_contract_address(receipt: Any) -> str | None:
    if not isinstance(receipt, Mapping):
        return None
    for key in ("contractAddress", "contract_address"):
        value = receipt.get(key)
        if isinstance(value, str) and value:
            return value
    logs = receipt.get("logs")
    if isinstance(logs, list):
        for log in logs:
            if isinstance(log, Mapping):
                for key in ("contractAddress", "contract_address"):
                    value = log.get(key)
                    if isinstance(value, str) and value:
                        return value
    return None


def deploy_package(
    *,
    rpc,
    signer,
    manifest: str | Path | Mapping[str, Any],
    code: str | Path | bytes,
    chain_id: int,
    from_addr: str | None = None,
    nonce: int = 0,
    max_fee: int = 1,
    gas_limit: int | None = None,
    valid_after: int | None = None,
    valid_until: int | None = None,
    salt: bytes | None = None,
    timeout_s: float = 120.0,
    await_receipt: bool = True,
    receipt_timeout_s: float | None = None,
):
    sender = from_addr or getattr(signer, "address", None)
    if not sender:
        raise RuntimeError("deploy_package requires from_addr or signer.address")

    manifest_obj = manifest if isinstance(manifest, Mapping) else _load_json(manifest)
    if isinstance(code, (str, Path)) and Path(code).exists():
        code_blob = Path(code).read_bytes()
    elif isinstance(code, bytes):
        code_blob = bytes(code)
    else:
        code_blob = str(code).encode()
    package_bytes = make_package_bytes(manifest=manifest_obj, code=code_blob)
    resolved_valid_after, resolved_valid_until = _resolve_validity_window_from_rpc(
        rpc,
        valid_after=valid_after,
        valid_until=valid_until,
    )
    resolved_salt = _resolve_v2_salt(salt)

    tx = build_deploy_tx(
        from_addr=sender,
        chain_id=chain_id,
        nonce=nonce,
        max_fee=max_fee,
        package_bytes=package_bytes,
        manifest_obj=manifest_obj,
        code_bytes=code_blob,
        gas_limit=gas_limit,
        valid_after=resolved_valid_after,
        valid_until=resolved_valid_until,
        salt=resolved_salt,
    )

    ctx = resolve_signing_context(rpc, chain_id=int(chain_id))
    signed = sign_transaction_for_submission(tx, signer, context=ctx)
    raw = signed.raw_tx

    if await_receipt:
        wait_timeout = receipt_timeout_s if receipt_timeout_s is not None else timeout_s
        tx_hash = tx_send.submit_raw(rpc, raw)
        try:
            receipt = tx_send.wait_for_receipt(rpc, tx_hash, timeout_s=wait_timeout)
        except TimeoutError as exc:
            status = None
            status_error = None
            try:
                status = _rpc_req(rpc, "tx.getStatus", [tx_hash])
            except Exception as status_exc:  # noqa: BLE001
                status_error = str(status_exc)
            raise TimeoutError(
                "transaction accepted but receipt not indexed yet "
                f"(tx={tx_hash}, timeout_s={wait_timeout}, "
                f"tx_status={status!r}, tx_status_error={status_error!r})"
            ) from exc
        if isinstance(receipt, dict):
            receipt.setdefault("txHash", tx_hash)
    else:
        tx_hash = tx_send.submit_raw(rpc, raw)
        receipt = {"txHash": tx_hash}

    contract_address = _extract_contract_address(receipt)
    return contract_address, receipt
