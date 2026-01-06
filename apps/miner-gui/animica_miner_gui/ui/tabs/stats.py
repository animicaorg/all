"""Stats/Graphs tab - hashrate visualization."""

import logging
from collections import deque
from typing import Optional

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QVBoxLayout,
    QWidget,
    QLabel,
    QGroupBox,
)

from animica_miner_gui.backend.miner_runner import MiningEvent, EventType

logger = logging.getLogger(__name__)


class StatsTab(QWidget):
    """Statistics and graphs tab."""
    
    # Signal for thread-safe event handling
    mining_event_received = Signal(object)  # MiningEvent
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.hashrate_history = deque(maxlen=300)  # 5 minutes at 1s interval
        self.setup_ui()
        
        # Connect signal to slot for thread-safe UI updates
        self.mining_event_received.connect(self._handle_mining_event_in_main_thread)
    
    def setup_ui(self) -> None:
        """Set up the UI."""
        layout = QVBoxLayout()
        
        # Stats summary
        stats_group = QGroupBox("Statistics Summary")
        stats_layout = QVBoxLayout()
        
        self.avg_hashrate_label = QLabel("Average Hashrate: --")
        
        stats_layout.addWidget(self.avg_hashrate_label)
        
        stats_group.setLayout(stats_layout)
        layout.addWidget(stats_group)
        
        # Graph placeholder
        graph_group = QGroupBox("Hashrate Graph")
        graph_layout = QVBoxLayout()
        
        try:
            # Try to create matplotlib graph
            from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
            from matplotlib.figure import Figure
            
            self.figure = Figure(figsize=(8, 4))
            self.canvas = FigureCanvasQTAgg(self.figure)
            self.ax = self.figure.add_subplot(111)
            self.ax.set_xlabel("Time (seconds)")
            self.ax.set_ylabel("Hashrate (H/s)")
            self.ax.set_title("Mining Hashrate")
            self.ax.grid(True, alpha=0.3)
            
            graph_layout.addWidget(self.canvas)
            self.has_matplotlib = True
        
        except ImportError:
            graph_layout.addWidget(
                QLabel("Matplotlib not available. Install matplotlib for graphs.")
            )
            self.has_matplotlib = False
        
        graph_group.setLayout(graph_layout)
        layout.addWidget(graph_group)
        
        # Template stats group
        template_group = QGroupBox("Template Statistics")
        template_layout = QVBoxLayout()
        
        self.mempool_total_label = QLabel("Mempool Total: --")
        self.mempool_included_label = QLabel("Included: --")
        self.mempool_rejected_label = QLabel("Rejected: --")
        
        template_layout.addWidget(self.mempool_total_label)
        template_layout.addWidget(self.mempool_included_label)
        template_layout.addWidget(self.mempool_rejected_label)
        
        template_group.setLayout(template_layout)
        layout.addWidget(template_group)
        
        layout.addStretch()
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
        
        if event.event_type == EventType.HASHRATE_UPDATE:
            hashrate = event.data.get('hashrate', 0)
            self.hashrate_history.append(hashrate)
            self.update_stats()
            self.update_graph()
        
        elif event.event_type == EventType.TEMPLATE_UPDATE:
            tx_count = event.data.get('transactions', 0)
            self.mempool_total_label.setText(f"Mempool Total: {tx_count}")
    
    def update_stats(self) -> None:
        """Update statistics display."""
        if not self.hashrate_history:
            return
        
        avg_hashrate = sum(self.hashrate_history) / len(self.hashrate_history)
        
        if avg_hashrate >= 1e9:
            hr_str = f"{avg_hashrate/1e9:.2f} GH/s"
        elif avg_hashrate >= 1e6:
            hr_str = f"{avg_hashrate/1e6:.2f} MH/s"
        elif avg_hashrate >= 1e3:
            hr_str = f"{avg_hashrate/1e3:.2f} KH/s"
        else:
            hr_str = f"{avg_hashrate:.0f} H/s"
        
        self.avg_hashrate_label.setText(f"Average Hashrate: {hr_str}")
    
    def update_graph(self) -> None:
        """Update the hashrate graph."""
        if not self.has_matplotlib or not self.hashrate_history:
            return
        
        try:
            self.ax.clear()
            self.ax.set_xlabel("Time (seconds ago)")
            self.ax.set_ylabel("Hashrate (H/s)")
            self.ax.set_title("Mining Hashrate")
            self.ax.grid(True, alpha=0.3)
            
            # Plot hashrate
            x_data = list(range(len(self.hashrate_history)))
            x_data = [len(self.hashrate_history) - i - 1 for i in x_data]
            y_data = list(self.hashrate_history)
            
            self.ax.plot(x_data, y_data, 'b-', linewidth=2)
            self.ax.fill_between(x_data, y_data, alpha=0.3)
            
            # Invert x-axis so recent is on the right
            self.ax.invert_xaxis()
            
            self.canvas.draw()
        
        except Exception as e:
            logger.debug(f"Error updating graph: {e}")
