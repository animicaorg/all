#!/usr/bin/env python3
"""
Test script for wallet chain integration.

This script validates the chain integration implementation by checking:
1. walletd server has the new proxy methods
2. walletd_manager has the new client methods
3. UI components are properly connected
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


def main():
    """Run validation checks."""
    base_dir = Path(__file__).parent / "src" / "animica_qt_wallet"
    
    print("=" * 60)
    print("Wallet Chain Integration Validation")
    print("=" * 60)
    
    all_passed = True
    
    # Check walletd server
    print("\n[1] Checking walletd server...")
    server_file = base_dir / "walletd" / "server.py"
    server_methods = [
        "_proxy_to_node",
        "dispatch",
    ]
    passed, missing = check_file_for_methods(server_file, server_methods)
    if passed:
        # Check for chain method handlers in dispatch
        with open(server_file) as f:
            content = f.read()
            chain_methods = [
                'method == "chain.getHead"',
                'method == "state.getBalance"',
                'method == "net.peers"',
                'method == "net.peerCount"',
            ]
            for cm in chain_methods:
                if cm not in content:
                    print(f"  ❌ Missing handler: {cm}")
                    passed = False
                else:
                    print(f"  ✓ Found handler: {cm}")
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
        "chain_get_head",
        "state_get_balance",
        "net_peers",
        "net_peer_count",
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
    
    # Check UI main_window
    print("\n[3] Checking UI main_window...")
    ui_file = base_dir / "ui" / "main_window.py"
    ui_methods = [
        "_build_overview_tab",
        "_build_node_tab",
        "_refresh_chain_info",
        "_update_chain_info",
        "_refresh_selected_balance",
        "_handle_account_selection",
    ]
    passed, missing = check_file_for_methods(ui_file, ui_methods)
    if passed:
        print(f"  ✓ All UI methods found: {', '.join(ui_methods)}")
    else:
        print(f"  ❌ Missing UI methods: {missing}")
    
    # Check for QTabWidget import
    ui_imports = ["QTabWidget"]
    imports_ok, missing_imports = check_imports(ui_file, ui_imports)
    if imports_ok:
        print("  ✓ QTabWidget imported")
    else:
        print(f"  ❌ Missing imports: {missing_imports}")
        passed = False
    
    # Check for timer
    with open(ui_file) as f:
        content = f.read()
        if "_chain_info_timer" in content:
            print("  ✓ Chain info timer found")
        else:
            print("  ❌ Chain info timer missing")
            passed = False
    
    if passed and imports_ok:
        print("  ✓ UI main_window: PASSED")
    else:
        print("  ❌ UI main_window: FAILED")
        all_passed = False
    
    # Summary
    print("\n" + "=" * 60)
    if all_passed:
        print("✓ ALL CHECKS PASSED")
        print("\nThe chain integration implementation is complete and")
        print("all required components are in place.")
        return 0
    else:
        print("❌ SOME CHECKS FAILED")
        print("\nPlease review the failures above and ensure all")
        print("required methods and imports are present.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
