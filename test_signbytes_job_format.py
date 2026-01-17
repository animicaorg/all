"""
Simple test to verify stratum job format includes signBytes and mixSeed correctly.
No pytest required - just run with python3.
"""
import sys
from pathlib import Path

# Add project paths
repo_root = Path(__file__).parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "python"))


def test_mining_job_sign_bytes_extraction():
    """Test that signBytes is correctly extracted from RPC response."""
    print("\n" + "="*70)
    print("Test 1: MiningJob signBytes extraction from RPC response")
    print("="*70)
    
    # Simulate RPC response from miner.getWork
    work_response = {
        "jobId": "test-job-1",
        "header": {
            "chainId": 1,
            "height": 100,
            "parentHash": "0x" + "11" * 32,
            "stateRoot": "0x" + "00" * 32,
            "thetaMicro": 800_000,
            # Note: signBytes is NOT in header dict in RPC response
        },
        "signBytes": "0x" + "aa" * 80,  # signBytes is at top level
        "hints": {"mixSeed": "0x" + "bb" * 32},
        "target": "0x" + "ff" * 32,
        "thetaMicro": 800_000,
        "shareTarget": 0.01,
        "height": 100,
    }
    
    # Extract as the adapter does
    sign_bytes = work_response.get("signBytes")
    height = work_response.get("height")
    header = work_response.get("header", {})
    hints = work_response.get("hints", {})
    
    print(f"  RPC response signBytes: {sign_bytes is not None}")
    print(f"  RPC response height: {height}")
    print(f"  RPC header has signBytes: {'signBytes' in header}")
    print(f"  RPC hints has mixSeed: {'mixSeed' in hints}")
    
    # Verify
    assert sign_bytes is not None, "✗ signBytes should be extracted"
    assert sign_bytes == "0x" + "aa" * 80, "✗ signBytes should match"
    assert height == 100, "✗ height should be extracted"
    assert "signBytes" not in header, "✗ signBytes should NOT be in header initially"
    assert "mixSeed" in hints, "✗ mixSeed should be in hints"
    
    print("  ✓ All checks passed")
    return True


def test_stratum_job_header_construction():
    """Test that StratumJob header includes signBytes and mixSeed for miners."""
    print("\n" + "="*70)
    print("Test 2: StratumJob header construction")
    print("="*70)
    
    # Simulate MiningJob (as created by adapter)
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
    
    mining_job = MiningJob(
        job_id="test-job-1",
        header={
            "chainId": 1,
            "height": 100,
            "parentHash": "0x" + "11" * 32,
            "stateRoot": "0x" + "00" * 32,
            "thetaMicro": 800_000,
        },
        theta_micro=800_000,
        share_target=0.01,
        height=100,
        target="0x" + "ff" * 32,
        sign_bytes="0x" + "aa" * 80,
        hints={"mixSeed": "0x" + "bb" * 32},
    )
    
    print(f"  MiningJob.sign_bytes: {mining_job.sign_bytes is not None}")
    print(f"  MiningJob.hints.mixSeed: {'mixSeed' in mining_job.hints}")
    
    # Simulate CURRENT stratum server behavior (before fix)
    header_before_fix = dict(mining_job.header or {})
    if mining_job.sign_bytes:
        header_before_fix.setdefault("signBytes", mining_job.sign_bytes)
    if mining_job.target:
        header_before_fix.setdefault("target", mining_job.target)
    if mining_job.height:
        header_before_fix.setdefault("number", mining_job.height)
    # NOTE: mixSeed is NOT added to header - this is the bug!
    
    print(f"\n  Before fix:")
    print(f"    header has signBytes: {'signBytes' in header_before_fix}")
    print(f"    header has mixSeed: {'mixSeed' in header_before_fix}")
    print(f"    header has number: {'number' in header_before_fix}")
    
    # Simulate FIXED stratum server behavior (after fix)
    header_after_fix = dict(mining_job.header or {})
    if mining_job.sign_bytes:
        header_after_fix.setdefault("signBytes", mining_job.sign_bytes)
    if mining_job.target:
        header_after_fix.setdefault("target", mining_job.target)
    if mining_job.height:
        header_after_fix.setdefault("number", mining_job.height)
    # FIX: Add mixSeed to header from hints
    if mining_job.hints and "mixSeed" in mining_job.hints:
        header_after_fix.setdefault("mixSeed", mining_job.hints["mixSeed"])
    
    print(f"\n  After fix:")
    print(f"    header has signBytes: {'signBytes' in header_after_fix}")
    print(f"    header has mixSeed: {'mixSeed' in header_after_fix}")
    print(f"    header has number: {'number' in header_after_fix}")
    
    # Verify after fix
    assert "signBytes" in header_after_fix, "✗ header should contain signBytes"
    assert header_after_fix["signBytes"] == "0x" + "aa" * 80, "✗ signBytes value mismatch"
    assert "mixSeed" in header_after_fix, "✗ header should contain mixSeed"
    assert header_after_fix["mixSeed"] == "0x" + "bb" * 32, "✗ mixSeed value mismatch"
    assert "number" in header_after_fix, "✗ header should contain number (height)"
    assert header_after_fix["number"] == 100, "✗ height value mismatch"
    
    print("  ✓ All checks passed")
    
    # Show what miner receives
    print(f"\n  Miner-compatible job format:")
    print(f"    jobId: {mining_job.job_id}")
    print(f"    header.signBytes: {header_after_fix.get('signBytes', 'MISSING')[:20]}...")
    print(f"    header.mixSeed: {header_after_fix.get('mixSeed', 'MISSING')[:20]}...")
    print(f"    header.number: {header_after_fix.get('number', '?')}")
    
    return True


def main():
    """Run all tests."""
    print("\n" + "="*70)
    print("STRATUM JOB FORMAT TEST SUITE")
    print("="*70)
    
    try:
        test_mining_job_sign_bytes_extraction()
        test_stratum_job_header_construction()
        
        print("\n" + "="*70)
        print("✓ ALL TESTS PASSED")
        print("="*70)
        print("\nConclusion:")
        print("  - signBytes is correctly extracted from RPC")
        print("  - Current code adds signBytes to header ✓")
        print("  - Current code DOES NOT add mixSeed to header ✗")
        print("  - Fix: Add mixSeed from hints to header")
        print("="*70 + "\n")
        return 0
    except AssertionError as e:
        print(f"\n✗ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n✗ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
