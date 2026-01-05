"""Dashboard tab - main status and control panel."""

import logging
from typing import Optional

from PySide6.QtCore import Qt, Signal, QTimer
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
from animica_miner_gui.backend.rpc_client import RPCClient

logger = logging.getLogger(__name__)

# Constants
ANM_BASE_UNITS = 1_000_000_000  # 1 ANM = 1e9 base units


class DashboardTab(QWidget):
    """Dashboard tab for main status and controls."""
    
    start_mining_requested = Signal()
    stop_mining_requested = Signal()
    
    def __init__(self, config: MiningAppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.rpc_client: Optional[RPCClient] = None
        self.setup_ui()
        self.setup_rpc_timer()
    
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
        
        payout_layout.addWidget(QLabel("Balance:"), 1, 0)
        self.balance_label = QLabel("--")
        payout_layout.addWidget(self.balance_label, 1, 1)
        
        # Add refresh button for balance
        refresh_button = QPushButton("Refresh Balance")
        refresh_button.clicked.connect(self.refresh_balance)
        payout_layout.addWidget(refresh_button, 2, 0, 1, 2)
        
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
    
    def setup_rpc_timer(self) -> None:
        """Set up timer to periodically query RPC for chain data."""
        # Initialize RPC client
        try:
            self.rpc_client = RPCClient(self.config.network.rpc_url)
        except Exception as e:
            logger.error(f"Failed to initialize RPC client: {e}")
            return
        
        # Set up timer to update chain info every 5 seconds
        self.rpc_timer = QTimer()
        self.rpc_timer.timeout.connect(self.update_chain_info)
        self.rpc_timer.start(5000)
        
        # Do initial update
        self.update_chain_info()
    
    def update_chain_info(self) -> None:
        """Query RPC for current chain head and update display."""
        if not self.rpc_client:
            return
        
        try:
            head = self.rpc_client.get_chain_head()
            
            # Update chain ID
            chain_id = head.get("chainId") or head.get("chain_id")
            if chain_id:
                self.chain_id_label.setText(str(chain_id))
            
            # Update height
            height = head.get("number") or head.get("height")
            if height is not None:
                self.height_label.setText(str(height))
            
            # Update sync status
            try:
                sync_status = self.rpc_client.get_sync_status()
                if sync_status.get("syncing"):
                    current = sync_status.get("currentBlock", 0)
                    highest = sync_status.get("highestBlock", 0)
                    self.sync_label.setText(f"Syncing: {current}/{highest}")
                else:
                    self.sync_label.setText("Synced")
            except Exception:
                self.sync_label.setText("Unknown")
            
        except Exception as e:
            logger.debug(f"Failed to update chain info: {e}")
            # Don't update labels if RPC fails - keep previous values
    
    def refresh_balance(self) -> None:
        """Query RPC for wallet balance."""
        if not self.rpc_client:
            self.balance_label.setText("RPC not available")
            return
        
        payout_address = self.config.miner.payout_address
        if not payout_address:
            self.balance_label.setText("No payout address")
            return
        
        try:
            # Try multiple balance methods
            balance = None
            for method in ["state_getBalance", "state.getBalance", "eth_getBalance"]:
                try:
                    result = self.rpc_client._call(method, [payout_address])
                    if result is not None:
                        # Result might be a dict with 'balance' key or just a number
                        if isinstance(result, dict):
                            balance = result.get("balance") or result.get("value")
                        else:
                            balance = result
                        break
                except Exception:
                    continue
            
            if balance is not None:
                # Convert from base units to ANM
                try:
                    balance_value = float(balance) / ANM_BASE_UNITS
                    self.balance_label.setText(f"{balance_value:.9f} ANM")
                except (ValueError, TypeError):
                    self.balance_label.setText("Invalid balance")
            else:
                self.balance_label.setText("Unable to query")
                
        except Exception as e:
            logger.error(f"Failed to query balance: {e}")
            self.balance_label.setText("Query failed")
    
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
