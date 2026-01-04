"""Configuration tab - JSON editor with schema validation."""

import json
import logging
from typing import Optional

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_miner_gui.backend.config import (
    MiningAppConfig,
    get_default_config_path,
    load_config,
    save_config,
)

logger = logging.getLogger(__name__)


class ConfigurationTab(QWidget):
    """Configuration editor tab."""
    
    def __init__(self, config: MiningAppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.config = config
        self.setup_ui()
    
    def setup_ui(self) -> None:
        """Set up the UI."""
        layout = QVBoxLayout()
        
        # Info label
        info_label = QLabel(
            "Edit configuration as JSON. Changes are validated against the schema."
        )
        layout.addWidget(info_label)
        
        # JSON editor
        self.editor = QTextEdit()
        self.editor.setFontFamily("monospace")
        self.load_config_to_editor()
        layout.addWidget(self.editor)
        
        # Buttons
        button_layout = QHBoxLayout()
        
        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self.load_config_to_editor)
        button_layout.addWidget(reload_btn)
        
        validate_btn = QPushButton("Validate")
        validate_btn.clicked.connect(self.validate_config)
        button_layout.addWidget(validate_btn)
        
        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self.save_config_from_editor)
        button_layout.addWidget(save_btn)
        
        button_layout.addStretch()
        layout.addLayout(button_layout)
        
        # Status label
        self.status_label = QLabel("")
        layout.addWidget(self.status_label)
        
        self.setLayout(layout)
    
    def load_config_to_editor(self) -> None:
        """Load current configuration into the editor."""
        try:
            config_json = json.dumps(self.config.model_dump(), indent=2)
            self.editor.setPlainText(config_json)
            self.status_label.setText("Configuration loaded")
            self.status_label.setStyleSheet("color: green;")
        except Exception as e:
            logger.error(f"Error loading config to editor: {e}")
            self.status_label.setText(f"Error: {e}")
            self.status_label.setStyleSheet("color: red;")
    
    def validate_config(self) -> bool:
        """Validate the configuration in the editor."""
        try:
            text = self.editor.toPlainText()
            data = json.loads(text)
            
            # Try to parse with Pydantic
            MiningAppConfig(**data)
            
            self.status_label.setText("✓ Configuration is valid")
            self.status_label.setStyleSheet("color: green;")
            return True
        
        except json.JSONDecodeError as e:
            self.status_label.setText(f"✗ JSON parse error: {e}")
            self.status_label.setStyleSheet("color: red;")
            return False
        
        except Exception as e:
            self.status_label.setText(f"✗ Validation error: {e}")
            self.status_label.setStyleSheet("color: red;")
            return False
    
    def save_config_from_editor(self) -> None:
        """Save configuration from the editor."""
        if not self.validate_config():
            QMessageBox.warning(
                self,
                "Validation Error",
                "Configuration is invalid. Please fix errors before saving."
            )
            return
        
        try:
            text = self.editor.toPlainText()
            data = json.loads(text)
            new_config = MiningAppConfig(**data)
            
            save_config(new_config)
            self.config = new_config
            
            self.status_label.setText("✓ Configuration saved successfully")
            self.status_label.setStyleSheet("color: green;")
            
            QMessageBox.information(
                self,
                "Configuration Saved",
                "Configuration has been saved. Restart mining for changes to take effect."
            )
        
        except Exception as e:
            logger.error(f"Error saving config: {e}")
            self.status_label.setText(f"✗ Save error: {e}")
            self.status_label.setStyleSheet("color: red;")
            QMessageBox.critical(
                self,
                "Save Error",
                f"Failed to save configuration: {e}"
            )
