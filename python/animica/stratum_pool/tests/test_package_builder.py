from __future__ import annotations

import sys
import tarfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from animica.stratum_pool.package_builder import MinerBundleBuilder
from animica.stratum_pool.portal import ResolvedMiningConfig, build_bundle_input


def _resolved() -> ResolvedMiningConfig:
    return ResolvedMiningConfig(
        network="mainnet",
        chain_id=1,
        pool_enabled=True,
        bind_host="0.0.0.0",
        bind_port=3333,
        public_host="pool.animica.org",
        public_port=3333,
        public_scheme="stratum+tcp",
        tls_enabled=False,
        host_source="request_host_site_alias",
        api_base_url="https://pool.animica.org",
        profile="hashshare",
        pool_mode="pps",
        algorithm="Animica HashShare",
        device_type="CPU miner",
        fee_percent=None,
        payout_minimum=None,
        payout_interval_seconds=0.0,
        payout_min_amount=1,
        download_base_url="https://pool.animica.org",
        warnings=(),
    )


def test_package_builder_defaults_to_python_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("ANIMICA_MINER_EXECUTABLES_DIR", raising=False)
    builder = MinerBundleBuilder(output_dir=tmp_path, version="1.0.0")
    monkeypatch.setattr(builder, "_resolve_prebuilt_executable", lambda _platform: None)
    artifact = builder.build(_resolved(), "linux", build_bundle_input())

    assert artifact.includes_executable is False
    assert artifact.requires_python is True
    assert artifact.entrypoint == "animica-miner"

    with tarfile.open(artifact.path, "r:gz") as archive:
        names = set(archive.getnames())
    assert "animica_cpu_miner.py" in names
    assert "start_mining.sh" in names
    assert "animica-miner.config.json" in names


def test_package_builder_includes_prebuilt_executable(tmp_path: Path, monkeypatch) -> None:
    binaries_root = tmp_path / "binaries"
    platform_dir = binaries_root / "linux"
    platform_dir.mkdir(parents=True, exist_ok=True)
    executable = platform_dir / "animica-miner"
    executable.write_text("#!/usr/bin/env bash\necho miner\n", encoding="utf-8")
    executable.chmod(0o755)
    monkeypatch.setenv("ANIMICA_MINER_EXECUTABLES_DIR", str(binaries_root))

    builder = MinerBundleBuilder(output_dir=tmp_path, version="1.0.0")
    artifact = builder.build(_resolved(), "linux", build_bundle_input())

    assert artifact.includes_executable is True
    assert artifact.requires_python is False
    assert artifact.entrypoint == "animica-miner"

    with tarfile.open(artifact.path, "r:gz") as archive:
        names = set(archive.getnames())
    assert "animica-miner" in names
