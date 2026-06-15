"""Dashboard tab - main status and control panel."""

import logging
import math
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
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
from animica_miner_gui.backend.node_controller import NodeController
from animica_miner_gui.backend.rpc_client import RPCClient

logger = logging.getLogger(__name__)

# Constants
ANM_BASE_UNITS = 1_000_000_000  # 1 ANM = 1e9 base units


class DashboardTab(QWidget):
    """Dashboard tab for main status and controls."""
    
    start_mining_requested = Signal()
    stop_mining_requested = Signal()
    
    # Signal for thread-safe event handling
    mining_event_received = Signal(object)  # MiningEvent
    
    def __init__(self, config: MiningAppConfig, node_controller: NodeController, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.node_controller = node_controller
        self.rpc_client: Optional[RPCClient] = None
        self.current_hashrate = 0.0
        self.current_theta_micro = 0
        self.current_share_target = 0.25
        self.setup_ui()
        self.node_controller.rpcChanged.connect(self.on_rpc_changed)
        self.node_controller.statusUpdated.connect(self.on_status_updated)

        # Connect signal to slot for thread-safe UI updates
        self.mining_event_received.connect(self._handle_mining_event_in_main_thread)

        # Show which unified components will run for this machine.
        self.refresh_unified_plan()
    
    def setup_ui(self) -> None:
        """Set up the UI."""
        layout = QVBoxLayout()
        
        # Node status group
        status_group = QGroupBox("Node Status")
        status_layout = QGridLayout()
        
        # Chain info
        status_layout.addWidget(QLabel("Chain ID:"), 0, 0)
        self.chain_id_label = QLabel("--")
        status_layout.addWidget(self.chain_id_label, 0, 1)
        
        status_layout.addWidget(QLabel("Head Height:"), 1, 0)
        self.height_label = QLabel("--")
        status_layout.addWidget(self.height_label, 1, 1)

        status_layout.addWidget(QLabel("Head Hash:"), 2, 0)
        self.head_hash_label = QLabel("--")
        self.head_hash_label.setWordWrap(True)
        status_layout.addWidget(self.head_hash_label, 2, 1)
        
        status_layout.addWidget(QLabel("Sync Status:"), 3, 0)
        self.sync_label = QLabel("--")
        status_layout.addWidget(self.sync_label, 3, 1)

        status_layout.addWidget(QLabel("Peers (in/out/total):"), 4, 0)
        self.peers_label = QLabel("--")
        status_layout.addWidget(self.peers_label, 4, 1)

        status_layout.addWidget(QLabel("Node PID:"), 5, 0)
        self.node_pid_label = QLabel("--")
        status_layout.addWidget(self.node_pid_label, 5, 1)

        status_layout.addWidget(QLabel("RPC URL:"), 6, 0)
        self.rpc_url_label = QLabel("--")
        self.rpc_url_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        status_layout.addWidget(self.rpc_url_label, 6, 1)

        status_layout.addWidget(QLabel("Logs:"), 7, 0)
        self.logs_label = QLabel("--")
        self.logs_label.setWordWrap(True)
        status_layout.addWidget(self.logs_label, 7, 1)

        if not self.config.network.resolved_rpc_url():
            self.rpc_url_label.setText("Starting node...")
        
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
        
        mining_layout.addWidget(QLabel("Difficulty:"), 2, 0)
        self.difficulty_label = QLabel("--")
        self.difficulty_label.setToolTip("Network difficulty (theta) - higher values mean harder to find blocks")
        mining_layout.addWidget(self.difficulty_label, 2, 1)
        
        mining_layout.addWidget(QLabel("Time to Block:"), 3, 0)
        self.time_to_block_label = QLabel("--")
        self.time_to_block_label.setToolTip("Estimated average time to find a block at current hashrate")
        mining_layout.addWidget(self.time_to_block_label, 3, 1)
        
        mining_layout.addWidget(QLabel("Blocks Found:"), 4, 0)
        self.blocks_label = QLabel("0")
        mining_layout.addWidget(self.blocks_label, 4, 1)
        
        mining_layout.addWidget(QLabel("Last Submit:"), 5, 0)
        self.last_submit_label = QLabel("--")
        mining_layout.addWidget(self.last_submit_label, 5, 1)
        
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

        # Unified "mine + AI" mode: when on, the GUI drives the full unified
        # flow (PoW + AICF inference, ENA useful-work, and — on capable GPUs —
        # training / serving / Bittensor), not PoW-only.
        unified_group = QGroupBox("Mining Mode")
        unified_layout = QVBoxLayout()

        self.unified_checkbox = QCheckBox("Mine everything (mine + AI)")
        self.unified_checkbox.setToolTip(
            "Run the unified Animica flow: SHA3 proof-of-work plus AI compute "
            "(AICF inference, ENA useful-work, and GPU training/serving where "
            "available). Off = proof-of-work only. Everything is paid to your "
            "ANM payout address."
        )
        self.unified_checkbox.setChecked(True)
        self.unified_checkbox.toggled.connect(self._on_unified_toggled)
        unified_layout.addWidget(self.unified_checkbox)

        self.unified_components_label = QLabel("--")
        self.unified_components_label.setWordWrap(True)
        self.unified_components_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        unified_layout.addWidget(self.unified_components_label)

        unified_group.setLayout(unified_layout)
        layout.addWidget(unified_group)

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
    
    def unified_enabled(self) -> bool:
        """Whether the unified "mine + AI" mode is selected."""
        return self.unified_checkbox.isChecked()

    def _on_unified_toggled(self, checked: bool) -> None:
        """Refresh the displayed plan when the user flips the mode."""
        self.refresh_unified_plan()

    def refresh_unified_plan(self) -> None:
        """Preview which unified components will run for this machine.

        Pure: builds the plan via the backend (no launching) and lists the
        components. When unified mode is off, shows PoW-only.
        """
        if not self.unified_checkbox.isChecked():
            self.unified_components_label.setText(
                "Proof-of-work only (AI compute disabled)."
            )
            return
        try:
            from animica_miner_gui.backend.unified_runner import plan_preview

            cfg = {"address": self.config.miner.payout_address or ""}
            summary = plan_preview(cfg)
            will_run = summary.get("will_run", [])
            pending = summary.get("enabled_but_pending", [])
            caps = summary.get("capabilities", {})

            tier = "GPU" if caps.get("gpu") else "CPU-only"
            if caps.get("qualified_bittensor"):
                tier = "GPU (qualified)"

            lines = [f"Will run ({tier}): " + (", ".join(will_run) or "nothing")]
            if pending:
                lines.append("Pending (coming soon): " + ", ".join(pending))
            self.unified_components_label.setText("\n".join(lines))
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to preview unified plan: {e}")
            self.unified_components_label.setText(
                "Unified plan unavailable (see logs)."
            )

    def on_unified_status(self, status: dict) -> None:
        """Render live per-component status from the unified runner."""
        if not status:
            return
        will_run = status.get("will_run", [])
        pending = status.get("enabled_but_pending", [])
        state = status.get("status", "stopped")

        lines = [f"Unified status: {state}"]
        if will_run:
            lines.append("Running: " + ", ".join(will_run))
        if pending:
            lines.append("Pending (coming soon): " + ", ".join(pending))
        error = status.get("error")
        if error:
            lines.append(f"Error: {error}")
        self.unified_components_label.setText("\n".join(lines))

    def on_rpc_changed(self, rpc_url: str, token: str) -> None:
        """Update the RPC client when the node changes."""
        try:
            self.rpc_client = RPCClient(rpc_url, token=token)
            self.rpc_url_label.setText(rpc_url)
        except Exception as e:
            logger.error(f"Failed to initialize RPC client: {e}")

    def on_status_updated(self, status: dict) -> None:
        """Update node status from backend polling."""
        head = status.get("head") or {}
        chain_id = head.get("chainId") or head.get("chain_id")
        if chain_id:
            self.chain_id_label.setText(str(chain_id))

        height = head.get("number") or head.get("height")
        if height is not None:
            self.height_label.setText(str(height))

        head_hash = head.get("hash") or head.get("blockHash") or head.get("block_hash")
        if head_hash:
            self.head_hash_label.setText(str(head_hash))

        sync_status = status.get("sync") or {}
        if sync_status.get("syncing"):
            current = sync_status.get("currentBlock", 0)
            highest = sync_status.get("highestBlock", 0)
            self.sync_label.setText(f"Syncing: {current}/{highest}")
        elif sync_status:
            self.sync_label.setText("Synced")
        else:
            self.sync_label.setText("Unknown")

        peers = status.get("peers") or {}
        if isinstance(peers, dict):
            inbound = peers.get("inbound")
            outbound = peers.get("outbound")
            total = peers.get("total")
            if total is not None:
                self.peers_label.setText(f"{inbound or 0}/{outbound or 0}/{total}")

        pid = status.get("pid")
        if pid:
            self.node_pid_label.setText(str(pid))

        rpc_url = status.get("rpc_url")
        if rpc_url:
            self.rpc_url_label.setText(rpc_url)

        logs_dir = status.get("logs_dir")
        last_log = status.get("last_log_line")
        if logs_dir:
            display = f"{logs_dir}"
            if last_log:
                display = f"{logs_dir}\n{last_log}"
            self.logs_label.setText(display)
    
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
                    # Handle hex strings (e.g., "0x1234") and regular numbers
                    if isinstance(balance, str):
                        if balance.startswith("0x"):
                            balance_int = int(balance, 16)
                        else:
                            balance_int = int(balance)
                    else:
                        balance_int = int(balance)
                    
                    balance_value = balance_int / ANM_BASE_UNITS
                    self.balance_label.setText(f"{balance_value:.9f} ANM")
                except (ValueError, TypeError) as e:
                    logger.error(f"Failed to parse balance '{balance}': {e}")
                    # Only show first 20 chars to avoid displaying sensitive data
                    safe_balance = str(balance)[:20] if balance else "None"
                    self.balance_label.setText(f"Parse error: {safe_balance}...")
            else:
                self.balance_label.setText("Unable to query")
                
        except Exception as e:
            logger.error(f"Failed to query balance: {e}")
            self.balance_label.setText("Query failed")
    
    def _update_time_to_block(self) -> None:
        """Update the estimated time to find a block based on current hashrate and difficulty."""
        if self.current_hashrate <= 0 or self.current_theta_micro <= 0:
            self.time_to_block_label.setText("--")
            return
        
        try:
            # Calculate probability of finding a block per hash
            # Block threshold is theta_micro (in micro-nats)
            # Probability = e^(-theta)
            theta_nats = self.current_theta_micro / 1_000_000
            block_probability = math.exp(-theta_nats)
            
            if block_probability <= 0:
                self.time_to_block_label.setText("Very high")
                return
            
            # Average hashes needed = 1 / probability
            avg_hashes_needed = 1.0 / block_probability
            
            # Time = hashes / hashrate (in seconds)
            time_seconds = avg_hashes_needed / self.current_hashrate
            
            # Format the time nicely
            if time_seconds < 60:
                time_str = f"{time_seconds:.0f}s"
            elif time_seconds < 3600:
                minutes = time_seconds / 60
                time_str = f"{minutes:.1f}m"
            elif time_seconds < 86400:
                hours = time_seconds / 3600
                time_str = f"{hours:.1f}h"
            else:
                days = time_seconds / 86400
                time_str = f"{days:.1f}d"
            
            self.time_to_block_label.setText(f"~{time_str}")
        
        except Exception as e:
            logger.debug(f"Error calculating time to block: {e}")
            self.time_to_block_label.setText("--")
    
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
        
        if event.event_type == EventType.STATUS_CHANGE:
            status = event.data.get('status', 'unknown')
            self.mining_status_label.setText(status.capitalize())
            
            # Update button states
            is_running = status == 'running'
            self.start_button.setEnabled(not is_running)
            self.stop_button.setEnabled(is_running)
        
        elif event.event_type == EventType.HASHRATE_UPDATE:
            hashrate = event.data.get('hashrate', 0)
            self.current_hashrate = hashrate
            
            if hashrate >= 1e9:
                hr_str = f"{hashrate/1e9:.2f} GH/s"
            elif hashrate >= 1e6:
                hr_str = f"{hashrate/1e6:.2f} MH/s"
            elif hashrate >= 1e3:
                hr_str = f"{hashrate/1e3:.2f} KH/s"
            else:
                hr_str = f"{hashrate:.0f} H/s"
            
            self.hashrate_label.setText(hr_str)
            
            # Update time to block when hashrate changes
            self._update_time_to_block()
        
        elif event.event_type == EventType.BLOCK_FOUND:
            count = event.data.get('block_count', 0)
            self.blocks_label.setText(str(count))
            
            import time
            self.last_submit_label.setText(
                time.strftime("%H:%M:%S", time.localtime(event.timestamp))
            )
            
            # Refresh balance when a block is found
            self.refresh_balance()
        
        elif event.event_type == EventType.TEMPLATE_UPDATE:
            # Don't update height from templates as it conflicts with RPC updates
            # The RPC timer will update height from actual chain state
            
            # Extract theta and share target if available
            theta_micro = event.data.get('theta_micro', 0)
            share_target = event.data.get('share_target', 0)
            
            if theta_micro > 0:
                self.current_theta_micro = theta_micro
                # Display difficulty in a readable format
                # Theta is in micro-nats, convert to difficulty display
                # Higher theta = higher difficulty
                theta_nats = theta_micro / 1_000_000
                self.difficulty_label.setText(f"{theta_nats:.2f} nats")
            
            if share_target > 0:
                self.current_share_target = share_target
            
            # Update time to block when difficulty changes
            self._update_time_to_block()
