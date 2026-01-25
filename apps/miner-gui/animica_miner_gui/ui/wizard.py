"""First-run wizard for initial setup.

Guides users through:
1. Network selection (mainnet/testnet/devnet)
2. Wallet/payout address setup
3. Device detection and selection
4. Performance presets
5. Summary and start mining
"""

import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
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

from animica_miner_gui.backend.config import MiningAppConfig, NetworkType, save_config
from animica_miner_gui.backend.device_detection import detect_all

logger = logging.getLogger(__name__)


class CreateWalletDialog(QDialog):
    """Dialog for creating a new wallet."""
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("Create New Wallet")
        self.setMinimumWidth(500)
        
        layout = QVBoxLayout()
        
        # Label input
        layout.addWidget(QLabel("Wallet Label:"))
        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText("My Wallet")
        layout.addWidget(self.label_input)
        
        # Wallet file path selection
        layout.addWidget(QLabel("Wallet File Location:"))
        path_layout = QHBoxLayout()
        self.wallet_path_input = QLineEdit()
        default_wallet_path = Path.home() / ".animica" / "wallets.json"
        self.wallet_path_input.setText(str(default_wallet_path))
        self.wallet_path_input.setPlaceholderText(str(default_wallet_path))
        self.wallet_path_input.textChanged.connect(self._update_info_text)
        path_layout.addWidget(self.wallet_path_input)
        
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse_wallet_file)
        path_layout.addWidget(browse_button)
        layout.addLayout(path_layout)
        
        # Info text
        self.info_text = QLabel()
        self._update_info_text()
        self.info_text.setWordWrap(True)
        self.info_text.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self.info_text)
        
        # Status label
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        
        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.create_wallet)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        self.setLayout(layout)
        
        self.created_address: Optional[str] = None
    
    def _browse_wallet_file(self) -> None:
        """Open file dialog to select wallet file location."""
        current_path = self.wallet_path_input.text().strip()
        if current_path:
            # If we have a current path, use the directory and filename
            start_location = current_path
        else:
            # Default to .animica directory with wallets.json filename
            start_location = str(Path.home() / ".animica" / "wallets.json")
        
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Wallet File Location",
            start_location,
            "JSON Files (*.json);;All Files (*)"
        )
        
        if file_path:
            self.wallet_path_input.setText(file_path)
    
    def _update_info_text(self) -> None:
        """Update the info text based on the selected wallet path."""
        wallet_path = self.wallet_path_input.text().strip()
        if not wallet_path:
            wallet_path = str(Path.home() / ".animica" / "wallets.json")
        
        self.info_text.setText(
            f"A new wallet will be created and saved to {wallet_path}\n"
            "The wallet will use Dilithium3 post-quantum cryptography."
        )
    
    def create_wallet(self) -> None:
        """Create a new wallet using the wallet CLI functionality."""
        label = self.label_input.text().strip()
        wallet_file_path = self.wallet_path_input.text().strip()
        
        if not label:
            self.status_label.setText("Please enter a wallet label")
            self.status_label.setStyleSheet("color: red;")
            return
        
        if not wallet_file_path:
            wallet_file_path = str(Path.home() / ".animica" / "wallets.json")
        
        # Validate label: only allow alphanumeric, spaces, hyphens, and underscores
        # This prevents command injection and ensures clean wallet names
        if not re.match(r'^[\w\s\-]+$', label):
            self.status_label.setText("Label can only contain letters, numbers, spaces, hyphens, and underscores")
            self.status_label.setStyleSheet("color: red;")
            return
        
        if len(label) > 50:
            self.status_label.setText("Label too long (max 50 characters)")
            self.status_label.setStyleSheet("color: red;")
            return
        
        # Validate wallet file path
        try:
            wallet_path = Path(wallet_file_path)
            
            # Ensure the path is well-formed
            if wallet_path.suffix != '.json':
                self.status_label.setText("Wallet file must have .json extension")
                self.status_label.setStyleSheet("color: red;")
                return
            
            # Resolve the path to absolute to prevent directory traversal
            wallet_path_resolved = wallet_path.resolve()
            
            # Ensure parent directory exists or can be created
            # This is intentional to allow users to organize wallets in custom directories
            # The resolved path is already validated and made absolute, preventing traversal attacks
            wallet_path_resolved.parent.mkdir(parents=True, exist_ok=True)
            
            # Convert back to string for command
            wallet_file_path = str(wallet_path_resolved)
            
        except (ValueError, OSError) as e:
            self.status_label.setText(f"Invalid wallet file path: {e}")
            self.status_label.setStyleSheet("color: red;")
            return
        
        try:
            self.status_label.setText("Creating wallet...")
            self.status_label.setStyleSheet("color: blue;")
            
            # Call the wallet creation CLI with label and wallet file path as separate arguments
            # Using subprocess.run with a list of arguments (not shell=True) prevents command injection
            cmd = [
                sys.executable, "-m", "animica", "wallet", 
                "--wallet-file", wallet_file_path,
                "create", 
                "--label", label, 
                "--allow-insecure-fallback"
            ]
            
            # Log only the command structure for debugging (wallet path already resolved and validated)
            logger.debug(f"Creating wallet with label '{label}'")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                raise Exception(f"Wallet creation failed: {result.stderr}")
            
            # Parse the output to get the address using regex for robustness
            address_match = re.search(r'Address:\s*(anim1[a-z0-9]{39,})', result.stdout)
            if address_match:
                address = address_match.group(1)
            else:
                # Fallback to line-by-line parsing
                address = None
                for line in result.stdout.strip().split("\n"):
                    if line.startswith("Address:"):
                        address = line.split("Address:")[1].strip()
                        break
                
                if not address:
                    raise Exception("Could not parse wallet address from output")
            
            self.created_address = address
            self.status_label.setText(f"✓ Wallet created successfully at {wallet_file_path}!")
            self.status_label.setStyleSheet("color: green;")
            
            # Accept the dialog after successful creation
            self.accept()
            
        except subprocess.TimeoutExpired:
            self.status_label.setText("Wallet creation timed out")
            self.status_label.setStyleSheet("color: red;")
        except Exception as e:
            logger.error(f"Wallet creation failed: {e}")
            self.status_label.setText(f"Error: {str(e)}")
            self.status_label.setStyleSheet("color: red;")


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
        self.devnet_radio.setChecked(True)  # Default to devnet
        
        layout.addWidget(self.mainnet_radio)
        layout.addWidget(self.testnet_radio)
        layout.addWidget(self.devnet_radio)
        layout.addStretch()
        self.setLayout(layout)
        
        # Register fields
        self.registerField("mainnet", self.mainnet_radio)
        self.registerField("testnet", self.testnet_radio)
        self.registerField("devnet", self.devnet_radio)
    
    def validatePage(self) -> bool:
        """Validate the page before moving to next."""
        return True


class WalletConfigPage(QWizardPage):
    """Wallet/payout address configuration page."""
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setTitle("Payout Address")
        self.setSubTitle("Configure your wallet address for receiving mining rewards")
        
        layout = QVBoxLayout()
        
        # Manual address entry
        layout.addWidget(QLabel("Enter payout address:"))
        self.address_input = QLineEdit()
        self.address_input.setPlaceholderText("anim1...")
        self.address_input.textChanged.connect(lambda: self.completeChanged.emit())
        
        layout.addWidget(self.address_input)
        
        # Buttons layout
        buttons_layout = QHBoxLayout()
        
        # Create new wallet button
        self.create_button = QPushButton("Create New Wallet")
        self.create_button.clicked.connect(self.create_new_wallet)
        buttons_layout.addWidget(self.create_button)
        
        # Import from wallets.json button
        self.import_button = QPushButton("Import from Wallets")
        self.import_button.clicked.connect(self.import_from_wallets)
        buttons_layout.addWidget(self.import_button)
        
        buttons_layout.addStretch()
        
        layout.addLayout(buttons_layout)
        
        # Warning about wallet location
        warning_label = QLabel(
            "⚠️ Note: When importing from wallets, your wallets.json file must be located at ~/.animica/wallets.json\n"
            "or you can browse to select a custom wallet file location."
        )
        warning_label.setWordWrap(True)
        warning_label.setStyleSheet(
            "color: #ff9800; "
            "background-color: rgba(255, 152, 0, 0.1); "
            "padding: 8px; "
            "border-left: 3px solid #ff9800; "
            "border-radius: 3px; "
            "margin-top: 8px; "
            "margin-bottom: 8px;"
        )
        layout.addWidget(warning_label)
        
        # Validation status
        self.validation_label = QLabel("")
        layout.addWidget(self.validation_label)
        
        layout.addStretch()
        self.setLayout(layout)
        
        # Hidden field to store wallet file path
        self.wallet_file_path_input = QLineEdit()
        self.wallet_file_path_input.setVisible(False)
        layout.addWidget(self.wallet_file_path_input)
        
        self.registerField("payout_address*", self.address_input)
        self.registerField("wallet_file_path", self.wallet_file_path_input)
    
    def create_new_wallet(self) -> None:
        """Create a new wallet via dialog."""
        dialog = CreateWalletDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if dialog.created_address:
                self.address_input.setText(dialog.created_address)
                # Save the wallet file path used during creation
                wallet_path = dialog.wallet_path_input.text().strip()
                if wallet_path:
                    self.wallet_file_path_input.setText(wallet_path)
                self.validation_label.setText("✓ New wallet created and loaded")
                self.validation_label.setStyleSheet("color: green;")
    
    def import_from_wallets(self) -> None:
        """Import address from a wallets.json file with file browser."""
        # Open file dialog to choose wallets.json
        default_wallet_path = Path.home() / ".animica" / "wallets.json"
        
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Wallets File",
            str(default_wallet_path.parent),
            "JSON Files (*.json);;All Files (*)"
        )
        
        if not file_path:
            return  # User cancelled
        
        wallet_path = Path(file_path)
        
        if not wallet_path.exists():
            self.validation_label.setText("File not found")
            self.validation_label.setStyleSheet("color: red;")
            return
        
        try:
            with open(wallet_path, "r") as f:
                data = json.load(f)
            
            # Handle both list format and dict format
            if isinstance(data, dict) and "wallets" in data:
                wallets = data["wallets"]
            elif isinstance(data, list):
                wallets = data
            else:
                wallets = [data]  # Single wallet object
            
            if not wallets:
                self.validation_label.setText("No wallets in file")
                self.validation_label.setStyleSheet("color: orange;")
                return
            
            # If only one wallet, use it directly
            if len(wallets) == 1:
                address = wallets[0].get("address", "")
                label = wallets[0].get("label", "Unknown")
                
                if address:
                    self.address_input.setText(address)
                    # Save the wallet file path for later use
                    self.wallet_file_path_input.setText(str(wallet_path))
                    self.validation_label.setText(f"✓ Imported: {label}")
                    self.validation_label.setStyleSheet("color: green;")
                else:
                    self.validation_label.setText("No address in wallet")
                    self.validation_label.setStyleSheet("color: red;")
                return
            
            # Multiple wallets - show selection dialog
            dialog = QDialog(self)
            dialog.setWindowTitle("Select Wallet")
            dialog.setMinimumWidth(500)
            
            layout = QVBoxLayout()
            layout.addWidget(QLabel("Choose a wallet to import:"))
            
            wallet_list = QListWidget()
            for i, wallet in enumerate(wallets):
                label = wallet.get("label", f"Wallet {i+1}")
                address = wallet.get("address", "No address")
                item_text = f"{label} - {address[:20]}...{address[-10:]}" if len(address) > 30 else f"{label} - {address}"
                wallet_list.addItem(item_text)
            
            layout.addWidget(wallet_list)
            
            # Add buttons
            button_box = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
            )
            button_box.accepted.connect(dialog.accept)
            button_box.rejected.connect(dialog.reject)
            layout.addWidget(button_box)
            
            dialog.setLayout(layout)
            
            if dialog.exec() == QDialog.DialogCode.Accepted:
                selected_idx = wallet_list.currentRow()
                if selected_idx >= 0:
                    selected_wallet = wallets[selected_idx]
                    address = selected_wallet.get("address", "")
                    label = selected_wallet.get("label", "Unknown")
                    
                    if address:
                        self.address_input.setText(address)
                        # Save the wallet file path for later use
                        self.wallet_file_path_input.setText(str(wallet_path))
                        self.validation_label.setText(f"✓ Imported: {label}")
                        self.validation_label.setStyleSheet("color: green;")
                    else:
                        self.validation_label.setText("No address in selected wallet")
                        self.validation_label.setStyleSheet("color: red;")
        
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
        self.setSubTitle("Review your configuration and choose whether to start mining")
        
        layout = QVBoxLayout()
        
        self.summary_text = QTextEdit()
        self.summary_text.setReadOnly(True)
        
        layout.addWidget(self.summary_text)
        
        # Start mining checkbox with more descriptive label
        self.start_mining_checkbox = QCheckBox("Start mining immediately (uncheck to setup wallet only)")
        self.start_mining_checkbox.setChecked(True)
        self.start_mining_checkbox.toggled.connect(self._on_checkbox_toggled)
        layout.addWidget(self.start_mining_checkbox)
        
        # Help text
        self.help_label = QLabel(
            "You can start mining later from the main window if you choose wallet-only setup."
        )
        self.help_label.setWordWrap(True)
        self.help_label.setStyleSheet("color: gray; font-size: 11px;")
        self.help_label.setVisible(False)
        layout.addWidget(self.help_label)
        
        self.setLayout(layout)
        
        self.registerField("start_mining", self.start_mining_checkbox)
        
        # Cache for summary data
        self._network = ""
        self._preset = ""
        self._payout_address = ""
    
    def _on_checkbox_toggled(self, checked: bool) -> None:
        """Show/hide help text based on checkbox state and update mode in summary."""
        self.help_label.setVisible(not checked)
        # Update the summary display with the new mode
        self._update_mode_display()
    
    def _update_mode_display(self) -> None:
        """Update the complete summary with current mode and action text."""
        start_mining = self.start_mining_checkbox.isChecked()
        action_text = (
            "Click <b>Finish</b> to save this configuration and start mining."
            if start_mining
            else "Click <b>Finish</b> to save this configuration. You can start mining later from the main window."
        )
        
        summary = f"""
        <h3>Configuration Summary</h3>
        <table>
        <tr><td><b>Network:</b></td><td>{self._network}</td></tr>
        <tr><td><b>Payout Address:</b></td><td>{self._payout_address}</td></tr>
        <tr><td><b>Performance Preset:</b></td><td>{self._preset}</td></tr>
        <tr><td><b>Mode:</b></td><td>{"Start mining immediately" if start_mining else "Wallet setup only"}</td></tr>
        </table>
        <br>
        <p>{action_text}</p>
        """
        
        self.summary_text.setHtml(summary)
    
    def initializePage(self) -> None:
        """Generate summary from wizard fields."""
        # Network
        if self.field("mainnet"):
            self._network = "Mainnet"
        elif self.field("testnet"):
            self._network = "Testnet"
        else:
            self._network = "Devnet"
        
        # Preset
        if self.field("preset_max"):
            self._preset = "Maximum Performance"
        elif self.field("preset_safe"):
            self._preset = "Safe Mode"
        else:
            self._preset = "Recommended"
        
        # Payout address
        self._payout_address = self.field('payout_address')
        
        # Generate full summary
        self._update_mode_display()


class FirstRunWizard(QWizard):
    """First-run setup wizard."""
    
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        
        self.setWindowTitle("Animica Miner Setup")
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.HaveHelpButton, False)
        self.setMinimumSize(600, 500)
        
        # Set window icon
        from animica_miner_gui.resources import get_logo_path
        from PySide6.QtGui import QIcon
        logo_path = get_logo_path()
        if logo_path:
            self.setWindowIcon(QIcon(str(logo_path)))
        
        # Add pages
        self.addPage(NetworkSelectionPage())
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
            if self.field("mainnet"):
                config.network.network_type = NetworkType.MAINNET
            elif self.field("testnet"):
                config.network.network_type = NetworkType.TESTNET
            else:
                config.network.network_type = NetworkType.DEVNET
            
            # Payout address
            config.miner.payout_address = self.field("payout_address")
            
            # Wallet file path (if custom location was used)
            wallet_file_path = self.field("wallet_file_path")
            if wallet_file_path:
                config.miner.wallet_file = wallet_file_path
            
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
