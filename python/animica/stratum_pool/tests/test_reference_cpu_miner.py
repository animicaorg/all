import sys
import asyncio
import argparse
import json
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import animica.stratum_pool.reference_cpu_miner as miner_mod
from animica.stratum_pool.reference_cpu_miner import (
    MinerConfig,
    ShareResult,
    StratumCpuMiner,
    SubmitOutcome,
    _apply_runtime_difficulty_to_job,
    _is_stale_job_reason,
    _normalize_job_payload,
    _should_stop_job,
    resolve_config,
)


def test_scan_range_worker_scans_full_window_and_returns_best_share(
    monkeypatch: pytest.MonkeyPatch,
):
    def digest_for_h_micro(h_micro: int) -> bytes:
        unit = math.exp(-float(h_micro) / float(miner_mod.MICRO))
        digest_int = int(unit * float(miner_mod.UINT256_MAX + 1) - 1.0)
        digest_int = max(0, min(miner_mod.UINT256_MAX, digest_int))
        return int(digest_int).to_bytes(32, "big", signed=False)

    def fake_digest_from_sign_bytes(
        _prefix: bytes,
        *,
        mix_seed: bytes = b"",
        nonce_int: int,
        nonce_byteorder: str = "little",
    ) -> bytes:
        _ = mix_seed, nonce_byteorder
        desired_h = 10_000 + (int(nonce_int) * 100_000)
        return digest_for_h_micro(desired_h)

    monkeypatch.setattr(miner_mod, "digest_from_sign_bytes", fake_digest_from_sign_bytes)
    share = miner_mod._scan_range_worker(
        b"\x01\x02",
        b"",
        miner_mod.UINT256_MAX,
        1_000_000,
        0,
        5,
    )

    assert share is not None
    assert share.nonce == 4


def test_scan_header_range_worker_scans_full_window_and_returns_best_share(
    monkeypatch: pytest.MonkeyPatch,
):
    def digest_for_h_micro(h_micro: int) -> bytes:
        unit = math.exp(-float(h_micro) / float(miner_mod.MICRO))
        digest_int = int(unit * float(miner_mod.UINT256_MAX + 1) - 1.0)
        digest_int = max(0, min(miner_mod.UINT256_MAX, digest_int))
        return int(digest_int).to_bytes(32, "big", signed=False)

    def fake_hash_candidate_header(_header_template: object, *, nonce: int):
        desired_h = 10_000 + ((int(nonce) - 10) * 120_000)
        digest = digest_for_h_micro(desired_h)
        digest_int = int.from_bytes(digest, "big", signed=False)
        return SimpleNamespace(digest_int=digest_int, digest=digest)

    monkeypatch.setattr(miner_mod, "_hash_candidate_header", fake_hash_candidate_header)
    share = miner_mod._scan_header_range_worker(
        object(),
        miner_mod.UINT256_MAX,
        1_000_000,
        10,
        4,
    )

    assert share is not None
    assert share.nonce == 13


def test_emit_share_batch_update_aggregates_rejections(capsys: pytest.CaptureFixture[str]):
    miner = StratumCpuMiner(
        MinerConfig(
            host="127.0.0.1",
            port=3333,
            scheme="stratum+tcp",
            tls=False,
            address="anim1qqq",
            worker="animica-cpu",
            threads=1,
            scan_window=25_000,
            stats_interval_sec=20.0,
            log_level="INFO",
        )
    )
    try:
        miner._apply_submit_outcome(
            SubmitOutcome(accepted=False, is_block=False, reason="low_difficulty", stale_job=False)
        )
        miner._apply_submit_outcome(
            SubmitOutcome(accepted=False, is_block=False, reason="low_difficulty", stale_job=False)
        )
        miner._apply_submit_outcome(
            SubmitOutcome(accepted=False, is_block=False, reason="bad_nonce", stale_job=False)
        )
        miner._apply_submit_outcome(
            SubmitOutcome(accepted=True, is_block=False, reason=None, stale_job=False)
        )
        miner._emit_share_batch_update()
        out = capsys.readouterr().out
        assert "accepted=+1" in out
        assert "rejected=+3" in out
        assert "low_difficulty x2" in out
        assert "bad_nonce x1" in out

        miner._emit_share_batch_update()
        out2 = capsys.readouterr().out
        assert out2 == ""
    finally:
        miner._scan_executor.shutdown(wait=False, cancel_futures=True)


@pytest.mark.asyncio
async def test_submit_share_does_not_log_warning_per_reject(caplog: pytest.LogCaptureFixture):
    miner = StratumCpuMiner(
        MinerConfig(
            host="127.0.0.1",
            port=3333,
            scheme="stratum+tcp",
            tls=False,
            address="anim1qqq",
            worker="animica-cpu",
            threads=1,
            scan_window=25_000,
            log_level="INFO",
        )
    )
    miner._session_id = "test-session"

    async def fake_call(_method: str, _params: dict[str, object]) -> dict[str, object]:
        return {"error": {"code": -32602, "message": "low difficulty share"}}

    miner._call = fake_call  # type: ignore[assignment]

    with caplog.at_level("WARNING", logger="animica.cpu_miner"):
        outcome = await miner._submit_share(
            "job-1",
            ShareResult(nonce=1, h_micro=1_000_000, d_ratio=1.0),
        )

    try:
        assert outcome.accepted is False
        assert outcome.stale_job is False
        assert not [r for r in caplog.records if r.levelname == "WARNING"]
    finally:
        miner._scan_executor.shutdown(wait=False, cancel_futures=True)


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


def test_apply_runtime_difficulty_to_job_overrides_stale_notify_target():
    job = {
        "jobId": "job-live",
        "shareTarget": 1.0,
        "header": {
            "number": 1,
            "signBytes": "0x1234",
            "thetaMicro": 1_000_000,
        },
    }

    updated = _apply_runtime_difficulty_to_job(
        job,
        theta_micro=850_000,
        share_target=0.25,
    )

    job_id, header, sign_hex, theta_micro, share_target = _normalize_job_payload(
        updated,
        default_theta_micro=0,
        default_share_target=1.0,
    )

    assert job_id == "job-live"
    assert sign_hex == "0x1234"
    assert theta_micro == 850_000
    assert share_target == 0.25
    assert header["thetaMicro"] == 850_000
    assert header["thetaTargetMicro"] == 850_000
    assert header["theta_target_micro"] == 850_000


def test_is_stale_job_reason_matches_pool_rpc_error():
    assert _is_stale_job_reason(
        "rpc:-32602:RPC error -32602: unknown or stale jobId"
    )


def test_is_stale_job_reason_matches_stale_template():
    assert _is_stale_job_reason(
        "rpc:-32063:RPC error -32063: stale template"
    )


def test_is_stale_job_reason_ignores_low_difficulty():
    assert not _is_stale_job_reason("low difficulty share")


def test_should_stop_job_after_accepted_block():
    assert _should_stop_job(SubmitOutcome(True, True, None, False))


def test_resolve_config_reads_api_and_mode_from_file(tmp_path: Path):
    config_path = tmp_path / "miner.json"
    config_path.write_text(
        json.dumps(
            {
                "host": "pool.animica.test",
                "port": 3333,
                "scheme": "stratum+tcp",
                "address": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
                "worker": "office-rig",
                "threads": 2,
                "api_base_url": "https://mine.animica.test",
                "pool_mode": "solo",
                "stats_interval_sec": 12,
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_config(
        argparse.Namespace(
            config=str(config_path),
            host=None,
            port=None,
            scheme=None,
            tls=False,
            api_base_url=None,
            address=None,
            worker=None,
            pool_mode=None,
            threads=None,
            scan_window=None,
            stats_interval=None,
            log_level=None,
        )
    )

    assert resolved.api_base_url == "https://mine.animica.test"
    assert resolved.pool_mode == "solo"
    assert resolved.stats_interval_sec == 12.0


def test_resolve_config_defaults_threads_to_local_cpu_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config_path = tmp_path / "miner-auto-threads.json"
    config_path.write_text(
        json.dumps(
            {
                "host": "pool.animica.test",
                "port": 3333,
                "address": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(miner_mod.os, "cpu_count", lambda: 32)

    resolved = resolve_config(
        argparse.Namespace(
            config=str(config_path),
            pool_url=None,
            host=None,
            port=None,
            scheme=None,
            tls=False,
            api_base_url=None,
            address=None,
            worker=None,
            pool_mode=None,
            threads=None,
            scan_window=None,
            stats_interval=None,
            log_level=None,
        )
    )

    assert resolved.threads == 32


def test_resolve_config_rejects_invalid_address(tmp_path: Path):
    config_path = tmp_path / "miner-invalid.json"
    config_path.write_text(
        json.dumps(
            {
                "host": "pool.animica.test",
                "port": 3333,
                "address": "not-an-address",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="Invalid Animica payout address"):
        resolve_config(
            argparse.Namespace(
                config=str(config_path),
                host=None,
                port=None,
                scheme=None,
                tls=False,
                api_base_url=None,
                address=None,
                worker=None,
                pool_mode=None,
                threads=None,
                scan_window=None,
                stats_interval=None,
                log_level=None,
            )
        )


def test_resolve_config_accepts_pool_url_override(tmp_path: Path):
    config_path = tmp_path / "miner-pool-url.json"
    config_path.write_text(
        json.dumps(
            {
                "pool_url": "stratum+tls://pool.animica.test:4444",
                "address": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq",
                "worker": "office-rig",
                "threads": 2,
            }
        ),
        encoding="utf-8",
    )

    resolved = resolve_config(
        argparse.Namespace(
            config=str(config_path),
            pool_url=None,
            host=None,
            port=None,
            scheme=None,
            tls=False,
            api_base_url=None,
            address=None,
            worker=None,
            pool_mode=None,
            threads=None,
            scan_window=None,
            stats_interval=None,
            log_level=None,
        )
    )

    assert resolved.host == "pool.animica.test"
    assert resolved.port == 4444
    assert resolved.scheme == "stratum+tls"
    assert resolved.tls is True


@pytest.mark.asyncio
async def test_mine_job_stops_after_stale_submit(monkeypatch: pytest.MonkeyPatch):
    miner = StratumCpuMiner(
        MinerConfig(
            host="127.0.0.1",
            port=3333,
            scheme="stratum+tcp",
            tls=False,
            address="anim1qqq",
            worker="animica-cpu",
            threads=1,
            scan_window=25_000,
            log_level="INFO",
        )
    )
    scans: list[int] = []

    def fake_scan(*_args, **_kwargs):
        scans.append(1)
        return ShareResult(nonce=2, h_micro=1_000_000, d_ratio=1.0)

    async def fake_submit(_job_id: str, _share: ShareResult) -> SubmitOutcome:
        return SubmitOutcome(
            False,
            True,
            "rpc:-32602:RPC error -32602: unknown or stale jobId",
            True,
        )

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(miner, "_scan_parallel", fake_scan)
    monkeypatch.setattr(miner, "_submit_share", fake_submit)
    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    try:
        await asyncio.wait_for(
            miner._mine_job(
                0,
                {
                    "jobId": "job-stale",
                    "signBytes": "0x1234",
                    "thetaMicro": 1_000_000,
                    "shareTarget": 1.0,
                },
            ),
            timeout=0.5,
        )
    finally:
        miner._scan_executor.shutdown(wait=False, cancel_futures=True)

    assert len(scans) == 1


@pytest.mark.asyncio
async def test_mine_job_stops_after_stale_template_submit(
    monkeypatch: pytest.MonkeyPatch,
):
    miner = StratumCpuMiner(
        MinerConfig(
            host="127.0.0.1",
            port=3333,
            scheme="stratum+tcp",
            tls=False,
            address="anim1qqq",
            worker="animica-cpu",
            threads=1,
            scan_window=25_000,
            log_level="INFO",
        )
    )
    scans: list[int] = []

    def fake_scan(*_args, **_kwargs):
        scans.append(1)
        return ShareResult(nonce=3, h_micro=1_000_000, d_ratio=1.0)

    async def fake_submit(_job_id: str, _share: ShareResult) -> SubmitOutcome:
        return SubmitOutcome(
            False,
            True,
            "rpc:-32063:RPC error -32063: stale template",
            _is_stale_job_reason("rpc:-32063:RPC error -32063: stale template"),
        )

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(miner, "_scan_parallel", fake_scan)
    monkeypatch.setattr(miner, "_submit_share", fake_submit)
    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    try:
        await asyncio.wait_for(
            miner._mine_job(
                0,
                {
                    "jobId": "job-stale-template",
                    "signBytes": "0x1234",
                    "thetaMicro": 1_000_000,
                    "shareTarget": 1.0,
                },
            ),
            timeout=0.5,
        )
    finally:
        miner._scan_executor.shutdown(wait=False, cancel_futures=True)

    assert len(scans) == 1


@pytest.mark.asyncio
async def test_mine_job_accepts_full_header_template_without_signbytes(
    monkeypatch: pytest.MonkeyPatch,
):
    miner = StratumCpuMiner(
        MinerConfig(
            host="127.0.0.1",
            port=3333,
            scheme="stratum+tcp",
            tls=False,
            address="anim1qqq",
            worker="animica-cpu",
            threads=1,
            scan_window=25_000,
            log_level="INFO",
        )
    )
    scans: list[int] = []

    def fake_scan(*_args, **_kwargs):
        scans.append(1)
        return ShareResult(nonce=7, h_micro=1_000_000, d_ratio=1.0)

    async def fake_submit(_job_id: str, _share: ShareResult) -> SubmitOutcome:
        return SubmitOutcome(True, True, None, False)

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(miner, "_scan_header_parallel", fake_scan)
    monkeypatch.setattr(miner, "_submit_share", fake_submit)
    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    try:
        await asyncio.wait_for(
            miner._mine_job(
                0,
                {
                    "jobId": "job-header-template",
                    "header": {
                        "v": 1,
                        "chainId": 1,
                        "height": 1,
                        "parentHash": "0x" + "11" * 32,
                        "timestamp": 1_800_000_000,
                        "stateRoot": "0x" + "22" * 32,
                        "txsRoot": "0x" + "33" * 32,
                        "receiptsRoot": "0x" + "44" * 32,
                        "proofsRoot": "0x" + "55" * 32,
                        "daRoot": "0x" + "66" * 32,
                        "mixSeed": "0x" + "77" * 32,
                        "poiesPolicyRoot": "0x" + "88" * 32,
                        "pqAlgPolicyRoot": "0x" + "99" * 32,
                        "thetaMicro": 1_000_000,
                        "workType": 0,
                        "extra": "0x",
                    },
                    "shareTarget": 1.0,
                },
            ),
            timeout=0.5,
        )
    finally:
        miner._scan_executor.shutdown(wait=False, cancel_futures=True)

    assert len(scans) == 1


@pytest.mark.asyncio
async def test_mine_job_falls_back_to_signbytes_when_header_helpers_unavailable(
    monkeypatch: pytest.MonkeyPatch,
):
    miner = StratumCpuMiner(
        MinerConfig(
            host="127.0.0.1",
            port=3333,
            scheme="stratum+tcp",
            tls=False,
            address="anim1qqq",
            worker="animica-cpu",
            threads=1,
            scan_window=25_000,
            log_level="INFO",
        )
    )
    scans: list[int] = []

    def fake_scan(*_args, **_kwargs):
        scans.append(1)
        return ShareResult(nonce=11, h_micro=1_000_000, d_ratio=1.0)

    async def fake_submit(_job_id: str, _share: ShareResult) -> SubmitOutcome:
        return SubmitOutcome(True, True, None, False)

    async def fake_to_thread(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(miner_mod, "_hash_candidate_header", None)
    monkeypatch.setattr(miner_mod, "_header_from_template_view", None)
    monkeypatch.setattr(miner, "_scan_parallel", fake_scan)
    monkeypatch.setattr(miner, "_submit_share", fake_submit)
    monkeypatch.setattr(asyncio, "to_thread", fake_to_thread)

    try:
        await asyncio.wait_for(
            miner._mine_job(
                0,
                {
                    "jobId": "job-signbytes-only",
                    "header": {
                        "thetaMicro": 1_000_000,
                        "signBytes": "0x1234",
                    },
                    "shareTarget": 1.0,
                },
            ),
            timeout=0.5,
        )
    finally:
        miner._scan_executor.shutdown(wait=False, cancel_futures=True)

    assert len(scans) == 1
