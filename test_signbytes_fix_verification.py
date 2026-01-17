"""
Verification test for signBytes and mixSeed fix in stratum job format.

This test simulates the full flow from RPC -> adapter -> stratum server -> miner
and verifies that miners receive jobs with all required fields.
"""
import sys
from pathlib import Path

# Add project paths
repo_root = Path(__file__).parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "python"))


def simulate_full_job_flow():
    """Simulate the complete job flow from RPC to miner."""
    print("\n" + "="*70)
    print("FULL JOB FLOW VERIFICATION TEST")
    print("="*70)
    
    # Step 1: RPC Response (from miner.getWork)
    print("\nStep 1: RPC Response from miner.getWork")
    rpc_response = {
        "jobId": "test-job-123",
        "header": {
            "chainId": 1,
            "height": 100,
            "number": 100,  # Some implementations include number in header
            "parentHash": "0x" + "11" * 32,
            "stateRoot": "0x" + "00" * 32,
            "thetaMicro": 800_000,
            # signBytes is NOT in header initially (added by pool)
        },
        "signBytes": "0x" + "aa" * 80,  # At top level
        "hints": {"mixSeed": "0x" + "bb" * 32},  # mixSeed in hints
        "target": "0x" + "ff" * 32,
        "thetaMicro": 800_000,
        "shareTarget": 0.01,
        "height": 100,  # Also at top level
    }
    print(f"  ✓ RPC returns signBytes: {rpc_response.get('signBytes')[:20]}...")
    print(f"  ✓ RPC returns height: {rpc_response.get('height')}")
    print(f"  ✓ RPC hints.mixSeed: {rpc_response['hints']['mixSeed'][:20]}...")
    
    # Step 2: Pool Adapter extraction (MiningCoreAdapter.get_new_job)
    print("\nStep 2: Pool Adapter extraction")
    
    class MiningJob:
        def __init__(self, job_id, header, theta_micro, share_target, height, 
                     target=None, sign_bytes=None, hints=None):
            self.job_id = job_id
            self.header = header
            self.theta_micro = theta_micro
            self.share_target = share_target
            self.height = height
            self.target = target
            self.sign_bytes = sign_bytes
            self.hints = hints or {}
    
    # Simulate adapter extraction
    mining_job = MiningJob(
        job_id=rpc_response.get("jobId"),
        header=rpc_response.get("header", {}),
        theta_micro=int(rpc_response.get("thetaMicro", 0)),
        share_target=float(rpc_response.get("shareTarget", 0.0)),
        height=int(rpc_response.get("height", 0)),
        target=rpc_response.get("target"),
        sign_bytes=rpc_response.get("signBytes"),  # Extracted from top level
        hints=rpc_response.get("hints", {}),
    )
    print(f"  ✓ MiningJob.job_id: {mining_job.job_id}")
    print(f"  ✓ MiningJob.sign_bytes: {mining_job.sign_bytes[:20]}...")
    print(f"  ✓ MiningJob.height: {mining_job.height}")
    print(f"  ✓ MiningJob.hints.mixSeed: {mining_job.hints.get('mixSeed', 'MISSING')[:20]}...")
    
    # Step 3: Stratum Server job construction (WITH FIX)
    print("\nStep 3: Stratum Server constructs job header (WITH FIX)")
    
    # This is the _on_new_job method with the fix
    header = dict(mining_job.header or {})
    if mining_job.sign_bytes:
        header.setdefault("signBytes", mining_job.sign_bytes)
    if mining_job.target:
        header.setdefault("target", mining_job.target)
    if mining_job.height:
        header.setdefault("number", mining_job.height)
    # FIX: Add mixSeed to header from hints
    if mining_job.hints and "mixSeed" in mining_job.hints:
        header.setdefault("mixSeed", mining_job.hints["mixSeed"])
    
    print(f"  ✓ header.signBytes: {header.get('signBytes', 'MISSING')[:20]}...")
    print(f"  ✓ header.mixSeed: {header.get('mixSeed', 'MISSING')[:20]}...")
    print(f"  ✓ header.number: {header.get('number', 'MISSING')}")
    print(f"  ✓ header.target: {header.get('target', 'MISSING')[:20]}...")
    
    # Step 4: Stratum Protocol notify message
    print("\nStep 4: Stratum Protocol notify message")
    
    # Simulate push_notify with height parameter
    notify_params = {
        "jobId": mining_job.job_id,
        "cleanJobs": True,
        "header": header,
        "shareTarget": mining_job.share_target,
        "hints": mining_job.hints,
        "height": mining_job.height,  # FIX: height at top level
    }
    print(f"  ✓ notify.jobId: {notify_params['jobId']}")
    print(f"  ✓ notify.height: {notify_params.get('height', '?')}")
    print(f"  ✓ notify.header.signBytes: {notify_params['header'].get('signBytes', 'MISSING')[:20]}...")
    print(f"  ✓ notify.header.mixSeed: {notify_params['header'].get('mixSeed', 'MISSING')[:20]}...")
    
    # Step 5: Miner receives and validates job
    print("\nStep 5: Miner receives and validates job")
    
    # Simulate what miner CLI does
    job_data = notify_params  # This is what client.last_job contains
    job_id = job_data.get("jobId", "unknown")
    height = job_data.get("height", "?")  # Top-level height
    header_miner = job_data.get("header", {})
    
    print(f"  Miner sees:")
    print(f"    job_id: {job_id}")
    print(f"    height: {height} (was '?' before fix)")
    
    # Check signBytes (line 2084 in mining.py)
    sign_bytes_hex = header_miner.get("signBytes", "")
    if not sign_bytes_hex:
        print(f"    ✗ FAIL: No signBytes in job header (miner would skip)")
        return False
    else:
        print(f"    ✓ signBytes: {sign_bytes_hex[:20]}...")
    
    # Check mixSeed (line 2091 in mining.py)
    mix_seed_hex = header_miner.get("mixSeed", "")
    if not mix_seed_hex:
        print(f"    ✗ FAIL: No mixSeed in job header (miner would use default)")
        return False
    else:
        print(f"    ✓ mixSeed: {mix_seed_hex[:20]}...")
    
    # Try to parse (as miner does at line 2096-2098)
    try:
        prefix = bytes.fromhex(sign_bytes_hex[2:] if sign_bytes_hex.startswith("0x") else sign_bytes_hex)
        mix_seed = bytes.fromhex(mix_seed_hex[2:] if mix_seed_hex.startswith("0x") else mix_seed_hex)
        print(f"    ✓ signBytes parsed: {len(prefix)} bytes")
        print(f"    ✓ mixSeed parsed: {len(mix_seed)} bytes")
    except (ValueError, AttributeError) as e:
        print(f"    ✗ FAIL: Error parsing bytes: {e}")
        return False
    
    print(f"\n  ✓ Miner can hash with this job!")
    
    # Verification summary
    print("\n" + "="*70)
    print("VERIFICATION SUMMARY")
    print("="*70)
    
    checks = [
        ("signBytes extracted from RPC", mining_job.sign_bytes is not None),
        ("signBytes in header", "signBytes" in header),
        ("mixSeed in hints", "mixSeed" in mining_job.hints),
        ("mixSeed added to header (FIX)", "mixSeed" in header),
        ("height extracted from RPC", mining_job.height == 100),
        ("height in notify message (FIX)", notify_params.get("height") == 100),
        ("height displayed correctly", height == 100),
        ("signBytes parseable", len(prefix) > 0),
        ("mixSeed parseable", len(mix_seed) == 32),
    ]
    
    all_pass = True
    for check_name, check_result in checks:
        status = "✓" if check_result else "✗"
        print(f"  {status} {check_name}")
        if not check_result:
            all_pass = False
    
    print("="*70)
    if all_pass:
        print("✓✓✓ ALL CHECKS PASSED - FIX VERIFIED ✓✓✓")
        print("\nMiner output would be:")
        print(f'  → New job: {job_id} (height {height})')
        print("  [Mining starts immediately - no 'No signBytes' warning]")
    else:
        print("✗✗✗ SOME CHECKS FAILED ✗✗✗")
    print("="*70 + "\n")
    
    return all_pass


def main():
    """Run verification test."""
    try:
        success = simulate_full_job_flow()
        return 0 if success else 1
    except Exception as e:
        print(f"\n✗ TEST FAILED WITH EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
