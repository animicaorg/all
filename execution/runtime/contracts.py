"""
execution.runtime.contracts — adapter hook to vm_py (no-op until vm_py lands)

This module defines the contract execution entrypoints used by the dispatcher.
By default (feature-flag off or vm_py unavailable), both deploy and call
behave as *deterministic no-ops that REVERT*, while still charging intrinsic
gas and splitting base/tip fees like a normal transaction. This keeps the node
fully functional before the Python-VM is integrated.

Once vm_py is available, set either:
  - environment ANIMICA_ENABLE_VM_PY=1
  - or supply enable_vm_py=True to the functions

and the adapter will attempt to import vm_py lazily and route deploy/call.
(That code path is guarded and will continue to REVERT if imports fail.)
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, List, Mapping, Optional, Tuple

from ..errors import ExecError
from ..state.apply_balance import (
    InsufficientBalance,
    credit as state_credit,
    debit as state_debit,
)
from ..types.events import LogEvent
from ..types.result import ApplyResult
from ..types.status import TxStatus

if TYPE_CHECKING:
    from .env import BlockEnv, TxEnv


# --------------------------------------------------------------------------------------
# Utilities (kept local to avoid import tangles)
# --------------------------------------------------------------------------------------

DEFAULT_INTRINSIC_CALL = 21_000
DEFAULT_INTRINSIC_DEPLOY = 53_000
ADDRESS_LEN = 32
MAX_INTERCALL_DEPTH = 16


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        if isinstance(obj, Mapping) and n in obj:
            return obj[n]
        if hasattr(obj, n):
            return getattr(obj, n)
    return default


def _as_int(x: Any, *, default: int = 0) -> int:
    if x is None:
        return default
    if isinstance(x, int):
        return x
    if isinstance(x, (bytes, bytearray)):
        return int.from_bytes(x, "big", signed=False)
    if isinstance(x, str):
        s = x.strip()
        if s.startswith(("0x", "0X")):
            try:
                return int(s, 16)
            except ValueError:
                return default
        try:
            return int(s, 10)
        except ValueError:
            return default
    try:
        return int(x)
    except Exception:
        return default


def _as_bytes(x: Any, *, expect_len: Optional[int] = None) -> bytes:
    if x is None:
        out = b""
    elif isinstance(x, (bytes, bytearray)):
        out = bytes(x)
    elif isinstance(x, str):
        s = x.strip()
        if s.startswith(("0x", "0X")):
            s = s[2:]
        if len(s) % 2:
            s = "0" + s
        try:
            out = bytes.fromhex(s)
        except ValueError:
            out = b""
    else:
        try:
            i = int(x)
            if i < 0:
                i = 0
            length = max(1, (i.bit_length() + 7) // 8)
            out = i.to_bytes(length, "big")
        except Exception:
            out = b""
    if expect_len is not None:
        if len(out) > expect_len:
            out = out[-expect_len:]
        elif len(out) < expect_len:
            out = out.rjust(expect_len, b"\x00")
    return out


def _ensure_account(state: Any, addr: bytes) -> None:
    if hasattr(state, "ensure_account"):
        state.ensure_account(addr)  # type: ignore[attr-defined]
        return
    m = getattr(state, "balances", None)
    if isinstance(m, dict) and addr not in m:
        m[addr] = 0


def _get_balance(state: Any, addr: bytes) -> int:
    if hasattr(state, "get_balance"):
        return int(state.get_balance(addr))  # type: ignore[attr-defined]
    view = getattr(state, "view", None)
    if view is not None and hasattr(view, "get_balance"):
        return int(view.get_balance(addr))  # type: ignore[attr-defined]
    m = getattr(state, "balances", None)
    if isinstance(m, dict):
        return int(m.get(addr, 0))
    accounts = getattr(state, "accounts", None)
    if isinstance(accounts, dict) and addr in accounts:
        return int(getattr(accounts[addr], "balance", 0))
    raise ExecError("state does not expose a readable balance API")


def _set_balance(state: Any, addr: bytes, value: int) -> None:
    if hasattr(state, "set_balance"):
        state.set_balance(addr, int(value))  # type: ignore[attr-defined]
        return
    cur = _get_balance(state, addr)
    delta = int(value) - cur
    if delta == 0:
        return
    if delta > 0 and hasattr(state, "credit"):
        state.credit(addr, delta)  # type: ignore[attr-defined]
        return
    if delta < 0 and hasattr(state, "debit"):
        state.debit(addr, -delta)  # type: ignore[attr-defined]
        return
    m = getattr(state, "balances", None)
    if isinstance(m, dict):
        m[addr] = int(value)
        return
    accounts = getattr(state, "accounts", None)
    if isinstance(accounts, dict):
        acc = accounts.get(addr)
        if acc is None:

            class _Acc:
                balance: int = 0
                code_hash: bytes = b""

            accounts[addr] = _Acc()  # type: ignore[call-arg]
            setattr(accounts[addr], "balance", int(value))
        else:
            setattr(acc, "balance", int(value))
        return
    raise ExecError("state does not expose a writable balance API")



def _maybe_state_root(state: Any) -> bytes:
    for name in ("compute_state_root", "state_root", "merkle_root"):
        fn = getattr(state, name, None)
        if callable(fn):
            try:
                root = fn()
                return _as_bytes(root, expect_len=32)
            except Exception:
                pass
        val = getattr(state, name, None)
        if isinstance(val, (bytes, bytearray, str)):
            return _as_bytes(val, expect_len=32)
    return b"\x00" * 32


def _compute_fee_parts(
    base_price: int, gas_price: int, gas_used: int
) -> Tuple[int, int, int]:
    base_per_gas = max(0, int(base_price))
    gas_per_gas = max(0, int(gas_price))
    used = max(0, int(gas_used))
    if gas_per_gas < base_per_gas:
        raise ExecError(
            "gas_price below base_fee",
            code="FEE_TOO_LOW",
            data={
                "gas_price": str(gas_per_gas),
                "base_fee": str(base_per_gas),
            },
        )
    base_component = base_per_gas * used
    tip_component = (gas_per_gas - base_per_gas) * used
    total = base_component + tip_component
    return total, base_component, tip_component


def _tx_hash_hex(tx: Any) -> str | None:
    tx_hash_value = _get(tx, "hash", "tx_hash", "transactionHash")
    if isinstance(tx_hash_value, (bytes, bytearray)):
        raw = bytes(tx_hash_value)
        if len(raw) == 32:
            return "0x" + raw.hex()
    if isinstance(tx_hash_value, str) and tx_hash_value:
        return tx_hash_value if tx_hash_value.startswith("0x") else f"0x{tx_hash_value}"
    if hasattr(tx, "txid") and callable(getattr(tx, "txid")):
        try:
            txid = tx.txid()
            if isinstance(txid, (bytes, bytearray)):
                return "0x" + bytes(txid).hex()
        except Exception:
            pass
    if hasattr(tx, "hash") and callable(getattr(tx, "hash")):
        try:
            digest = tx.hash()
            if isinstance(digest, (bytes, bytearray)):
                return "0x" + bytes(digest).hex()
        except Exception:
            pass
    return None


def _extract_tx_nonce(tx: Any) -> int | None:
    unsigned = _get(tx, "unsigned")
    nonce = _get(unsigned, "nonce") if unsigned is not None else None
    if nonce is None:
        nonce = _get(tx, "nonce", "n")
    if nonce is None and isinstance(tx, Mapping):
        body = tx.get("body")
        if isinstance(body, Mapping):
            nonce = body.get("nonce")
    if nonce is None:
        return None
    return _as_int(nonce, default=0)


def _credit_balance(
    state: Any,
    addr: bytes,
    amount: int,
    *,
    reason: str,
    tx_hash: str | None,
    height: int | None,
) -> None:
    if amount < 0:
        raise ExecError("negative credit amount", code="NEGATIVE_AMOUNT")
    if amount == 0:
        return
    try:
        state_credit(
            state,
            addr,
            amount,
            reason=reason,
            tx_hash=tx_hash,
            height=height,
            callsite="execution.runtime.contracts._credit_balance",
        )
        return
    except Exception:
        cur = _get_balance(state, addr)
        _set_balance(state, addr, cur + amount)


def _debit_balance(
    state: Any,
    addr: bytes,
    amount: int,
    *,
    reason: str,
    tx_hash: str | None,
    height: int | None,
) -> None:
    if amount < 0:
        raise ExecError("negative debit amount", code="NEGATIVE_AMOUNT")
    if amount == 0:
        return
    try:
        state_debit(
            state,
            addr,
            amount,
            reason=reason,
            tx_hash=tx_hash,
            height=height,
            callsite="execution.runtime.contracts._debit_balance",
        )
        return
    except Exception:
        cur = _get_balance(state, addr)
        if cur < amount:
            raise InsufficientBalance(
                "insufficient balance",
                required=amount,
                available=cur,
                shortfall=amount - cur,
            )
        _set_balance(state, addr, cur - amount)


def _increment_nonce(state: Any, addr: bytes) -> None:
    get_nonce = getattr(state, "get_nonce", None)
    set_nonce = getattr(state, "set_nonce", None)
    if callable(get_nonce) and callable(set_nonce):
        current = int(get_nonce(addr))
        set_nonce(addr, current + 1)
        return
    inc_nonce = getattr(state, "increment_nonce", None)
    if callable(inc_nonce):
        inc_nonce(addr)


def _resolve_intrinsic_call(tx: Any, params: Optional[Any]) -> int:
    try:
        from ..gas.intrinsic import intrinsic_for_call  # type: ignore

        return int(intrinsic_for_call(tx, params))
    except Exception:
        pass
    try:
        from ..gas.table import get_gas_cost  # type: ignore

        v = get_gas_cost("call")
        if isinstance(v, int):
            return v
        if isinstance(v, Mapping) and "base" in v:
            return int(v["base"])
    except Exception:
        pass
    return DEFAULT_INTRINSIC_CALL


def _resolve_intrinsic_deploy(tx: Any, params: Optional[Any]) -> int:
    try:
        from ..gas.intrinsic import intrinsic_for_deploy  # type: ignore

        return int(intrinsic_for_deploy(tx, params))
    except Exception:
        pass
    try:
        from ..gas.table import get_gas_cost  # type: ignore

        v = get_gas_cost("deploy")
        if isinstance(v, int):
            return v
        if isinstance(v, Mapping) and "base" in v:
            return int(v["base"])
    except Exception:
        pass
    return DEFAULT_INTRINSIC_DEPLOY


def _settle_fees(
    state: Any,
    block_env: "BlockEnv",
    *,
    sender: bytes,
    gas_used: int,
    gas_price: int,
    base_price: int,
    tx_hash: str | None,
    block_height: int | None,
) -> Tuple[int, int, int]:
    total_fee, base_fee_part, tip_fee_part = _compute_fee_parts(
        base_price, gas_price, gas_used
    )

    _debit_balance(
        state,
        sender,
        total_fee,
        reason="BLOCK_APPLY_SENDER_TOTAL",
        tx_hash=tx_hash,
        height=block_height,
    )

    coinbase = _as_bytes(
        getattr(block_env, "coinbase", b"\x00" * ADDRESS_LEN), expect_len=ADDRESS_LEN
    )
    if tip_fee_part > 0 and any(coinbase):
        _ensure_account(state, coinbase)
        _credit_balance(
            state,
            coinbase,
            tip_fee_part,
            reason="BLOCK_APPLY_TIP",
            tx_hash=tx_hash,
            height=block_height,
        )

    treasury = getattr(block_env, "treasury", None)
    if base_fee_part > 0 and isinstance(treasury, (bytes, bytearray, str)):
        t_addr = _as_bytes(treasury, expect_len=ADDRESS_LEN)
        if any(t_addr):
            _ensure_account(state, t_addr)
            _credit_balance(
                state,
                t_addr,
                base_fee_part,
                reason="BLOCK_APPLY_BASE_FEE",
                tx_hash=tx_hash,
                height=block_height,
            )

    return total_fee, base_fee_part, tip_fee_part


# --------------------------------------------------------------------------------------
# VM-PY package helpers
# --------------------------------------------------------------------------------------


class ContractCallError(ExecError):
    """Raised when contract call/deploy data cannot be executed."""


class ContractNotFoundError(ContractCallError):
    """Raised when contract code is not found at target address."""


class InvalidCalldataError(ContractCallError):
    """Raised when calldata cannot be decoded against ABI."""


def _extract_payload_value(tx: Any) -> Any:
    payload = _get(tx, "payload")
    if payload is None:
        payload = _get(_get(tx, "unsigned"), "payload")
    if payload is None:
        payload = _get(_get(tx, "tx"), "payload")
    if payload is None and isinstance(tx, Mapping):
        body = tx.get("body")
        if isinstance(body, Mapping):
            payload = body.get("payload")
    if isinstance(payload, Mapping):
        inner = payload.get("v")
        if isinstance(inner, Mapping):
            return inner
    return payload


def _extract_deploy_payload(tx: Any) -> tuple[bytes, bytes]:
    payload = _extract_payload_value(tx)
    code = _as_bytes(_get(payload, "code", default=_get(tx, "code")))
    manifest = _as_bytes(_get(payload, "manifest", default=_get(tx, "manifest")))
    if not code or not manifest:
        raise ContractCallError(
            "deploy payload missing code/manifest",
            code="DEPLOY_PAYLOAD_INVALID",
        )
    return code, manifest


def _extract_call_payload(tx: Any) -> tuple[bytes, bytes]:
    payload = _extract_payload_value(tx)
    to_addr = _as_bytes(
        _get(payload, "to", default=_get(tx, "to", "recipient")),
        expect_len=ADDRESS_LEN,
    )
    data = _as_bytes(_get(payload, "data", default=_get(tx, "data", "input")))
    if not any(to_addr):
        raise ContractCallError("call payload missing contract address", code="CALL_TO_MISSING")
    if not data:
        raise InvalidCalldataError("call payload missing calldata", code="CALLDATA_EMPTY")
    return to_addr, data


def _manifest_object_from_bytes(manifest_bytes: bytes) -> Mapping[str, Any]:
    try:
        raw = json.loads(manifest_bytes.decode("utf-8"))
    except Exception as exc:
        raise ContractCallError(
            "manifest bytes are not valid JSON object",
            code="MANIFEST_INVALID",
            data={"error": str(exc)},
        ) from exc
    if not isinstance(raw, Mapping):
        raise ContractCallError("manifest must decode to JSON object", code="MANIFEST_INVALID")
    return dict(raw)


def _pack_contract_package(*, code: bytes, manifest_bytes: bytes) -> bytes:
    try:
        import cbor2

        manifest_obj = _manifest_object_from_bytes(manifest_bytes)
        return cbor2.dumps(
            {
                "manifest": manifest_obj,
                "code": bytes(code),
            },
            canonical=True,
        )
    except Exception as exc:
        raise ContractCallError(
            "failed to encode contract package",
            code="CONTRACT_PACKAGE_ENCODE_FAILED",
            data={"error": str(exc)},
        ) from exc


def _unpack_contract_package(code_blob: bytes) -> tuple[Mapping[str, Any], bytes]:
    if not code_blob:
        raise ContractNotFoundError("empty contract code", code="CONTRACT_NOT_FOUND")
    try:
        import cbor2

        decoded = cbor2.loads(code_blob)
    except Exception:
        raise ContractNotFoundError(
            "contract package metadata not found",
            code="CONTRACT_PACKAGE_MISSING",
        ) from None
    if not isinstance(decoded, Mapping):
        raise ContractCallError("contract package must decode to CBOR map", code="CONTRACT_PACKAGE_INVALID")

    manifest_raw = decoded.get("manifest")
    code_raw = decoded.get("code", b"")
    if isinstance(manifest_raw, Mapping):
        manifest_obj: Mapping[str, Any] = dict(manifest_raw)
    elif isinstance(manifest_raw, (bytes, bytearray)):
        manifest_obj = _manifest_object_from_bytes(bytes(manifest_raw))
    elif isinstance(manifest_raw, str):
        manifest_obj = _manifest_object_from_bytes(manifest_raw.encode("utf-8"))
    else:
        raise ContractCallError("contract package missing manifest", code="CONTRACT_PACKAGE_INVALID")

    code_bytes = _as_bytes(code_raw)
    return manifest_obj, code_bytes


def _derive_deploy_address(tx: Any, *, sender: bytes, chain_id: int) -> bytes:
    try:
        from core.utils.deploy_metadata import build_python_vm_package_deploy_metadata

        meta = build_python_vm_package_deploy_metadata(
            tx,
            sender=sender,
            chain_id=chain_id,
            status=1,
        )
    except Exception as exc:
        raise ContractCallError(
            "failed to derive contract address",
            code="DEPLOY_ADDRESS_DERIVE_FAILED",
            data={"error": str(exc)},
        ) from exc

    if not isinstance(meta, Mapping):
        raise ContractCallError("unable to derive contract address", code="DEPLOY_ADDRESS_DERIVE_FAILED")
    addr_hex = meta.get("contractAddress") or meta.get("createdAddress")
    if not isinstance(addr_hex, str) or not addr_hex:
        raise ContractCallError("unable to derive contract address", code="DEPLOY_ADDRESS_DERIVE_FAILED")
    out = _as_bytes(addr_hex, expect_len=ADDRESS_LEN)
    if not any(out):
        raise ContractCallError("derived contract address is invalid", code="DEPLOY_ADDRESS_DERIVE_FAILED")
    return out


def _decode_call_data(abi_obj: Any, data: bytes) -> tuple[str, List[Any]]:
    try:
        from omni_sdk.types import abi as abi_codec
    except Exception as exc:
        raise ContractCallError(
            "ABI codec unavailable for call decoding",
            code="ABI_CODEC_UNAVAILABLE",
            data={"error": str(exc)},
        ) from exc

    if not isinstance(data, (bytes, bytearray)) or len(data) < 8:
        raise InvalidCalldataError("calldata too short for selector", code="CALLDATA_INVALID")

    try:
        normalized = abi_codec.normalize_abi(_abi_entries_for_codec(abi_obj))  # type: ignore[attr-defined]
    except Exception as exc:
        raise ContractCallError(
            "manifest ABI is invalid",
            code="MANIFEST_ABI_INVALID",
            data={"error": str(exc)},
        ) from exc

    functions = normalized.get("functions", {})
    if not isinstance(functions, Mapping):
        raise ContractCallError("manifest ABI has malformed functions", code="MANIFEST_ABI_INVALID")

    selector = bytes(data[:8])
    for fn_name, fn_def in functions.items():
        if not isinstance(fn_name, str) or not isinstance(fn_def, Mapping):
            continue
        try:
            fn_selector = abi_codec.function_selector(fn_def)  # type: ignore[attr-defined]
        except Exception:
            continue
        if fn_selector != selector:
            continue

        inputs = fn_def.get("inputs", [])
        if not isinstance(inputs, list):
            raise ContractCallError("ABI function inputs malformed", code="MANIFEST_ABI_INVALID")
        try:
            arg_count, offset = abi_codec._uvarint_decode(data, 8)  # type: ignore[attr-defined]
        except Exception as exc:
            raise InvalidCalldataError(
                "failed to decode calldata arg-count",
                code="CALLDATA_INVALID",
                data={"error": str(exc)},
            ) from exc
        if int(arg_count) != len(inputs):
            raise InvalidCalldataError(
                "calldata arg-count does not match ABI",
                code="CALLDATA_INVALID",
                data={"expected": len(inputs), "got": int(arg_count)},
            )

        values: List[Any] = []
        for param in inputs:
            if not isinstance(param, Mapping):
                raise ContractCallError("ABI function input malformed", code="MANIFEST_ABI_INVALID")
            typ = param.get("type")
            if not isinstance(typ, str):
                raise ContractCallError("ABI function input type missing", code="MANIFEST_ABI_INVALID")
            try:
                desc = abi_codec._parse_type(typ)  # type: ignore[attr-defined]
                value, offset = abi_codec._decode_desc(data, desc, offset)  # type: ignore[attr-defined]
            except Exception as exc:
                raise InvalidCalldataError(
                    "failed to decode calldata argument",
                    code="CALLDATA_INVALID",
                    data={"function": fn_name, "type": typ, "error": str(exc)},
                ) from exc
            values.append(value)

        if offset != len(data):
            raise InvalidCalldataError("calldata has trailing bytes", code="CALLDATA_INVALID")
        return fn_name, values

    raise InvalidCalldataError("unknown method selector", code="CALL_SELECTOR_UNKNOWN")


def _encode_return_data(abi_obj: Any, fn_name: str, value: Any) -> bytes:
    try:
        from omni_sdk.types import abi as abi_codec
    except Exception as exc:
        raise ContractCallError(
            "ABI codec unavailable for return encoding",
            code="ABI_CODEC_UNAVAILABLE",
            data={"error": str(exc)},
        ) from exc

    normalized = abi_codec.normalize_abi(_abi_entries_for_codec(abi_obj))  # type: ignore[attr-defined]
    fn_def = normalized.get("functions", {}).get(fn_name)
    if not isinstance(fn_def, Mapping):
        raise ContractCallError("function not found in ABI", code="MANIFEST_ABI_INVALID", data={"function": fn_name})
    outputs = fn_def.get("outputs", [])
    if not isinstance(outputs, list):
        raise ContractCallError("ABI outputs malformed", code="MANIFEST_ABI_INVALID")
    if len(outputs) == 0:
        return b""

    encoded: List[bytes] = []
    encoded.append(abi_codec._uvarint_encode(len(outputs)))  # type: ignore[attr-defined]
    if len(outputs) == 1:
        values = [value]
    else:
        if not isinstance(value, (list, tuple)) or len(value) != len(outputs):
            raise ContractCallError(
                "return value count does not match ABI outputs",
                code="RETURN_ENCODING_FAILED",
                data={"expected": len(outputs)},
            )
        values = list(value)

    for out_item, out_value in zip(outputs, values):
        if not isinstance(out_item, Mapping):
            raise ContractCallError("ABI output malformed", code="MANIFEST_ABI_INVALID")
        typ = out_item.get("type")
        if not isinstance(typ, str):
            raise ContractCallError("ABI output type missing", code="MANIFEST_ABI_INVALID")
        try:
            desc = abi_codec._parse_type(typ)  # type: ignore[attr-defined]
            encoded.append(abi_codec._encode_desc(out_value, desc))  # type: ignore[attr-defined]
        except Exception as exc:
            raise ContractCallError(
                "failed to encode return value",
                code="RETURN_ENCODING_FAILED",
                data={"type": typ, "error": str(exc)},
            ) from exc
    return b"".join(encoded)


def _abi_entries_for_codec(abi_obj: Any) -> Any:
    """
    Accept both entry-list ABI and manifest-style ABI maps.

    Runtime manifests commonly store ABI as:
      {"functions": [...], "events": [...]}
    while omni_sdk.types.abi expects a list of entries with explicit "type".
    """
    if not isinstance(abi_obj, Mapping):
        return abi_obj
    if {"entries", "functions", "events"} <= set(abi_obj.keys()):
        return abi_obj

    entries: List[dict[str, Any]] = []

    functions = abi_obj.get("functions")
    if isinstance(functions, list):
        for fn in functions:
            if isinstance(fn, Mapping):
                item = dict(fn)
                item.setdefault("type", "function")
                entries.append(item)
    elif isinstance(functions, Mapping):
        for fn_name, fn in functions.items():
            if isinstance(fn_name, str) and isinstance(fn, Mapping):
                item = dict(fn)
                item.setdefault("name", fn_name)
                item.setdefault("type", "function")
                entries.append(item)

    events = abi_obj.get("events")
    if isinstance(events, list):
        for ev in events:
            if isinstance(ev, Mapping):
                item = dict(ev)
                item.setdefault("type", "event")
                entries.append(item)
    elif isinstance(events, Mapping):
        for ev_name, ev in events.items():
            if isinstance(ev_name, str) and isinstance(ev, Mapping):
                item = dict(ev)
                item.setdefault("name", ev_name)
                item.setdefault("type", "event")
                entries.append(item)

    return entries if entries else abi_obj


def _looks_like_inline_source(text: str) -> bool:
    stripped = text.lstrip()
    if "\n" in text:
        return True
    inline_prefixes = (
        "def ",
        "from ",
        "import ",
        "class ",
        "if ",
        "#",
        '"""',
        "'''",
        "@",
    )
    return stripped.startswith(inline_prefixes)


def _source_text_from_code_bytes(code_bytes: bytes) -> str | None:
    if not code_bytes:
        return None
    try:
        text = code_bytes.decode("utf-8")
    except Exception:
        return None
    if not text.strip():
        return None
    if "\x00" in text:
        return None
    if _looks_like_inline_source(text):
        return text
    return None


def _manifest_for_runtime_execution(
    manifest_obj: Mapping[str, Any],
    code_bytes: bytes,
) -> Mapping[str, Any]:
    """
    Build a VM runtime manifest that can execute deterministically from chain state.

    Some deployments carry path-based source metadata that cannot be resolved from
    an on-chain context. When source text is available in package code bytes, use
    it as inline source for execution.
    """
    runtime_manifest = dict(manifest_obj)
    source_value = runtime_manifest.get("source")
    if isinstance(source_value, str) and _looks_like_inline_source(source_value):
        # Wrap inline source to bypass path probing in manifest resolution.
        runtime_manifest["source"] = {"inline": source_value}
        return runtime_manifest

    inline_source = _source_text_from_code_bytes(code_bytes)
    if inline_source is None:
        return runtime_manifest

    runtime_manifest["source"] = {"inline": inline_source}
    return runtime_manifest


class _StateStoragePatch:
    def __init__(self, state: Any, contract_addr: bytes, *, read_only: bool) -> None:
        self._state = state
        self._contract_addr = contract_addr
        self._read_only = bool(read_only)
        self._overlay: dict[bytes, bytes | None] = {}
        self._stdlib_storage: Any = None
        self._saved: dict[str, Any] = {}

    def __enter__(self) -> "_StateStoragePatch":
        from stdlib import storage as std_storage  # type: ignore

        self._stdlib_storage = std_storage
        for name in ("get", "set", "delete", "reset_backend"):
            self._saved[name] = getattr(std_storage, name, None)

        def _get_value(key: Any, default: Any = None) -> bytes:
            k = _as_bytes(key)
            if k in self._overlay:
                val = self._overlay[k]
                if val is None:
                    return _as_bytes(default) if default is not None else b""
                return bytes(val)

            getter = getattr(self._state, "get_storage", None)
            if callable(getter):
                stored = getter(self._contract_addr, k)
                if stored is None:
                    return _as_bytes(default) if default is not None else b""
                return _as_bytes(stored)
            return _as_bytes(default) if default is not None else b""

        def _set_value(key: Any, value: Any) -> None:
            k = _as_bytes(key)
            v = _as_bytes(value)
            if self._read_only:
                self._overlay[k] = v
                return
            setter = getattr(self._state, "set_storage", None)
            if callable(setter):
                setter(self._contract_addr, k, v)
                return
            raise ContractCallError("state backend does not expose set_storage", code="STATE_STORAGE_UNAVAILABLE")

        def _delete_value(key: Any) -> None:
            k = _as_bytes(key)
            if self._read_only:
                self._overlay[k] = None
                return
            deleter = getattr(self._state, "delete_storage", None)
            if callable(deleter):
                deleter(self._contract_addr, k)
                return
            self._overlay[k] = None

        setattr(std_storage, "get", _get_value)
        setattr(std_storage, "set", _set_value)
        setattr(std_storage, "delete", _delete_value)
        setattr(std_storage, "reset_backend", lambda: None)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._stdlib_storage is None:
            return
        for name, previous in self._saved.items():
            if previous is None:
                try:
                    delattr(self._stdlib_storage, name)
                except Exception:
                    continue
            else:
                setattr(self._stdlib_storage, name, previous)


def _execute_contract_method(
    *,
    state: Any,
    contract_addr: bytes,
    manifest_obj: Mapping[str, Any],
    code_bytes: bytes,
    method_name: str,
    method_args: List[Any],
    read_only: bool,
    sender: bytes,
    origin: bytes,
    call_value: int,
    chain_id: int,
    tx_hash: bytes,
    block_height: int,
    block_timestamp: int,
    depth: int,
    max_depth: int = MAX_INTERCALL_DEPTH,
) -> Any:
    try:
        from vm_py.runtime.loader import run_call as vm_run_call  # type: ignore
        from vm_py.runtime import abi as vm_abi  # type: ignore
        try:
            from vm_py.runtime import sandbox as vm_sandbox  # type: ignore

            install_proxy = getattr(vm_sandbox, "install_stdlib_proxy", None)
            if callable(install_proxy):
                install_proxy(overwrite=True)
        except Exception:
            pass
    except Exception as exc:
        raise ContractCallError(
            "vm_py runtime unavailable for contract execution",
            code="VM_RUNTIME_UNAVAILABLE",
            data={"error": str(exc)},
        ) from exc

    if depth < 0:
        raise ContractCallError("invalid call depth", code="CALL_DEPTH_INVALID")
    if depth > max_depth:
        raise ContractCallError(
            "maximum call depth exceeded",
            code="CALL_DEPTH_EXCEEDED",
            data={"depth": depth, "max_depth": max_depth},
        )

    runtime_manifest = _manifest_for_runtime_execution(manifest_obj, code_bytes)

    def _inter_contract_call(
        target: bytes,
        target_method: str,
        target_args: Any,
        value_amount: int,
        force_read_only: bool,
    ) -> Any:
        nested_read_only = bool(read_only or force_read_only)
        nested_depth = depth + 1
        if nested_depth > max_depth:
            raise ContractCallError(
                "maximum call depth exceeded",
                code="CALL_DEPTH_EXCEEDED",
                data={"depth": nested_depth, "max_depth": max_depth},
            )
        if value_amount < 0:
            raise ContractCallError("negative call value", code="CALL_VALUE_INVALID")
        if nested_read_only and value_amount:
            raise ContractCallError(
                "cannot transfer value in read-only call",
                code="CALL_VALUE_READ_ONLY",
            )

        target_addr = _as_bytes(target, expect_len=ADDRESS_LEN)
        if not any(target_addr):
            raise ContractCallError("inter-contract call missing target", code="CALL_TO_MISSING")
        manifest_child, code_child = _load_contract_from_state(state, target_addr)

        call_args: List[Any]
        if target_args is None:
            call_args = []
        elif isinstance(target_args, list):
            call_args = list(target_args)
        elif isinstance(target_args, tuple):
            call_args = list(target_args)
        else:
            call_args = [target_args]

        snap = _take_state_snapshot(state)
        try:
            if value_amount > 0:
                # Move native value atomically with child-call state; snapshot will
                # roll this back if execution fails.
                _debit_balance(
                    state,
                    contract_addr,
                    value_amount,
                    reason="CONTRACT_INTERNAL_CALL_VALUE_DEBIT",
                    tx_hash=None,
                    height=block_height or None,
                )
                _credit_balance(
                    state,
                    target_addr,
                    value_amount,
                    reason="CONTRACT_INTERNAL_CALL_VALUE_CREDIT",
                    tx_hash=None,
                    height=block_height or None,
                )

            return _execute_contract_method(
                state=state,
                contract_addr=target_addr,
                manifest_obj=manifest_child,
                code_bytes=code_child,
                method_name=target_method,
                method_args=call_args,
                read_only=nested_read_only,
                sender=contract_addr,
                origin=origin,
                call_value=value_amount,
                chain_id=chain_id,
                tx_hash=tx_hash,
                block_height=block_height,
                block_timestamp=block_timestamp,
                depth=nested_depth,
                max_depth=max_depth,
            )
        except Exception:
            _revert_state_snapshot(state, snap)
            raise

    ctx = vm_abi.CallContext(
        sender=bytes(sender),
        origin=bytes(origin),
        contract=bytes(contract_addr),
        value=max(0, int(call_value)),
        chain_id=max(0, int(chain_id)),
        tx_hash=bytes(tx_hash),
        block_height=max(0, int(block_height)),
        block_timestamp=max(0, int(block_timestamp)),
        depth=max(0, int(depth)),
        read_only=bool(read_only),
    )

    try:
        with vm_abi.runtime_context(ctx):
            vm_abi.push_call_hook(_inter_contract_call)
            try:
                with _StateStoragePatch(state, contract_addr, read_only=read_only):
                    out = vm_run_call(dict(runtime_manifest), method_name, list(method_args))
            finally:
                vm_abi.pop_call_hook()
    except Exception as exc:
        raise ContractCallError(
            "contract execution failed",
            code="CALL_EXECUTION_FAILED",
            data={"error": str(exc)},
        ) from exc
    if isinstance(out, Mapping):
        return out.get("result")
    return out


def _take_state_snapshot(state: Any) -> Any:
    snap_fn = getattr(state, "snapshot", None)
    if callable(snap_fn):
        try:
            return snap_fn()
        except Exception:
            return None
    return None


def _revert_state_snapshot(state: Any, snap: Any) -> None:
    if snap is None:
        return
    revert_fn = getattr(state, "revert", None)
    if callable(revert_fn):
        revert_fn(snap)


def _load_contract_from_state(state: Any, contract_addr: bytes) -> tuple[Mapping[str, Any], bytes]:
    get_code = getattr(state, "get_code", None)
    if not callable(get_code):
        raise ContractCallError("state backend does not expose get_code", code="STATE_CODE_UNAVAILABLE")
    code_blob = get_code(contract_addr)
    if not isinstance(code_blob, (bytes, bytearray)) or len(code_blob) == 0:
        raise ContractNotFoundError("contract code not found", code="CONTRACT_NOT_FOUND")
    return _unpack_contract_package(bytes(code_blob))


def simulate_call(
    *,
    state: Any,
    contract_addr: bytes,
    calldata: bytes,
    sender: bytes | None = None,
) -> bytes:
    manifest_obj, code_bytes = _load_contract_from_state(state, contract_addr)
    abi_obj = manifest_obj.get("abi")
    method_name, method_args = _decode_call_data(abi_obj, calldata)
    sender_addr = _as_bytes(sender, expect_len=ADDRESS_LEN) if sender is not None else b"\x00" * ADDRESS_LEN
    result = _execute_contract_method(
        state=state,
        contract_addr=contract_addr,
        manifest_obj=manifest_obj,
        code_bytes=code_bytes,
        method_name=method_name,
        method_args=method_args,
        read_only=True,
        sender=sender_addr,
        origin=sender_addr,
        call_value=0,
        chain_id=0,
        tx_hash=b"",
        block_height=0,
        block_timestamp=0,
        depth=0,
    )
    return _encode_return_data(abi_obj, method_name, result)


def _apply_deploy_vm(state: Any, tx: Any, sender: bytes, chain_id: int) -> tuple[bytes, str]:
    code, manifest = _extract_deploy_payload(tx)
    contract_addr = _derive_deploy_address(tx, sender=sender, chain_id=chain_id)
    set_code = getattr(state, "set_code", None)
    if not callable(set_code):
        raise ContractCallError("state backend does not expose set_code", code="STATE_CODE_UNAVAILABLE")
    package_blob = _pack_contract_package(code=code, manifest_bytes=manifest)
    set_code(contract_addr, package_blob)
    return contract_addr, "0x" + contract_addr.hex()


def _apply_call_vm(
    state: Any,
    tx: Any,
    sender: bytes,
    *,
    chain_id: int,
    tx_hash: bytes,
    block_height: int,
    block_timestamp: int,
) -> Any:
    contract_addr, calldata = _extract_call_payload(tx)
    manifest_obj, code_bytes = _load_contract_from_state(state, contract_addr)
    abi_obj = manifest_obj.get("abi")
    method_name, method_args = _decode_call_data(abi_obj, calldata)
    payload = _extract_payload_value(tx)
    call_value = _as_int(
        _get(payload, "value", default=_get(tx, "value", "amount")),
        default=0,
    )
    return _execute_contract_method(
        state=state,
        contract_addr=contract_addr,
        manifest_obj=manifest_obj,
        code_bytes=code_bytes,
        method_name=method_name,
        method_args=method_args,
        read_only=False,
        sender=sender,
        origin=sender,
        call_value=max(0, int(call_value)),
        chain_id=max(0, int(chain_id)),
        tx_hash=bytes(tx_hash),
        block_height=max(0, int(block_height)),
        block_timestamp=max(0, int(block_timestamp)),
        depth=0,
    )


# --------------------------------------------------------------------------------------
# Public API — Deploy & Call
# --------------------------------------------------------------------------------------


def apply_deploy(
    tx: Any,
    state: Any,
    block_env: "BlockEnv",
    tx_env: "TxEnv",
    *,
    params: Optional[Any] = None,
    enable_vm_py: Optional[bool] = None,
) -> ApplyResult:
    """
    Deploy a contract package (manifest + code). Until vm_py is enabled/available,
    this deterministically REVERTs while charging intrinsic gas and fees.
    """
    intrinsic = _resolve_intrinsic_deploy(tx, params)
    gas_limit = _as_int(_get(tx, "gas", "gas_limit", "gasLimit"), default=0)

    # OOG check (intrinsic)
    if gas_limit and intrinsic > gas_limit:
        return ApplyResult(
            status=TxStatus.OOG,
            gas_used=max(0, gas_limit),
            logs=[],
            state_root=_maybe_state_root(state),
            receipt=None,
        )

    # Fees: debit sender, pay tip/treasury
    sender = _as_bytes(getattr(tx_env, "sender", None), expect_len=ADDRESS_LEN)
    if len(sender) != ADDRESS_LEN:
        raise ExecError(f"TxEnv.sender must be {ADDRESS_LEN} bytes")
    tx_nonce = _extract_tx_nonce(tx)
    if tx_nonce is not None:
        get_nonce = getattr(state, "get_nonce", None)
        if callable(get_nonce):
            expected_nonce = int(get_nonce(sender))
            if int(tx_nonce) != expected_nonce:
                raise ExecError(
                    "nonce mismatch",
                    code="NONCE_MISMATCH",
                    data={"expected": expected_nonce, "got": int(tx_nonce)},
                )

    gas_price = _as_int(getattr(tx_env, "gas_price", 0))
    base_price = _as_int(getattr(tx_env, "base_price", 0))
    tx_hash_hex = _tx_hash_hex(tx)
    block_height = _as_int(getattr(block_env, "height", None), default=0) or None

    _ensure_account(state, sender)
    _settle_fees(
        state,
        block_env,
        sender=sender,
        gas_used=intrinsic,
        gas_price=gas_price,
        base_price=base_price,
        tx_hash=tx_hash_hex,
        block_height=block_height,
    )

    try:
        contract_addr, contract_hex = _apply_deploy_vm(
            state=state,
            tx=tx,
            sender=sender,
            chain_id=_as_int(getattr(block_env, "chain_id", 0)),
        )
        _increment_nonce(state, sender)
        receipt = {
            "status": "SUCCESS",
            "contractAddress": contract_hex,
            "createdAddress": contract_hex,
        }
        return ApplyResult(
            status=TxStatus.SUCCESS,
            gas_used=intrinsic,
            logs=[],
            state_root=_maybe_state_root(state),
            receipt=receipt,
        )
    except Exception as exc:
        if os.getenv("ANIMICA_ENABLE_VM_PY") == "1" or enable_vm_py is True:
            # In explicit VM mode, surface failures via deterministic revert log.
            pass

    # Deterministic no-op: REVERT with a diagnostic log
    logs: List[LogEvent] = [
        LogEvent(
            address=locals().get("contract_addr", b"\x00" * ADDRESS_LEN),
            topics=[b"vm.disabled", b"deploy"],
            # ANM-L02: never hash freeform exception-message text (varies by
            # Python version/platform/locale) into consensus logs. Use the
            # deterministic exception class name as a fixed error code.
            data=(b"err:" + type(exc).__name__.encode("ascii", "replace"))
            if "exc" in locals()
            else b"",
        )
    ]
    _increment_nonce(state, sender)

    return ApplyResult(
        status=TxStatus.REVERT,
        gas_used=intrinsic,
        logs=logs,
        state_root=_maybe_state_root(state),
        receipt=None,
    )


def apply_call(
    tx: Any,
    state: Any,
    block_env: "BlockEnv",
    tx_env: "TxEnv",
    *,
    params: Optional[Any] = None,
    enable_vm_py: Optional[bool] = None,
) -> ApplyResult:
    """
    Call a deployed contract. Until vm_py is enabled/available, this REVERTs while
    charging intrinsic gas and fees. Value attached to the call is not transferred
    on REVERT (consistent with rollback semantics).
    """
    intrinsic = _resolve_intrinsic_call(tx, params)
    gas_limit = _as_int(_get(tx, "gas", "gas_limit", "gasLimit"), default=0)

    # OOG check (intrinsic)
    if gas_limit and intrinsic > gas_limit:
        return ApplyResult(
            status=TxStatus.OOG,
            gas_used=max(0, gas_limit),
            logs=[],
            state_root=_maybe_state_root(state),
            receipt=None,
        )

    # Fees: debit sender, pay tip/treasury
    sender = _as_bytes(getattr(tx_env, "sender", None), expect_len=ADDRESS_LEN)
    if len(sender) != ADDRESS_LEN:
        raise ExecError(f"TxEnv.sender must be {ADDRESS_LEN} bytes")
    tx_nonce = _extract_tx_nonce(tx)
    if tx_nonce is not None:
        get_nonce = getattr(state, "get_nonce", None)
        if callable(get_nonce):
            expected_nonce = int(get_nonce(sender))
            if int(tx_nonce) != expected_nonce:
                raise ExecError(
                    "nonce mismatch",
                    code="NONCE_MISMATCH",
                    data={"expected": expected_nonce, "got": int(tx_nonce)},
                )

    gas_price = _as_int(getattr(tx_env, "gas_price", 0))
    base_price = _as_int(getattr(tx_env, "base_price", 0))
    tx_hash_hex = _tx_hash_hex(tx)
    block_height = _as_int(getattr(block_env, "height", None), default=0) or None

    _ensure_account(state, sender)
    _settle_fees(
        state,
        block_env,
        sender=sender,
        gas_used=intrinsic,
        gas_price=gas_price,
        base_price=base_price,
        tx_hash=tx_hash_hex,
        block_height=block_height,
    )

    state_snap = _take_state_snapshot(state)
    try:
        _apply_call_vm(
            state=state,
            tx=tx,
            sender=sender,
            chain_id=_as_int(getattr(block_env, "chain_id", 0)),
            tx_hash=_as_bytes(tx_hash_hex or "", expect_len=32)
            if tx_hash_hex
            else b"",
            block_height=_as_int(getattr(block_env, "height", 0)),
            block_timestamp=_as_int(getattr(block_env, "timestamp", 0)),
        )
        _increment_nonce(state, sender)
        return ApplyResult(
            status=TxStatus.SUCCESS,
            gas_used=intrinsic,
            logs=[],
            state_root=_maybe_state_root(state),
            receipt={"status": "SUCCESS"},
        )
    except Exception as exc:
        _revert_state_snapshot(state, state_snap)
        if os.getenv("ANIMICA_ENABLE_VM_PY") == "1" or enable_vm_py is True:
            pass

    # Deterministic no-op: REVERT with diagnostic log tagged with recipient
    to = _as_bytes(
        _get(_extract_payload_value(tx), "to", default=_get(tx, "to", "recipient")),
        expect_len=ADDRESS_LEN,
    )
    logs: List[LogEvent] = [
        LogEvent(
            address=to if any(to) else b"\x00" * ADDRESS_LEN,
            topics=[b"vm.disabled", b"call"],
            # ANM-L02: never hash freeform exception-message text (varies by
            # Python version/platform/locale) into consensus logs. Use the
            # deterministic exception class name as a fixed error code.
            data=(b"err:" + type(exc).__name__.encode("ascii", "replace"))
            if "exc" in locals()
            else b"",
        )
    ]
    _increment_nonce(state, sender)

    return ApplyResult(
        status=TxStatus.REVERT,
        gas_used=intrinsic,
        logs=logs,
        state_root=_maybe_state_root(state),
        receipt=None,
    )


__all__ = [
    "ContractCallError",
    "ContractNotFoundError",
    "InvalidCalldataError",
    "simulate_call",
    "apply_deploy",
    "apply_call",
]
