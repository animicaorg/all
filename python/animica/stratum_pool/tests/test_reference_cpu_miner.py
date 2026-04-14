import sys
import asyncio
import argparse
import json
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

import animica.stratum_pool.reference_cpu_miner as miner_mod
from animica.stratum_pool.reference_cpu_miner import (
    MinerConfig,
    ShareResult,
    StratumCpuMiner,
    SubmitOutcome,
    _is_stale_job_reason,
    _normalize_job_payload,
    _should_stop_job,
    resolve_config,
)


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
