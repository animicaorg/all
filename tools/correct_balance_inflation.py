#!/usr/bin/env python3
"""
Automatic Balance Inflation Correction Tool

This script automatically detects and corrects inflated balances caused by state rebuild bugs.
It scans all addresses with balances, detects inflation factors, and applies corrections.

Usage:
    python correct_balance_inflation.py --rpc http://localhost:8545 --db-path ~/.animica/chain-1337/state.db
    
    # Dry run (detection only, no changes):
    python correct_balance_inflation.py --rpc http://localhost:8545 --db-path ~/.animica/chain-1337/state.db --dry-run
    
    # Apply corrections automatically:
    python correct_balance_inflation.py --rpc http://localhost:8545 --db-path ~/.animica/chain-1337/state.db --apply
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Block reward constant (5 ANM = 5_000_000_000 nANM)
BLOCK_REWARD = 5_000_000_000


def detect_inflation_factor(balance: int) -> Tuple[Optional[int], str, int]:
    """
    Detect if balance appears to be inflated by checking for common multipliers.
    
    The inflation bug causes balances to be exact multiples (2x, 3x, 4x, etc.) of the
    correct value. This function detects such patterns by checking if dividing the
    balance by common factors (2-10) yields a "more reasonable" result.
    
    We use the heuristic that the FIRST (smallest) factor that divides evenly is
    likely the correct inflation factor, since state rebuilds typically happen
    consistently (e.g., all blocks get re-applied, not just some).
    
    Args:
        balance: Current balance in nANM
        
    Returns:
        Tuple of (factor, explanation, corrected_balance)
        factor is the suspected multiplication count, or None if no inflation detected
    """
    if balance == 0:
        return None, "Balance is zero (no inflation detected)", 0
    
    # All balances from mining are multiples of BLOCK_REWARD
    # If it's not, something else is going on (transfers, contracts, etc.)
    if balance % BLOCK_REWARD != 0:
        return None, "Balance is not a clean multiple of block reward (manual inspection needed)", balance
    
    # For inflation detection, we check if the balance is an "obvious" multiple
    # The key insight: if balance = N * BLOCK_REWARD, and N is divisible by
    # a small integer k (2-10), then k might be the inflation factor.
    # 
    # However, naturally mined balances can also be divisible (e.g., 10 blocks).
    # The difference is that inflated balances from the bug described in
    # BALANCE_INFLATION_FIX_COMPLETE.md show VERY LARGE multipliers that are
    # exact factors of the expected amount.
    #
    # Example from docs: 464,100 ANM = 92,820 ANM * 5
    #   92,820 ANM = 18,564 blocks (reasonable mining activity)
    #   464,100 ANM = 92,820 blocks (unreasonably high)
    #
    # So we check: is the current balance "unreasonably large" and divisible
    # by a small factor into something more reasonable?
    
    blocks = balance // BLOCK_REWARD
    
    # Threshold: balances over 10,000 blocks (~50,000 ANM) are suspicious
    # unless the chain is very mature. For early chains, this is a red flag.
    if blocks < 10_000:
        # Small balance, unlikely to be inflated
        return None, f"Balance appears normal | Estimated blocks mined: {blocks}", balance
    
    # Check factors 2-10
    for factor in range(2, 11):
        if blocks % factor == 0:
            corrected_blocks = blocks // factor
            corrected_balance = corrected_blocks * BLOCK_REWARD
            explanation = f"Balance appears to be {factor}x inflated ({factor-1} state rebuilds) | Estimated blocks mined: {corrected_blocks}"
            return factor, explanation, corrected_balance
    
    # No inflation pattern detected
    return None, f"Balance appears normal | Estimated blocks mined: {blocks}", balance


def scan_and_detect_inflation(state_db) -> List[Dict]:
    """
    Scan all accounts in state DB and detect inflation.
    
    Args:
        state_db: StateDB instance
        
    Returns:
        List of dicts with address, current balance, inflation factor, and corrected balance
    """
    results = []
    
    logger.info("Scanning state DB for accounts with balances...")
    
    for addr_bytes, account in state_db.iter_accounts():
        balance = account.balance
        if balance <= 0:
            continue
            
        # Detect inflation
        factor, explanation, corrected_balance = detect_inflation_factor(balance)
        
        # Only include accounts with detected inflation
        if factor is not None:
            addr_hex = "0x" + addr_bytes.hex()
            results.append({
                "address": addr_hex,
                "address_bytes": addr_bytes,
                "current_balance": balance,
                "inflation_factor": factor,
                "corrected_balance": corrected_balance,
                "explanation": explanation,
            })
            logger.info(f"Inflation detected: {addr_hex[:16]}... - {balance/1e9:.9f} ANM -> {corrected_balance/1e9:.9f} ANM ({factor}x)")
    
    return results


def apply_corrections(state_db, corrections: List[Dict], audit_path: Path) -> Dict:
    """
    Apply balance corrections to state DB.
    
    Args:
        state_db: StateDB instance
        corrections: List of correction dicts from scan_and_detect_inflation
        audit_path: Path to write audit trail
        
    Returns:
        Dict with summary statistics
    """
    if not corrections:
        logger.info("No corrections to apply")
        return {"applied": 0, "total": 0, "errors": 0}
    
    logger.info(f"Applying corrections to {len(corrections)} accounts...")
    
    applied = 0
    errors = 0
    audit_records = []
    
    # Use batch for efficient writes
    with state_db.batch() as batch:
        for correction in corrections:
            addr_bytes = correction["address_bytes"]
            corrected_balance = correction["corrected_balance"]
            
            try:
                # Set corrected balance
                state_db.set_balance(addr_bytes, corrected_balance, batch=batch)
                
                # Record audit trail
                audit_records.append({
                    "address": correction["address"],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "old_balance": correction["current_balance"],
                    "new_balance": corrected_balance,
                    "inflation_factor": correction["inflation_factor"],
                    "explanation": correction["explanation"],
                })
                
                applied += 1
                logger.info(
                    f"Corrected {correction['address'][:16]}...: "
                    f"{correction['current_balance']/1e9:.9f} ANM -> {corrected_balance/1e9:.9f} ANM"
                )
            except Exception as e:
                logger.error(f"Failed to correct {correction['address']}: {e}")
                errors += 1
    
    # Write audit trail
    if audit_records:
        audit_data = {
            "corrected_at": datetime.now(timezone.utc).isoformat(),
            "total_corrections": len(audit_records),
            "corrections": audit_records,
        }
        audit_path.write_text(json.dumps(audit_data, indent=2), encoding="utf-8")
        logger.info(f"Audit trail written to: {audit_path}")
    
    return {
        "applied": applied,
        "total": len(corrections),
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Automatic balance inflation correction tool"
    )
    parser.add_argument(
        "--db-path",
        required=True,
        type=Path,
        help="Path to state database (e.g., ~/.animica/chain-1337/state.db)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Detect inflation without applying corrections",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply corrections automatically (requires explicit confirmation)",
    )
    parser.add_argument(
        "--audit-path",
        type=Path,
        help="Path for audit trail file (default: <db-path>/../balance_corrections_<timestamp>.json)",
    )
    
    args = parser.parse_args()
    
    # Validate arguments
    if args.apply and args.dry_run:
        logger.error("Cannot specify both --apply and --dry-run")
        return 1
    
    if not args.db_path.exists():
        logger.error(f"State database not found: {args.db_path}")
        return 1
    
    # Determine audit path
    if args.audit_path is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        args.audit_path = args.db_path.parent / f"balance_corrections_{timestamp}.json"
    
    logger.info("=" * 80)
    logger.info("Automatic Balance Inflation Correction Tool")
    logger.info("=" * 80)
    logger.info(f"Database path: {args.db_path}")
    logger.info(f"Mode: {'DRY RUN (detection only)' if args.dry_run else 'APPLY CORRECTIONS' if args.apply else 'DETECTION ONLY'}")
    logger.info(f"Audit path: {args.audit_path}")
    logger.info("")
    
    # Import state DB module
    try:
        from core.db.kv import open_kv
        from core.db.state_db import StateDB
    except ImportError as e:
        logger.error(f"Failed to import required modules: {e}")
        logger.error("Ensure this script is run from the repository root with proper PYTHONPATH")
        return 1
    
    # Open state DB
    try:
        kv = open_kv(str(args.db_path))
        state_db = StateDB(kv)
    except Exception as e:
        logger.error(f"Failed to open state database: {e}")
        return 1
    
    try:
        # Scan and detect inflation
        corrections = scan_and_detect_inflation(state_db)
        
        if not corrections:
            logger.info("")
            logger.info("=" * 80)
            logger.info("✓ No balance inflation detected")
            logger.info("=" * 80)
            logger.info("All account balances appear to be correct.")
            return 0
        
        # Display summary
        logger.info("")
        logger.info("=" * 80)
        logger.info("⚠️  INFLATION DETECTED!")
        logger.info("=" * 80)
        logger.info(f"Accounts with inflation: {len(corrections)}")
        
        total_inflated = sum(c["current_balance"] for c in corrections)
        total_corrected = sum(c["corrected_balance"] for c in corrections)
        total_excess = total_inflated - total_corrected
        
        logger.info(f"Total inflated balance: {total_inflated/1e9:.9f} ANM")
        logger.info(f"Total corrected balance: {total_corrected/1e9:.9f} ANM")
        logger.info(f"Total excess to remove: {total_excess/1e9:.9f} ANM")
        logger.info("")
        
        # Show sample of affected accounts
        logger.info("Sample of affected accounts:")
        for i, correction in enumerate(corrections[:5]):
            logger.info(
                f"  {i+1}. {correction['address'][:16]}... "
                f"{correction['current_balance']/1e9:.9f} ANM -> "
                f"{correction['corrected_balance']/1e9:.9f} ANM "
                f"({correction['inflation_factor']}x)"
            )
        if len(corrections) > 5:
            logger.info(f"  ... and {len(corrections) - 5} more")
        logger.info("")
        
        # Apply corrections if requested
        if args.apply:
            logger.info("=" * 80)
            logger.info("⚠️  WARNING: About to modify account balances")
            logger.info("=" * 80)
            logger.info(f"This will correct {len(corrections)} accounts")
            logger.info(f"Audit trail will be saved to: {args.audit_path}")
            logger.info("")
            
            response = input("Type 'CONFIRM' to proceed with corrections: ")
            if response.strip() != "CONFIRM":
                logger.info("Correction cancelled by user")
                return 1
            
            logger.info("")
            summary = apply_corrections(state_db, corrections, args.audit_path)
            
            logger.info("")
            logger.info("=" * 80)
            logger.info("✓ Corrections applied successfully")
            logger.info("=" * 80)
            logger.info(f"Corrected: {summary['applied']}/{summary['total']} accounts")
            if summary['errors'] > 0:
                logger.warning(f"Errors: {summary['errors']}")
            logger.info(f"Audit trail: {args.audit_path}")
            logger.info("")
            logger.info("NEXT STEPS:")
            logger.info("1. Restart your node to ensure changes take effect")
            logger.info("2. Verify balances are now correct")
            logger.info("3. Review the audit trail for details")
            
            return 0
        elif args.dry_run:
            logger.info("=" * 80)
            logger.info("DRY RUN COMPLETE")
            logger.info("=" * 80)
            logger.info("To apply corrections, run with --apply flag:")
            logger.info(f"  python {sys.argv[0]} --db-path {args.db_path} --apply")
            return 0
        else:
            logger.info("=" * 80)
            logger.info("DETECTION COMPLETE")
            logger.info("=" * 80)
            logger.info("To apply corrections, run with --apply flag:")
            logger.info(f"  python {sys.argv[0]} --db-path {args.db_path} --apply")
            return 0
    
    finally:
        # Clean up
        try:
            state_db.close()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
