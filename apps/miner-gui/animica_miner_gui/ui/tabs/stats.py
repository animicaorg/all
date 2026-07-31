"""Stats tab — mempool figures from the node.

The hashrate summary and graph that used to live here were removed: the miner
reports no hashrate, so the value was inferred from block-timestamp gaps and
was always either 0 or invented.
"""

import logging
from typing import Optional

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QVBoxLayout,
    QWidget,
    QLabel,
    QGroupBox,
)

from animica_miner_gui.backend.miner_runner import MiningEvent

logger = logging.getLogger(__name__)


class StatsTab(QWidget):
    """Statistics and graphs tab."""
    
    # Signal for thread-safe event handling
    mining_event_received = Signal(object)  # MiningEvent
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setup_ui()
        
        # Connect signal to slot for thread-safe UI updates
        self.mining_event_received.connect(self._handle_mining_event_in_main_thread)
    
    def setup_ui(self) -> None:
        """Set up the UI."""
        layout = QVBoxLayout()
        
        # No hashrate summary and no hashrate graph. The miner does not report
        # a hashrate; the figure shown here was inferred from the gaps between
        # block timestamps, so it read 0 H/s while mining normally and then
        # invented a value the moment a block landed. Removed rather than left
        # showing something untrue.

        # Mempool, from the node's own RPC.
        template_group = QGroupBox("Mempool")
        template_layout = QVBoxLayout()

        self.mempool_total_label = QLabel("Transactions: --")
        self.mempool_bytes_label = QLabel("Total size: --")
        # "Included"/"Rejected" used to live here; no code path ever wrote them,
        # so they were permanently "--" with no way to tell whether that meant
        # zero, unknown, or broken.
        template_layout.addWidget(self.mempool_total_label)
        template_layout.addWidget(self.mempool_bytes_label)

        template_group.setLayout(template_layout)
        layout.addWidget(template_group)

        layout.addStretch()
        self.setLayout(layout)
    
    def on_node_status(self, status: dict) -> None:
        """Fill the mempool figures from the node status payload."""
        mempool = status.get("mempool") if isinstance(status, dict) else None
        if not isinstance(mempool, dict):
            return
        count = mempool.get("count")
        total_bytes = mempool.get("totalBytes", mempool.get("total_bytes"))
        if count is not None:
            self.mempool_total_label.setText(f"Transactions: {count}")
        if total_bytes is not None:
            try:
                kb = int(total_bytes) / 1024
                self.mempool_bytes_label.setText(f"Total size: {kb:,.1f} KiB")
            except (TypeError, ValueError):
                pass

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
        
