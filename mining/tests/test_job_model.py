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


def test_template_and_job_refresh_when_theta_changes(monkeypatch):
    fixed_time = 1_700_000_123
    monkeypatch.setattr(time, "time", lambda: fixed_time)

    parent_hash = b"\xaa" * 32
    parent_mix = b"\xbb" * 32
    theta_holder = {"value": 700_000}

    def _head():
        return parent_hash, 42, parent_mix, 1, b"\xcc" * 32

    def _theta():
        return int(theta_holder["value"])

    def _roots():
        return b"\xdd" * 32, b"\xee" * 32

    tb = TemplateBuilder(
        get_head_info=_head,
        get_theta=_theta,
        get_policy_roots=_roots,
        get_beacon=lambda: b"",
    )

    tpl_a = tb.current_template()
    job_a = tb.current_job()

    # Parent is unchanged, only theta moves.
    theta_holder["value"] = 900_000

    tpl_b = tb.current_template()
    job_b = tb.current_job()

    assert tpl_a.parent_hash == tpl_b.parent_hash
    assert tpl_a.theta_target_micro != tpl_b.theta_target_micro
    assert tpl_a.sign_bytes != tpl_b.sign_bytes
    assert job_a.job_id != job_b.job_id
