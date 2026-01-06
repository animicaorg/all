"""Tests for wallet tab functionality."""

import pytest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

from animica_miner_gui.backend.config import MiningAppConfig
from animica_miner_gui.ui.tabs.wallet import WalletTab

# Test constants
TEST_WALLET_ADDRESS = "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz"
TEST_RECIPIENT_ADDRESS = "anim1zqp2pg8s9mjhyfkmkdwfxzyaw6tzn3afqt2jj4kd2un3uz89e7n2rggxgsw3p"
TEST_RPC_URL = "https://rpc.mainnet.animica.org/rpc"


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
def config_no_address():
    """Config without a payout address."""
    config = MiningAppConfig()
    config.miner.payout_address = None
    return config


def test_wallet_tab_creation_with_address(qapp, config_with_address):
    """Test wallet tab creation with configured address."""
    tab = WalletTab(config_with_address)
    
    # Check address label is set
    assert config_with_address.miner.payout_address in tab.address_label.text()
    
    # Check copy button is enabled
    assert tab.copy_address_button.isEnabled()


def test_wallet_tab_creation_without_address(qapp, config_no_address):
    """Test wallet tab creation without address."""
    tab = WalletTab(config_no_address)
    
    # Check address label shows "Not configured"
    assert "Not configured" in tab.address_label.text()
    
    # Check copy button is disabled
    assert not tab.copy_address_button.isEnabled()


def test_copy_address_to_clipboard(qapp, config_with_address):
    """Test copying address to clipboard."""
    tab = WalletTab(config_with_address)
    
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


def test_tx_send_command_uses_correct_rpc_option(qapp, config_with_address):
    """Test that transaction send uses --rpc-url (not --rpc)."""
    tab = WalletTab(config_with_address)
    
    # Set up valid inputs
    tab.recipient_input.setText(TEST_RECIPIENT_ADDRESS)
    tab.amount_input.setText("1.0")
    
    # Mock subprocess and message boxes
    with patch('animica_miner_gui.ui.tabs.wallet.subprocess.run') as mock_run, \
         patch('animica_miner_gui.ui.tabs.wallet.QMessageBox.question') as mock_question, \
         patch('animica_miner_gui.ui.tabs.wallet.QMessageBox.information'):
        
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
        assert cmd[rpc_idx + 1] == config_with_address.network.rpc_url


def test_tx_send_validation_no_address(qapp, config_no_address):
    """Test that send fails gracefully without configured address."""
    tab = WalletTab(config_no_address)
    
    with patch('animica_miner_gui.ui.tabs.wallet.QMessageBox.warning') as mock_warning:
        tab.send_transaction()
        
        # Should show warning about no wallet
        mock_warning.assert_called_once()
        call_args = mock_warning.call_args[0]
        assert "No Wallet" in call_args or "payout address" in str(call_args)


def test_tx_send_validation_invalid_recipient(qapp, config_with_address):
    """Test transaction validation for invalid recipient."""
    tab = WalletTab(config_with_address)
    
    # Set invalid recipient (too short)
    tab.recipient_input.setText("anim1short")
    tab.amount_input.setText("1.0")
    
    with patch('animica_miner_gui.ui.tabs.wallet.QMessageBox.warning') as mock_warning:
        tab.send_transaction()
        
        # Should show warning about invalid address
        mock_warning.assert_called()


def test_tx_send_validation_invalid_amount(qapp, config_with_address):
    """Test transaction validation for invalid amount."""
    tab = WalletTab(config_with_address)
    
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
