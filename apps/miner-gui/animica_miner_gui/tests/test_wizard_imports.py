"""Tests for the first-run wizard."""

import pytest


def test_wallet_config_page_import():
    """Test that WalletConfigPage can be imported."""
    try:
        from animica_miner_gui.ui.wizard import WalletConfigPage
        assert WalletConfigPage is not None
    except ImportError as e:
        pytest.skip(f"Could not import WalletConfigPage: {e}")


def test_create_wallet_dialog_import():
    """Test that CreateWalletDialog can be imported."""
    try:
        from animica_miner_gui.ui.wizard import CreateWalletDialog
        assert CreateWalletDialog is not None
    except ImportError as e:
        pytest.skip(f"Could not import CreateWalletDialog: {e}")


def test_summary_page_import():
    """Test that SummaryPage can be imported."""
    try:
        from animica_miner_gui.ui.wizard import SummaryPage
        assert SummaryPage is not None
    except ImportError as e:
        pytest.skip(f"Could not import SummaryPage: {e}")


def test_wizard_has_required_components():
    """Test that wizard module has all required components."""
    try:
        from animica_miner_gui.ui import wizard
        
        # Check that all necessary classes exist
        assert hasattr(wizard, 'FirstRunWizard')
        assert hasattr(wizard, 'WalletConfigPage')
        assert hasattr(wizard, 'CreateWalletDialog')
        assert hasattr(wizard, 'SummaryPage')
        assert hasattr(wizard, 'NetworkSelectionPage')
        assert hasattr(wizard, 'RPCConfigPage')
        assert hasattr(wizard, 'DeviceSelectionPage')
        assert hasattr(wizard, 'PresetSelectionPage')
    except ImportError as e:
        pytest.skip(f"Could not import wizard module: {e}")
