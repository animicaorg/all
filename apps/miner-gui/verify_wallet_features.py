#!/usr/bin/env python3
"""
Manual verification script for wallet creation and skip mining features.

This script demonstrates the new features by showing the workflow
and can be used for manual testing when a display is available.

Usage:
    python verify_wallet_features.py

Requirements:
    - PySide6 installed
    - Display environment (X11/Wayland/macOS/Windows)
    - animica package installed
"""

import sys


def verify_imports():
    """Verify that all required components can be imported."""
    print("=" * 60)
    print("STEP 1: Verifying imports...")
    print("=" * 60)
    
    try:
        from animica_miner_gui.ui.wizard import (
            FirstRunWizard,
            WalletConfigPage,
            CreateWalletDialog,
            SummaryPage,
        )
        print("✓ All wizard components imported successfully")
        return True
    except ImportError as e:
        print(f"✗ Import failed: {e}")
        return False


def verify_wallet_config_page():
    """Verify WalletConfigPage has new features."""
    print("\n" + "=" * 60)
    print("STEP 2: Verifying WalletConfigPage features...")
    print("=" * 60)
    
    try:
        from animica_miner_gui.ui.wizard import WalletConfigPage
        from PySide6.QtWidgets import QWizard, QApplication
        
        app = QApplication.instance() or QApplication(sys.argv)
        wizard = QWizard()
        page = WalletConfigPage()
        wizard.addPage(page)
        
        # Check for new button
        assert hasattr(page, 'create_button'), "Missing create_button"
        assert hasattr(page, 'import_button'), "Missing import_button"
        assert hasattr(page, 'address_input'), "Missing address_input"
        
        print("✓ WalletConfigPage has create_button")
        print("✓ WalletConfigPage has import_button")
        print("✓ WalletConfigPage has address_input")
        
        # Check button text
        create_text = page.create_button.text()
        import_text = page.import_button.text()
        
        assert "Create" in create_text, f"Unexpected create button text: {create_text}"
        assert "Import" in import_text, f"Unexpected import button text: {import_text}"
        
        print(f"✓ Create button text: '{create_text}'")
        print(f"✓ Import button text: '{import_text}'")
        
        return True
        
    except Exception as e:
        print(f"✗ Verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_create_wallet_dialog():
    """Verify CreateWalletDialog exists and has required components."""
    print("\n" + "=" * 60)
    print("STEP 3: Verifying CreateWalletDialog...")
    print("=" * 60)
    
    try:
        from animica_miner_gui.ui.wizard import CreateWalletDialog
        from PySide6.QtWidgets import QApplication
        
        app = QApplication.instance() or QApplication(sys.argv)
        dialog = CreateWalletDialog()
        
        assert hasattr(dialog, 'label_input'), "Missing label_input"
        assert hasattr(dialog, 'status_label'), "Missing status_label"
        assert hasattr(dialog, 'created_address'), "Missing created_address"
        
        print("✓ CreateWalletDialog has label_input")
        print("✓ CreateWalletDialog has status_label")
        print("✓ CreateWalletDialog has created_address attribute")
        
        # Check initial state
        assert dialog.created_address is None, "created_address should be None initially"
        print("✓ created_address is None initially (correct)")
        
        return True
        
    except Exception as e:
        print(f"✗ Verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def verify_summary_page():
    """Verify SummaryPage has new features."""
    print("\n" + "=" * 60)
    print("STEP 4: Verifying SummaryPage features...")
    print("=" * 60)
    
    try:
        from animica_miner_gui.ui.wizard import SummaryPage
        from PySide6.QtWidgets import QWizard, QApplication
        
        app = QApplication.instance() or QApplication(sys.argv)
        wizard = QWizard()
        page = SummaryPage()
        wizard.addPage(page)
        
        assert hasattr(page, 'start_mining_checkbox'), "Missing start_mining_checkbox"
        assert hasattr(page, 'help_label'), "Missing help_label"
        
        print("✓ SummaryPage has start_mining_checkbox")
        print("✓ SummaryPage has help_label")
        
        # Check checkbox label
        checkbox_text = page.start_mining_checkbox.text()
        assert "wallet only" in checkbox_text.lower(), f"Checkbox text doesn't mention wallet-only: {checkbox_text}"
        print(f"✓ Checkbox text mentions wallet-only: '{checkbox_text}'")
        
        # Check checkbox default state
        assert page.start_mining_checkbox.isChecked(), "Checkbox should be checked by default"
        print("✓ Checkbox is checked by default")
        
        # Check help label initial visibility
        assert not page.help_label.isVisible(), "Help label should be hidden initially"
        print("✓ Help label is hidden initially (checkbox checked)")
        
        # Uncheck and verify help label appears
        page.start_mining_checkbox.setChecked(False)
        assert page.help_label.isVisible(), "Help label should be visible when unchecked"
        print("✓ Help label appears when checkbox is unchecked")
        
        # Check back and verify help label is hidden
        page.start_mining_checkbox.setChecked(True)
        assert not page.help_label.isVisible(), "Help label should be hidden when checked"
        print("✓ Help label is hidden when checkbox is checked again")
        
        return True
        
    except Exception as e:
        print(f"✗ Verification failed: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all verification steps."""
    print("\n" + "=" * 60)
    print("WALLET CREATION AND SKIP MINING FEATURE VERIFICATION")
    print("=" * 60)
    
    results = []
    
    # Run verifications
    results.append(("Imports", verify_imports()))
    
    # Only continue if imports succeeded
    if results[0][1]:
        results.append(("WalletConfigPage", verify_wallet_config_page()))
        results.append(("CreateWalletDialog", verify_create_wallet_dialog()))
        results.append(("SummaryPage", verify_summary_page()))
    
    # Print summary
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    
    for name, success in results:
        status = "✓ PASSED" if success else "✗ FAILED"
        print(f"{name:20s}: {status}")
    
    all_passed = all(success for _, success in results)
    
    print("\n" + "=" * 60)
    if all_passed:
        print("ALL VERIFICATIONS PASSED ✓")
        print("=" * 60)
        print("\nNext steps:")
        print("1. Run the GUI: animica-miner-gui")
        print("2. Click through the wizard")
        print("3. Test 'Create New Wallet' button on wallet page")
        print("4. Test 'Start mining immediately' checkbox on summary page")
        print("5. Verify wallet-only mode works (uncheck the checkbox)")
        return 0
    else:
        print("SOME VERIFICATIONS FAILED ✗")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
