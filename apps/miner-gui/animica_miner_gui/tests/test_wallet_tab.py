"""Tests for wallet tab functionality."""

import pytest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

from animica_miner_gui.backend.config import MiningAppConfig
from animica_miner_gui.backend.node_controller import NodeController
from animica_miner_gui.ui.tabs.wallet import WalletTab

# Test constants
TEST_WALLET_ADDRESS = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
TEST_RECIPIENT_ADDRESS = "anim1zqp2pg8s9mjhyfkmkdwfxzyaw6tzn3afqt2jj4kd2un3uz89e7n2rggxgsw3p"
TEST_RPC_URL = "http://127.0.0.1:8545/rpc"


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
    config.network.rpc_url = TEST_RPC_URL
    return config


@pytest.fixture
def node_controller(qapp):
    """Node controller stub for tabs."""
    return NodeController()


@pytest.fixture
def config_no_address():
    """Config without a payout address."""
    config = MiningAppConfig()
    config.miner.payout_address = None
    return config


def test_wallet_tab_creation_with_address(qapp, config_with_address, node_controller):
    """Test wallet tab creation with configured address."""
    with patch('animica_miner_gui.ui.tabs.wallet.RPCClient'):
        tab = WalletTab(config_with_address, node_controller)
        
        # Check address label is set
        assert config_with_address.miner.payout_address in tab.address_label.text()
        
        # Check copy button is enabled
        assert tab.copy_address_button.isEnabled()
        
        # Check refresh button is enabled
        assert tab.refresh_balance_button.isEnabled()


def test_wallet_tab_creation_without_address(qapp, config_no_address, node_controller):
    """Test wallet tab creation without address."""
    with patch('animica_miner_gui.ui.tabs.wallet.RPCClient'):
        tab = WalletTab(config_no_address, node_controller)
        
        # Check address label shows "Not configured"
        assert "Not configured" in tab.address_label.text()
        
        # Check copy button is disabled
        assert not tab.copy_address_button.isEnabled()
        
        # Check refresh button is disabled
        assert not tab.refresh_balance_button.isEnabled()


def test_copy_address_to_clipboard(qapp, config_with_address, node_controller):
    """Test copying address to clipboard."""
    with patch('animica_miner_gui.ui.tabs.wallet.RPCClient'):
        tab = WalletTab(config_with_address, node_controller)
        
        # Mock clipboard and message box
        with patch.object(QApplication, 'clipboard') as mock_clipboard, \
             patch('animica_miner_gui.ui.tabs.wallet.QMessageBox.information') as mock_info:
            
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


def test_tx_send_command_uses_correct_rpc_option(qapp, config_with_address, node_controller):
    """Test that transaction send uses --rpc-url (not --rpc)."""
    with patch('animica_miner_gui.ui.tabs.wallet.RPCClient'):
        tab = WalletTab(config_with_address, node_controller)
        
        # Set up valid inputs
        tab.recipient_input.setText(TEST_RECIPIENT_ADDRESS)
        tab.amount_input.setText("1.0")
        
        # Mock subprocess, message boxes, and wallet file check
        with patch('animica_miner_gui.ui.tabs.wallet.subprocess.run') as mock_run, \
             patch('animica_miner_gui.ui.tabs.wallet.QMessageBox.question') as mock_question, \
             patch('animica_miner_gui.ui.tabs.wallet.QMessageBox.information'), \
             patch('animica_miner_gui.ui.tabs.wallet.os.path.exists', return_value=True), \
             patch('builtins.open', MagicMock()):
            
            # Mock the wallet file to contain the address
            import json
            mock_wallet_data = {
                "wallets": [
                    {"address": TEST_WALLET_ADDRESS, "label": "test"}
                ]
            }
            with patch('animica_miner_gui.ui.tabs.wallet.json.load', return_value=mock_wallet_data):
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
                cmd = call_args[0][0] if call_args[0] else call_args.kwargs.get('args', [])
                
                # Verify --rpc-url is used (not --rpc)
                assert "--rpc-url" in cmd
                assert "--rpc" not in cmd
                
                # Verify RPC URL is passed
                rpc_idx = cmd.index("--rpc-url")
                assert cmd[rpc_idx + 1] == config_with_address.network.resolved_rpc_url()


def test_tx_send_validation_no_address(qapp, config_no_address, node_controller):
    """Test that send fails gracefully without configured address."""
    with patch('animica_miner_gui.ui.tabs.wallet.RPCClient'):
        tab = WalletTab(config_no_address, node_controller)
        
        with patch('animica_miner_gui.ui.tabs.wallet.QMessageBox.warning') as mock_warning:
            tab.send_transaction()
            
            # Should show warning about no wallet
            mock_warning.assert_called_once()
            call_args = mock_warning.call_args[0]
            assert "No Wallet" in call_args or "payout address" in str(call_args)


def test_tx_send_validation_invalid_recipient(qapp, config_with_address, node_controller):
    """Test transaction validation for invalid recipient."""
    with patch('animica_miner_gui.ui.tabs.wallet.RPCClient'):
        tab = WalletTab(config_with_address, node_controller)
        
        # Set invalid recipient (too short)
        tab.recipient_input.setText("anim1short")
        tab.amount_input.setText("1.0")
        
        with patch('animica_miner_gui.ui.tabs.wallet.QMessageBox.warning') as mock_warning:
            tab.send_transaction()
            
            # Should show warning about invalid address
            mock_warning.assert_called()


def test_tx_send_validation_invalid_amount(qapp, config_with_address, node_controller):
    """Test transaction validation for invalid amount."""
    with patch('animica_miner_gui.ui.tabs.wallet.RPCClient'):
        tab = WalletTab(config_with_address, node_controller)
        
        tab.recipient_input.setText(TEST_RECIPIENT_ADDRESS)
        
        # Test negative amount
        tab.amount_input.setText("-1.0")
        with patch('animica_miner_gui.ui.tabs.wallet.QMessageBox.warning') as mock_warning:
            tab.send_transaction()
            mock_warning.assert_called()
        
        # Test zero amount
        tab.amount_input.setText("0")
        with patch('animica_miner_gui.ui.tabs.wallet.QMessageBox.warning') as mock_warning:
            tab.send_transaction()
            mock_warning.assert_called()
        
        # Test non-numeric amount
        tab.amount_input.setText("not_a_number")
        with patch('animica_miner_gui.ui.tabs.wallet.QMessageBox.warning') as mock_warning:
            tab.send_transaction()
            mock_warning.assert_called()


def test_wallet_info_refresh(qapp, config_with_address, node_controller):
    """Test wallet info refresh functionality."""
    with patch('animica_miner_gui.ui.tabs.wallet.RPCClient') as mock_rpc_class:
        # Mock the RPC client instance
        mock_rpc_instance = MagicMock()
        mock_rpc_class.return_value = mock_rpc_instance
        
        # Mock balance and nonce responses using the new public methods
        mock_rpc_instance.get_balance.return_value = 1_500_000_000  # 1.5 ANM in base units
        mock_rpc_instance.get_nonce.return_value = 5
        
        tab = WalletTab(config_with_address, node_controller)
        
        # Trigger refresh
        tab.refresh_wallet_info()
        
        # Check that balance and nonce labels were updated
        assert "1.5" in tab.balance_label.text() or "ANM" in tab.balance_label.text()
        assert "5" in tab.nonce_label.text() or tab.nonce_label.text() != "--"


def test_wallet_info_refresh_no_address(qapp, config_no_address, node_controller):
    """Test wallet info refresh when no address is configured."""
    with patch('animica_miner_gui.ui.tabs.wallet.RPCClient'):
        tab = WalletTab(config_no_address, node_controller)
        
        # Trigger refresh
        tab.refresh_wallet_info()
        
        # Should show appropriate message
        assert "No payout address" in tab.balance_label.text()
        assert tab.nonce_label.text() == "--"


def test_wallet_info_refresh_zero_balance_and_nonce(qapp, config_with_address, node_controller):
    """Test wallet info refresh handles zero balance and nonce correctly."""
    with patch('animica_miner_gui.ui.tabs.wallet.RPCClient') as mock_rpc_class:
        # Mock the RPC client instance
        mock_rpc_instance = MagicMock()
        mock_rpc_class.return_value = mock_rpc_instance
        
        # Mock zero balance and zero nonce (new address)
        mock_rpc_instance.get_balance.return_value = 0
        mock_rpc_instance.get_nonce.return_value = 0
        
        tab = WalletTab(config_with_address, node_controller)
        
        # Trigger refresh
        tab.refresh_wallet_info()
        
        # Check that zero balance and nonce are displayed correctly
        assert "0.000000000 ANM" in tab.balance_label.text()
        assert tab.nonce_label.text() == "0"
