import asyncio

import pytest

from mining.challenges import derive_challenge
from mining.proof_payloads import verify_payload
from mining.quantum_worker import SimulatedQuantumProvider


@pytest.mark.asyncio
async def test_quantum_simulated_provider_payload():
    challenge = derive_challenge(
        chain_id=1,
        parent_hash=b"\x01" * 32,
        parent_height=42,
        proof_type="quantum",
    )
    provider = SimulatedQuantumProvider()
    payload = await provider.solve(challenge)
    assert verify_payload(payload)
