"""Logs tab - real-time log stream with filtering."""

import logging
from collections import deque
from typing import List, Optional

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_miner_gui.backend.miner_runner import MiningEvent, EventType

logger = logging.getLogger(__name__)


class LogsTab(QWidget):
    """Logs viewing tab with filtering."""
    
    # Signal for thread-safe event handling
    mining_event_received = Signal(object)  # MiningEvent
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.log_buffer = deque(maxlen=1000)  # Keep last 1000 log entries
        self._last_node_log_line: Optional[str] = None
        self.setup_ui()
        
        # Connect signal to slot for thread-safe UI updates
        self.mining_event_received.connect(self._handle_mining_event_in_main_thread)
    
    def setup_ui(self) -> None:
        """Set up the UI."""
        layout = QVBoxLayout()
        
        # Filter controls
        filter_layout = QHBoxLayout()
        
        filter_layout.addWidget(QLabel("Level:"))
        self.level_combo = QComboBox()
        self.level_combo.addItems(["All", "Debug", "Info", "Warning", "Error"])
        self.level_combo.currentTextChanged.connect(self.apply_filters)
        filter_layout.addWidget(self.level_combo)
        
        filter_layout.addWidget(QLabel("Search:"))
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Filter logs...")
        self.search_input.textChanged.connect(self.apply_filters)
        filter_layout.addWidget(self.search_input)
        
        clear_btn = QPushButton("Clear")
        clear_btn.clicked.connect(self.clear_logs)
        filter_layout.addWidget(clear_btn)
        
        export_btn = QPushButton("Export")
        export_btn.clicked.connect(self.export_logs)
        filter_layout.addWidget(export_btn)
        
        layout.addLayout(filter_layout)
        
        # Log display
        self.log_display = QTextEdit()
        self.log_display.setReadOnly(True)
        self.log_display.setFontFamily("monospace")
        layout.addWidget(self.log_display)
        
        # Auto-scroll checkbox
        self.autoscroll_check = QCheckBox("Auto-scroll")
        self.autoscroll_check.setChecked(True)
        layout.addWidget(self.autoscroll_check)
        
        self.setLayout(layout)
    
    def on_mining_event(self, event: MiningEvent) -> None:
        """Handle mining events from any thread.
        
        This method can be called from background threads, so it emits a signal
        to ensure UI updates happen in the main thread.
        """
        # Emit signal to handle in main thread
        self.mining_event_received.emit(event)
    
    @Slot(object)
    def _handle_mining_event_in_main_thread(self, event: MiningEvent) -> None:
        """Handle mining events in the main thread (thread-safe).
        
        This slot is called via Qt's signal/slot mechanism, ensuring it runs
        in the main thread regardless of where on_mining_event was called from.
        """
        # Guard against None event.data
        if event.data is None:
            logger.warning("Received mining event with None data")
            return
        
        if event.event_type == EventType.LOG:
            level = event.data.get('level', 'info')
            message = event.data.get('message', '')
            component = event.data.get('component', 'miner')
            
            import time
            timestamp = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
            
            log_entry = f"[{timestamp}] [{level.upper()}] {component}: {message}"
            self.add_log(log_entry, level)
        
        elif event.event_type == EventType.ERROR:
            error = event.data.get('error', 'Unknown error')
            
            import time
            timestamp = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
            
            log_entry = f"[{timestamp}] [ERROR] miner: {error}"
            self.add_log(log_entry, 'error')
    
    def add_log(self, message: str, level: str = 'info') -> None:
        """Add a log message."""
        self.log_buffer.append((message, level))
        self.apply_filters()
    
    def apply_filters(self) -> None:
        """Apply current filters and update display."""
        level_filter = self.level_combo.currentText().lower()
        search_text = self.search_input.text().lower()
        
        filtered_logs = []
        
        for message, level in self.log_buffer:
            # Level filter
            if level_filter != "all" and level.lower() != level_filter:
                continue
            
            # Search filter
            if search_text and search_text not in message.lower():
                continue
            
            filtered_logs.append(message)
        
        # Update display
        self.log_display.setPlainText("\n".join(filtered_logs))
        
        # Auto-scroll to bottom
        if self.autoscroll_check.isChecked():
            scrollbar = self.log_display.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
    
    def clear_logs(self) -> None:
        """Clear all logs."""
        self.log_buffer.clear()
        self.log_display.clear()
    
    def export_logs(self) -> None:
        """Export logs to file."""
        from PySide6.QtWidgets import QFileDialog
        
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Export Logs",
            "miner_logs.txt",
            "Text Files (*.txt);;All Files (*)"
        )
        
        if filename:
            try:
                with open(filename, 'w') as f:
                    for message, _ in self.log_buffer:
                        f.write(message + '\n')
                
                logger.info(f"Logs exported to {filename}")
            
            except Exception as e:
                logger.error(f"Error exporting logs: {e}")
    
    def get_recent_logs(self, count: int = 50) -> List[str]:
        """Get recent log messages for diagnostics.
        
        Args:
            count: Number of recent log entries to return
        
        Returns:
            List of log message strings
        """
        recent = list(self.log_buffer)[-count:]
        return [msg for msg, _ in recent]

    def update_node_log_line(self, status: dict) -> None:
        """Update logs with the latest node log line."""
        line = status.get("last_log_line")
        if not line or line == self._last_node_log_line:
            return
        self._last_node_log_line = line
        import time
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        log_entry = f"[{timestamp}] [INFO] node: {line}"
        self.add_log(log_entry, "info")
