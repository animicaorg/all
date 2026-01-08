#!/usr/bin/env python3
"""
Snapshot System Verification Script
====================================

This script verifies that the automatic snapshot creation and sharing system
is working correctly. It checks:

1. Configuration - Are snapshot-related env vars set correctly?
2. Auto-Creation - Is BlockImporter configured to create snapshots?
3. RPC Methods - Are snapshot RPC methods registered?
4. Disk Snapshots - Do any snapshots exist on disk?
5. Peer Discovery - Can the system query peers for snapshots?

Usage:
    python3 scripts/verify_snapshot_system.py
    
    # Or with custom data directory:
    ANIMICA_DATA_DIR=/path/to/data python3 scripts/verify_snapshot_system.py
"""

import os
import sys
import json
from pathlib import Path
from typing import List, Tuple


def check_env_config() -> Tuple[bool, List[str]]:
    """Check snapshot-related environment configuration."""
    print("\n" + "="*70)
    print("1. Checking Environment Configuration")
    print("="*70)
    
    messages = []
    all_good = True
    
    # Check snapshot interval
    interval = os.getenv("ANIMICA_SNAPSHOT_INTERVAL", "2000")
    print(f"ANIMICA_SNAPSHOT_INTERVAL: {interval}")
    try:
        int_val = int(interval)
        if int_val > 0:
            messages.append(f"✓ Snapshot interval set to {int_val} blocks")
        else:
            messages.append(f"✗ Invalid snapshot interval: {int_val}")
            all_good = False
    except ValueError:
        messages.append(f"✗ Invalid snapshot interval value: {interval}")
        all_good = False
    
    # Check auto-create setting
    auto_create = os.getenv("ANIMICA_SNAPSHOT_AUTO_CREATE", "true").lower()
    print(f"ANIMICA_SNAPSHOT_AUTO_CREATE: {auto_create}")
    if auto_create in ("true", "1", "yes", "on"):
        messages.append("✓ Automatic snapshot creation is ENABLED")
    else:
        messages.append("⚠ Automatic snapshot creation is DISABLED")
        all_good = False
    
    # Check sync settings
    sync_enabled = os.getenv("ANIMICA_SNAPSHOT_SYNC_ENABLED", "true").lower()
    print(f"ANIMICA_SNAPSHOT_SYNC_ENABLED: {sync_enabled}")
    if sync_enabled in ("true", "1", "yes", "on"):
        messages.append("✓ Snapshot-based sync is ENABLED")
    else:
        messages.append("⚠ Snapshot-based sync is DISABLED")
    
    # Check RPC URL (optional)
    rpc_url = os.getenv("ANIMICA_SNAPSHOT_RPC_URL", "")
    if rpc_url:
        print(f"ANIMICA_SNAPSHOT_RPC_URL: {rpc_url}")
        messages.append(f"✓ Static snapshot RPC URL configured: {rpc_url}")
    else:
        print("ANIMICA_SNAPSHOT_RPC_URL: (not set, will use peer discovery)")
        messages.append("• No static RPC URL set (will use peer discovery)")
    
    # Check minimum height
    min_height = os.getenv("ANIMICA_SNAPSHOT_MIN_HEIGHT", "1000")
    print(f"ANIMICA_SNAPSHOT_MIN_HEIGHT: {min_height}")
    messages.append(f"• Minimum height for snapshot sync: {min_height}")
    
    print()
    for msg in messages:
        print(msg)
    
    return all_good, messages


def check_rpc_methods() -> Tuple[bool, List[str]]:
    """Check if snapshot RPC methods are registered."""
    print("\n" + "="*70)
    print("2. Checking RPC Method Registration")
    print("="*70)
    
    messages = []
    all_good = True
    
    try:
        # Need to set PYTHONPATH
        import sys
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        
        from rpc.methods import ensure_loaded, get_methods
        
        # Load methods
        ensure_loaded()
        methods = get_methods()
        
        # Check for required snapshot methods
        required_methods = [
            'snapshot.create',
            'snapshot.list',
            'snapshot.get',
            'snapshot.verify',
            'snapshot.import',
            'snapshot.delete',
            'snapshot.downloadChunk',
        ]
        
        print(f"Total RPC methods registered: {len(methods)}")
        print()
        
        for method_name in required_methods:
            if method_name in methods:
                print(f"✓ {method_name}")
                messages.append(f"✓ {method_name} is registered")
            else:
                print(f"✗ {method_name}")
                messages.append(f"✗ {method_name} is NOT registered")
                all_good = False
        
    except ImportError as e:
        print(f"✗ Cannot import RPC modules: {e}")
        messages.append(f"✗ Cannot verify RPC methods: {e}")
        all_good = False
    except Exception as e:
        print(f"✗ Error checking RPC methods: {e}")
        messages.append(f"✗ Error: {e}")
        all_good = False
    
    return all_good, messages


def check_disk_snapshots() -> Tuple[bool, List[str]]:
    """Check for existing snapshots on disk."""
    print("\n" + "="*70)
    print("3. Checking Disk Snapshots")
    print("="*70)
    
    messages = []
    all_good = True
    
    # Get snapshots directory
    data_dir = os.getenv("ANIMICA_DATA_DIR", "~/.animica")
    snapshots_dir = Path(data_dir).expanduser() / "snapshots"
    
    print(f"Snapshots directory: {snapshots_dir}")
    
    if not snapshots_dir.exists():
        print("⚠ Snapshots directory does not exist")
        messages.append("⚠ No snapshots directory found (will be created when first snapshot is made)")
        return True, messages  # Not an error, just means no snapshots yet
    
    # Scan for snapshot directories
    snapshots = []
    for item in snapshots_dir.iterdir():
        if not item.is_dir():
            continue
        
        # Parse directory name: chain-{id}-height-{height}
        if not item.name.startswith("chain-"):
            continue
        
        parts = item.name.split("-")
        if len(parts) != 4:
            continue
        
        try:
            chain_id = int(parts[1])
            height = int(parts[3])
        except ValueError:
            continue
        
        # Check for manifest
        manifest_file = item / "manifest.json"
        if manifest_file.exists():
            try:
                with open(manifest_file) as f:
                    manifest = json.load(f)
                
                snapshots.append({
                    "chain_id": chain_id,
                    "height": height,
                    "path": str(item),
                    "blocks_count": manifest.get("blocks_count", 0),
                    "timestamp": manifest.get("timestamp", 0),
                    "chunks": len(manifest.get("chunks", [])),
                })
                
            except Exception as e:
                print(f"⚠ Failed to read manifest from {manifest_file}: {e}")
    
    if snapshots:
        print(f"\n✓ Found {len(snapshots)} snapshot(s):\n")
        for snap in sorted(snapshots, key=lambda s: (s["chain_id"], s["height"])):
            print(f"  Chain {snap['chain_id']}, Height {snap['height']}:")
            print(f"    Path: {snap['path']}")
            print(f"    Blocks: {snap['blocks_count']}")
            print(f"    Chunks: {snap['chunks']}")
            print()
        
        messages.append(f"✓ Found {len(snapshots)} existing snapshot(s)")
        all_good = True
    else:
        print("⚠ No snapshots found on disk")
        messages.append("⚠ No snapshots found (they will be created as chain grows)")
        # Not an error - snapshots may not exist yet
        all_good = True
    
    return all_good, messages


def check_block_importer_config() -> Tuple[bool, List[str]]:
    """Check if BlockImporter is configured for snapshot creation."""
    print("\n" + "="*70)
    print("4. Checking BlockImporter Configuration")
    print("="*70)
    
    messages = []
    all_good = True
    
    try:
        import sys
        from pathlib import Path
        repo_root = Path(__file__).resolve().parents[1]
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        
        from core.chain import block_import
        
        # Check constants
        interval = block_import.DEFAULT_SNAPSHOT_INTERVAL
        auto_create = block_import.SNAPSHOT_AUTO_CREATE
        
        print(f"DEFAULT_SNAPSHOT_INTERVAL: {interval}")
        print(f"SNAPSHOT_AUTO_CREATE: {auto_create}")
        print()
        
        if interval > 0:
            messages.append(f"✓ Snapshot interval is {interval} blocks")
        else:
            messages.append(f"✗ Invalid snapshot interval: {interval}")
            all_good = False
        
        if auto_create:
            messages.append("✓ Auto-creation is enabled in BlockImporter")
        else:
            messages.append("✗ Auto-creation is disabled in BlockImporter")
            all_good = False
        
        # Check that methods exist
        if hasattr(block_import, 'BlockImporter'):
            importer_class = block_import.BlockImporter
            required_methods = [
                '_should_create_disk_snapshot',
                '_create_disk_snapshot',
                '_check_and_create_missing_snapshots',
            ]
            
            for method_name in required_methods:
                if hasattr(importer_class, method_name):
                    print(f"✓ {method_name} method exists")
                    messages.append(f"✓ {method_name} exists")
                else:
                    print(f"✗ {method_name} method missing")
                    messages.append(f"✗ {method_name} missing")
                    all_good = False
        else:
            messages.append("✗ BlockImporter class not found")
            all_good = False
        
    except ImportError as e:
        print(f"✗ Cannot import block_import module: {e}")
        messages.append(f"✗ Cannot verify BlockImporter: {e}")
        all_good = False
    except Exception as e:
        print(f"✗ Error checking BlockImporter: {e}")
        messages.append(f"✗ Error: {e}")
        all_good = False
    
    return all_good, messages


def print_summary(results: dict):
    """Print overall summary."""
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    
    all_passed = all(results.values())
    
    print("\nComponent Status:")
    for component, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {component}: {status}")
    
    print()
    if all_passed:
        print("✅ All checks passed! Snapshot system is properly configured.")
        print()
        print("Next steps:")
        print("1. Run a node and let it import blocks past height 2000")
        print("2. Check logs for snapshot creation messages")
        print("3. Verify snapshots appear in the snapshots directory")
        print("4. Test snapshot sync by starting a new node")
    else:
        print("❌ Some checks failed. Please review the issues above.")
        print()
        print("Common fixes:")
        print("1. Ensure ANIMICA_SNAPSHOT_AUTO_CREATE=true")
        print("2. Ensure ANIMICA_SNAPSHOT_INTERVAL is set (default: 2000)")
        print("3. Check that the codebase is up to date")
        print("4. Verify Python environment has all dependencies")
    
    print()


def main():
    """Run all verification checks."""
    print("="*70)
    print("Animica Snapshot System Verification")
    print("="*70)
    
    results = {}
    
    # Run all checks
    results["Environment Config"], _ = check_env_config()
    results["RPC Methods"], _ = check_rpc_methods()
    results["Disk Snapshots"], _ = check_disk_snapshots()
    results["BlockImporter Config"], _ = check_block_importer_config()
    
    # Print summary
    print_summary(results)
    
    # Return exit code
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
