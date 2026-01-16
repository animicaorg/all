"""Node control tab for managing the local Animica node.

Provides:
- Node status display
- Start/Stop/Restart controls
- Sync status and progress
- Console for CLI commands
- Quick actions (open data dir, view logs)
"""

import logging
from typing import Optional, Callable

from PySide6.QtCore import Qt, QTimer, Signal, QUrl, QEvent
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)

from animica_miner_gui.core.localnode import (
    LocalNodeManager,
    LocalRpcClient,
    NodeStatus,
    SyncStatus,
)
from animica_miner_gui.core.localnode.console import ConsoleCommandExecutor
from animica_miner_gui.backend.console_router import (
    ConsoleResult,
    run_console_command,
    set_rpc_client,
)
from animica_miner_gui.backend.config import MiningAppConfig

logger = logging.getLogger(__name__)


class NodeTab(QWidget):
    """Node control and monitoring tab."""
    
    # Signals
    node_ready = Signal()
    node_stopped = Signal()
    
    def __init__(self, config: MiningAppConfig, node_manager: LocalNodeManager):
        super().__init__()
        
        self.config = config
        self.node_manager = node_manager
        self.console_history: list[str] = []
        self.console_history_index = 0
        
        self.setup_ui()
        
        # Update timer
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_status)
        self.update_timer.start(2000)  # Update every 2 seconds
        
        # Initial update
        self.update_status()
    
    def setup_ui(self) -> None:
        """Set up the UI."""
        layout = QVBoxLayout()
        
        # Node status group
        status_group = QGroupBox("Node Status")
        status_layout = QVBoxLayout()
        
        # Status display
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("Status:"))
        self.status_label = QLabel("Stopped")
        self.status_label.setStyleSheet("font-weight: bold;")
        status_row.addWidget(self.status_label)
        status_row.addStretch()
        status_layout.addLayout(status_row)
        
        # PID and port
        info_row = QHBoxLayout()
        info_row.addWidget(QLabel("Process:"))
        self.pid_label = QLabel("N/A")
        info_row.addWidget(self.pid_label)
        info_row.addWidget(QLabel("  Port:"))
        self.port_label = QLabel("N/A")
        info_row.addWidget(self.port_label)
        info_row.addStretch()
        status_layout.addLayout(info_row)
        
        # Control buttons
        control_row = QHBoxLayout()
        self.start_btn = QPushButton("Start Node")
        self.start_btn.clicked.connect(self.start_node)
        control_row.addWidget(self.start_btn)
        
        self.stop_btn = QPushButton("Stop Node")
        self.stop_btn.clicked.connect(self.stop_node)
        self.stop_btn.setEnabled(False)
        control_row.addWidget(self.stop_btn)
        
        self.restart_btn = QPushButton("Restart Node")
        self.restart_btn.clicked.connect(self.restart_node)
        self.restart_btn.setEnabled(False)
        control_row.addWidget(self.restart_btn)
        
        control_row.addStretch()
        status_layout.addLayout(control_row)
        
        status_group.setLayout(status_layout)
        layout.addWidget(status_group)
        
        # Sync status group
        sync_group = QGroupBox("Blockchain Sync")
        sync_layout = QVBoxLayout()
        
        # Sync info
        sync_info_row = QHBoxLayout()
        sync_info_row.addWidget(QLabel("Current Height:"))
        self.current_height_label = QLabel("0")
        sync_info_row.addWidget(self.current_height_label)
        sync_info_row.addWidget(QLabel("  Best Height:"))
        self.best_height_label = QLabel("0")
        sync_info_row.addWidget(self.best_height_label)
        sync_info_row.addWidget(QLabel("  Peers:"))
        self.peers_label = QLabel("0")
        sync_info_row.addWidget(self.peers_label)
        sync_info_row.addStretch()
        sync_layout.addLayout(sync_info_row)
        
        # Progress bar
        self.sync_progress = QProgressBar()
        self.sync_progress.setMinimum(0)
        self.sync_progress.setMaximum(100)
        self.sync_progress.setValue(0)
        sync_layout.addWidget(self.sync_progress)
        
        # Phase
        phase_row = QHBoxLayout()
        phase_row.addWidget(QLabel("Phase:"))
        self.phase_label = QLabel("Idle")
        phase_row.addWidget(self.phase_label)
        phase_row.addStretch()
        sync_layout.addLayout(phase_row)
        
        sync_group.setLayout(sync_layout)
        layout.addWidget(sync_group)
        
        # Console group
        console_group = QGroupBox("CLI Console")
        console_layout = QVBoxLayout()
        
        # Output
        self.console_output = QTextEdit()
        self.console_output.setReadOnly(True)
        self.console_output.setMaximumHeight(200)
        self.console_output.setPlaceholderText("Console output will appear here...")
        console_layout.addWidget(self.console_output)
        
        # Input
        input_row = QHBoxLayout()
        input_row.addWidget(QLabel("Command:"))
        self.console_input = QLineEdit()
        self.console_input.setPlaceholderText("Enter command (e.g. animica node status, rpc chain.getHead [])...")
        self.console_input.returnPressed.connect(self.execute_command)
        self.console_input.installEventFilter(self)
        input_row.addWidget(self.console_input)
        
        self.exec_btn = QPushButton("Execute")
        self.exec_btn.clicked.connect(self.execute_command)
        input_row.addWidget(self.exec_btn)
        
        console_layout.addLayout(input_row)
        
        console_group.setLayout(console_layout)
        layout.addWidget(console_group)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def start_node(self) -> None:
        """Start the local node."""
        if self.node_manager.is_running:
            self.console_output.append("<b>Node is already running</b>")
            return
        
        self.console_output.append("<b>Starting node...</b>")
        self.start_btn.setEnabled(False)
        
        try:
            status = self.node_manager.start()
            
            if status.is_ready:
                self.console_output.append(f"<b style='color: green;'>✓ Node started successfully on port {status.port}</b>")
                self.node_ready.emit()
                
                set_rpc_client(self.node_manager.get_rpc_client())
            else:
                error_msg = status.error or "Unknown error"
                self.console_output.append(f"<b style='color: red;'>✗ Failed to start node: {error_msg}</b>")
                self.show_node_failure_dialog(error_msg)
        
        except Exception as e:
            logger.error(f"Error starting node: {e}")
            self.console_output.append(f"<b style='color: red;'>✗ Error: {e}</b>")
        
        finally:
            self.update_status()
    
    def stop_node(self) -> None:
        """Stop the local node."""
        self.console_output.append("<b>Stopping node...</b>")
        self.stop_btn.setEnabled(False)
        
        try:
            self.node_manager.stop()
            self.console_output.append("<b style='color: orange;'>Node stopped</b>")
            set_rpc_client(None)
            self.node_stopped.emit()
        
        except Exception as e:
            logger.error(f"Error stopping node: {e}")
            self.console_output.append(f"<b style='color: red;'>✗ Error: {e}</b>")
        
        finally:
            self.update_status()
    
    def restart_node(self) -> None:
        """Restart the local node."""
        self.console_output.append("<b>Restarting node...</b>")
        self.restart_btn.setEnabled(False)
        
        try:
            status = self.node_manager.restart()
            
            if status.is_ready:
                self.console_output.append(f"<b style='color: green;'>✓ Node restarted on port {status.port}</b>")
                
                set_rpc_client(self.node_manager.get_rpc_client())
            else:
                error_msg = status.error or "Unknown error"
                self.console_output.append(f"<b style='color: red;'>✗ Failed to restart: {error_msg}</b>")
                self.show_node_failure_dialog(error_msg)
        
        except Exception as e:
            logger.error(f"Error restarting node: {e}")
            self.console_output.append(f"<b style='color: red;'>✗ Error: {e}</b>")
        
        finally:
            self.update_status()
    
    def execute_command(self) -> None:
        """Execute a CLI command."""
        if not self.node_manager.is_ready:
            self.console_output.append("<b style='color: red;'>✗ Node must be running to execute commands</b>")
            return
        
        command = self.console_input.text().strip()
        if not command:
            return
        
        # Check for dangerous commands
        is_dangerous, reason = ConsoleCommandExecutor.is_dangerous_command(command)
        if is_dangerous:
            from PySide6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                "Confirm Dangerous Command",
                f"Warning: {reason}\n\nDo you want to proceed?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
        
        self.console_output.append(f"<b>$ {command}</b>")
        self.console_input.clear()
        self.exec_btn.setEnabled(False)
        
        try:
            result = run_console_command(command)
            self._append_console_result(result, command)
        
        except Exception as e:
            logger.error(f"Error executing command: {e}")
            self.console_output.append(f"<b style='color: red;'>✗ Error: {e}</b>")
        
        finally:
            self.exec_btn.setEnabled(True)
            # Auto-scroll to bottom
            self.console_output.verticalScrollBar().setValue(
                self.console_output.verticalScrollBar().maximum()
            )
    
    def update_status(self) -> None:
        """Update the status display."""
        status = self.node_manager.get_status()
        
        # Update status label
        if status.is_ready:
            self.status_label.setText("Ready")
            self.status_label.setStyleSheet("color: green; font-weight: bold;")
        elif status.is_running:
            self.status_label.setText("Starting...")
            self.status_label.setStyleSheet("color: orange; font-weight: bold;")
        elif status.state.value == "error":
            self.status_label.setText(f"Error: {status.error}")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
        else:
            self.status_label.setText("Stopped")
            self.status_label.setStyleSheet("color: gray; font-weight: bold;")
        
        # Update PID and port
        self.pid_label.setText(str(status.pid) if status.pid else "N/A")
        self.port_label.setText(str(status.port) if status.port else "N/A")
        
        # Update buttons
        self.start_btn.setEnabled(not status.is_running)
        self.stop_btn.setEnabled(status.is_running)
        self.restart_btn.setEnabled(status.is_running)
        self.exec_btn.setEnabled(status.is_ready)
        self.console_input.setEnabled(status.is_ready)
        
        # Update sync status
        if status.is_ready:
            set_rpc_client(self.node_manager.get_rpc_client())
            sync_status = self.node_manager.get_sync_status()
            if sync_status:
                self.current_height_label.setText(str(sync_status.current_height))
                self.best_height_label.setText(str(sync_status.best_height))
                self.peers_label.setText(str(sync_status.peer_count))
                self.phase_label.setText(sync_status.phase)
                self.sync_progress.setValue(int(sync_status.progress_percent))
                
                if sync_status.is_synced:
                    self.phase_label.setStyleSheet("color: green;")
                else:
                    self.phase_label.setStyleSheet("color: orange;")
        else:
            self.current_height_label.setText("N/A")
            self.best_height_label.setText("N/A")
            self.peers_label.setText("N/A")
            self.phase_label.setText("N/A")
            self.sync_progress.setValue(0)
            self.phase_label.setStyleSheet("")

    def eventFilter(self, obj, event):
        if obj is self.console_input and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Up:
                self._navigate_history(-1)
                return True
            if event.key() == Qt.Key_Down:
                self._navigate_history(1)
                return True
        return super().eventFilter(obj, event)

    def _navigate_history(self, direction: int) -> None:
        if not self.console_history:
            return

        if direction < 0 and self.console_history_index > 0:
            self.console_history_index -= 1
        elif direction > 0 and self.console_history_index < len(self.console_history):
            self.console_history_index += 1

        if self.console_history_index >= len(self.console_history):
            self.console_input.setText("")
            return

        self.console_input.setText(self.console_history[self.console_history_index])

    def _append_console_result(self, result: ConsoleResult, command: str) -> None:
        import html

        if result.ok:
            output = html.escape(result.output)
            self.console_output.append(f"<pre>{output}</pre>")
        else:
            output = html.escape(result.output)
            self.console_output.append(f"<pre style='color: red;'>{output}</pre>")

        self._record_history(command)

    def _record_history(self, command: str) -> None:
        if not command:
            return
        if not self.console_history or self.console_history[-1] != command:
            self.console_history.append(command)
        self.console_history_index = len(self.console_history)

    def show_node_failure_dialog(
        self,
        error: str,
        retry_callback: Optional[Callable[[], None]] = None,
    ) -> None:
        """Show a modal dialog with node failure details and actions."""
        details = self.node_manager.proc_manager.pop_last_failure_details()
        if details is None:
            QMessageBox.warning(self, "Node Error", error)
            return

        exit_code = details.exit_code if details.exit_code is not None else "unknown"
        header = f"Node exited (code {exit_code})."
        stderr_text = details.stderr_tail or "(no stderr output)"
        stdout_text = details.stdout_tail or "(no stdout output)"
        log_dir = details.stderr_log.parent if details.stderr_log else None

        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Critical)
        msg.setWindowTitle("Node Exited")
        msg.setText(header)
        msg.setInformativeText(
            "Last stderr lines:\n"
            f"{stderr_text}\n\n"
            "Last stdout lines:\n"
            f"{stdout_text}"
        )

        open_btn = msg.addButton("Open full logs", QMessageBox.ActionRole)
        copy_btn = msg.addButton("Copy", QMessageBox.ActionRole)
        retry_btn = msg.addButton("Retry", QMessageBox.AcceptRole)
        msg.addButton("Close", QMessageBox.RejectRole)

        msg.exec()
        clicked = msg.clickedButton()

        if clicked == open_btn and log_dir:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(log_dir)))
        elif clicked == copy_btn:
            clipboard = QApplication.clipboard()
            clipboard.setText(
                f"{header}\n"
                f"Reason: {details.reason}\n"
                f"Node path: {details.node_path}\n"
                f"ARGV: {details.argv}\n"
                f"CWD: {details.cwd}\n"
                f"ENV_DELTAS: {details.env_deltas}\n"
                f"STDERR (tail):\n{stderr_text}\n\n"
                f"STDOUT (tail):\n{stdout_text}\n"
            )
        elif clicked == retry_btn:
            if retry_callback is not None:
                retry_callback()
            else:
                self.start_node()
