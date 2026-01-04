"""First-run wizard for initial setup.

Guides users through:
1. Network selection (mainnet/testnet/devnet/custom)
2. RPC URL configuration and validation
3. Wallet/payout address setup
4. Device detection and selection
5. Performance presets
6. Summary and start mining
"""

import logging
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QRadioButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QWizard,
    QWizardPage,
)

from animica_miner_gui.backend.config import (
    MiningAppConfig,
    NetworkConfig,
    NetworkType,
    save_config,
)
from animica_miner_gui.backend.device_detection import detect_all
from animica_miner_gui.backend.rpc_client import RPCClient

logger = logging.getLogger(__name__)


class NetworkSelectionPage(QWizardPage):
    """Network selection page."""
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setTitle("Network Selection")
        self.setSubTitle("Choose the Animica network to connect to")
        
        layout = QVBoxLayout()
        
        # Network type selection
        self.mainnet_radio = QRadioButton("Mainnet (production)")
        self.testnet_radio = QRadioButton("Testnet (testing with real conditions)")
        self.devnet_radio = QRadioButton("Devnet (development and testing)")
        self.custom_radio = QRadioButton("Custom (specify your own RPC)")
        
        self.devnet_radio.setChecked(True)  # Default to devnet
        
        layout.addWidget(self.mainnet_radio)
        layout.addWidget(self.testnet_radio)
        layout.addWidget(self.devnet_radio)
        layout.addWidget(self.custom_radio)
        
        # Custom RPC URL field (enabled only for custom)
        self.custom_rpc_label = QLabel("Custom RPC URL:")
        self.custom_rpc_input = QLineEdit()
        self.custom_rpc_input.setPlaceholderText("http://127.0.0.1:8545")
        self.custom_rpc_input.setEnabled(False)
        
        layout.addWidget(self.custom_rpc_label)
        layout.addWidget(self.custom_rpc_input)
        
        # Connect signals
        self.custom_radio.toggled.connect(self.custom_rpc_input.setEnabled)
        self.custom_radio.toggled.connect(self.custom_rpc_label.setEnabled)
        
        layout.addStretch()
        self.setLayout(layout)
        
        # Register fields
        self.registerField("mainnet", self.mainnet_radio)
        self.registerField("testnet", self.testnet_radio)
        self.registerField("devnet", self.devnet_radio)
        self.registerField("custom", self.custom_radio)
        self.registerField("custom_rpc", self.custom_rpc_input)
    
    def validatePage(self) -> bool:
        """Validate the page before moving to next."""
        if self.custom_radio.isChecked():
            url = self.custom_rpc_input.text().strip()
            if not url:
                return False
            if not (url.startswith("http://") or url.startswith("https://")):
                return False
        return True


class RPCConfigPage(QWizardPage):
    """RPC configuration and validation page."""
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setTitle("RPC Configuration")
        self.setSubTitle("Configure and test your RPC connection")
        
        layout = QVBoxLayout()
        
        # RPC URL display
        self.rpc_label = QLabel("RPC URL:")
        self.rpc_display = QLabel()
        self.rpc_display.setStyleSheet("font-weight: bold;")
        
        layout.addWidget(self.rpc_label)
        layout.addWidget(self.rpc_display)
        
        # Test button and status
        test_layout = QHBoxLayout()
        self.test_button = QPushButton("Test Connection")
        self.test_button.clicked.connect(self.test_connection)
        self.status_label = QLabel("")
        
        test_layout.addWidget(self.test_button)
        test_layout.addWidget(self.status_label)
        test_layout.addStretch()
        
        layout.addLayout(test_layout)
        
        # Connection info
        self.info_text = QTextEdit()
        self.info_text.setReadOnly(True)
        self.info_text.setMaximumHeight(150)
        
        layout.addWidget(QLabel("Connection Info:"))
        layout.addWidget(self.info_text)
        
        layout.addStretch()
        self.setLayout(layout)
        
        self.connection_ok = False
    
    def initializePage(self) -> None:
        """Initialize the page with RPC URL from previous page."""
        # Determine RPC URL based on network selection
        if self.field("custom"):
            rpc_url = self.field("custom_rpc")
        elif self.field("mainnet"):
            rpc_url = "https://rpc.mainnet.animica.org"
        elif self.field("testnet"):
            rpc_url = "https://rpc.testnet.animica.org"
        else:  # devnet
            rpc_url = "http://127.0.0.1:8545"
        
        self.rpc_display.setText(rpc_url)
        self.info_text.clear()
        self.status_label.setText("")
        self.connection_ok = False
    
    def test_connection(self) -> None:
        """Test the RPC connection."""
        rpc_url = self.rpc_display.text()
        self.status_label.setText("Testing...")
        self.test_button.setEnabled(False)
        
        try:
            client = RPCClient(rpc_url, timeout=5.0)
            if client.check_connection():
                head = client.get_chain_head()
                chain_id = head.get("chainId") or head.get("chain_id", "Unknown")
                height = head.get("number") or head.get("height", 0)
                
                self.info_text.setHtml(
                    f"<b style='color: green;'>✓ Connection Successful</b><br>"
                    f"Chain ID: {chain_id}<br>"
                    f"Current Height: {height}"
                )
                self.status_label.setText("✓ OK")
                self.status_label.setStyleSheet("color: green; font-weight: bold;")
                self.connection_ok = True
                self.completeChanged.emit()
            else:
                raise Exception("Connection check failed")
        
        except Exception as e:
            self.info_text.setHtml(
                f"<b style='color: red;'>✗ Connection Failed</b><br>"
                f"Error: {str(e)}"
            )
            self.status_label.setText("✗ Failed")
            self.status_label.setStyleSheet("color: red; font-weight: bold;")
            self.connection_ok = False
            self.completeChanged.emit()
        
        finally:
            self.test_button.setEnabled(True)
    
    def isComplete(self) -> bool:
        """Page is complete only if connection was tested successfully."""
        return self.connection_ok


class WalletConfigPage(QWizardPage):
    """Wallet/payout address configuration page."""
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setTitle("Payout Address")
        self.setSubTitle("Configure where mining rewards will be sent")
        
        layout = QVBoxLayout()
        
        # Manual address entry
        layout.addWidget(QLabel("Enter payout address:"))
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("anim1...")
        self.address_input.textChanged.connect(lambda: self.completeChanged.emit())
        
        layout.addWidget(self.address_input)
        
        # Import from wallets.json button
        import_layout = QHBoxLayout()
        self.import_button = QPushButton("Import from Wallets")
        self.import_button.clicked.connect(self.import_from_wallets)
        import_layout.addWidget(self.import_button)
        import_layout.addStretch()
        
        layout.addLayout(import_layout)
        
        # Validation status
        self.validation_label = QLabel("")
        layout.addWidget(self.validation_label)
        
        layout.addStretch()
        self.setLayout(layout)
        
        self.registerField("payout_address*", self.address_input)
    
    def import_from_wallets(self) -> None:
        """Import address from ~/.animica/wallets.json."""
        from pathlib import Path
        import json
        
        wallet_path = Path.home() / ".animica" / "wallets.json"
        
        if not wallet_path.exists():
            self.validation_label.setText("No wallets.json found")
            self.validation_label.setStyleSheet("color: orange;")
            return
        
        try:
            with open(wallet_path, "r") as f:
                wallets = json.load(f)
            
            if not wallets:
                self.validation_label.setText("No wallets in file")
                return
            
            # Use first wallet's address
            first_wallet = wallets[0]
            address = first_wallet.get("address", "")
            
            if address:
                self.address_input.setText(address)
                self.validation_label.setText(f"Imported: {first_wallet.get('label', 'Unknown')}")
                self.validation_label.setStyleSheet("color: green;")
            else:
                self.validation_label.setText("No address in wallet")
        
        except Exception as e:
            self.validation_label.setText(f"Error: {e}")
            self.validation_label.setStyleSheet("color: red;")
    
    def isComplete(self) -> bool:
        """Validate payout address."""
        addr = self.address_input.text().strip()
        
        if not addr:
            self.validation_label.setText("")
            return False
        
        # Basic validation
        if addr.startswith("anim1") and len(addr) >= 42:
            self.validation_label.setText("✓ Valid address format")
            self.validation_label.setStyleSheet("color: green;")
            return True
        else:
            self.validation_label.setText("✗ Invalid address format")
            self.validation_label.setStyleSheet("color: red;")
            return False


class DeviceSelectionPage(QWizardPage):
    """Device detection and selection page."""
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setTitle("Device Selection")
        self.setSubTitle("Detected mining devices")
        
        layout = QVBoxLayout()
        
        # Auto-detect button
        detect_layout = QHBoxLayout()
        self.detect_button = QPushButton("Auto-Detect Devices")
        self.detect_button.clicked.connect(self.run_detection)
        detect_layout.addWidget(self.detect_button)
        detect_layout.addStretch()
        
        layout.addLayout(detect_layout)
        
        # Device list
        self.device_list = QListWidget()
        layout.addWidget(QLabel("Available Devices:"))
        layout.addWidget(self.device_list)
        
        # Recommendations
        self.recommendations_text = QTextEdit()
        self.recommendations_text.setReadOnly(True)
        self.recommendations_text.setMaximumHeight(120)
        
        layout.addWidget(QLabel("Recommendations:"))
        layout.addWidget(self.recommendations_text)
        
        self.setLayout(layout)
        
        self.detection_done = False
    
    def initializePage(self) -> None:
        """Run device detection when page is shown."""
        self.run_detection()
    
    def run_detection(self) -> None:
        """Run device detection."""
        self.detect_button.setEnabled(False)
        self.device_list.clear()
        
        try:
            detection = detect_all()
            
            # Add CPU
            cpu_item = QListWidgetItem(
                f"✓ CPU: {detection.cpu.model_name} ({detection.cpu.threads} threads)"
            )
            self.device_list.addItem(cpu_item)
            
            # Add GPUs
            if detection.gpus:
                for gpu in detection.gpus:
                    marker = "✓" if gpu.recommended else "○"
                    gpu_item = QListWidgetItem(
                        f"{marker} GPU {gpu.device_id}: {gpu.name} "
                        f"({gpu.compute_units} CUs, {gpu.memory_mb} MB)"
                    )
                    self.device_list.addItem(gpu_item)
            
            # Show recommendations and warnings
            rec_text = ""
            
            if detection.recommendations:
                rec_text += "<b>Recommendations:</b><ul>"
                for rec in detection.recommendations:
                    rec_text += f"<li>{rec}</li>"
                rec_text += "</ul>"
            
            if detection.warnings:
                rec_text += "<b style='color: orange;'>Warnings:</b><ul>"
                for warning in detection.warnings:
                    rec_text += f"<li>{warning}</li>"
                rec_text += "</ul>"
            
            self.recommendations_text.setHtml(rec_text or "No recommendations.")
            
            self.detection_done = True
            self.completeChanged.emit()
        
        except Exception as e:
            logger.error(f"Device detection failed: {e}")
            self.recommendations_text.setHtml(
                f"<b style='color: red;'>Detection Failed:</b> {e}"
            )
        
        finally:
            self.detect_button.setEnabled(True)
    
    def isComplete(self) -> bool:
        """Page complete if detection was run."""
        return self.detection_done


class PresetSelectionPage(QWizardPage):
    """Performance preset selection page."""
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setTitle("Performance Preset")
        self.setSubTitle("Choose a performance profile")
        
        layout = QVBoxLayout()
        
        # Preset selection
        self.recommended_radio = QRadioButton("Recommended (balanced performance)")
        self.max_perf_radio = QRadioButton("Maximum Performance (use all resources)")
        self.safe_mode_radio = QRadioButton("Safe Mode (minimal resource usage)")
        
        self.recommended_radio.setChecked(True)
        
        layout.addWidget(self.recommended_radio)
        layout.addWidget(self.max_perf_radio)
        layout.addWidget(self.safe_mode_radio)
        
        # Description
        self.description_label = QLabel()
        self.description_label.setWordWrap(True)
        self.update_description()
        
        layout.addWidget(self.description_label)
        
        # Connect signals
        self.recommended_radio.toggled.connect(self.update_description)
        self.max_perf_radio.toggled.connect(self.update_description)
        self.safe_mode_radio.toggled.connect(self.update_description)
        
        layout.addStretch()
        self.setLayout(layout)
        
        self.registerField("preset_recommended", self.recommended_radio)
        self.registerField("preset_max", self.max_perf_radio)
        self.registerField("preset_safe", self.safe_mode_radio)
    
    def update_description(self) -> None:
        """Update preset description."""
        if self.recommended_radio.isChecked():
            desc = (
                "Balanced performance using detected capabilities. "
                "Leaves some CPU cores free for system tasks. "
                "This is the recommended option for most users."
            )
        elif self.max_perf_radio.isChecked():
            desc = (
                "Maximum mining performance using all available resources. "
                "May impact system responsiveness. "
                "Recommended for dedicated mining machines."
            )
        else:
            desc = (
                "Minimal resource usage with reduced intensity. "
                "Suitable for constrained environments or background mining. "
                "Recommended for laptops or containers with CPU limits."
            )
        
        self.description_label.setText(desc)


class SummaryPage(QWizardPage):
    """Summary and final configuration page."""
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setTitle("Summary")
        self.setSubTitle("Review your configuration")
        
        layout = QVBoxLayout()
        
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        
        layout.addWidget(self.summary_text)
        
        # Start mining checkbox
        self.start_mining_checkbox = QCheckBox("Start mining immediately")
        self.start_mining_checkbox.setChecked(True)
        layout.addWidget(self.start_mining_checkbox)
        
        self.setLayout(layout)
        
        self.registerField("start_mining", self.start_mining_checkbox)
    
    def initializePage(self) -> None:
        """Generate summary from wizard fields."""
        # Network
        if self.field("custom"):
            network = f"Custom ({self.field('custom_rpc')})"
        elif self.field("mainnet"):
            network = "Mainnet"
        elif self.field("testnet"):
            network = "Testnet"
        else:
            network = "Devnet"
        
        # Preset
        if self.field("preset_max"):
            preset = "Maximum Performance"
        elif self.field("preset_safe"):
            preset = "Safe Mode"
        else:
            preset = "Recommended"
        
        summary = f"""
        <h3>Configuration Summary</h3>
        <table>
        <tr><td><b>Network:</b></td><td>{network}</td></tr>
        <tr><td><b>Payout Address:</b></td><td>{self.field('payout_address')}</td></tr>
        <tr><td><b>Performance Preset:</b></td><td>{preset}</td></tr>
        </table>
        <br>
        <p>Click <b>Finish</b> to save this configuration and start the miner.</p>
        """
        
        self.summary_text.setHtml(summary)


class FirstRunWizard(QWizard):
    """First-run setup wizard."""
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        self.setWindowTitle("Animica Miner Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.HaveHelpButton, False)
        self.setMinimumSize(600, 500)
        
        # Add pages
        self.addPage(NetworkSelectionPage())
        self.addPage(RPCConfigPage())
        self.addPage(WalletConfigPage())
        self.addPage(DeviceSelectionPage())
        self.addPage(PresetSelectionPage())
        self.addPage(SummaryPage())
    
    def accept(self) -> None:
        """Save configuration when wizard is finished."""
        try:
            # Build configuration from wizard fields
            config = MiningAppConfig()
            
            # Network configuration
            if self.field("custom"):
                config.network.network_type = NetworkType.CUSTOM
                config.network.custom_rpc_url = self.field("custom_rpc")
                config.network.rpc_url = self.field("custom_rpc")
            elif self.field("mainnet"):
                config.network.network_type = NetworkType.MAINNET
                config.network.rpc_url = "https://rpc.mainnet.animica.org"
            elif self.field("testnet"):
                config.network.network_type = NetworkType.TESTNET
                config.network.rpc_url = "https://rpc.testnet.animica.org"
            else:
                config.network.network_type = NetworkType.DEVNET
                config.network.rpc_url = "http://127.0.0.1:8545"
            
            # Payout address
            config.miner.payout_address = self.field("payout_address")
            
            # Auto-start based on wizard choice
            config.miner.auto_start = self.field("start_mining")
            
            # Apply preset
            if self.field("preset_safe"):
                config.safe_mode.enabled = True
                config.cpu.threads = max(1, (config.cpu.threads or 1) - 2)
            elif self.field("preset_max"):
                config.cpu.threads = 0  # Use all
            
            # Save configuration
            save_config(config)
            logger.info("Configuration saved successfully")
            
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")
        
        super().accept()
