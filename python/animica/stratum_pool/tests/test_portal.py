from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.api import create_app
from animica.stratum_pool.config import PoolConfig
from animica.stratum_pool.portal import (PLACEHOLDER_ADDRESS, build_bundle_input,
                                         resolve_public_mining_config)


RELEVANT_ENV_VARS = [
    "ANIMICA_PUBLIC_STRATUM_URL",
    "ANIMICA_PUBLIC_STRATUM_HOST",
    "ANIMICA_PUBLIC_STRATUM_PORT",
    "ANIMICA_PUBLIC_DOMAIN",
    "ANIMICA_PUBLIC_STRATUM_SCHEME",
    "ANIMICA_PUBLIC_STRATUM_TLS_ENABLED",
    "ANIMICA_STRATUM_TLS_ENABLED",
    "ANIMICA_PUBLIC_POOL_API_URL",
    "ANIMICA_MINING_DOWNLOAD_BASE_URL",
    "ANIMICA_POOL_ENABLED",
    "ANIMICA_POOL_FEE_PERCENT",
    "ANIMICA_POOL_PAYOUT_MINIMUM",
    "ANIMICA_MINING_DOWNLOAD_DIR",
]


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in RELEVANT_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class DummyMetrics:
    def __init__(self, config: PoolConfig) -> None:
        self.config = config

    def pool_summary(self) -> dict[str, object]:
        return {
            "pool_name": "Animica Stratum Pool",
            "network": self.config.network,
            "height": 321,
            "last_block_hash": "0xabc",
            "pool_hashrate": 12.5,
            "hashrate_series": [],
            "hashrate_1m": 12.5,
            "hashrate_15m": 10.0,
            "hashrate_1h": 9.5,
            "num_miners": 3,
            "num_workers": 4,
            "round_duration_seconds": 1,
            "round_shares": 9,
            "round_estimated_reward": "0",
            "uptime_seconds": 120,
            "stratum_endpoint": f"stratum+tcp://{self.config.host}:{self.config.port}",
            "last_update": "2026-04-07T12:00:00+00:00",
            "latest_block": {
                "height": 321,
                "hash": "0xabc",
                "timestamp": "2026-04-07T12:00:00+00:00",
                "found_by_pool": True,
            },
        }

    def health(self) -> dict[str, object]:
        return {"status": "ok", "uptime": 120}


def test_resolve_public_config_prefers_explicit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANIMICA_PUBLIC_STRATUM_HOST", "pool.animica.test")
    monkeypatch.setenv("ANIMICA_PUBLIC_STRATUM_PORT", "4444")
    monkeypatch.setenv("ANIMICA_PUBLIC_STRATUM_SCHEME", "stratum+tls")
    monkeypatch.setenv("ANIMICA_PUBLIC_STRATUM_TLS_ENABLED", "1")
    monkeypatch.setenv("ANIMICA_POOL_FEE_PERCENT", "1.25")
    monkeypatch.setenv("ANIMICA_POOL_PAYOUT_MINIMUM", "10 ANM")

    resolved = resolve_public_mining_config(
        PoolConfig(host="0.0.0.0", port=3333, network="mainnet")
    )

    assert resolved.public_host == "pool.animica.test"
    assert resolved.public_port == 4444
    assert resolved.public_scheme == "stratum+tls"
    assert resolved.tls_enabled is True
    assert resolved.host_source == "public_stratum_host"
    assert resolved.fee_percent == pytest.approx(1.25)
    assert resolved.payout_minimum == "10 ANM"


@pytest.mark.asyncio
async def test_api_mining_endpoints_reflect_request_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ANIMICA_MINING_DOWNLOAD_DIR", str(tmp_path))
    cfg = PoolConfig(host="0.0.0.0", port=3333, api_host="0.0.0.0", api_port=8550, network="devnet")
    app = create_app(DummyMetrics(cfg))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://mine.animica.test") as client:
        config_res = await client.get("/api/mining/config")
        assert config_res.status_code == 200
        config_payload = config_res.json()
        assert config_payload["stratum_host"] == "mine.animica.test"
        assert config_payload["stratum_port"] == 3333
        assert config_payload["host_source"] == "request_host"
        assert config_payload["status"]["network"] == "devnet"
        assert any("Devnet" in warning for warning in config_payload["warnings"])

        manifest_res = await client.get("/api/mining/downloads")
        assert manifest_res.status_code == 200
        manifest = manifest_res.json()
        assert len(manifest["items"]) == 3
        assert {item["platform"] for item in manifest["items"]} == {"windows", "macos", "linux"}
        assert all(item["url"].startswith("https://mine.animica.test/api/mining/downloads/") for item in manifest["items"])
        assert all(item["sha256"] for item in manifest["items"])

        generated_res = await client.get(
            "/api/mining/generate",
            params={"address": "anim1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq", "worker": "office rig", "threads": 6},
        )
        assert generated_res.status_code == 200
        generated = generated_res.json()
        assert generated["commands"]["windows"].startswith("py -3 animica_cpu_miner.py --host mine.animica.test")
        assert generated["worker"] == "office-rig"
        assert generated["threads"] == 6
        assert generated["config"]["content"].count("mine.animica.test") == 1
        assert generated["download_urls"]["linux"].startswith(
            "https://mine.animica.test/api/mining/downloads/linux?"
        )

        download_res = await client.get("/api/mining/downloads/windows")
        assert download_res.status_code == 200
        assert download_res.headers["content-type"] == "application/zip"
        assert len(download_res.content) > 100


def test_build_bundle_input_uses_placeholder_defaults() -> None:
    bundle = build_bundle_input()
    assert bundle.address == PLACEHOLDER_ADDRESS
    assert bundle.worker
    assert bundle.threads >= 1
