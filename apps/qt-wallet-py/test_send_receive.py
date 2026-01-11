#!/usr/bin/env python3
"""
Test script for wallet send/receive functionality.

This script validates the send/receive implementation by checking:
1. walletd server has the new transaction methods
2. walletd_manager has the new client methods
3. UI tabs are properly created and integrated
"""

import ast
import sys
from pathlib import Path


def check_file_for_methods(file_path: Path, methods: list[str]) -> tuple[bool, list[str]]:
    """Check if a Python file contains the specified methods."""
    try:
        with open(file_path) as f:
            tree = ast.parse(f.read(), filename=str(file_path))
        
        found_methods = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in methods:
                    found_methods.append(node.name)
        
        missing = [m for m in methods if m not in found_methods]
        return len(missing) == 0, missing
    except Exception as e:
        print(f"Error checking {file_path}: {e}")
        return False, methods


def check_imports(file_path: Path, required_imports: list[str]) -> tuple[bool, list[str]]:
    """Check if a Python file has the required imports."""
    try:
        with open(file_path) as f:
            content = f.read()
        
        found = []
        for imp in required_imports:
            if imp in content:
                found.append(imp)
        
        missing = [i for i in required_imports if i not in found]
        return len(missing) == 0, missing
    except Exception as e:
        print(f"Error checking imports in {file_path}: {e}")
        return False, required_imports


def check_file_exists(file_path: Path) -> bool:
    """Check if a file exists."""
    return file_path.exists() and file_path.is_file()


def main():
    """Run validation checks."""
    base_dir = Path(__file__).parent / "src" / "animica_qt_wallet"
    
    print("=" * 60)
    print("Wallet Send/Receive Functionality Validation")
    print("=" * 60)
    
    all_passed = True
    
    # Check walletd server
    print("\n[1] Checking walletd server...")
    server_file = base_dir / "walletd" / "server.py"
    server_methods = [
        "_compute_tx_hash",
        "dispatch",
    ]
    passed, missing = check_file_for_methods(server_file, server_methods)
    if passed:
        # Check for transaction method handlers in dispatch
        with open(server_file) as f:
            content = f.read()
            tx_methods = [
                'method == "tx.estimateFees"',
                'method == "tx.build"',
                'method == "tx.sign"',
                'method == "tx.send"',
                'method == "tx.get"',
            ]
            for tm in tx_methods:
                if tm not in content:
                    print(f"  ❌ Missing handler: {tm}")
                    passed = False
                else:
                    print(f"  ✓ Found handler: {tm}")
    else:
        print(f"  ❌ Missing methods: {missing}")
    
    if passed:
        print("  ✓ walletd server: PASSED")
    else:
        print("  ❌ walletd server: FAILED")
        all_passed = False
    
    # Check walletd_manager
    print("\n[2] Checking walletd_manager...")
    manager_file = base_dir / "core" / "walletd_manager.py"
    manager_methods = [
        "tx_estimate_fees",
        "tx_build",
        "tx_sign",
        "tx_send",
        "tx_get",
    ]
    passed, missing = check_file_for_methods(manager_file, manager_methods)
    if passed:
        print(f"  ✓ All methods found: {', '.join(manager_methods)}")
    else:
        print(f"  ❌ Missing methods: {missing}")
    
    if passed:
        print("  ✓ walletd_manager: PASSED")
    else:
        print("  ❌ walletd_manager: FAILED")
        all_passed = False
    
    # Check Send tab
    print("\n[3] Checking Send tab...")
    send_tab_file = base_dir / "ui" / "send_tab.py"
    if not check_file_exists(send_tab_file):
        print(f"  ❌ Send tab file does not exist: {send_tab_file}")
        passed = False
        all_passed = False
    else:
        send_tab_methods = [
            "refresh_accounts",
            "_handle_send",
            "_send_transaction",
            "_map_error",
        ]
        passed, missing = check_file_for_methods(send_tab_file, send_tab_methods)
        if passed:
            print(f"  ✓ All Send tab methods found")
        else:
            print(f"  ❌ Missing Send tab methods: {missing}")
            all_passed = False
        
        # Check for required classes
        with open(send_tab_file) as f:
            content = f.read()
            required_classes = ["SendTab", "SendConfirmDialog", "SendSuccessDialog"]
            for cls in required_classes:
                if f"class {cls}" in content:
                    print(f"  ✓ Found class: {cls}")
                else:
                    print(f"  ❌ Missing class: {cls}")
                    passed = False
                    all_passed = False
    
    if passed:
        print("  ✓ Send tab: PASSED")
    else:
        print("  ❌ Send tab: FAILED")
    
    # Check Receive tab
    print("\n[4] Checking Receive tab...")
    receive_tab_file = base_dir / "ui" / "receive_tab.py"
    if not check_file_exists(receive_tab_file):
        print(f"  ❌ Receive tab file does not exist: {receive_tab_file}")
        passed = False
        all_passed = False
    else:
        receive_tab_methods = [
            "refresh_accounts",
            "_update_display",
            "_generate_qr_code",
            "_copy_address",
        ]
        passed, missing = check_file_for_methods(receive_tab_file, receive_tab_methods)
        if passed:
            print(f"  ✓ All Receive tab methods found")
        else:
            print(f"  ❌ Missing Receive tab methods: {missing}")
            all_passed = False
        
        # Check for ReceiveTab class
        with open(receive_tab_file) as f:
            content = f.read()
            if "class ReceiveTab" in content:
                print(f"  ✓ Found class: ReceiveTab")
            else:
                print(f"  ❌ Missing class: ReceiveTab")
                passed = False
                all_passed = False
    
    if passed:
        print("  ✓ Receive tab: PASSED")
    else:
        print("  ❌ Receive tab: FAILED")
    
    # Check UI main_window integration
    print("\n[5] Checking UI main_window integration...")
    ui_file = base_dir / "ui" / "main_window.py"
    
    # Check imports
    ui_imports = ["SendTab", "ReceiveTab"]
    imports_ok, missing_imports = check_imports(ui_file, ui_imports)
    if imports_ok:
        print("  ✓ Send/Receive tabs imported")
    else:
        print(f"  ❌ Missing imports: {missing_imports}")
        passed = False
        all_passed = False
    
    # Check tab instantiation
    with open(ui_file) as f:
        content = f.read()
        if "_send_tab = SendTab" in content:
            print("  ✓ Send tab instantiated")
        else:
            print("  ❌ Send tab not instantiated")
            passed = False
            all_passed = False
        
        if "_receive_tab = ReceiveTab" in content:
            print("  ✓ Receive tab instantiated")
        else:
            print("  ❌ Receive tab not instantiated")
            passed = False
            all_passed = False
        
        if 'tabs.addTab(self._send_tab, "Send")' in content:
            print("  ✓ Send tab added to tabs")
        else:
            print("  ❌ Send tab not added to tabs")
            passed = False
            all_passed = False
        
        if 'tabs.addTab(self._receive_tab, "Receive")' in content:
            print("  ✓ Receive tab added to tabs")
        else:
            print("  ❌ Receive tab not added to tabs")
            passed = False
            all_passed = False
        
        if "refresh_accounts" in content:
            print("  ✓ Tab refresh_accounts called")
        else:
            print("  ❌ Tab refresh_accounts not called")
            passed = False
            all_passed = False
    
    if passed and imports_ok:
        print("  ✓ UI main_window integration: PASSED")
    else:
        print("  ❌ UI main_window integration: FAILED")
        all_passed = False
    
    # Summary
    print("\n" + "=" * 60)
    if all_passed:
        print("✓ ALL CHECKS PASSED")
        print("\nThe send/receive implementation is complete and")
        print("all required components are in place.")
        print("\nNext steps:")
        print("1. Start the wallet app with: ./run.sh")
        print("2. Unlock the wallet")
        print("3. Test the Send tab to send transactions")
        print("4. Test the Receive tab to view addresses and QR codes")
        return 0
    else:
        print("❌ SOME CHECKS FAILED")
        print("\nPlease review the failures above and ensure all")
        print("required methods, classes, and imports are present.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
