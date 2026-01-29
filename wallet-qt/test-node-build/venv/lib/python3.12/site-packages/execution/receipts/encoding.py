"""
execution.receipts.encoding — deterministic CBOR encoding/decoding for receipts.

Targets the minimal schema described in spec/tx_format.cddl:

  Receipt = {
    status:   int,            ; 0=SUCCESS, 1=REVERT, 2=OOG (see execution/types/status.py)
    gasUsed:  uint,           ; total gas used
    logs:     [ LogEvent ]    ; ordered list of events
    ; optional fields may exist in extended builds (logs_bloom, logs_root)
  }

  LogEvent = {
    address: bytes,           ; 20/32 bytes (implementation-defined length checks upstream)
    topics:  [ bytes ],       ; each topic is bytes (fixed length upstream)
    data:    bytes,
  }

This module encodes maps *canonically* and is strict about types to keep hashing
stable across nodes.

Public API
----------
- receipt_to_cbor(receipt) -> bytes
- receipt_from_cbor(data: bytes) -> Receipt

Implementation notes
--------------------
- We prefer `cbor2` with `canonical=True`. If unavailable, we raise an ImportError
  with a clear message (Animica nodes should vendor/pin cbor2).
- Field order in maps is canonical by UTF-8 key bytes per RFC 7049bis (§4.2.1).
- We tolerate either `gas_used` (snake) or `gasUsed` (camel) at the dataclass layer,
  but on the wire we always emit `gasUsed` (camel) for stability across languages.
"""

from __future__ import annotations

from dataclasses import asdict, fields, is_dataclass
from typing import Any, Dict, Iterable, List, Mapping, Tuple

try:
    import cbor2  # type: ignore
except Exception as e:  # pragma: no cover
    raise ImportError(
        "execution.receipts.encoding requires the 'cbor2' package. "
        "Install with: pip install cbor2"
    ) from e

# Types
try:
    # The Receipt dataclass lives in core/types in this codebase.
    from core.types.receipt import Receipt  # type: ignore
except Exception:  # pragma: no cover
    # Fallback if the project colocated a Receipt elsewhere during early bring-up.
    from execution.types.receipt import Receipt  # type: ignore

from execution.types.events import LogEvent

# ------------------------------ Helpers -------------------------------------


def _is_bytes_like(x: Any) -> bool:
    return isinstance(x, (bytes, bytearray, memoryview))


def _to_bytes(x: Any) -> bytes:
    if isinstance(x, bytes):
        return x
    if isinstance(x, bytearray):
        return bytes(x)
    if isinstance(x, memoryview):
        return bytes(x)
    if isinstance(x, str) and x.startswith("0x"):
        # Accept hex strings for convenience when decoding external fixtures.
        h = x[2:]
        if len(h) % 2:
            h = "0" + h
        return bytes.fromhex(h)
    raise TypeError(f"Expected bytes-like, got {type(x)!r}")


def _log_to_obj(ev: LogEvent) -> Dict[str, Any]:
    if not isinstance(ev, LogEvent):
        raise TypeError(f"LogEvent expected, got {type(ev)!r}")
    addr = _to_bytes(ev.address)
    topics = [_to_bytes(t) for t in list(ev.topics)]
    data = _to_bytes(ev.data)
    # Canonical key names for wire format
    return {"address": addr, "topics": topics, "data": data}


def _obj_to_log(obj: Mapping[str, Any]) -> LogEvent:
    try:
        address = _to_bytes(obj["address"])
        topics_any = obj.get("topics", [])
        topics = tuple(_to_bytes(t) for t in topics_any)
        data = _to_bytes(obj.get("data", b""))
    except KeyError as e:
        raise ValueError(f"Missing LogEvent field: {e}") from None
    return LogEvent(address=address, topics=topics, data=data)


def _receipt_to_obj(rcpt: Receipt) -> Dict[str, Any]:
    if not is_dataclass(rcpt):
        raise TypeError("Receipt must be a dataclass instance")

    # Introspect field names present in the dataclass to be flexible
    fset = {f.name for f in fields(rcpt)}
    out: Dict[str, Any] = {}

    # status
    status_val = getattr(rcpt, "status")
    # Enums should serialize to their underlying int
    try:
        status_int = int(status_val)  # type: ignore[arg-type]
    except Exception:
        # Fallback: some enums expose `.value`
        status_int = int(getattr(status_val, "value"))
    out["status"] = status_int

    # gasUsed on the wire; accept gas_used or gasUsed on the object
    if "gas_used" in fset:
        gas_used = int(getattr(rcpt, "gas_used"))
    else:
        gas_used = int(getattr(rcpt, "gasUsed"))
    out["gasUsed"] = gas_used

    # logs — ensure canonical element shape
    logs_list = getattr(rcpt, "logs")
    if not isinstance(logs_list, Iterable):
        raise TypeError("Receipt.logs must be iterable")
    out["logs"] = [_log_to_obj(ev) for ev in logs_list]

    # Optional enrichments if present (wire names use snake_case to avoid collision
    # with reserved camel names; these are not required by minimal schema)
    if "logs_bloom" in fset:
        out["logs_bloom"] = _to_bytes(getattr(rcpt, "logs_bloom"))
    if "logs_root" in fset:
        out["logs_root"] = _to_bytes(getattr(rcpt, "logs_root"))

    return out


def _obj_to_receipt(obj: Mapping[str, Any]) -> Receipt:
    # Required
    if "status" not in obj or "gasUsed" not in obj or "logs" not in obj:
        missing = [k for k in ("status", "gasUsed", "logs") if k not in obj]
        raise ValueError(f"Receipt missing required fields: {missing}")

    status = int(obj["status"])
    gas_used = int(obj["gasUsed"])
    logs = tuple(_obj_to_log(e) for e in obj["logs"])

    # Figure out which field style the dataclass uses
    fset = {f.name for f in fields(Receipt)}
    kwargs: Dict[str, Any] = {"status": status, "logs": list(logs)}
    if "gas_used" in fset:
        kwargs["gas_used"] = gas_used
    else:
        kwargs["gasUsed"] = gas_used

    # Optional enrichments (ignored if dataclass doesn't declare them)
    if "logs_bloom" in obj and "logs_bloom" in fset:
        kwargs["logs_bloom"] = _to_bytes(obj["logs_bloom"])
    if "logs_root" in obj and "logs_root" in fset:
        kwargs["logs_root"] = _to_bytes(obj["logs_root"])

    return Receipt(**kwargs)  # type: ignore[arg-type]


# ------------------------------ Public API ----------------------------------


def receipt_to_cbor(receipt: Receipt) -> bytes:
    """
    Serialize a Receipt to canonical CBOR bytes.
    """
    obj = _receipt_to_obj(receipt)
    return cbor2.dumps(obj, canonical=True)


def receipt_from_cbor(data: bytes) -> Receipt:
    """
    Deserialize CBOR bytes into a Receipt, validating required fields and shapes.
    """
    if not _is_bytes_like(data):
        raise TypeError("receipt_from_cbor expects a bytes-like object")
    obj = cbor2.loads(bytes(data))
    if not isinstance(obj, Mapping):
        raise ValueError("Receipt CBOR must decode to a map")
    return _obj_to_receipt(obj)


__all__ = ["receipt_to_cbor", "receipt_from_cbor"]
