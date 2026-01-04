"""Main window for the Animica GUI Miner.

Provides tabbed interface for:
- Dashboard: status, mining controls, stats
- Devices: CPU/GPU/ASIC configuration
- Pools/Modes: solo/pool configuration
- Configuration: JSON editor
- Logs: real-time log stream
- Stats/Graphs: hashrate and shares charts
"""

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
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

from animica_miner_gui.backend.config import load_config, MiningAppConfig
from animica_miner_gui.backend.miner_runner import get_runner, MiningEvent
from animica_miner_gui.ui.tabs.dashboard import DashboardTab
from animica_miner_gui.ui.tabs.devices import DevicesTab
from animica_miner_gui.ui.tabs.pools import PoolsTab
from animica_miner_gui.ui.tabs.configuration import ConfigurationTab
from animica_miner_gui.ui.tabs.logs import LogsTab
from animica_miner_gui.ui.tabs.stats import StatsTab

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """Main application window."""
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        self.setWindowTitle("Animica Miner")
        self.setMinimumSize(1000, 700)
        
        # Load configuration
        self.config = load_config()
        
        # Apply theme
        self.apply_theme()
        
        # Set up UI
        self.setup_ui()
        self.setup_menu()
        self.setup_status_bar()
        self.setup_system_tray()
        
        # Connect to miner runner
        self.miner_runner = get_runner()
        self.miner_runner.add_event_callback(self.on_mining_event)
        
        # Set up update timer
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_ui)
        self.update_timer.start(1000)  # Update every second
        
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
        self.dashboard_tab = DashboardTab(self.config)
        self.devices_tab = DevicesTab(self.config)
        self.pools_tab = PoolsTab(self.config)
        self.config_tab = ConfigurationTab(self.config)
        self.logs_tab = LogsTab()
        self.stats_tab = StatsTab()
        
        # Add tabs
        self.tabs.addTab(self.dashboard_tab, "Dashboard")
        self.tabs.addTab(self.devices_tab, "Devices")
        self.tabs.addTab(self.pools_tab, "Pools/Modes")
        self.tabs.addTab(self.config_tab, "Configuration")
        self.tabs.addTab(self.logs_tab, "Logs")
        self.tabs.addTab(self.stats_tab, "Stats/Graphs")
        
        layout.addWidget(self.tabs)
        central_widget.setLayout(layout)
        self.setCentralWidget(central_widget)
        
        # Connect dashboard signals
        self.dashboard_tab.start_mining_requested.connect(self.start_mining)
        self.dashboard_tab.stop_mining_requested.connect(self.stop_mining)
    
    def setup_menu(self) -> None:
        """Set up the menu bar."""
        menubar = self.menuBar()
        
        # File menu
        file_menu = menubar.addMenu("&File")
        
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
        # Use a simple icon (in production, use proper icon file)
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
    
    def start_mining(self) -> None:
        """Start the mining process."""
        if self.miner_runner.is_running():
            logger.info("Mining already running")
            return
        
        logger.info("Starting mining")
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
    
    def stop_mining(self) -> None:
        """Stop the mining process."""
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
    
    def update_ui(self) -> None:
        """Update UI periodically."""
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
                f"Mining: {hr_str} | Shares: {stats['shares']} | Blocks: {stats['blocks']}"
            )
        else:
            self.status_bar.showMessage(f"Status: {status}")
    
    def show_about(self) -> None:
        """Show about dialog."""
        QMessageBox.about(
            self,
            "About Animica Miner",
            "<h3>Animica Miner</h3>"
            "<p>Production-quality Qt desktop GUI miner for Animica blockchain.</p>"
            "<p>Version: 0.1.0</p>"
            "<p>© 2026 Animica</p>"
        )
    
    def copy_diagnostics(self) -> None:
        """Copy diagnostics to clipboard."""
        from PySide6.QtWidgets import QApplication
        import platform
        
        # Gather diagnostics
        diagnostics = []
        diagnostics.append("=== Animica Miner Diagnostics ===")
        diagnostics.append(f"Version: 0.1.0")
        diagnostics.append(f"Platform: {platform.system()} {platform.release()}")
        diagnostics.append(f"Python: {platform.python_version()}")
        diagnostics.append("")
        
        # Config summary
        diagnostics.append("=== Configuration ===")
        diagnostics.append(f"Network: {self.config.network.network_type.value}")
        diagnostics.append(f"RPC URL: {self.config.network.rpc_url}")
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
        if self.miner_runner.is_running():
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
            if self.miner_runner.is_running():
                self.stop_mining()
            event.accept()
