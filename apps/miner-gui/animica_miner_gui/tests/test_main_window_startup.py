"""Tests for MainWindow startup without rpc_url."""

from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QApplication

from animica_miner_gui.backend.config import MiningAppConfig
from animica_miner_gui.ui.main_window import MainWindow


@pytest.fixture
def qapp():
    """Provide QApplication instance for tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_main_window_init_without_rpc(qapp):
    """MainWindow should initialize even when rpc_url is missing."""
    config = MiningAppConfig()
    config.network.rpc_url = None

    with patch("animica_miner_gui.ui.main_window.load_config", return_value=config), \
         patch("animica_miner_gui.backend.node_controller.NodeController.start"):
        window = MainWindow()
        assert window.config.network.rpc_url is None
