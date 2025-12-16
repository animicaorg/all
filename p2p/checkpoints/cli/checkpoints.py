#!/usr/bin/env python3
"""
checkpoints.py
==============

CLI tool for managing and inspecting built-in checkpoints.

Commands:
---------
- list: Display built-in checkpoints for all or specific chain
- export: Export built-in checkpoints to a JSON file

Examples:
---------
# List all built-in checkpoints
python -m p2p.checkpoints.cli.checkpoints list

# List checkpoints for mainnet (chain_id=1)
python -m p2p.checkpoints.cli.checkpoints list --chain-id 1

# Export mainnet checkpoints to file
python -m p2p.checkpoints.cli.checkpoints export --chain-id 1 --output checkpoints.json

# Export all checkpoints to file
python -m p2p.checkpoints.cli.checkpoints export --output all_checkpoints.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

from p2p.checkpoints import builtin
from p2p.checkpoints.loader import Checkpoint


def get_chain_name(chain_id: int) -> str:
    """Get human-readable chain name."""
    names = {
        1: "mainnet",
        2: "testnet",
        1337: "devnet",
    }
    return names.get(chain_id, f"chain-{chain_id}")


def format_checkpoint(cp: Checkpoint) -> str:
    """Format a checkpoint for display."""
    return f"  Height {cp.height:8d}: {cp.hash}"


def cmd_list(args: argparse.Namespace) -> int:
    """List built-in checkpoints."""
    if args.chain_id is not None:
        # List for specific chain
        chain_id = args.chain_id
        checkpoints = builtin.get_builtin_checkpoints(chain_id)
        
        if not checkpoints:
            print(f"No built-in checkpoints for {get_chain_name(chain_id)} (chain_id={chain_id})")
            return 0
        
        print(f"Built-in checkpoints for {get_chain_name(chain_id)} (chain_id={chain_id}):")
        for cp in checkpoints:
            print(format_checkpoint(cp))
        
        print(f"\nTotal: {len(checkpoints)} checkpoint(s)")
    else:
        # List for all chains
        all_checkpoints = builtin.get_all_builtin_checkpoints()
        
        if not all_checkpoints:
            print("No built-in checkpoints defined for any chain")
            return 0
        
        total_count = 0
        for chain_id, checkpoints in sorted(all_checkpoints.items()):
            print(f"{get_chain_name(chain_id)} (chain_id={chain_id}):")
            for cp in checkpoints:
                print(format_checkpoint(cp))
            print()
            total_count += len(checkpoints)
        
        print(f"Total: {total_count} checkpoint(s) across {len(all_checkpoints)} chain(s)")
    
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Export built-in checkpoints to JSON file."""
    output_path = Path(args.output).expanduser()
    
    if args.chain_id is not None:
        # Export for specific chain
        chain_id = args.chain_id
        checkpoints = builtin.get_builtin_checkpoints(chain_id)
        
        if not checkpoints:
            print(f"No built-in checkpoints for {get_chain_name(chain_id)} (chain_id={chain_id})", file=sys.stderr)
            return 1
        
        data = {
            "network": get_chain_name(chain_id),
            "chain_id": chain_id,
            "checkpoints": [
                {"height": cp.height, "hash": cp.hash}
                for cp in checkpoints
            ],
            "description": f"Built-in checkpoints for {get_chain_name(chain_id)}",
        }
    else:
        # Export all chains
        all_checkpoints = builtin.get_all_builtin_checkpoints()
        
        if not all_checkpoints:
            print("No built-in checkpoints defined for any chain", file=sys.stderr)
            return 1
        
        data = {
            "networks": {
                get_chain_name(chain_id): {
                    "chain_id": chain_id,
                    "checkpoints": [
                        {"height": cp.height, "hash": cp.hash}
                        for cp in checkpoints
                    ]
                }
                for chain_id, checkpoints in all_checkpoints.items()
            },
            "description": "Built-in checkpoints for all networks",
        }
    
    # Write to file
    try:
        with output_path.open("w") as f:
            json.dump(data, f, indent=2)
        
        print(f"Exported checkpoints to {output_path}")
        return 0
    
    except Exception as e:
        print(f"Error writing to {output_path}: {e}", file=sys.stderr)
        return 1


def main() -> int:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Manage and inspect built-in checkpoints",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Command to run")
    
    # list command
    list_parser = subparsers.add_parser(
        "list",
        help="List built-in checkpoints",
    )
    list_parser.add_argument(
        "--chain-id",
        type=int,
        help="Chain ID to list checkpoints for (default: all chains)",
    )
    
    # export command
    export_parser = subparsers.add_parser(
        "export",
        help="Export built-in checkpoints to JSON file",
    )
    export_parser.add_argument(
        "--chain-id",
        type=int,
        help="Chain ID to export checkpoints for (default: all chains)",
    )
    export_parser.add_argument(
        "-o", "--output",
        required=True,
        help="Output JSON file path",
    )
    
    args = parser.parse_args()
    
    if args.command == "list":
        return cmd_list(args)
    elif args.command == "export":
        return cmd_export(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
