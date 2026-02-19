#!/usr/bin/env python3
"""
Example: Using the DA Storage Provider Subsystem

This script demonstrates the core functionality of the storage provider
subsystem, including registration, blob storage, and service operation.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

# Import provider components
from da.provider.registry import (
    ProviderRegistry,
    create_provider_entry,
    create_provider_id,
)


def example_provider_registration():
    """Example 1: Register a storage provider."""
    print("=" * 60)
    print("Example 1: Provider Registration")
    print("=" * 60)

    # Create a temporary database
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "registry.db"
        registry = ProviderRegistry(db_path=db_path)

        # Create provider keypair (simplified for example)
        import hashlib
        import secrets

        privkey = secrets.token_bytes(32)
        pubkey = hashlib.sha3_256(privkey).digest() + secrets.token_bytes(32)
        
        # Create payment address
        address = hashlib.sha3_256(pubkey).digest()[:20]

        # Create provider entry
        entry = create_provider_entry(
            pubkey=pubkey,
            address=address,
            endpoint="https://provider.example.com:9090",
            capacity_bytes=1_000_000_000_000,  # 1 TB
            region_tags=["us-west", "ssd", "low-latency"],
        )

        # Register
        registry.register_provider(entry)

        provider_id = create_provider_id(pubkey)
        print(f"✓ Provider registered")
        print(f"  Provider ID: {provider_id.hex()[:32]}...")
        print(f"  Endpoint: {entry.endpoint}")
        print(f"  Capacity: {entry.capacity_bytes_advertised:,} bytes")
        print(f"  Regions: {', '.join(entry.region_tags)}")
        print(f"  Uptime Score: {entry.uptime_score / 100:.1f}%")

        # Retrieve and verify
        retrieved = registry.get_provider(provider_id)
        assert retrieved is not None
        print(f"\n✓ Provider successfully retrieved from database")


def example_blob_assignment():
    """Example 2: Assign blobs to a provider."""
    print("\n" + "=" * 60)
    print("Example 2: Blob Assignment")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "registry.db"
        registry = ProviderRegistry(db_path=db_path)

        # Register a provider
        import hashlib
        import secrets

        pubkey = secrets.token_bytes(64)
        address = hashlib.sha3_256(pubkey).digest()[:20]

        entry = create_provider_entry(
            pubkey=pubkey,
            address=address,
            endpoint="https://provider.example.com:9090",
            capacity_bytes=10_000_000_000,
        )
        registry.register_provider(entry)

        # Create blob assignments
        from da.provider.registry import BlobAssignment
        import time

        for i in range(3):
            blob_commitment = hashlib.sha3_256(f"blob_{i}".encode()).digest()
            assignment = BlobAssignment(
                blob_commitment=blob_commitment,
                provider_id=entry.provider_id,
                assigned_at=int(time.time()),
                replicas=3,
                blob_size=4096 * (i + 1),
            )
            registry.add_assignment(assignment)
            print(
                f"✓ Assigned blob {blob_commitment.hex()[:16]}... "
                f"({assignment.blob_size} bytes, {assignment.replicas} replicas)"
            )

        # Retrieve assignments
        assignments = registry.get_assignments_for_provider(entry.provider_id)
        print(f"\n✓ Provider has {len(assignments)} blob assignment(s)")


def example_provider_service():
    """Example 3: Store and serve blobs."""
    print("\n" + "=" * 60)
    print("Example 3: Provider Service (Blob Storage)")
    print("=" * 60)

    try:
        from da.provider.service import ProviderService
        from fastapi import FastAPI
    except ImportError:
        print("⚠ FastAPI not available, skipping service example")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        storage_path = Path(tmpdir) / "storage"
        service = ProviderService(
            storage_path=storage_path,
            rate_limit_rps=100,
        )

        # Store some blobs
        import hashlib

        blobs = [
            (b"Hello, world!", "greeting"),
            (b"Test blob data content", "test"),
            (b"x" * 1024, "kilobyte"),
        ]

        for data, name in blobs:
            commitment = hashlib.sha3_256(data).digest()
            blob_path = service.store_blob(commitment, data)
            print(
                f"✓ Stored blob '{name}': "
                f"{commitment.hex()[:16]}... ({len(data)} bytes)"
            )
            print(f"  Path: {blob_path.relative_to(storage_path)}")

        # Verify retrieval
        print("\n✓ Verifying blob retrieval:")
        for data, name in blobs:
            commitment = hashlib.sha3_256(data).digest()
            retrieved = service.get_blob(commitment)
            assert retrieved == data
            print(f"  ✓ '{name}' retrieved successfully")

        # Check organization
        print(f"\n✓ Storage organization:")
        print(f"  Base: {storage_path}")
        for prefix_dir in sorted(storage_path.iterdir()):
            blobs_in_prefix = list(prefix_dir.glob("*.blob"))
            print(f"  {prefix_dir.name}/: {len(blobs_in_prefix)} blob(s)")


def example_capacity_tracking():
    """Example 4: Track total capacity across providers."""
    print("\n" + "=" * 60)
    print("Example 4: Capacity Tracking")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "registry.db"
        registry = ProviderRegistry(db_path=db_path)

        # Register multiple providers with different capacities
        import hashlib
        import secrets

        capacities = [
            (1_000_000_000_000, ["us-west"]),  # 1 TB
            (2_000_000_000_000, ["us-east"]),  # 2 TB
            (500_000_000_000, ["eu-central"]),  # 500 GB
        ]

        for capacity, regions in capacities:
            pubkey = secrets.token_bytes(64)
            address = hashlib.sha3_256(pubkey).digest()[:20]

            entry = create_provider_entry(
                pubkey=pubkey,
                address=address,
                endpoint=f"https://{regions[0]}.example.com:9090",
                capacity_bytes=capacity,
                region_tags=regions,
            )
            registry.register_provider(entry)
            print(
                f"✓ Registered provider in {regions[0]}: "
                f"{capacity / 1e9:.1f} GB capacity"
            )

        # Get total capacity
        total_adv, total_comm = registry.get_total_capacity()
        print(f"\n✓ Network-wide capacity:")
        print(f"  Advertised: {total_adv / 1e12:.2f} TB")
        print(f"  Committed:  {total_comm / 1e12:.2f} TB")
        print(f"  Available:  {(total_adv - total_comm) / 1e12:.2f} TB")


def main():
    """Run all examples."""
    print("\n" + "=" * 60)
    print("DA Storage Provider Subsystem Examples")
    print("=" * 60 + "\n")

    try:
        example_provider_registration()
        example_blob_assignment()
        example_provider_service()
        example_capacity_tracking()

        print("\n" + "=" * 60)
        print("All examples completed successfully! ✓")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ Example failed: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
