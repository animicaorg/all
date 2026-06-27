"""Worker run_once against the local in-process QUW service (software path)."""
from __future__ import annotations

from animica.quantum.quw_worker import QuwWorker, selftest
from randomness.qrng.service import QuantumWorkService


def test_worker_run_once_local_service():
    svc = QuantumWorkService()

    def submit(method, params):
        if method == "rand.getQuantumChallenge":
            return svc.get_challenge(int(params.get("round_id", 0)))
        if method == "rand.contributeQuantumEntropy":
            return svc.contribute(params.get("contribution") or params)
        raise AssertionError(method)

    w = QuwWorker(address="anim1worker", n_bytes=2048, submit_fn=submit)
    out = w.run_once(round_id=11)
    assert out["submitted"] is True
    assert out["result"]["accepted"] is True
    assert out["result"]["credited_units"] > 0


def test_selftest_reports_verified():
    rep = selftest(n_bytes=2048)
    assert rep["verified"] is True
    assert rep["metrics"]["quantum_units"] > 0
