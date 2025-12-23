import time

from mining import device as device_mod
from mining.hash_search import micro_threshold_to_target256
from mining.templates import TemplateBuilder


def test_cpu_backend_finds_valid_nonce(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: 1_700_000_000)

    parent_hash = b"\x10" * 32
    parent_mix = b"\x20" * 32

    def _head():
        return parent_hash, 1, parent_mix, 1, b"\x30" * 32

    tb = TemplateBuilder(
        get_head_info=_head,
        get_theta=lambda: 1,  # tiny threshold for easy shares
        get_policy_roots=lambda: (b"\x40" * 32, b"\x50" * 32),
        get_beacon=lambda: b"beacon",
    )
    job = tb.current_job(force=True, proof_type="sha256d")

    dev = device_mod.create("cpu", threads=1)
    prepared = dev.prepare_header(job.sign_bytes, job.header.mix_seed)
    shares = dev.scan(
        prepared,
        theta_micro=1.0,
        start_nonce=0,
        iterations=8,
        max_found=2,
        thread_id=0,
    )
    assert shares, "expected to find at least one share"

    target = micro_threshold_to_target256(1)
    digest_int = int.from_bytes(shares[0]["hash"], "big")
    assert digest_int <= target
