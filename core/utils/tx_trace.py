from __future__ import annotations

import logging
import os
from typing import Any, Mapping

log = logging.getLogger("animica.tx.trace")


def _normalize_txid(txid: bytes | str | None) -> str | None:
    if txid is None:
        return None
    if isinstance(txid, (bytes, bytearray)):
        return "0x" + bytes(txid).hex()
    if isinstance(txid, str):
        return txid if txid.startswith("0x") else f"0x{txid}"
    return str(txid)


def tx_trace(
    txid: bytes | str | None,
    stage: str,
    details: Mapping[str, Any] | None = None,
) -> None:
    """
    Emit a structured TX_TRACE log line with consistent fields.

    Expected fields:
      - txid (hex string)
      - stage (short lifecycle marker)
      - node_id (best-effort, from env if not supplied)
      - peer (optional)
      - reason_code / reason (optional)
    """
    payload: dict[str, Any] = {
        "txid": _normalize_txid(txid),
        "stage": stage,
    }
    if details:
        payload.update(details)
    payload.setdefault("node_id", os.environ.get("ANIMICA_NODE_ID"))
    log.info("TX_TRACE", extra=payload)


__all__ = ["tx_trace"]
