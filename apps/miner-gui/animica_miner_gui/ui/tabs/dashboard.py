"""Dashboard tab - main status and control panel."""

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from animica_miner_gui.backend.config import MiningAppConfig
from animica_miner_gui.backend.miner_runner import MiningEvent, EventType

logger = logging.getLogger(__name__)


class DashboardTab(QWidget):
    """Dashboard tab for main status and controls."""
    
    start_mining_requested = Signal()
    stop_mining_requested = Signal()
    
    def __init__(self, config: MiningAppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.setup_ui()
    
    def setup_ui(self) -> None:
        """Set up the UI."""
        layout = QVBoxLayout()
        
        # Status group
        status_group = QGroupBox("Status")
        status_layout = QGridLayout()
        
        # Chain info
        status_layout.addWidget(QLabel("Chain ID:"), 0, 0)
        self.chain_id_label = QLabel("--")
        status_layout.addWidget(self.chain_id_label, 0, 1)
        
        status_layout.addWidget(QLabel("Block Height:"), 1, 0)
        self.height_label = QLabel("--")
        status_layout.addWidget(self.height_label, 1, 1)
        
        status_layout.addWidget(QLabel("Sync Status:"), 2, 0)
        self.sync_label = QLabel("--")
        status_layout.addWidget(self.sync_label, 2, 1)
        
        status_group.setLayout(status_layout)
        layout.addWidget(status_group)
        
        # Mining status group
        mining_group = QGroupBox("Mining Status")
        mining_layout = QGridLayout()
        
        mining_layout.addWidget(QLabel("Status:"), 0, 0)
        self.mining_status_label = QLabel("Stopped")
        mining_layout.addWidget(self.mining_status_label, 0, 1)
        
        mining_layout.addWidget(QLabel("Hashrate:"), 1, 0)
        self.hashrate_label = QLabel("0 H/s")
        self.hashrate_label.setStyleSheet("font-weight: bold; font-size: 14pt;")
        mining_layout.addWidget(self.hashrate_label, 1, 1)
        
        mining_layout.addWidget(QLabel("Shares Found:"), 2, 0)
        self.shares_label = QLabel("0")
        mining_layout.addWidget(self.shares_label, 2, 1)
        
        mining_layout.addWidget(QLabel("Blocks Found:"), 3, 0)
        self.blocks_label = QLabel("0")
        mining_layout.addWidget(self.blocks_label, 3, 1)
        
        mining_layout.addWidget(QLabel("Last Submit:"), 4, 0)
        self.last_submit_label = QLabel("--")
        mining_layout.addWidget(self.last_submit_label, 4, 1)
        
        mining_group.setLayout(mining_layout)
        layout.addWidget(mining_group)
        
        # Payout info group
        payout_group = QGroupBox("Payout Information")
        payout_layout = QGridLayout()
        
        payout_layout.addWidget(QLabel("Payout Address:"), 0, 0)
        self.payout_label = QLabel(self.config.miner.payout_address or "--")
        self.payout_label.setWordWrap(True)
        payout_layout.addWidget(self.payout_label, 0, 1)
        
        payout_layout.addWidget(QLabel("Estimated Earnings:"), 1, 0)
        self.earnings_label = QLabel("--")
        payout_layout.addWidget(self.earnings_label, 1, 1)
        
        payout_group.setLayout(payout_layout)
        layout.addWidget(payout_group)
        
        # Control buttons
        control_layout = QHBoxLayout()
        
        self.start_button = QPushButton("Start Mining")
        self.start_button.clicked.connect(self.start_mining_requested.emit)
        self.start_button.setMinimumHeight(40)
        
        self.stop_button = QPushButton("Stop Mining")
        self.stop_button.clicked.connect(self.stop_mining_requested.emit)
        self.stop_button.setMinimumHeight(40)
        self.stop_button.setEnabled(False)
        
        control_layout.addWidget(self.start_button)
        control_layout.addWidget(self.stop_button)
        
        layout.addLayout(control_layout)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def on_mining_event(self, event: MiningEvent) -> None:
        """Handle mining events."""
        if event.event_type == EventType.STATUS_CHANGE:
            status = event.data.get('status', 'unknown')
            self.mining_status_label.setText(status.capitalize())
            
            # Update button states
            is_running = status == 'running'
            self.start_button.setEnabled(not is_running)
            self.stop_button.setEnabled(is_running)
        
        elif event.event_type == EventType.HASHRATE_UPDATE:
            hashrate = event.data.get('hashrate', 0)
            
            if hashrate >= 1e9:
                hr_str = f"{hashrate/1e9:.2f} GH/s"
            elif hashrate >= 1e6:
                hr_str = f"{hashrate/1e6:.2f} MH/s"
            elif hashrate >= 1e3:
                hr_str = f"{hashrate/1e3:.2f} KH/s"
            else:
                hr_str = f"{hashrate:.0f} H/s"
            
            self.hashrate_label.setText(hr_str)
        
        elif event.event_type == EventType.SHARE_FOUND:
            count = event.data.get('share_count', 0)
            self.shares_label.setText(str(count))
            
            import time
            self.last_submit_label.setText(
                time.strftime("%H:%M:%S", time.localtime(event.timestamp))
            )
        
        elif event.event_type == EventType.BLOCK_FOUND:
            count = event.data.get('block_count', 0)
            self.blocks_label.setText(str(count))
        
        elif event.event_type == EventType.TEMPLATE_UPDATE:
            height = event.data.get('height', 0)
            self.height_label.setText(str(height))
