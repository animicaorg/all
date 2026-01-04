"""Devices tab - CPU/GPU/ASIC configuration and benchmarking."""

import logging
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QDoubleSpinBox,
    QScrollArea,
)

from animica_miner_gui.backend.config import MiningAppConfig

logger = logging.getLogger(__name__)


class DevicesTab(QWidget):
    """Devices configuration tab."""
    
    def __init__(self, config: MiningAppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.setup_ui()
    
    def setup_ui(self) -> None:
        """Set up the UI."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        
        content = QWidget()
        layout = QVBoxLayout()
        
        # CPU configuration
        cpu_group = QGroupBox("CPU Configuration")
        cpu_layout = QVBoxLayout()
        
        cpu_enabled = QCheckBox("Enable CPU Mining")
        cpu_enabled.setChecked(self.config.cpu.enabled)
        cpu_layout.addWidget(cpu_enabled)
        
        threads_layout = QHBoxLayout()
        threads_layout.addWidget(QLabel("Threads:"))
        threads_spin = QSpinBox()
        threads_spin.setMinimum(0)
        threads_spin.setMaximum(256)
        threads_spin.setValue(self.config.cpu.threads)
        threads_spin.setToolTip("0 = auto-detect")
        threads_layout.addWidget(threads_spin)
        threads_layout.addStretch()
        cpu_layout.addLayout(threads_layout)
        
        hugepages_check = QCheckBox("Enable Hugepages (if available)")
        hugepages_check.setChecked(self.config.cpu.hugepages)
        cpu_layout.addWidget(hugepages_check)
        
        priority_layout = QHBoxLayout()
        priority_layout.addWidget(QLabel("Priority (-20 to 19):"))
        priority_spin = QSpinBox()
        priority_spin.setMinimum(-20)
        priority_spin.setMaximum(19)
        priority_spin.setValue(self.config.cpu.priority)
        priority_layout.addWidget(priority_spin)
        priority_layout.addStretch()
        cpu_layout.addLayout(priority_layout)
        
        cpu_group.setLayout(cpu_layout)
        layout.addWidget(cpu_group)
        
        # GPU configuration
        gpu_group = QGroupBox("GPU Configuration")
        gpu_layout = QVBoxLayout()
        
        if not self.config.gpus:
            gpu_layout.addWidget(QLabel("No GPUs configured. Use device detection."))
        else:
            for gpu in self.config.gpus:
                gpu_item = QGroupBox(f"GPU {gpu.device_id}: {gpu.name or 'Unknown'}")
                gpu_item_layout = QVBoxLayout()
                
                gpu_enabled = QCheckBox("Enabled")
                gpu_enabled.setChecked(gpu.enabled)
                gpu_item_layout.addWidget(gpu_enabled)
                
                intensity_layout = QHBoxLayout()
                intensity_layout.addWidget(QLabel("Intensity:"))
                intensity_spin = QDoubleSpinBox()
                intensity_spin.setMinimum(0.0)
                intensity_spin.setMaximum(1.0)
                intensity_spin.setSingleStep(0.1)
                intensity_spin.setValue(gpu.intensity)
                intensity_layout.addWidget(intensity_spin)
                intensity_layout.addStretch()
                gpu_item_layout.addLayout(intensity_layout)
                
                gpu_item.setLayout(gpu_item_layout)
                gpu_layout.addWidget(gpu_item)
        
        gpu_group.setLayout(gpu_layout)
        layout.addWidget(gpu_group)
        
        # ASIC configuration (stub)
        asic_group = QGroupBox("ASIC Configuration (Placeholder)")
        asic_layout = QVBoxLayout()
        
        asic_enabled = QCheckBox("Enable ASIC Worker (Stub)")
        asic_enabled.setChecked(self.config.asic.enabled)
        asic_layout.addWidget(asic_enabled)
        
        asic_layout.addWidget(QLabel("Endpoint:"))
        asic_layout.addWidget(QLabel(self.config.asic.endpoint or "Not configured"))
        
        asic_group.setLayout(asic_layout)
        layout.addWidget(asic_group)
        
        # Benchmark button
        benchmark_btn = QPushButton("Benchmark Devices")
        benchmark_btn.setToolTip("Run benchmark on selected devices")
        layout.addWidget(benchmark_btn)
        
        layout.addStretch()
        content.setLayout(layout)
        scroll.setWidget(content)
        
        main_layout = QVBoxLayout()
        main_layout.addWidget(scroll)
        self.setLayout(main_layout)
