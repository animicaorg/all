"""Tests for wallet tab functionality."""

from unittest.mock import MagicMock, patch

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from animica_miner_gui.backend.config import MiningAppConfig
from animica_miner_gui.core.localnode import LocalNodeManager
from animica_miner_gui.ui.main_window import MainWindow
from animica_miner_gui.ui.tabs.wallet import WalletTab

# Test constants
TEST_WALLET_ADDRESS = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
TEST_RECIPIENT_ADDRESS = "anim1zqp2pg8s9mjhyfkmkdwfxzyaw6tzn3afqt2jj4kd2un3uz89e7n2rggxgsw3p"
TEST_RPC_URL = "http://127.0.0.1:1234/rpc"


class StubBackend(QObject):
    nodeReady = Signal(object)
    nodeError = Signal(str)
    syncStatus = Signal(object)
    walletUpdated = Signal(object)

    def __init__(self):
        super().__init__()
        self._rpc = None

    def ensureNodeRunning(self) -> None:
        return None

    def getRpc(self):
        return self._rpc

    def isReady(self) -> bool:
        return self._rpc is not None

    def set_ready(self, rpc_client) -> None:
        self._rpc = rpc_client
        self.nodeReady.emit(rpc_client)

    def set_error(self, message: str) -> None:
        self._rpc = None
        self.nodeError.emit(message)


@pytest.fixture
def qapp():
    """Provide QApplication instance for tests."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@pytest.fixture
def config_with_address():
    """Config with a valid payout address."""
    config = MiningAppConfig()
    config.miner.payout_address = TEST_WALLET_ADDRESS
    return config


@pytest.fixture
def config_no_address():
    """Config without a payout address."""
    config = MiningAppConfig()
    config.miner.payout_address = None
    return config


@pytest.fixture
def backend_stub():
    return StubBackend()


def test_wallet_tab_creation_with_address(qapp, config_with_address, backend_stub):
    """Test wallet tab creation with configured address."""
    tab = WalletTab(config_with_address, backend_stub)

    # Check address label is set
    assert config_with_address.miner.payout_address in tab.address_label.text()

    # Check copy button is enabled
    assert tab.copy_address_button.isEnabled()

    # Refresh button stays disabled until node is ready
    assert not tab.refresh_balance_button.isEnabled()


def test_wallet_tab_creation_without_address(qapp, config_no_address, backend_stub):
    """Test wallet tab creation without address."""
    tab = WalletTab(config_no_address, backend_stub)

    # Check address label shows "Not configured"
    assert "Not configured" in tab.address_label.text()

    # Check copy button is disabled
    assert not tab.copy_address_button.isEnabled()

    # Check refresh button is disabled
    assert not tab.refresh_balance_button.isEnabled()


def test_copy_address_to_clipboard(qapp, config_with_address, backend_stub):
    """Test copying address to clipboard."""
    tab = WalletTab(config_with_address, backend_stub)

    # Mock clipboard and message box
    with patch.object(QApplication, "clipboard") as mock_clipboard, \
         patch("animica_miner_gui.ui.tabs.wallet.QMessageBox.information") as mock_info:

        mock_clipboard_instance = MagicMock()
        mock_clipboard.return_value = mock_clipboard_instance

        # Trigger copy
        tab.copy_address_to_clipboard()

        # Verify clipboard.setText was called with the address
        mock_clipboard_instance.setText.assert_called_once_with(
            config_with_address.miner.payout_address
        )

        # Verify success message shown
        mock_info.assert_called_once()


def test_tx_send_command_uses_local_rpc(qapp, config_with_address, backend_stub):
    """Test that transaction send uses the local RPC URL."""
    tab = WalletTab(config_with_address, backend_stub)

    # Mark backend ready with a mock RPC client
    mock_rpc_client = MagicMock()
    mock_rpc_client.rpc_url = TEST_RPC_URL
    backend_stub.set_ready(mock_rpc_client)

    # Set up valid inputs
    tab.recipient_input.setText(TEST_RECIPIENT_ADDRESS)
    tab.amount_input.setText("1.0")

    # Mock subprocess, message boxes, and wallet file check
    with patch("animica_miner_gui.ui.tabs.wallet.subprocess.run") as mock_run, \
         patch("animica_miner_gui.ui.tabs.wallet.QMessageBox.question") as mock_question, \
         patch("animica_miner_gui.ui.tabs.wallet.QMessageBox.information"), \
         patch("animica_miner_gui.ui.tabs.wallet.os.path.exists", return_value=True), \
         patch("builtins.open", MagicMock()):

        # Mock the wallet file to contain the address
        mock_wallet_data = {
            "wallets": [
                {"address": TEST_WALLET_ADDRESS, "label": "test"}
            ]
        }
        with patch("animica_miner_gui.ui.tabs.wallet.json.load", return_value=mock_wallet_data):
            # Mock user confirming transaction
            from PySide6.QtWidgets import QMessageBox
            mock_question.return_value = QMessageBox.StandardButton.Yes

            # Mock successful subprocess result
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "Transaction sent successfully"
            mock_result.stderr = ""
            mock_run.return_value = mock_result

            # Trigger send
            tab.send_transaction()

            # Verify subprocess.run was called
            assert mock_run.called

            # Get the command that was passed
            call_args = mock_run.call_args
            cmd = call_args[0][0] if call_args[0] else call_args.kwargs.get("args", [])

            # Verify --rpc-url is used (not --rpc)
            assert "--rpc-url" in cmd
            assert "--rpc" not in cmd

            # Verify RPC URL is passed from the local client
            rpc_idx = cmd.index("--rpc-url")
            assert cmd[rpc_idx + 1] == TEST_RPC_URL


def test_tx_send_validation_no_address(qapp, config_no_address, backend_stub):
    """Test that send fails gracefully without configured address."""
    tab = WalletTab(config_no_address, backend_stub)

    mock_rpc_client = MagicMock()
    mock_rpc_client.rpc_url = TEST_RPC_URL
    backend_stub.set_ready(mock_rpc_client)

    with patch("animica_miner_gui.ui.tabs.wallet.QMessageBox.warning") as mock_warning:
        tab.send_transaction()

        # Should show warning about no wallet
        mock_warning.assert_called_once()
        call_args = mock_warning.call_args[0]
        assert "No Wallet" in call_args or "payout address" in str(call_args)


def test_tx_send_validation_invalid_recipient(qapp, config_with_address, backend_stub):
    """Test transaction validation for invalid recipient."""
    tab = WalletTab(config_with_address, backend_stub)

    mock_rpc_client = MagicMock()
    mock_rpc_client.rpc_url = TEST_RPC_URL
    backend_stub.set_ready(mock_rpc_client)

    # Set invalid recipient (too short)
    tab.recipient_input.setText("anim1short")
    tab.amount_input.setText("1.0")

    with patch("animica_miner_gui.ui.tabs.wallet.QMessageBox.warning") as mock_warning:
        tab.send_transaction()

        # Should show warning about invalid address
        mock_warning.assert_called()


def test_tx_send_validation_invalid_amount(qapp, config_with_address, backend_stub):
    """Test transaction validation for invalid amount."""
    tab = WalletTab(config_with_address, backend_stub)

    mock_rpc_client = MagicMock()
    mock_rpc_client.rpc_url = TEST_RPC_URL
    backend_stub.set_ready(mock_rpc_client)

    tab.recipient_input.setText(TEST_RECIPIENT_ADDRESS)

    # Test negative amount
    tab.amount_input.setText("-1.0")
    with patch("animica_miner_gui.ui.tabs.wallet.QMessageBox.warning") as mock_warning:
        tab.send_transaction()
        mock_warning.assert_called()

    # Test zero amount
    tab.amount_input.setText("0")
    with patch("animica_miner_gui.ui.tabs.wallet.QMessageBox.warning") as mock_warning:
        tab.send_transaction()
        mock_warning.assert_called()

    # Test non-numeric amount
    tab.amount_input.setText("not_a_number")
    with patch("animica_miner_gui.ui.tabs.wallet.QMessageBox.warning") as mock_warning:
        tab.send_transaction()
        mock_warning.assert_called()


def test_wallet_info_refresh(qapp, config_with_address, backend_stub):
    """Test wallet info refresh functionality."""
    mock_rpc_instance = MagicMock()
    mock_rpc_instance.get_balance.return_value = 1_500_000_000  # 1.5 ANM in base units
    mock_rpc_instance.get_nonce.return_value = 5
    mock_rpc_instance.rpc_url = TEST_RPC_URL

    tab = WalletTab(config_with_address, backend_stub)
    backend_stub.set_ready(mock_rpc_instance)

    # Trigger refresh
    tab.refresh_wallet_info()

    # Check that balance and nonce labels were updated
    assert "1.5" in tab.balance_label.text() or "ANM" in tab.balance_label.text()
    assert "5" in tab.nonce_label.text() or tab.nonce_label.text() != "--"


def test_wallet_info_refresh_no_address(qapp, config_no_address, backend_stub):
    """Test wallet info refresh when no address is configured."""
    mock_rpc_instance = MagicMock()
    mock_rpc_instance.rpc_url = TEST_RPC_URL

    tab = WalletTab(config_no_address, backend_stub)
    backend_stub.set_ready(mock_rpc_instance)

    # Trigger refresh
    tab.refresh_wallet_info()

    # Should show appropriate message
    assert "No payout address" in tab.balance_label.text()
    assert tab.nonce_label.text() == "--"


def test_wallet_info_refresh_zero_balance_and_nonce(qapp, config_with_address, backend_stub):
    """Test wallet info refresh handles zero balance and nonce correctly."""
    mock_rpc_instance = MagicMock()
    mock_rpc_instance.get_balance.return_value = 0
    mock_rpc_instance.get_nonce.return_value = 0
    mock_rpc_instance.rpc_url = TEST_RPC_URL

    tab = WalletTab(config_with_address, backend_stub)
    backend_stub.set_ready(mock_rpc_instance)

    # Trigger refresh
    tab.refresh_wallet_info()

    # Check that zero balance and nonce are displayed correctly
    assert "0.000000000 ANM" in tab.balance_label.text()
    assert tab.nonce_label.text() == "0"


def test_wallet_tab_refresh_before_node_ready(qapp, config_with_address, backend_stub):
    """Test refresh is safe before the node is ready."""
    tab = WalletTab(config_with_address, backend_stub)

    tab.refresh_wallet_info()

    assert "Node starting" in tab.balance_label.text()


def test_main_window_wallet_tab_starts_in_starting_state(qapp, backend_stub):
    """MainWindow should construct WalletTab without requiring node readiness."""
    node_manager = LocalNodeManager(network="devnet")
    window = MainWindow(node_manager=node_manager, backend=backend_stub)

    assert "Node starting" in window.wallet_tab.balance_label.text()
