"""Pools/Modes tab - solo/pool/failover configuration."""

import logging
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QLabel,
    QLineEdit,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from animica_miner_gui.backend.config import MiningAppConfig, MiningMode

logger = logging.getLogger(__name__)


class PoolsTab(QWidget):
    """Pools and mining modes configuration tab."""
    
    def __init__(self, config: MiningAppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.setup_ui()
    
    def setup_ui(self) -> None:
        """Set up the UI."""
        layout = QVBoxLayout()
        
        # Mining mode selection
        mode_group = QGroupBox("Mining Mode")
        mode_layout = QVBoxLayout()
        
        self.solo_radio = QRadioButton("SOLO Mining (default)")
        self.pool_radio = QRadioButton("Pool Mining (Stratum)")
        
        if self.config.miner.mining_mode == MiningMode.SOLO:
            self.solo_radio.setChecked(True)
        else:
            self.pool_radio.setChecked(True)
        
        mode_layout.addWidget(self.solo_radio)
        mode_layout.addWidget(self.pool_radio)
        mode_group.setLayout(mode_layout)
        layout.addWidget(mode_group)
        
        # Pool configuration
        pool_group = QGroupBox("Stratum Pool Configuration")
        pool_layout = QVBoxLayout()
        
        pool_layout.addWidget(QLabel("Pool URL:"))
        self.pool_url_input = QLineEdit()
        self.pool_url_input.setPlaceholderText("stratum+tcp://pool.example.com:3333")
        self.pool_url_input.setText(self.config.pool.url)
        pool_layout.addWidget(self.pool_url_input)
        
        pool_layout.addWidget(QLabel("Username/Wallet:"))
        self.pool_username_input = QLineEdit()
        self.pool_username_input.setPlaceholderText("your_wallet_address")
        self.pool_username_input.setText(self.config.pool.username)
        pool_layout.addWidget(self.pool_username_input)
        
        pool_layout.addWidget(QLabel("Password:"))
        self.pool_password_input = QLineEdit()
        self.pool_password_input.setPlaceholderText("x")
        self.pool_password_input.setText(self.config.pool.password)
        pool_layout.addWidget(self.pool_password_input)
        
        pool_group.setLayout(pool_layout)
        layout.addWidget(pool_group)
        
        # Failover configuration
        failover_group = QGroupBox("Failover Pools (Placeholder)")
        failover_layout = QVBoxLayout()
        
        failover_layout.addWidget(
            QLabel("Failover pool configuration will be available in a future release.")
        )
        
        failover_group.setLayout(failover_layout)
        layout.addWidget(failover_group)
        
        layout.addStretch()
        self.setLayout(layout)
