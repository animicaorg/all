"""Console tab - RPC-backed command console."""

from __future__ import annotations

import json
from typing import Optional

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from animica_miner_gui.backend.console_router import ConsoleRouter
from animica_miner_gui.backend.node_controller import NodeController
from animica_miner_gui.backend.rpc_client import RPCClient


class ConsoleTab(QWidget):
    """Interactive console tab."""

    def __init__(self, node_controller: NodeController, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.node_controller = node_controller
        self.rpc_client: Optional[RPCClient] = None
        self.router: Optional[ConsoleRouter] = None
        self._setup_ui()
        self.node_controller.rpcChanged.connect(self._on_rpc_changed)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()
        hint = QLabel("Commands: animica node status | animica peer bootstrap <addr> | rpc <method> <json>")
        layout.addWidget(hint)

        self.output = QTextEdit()
        self.output.setReadOnly(True)
        self.output.setFontFamily("monospace")
        layout.addWidget(self.output)

        input_layout = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Enter command...")
        self.input.returnPressed.connect(self._run_command)
        run_button = QPushButton("Run")
        run_button.clicked.connect(self._run_command)
        input_layout.addWidget(self.input)
        input_layout.addWidget(run_button)
        layout.addLayout(input_layout)

        self.setLayout(layout)

    def _on_rpc_changed(self, rpc_url: str, token: str) -> None:
        self.rpc_client = RPCClient(rpc_url, token=token)
        self.router = ConsoleRouter(self.rpc_client)
        self._append_line(f"Connected to RPC: {rpc_url}")

    def _run_command(self) -> None:
        cmd = self.input.text().strip()
        if not cmd:
            return
        self._append_line(f"> {cmd}")
        self.input.clear()

        if not self.router:
            self._append_line("RPC not ready yet.")
            return

        result = self.router.handle(cmd)
        self._append_line(json.dumps(result, indent=2, sort_keys=True))

    def _append_line(self, line: str) -> None:
        self.output.append(line)
