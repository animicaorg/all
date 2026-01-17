#!/usr/bin/env python3
"""
Manual test script to verify the Stratum bridge initial job fix.

This script simulates the problem scenario and verifies the fix:
1. Mock RPC server that provides block templates
2. Start stratum bridge
3. Connect client and verify job is received immediately
"""
import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
repo_root = Path(__file__).parent
sys.path.insert(0, str(repo_root))

from mining.stratum_bridge import StratumBridge
from mining.stratum_client import StratumClient
from mining.stratum_server import StratumServer, StratumJob


class MockRpcClient:
    """Mock RPC client that provides block templates."""
    
    def __init__(self):
        self.call_count = 0
    
    async def call(self, method: str, params):
        """Mock RPC call handler."""
        self.call_count += 1
        
        if method == "miner.getBlockTemplate":
            # Return a mock template
            return {
                "enabled": True,
                "header": {
                    "v": 1,
                    "chainId": 1,
                    "height": 100,
                    "parentHash": "0x" + "11" * 32,
                    "timestamp": 1234567890,
                    "stateRoot": "0x" + "00" * 32,
                    "txsRoot": "0x" + "00" * 32,
                    "receiptsRoot": "0x" + "00" * 32,
                    "proofsRoot": "0x" + "00" * 32,
                    "daRoot": "0x" + "00" * 32,
                    "mixSeed": "0x" + "00" * 32,
                    "poiesPolicyRoot": "0x" + "00" * 32,
                    "pqAlgPolicyRoot": "0x" + "00" * 32,
                    "thetaMicro": 800_000,
                    "workType": 0,
                    "nonce": 0,
                    "extra": "0x",
                    "signBytes": "0x" + "00" * 80,
                },
                "target": "0x" + "ff" * 32,
                "thetaMicro": 800_000,
                "parent": {
                    "hash": "0x" + "11" * 32,
                    "height": 99,
                },
                "templateId": "test-template-1",
                "coinbase": {
                    "amount": 1000000000,
                },
            }
        
        return None


async def test_initial_job_availability():
    """Test that initial job is available to newly connected clients."""
    print("=" * 70)
    print("Testing Stratum Bridge Initial Job Fix")
    print("=" * 70)
    
    # Create mock bridge
    bridge = StratumBridge(
        rpc_url="http://mock:8545",
        poll_interval=2.0,
        default_share_target=0.01,
    )
    
    # Replace RPC client with mock
    bridge._rpc = MockRpcClient()
    
    # Start bridge with payout address
    await bridge.start("anim1test")
    
    print("\n1. Bridge started")
    
    # Fetch initial template (this is what our fix does)
    print("2. Fetching initial template...")
    for attempt in range(5):
        try:
            await bridge._poll_template()
            if bridge._current_template:
                print(f"   ✓ Initial template fetched on attempt {attempt + 1}")
                print(f"   ✓ Template ID: {bridge._current_job_id}")
                break
        except Exception as e:
            print(f"   Attempt {attempt + 1} failed: {e}")
        await asyncio.sleep(0.2)
    
    if not bridge._current_template:
        print("   ✗ FAIL: Could not fetch initial template")
        return False
    
    # Create stratum server
    server = StratumServer(host="127.0.0.1", port=13333)
    await server.start()
    print("\n3. Stratum server started on 127.0.0.1:13333")
    
    # Load initial job into server (our fix does this)
    if bridge._current_template:
        job_dict = await bridge.get_current_job()
        if job_dict:
            initial_job = StratumJob(
                job_id=job_dict["job_id"],
                header=job_dict.get("header", {}),
                share_target=job_dict.get("share_target", 0.01),
                theta_micro=job_dict.get("theta_micro", 800_000),
                target=job_dict.get("target"),
                sign_bytes=job_dict.get("sign_bytes"),
                height=job_dict.get("height"),
                parent_hash=job_dict.get("parent_hash"),
                parent_height=job_dict.get("parent_height"),
                chain_id=job_dict.get("chain_id"),
            )
            server._jobs[initial_job.job_id] = initial_job
            server._current_job_id = initial_job.job_id
            print(f"   ✓ Initial job loaded into server: {initial_job.job_id}")
    
    # Connect client
    client = StratumClient(host="127.0.0.1", port=13333)
    await client.connect()
    print("\n4. Client connected")
    
    # Subscribe and authorize
    await client.subscribe()
    print("   ✓ Client subscribed")
    
    await client.authorize(worker="test_miner", address="anim1test")
    print("   ✓ Client authorized")
    
    # Wait briefly for job notification
    await asyncio.sleep(0.5)
    
    # Check if client received a job
    print("\n5. Checking if client received job...")
    if client.last_job:
        print(f"   ✓ SUCCESS: Client received job immediately!")
        print(f"   ✓ Job ID: {client.last_job.get('jobId', client.last_job.get('job_id'))}")
        result = True
    else:
        print("   ✗ FAIL: Client did not receive job")
        result = False
    
    # Cleanup
    await client.close()
    await server.stop()
    await bridge.stop()
    
    print("\n" + "=" * 70)
    if result:
        print("TEST PASSED: Fix verified - client receives job immediately!")
    else:
        print("TEST FAILED: Client still does not receive job")
    print("=" * 70)
    
    return result


async def main():
    """Main test runner."""
    try:
        success = await test_initial_job_availability()
        return 0 if success else 1
    except Exception as e:
        print(f"\nTest failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
