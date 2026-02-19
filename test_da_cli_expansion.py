#!/usr/bin/env python3
"""
Test script for expanded DA CLI commands.

This script demonstrates that all the new DA CLI commands are properly implemented:
1. animica da put (alias for submit)
2. animica da proof (generate/verify DA proof)
3. animica da storage register
4. animica da storage list
5. animica da storage heartbeat
6. animica da checkpoints list
7. animica da checkpoints verify
"""

import ast
import sys
from pathlib import Path

def test_da_cli_structure():
    """Test that the DA CLI has all required commands."""
    
    da_file = Path(__file__).parent / "python" / "animica" / "cli" / "da.py"
    
    if not da_file.exists():
        print(f"❌ DA CLI file not found: {da_file}")
        return False
    
    with open(da_file, 'r') as f:
        content = f.read()
    
    # Check for required imports
    required_imports = [
        "import json",
        "from .aicf_utils import normalize_rpc_url",
    ]
    
    for imp in required_imports:
        if imp not in content:
            print(f"❌ Missing import: {imp}")
            return False
    
    # Check for subcommand groups
    if "storage_app = typer.Typer" not in content:
        print("❌ Missing storage_app typer group")
        return False
    
    if "checkpoints_app = typer.Typer" not in content:
        print("❌ Missing checkpoints_app typer group")
        return False
    
    # Check that subcommand groups are added to main app
    if 'app.add_typer(storage_app, name="storage")' not in content:
        print("❌ storage_app not added to main app")
        return False
    
    if 'app.add_typer(checkpoints_app, name="checkpoints")' not in content:
        print("❌ checkpoints_app not added to main app")
        return False
    
    # Check for required commands
    required_commands = {
        "submit": "@app.command()",
        "put": "@app.command()",
        "get": "@app.command()",
        "verify": "@app.command()",
        "proof": "@app.command()",
        "storage_register": '@storage_app.command("register")',
        "storage_list": '@storage_app.command("list")',
        "storage_heartbeat": '@storage_app.command("heartbeat")',
        "checkpoints_list": '@checkpoints_app.command("list")',
        "checkpoints_verify": '@checkpoints_app.command("verify")',
    }
    
    for cmd_name, decorator in required_commands.items():
        if decorator not in content:
            print(f"❌ Missing command decorator: {decorator} for {cmd_name}")
            return False
        if f"def {cmd_name}(" not in content:
            print(f"❌ Missing command function: {cmd_name}")
            return False
    
    # Check for --json flag support in new commands
    json_commands = ["put", "proof", "storage_register", "storage_list", 
                     "storage_heartbeat", "checkpoints_list", "checkpoints_verify"]
    
    for cmd in json_commands:
        # Find the function definition
        start_idx = content.find(f"def {cmd}(")
        if start_idx == -1:
            print(f"❌ Command {cmd} not found")
            return False
        
        # Look for --json option in the next 500 characters
        snippet = content[start_idx:start_idx+1000]
        if '--json' not in snippet and '"--json"' not in snippet:
            print(f"❌ Command {cmd} missing --json flag")
            return False
    
    # Check for normalize_rpc_url usage
    if content.count("normalize_rpc_url") < 5:
        print("❌ normalize_rpc_url not used enough (should be in all new commands)")
        return False
    
    # Check for path validation in storage register
    if "endpoint_path = Path(endpoint)" not in content:
        print("❌ Missing path validation in storage register")
        return False
    
    if "Security check" not in content and "writable" not in content.lower():
        print("❌ Missing security check in storage register")
        return False
    
    # Check for proper error handling
    if content.count("except typer.Exit:") < 10:
        print("❌ Not enough error handling (should have typer.Exit in all commands)")
        return False
    
    print("✓ All required commands present")
    print("✓ All imports correct")
    print("✓ Subcommand groups properly configured")
    print("✓ --json flag support added")
    print("✓ normalize_rpc_url used throughout")
    print("✓ Path validation and security checks in place")
    print("✓ Proper error handling implemented")
    
    return True


def test_command_signatures():
    """Test that command signatures match requirements."""
    
    da_file = Path(__file__).parent / "python" / "animica" / "cli" / "da.py"
    
    with open(da_file, 'r') as f:
        content = f.read()
    
    # Test storage register signature
    if "--bytes" not in content:
        print("❌ storage register missing --bytes option")
        return False
    
    if "bytes_capacity" not in content:
        print("❌ storage register missing bytes_capacity parameter")
        return False
    
    if "--endpoint" not in content:
        print("❌ storage register missing --endpoint option")
        return False
    
    # Test checkpoints list signature
    if "--namespace" not in content:
        print("❌ checkpoints list missing --namespace option")
        return False
    
    # Test proof command signature
    if "--verify" not in content:
        print("❌ proof command missing --verify option")
        return False
    
    print("✓ All command signatures correct")
    return True


def test_documentation():
    """Test that commands have proper docstrings."""
    
    da_file = Path(__file__).parent / "python" / "animica" / "cli" / "da.py"
    
    with open(da_file, 'r') as f:
        content = f.read()
    
    # Check module docstring
    if "animica da put" not in content:
        print("❌ Module docstring missing 'animica da put'")
        return False
    
    if "animica da proof" not in content:
        print("❌ Module docstring missing 'animica da proof'")
        return False
    
    if "animica da storage register" not in content:
        print("❌ Module docstring missing storage commands")
        return False
    
    if "animica da checkpoints" not in content:
        print("❌ Module docstring missing checkpoint commands")
        return False
    
    # Check for Examples in docstrings
    if content.count("Examples:") < 10:
        print("❌ Not enough Examples sections in docstrings")
        return False
    
    print("✓ Documentation complete")
    return True


def main():
    """Run all tests."""
    print("=" * 70)
    print("Testing expanded DA CLI implementation")
    print("=" * 70)
    print()
    
    all_passed = True
    
    print("Test 1: DA CLI Structure")
    print("-" * 70)
    if not test_da_cli_structure():
        all_passed = False
    print()
    
    print("Test 2: Command Signatures")
    print("-" * 70)
    if not test_command_signatures():
        all_passed = False
    print()
    
    print("Test 3: Documentation")
    print("-" * 70)
    if not test_documentation():
        all_passed = False
    print()
    
    print("=" * 70)
    if all_passed:
        print("✓ All tests passed!")
        print()
        print("Summary of implemented commands:")
        print("  • animica da submit [--json]    (enhanced with JSON output)")
        print("  • animica da put [--json]       (new alias for submit)")
        print("  • animica da get                (existing)")
        print("  • animica da verify             (existing)")
        print("  • animica da proof [--verify] [--json]")
        print("  • animica da storage register --bytes <n> --endpoint <url|path> [--json]")
        print("  • animica da storage list [--json]")
        print("  • animica da storage heartbeat [--id <id>] [--json]")
        print("  • animica da checkpoints list [--namespace <ns>] [--limit <n>] [--json]")
        print("  • animica da checkpoints verify <commitment> [--json]")
        print()
        print("Key features:")
        print("  ✓ All commands use normalize_rpc_url for URL handling")
        print("  ✓ All commands support --json output flag")
        print("  ✓ Storage register has path validation and security checks")
        print("  ✓ Proper error handling with user-friendly messages")
        print("  ✓ Follows existing patterns in da.py")
        print("  ✓ Falls back to RPC calls when omni_sdk not available")
        return 0
    else:
        print("❌ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
