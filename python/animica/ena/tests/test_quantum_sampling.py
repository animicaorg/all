"""Tests for animica.ena.quantum_sampling — quantum-seeded generation (7.1.1 P2)."""

from animica.ena import quantum_sampling as QS


def test_seed_from_beacon_and_verify(monkeypatch):
    QS.beacon_round_cache_clear()
    beacon_hex = "11" * 32

    def fake_rpc(method, params, *, timeout):
        if method == "rand.getQuantumBeacon":
            return {"available": True, "value_hex": beacon_hex, "round_id": 42, "attested": True}
        return None

    monkeypatch.setattr(QS, "_rpc", fake_rpc)
    qs = QS.quantum_seed_for_request("req-1", attested=True)
    assert qs is not None and qs.beacon_round == 42 and qs.attested is True

    from aicf.integration.quantum_seed import verify_quantum_seed
    assert verify_quantum_seed(qs, beacon_seed=bytes.fromhex(beacon_hex)) is True

    d = QS.seed_receipt_dict(qs, seed_applied=True)
    assert d["seed_applied"] is True and d["attestation_backend"] == "beacon"
    assert isinstance(QS.seed_int(qs), int)
    QS.beacon_round_cache_clear()


def test_local_fallback_when_no_node(monkeypatch):
    QS.beacon_round_cache_clear()
    monkeypatch.setattr(QS, "_rpc", lambda *a, **k: None)
    qs = QS.quantum_seed_for_request("req-2", attested=True)
    assert qs is not None and qs.attested is False
    d = QS.seed_receipt_dict(qs, seed_applied=False)
    assert d["attestation_backend"] == "local" and d["seed_applied"] is False
    QS.beacon_round_cache_clear()


def test_randombytes_fallback_labels_software(monkeypatch):
    QS.beacon_round_cache_clear()

    def fake_rpc(method, params, *, timeout):
        if method == "rand.getQuantumBeacon":
            return {"available": False}
        if method == "quantum.quw.randomBytes":
            return {"bytes_hex": "22" * 32,
                    "source": {"is_hardware": False, "name": "software-fallback", "attested": False},
                    "attestation": {"attested": False}}
        return None

    monkeypatch.setattr(QS, "_rpc", fake_rpc)
    qs = QS.quantum_seed_for_request("req-3")
    d = QS.seed_receipt_dict(qs, seed_applied=True)
    assert d["attested"] is False
    assert d["attestation_backend"] in ("software", "software-fallback")
    QS.beacon_round_cache_clear()


def test_beacon_cache_avoids_refetch(monkeypatch):
    QS.beacon_round_cache_clear()
    calls = {"n": 0}

    def fake_rpc(method, params, *, timeout):
        if method == "rand.getQuantumBeacon":
            calls["n"] += 1
            return {"available": True, "value_hex": "33" * 32, "round_id": 7, "attested": False}
        return None

    monkeypatch.setattr(QS, "_rpc", fake_rpc)
    QS.quantum_seed_for_request("a")
    QS.quantum_seed_for_request("b")
    # Two requests, one beacon fetch (cached within the TTL window).
    assert calls["n"] == 1
    QS.beacon_round_cache_clear()
