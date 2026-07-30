"""Main window for the Animica GUI Miner.

Provides tabbed interface for:
- Dashboard: status, mining controls, stats
- Devices: CPU/GPU/ASIC configuration
- Pools/Modes: solo/pool configuration
- Configuration: JSON editor
- Logs: real-time log stream
- Stats/Graphs: hashrate charts
"""

import logging
import subprocess
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QMainWindow,
    QMenuBar,
    QMessageBox,
    QStatusBar,
    QSystemTrayIcon,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from animica_miner_gui.backend.config import load_config, get_default_config_path, MiningAppConfig
from animica_miner_gui.backend.miner_runner import get_runner, MiningEvent
from animica_miner_gui.backend.unified_runner import get_unified_runner
from animica_miner_gui.backend.node_controller import NodeController
from animica_miner_gui.backend.app_paths import get_startup_log_path
from animica_miner_gui.backend.node_paths import resolve_node_executable
from animica_miner_gui.ui.tabs.dashboard import DashboardTab
from animica_miner_gui.ui.tabs.devices import DevicesTab
from animica_miner_gui.ui.tabs.pools import PoolsTab
from animica_miner_gui.ui.tabs.configuration import ConfigurationTab
from animica_miner_gui.ui.tabs.logs import LogsTab
from animica_miner_gui.ui.tabs.stats import StatsTab
from animica_miner_gui.ui.tabs.wallet import WalletTab
from animica_miner_gui.ui.tabs.console import ConsoleTab
from animica_miner_gui.ui.tabs.ide import IDETab

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Main application window."""

    # Emitted by the unified runner's (off-thread) status callback; routed to a
    # main-thread slot so Qt widgets are only touched from the GUI thread.
    unified_status_changed = Signal(str, str)

    def __init__(self, parent: Optional[QWidget] = None, *, unified: bool = False):
        super().__init__(parent)

        # When True the GUI launches straight into unified "mine + AI" mode
        # (e.g. `animica gui full` / `animica gui miner --unified`).
        self._launch_unified = unified

        self.setWindowTitle("Animica Miner")
        self.setMinimumSize(1000, 700)
        
        # Set window icon
        from animica_miner_gui.resources import get_logo_path
        logo_path = get_logo_path()
        if logo_path:
            self.setWindowIcon(QIcon(str(logo_path)))
        
        # Load configuration
        self.config = load_config()
        if self.config._load_warnings:
            self._show_config_repair_notice()

        # Node controller
        self.node_controller = NodeController(self)
        self.node_controller.nodeStarted.connect(self.on_node_started)
        self.node_controller.nodeFailed.connect(self.on_node_failed)
        self.node_controller.statusUpdated.connect(self.on_node_status)
        
        # Apply theme
        self.apply_theme()
        
        # Set up UI
        self.setup_ui()
        self.setup_menu()
        self.setup_status_bar()
        self.setup_system_tray()
        
        # Connect to miner runner (PoW-only path)
        self.miner_runner = get_runner()
        self.miner_runner.add_event_callback(self.on_mining_event)

        # Connect to the unified runner (mine + AI path). It runs the whole
        # animica.unified flow; status is polled in update_ui.
        self.unified_runner = get_unified_runner()
        self.unified_status_changed.connect(self._on_unified_status_changed_main)
        self.unified_runner.add_status_callback(self.on_unified_status_change)

        # Honor the launch-into-unified preference (CLI flag / `animica gui full`).
        if self._launch_unified:
            self.dashboard_tab.unified_checkbox.setChecked(True)
            self.dashboard_tab.refresh_unified_plan()

        # Set up update timer
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_ui)
        self.update_timer.start(1000)  # Update every second

        # Start network services
        self.start_network_services()

        # Auto-start mining if configured
        if self.config.miner.auto_start:
            logger.info("Auto-starting mining")
            self.start_mining()
    
    def apply_theme(self) -> None:
        """Apply dark theme if enabled."""
        if self.config.ui.dark_theme:
            # Simple dark theme stylesheet
            self.setStyleSheet("""
                QMainWindow {
                    background-color: #2b2b2b;
                    color: #ffffff;
                }
                QTabWidget::pane {
                    border: 1px solid #555;
                    background-color: #2b2b2b;
                }
                QTabBar::tab {
                    background-color: #3c3c3c;
                    color: #ffffff;
                    padding: 8px 16px;
                    border: 1px solid #555;
                }
                QTabBar::tab:selected {
                    background-color: #4a4a4a;
                }
                QPushButton {
                    background-color: #3c3c3c;
                    color: #ffffff;
                    border: 1px solid #555;
                    padding: 6px 12px;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #4a4a4a;
                }
                QPushButton:pressed {
                    background-color: #555;
                }
                QLineEdit, QTextEdit, QPlainTextEdit {
                    background-color: #3c3c3c;
                    color: #ffffff;
                    border: 1px solid #555;
                    padding: 4px;
                }
                QLabel {
                    color: #ffffff;
                }
                QGroupBox {
                    border: 1px solid #555;
                    margin-top: 8px;
                    color: #ffffff;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    padding: 0 3px;
                }
            """)
    
    def setup_ui(self) -> None:
        """Set up the main UI components."""
        # Central widget with tab widget
        central_widget = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Create tab widget
        self.tabs = QTabWidget()
        
        # Create tabs
        self.dashboard_tab = DashboardTab(self.config, self.node_controller)
        self.devices_tab = DevicesTab(self.config)
        self.pools_tab = PoolsTab(self.config)
        self.wallet_tab = WalletTab(self.config, self.node_controller)
        self.config_tab = ConfigurationTab(self.config)
        self.logs_tab = LogsTab()
        self.stats_tab = StatsTab()
        self.console_tab = ConsoleTab(self.node_controller)
        self.ide_tab = IDETab(self.config, self.node_controller)
        
        # Add tabs
        self.tabs.addTab(self.dashboard_tab, "Dashboard")
        self.tabs.addTab(self.devices_tab, "Devices")
        self.tabs.addTab(self.pools_tab, "Pools/Modes")
        self.tabs.addTab(self.wallet_tab, "Wallet")
        self.tabs.addTab(self.config_tab, "Configuration")
        self.tabs.addTab(self.logs_tab, "Logs")
        self.tabs.addTab(self.stats_tab, "Stats/Graphs")
        self.tabs.addTab(self.console_tab, "Console")
        self.tabs.addTab(self.ide_tab, "IDE")
        
        layout.addWidget(self.tabs)
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)
        
        # Connect dashboard signals
        self.dashboard_tab.start_mining_requested.connect(self.start_mining)
        self.dashboard_tab.stop_mining_requested.connect(self.stop_mining)

        self.node_controller.statusUpdated.connect(self.logs_tab.update_node_log_line)

    def closeEvent(self, event) -> None:  # noqa: D401 - Qt override
        """Prompt for unsaved IDE changes on close."""
        if hasattr(self, "ide_tab") and not self.ide_tab.prompt_close():
            event.ignore()
            return
        super().closeEvent(event)
    
    def setup_menu(self) -> None:
        """Set up the menu bar."""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("&File")
        
        restart_wizard_action = QAction("&Restart Setup Wizard", self)
        restart_wizard_action.triggered.connect(self.restart_wizard)
        file_menu.addAction(restart_wizard_action)
        
        file_menu.addSeparator()
        
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # Mining menu
        mining_menu = menubar.addMenu("&Mining")
        
        start_action = QAction("&Start Mining", self)
        start_action.setShortcut("F5")
        start_action.triggered.connect(self.start_mining)
        mining_menu.addAction(start_action)
        
        stop_action = QAction("S&top Mining", self)
        stop_action.setShortcut("F6")
        stop_action.triggered.connect(self.stop_mining)
        mining_menu.addAction(stop_action)
        
        # Help menu
        help_menu = menubar.addMenu("&Help")
        
        about_action = QAction("&About", self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
        
        diagnostics_action = QAction("Copy &Diagnostics", self)
        diagnostics_action.triggered.connect(self.copy_diagnostics)
        help_menu.addAction(diagnostics_action)
    
    def setup_status_bar(self) -> None:
        """Set up the status bar."""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
    
    def setup_system_tray(self) -> None:
        """Set up system tray icon."""
        if not self.config.ui.show_system_tray:
            return
        
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.warning("System tray not available")
            return
        
        self.tray_icon = QSystemTrayIcon(self)
        
        # Set tray icon to logo
        from animica_miner_gui.resources import get_logo_path
        logo_path = get_logo_path()
        if logo_path:
            self.tray_icon.setIcon(QIcon(str(logo_path)))
        
        self.tray_icon.setToolTip("Animica Miner")
        
        # Tray menu
        from PySide6.QtWidgets import QMenu
        tray_menu = QMenu()
        
        show_action = tray_menu.addAction("Show")
        show_action.triggered.connect(self.show)
        
        tray_menu.addSeparator()
        
        start_action = tray_menu.addAction("Start Mining")
        start_action.triggered.connect(self.start_mining)
        
        stop_action = tray_menu.addAction("Stop Mining")
        stop_action.triggered.connect(self.stop_mining)
        
        tray_menu.addSeparator()
        
        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(self.quit_app)
        
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self.tray_icon_activated)
        self.tray_icon.show()
    
    def tray_icon_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon activation."""
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show()
            self.activateWindow()
    
    def _unified_mode_active(self) -> bool:
        """Whether the dashboard's "mine + AI" toggle is on."""
        return self.dashboard_tab.unified_enabled()

    def _any_mining_running(self) -> bool:
        """True if either the PoW-only or unified flow is running."""
        return self.miner_runner.is_running() or self.unified_runner.is_running()

    def start_mining(self) -> None:
        """Start mining.

        Routes to the unified "mine + AI" flow (animica.unified) when the
        dashboard toggle is on, otherwise the PoW-only miner runner.
        """
        if self.miner_runner.is_running() or self.unified_runner.is_running():
            logger.info("Mining already running")
            return

        if self._unified_mode_active():
            self._start_unified_mining()
            return

        logger.info("Starting mining (proof-of-work only)")
        config_dict = self.config.model_dump()

        if self.miner_runner.start(config_dict):
            self.status_bar.showMessage("Mining started")
            if self.config.ui.notifications_enabled and hasattr(self, 'tray_icon'):
                self.tray_icon.showMessage(
                    "Animica Miner",
                    "Mining started",
                    QSystemTrayIcon.MessageIcon.Information,
                    3000
                )
        else:
            self.status_bar.showMessage("Failed to start mining")
            QMessageBox.warning(
                self,
                "Mining Error",
                "Failed to start mining. Check logs for details."
            )

    def _start_unified_mining(self) -> None:
        """Start the full unified flow (PoW + AI), bound to the ANM address."""
        logger.info("Starting mining (unified: mine + AI)")
        # Everything binds to the configured payout address; the runner
        # auto-creates a wallet via the same flow if none is configured.
        cfg = {
            "address": self.config.miner.payout_address or "",
            "threads": self.config.cpu.threads,
        }
        if self.unified_runner.start(cfg):
            # The runner may have auto-created/resolved an address; reflect it.
            snap = self.unified_runner.status_dict()
            resolved = snap.get("address")
            if resolved and resolved != self.config.miner.payout_address:
                self.config.miner.payout_address = resolved
                self.dashboard_tab.payout_label.setText(resolved)
            self.dashboard_tab.on_unified_status(snap)
            self.status_bar.showMessage("Unified mining started (mine + AI)")
            if self.config.ui.notifications_enabled and hasattr(self, 'tray_icon'):
                self.tray_icon.showMessage(
                    "Animica Miner",
                    "Unified mining started (mine + AI)",
                    QSystemTrayIcon.MessageIcon.Information,
                    3000
                )
        else:
            snap = self.unified_runner.status_dict()
            self.dashboard_tab.on_unified_status(snap)
            self.status_bar.showMessage("Failed to start unified mining")
            QMessageBox.warning(
                self,
                "Mining Error",
                "Failed to start unified mining (mine + AI). "
                f"{snap.get('error', 'Check logs for details.')}"
            )

    def stop_mining(self) -> None:
        """Stop whichever mining flow is running (PoW-only or unified)."""
        if self.unified_runner.is_running():
            logger.info("Stopping unified mining")
            if self.unified_runner.stop():
                self.dashboard_tab.on_unified_status(self.unified_runner.status_dict())
                self.status_bar.showMessage("Mining stopped")
            else:
                self.status_bar.showMessage("Failed to stop mining")
            return

        if not self.miner_runner.is_running():
            logger.info("Mining not running")
            return

        logger.info("Stopping mining")

        if self.miner_runner.stop():
            self.status_bar.showMessage("Mining stopped")
        else:
            self.status_bar.showMessage("Failed to stop mining")
    
    def on_mining_event(self, event: MiningEvent) -> None:
        """Handle mining events."""
        # Forward events to tabs
        self.dashboard_tab.on_mining_event(event)
        self.logs_tab.on_mining_event(event)
        self.stats_tab.on_mining_event(event)
        
        # Handle notifications
        if self.config.ui.notifications_enabled and hasattr(self, 'tray_icon'):
            from animica_miner_gui.backend.miner_runner import EventType
            
            if event.event_type == EventType.BLOCK_FOUND:
                self.tray_icon.showMessage(
                    "Block Found!",
                    f"Block #{event.data.get('height', '?')} mined successfully!",
                    QSystemTrayIcon.MessageIcon.Information,
                    5000
                )
            elif event.event_type == EventType.ERROR:
                self.tray_icon.showMessage(
                    "Mining Error",
                    event.data.get('error', 'Unknown error'),
                    QSystemTrayIcon.MessageIcon.Warning,
                    5000
                )
    
    def on_unified_status_change(self, status_value: str, reason: str) -> None:
        """Unified runner status callback (may be called off the main thread).

        Defer to the main thread via a queued signal so Qt widgets are only
        touched from the GUI thread.
        """
        self.unified_status_changed.emit(status_value, reason)

    @Slot(str, str)
    def _on_unified_status_changed_main(self, status_value: str, reason: str) -> None:
        """Main-thread handler for unified status changes."""
        snap = self.unified_runner.status_dict()
        self.dashboard_tab.on_unified_status(snap)
        msg = f"Unified: {status_value}"
        if reason:
            msg += f" ({reason})"
        self.status_bar.showMessage(msg)

    def update_ui(self) -> None:
        """Update UI periodically."""
        # When unified mining is active, show its component status and stop here;
        # the PoW-only stats below do not apply.
        if self.unified_runner.is_running():
            snap = self.unified_runner.status_dict()
            self.dashboard_tab.on_unified_status(snap)
            running = snap.get("will_run", [])
            self.status_bar.showMessage(
                "Unified mining (mine + AI): " + (", ".join(running) or "starting")
            )
            return

        # Update status bar
        stats = self.miner_runner.get_stats()
        status = stats['status']

        if status == 'running':
            hashrate = stats['hashrate']
            if hashrate >= 1e9:
                hr_str = f"{hashrate/1e9:.2f} GH/s"
            elif hashrate >= 1e6:
                hr_str = f"{hashrate/1e6:.2f} MH/s"
            elif hashrate >= 1e3:
                hr_str = f"{hashrate/1e3:.2f} KH/s"
            else:
                hr_str = f"{hashrate:.0f} H/s"
            
            self.status_bar.showMessage(
                f"Mining: {hr_str} | Blocks: {stats['blocks']}"
            )
        else:
            self.status_bar.showMessage(f"Status: {status}")
    
    def show_about(self) -> None:
        """Show about dialog."""
        from animica_miner_gui import __version__, bundled_animica_version

        QMessageBox.about(
            self,
            "About Animica Miner",
            "<h3>Animica Miner</h3>"
            "<p>Production-quality Qt desktop GUI miner for Animica blockchain.</p>"
            f"<p>Version: {__version__}</p>"
            f"<p>Bundled animica: {bundled_animica_version()}</p>"
            "<p>© 2026 Animica</p>"
        )
    
    def copy_diagnostics(self) -> None:
        """Copy diagnostics to clipboard."""
        from PySide6.QtWidgets import QApplication
        import platform
        
        from animica_miner_gui import __version__, bundled_animica_version

        # Gather diagnostics
        diagnostics = []
        diagnostics.append("=== Animica Miner Diagnostics ===")
        diagnostics.append(f"Version: {__version__}")
        diagnostics.append(f"Bundled animica: {bundled_animica_version()}")
        diagnostics.append(f"Platform: {platform.system()} {platform.release()}")
        diagnostics.append(f"Python: {platform.python_version()}")
        diagnostics.append("")
        
        # Config summary
        diagnostics.append("=== Configuration ===")
        diagnostics.append(f"Network: {self.config.network.network_type.value}")
        diagnostics.append(f"RPC URL: {self.config.network.resolved_rpc_url()}")
        diagnostics.append(f"Mining Mode: {self.config.miner.mining_mode.value}")
        diagnostics.append(f"CPU Threads: {self.config.cpu.threads}")
        diagnostics.append(f"GPU Count: {len(self.config.gpus)}")
        diagnostics.append("")
        
        # Device detection
        diagnostics.append("=== Devices ===")
        from animica_miner_gui.backend.device_detection import detect_all
        detection = detect_all()
        diagnostics.append(f"CPU: {detection.cpu.model_name} ({detection.cpu.threads} threads)")
        diagnostics.append(f"GPUs: {len(detection.gpus)}")
        for gpu in detection.gpus:
            diagnostics.append(f"  - {gpu.name} ({gpu.memory_mb} MB)")
        diagnostics.append("")
        
        # Mining stats
        diagnostics.append("=== Mining Stats ===")
        stats = self.miner_runner.get_stats()
        for key, value in stats.items():
            diagnostics.append(f"{key}: {value}")
        diagnostics.append("")
        
        # Recent logs
        diagnostics.append("=== Recent Logs (last 50 lines) ===")
        logs = self.logs_tab.get_recent_logs(50)
        diagnostics.extend(logs)
        
        # Copy to clipboard
        text = "\n".join(diagnostics)
        QApplication.clipboard().setText(text)
        
        self.status_bar.showMessage("Diagnostics copied to clipboard", 3000)
        QMessageBox.information(
            self,
            "Diagnostics Copied",
            "Diagnostics information has been copied to clipboard."
        )
    
    def quit_app(self) -> None:
        """Quit the application."""
        if self._any_mining_running():
            reply = QMessageBox.question(
                self,
                "Confirm Quit",
                "Mining is currently running. Stop mining and quit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self.stop_mining()
                self.close()
        else:
            self.close()

    def raise_and_activate(self) -> None:
        """Raise the main window and bring it to foreground."""
        self.show()
        self.raise_()
        self.activateWindow()

    def on_node_started(self, rpc_url: str, token: str) -> None:
        """Handle node started event."""
        self.config.network.rpc_url = rpc_url
        logger.info("Node started. rpc_url=%s", rpc_url)

    def start_network_services(self) -> None:
        """Initialize RPC connectivity based on config mode."""
        self.status_bar.showMessage("Starting node...")
        self.node_controller.start()

    def _show_config_repair_notice(self) -> None:
        """Show dialog about repaired config file."""
        config_path = get_default_config_path()
        startup_log = get_startup_log_path()
        message = (
            "Config invalid, repaired defaults applied.\n\n"
            f"Config path: {config_path}\n"
            f"Startup log: {startup_log}"
        )
        QMessageBox.warning(self, "Configuration Repaired", message)

    def on_node_failed(self, error: str) -> None:
        """Handle node start failure."""
        logger.error("Node failed: %s", error)
        node_paths = resolve_node_executable()
        if node_paths.exe_path is None:
            self._show_node_missing_dialog(error, node_paths.reason, node_paths.mode)
            return
        QMessageBox.critical(
            self,
            "Node Error",
            f"Node bundle missing/broken or failed to start.\n{error}",
        )

    def on_node_status(self, status: dict) -> None:
        """Update status bar with node info if available."""
        _ = status

    def _show_node_missing_dialog(self, error: str, details: str, mode: str) -> None:
        log_path = get_startup_log_path()
        message = (
            "Node bundle missing/broken or failed to start.\n\n"
            f"{error}\n\n"
            f"Startup log: {log_path}\n"
        )
        if mode != "dev":
            message += (
                "\nThe packaged app is missing its bundled node payload. "
                "Please reinstall or rebuild the application."
            )
            QMessageBox.critical(self, "Node Missing", message)
            return

        message += (
            "\nThis is a development build. You can build and install the bundled node now."
        )
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Node Missing")
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setText(message)
        dialog.setDetailedText(details)
        build_button = dialog.addButton("Build node now", QMessageBox.ButtonRole.AcceptRole)
        dialog.addButton(QMessageBox.StandardButton.Close)
        dialog.exec()

        if dialog.clickedButton() is not build_button:
            return

        repo_root = Path(__file__).resolve().parents[4]
        script_path = repo_root / "ops" / "build" / "install_node_payload_for_gui.sh"
        try:
            subprocess.run([str(script_path)], check=True, cwd=str(repo_root))
        except Exception as exc:
            QMessageBox.critical(
                self,
                "Node Build Failed",
                f"Failed to build/install node payload.\n{exc}\n\n"
                f"See logs at: {log_path}",
            )
            return

        QMessageBox.information(
            self,
            "Node Installed",
            "Node payload installed. The GUI will retry starting the node.",
        )
        self.node_controller.start()
    
    def restart_wizard(self) -> None:
        """Restart the setup wizard."""
        # Confirm with user
        reply = QMessageBox.question(
            self,
            "Restart Setup Wizard",
            "This will stop mining (if running) and restart the setup wizard. Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        
        if reply != QMessageBox.StandardButton.Yes:
            return
        
        # Stop mining if running
        if self._any_mining_running():
            self.stop_mining()

        # Hide main window
        self.hide()
        
        # Show wizard
        from animica_miner_gui.ui.wizard import FirstRunWizard
        wizard = FirstRunWizard()
        if wizard.exec() == wizard.DialogCode.Accepted:
            # Reload configuration
            from animica_miner_gui.backend.config import load_config
            self.config = load_config()
            if self.config._load_warnings:
                self._show_config_repair_notice()
            
            # Update all tabs with new config
            self.dashboard_tab.config = self.config
            self.devices_tab.config = self.config
            self.pools_tab.config = self.config
            self.wallet_tab.config = self.config
            self.config_tab.config = self.config
            
            # Refresh dashboard and wallet
            self.dashboard_tab.payout_label.setText(
                self.config.miner.payout_address or "--"
            )
            self.wallet_tab.address_label.setText(
                self.config.miner.payout_address or "Not configured"
            )
            
            # Show main window again
            self.show()
            
            # Auto-start if configured
            if self.config.miner.auto_start:
                self.start_mining()
        else:
            # Wizard was cancelled, show main window again
            self.show()
    
    def closeEvent(self, event) -> None:
        """Handle window close event."""
        if self.config.ui.minimize_to_tray and hasattr(self, 'tray_icon'):
            event.ignore()
            self.hide()
            if self.config.ui.notifications_enabled:
                self.tray_icon.showMessage(
                    "Animica Miner",
                    "Application minimized to tray",
                    QSystemTrayIcon.MessageIcon.Information,
                    2000
                )
        else:
            # Stop mining before closing
            if self._any_mining_running():
                self.stop_mining()
            self.node_controller.stop()
            event.accept()
