#!/usr/bin/env python3
"""
Visual verification script for custom wallet location feature.

This script checks that all components are properly configured to support
custom wallet locations.
"""

import json
import os
from pathlib import Path


def check_file_changes():
    """Verify that all necessary files have been modified."""
    print("="*70)
    print("CUSTOM WALLET LOCATION FEATURE - VERIFICATION")
    print("="*70)
    print()
    
    repo_root = Path(__file__).parent
    
    files_to_check = {
        "config.py": repo_root / "apps/miner-gui/animica_miner_gui/backend/config.py",
        "wallet.py": repo_root / "apps/miner-gui/animica_miner_gui/ui/tabs/wallet.py",
        "wizard.py": repo_root / "apps/miner-gui/animica_miner_gui/ui/wizard.py",
        "miner_runner.py": repo_root / "apps/miner-gui/animica_miner_gui/backend/miner_runner.py",
    }
    
    print("📁 Checking modified files:")
    print()
    
    all_exist = True
    for name, path in files_to_check.items():
        exists = path.exists()
        status = "✓" if exists else "✗"
        print(f"  {status} {name}: {path}")
        if not exists:
            all_exist = False
    
    print()
    
    if not all_exist:
        print("❌ Some files are missing!")
        return False
    
    print("✓ All files exist")
    print()
    
    # Check for specific code patterns
    print("🔍 Checking for key code patterns:")
    print()
    
    checks = [
        ("config.py", "wallet_file: Optional[str]", "MinerConfig has wallet_file field"),
        ("wallet.py", "ANIMICA_WALLETS_FILE", "WalletTab checks environment variable"),
        ("wallet.py", "self.config.miner.wallet_file", "WalletTab uses config wallet_file"),
        ("wizard.py", "wallet_file_path_input", "Wizard stores wallet file path"),
        ("miner_runner.py", "ANIMICA_WALLETS_FILE", "Miner runner sets env var"),
    ]
    
    all_patterns_found = True
    for filename, pattern, description in checks:
        file_path = files_to_check.get(filename)
        if file_path and file_path.exists():
            content = file_path.read_text()
            found = pattern in content
            status = "✓" if found else "✗"
            print(f"  {status} {description}")
            if not found:
                all_patterns_found = False
                print(f"      Missing pattern: '{pattern}' in {filename}")
        else:
            print(f"  ✗ Cannot check {filename} - file not found")
            all_patterns_found = False
    
    print()
    
    if not all_patterns_found:
        print("❌ Some required code patterns are missing!")
        return False
    
    print("✓ All required code patterns found")
    print()
    
    # Check documentation
    print("📚 Checking documentation:")
    print()
    
    docs = {
        "Implementation Guide": repo_root / "apps/miner-gui/CUSTOM_WALLET_LOCATION_FEATURE.md",
        "User Guide": repo_root / "apps/miner-gui/CUSTOM_WALLET_LOCATION_USER_GUIDE.md",
    }
    
    all_docs_exist = True
    for name, path in docs.items():
        exists = path.exists()
        status = "✓" if exists else "✗"
        print(f"  {status} {name}: {path}")
        if not exists:
            all_docs_exist = False
    
    print()
    
    if not all_docs_exist:
        print("⚠️  Some documentation is missing")
        # Don't fail for missing docs, just warn
    
    # Summary
    print("="*70)
    if all_exist and all_patterns_found:
        print("✅ VERIFICATION PASSED")
        print()
        print("All required changes are in place. The custom wallet location")
        print("feature should work correctly.")
        print()
        print("Next steps:")
        print("  1. Test wallet import from custom location")
        print("  2. Test mining with custom wallet location")
        print("  3. Verify transactions work from custom wallet")
        print()
    else:
        print("❌ VERIFICATION FAILED")
        print()
        print("Some required changes are missing. Review the output above.")
        print()
    print("="*70)
    
    return all_exist and all_patterns_found


def show_usage_example():
    """Show example configuration."""
    print()
    print("="*70)
    print("EXAMPLE CONFIGURATION")
    print("="*70)
    print()
    print("To use a custom wallet location, edit your config file:")
    print()
    print("  ~/.animica/gui-miner/config.json")
    print()
    print("And add the wallet_file field:")
    print()
    
    example_config = {
        "version": "1.0",
        "miner": {
            "payout_address": "anim1zqqjt3258rgnfckqxv686unmgtvkl2hn6y7afdgxthummydzr6exw9spuqzdz",
            "wallet_file": "/custom/path/wallets.json",
            "auto_start": False
        },
        "network": {
            "network_type": "mainnet",
            "rpc_url": "https://rpc.mainnet.animica.org/rpc"
        }
    }
    
    print(json.dumps(example_config, indent=2))
    print()
    print("Or use the environment variable:")
    print()
    print("  export ANIMICA_WALLETS_FILE=/custom/path/wallets.json")
    print("  ./animica-miner-gui")
    print()
    print("="*70)
    print()


if __name__ == "__main__":
    success = check_file_changes()
    show_usage_example()
    
    if not success:
        exit(1)
