"""Dashboard page — network status, total balance, and recent activity."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from animica_studio.services.activity_store import ActivityKind, ActivityStore
from animica_studio.services.explorer_balance_service import ExplorerBalanceService, TotalBalanceResult
from animica_studio.services.wallet_store import WalletStore
from animica_studio.services.cli_runner import CliRunner
from animica_studio.services.workers import run_in_threadpool
from animica_studio.util.qt import ui_thread_only
from animica_studio.util.threading_guard import assert_ui_thread
from animica_studio.ui.components.primitives import (
    Badge,
    Card,
    SectionHeader,
)
from animica_studio.ui.effects.hero import HeroVisual

log = logging.getLogger(__name__)

_POLL_INTERVAL_MS = 45_000   # network status re-check every 45 000 ms (45 s)
_KIND_ICON: dict[ActivityKind, str] = {
    ActivityKind.JOB_OK: "⚙",
    ActivityKind.JOB_FAIL: "⚙",
    ActivityKind.BALANCE_FETCH: "◎",
    ActivityKind.NETWORK_CHECK: "◍",
    ActivityKind.TX_SEND: "↗",
    ActivityKind.WALLET_LOAD: "◉",
    ActivityKind.GENERIC: "·",
}


class DashboardPage(QWidget):
    def __init__(self, config: Any = None, profile_service: Any = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._profile_service = profile_service
        self._net_worker = None
        self._health_job = None
        self._last_health_payload: dict[str, Any] = {}
        self._balance_refresh_in_flight = False
        self._build_ui()

        # Deferred init — run after the window is shown
        QTimer.singleShot(0, self._post_show_init)

    def _ensure_ui_thread(self, fn, *args) -> bool:
        if assert_ui_thread():
            return True
        QTimer.singleShot(0, lambda: fn(*args))
        return False

    # ------------------------------------------------------------------
    # UI build
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(SectionHeader("Dashboard", "Overview of wallet, network, and latest activity."))

        # Hero section
        self.hero = HeroVisual(mode="balanced")
        hero_card = Card()
        hero_card.layout().setContentsMargins(0, 0, 0, 0)
        hero_card.layout().addWidget(self.hero)
        layout.addWidget(hero_card)

        # ── Status row ──────────────────────────────────────────────────
        row = QHBoxLayout()
        row.setSpacing(12)

        # Network status card
        status_card = Card()
        status_card.layout().addWidget(QLabel("Network status"))
        badge_row = QHBoxLayout()
        self._status_badge = Badge("● Checking…")
        self._status_badge.setToolTip("Running initial check…")
        badge_row.addWidget(self._status_badge)
        badge_row.addStretch()
        status_card.layout().addLayout(badge_row)
        self._status_detail = QLabel("")
        self._status_detail.setStyleSheet("color: #9aa0a6; font-size: 11px;")
        self._status_detail.setWordWrap(True)
        status_card.layout().addWidget(self._status_detail)
        net_refresh = QPushButton("↺")
        net_refresh.setMaximumWidth(32)
        net_refresh.setToolTip("Re-check network")
        net_refresh.clicked.connect(self.refresh_network_status)
        badge_row.addWidget(net_refresh)
        diag_btn = QPushButton("Copy diagnostics")
        diag_btn.clicked.connect(self._copy_diagnostics)
        status_card.layout().addWidget(diag_btn, alignment=Qt.AlignmentFlag.AlignLeft)
        row.addWidget(status_card, 1)

        # Total balance card
        wallet_card = Card()
        wallet_card.layout().addWidget(QLabel("Total Balance"))
        self._balance_label = QLabel("…")
        self._balance_label.setStyleSheet("font-size: 18px; font-weight: 700;")
        wallet_card.layout().addWidget(self._balance_label)
        self._balance_meta = QLabel("")
        self._balance_meta.setStyleSheet("color: #9aa0a6; font-size: 11px;")
        wallet_card.layout().addWidget(self._balance_meta)
        bal_refresh = QPushButton("↺")
        bal_refresh.setMaximumWidth(32)
        bal_refresh.setToolTip("Refresh balances")
        bal_refresh.clicked.connect(self.refresh_balance)
        wallet_card.layout().addWidget(bal_refresh, alignment=Qt.AlignmentFlag.AlignLeft)
        row.addWidget(wallet_card, 1)

        layout.addLayout(row)

        # ── Recent activity ─────────────────────────────────────────────
        activity_card = Card()
        act_header = QHBoxLayout()
        act_header.addWidget(QLabel("Recent Activity"))
        act_header.addStretch()
        act_refresh = QPushButton("↺")
        act_refresh.setMaximumWidth(32)
        act_refresh.setToolTip("Refresh activity list")
        act_refresh.clicked.connect(self._refresh_activity)
        act_header.addWidget(act_refresh)
        activity_card.layout().addLayout(act_header)
        self._activity_container = QVBoxLayout()
        self._activity_container.setSpacing(4)
        activity_card.layout().addLayout(self._activity_container)
        self._activity_empty = QLabel(
            "No activity yet. Try: Refresh balances / Check node status."
        )
        self._activity_empty.setStyleSheet("color: #9aa0a6;")
        self._activity_empty.setWordWrap(True)
        activity_card.layout().addWidget(self._activity_empty)
        layout.addWidget(activity_card)

        layout.addStretch()

    # ------------------------------------------------------------------
    # Post-show init
    # ------------------------------------------------------------------

    def _post_show_init(self) -> None:
        self.refresh_network_status()
        self.refresh_balance()
        self._refresh_activity()

        # Periodic re-check timer (network status)
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(_POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self.refresh_network_status)
        self._poll_timer.start()

        # Activity auto-refresh (every 10 s)
        self._activity_timer = QTimer(self)
        self._activity_timer.setInterval(10_000)
        self._activity_timer.timeout.connect(self._refresh_activity)
        self._activity_timer.start()

    # ------------------------------------------------------------------
    # Network status
    # ------------------------------------------------------------------

    def refresh_network_status(self) -> None:
        """Kick off a non-blocking network reachability check."""
        profile = self._active_profile()
        if profile is None:
            self._set_status("⚠ No profile", "#e8a029", "Configure a profile in Settings.")
            return

        rpc_url = profile.effective_rpc_url()
        explorer_url = (profile.explorer_base_url or "").strip().rstrip("/")

        self._status_badge.setText("● Checking…")
        self._status_badge.setStyleSheet("")
        self._status_detail.setText("")

        def _check() -> dict[str, Any]:
            import requests as _req  # noqa: PLC0415
            from animica_studio.services.rpc_client import RpcClient  # noqa: PLC0415

            rpc = {"ok": False, "error": "", "detail": {}, "url": rpc_url}
            cli = {"ok": False, "error": "", "returncode": None}
            explorer = {"ok": False, "error": ""}

            try:
                c = RpcClient(rpc_url, connect_timeout=3.0, read_timeout=8.0, max_retries=1)
                try:
                    ping = c.ping_details()
                    rpc["ok"] = bool(ping.get("ok"))
                    rpc["detail"] = ping
                    rpc["error"] = str(ping.get("error", ""))
                finally:
                    c.close()
            except Exception as exc:  # noqa: BLE001
                rpc["error"] = str(exc)

            runner = CliRunner()
            cmd = ["animica", "node", "status"]
            cli_result = runner.run(cmd, timeout_s=8.0)
            cli["returncode"] = cli_result.returncode
            cli["ok"] = (cli_result.returncode == 0)
            cli["error"] = cli_result.error or (cli_result.stderr_lines[-1] if cli_result.stderr_lines else "")

            if explorer_url:
                try:
                    r = _req.get(f"{explorer_url}/api/health", timeout=(3, 8))
                    explorer["ok"] = r.status_code < 500
                    if not explorer["ok"]:
                        explorer["error"] = f"HTTP {r.status_code}"
                except Exception as exc:  # noqa: BLE001
                    explorer["error"] = str(exc)

            return {"rpc": rpc, "cli": cli, "explorer": explorer, "ts": time.time()}

        self._health_job = run_in_threadpool(_check)
        self._health_job.signals.result.connect(self._on_network_result)
        self._health_job.signals.error.connect(
            lambda m, _tb: self._on_network_result({"rpc": {"ok": False, "error": m, "url": rpc_url}, "cli": {"ok": False, "error": m, "returncode": None}, "explorer": {"ok": False, "error": m}, "ts": time.time()})
        )
        QTimer.singleShot(12_000, lambda: self._ensure_status_resolved())
    def _ensure_status_resolved(self) -> None:
        """If status is still "Checking…", mark as unknown."""
        if "Checking" in self._status_badge.text():
            self._set_status("? Unknown", "#9aa0a6", "Check timed out — will retry.")

    @ui_thread_only(log)
    def _on_network_result(self, result: dict) -> None:
        self._last_health_payload = result
        rpc = result.get("rpc", {})
        cli = result.get("cli", {})
        explorer = result.get("explorer", {})

        rpc_ok = bool(rpc.get("ok"))
        cli_ok = bool(cli.get("ok"))
        exp_ok = bool(explorer.get("ok"))

        if rpc_ok:
            label = "● ONLINE"
            color = "#34a853"
            detail = f"RPC reachable at {rpc.get('url', '')}"
            if exp_ok:
                detail += " | Explorer reachable"
        elif cli_ok:
            label = "⚠ DEGRADED"
            color = "#e8a029"
            detail = (
                f"Node running (CLI), RPC unreachable at {rpc.get('url', '')}. "
                f"Hint: verify host/port/path (/rpc). {rpc.get('error', '')}"
            ).strip()
        else:
            label = "✗ OFFLINE"
            color = "#ea4335"
            parts = []
            if rpc.get("error"):
                parts.append(f"RPC: {rpc.get('error')}")
            if cli.get("error"):
                parts.append(f"CLI: {cli.get('error')}")
            if explorer.get("error"):
                parts.append(f"Explorer: {explorer.get('error')}")
            detail = " | ".join(parts) if parts else "No connection"

        self._set_status(label, color, detail)
        ActivityStore.instance().record_network_check(
            f"Network: {label.strip('●⚠✗ ')}",
            ok=rpc_ok or cli_ok,
            detail=detail,
        )

    @ui_thread_only(log)
    def _copy_diagnostics(self) -> None:
        payload = self._last_health_payload or {}
        rpc = payload.get("rpc", {})
        cli = payload.get("cli", {})
        lines = [
            "Dashboard diagnostics",
            f"rpc_url: {rpc.get('url', '')}",
            f"last_ping_ok: {rpc.get('ok', False)}",
            f"last_ping_error: {rpc.get('error', '')}",
            f"cli_status_ok: {cli.get('ok', False)}",
            f"cli_status_returncode: {cli.get('returncode')}",
            f"cli_status_error: {cli.get('error', '')}",
            f"checked_at: {payload.get('ts', '')}",
        ]
        QGuiApplication.clipboard().setText("\n".join(lines))
        self._status_detail.setText("Diagnostics copied to clipboard")

    @ui_thread_only(log)
    def _set_status(self, label: str, color: str, detail: str) -> None:
        self._status_badge.setText(label)
        self._status_badge.setStyleSheet(f"color: {color};")
        self._status_badge.setToolTip(detail)
        self._status_detail.setText(detail)

    # ------------------------------------------------------------------
    # Total balance
    # ------------------------------------------------------------------

    def refresh_balance(self, *, force: bool = False) -> None:
        """Fetch total balance across all wallets from Explorer."""
        if not self._ensure_ui_thread(lambda: self.refresh_balance(force=force)):
            return
        if self._balance_refresh_in_flight:
            return
        self._balance_refresh_in_flight = True
        profile = self._active_profile()
        if profile is None:
            self._balance_label.setText("—")
            self._balance_meta.setText("No profile configured")
            self._balance_refresh_in_flight = False
            return

        if not (profile.explorer_base_url or "").strip():
            self._balance_label.setText("—")
            self._balance_meta.setText("Explorer not configured")
            self._balance_refresh_in_flight = False
            return

        wallets_path = Path.home() / ".animica" / "wallets.json"
        records = WalletStore().load_local_wallets(wallets_path)
        addresses = [r.address for r in records if r.address]

        if not addresses:
            self._balance_label.setText("0 ANM")
            self._balance_meta.setText("No wallets loaded")
            self._balance_refresh_in_flight = False
            return

        self._balance_label.setText("…")
        self._balance_meta.setText(f"Fetching {len(addresses)} wallet(s)…")

        def _on_total(total: TotalBalanceResult) -> None:
            if not self._ensure_ui_thread(_on_total, total):
                return
            self._balance_refresh_in_flight = False
            if total.error_count > 0 and total.ok_count == 0:
                self._balance_label.setText("—")
                err = total.errors[0] if total.errors else "Explorer unreachable"
                self._balance_meta.setText(f"Explorer error: {err}")
                self._balance_label.setToolTip("; ".join(total.errors))
            else:
                self._balance_label.setText(total.formatted)
                ts_str = time.strftime("%H:%M:%S", time.localtime(total.updated_ts))
                parts = [f"{total.ok_count}/{total.wallet_count} wallets", f"updated {ts_str}"]
                if total.error_count:
                    parts.append(f"{total.error_count} error(s)")
                self._balance_meta.setText(" · ".join(parts))
                self._balance_label.setToolTip(f"Sum of {total.ok_count} wallet(s) from Explorer")

        ExplorerBalanceService.instance().sum_balances(
            addresses,
            profile,
            on_result=_on_total,
            force_refresh=force,
        )

    # ------------------------------------------------------------------
    # Recent activity
    # ------------------------------------------------------------------

    def _refresh_activity(self) -> None:
        entries = ActivityStore.instance().get_recent(20)

        # Clear existing rows
        while self._activity_container.count():
            item = self._activity_container.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not entries:
            self._activity_empty.setVisible(True)
            return

        self._activity_empty.setVisible(False)
        for entry in entries:
            icon = _KIND_ICON.get(entry.kind, "·")
            badge_color = "#34a853" if entry.ok else "#ea4335"
            row = QHBoxLayout()
            row.setSpacing(6)

            badge = QLabel(f"{entry.status_badge}")
            badge.setStyleSheet(f"color: {badge_color}; font-weight: bold; min-width: 14px;")
            badge.setFixedWidth(14)
            row.addWidget(badge)

            icon_lbl = QLabel(icon)
            icon_lbl.setStyleSheet("color: #9aa0a6; min-width: 14px;")
            icon_lbl.setFixedWidth(14)
            row.addWidget(icon_lbl)

            summary_lbl = QLabel(entry.summary)
            summary_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            if entry.detail:
                summary_lbl.setToolTip(entry.detail)
            row.addWidget(summary_lbl, 1)

            age_lbl = QLabel(entry.age_label)
            age_lbl.setStyleSheet("color: #9aa0a6; font-size: 11px;")
            age_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            row.addWidget(age_lbl)

            container = QWidget()
            container.setLayout(row)
            self._activity_container.addWidget(container)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _active_profile(self):
        if self._profile_service is None:
            # Try to get profile from config if no profile_service set
            if self._config is not None:
                try:
                    from animica_studio.services.profile_service import ProfileService  # noqa: PLC0415
                except ImportError:
                    return None
            return None
        try:
            return self._profile_service.get_active()
        except Exception:
            return None

    def refresh(self) -> None:
        """Refresh all dashboard components (called from MainWindow if desired)."""
        self.refresh_network_status()
        self.refresh_balance(force=True)
        self._refresh_activity()

    def set_visual_effects(self, mode: str, reduced_motion: bool) -> None:
        self.hero.set_effect_mode(mode, reduced_motion)

    def closeEvent(self, event: Any) -> None:
        if hasattr(self, "_poll_timer") and self._poll_timer.isActive():
            self._poll_timer.stop()
        if hasattr(self, "_activity_timer") and self._activity_timer.isActive():
            self._activity_timer.stop()
        super().closeEvent(event)
