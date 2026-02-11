"""
python.animica.cli.aicf — AICF CLI Commands
=============================================

Commands for interacting with the AICF (AI Compute Fund) layer:
- status: Show current AICF pool state and parameters
- miner: Run AICF miner to submit proofs and earn rewards
- params: Show AICF parameters
- stats: Show usage statistics
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Optional

import typer

app = typer.Typer(
    name="aicf",
    help="AICF (AI Compute Fund) operations",
    no_args_is_help=True,
)


def _get_rpc_url() -> str:
    """Get RPC URL from environment or default."""
    return os.getenv("ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc")


def _rpc_call(method: str, params: dict | list | None = None) -> dict:
    """Make JSON-RPC call to node."""
    import requests
    
    url = _get_rpc_url()
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params or [],
        "id": 1,
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        
        if "error" in data:
            raise RuntimeError(f"RPC error: {data['error']}")
        
        return data.get("result", {})
    except Exception as e:
        raise RuntimeError(f"RPC call failed: {e}")


@app.command(name="status")
def status(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """
    Show current AICF pool state.
    
    Displays:
    - Pool balance and capacity
    - Issued and spent totals
    - Percentage filled
    - Number of miners
    """
    try:
        result = _rpc_call("aicf.getPoolState", {})
        
        if json_output:
            print(json.dumps(result, indent=2))
            return
        
        if not result.get("enabled", False):
            print("❌ AICF pool is not enabled on this network")
            return
        
        balance_anm = result.get("balance_anm", 0)
        cap_anm = result.get("cap_anm", 0)
        issued_anm = result.get("issued_anm", 0)
        spent_anm = result.get("spent_anm", 0)
        percent = result.get("percent_filled", 0)
        
        print("╔═══════════════════════════════════════════════════════╗")
        print("║         AICF Pool Status                              ║")
        print("╠═══════════════════════════════════════════════════════╣")
        print(f"║ Balance:      {balance_anm:>15,.2f} ANM              ║")
        print(f"║ Capacity:     {cap_anm:>15,.2f} ANM              ║")
        print(f"║ Filled:       {percent:>15.2f}%                   ║")
        print("╠═══════════════════════════════════════════════════════╣")
        print(f"║ Total Issued: {issued_anm:>15,.2f} ANM              ║")
        print(f"║ Total Spent:  {spent_anm:>15,.2f} ANM              ║")
        print(f"║ Miners:       {result.get('miner_credits_count', 0):>15,} active           ║")
        print("╚═══════════════════════════════════════════════════════╝")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise typer.Exit(1)


@app.command(name="params")
def params(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """
    Show AICF parameters.
    
    Displays all AICF configuration parameters including:
    - Reward per proof
    - Rate limits
    - Difficulty settings
    """
    try:
        result = _rpc_call("aicf.getParams", {})
        
        if json_output:
            print(json.dumps(result, indent=2))
            return
        
        if not result.get("enabled", False):
            print("❌ AICF is not enabled on this network")
            return
        
        print("\n📋 AICF Parameters:")
        print(f"  Capacity:                  {result.get('cap_anm', 0):,.0f} ANM")
        print(f"  Reward per proof:          {result.get('reward_per_proof_anm', 0):,.2f} ANM")
        print(f"  Max proofs per block:      {result.get('max_proofs_per_block', 0)}")
        print(f"  Max proofs per epoch:      {result.get('max_proofs_per_miner_per_epoch', 0)}")
        print(f"  Epoch length:              {result.get('epoch_blocks', 0)} blocks")
        print(f"  Min work difficulty:       {result.get('min_work_difficulty', 0)}")
        print(f"  Verification timeout:      {result.get('verification_timeout_ms', 0)} ms")
        print(f"  Fee routing:               {result.get('fee_routing_pct', 0)}%")
        print()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise typer.Exit(1)


@app.command(name="stats")
def stats(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """
    Show AICF usage statistics.
    
    Displays:
    - Total proofs submitted
    - Total miners
    - Top miners by credits
    """
    try:
        result = _rpc_call("aicf.getUsageStats", {})
        
        if json_output:
            print(json.dumps(result, indent=2))
            return
        
        print(f"\n📊 AICF Usage Statistics:")
        print(f"  Total proofs:    {result.get('total_proofs', 0):,}")
        print(f"  Total miners:    {result.get('total_miners', 0):,}")
        print(f"  Epochs tracked:  {result.get('epochs_tracked', 0):,}")
        
        top_miners = result.get("top_miners", [])
        if top_miners:
            print(f"\n🏆 Top {len(top_miners)} Miners:")
            for i, miner in enumerate(top_miners, 1):
                addr = miner.get("address", "unknown")[:20]
                credits_anm = miner.get("credits_anm", 0)
                print(f"  {i:2d}. {addr}... {credits_anm:>12,.2f} ANM")
        print()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        raise typer.Exit(1)


@app.command(name="miner")
def miner(
    address: str = typer.Option(..., "--address", "-a", help="Miner address (bech32)"),
    work_difficulty: int = typer.Option(20, "--difficulty", "-d", help="Work difficulty (higher = more work)"),
    interval: int = typer.Option(60, "--interval", "-i", help="Interval between proofs (seconds)"),
    count: Optional[int] = typer.Option(None, "--count", "-n", help="Number of proofs to submit (default: infinite)"),
):
    """
    Run AICF miner to submit proofs and earn rewards.
    
    This is a placeholder implementation that submits deterministic work proofs.
    In production, this would perform real AI compute verification.
    
    Example:
        animica aicf miner --address anim1... --difficulty 20 --interval 60
    """
    print(f"🏗️  Starting AICF miner...")
    print(f"   Address:    {address}")
    print(f"   Difficulty: {work_difficulty}")
    print(f"   Interval:   {interval}s")
    if count:
        print(f"   Count:      {count}")
    print()
    
    submitted = 0
    nonce = 0
    
    try:
        while True:
            # Check if we've reached the count limit
            if count is not None and submitted >= count:
                print(f"✅ Submitted {submitted} proofs. Stopping.")
                break
            
            # Generate deterministic work proof
            # In production, this would be real AI compute + verification
            timestamp = int(time.time())
            proof_input = f"{address}:{nonce}:{timestamp}".encode()
            
            # Simulate work by computing hashes until difficulty met
            work_units = 0
            proof_hash = hashlib.sha256(proof_input).digest()
            
            # Simple proof-of-work: hash until leading zeros meet difficulty
            while work_units < work_difficulty:
                proof_input = hashlib.sha256(proof_input + proof_hash).digest()
                proof_hash = hashlib.sha256(proof_input).digest()
                work_units += 1
                
                # Check first byte for simplicity
                if proof_hash[0] == 0:
                    work_units += 5  # Bonus for finding zeros
            
            # Submit proof
            print(f"⛏️  Submitting proof #{submitted + 1} (nonce={nonce}, work={work_units})...")
            
            try:
                result = _rpc_call("aicf.submitProof", {
                    "miner_addr": address,
                    "work_units": work_units,
                    "proof_data": proof_hash.hex(),
                    "timestamp": timestamp,
                    "nonce": nonce,
                })
                
                if result.get("valid"):
                    reward_anm = result.get("reward_anm", 0)
                    print(f"   ✅ Proof accepted! Reward: {reward_anm:.2f} ANM")
                    submitted += 1
                else:
                    reason = result.get("reason", "unknown")
                    print(f"   ❌ Proof rejected: {reason}")
                
            except Exception as e:
                print(f"   ❌ Submission error: {e}")
            
            nonce += 1
            
            # Sleep before next proof (unless this was the last one)
            if count is None or submitted < count:
                time.sleep(interval)
    
    except KeyboardInterrupt:
        print(f"\n⚠️  Interrupted. Submitted {submitted} proofs.")
        raise typer.Exit(0)


def main():
    """Entry point for AICF CLI."""
    app()


if __name__ == "__main__":
    main()
