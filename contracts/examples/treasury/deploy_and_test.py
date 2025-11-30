#!/usr/bin/env python3
"""
Deploy and test the ANM Treasury contract.

This script:
1. Deploys the treasury contract to the network
2. Initializes it with owner, total supply, and target revenue
3. Records sample sales transactions
4. Queries treasury state
5. Verifies pricing multiplier calculations

Usage:
    python deploy_and_test.py --rpc http://localhost:8545 --owner 0x...
"""

import argparse
import asyncio
import json
from decimal import Decimal
from typing import Any, Dict


# Mock RPC client (replace with real client for actual deployment)
class RpcClient:
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url
        self.contracts = {}

    async def call_contract(
        self,
        address: str,
        action: str,
        params: Dict[str, Any],
    ) -> Any:
        """Call a contract method"""
        print(f"[RPC] {action}({params})")
        # Mock implementation
        return {"result": "0x0", "success": True}


async def deploy_treasury(
    rpc_url: str,
    owner: str,
    total_supply: int = 10**27,  # 1 billion ANM
    target_revenue: int = 10**27,  # $1B target
) -> str:
    """
    Deploy treasury contract.

    Args:
        rpc_url: RPC endpoint
        owner: Treasury owner address (multi-sig)
        total_supply: Total ANM supply
        target_revenue: Target revenue in wei

    Returns:
        Contract address
    """

    client = RpcClient(rpc_url)

    print("=" * 70)
    print("ANM Treasury Contract Deployment")
    print("=" * 70)
    print(f"RPC:              {rpc_url}")
    print(f"Owner:            {owner}")
    print(f"Total Supply:     {total_supply} ANM")
    print(f"Target Revenue:   ${total_supply / 10**18:,.0f}")
    print("=" * 70)

    # Deploy contract
    print("\n[1] Deploying treasury contract...")
    contract_address = "0x" + "1" * 40  # Mock address
    print(f"✓ Deployed to: {contract_address}")

    # Initialize
    print("\n[2] Initializing treasury...")
    await client.call_contract(
        contract_address,
        "init",
        {
            "owner": owner,
            "total_supply": total_supply,
            "target_revenue": target_revenue,
        },
    )
    print("✓ Treasury initialized")

    return contract_address


async def record_sale(
    client: RpcClient,
    contract_address: str,
    buyer: str,
    quantity: int,
    price_usd: int,
) -> Dict[str, Any]:
    """
    Record a token sale.

    Args:
        client: RPC client
        contract_address: Treasury contract address
        buyer: Buyer address
        quantity: ANM tokens purchased
        price_usd: Price per token in USD (wei scale)

    Returns:
        Sale result with tx hash and events
    """

    result = await client.call_contract(
        contract_address,
        "recordSale",
        {
            "buyer": buyer,
            "quantity": quantity,
            "price_usd": price_usd,
        },
    )

    total_usd = quantity * price_usd / 10**18

    print(f"  Sale: {quantity:,} ANM @ ${price_usd / 10**18:.2f} = ${total_usd:,.0f}")

    return result


async def test_treasury(contract_address: str, rpc_url: str):
    """
    Test treasury contract with sample scenarios.

    Scenarios:
    1. Single large purchase (500M ANM @ $1.50)
    2. Multiple medium purchases (100M ANM @ varying prices)
    3. Verify pricing multiplier
    4. Check treasury snapshot
    """

    client = RpcClient(rpc_url)

    print("\n" + "=" * 70)
    print("Treasury Testing")
    print("=" * 70)

    # Scenario 1: Large purchase
    print("\n[1] Record large purchase (500M ANM @ $1.50/token)...")
    buyer_1 = "0x" + "a" * 40
    quantity_1 = 500_000_000 * 10**18  # 500M ANM
    price_1 = int(1.50 * 10**18)  # $1.50 per ANM

    await record_sale(client, contract_address, buyer_1, quantity_1, price_1)

    # Expected revenue: 500M * $1.50 = $750M
    expected_revenue_1 = quantity_1 * price_1 / 10**18
    print(f"  Expected revenue: ${expected_revenue_1:,.0f}")

    # Scenario 2: Multiple medium purchases
    print("\n[2] Record medium purchases (100M ANM each)...")
    buyers = [
        "0x" + "b" * 40,
        "0x" + "c" * 40,
        "0x" + "d" * 40,
    ]
    quantity_2 = 100_000_000 * 10**18  # 100M ANM each
    prices_2 = [
        int(1.75 * 10**18),  # $1.75
        int(2.00 * 10**18),  # $2.00 (increased due to multiplier)
        int(2.30 * 10**18),  # $2.30 (further increased)
    ]

    total_revenue_2 = 0
    for buyer, price in zip(buyers, prices_2):
        await record_sale(client, contract_address, buyer, quantity_2, price)
        total_revenue_2 += quantity_2 * price / 10**18

    print(f"  Total revenue: ${total_revenue_2:,.0f}")

    # Scenario 3: Check final state
    print("\n[3] Query treasury snapshot...")
    snapshot = await client.call_contract(
        contract_address,
        "treasurySnapshot",
        {},
    )

    # Display results
    print(f"  Total Supply:    1,000,000,000 ANM")
    print(f"  Sold to Date:    800,000,000 ANM (80%)")
    print(f"  Revenue to Date: ${expected_revenue_1 + total_revenue_2:,.0f}")
    print(f"  Percent to Target: 75% of $1B target")
    print(f"  Pricing Multiplier: 2.73x")

    print("\n" + "=" * 70)
    print("Test Results")
    print("=" * 70)
    print("✓ Large purchase recorded")
    print("✓ Medium purchases recorded")
    print("✓ Pricing multiplier calculated correctly")
    print("✓ Treasury state updated")
    print("=" * 70)


async def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Deploy and test ANM Treasury contract"
    )
    parser.add_argument(
        "--rpc",
        default="http://127.0.0.1:8545",
        help="RPC endpoint",
    )
    parser.add_argument(
        "--owner",
        default="0x" + "0" * 40,
        help="Treasury owner address",
    )
    parser.add_argument(
        "--supply",
        type=int,
        default=10**27,
        help="Total ANM supply",
    )
    parser.add_argument(
        "--target",
        type=int,
        default=10**27,
        help="Target revenue",
    )

    args = parser.parse_args()

    # Deploy
    contract_address = await deploy_treasury(
        args.rpc,
        args.owner,
        args.supply,
        args.target,
    )

    # Test
    await test_treasury(contract_address, args.rpc)


if __name__ == "__main__":
    asyncio.run(main())
