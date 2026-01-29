"""
execution.types.status — canonical transaction status enum.

TxStatus models the *logical* outcome of executing a transaction:
  - SUCCESS : Execution completed without a semantic failure
  - REVERT  : Contract-triggered revert (explicit failure)
  - OOG     : Out-of-gas during execution

String forms:
  - str(TxStatus.SUCCESS) -> "success"   (good for logs/metrics)
  - TxStatus.SUCCESS.code  -> "SUCCESS"  (good for receipts/protocols)

Parsing is lenient via `TxStatus.from_str(...)` and accepts common aliases
(e.g., "ok", "revert", "oog", "out_of_gas").
"""

from __future__ import annotations

from enum import Enum
from typing import Optional


class TxStatus(str, Enum):
    SUCCESS = "success"
    REVERT = "revert"
    OOG = "oog"

    # ---------- convenience ----------

    @property
    def code(self) -> str:
        """Uppercase code form, e.g., 'SUCCESS' / 'REVERT' / 'OOG'."""
        return self.value.upper()

    @property
    def is_success(self) -> bool:
        """True iff status is SUCCESS."""
        return self is TxStatus.SUCCESS

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value

    # ---------- parsing ----------

    @classmethod
    def from_str(cls, s: str, *, default: Optional["TxStatus"] = None) -> "TxStatus":
        """
        Parse a status from a string (case/format-insensitive).

        Accepted values:
          - success: "success", "ok", "s", "passed"
          - revert : "revert", "rv", "failed", "fail"
          - oog    : "oog", "out_of_gas", "out-of-gas", "outofgas"

        Raises:
            ValueError if parsing fails and no default is provided.
        """
        if not s:
            if default is not None:
                return default
            raise ValueError("empty status")

        norm = s.strip().lower().replace("-", "_")
        if norm in {"success", "ok", "s", "passed"}:
            return cls.SUCCESS
        if norm in {"revert", "rv", "failed", "fail"}:
            return cls.REVERT
        if norm in {"oog", "out_of_gas", "outofgas"}:
            return cls.OOG

        # direct match to enum values, just in case
        try:
            return cls(norm)  # type: ignore[arg-type]
        except Exception:
            if default is not None:
                return default
            raise ValueError(f"unknown TxStatus: {s!r}") from None


__all__ = ["TxStatus"]
