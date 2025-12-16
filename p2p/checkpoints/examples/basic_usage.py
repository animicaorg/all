"""
Example: Basic checkpoint usage.

This example shows how to:
1. Load checkpoint configuration from environment
2. Initialize the checkpoint system
3. Verify chain against checkpoints
"""

import asyncio
import os
from pathlib import Path


async def main():
    # Import checkpoint modules
    from p2p.checkpoints import (
        initialize_checkpoints,
        verify_chain_checkpoints,
        load_checkpoints_config,
        get_checkpoint_config_summary,
    )
    
    # 1. Load configuration from environment
    print("=== Checkpoint Configuration ===")
    config = load_checkpoints_config()
    summary = get_checkpoint_config_summary(config)
    
    print(f"Mode: {summary['mode']}")
    print(f"Enabled: {summary['enabled']}")
    if summary['rpc_url']:
        print(f"RPC URL: {summary['rpc_url']}")
    if summary['file_path']:
        print(f"File path: {summary['file_path']}")
    print(f"Strict: {summary['strict']}")
    print()
    
    # 2. Initialize checkpoint system
    print("=== Initializing Checkpoints ===")
    try:
        verifier = await initialize_checkpoints(config)
        
        if verifier is None:
            print("Checkpoints disabled (mode=off)")
            return
        
        if verifier.has_checkpoints():
            lowest = verifier.get_lowest_checkpoint_height()
            highest = verifier.get_highest_checkpoint_height()
            print(f"Loaded checkpoints: heights {lowest} to {highest}")
        else:
            print("No checkpoints loaded")
            return
    
    except Exception as e:
        print(f"Failed to initialize checkpoints: {e}")
        return
    
    print()
    
    # 3. Create a mock chain view for demonstration
    class MockChainView:
        """Mock chain view for demonstration."""
        
        def __init__(self, blocks):
            self.blocks = blocks
        
        async def get_block_hash_at_height(self, height):
            return self.blocks.get(height)
    
    # Example: Create a chain with some blocks matching the example checkpoints
    mock_chain = MockChainView({
        1000: "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
        5000: "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
    })
    
    # 4. Verify chain against checkpoints
    print("=== Verifying Chain ===")
    is_valid, errors = await verify_chain_checkpoints(
        verifier=verifier,
        chain_view=mock_chain,
        max_height=10000,
    )
    
    if is_valid:
        print("✓ Chain verification passed!")
    else:
        print("✗ Chain verification failed!")
        for error in errors:
            print(f"  - {error}")


if __name__ == "__main__":
    # Use file mode with example checkpoint file
    example_file = Path(__file__).parent.parent / "fixtures" / "example_checkpoints.json"
    os.environ["ANIMICA_CHECKPOINTS_MODE"] = "file"
    os.environ["ANIMICA_CHECKPOINTS_FILE"] = str(example_file)
    
    # Run the example
    asyncio.run(main())
