"""Tests for deployment record persistence."""

from __future__ import annotations

import json
from pathlib import Path

from animica_miner_gui.backend.rpc_client import RPCClient
from animica_miner_gui.ide.deploy_manager import DeploymentManager, DeploymentOptions
from animica_miner_gui.ide.toolchain.builder import BuildArtifacts, BuildResult


def _build_result(tmp_path: Path) -> BuildResult:
    build_dir = tmp_path / "build"
    build_dir.mkdir()
    artifacts = BuildArtifacts(
        build_dir=build_dir,
        manifest_path=build_dir / "manifest.json",
        abi_path=build_dir / "abi.json",
        contract_path=build_dir / "contract.bin",
        sources_path=build_dir / "sources.json",
        code_hash="deadbeef",
    )
    return BuildResult(
        success=True,
        message="ok",
        artifacts=artifacts,
        diagnostics=[],
        manifest={"name": "Counter", "version": "0.1.0"},
    )


def test_deployment_record_persists(tmp_path: Path) -> None:
    manager = DeploymentManager(
        RPCClient("http://localhost:0"),
        workspace=tmp_path,
        wallet_path=tmp_path / "wallets.json",
    )
    options = DeploymentOptions(
        from_address="addr1",
        network="devnet",
        gas_limit=None,
        max_fee=1,
        explorer_url=None,
    )

    result = _build_result(tmp_path)
    manager._persist_deployment(
        options=options,
        tx_hash="0xabc",
        receipt={"status": "ok"},
        contract_address="contract1",
        block_height=123,
        build_result=result,
    )
    manager._persist_deployment(
        options=options,
        tx_hash="0xdef",
        receipt=None,
        contract_address=None,
        block_height=None,
        build_result=result,
    )

    payload = json.loads((tmp_path / ".animica_deployments.json").read_text(encoding="utf-8"))
    deployments = payload.get("deployments")
    assert isinstance(deployments, list)
    assert len(deployments) == 2
    assert deployments[0]["txHash"] == "0xabc"
    assert deployments[1]["txHash"] == "0xdef"
