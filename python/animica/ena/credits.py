from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import EnaConfigModel, JobReceipt
from .receipts import build_credit_event
from .store import EnaStore


def _normalize_row(value: Any) -> Dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return dict(value)
    return {"value": value}


class EnaCreditsAdapter:
    def __init__(self, store: EnaStore, config: EnaConfigModel):
        self.store = store
        self.config = config
        self.protocol_state = None
        self.error: Optional[str] = None
        self.db_path = Path(config.aicf_db_path).expanduser() if config.aicf_db_path else None
        try:
            from aicf.protocol.state import ProtocolState

            if self.db_path is None:
                from aicf.db import db_path as resolve_db_path

                self.db_path = resolve_db_path("ena_protocol.sqlite3", create=True)
            self.protocol_state = ProtocolState(str(self.db_path))
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)

    def available(self) -> bool:
        return self.protocol_state is not None

    def apply_receipt(self, receipt: JobReceipt) -> Dict[str, Any]:
        event = build_credit_event(receipt)
        miner_address = receipt.miner_address or self.config.default_miner_address or receipt.worker_id or receipt.provider_id or self.config.default_worker_id
        if self.protocol_state is None:
            return {
                "available": False,
                "applied": False,
                "ledger_id": event["ledger_id"],
                "miner_address": miner_address,
                "error": self.error or "AICF protocol state unavailable",
            }

        existing = self.protocol_state.get_credit_ledger(job_id=receipt.job_id, limit=100)
        if any(item.ledger_id == event["ledger_id"] for item in existing):
            return {
                "available": True,
                "applied": False,
                "duplicate": True,
                "ledger_id": event["ledger_id"],
                "miner_address": miner_address,
            }

        amount = int(event["amount"])
        if receipt.verification_passed and amount > 0:
            self.protocol_state.add_miner_credits(
                miner_address=miner_address,
                amount=amount,
                block_height=0,
                block_hash=receipt.receipt_hash,
            )
            self.protocol_state.update_aicf_totals(
                balance_delta=amount,
                minted_delta=amount,
                block_height=0,
                block_hash=receipt.receipt_hash,
            )

        self.protocol_state.log_credit_event(
            ledger_id=event["ledger_id"],
            event_type=event["event_type"],
            block_height=0,
            block_hash=receipt.receipt_hash,
            amount=event["amount"],
            source=event["source"],
            miner_address=miner_address,
            job_id=receipt.job_id,
            recipients=[miner_address],
            metadata=event.get("metadata", {}),
        )
        return {
            "available": True,
            "applied": True,
            "ledger_id": event["ledger_id"],
            "miner_address": miner_address,
            "amount": amount,
        }

    def show(self, miner_address: Optional[str] = None, *, limit: int = 20) -> Dict[str, Any]:
        resolved_miner = miner_address or self.config.default_miner_address or self.config.default_worker_id
        summary: Dict[str, Any] = {
            "available": self.available(),
            "db_path": str(self.db_path) if self.db_path else None,
            "miner_address": resolved_miner,
            "error": self.error,
            "receipts": [receipt.model_dump(mode="json") for receipt in self.store.list_receipts(limit=limit)],
        }
        if self.protocol_state is None:
            credits = sum(
                int(receipt.reward.get("credits", 0) or 0)
                for receipt in self.store.list_receipts(limit=500)
                if receipt.verification_passed
            )
            summary["totals"] = {"balance_total": str(credits), "minted_total": str(credits), "spent_total": "0"}
            summary["miner"] = {
                "miner_address": resolved_miner,
                "balance": str(credits),
                "lifetime_earned": str(credits),
                "lifetime_spent": "0",
            }
            summary["ledger"] = []
            return summary

        summary["totals"] = _normalize_row(self.protocol_state.get_aicf_totals())
        summary["miner"] = _normalize_row(self.protocol_state.get_miner_credits(resolved_miner))
        summary["ledger"] = [_normalize_row(item) for item in self.protocol_state.get_credit_ledger(miner_address=resolved_miner, limit=limit)]
        return summary

    def mining_status(self, miner_address: Optional[str] = None) -> Dict[str, Any]:
        credits = self.show(miner_address=miner_address, limit=10)
        verified_receipts = [item for item in credits["receipts"] if item.get("verification_passed")]
        return {
            "available": credits["available"],
            "db_path": credits["db_path"],
            "miner_address": credits["miner_address"],
            "verified_receipt_count": len(verified_receipts),
            "total_receipt_count": len(credits["receipts"]),
            "credit_totals": credits["totals"],
            "miner_credits": credits["miner"],
            "recent_ledger": credits.get("ledger", [])[:5],
        }
