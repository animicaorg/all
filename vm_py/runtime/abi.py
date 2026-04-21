from __future__ import annotations

import inspect
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .error import VmError


def _validation_error(
    message: str,
    *,
    phase: str | None = None,
    reason: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> Exception:
    try:
        from vm_py.errors import ValidationError as _VE  # type: ignore

        return _VE(message, phase=phase, reason=reason, context=context)
    except Exception:
        ctx = dict(context or {})
        if phase is not None:
            ctx.setdefault("phase", phase)
        if reason is not None:
            ctx.setdefault("reason", reason)
        return VmError(message, code="validation_error", context=ctx)


# ---------------------------------------------------------------------------
# Basic assertion/revert helpers
# ---------------------------------------------------------------------------


def _to_message(msg: Any) -> str:
    if isinstance(msg, (bytes, bytearray)):
        return msg.decode("utf-8", errors="replace")
    return str(msg)


def require(
    condition: bool,
    message: Any = "abi.require failed",
    *,
    code: str = "abi.require_failed",
    context: Optional[Mapping[str, Any]] = None,
) -> None:
    if condition:
        return
    raise VmError(_to_message(message), code=code, context=dict(context or {}))


def revert(message: Any = "revert", *, code: str = "abi.revert", context: Optional[Mapping[str, Any]] = None) -> None:
    raise VmError(_to_message(message), code=code, context=dict(context or {}))


# ---------------------------------------------------------------------------
# Runtime call context (thread-local)
# ---------------------------------------------------------------------------


@dataclass
class CallContext:
    sender: bytes
    origin: bytes
    contract: bytes
    value: int = 0
    chain_id: int = 0
    tx_hash: bytes = b""
    block_height: int = 0
    block_timestamp: int = 0
    depth: int = 0
    read_only: bool = False


_ThreadState = threading.local()


_CALL_HOOK = Callable[[bytes, str, Any, int, bool], Any]



def _ctx_stack() -> List[CallContext]:
    stack = getattr(_ThreadState, "ctx_stack", None)
    if stack is None:
        stack = []
        setattr(_ThreadState, "ctx_stack", stack)
    return stack



def _hook_stack() -> List[_CALL_HOOK]:
    stack = getattr(_ThreadState, "hook_stack", None)
    if stack is None:
        stack = []
        setattr(_ThreadState, "hook_stack", stack)
    return stack



def _current_context() -> Optional[CallContext]:
    stack = _ctx_stack()
    if not stack:
        return None
    return stack[-1]



def push_context(ctx: CallContext) -> None:
    _ctx_stack().append(ctx)



def pop_context() -> Optional[CallContext]:
    stack = _ctx_stack()
    if not stack:
        return None
    return stack.pop()


@contextmanager
def runtime_context(ctx: CallContext):
    push_context(ctx)
    try:
        yield
    finally:
        pop_context()



def push_call_hook(hook: _CALL_HOOK) -> None:
    _hook_stack().append(hook)



def pop_call_hook() -> Optional[_CALL_HOOK]:
    stack = _hook_stack()
    if not stack:
        return None
    return stack.pop()



def set_call_hook(hook: _CALL_HOOK | None) -> _CALL_HOOK | None:
    """Compatibility helper; replaces the active hook and returns previous."""
    prev = current_call_hook()
    stack = _hook_stack()
    stack.clear()
    if hook is not None:
        stack.append(hook)
    return prev



def current_call_hook() -> _CALL_HOOK | None:
    stack = _hook_stack()
    if not stack:
        return None
    return stack[-1]


# ---------------------------------------------------------------------------
# Context accessors exposed to contracts
# ---------------------------------------------------------------------------


def _ctx_or_default() -> CallContext:
    ctx = _current_context()
    if ctx is not None:
        return ctx
    return CallContext(sender=b"", origin=b"", contract=b"")



def caller() -> bytes:
    return bytes(_ctx_or_default().sender)



def sender() -> bytes:
    return caller()



def tx_origin() -> bytes:
    return bytes(_ctx_or_default().origin)



def contract_address() -> bytes:
    return bytes(_ctx_or_default().contract)



def self_address() -> bytes:
    return contract_address()



def value() -> int:
    return int(_ctx_or_default().value)



def chain_id() -> int:
    return int(_ctx_or_default().chain_id)



def tx_hash() -> bytes:
    return bytes(_ctx_or_default().tx_hash)



def block_height() -> int:
    return int(_ctx_or_default().block_height)



def block_timestamp() -> int:
    return int(_ctx_or_default().block_timestamp)



def call_depth() -> int:
    return int(_ctx_or_default().depth)



def is_read_only() -> bool:
    return bool(_ctx_or_default().read_only)


# ---------------------------------------------------------------------------
# Inter-contract call primitive
# ---------------------------------------------------------------------------


def _coerce_address(value: Any) -> bytes:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith(("0x", "0X")):
            raw = text[2:]
            if len(raw) % 2:
                raw = "0" + raw
            try:
                return bytes.fromhex(raw)
            except Exception as exc:  # pragma: no cover
                raise VmError("invalid hex address", code="abi.bad_address", context={"value": value}) from exc
        return text.encode("utf-8")
    raise VmError("address must be bytes or string", code="abi.bad_address", context={"type": type(value).__name__})



def _coerce_method(value: Any) -> str:
    if isinstance(value, str):
        out = value
    elif isinstance(value, (bytes, bytearray, memoryview)):
        out = bytes(value).decode("utf-8", errors="strict")
    else:
        out = str(value)
    out = out.strip()
    if not out:
        raise VmError("method must be non-empty", code="abi.bad_method")
    return out



def _normalize_call_args(args: Any) -> Any:
    if args is None:
        return []
    if isinstance(args, (list, tuple)):
        return list(args)
    # Keep mapping payloads intact to support map-shaped cross-call arguments.
    if isinstance(args, Mapping):
        return dict(args)
    return [args]



def call_contract(
    to: Any,
    method: Any,
    args: Any = None,
    value: int = 0,
    *,
    read_only: bool = False,
) -> Any:
    """Call another contract through the execution-installed hook."""
    if is_read_only() and int(value) != 0:
        raise VmError(
            "cannot transfer value in read-only context",
            code="abi.read_only_value",
            context={"value": int(value)},
        )

    if not isinstance(value, int) or value < 0:
        raise VmError("value must be a non-negative int", code="abi.bad_value")

    hook = current_call_hook()
    if hook is None:
        raise VmError(
            "inter-contract calls are not enabled",
            code="abi.intercall_unavailable",
        )

    target = _coerce_address(to)
    fn = _coerce_method(method)
    call_args = _normalize_call_args(args)
    return hook(target, fn, call_args, int(value), bool(read_only))



def try_call_contract(
    to: Any,
    method: Any,
    args: Any = None,
    value: int = 0,
    *,
    read_only: bool = False,
) -> Tuple[bool, Any]:
    try:
        return True, call_contract(to, method, args=args, value=value, read_only=read_only)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


# ---------------------------------------------------------------------------
# Lightweight structured encode/decode convenience
# ---------------------------------------------------------------------------


def encode_struct(value: Any) -> bytes:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    except Exception as exc:  # noqa: BLE001
        raise VmError("encode_struct failed", code="abi.encode_struct_failed", context={"error": str(exc)}) from exc



def decode_struct(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            return json.loads(bytes(value).decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise VmError("decode_struct failed", code="abi.decode_struct_failed", context={"error": str(exc)}) from exc
    return value


# ---------------------------------------------------------------------------
# Dispatcher + fallback call envelope codec
# ---------------------------------------------------------------------------


_use_external_abi = True



def get_dispatch_table(contract: Any) -> Dict[str, Callable[..., Any]]:
    table: Dict[str, Callable[..., Any]] = {}
    for name in dir(contract):
        if name.startswith("_"):
            continue
        try:
            attr = getattr(contract, name)
        except Exception:
            continue
        if not callable(attr):
            continue
        export_flag = getattr(attr, "__animica_export__", True)
        if export_flag is False:
            continue
        table[name] = attr
    return table


class Dispatcher:
    def __init__(self, contract: Any, table: Dict[str, Callable[..., Any]]) -> None:
        self.contract = contract
        self.table = table

    @classmethod
    def for_contract(cls, contract: Any) -> "Dispatcher":
        return cls(contract, get_dispatch_table(contract))

    def invoke(self, method: str, args: Sequence[Any]) -> Any:
        fn = self.table.get(method)
        if fn is None:
            raise _validation_error(
                f"unknown method: {method}",
                phase="dispatch",
                reason="unknown_method",
                context={"method": method},
            )
        try:
            return fn(*list(args))
        except TypeError as exc:
            raise _validation_error(
                f"invalid call arguments for {method}",
                phase="dispatch",
                reason="bad_arguments",
                context={"method": method, "error": str(exc)},
            ) from exc



def _uvarint_encode(n: int) -> bytes:
    if not isinstance(n, int) or n < 0:
        raise _validation_error("uvarint expects non-negative int", phase="abi", reason="bad_varint")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            break
    return bytes(out)



def _uvarint_decode(buf: bytes, offset: int = 0) -> Tuple[int, int]:
    n = 0
    shift = 0
    i = int(offset)
    while i < len(buf):
        b = buf[i]
        i += 1
        n |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            return n, i
        shift += 7
        if shift > 10_000:  # defensive bound
            break
    raise _validation_error("malformed varint", phase="abi", reason="bad_varint")


_TAG_NONE = 0
_TAG_INT = 1
_TAG_BYTES = 2
_TAG_STR = 3
_TAG_BOOL = 4
_TAG_LIST = 5
_TAG_DICT = 6



def _enc_sint(v: int) -> bytes:
    # zig-zag signed integer encoding
    zz = (v << 1) ^ (v >> 63)
    if v.bit_length() > 63:
        zz = (abs(v) << 1) | (1 if v < 0 else 0)
    return _uvarint_encode(zz)



def _dec_sint(buf: bytes, offset: int) -> Tuple[int, int]:
    zz, off = _uvarint_decode(buf, offset)
    v = (zz >> 1) ^ -(zz & 1)
    return v, off



def _enc_value(v: Any) -> bytes:
    if v is None:
        return bytes([_TAG_NONE])
    if isinstance(v, bool):
        return bytes([_TAG_BOOL, 1 if v else 0])
    if isinstance(v, int):
        return bytes([_TAG_INT]) + _enc_sint(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        b = bytes(v)
        return bytes([_TAG_BYTES]) + _uvarint_encode(len(b)) + b
    if isinstance(v, str):
        b = v.encode("utf-8")
        return bytes([_TAG_STR]) + _uvarint_encode(len(b)) + b
    if isinstance(v, (list, tuple)):
        out = bytearray([_TAG_LIST])
        out += _uvarint_encode(len(v))
        for item in v:
            out += _enc_value(item)
        return bytes(out)
    if isinstance(v, Mapping):
        items = sorted(((str(k), val) for k, val in v.items()), key=lambda kv: kv[0])
        out = bytearray([_TAG_DICT])
        out += _uvarint_encode(len(items))
        for k, val in items:
            out += _enc_value(k)
            out += _enc_value(val)
        return bytes(out)
    raise _validation_error(
        f"unsupported ABI value type: {type(v).__name__}",
        phase="abi",
        reason="unsupported_value",
    )



def _dec_value(buf: bytes, offset: int = 0) -> Tuple[Any, int]:
    if offset >= len(buf):
        raise _validation_error("truncated value", phase="abi", reason="truncated")
    tag = buf[offset]
    off = offset + 1

    if tag == _TAG_NONE:
        return None, off
    if tag == _TAG_BOOL:
        if off >= len(buf):
            raise _validation_error("truncated bool", phase="abi", reason="truncated")
        return bool(buf[off]), off + 1
    if tag == _TAG_INT:
        return _dec_sint(buf, off)
    if tag in (_TAG_BYTES, _TAG_STR):
        ln, off2 = _uvarint_decode(buf, off)
        end = off2 + ln
        if end > len(buf):
            raise _validation_error("truncated bytes", phase="abi", reason="truncated")
        raw = bytes(buf[off2:end])
        if tag == _TAG_STR:
            return raw.decode("utf-8"), end
        return raw, end
    if tag == _TAG_LIST:
        n, off2 = _uvarint_decode(buf, off)
        items: List[Any] = []
        cur = off2
        for _ in range(n):
            item, cur = _dec_value(buf, cur)
            items.append(item)
        return items, cur
    if tag == _TAG_DICT:
        n, off2 = _uvarint_decode(buf, off)
        cur = off2
        out: Dict[str, Any] = {}
        for _ in range(n):
            k, cur = _dec_value(buf, cur)
            if not isinstance(k, str):
                raise _validation_error("dict key must decode to string", phase="abi", reason="bad_map_key")
            val, cur = _dec_value(buf, cur)
            out[k] = val
        return out, cur

    raise _validation_error("unknown value tag", phase="abi", reason="bad_tag", context={"tag": tag})



def _fallback_decode_call(data: bytes) -> Tuple[str, List[Any]]:
    mlen, off = _uvarint_decode(data, 0)
    end = off + mlen
    if end > len(data):
        raise _validation_error("truncated method name", phase="abi", reason="truncated")
    method = bytes(data[off:end]).decode("utf-8")
    argc, cur = _uvarint_decode(data, end)
    args: List[Any] = []
    for _ in range(argc):
        v, cur = _dec_value(data, cur)
        args.append(v)
    if cur != len(data):
        raise _validation_error("trailing bytes in call payload", phase="abi", reason="trailing_bytes")
    return method, args



def _fallback_encode_return(value: Any) -> bytes:
    return _enc_value(value)



def _decode_call_external(data: bytes) -> Tuple[str, List[Any]]:
    # Placeholder for future ABI integration. Keep deterministic fallback behavior.
    return _fallback_decode_call(data)



def dispatch_call(contract: Any, data: bytes) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise _validation_error("call data must be bytes", phase="abi", reason="bad_payload")
    payload = bytes(data)

    if _use_external_abi:
        method, args = _decode_call_external(payload)
    else:
        method, args = _fallback_decode_call(payload)

    result = Dispatcher.for_contract(contract).invoke(method, args)
    return _fallback_encode_return(result)


__all__ = [
    "CallContext",
    "require",
    "revert",
    "caller",
    "sender",
    "tx_origin",
    "contract_address",
    "self_address",
    "value",
    "chain_id",
    "tx_hash",
    "block_height",
    "block_timestamp",
    "call_depth",
    "is_read_only",
    "push_context",
    "pop_context",
    "runtime_context",
    "push_call_hook",
    "pop_call_hook",
    "set_call_hook",
    "current_call_hook",
    "call_contract",
    "try_call_contract",
    "encode_struct",
    "decode_struct",
    "get_dispatch_table",
    "Dispatcher",
    "dispatch_call",
    "_use_external_abi",
    "_uvarint_encode",
    "_uvarint_decode",
    "_enc_value",
    "_dec_value",
    "_fallback_decode_call",
    "_fallback_encode_return",
]
