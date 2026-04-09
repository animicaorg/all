import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.reference_cpu_miner import _normalize_job_payload


def test_normalize_job_payload_accepts_live_pool_snake_case_theta():
    job = {
        "jobId": "job-live",
        "shareTarget": 0.999999,
        "header": {
            "number": 1,
            "signBytes": "0x1234",
            "theta_target_micro": 1_000_000,
        },
    }

    job_id, header, sign_hex, theta_micro, share_target = _normalize_job_payload(
        job,
        default_theta_micro=0,
        default_share_target=0.01,
    )

    assert job_id == "job-live"
    assert header["number"] == 1
    assert sign_hex == "0x1234"
    assert theta_micro == 1_000_000
    assert share_target == 0.999999


def test_normalize_job_payload_accepts_header_template_shape():
    job = {
        "jobId": "job-template",
        "headerTemplate": {
            "signBytes": "0xabcd",
            "thetaMicro": 5_400_000,
        },
    }

    job_id, _header, sign_hex, theta_micro, share_target = _normalize_job_payload(
        job,
        default_theta_micro=0,
        default_share_target=0.25,
    )

    assert job_id == "job-template"
    assert sign_hex == "0xabcd"
    assert theta_micro == 5_400_000
    assert share_target == 0.25
