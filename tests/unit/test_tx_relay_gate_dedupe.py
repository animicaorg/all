from __future__ import annotations

from p2p.protocol.tx_relay import TxRelayGate


def test_tx_relay_gate_dedupes_seen_payloads() -> None:
    gate = TxRelayGate(bloom_m_bits=1024, bloom_k=3, generations=2)
    raw = b"fake-tx-payload"

    first = gate.admit_tx_body(raw)
    assert first.accepted is True
    assert first.tx_hash is not None

    second = gate.admit_tx_body(raw)
    assert second.accepted is False
    assert second.reason == "duplicate"
