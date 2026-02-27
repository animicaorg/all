from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from animica_studio.services.rpc_client import RpcClient
from animica_studio.models.wallet_models import is_valid_address


@dataclass
class EarningsSnapshot:
    address: str = ""
    balance_wei: int = 0
    session_start_wei: int = 0
    session_delta_wei: int = 0
    today_delta_wei: int = 0
    claimable_credits: float = 0.0
    last_claim_tx: str = ""
    last_claim_time: float = 0.0
    updated_at: float = 0.0
    error: str = ""


class EnaEarningsService(QObject):
    earningsUpdated = Signal(object)
    logLine = Signal(str, str)

    def __init__(self, rpc_url: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rpc_url = rpc_url
        self._snapshot = EarningsSnapshot()
        self._poll = QTimer(self)
        self._poll.timeout.connect(self.refresh)
        self._poll.setInterval(30_000)
        self._auto_claim = False
        self._claim_threshold = 0.0
        self._claimable_positional_fallback = False
        self._refresh_paused = False
        self._pause_reason = ""

    @property
    def snapshot(self) -> EarningsSnapshot:
        return self._snapshot

    def configure(self, *, rpc_url: str, address: str, auto_claim: bool = False, claim_threshold: float = 0.0) -> None:
        self._rpc_url = rpc_url
        self._auto_claim = auto_claim
        self._claim_threshold = max(0.0, float(claim_threshold or 0.0))
        if address != self._snapshot.address:
            self._snapshot = EarningsSnapshot(address=address)
            self._refresh_paused = False
            self._pause_reason = ""

    def start(self) -> None:
        self.refresh()
        self._poll.start()

    def stop(self) -> None:
        self._poll.stop()

    def refresh(self) -> None:
        if not self._snapshot.address:
            return
        if not is_valid_address(self._snapshot.address):
            snap = EarningsSnapshot(**asdict(self._snapshot))
            snap.error = "Payout address is invalid. Pick a wallet from wallet list."
            self._snapshot = snap
            self.earningsUpdated.emit(self._snapshot)
            return
        if self._refresh_paused:
            return
        snap = EarningsSnapshot(**asdict(self._snapshot))
        snap.updated_at = time.time()
        try:
            with RpcClient(self._rpc_url, connect_timeout=3.0, read_timeout=10.0, max_retries=1) as client:
                bal = client.get_balance(snap.address)
                snap.balance_wei = int(bal)
                if snap.session_start_wei == 0:
                    snap.session_start_wei = int(bal)
                snap.session_delta_wei = snap.balance_wei - snap.session_start_wei
                snap.today_delta_wei = snap.session_delta_wei
                snap.claimable_credits = self._get_claimable(client, snap.address)
                if self._auto_claim and snap.claimable_credits >= self._claim_threshold > 0:
                    self._claim(client, snap)
                snap.error = ""
        except Exception as exc:  # noqa: BLE001
            snap.error = str(exc)
            self.logLine.emit("warning", f"earnings refresh failed: {exc}")
            msg = str(exc).lower()
            if "invalid address" in msg or "address" in msg and "invalid" in msg:
                self._refresh_paused = True
                self._pause_reason = str(exc)
                self.logLine.emit("warning", "earnings refresh paused due to invalid payout address")
        self._snapshot = snap
        self.earningsUpdated.emit(self._snapshot)

    def claim_now(self) -> None:
        if not self._snapshot.address:
            return
        if not is_valid_address(self._snapshot.address):
            snap = EarningsSnapshot(**asdict(self._snapshot))
            snap.error = "Payout address is invalid. Pick a wallet from wallet list."
            self._snapshot = snap
            self.earningsUpdated.emit(self._snapshot)
            return
        if self._refresh_paused:
            return
        try:
            with RpcClient(self._rpc_url, connect_timeout=3.0, read_timeout=20.0, max_retries=1) as client:
                self._claim(client, self._snapshot)
        except Exception as exc:  # noqa: BLE001
            self.logLine.emit("error", f"claim failed: {exc}")
        self.refresh()

    def _get_claimable(self, client: RpcClient, address: str) -> float:
        reg = client.registry()
        method = reg.resolve_any(["aicf.getClaimable", "aicf_getClaimable", "aicf.creditsByAddress", "aicf_creditsByAddress"])
        if not method:
            return 0.0
        try:
            if self._claimable_positional_fallback:
                out = client.call(method, [address])
            else:
                out = client.call_with_schema(method, {"address": address})
        except Exception as exc:
            message = str(exc).lower()
            if "missing required params" in message and not self._claimable_positional_fallback:
                out = client.call(method, [address])
                self._claimable_positional_fallback = True
            else:
                raise
        if isinstance(out, dict):
            for key in ("claimable", "credits", "amount"):
                v = out.get(key)
                if isinstance(v, (int, float)):
                    return float(v)
                if isinstance(v, str):
                    try:
                        return float(v)
                    except ValueError:
                        continue
            return 0.0
        if isinstance(out, (int, float)):
            return float(out)
        return 0.0

    def _claim(self, client: RpcClient, snap: EarningsSnapshot) -> None:
        reg = client.registry()
        method = reg.resolve_any(["aicf.claim", "aicf_claim"])
        if not method:
            self.logLine.emit("warning", "aicf.claim unavailable")
            return
        out = client.call_with_schema(method, {"address": snap.address})
        tx_hash = ""
        if isinstance(out, dict):
            tx_hash = str(out.get("tx_hash") or out.get("hash") or "")
        elif isinstance(out, str):
            tx_hash = out
        snap.last_claim_tx = tx_hash
        snap.last_claim_time = time.time()
        self.logLine.emit("info", f"claim submitted tx={tx_hash or '<unknown>'}")
