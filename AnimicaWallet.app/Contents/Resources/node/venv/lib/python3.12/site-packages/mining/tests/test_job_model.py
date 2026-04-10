import time

from mining.challenges import derive_challenge
from mining.proof_payloads import build_payload, verify_payload
from mining.templates import TemplateBuilder


def test_job_id_determinism(monkeypatch):
    fixed_time = 1_700_000_000
    monkeypatch.setattr(time, "time", lambda: fixed_time)

    parent_hash = b"\x11" * 32
    parent_mix = b"\x22" * 32

    def _head():
        return parent_hash, 10, parent_mix, 1, b"\x33" * 32

    def _theta():
        return 500_000

    def _roots():
        return b"\x44" * 32, b"\x55" * 32

    def _beacon():
        return b"beacon"

    tb = TemplateBuilder(
        get_head_info=_head,
        get_theta=_theta,
        get_policy_roots=_roots,
        get_beacon=_beacon,
    )

    job1 = tb.current_job(force=True, proof_type="sha256d")
    job2 = tb.current_job(force=True, proof_type="sha256d")
    assert job1.job_id == job2.job_id


def test_aicf_challenge_and_payload_verification():
    challenge = derive_challenge(
        chain_id=1,
        parent_hash=b"\x00" * 32,
        parent_height=0,
        proof_type="aicf",
    )
    payload = build_payload(
        challenge=challenge,
        output_digest=b"\x99" * 32,
        metrics={"ai_units": 10, "qos": 0.99},
    )
    assert verify_payload(payload)
