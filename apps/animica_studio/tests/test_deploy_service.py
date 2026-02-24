"""Tests for DeployService — deployment records, validation, and pipeline."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from animica_studio.services.deploy_service import (
    ConstructorArg,
    DeployRecord,
    DeployRequest,
    DeployService,
    NetworkProfile,
    encode_constructor_args,
    generate_constructor_args_from_abi,
)
from animica_studio.services.vm_toolchain_service import VmToolchainService


@pytest.fixture()
def svc(tmp_path: Path) -> DeployService:
    return DeployService(records_dir=tmp_path / "deploys")


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    p = tmp_path / "my_contract"
    p.mkdir()
    (p / "contract.py").write_text("def init(ctx): pass\n")
    (p / "manifest.json").write_text('{"name": "TestContract", "version": "1.0.0"}')
    return p


# ---------------------------------------------------------------------------
# Deployment records CRUD
# ---------------------------------------------------------------------------


def _make_record(record_id: str = "test-id") -> DeployRecord:
    return DeployRecord(
        id=record_id,
        project_path="/tmp/proj",
        artifact_hash="abc123",
        abi_hash="def456",
        network_name="devnet",
        rpc_url="http://127.0.0.1:8545",
        chain_id=1337,
        contract_address="0xDeadBeef",
        tx_hash="0xTxHash",
        block_height=100,
        deployed_at=1700000000.0,
        constructor_args=[],
        signer_address="0xSigner",
        signer_label="test-wallet",
        status="confirmed",
    )


def test_save_and_list_records(svc: DeployService) -> None:
    record = _make_record("r1")
    svc.save_record(record)
    records = svc.list_records()
    assert len(records) == 1
    assert records[0].id == "r1"


def test_list_records_newest_first(svc: DeployService) -> None:
    r1 = _make_record("r1")
    r1.deployed_at  # read-only frozen
    r2 = _make_record("r2")
    object.__setattr__(r2, "deployed_at", r1.deployed_at + 100)  # newer
    svc.save_record(r1)
    svc.save_record(r2)
    records = svc.list_records()
    assert records[0].id == "r2"


def test_save_record_updates_existing(svc: DeployService) -> None:
    record = _make_record("r1")
    svc.save_record(record)
    # Mutate via dict update and re-save
    updated = DeployRecord.from_dict({**record.to_dict(), "status": "failed"})
    svc.save_record(updated)
    records = svc.list_records()
    assert len(records) == 1
    assert records[0].status == "failed"


def test_get_record(svc: DeployService) -> None:
    record = _make_record("r1")
    svc.save_record(record)
    fetched = svc.get_record("r1")
    assert fetched is not None
    assert fetched.contract_address == "0xDeadBeef"


def test_get_record_missing(svc: DeployService) -> None:
    assert svc.get_record("nonexistent") is None


def test_delete_record(svc: DeployService) -> None:
    svc.save_record(_make_record("r1"))
    assert svc.delete_record("r1") is True
    assert svc.list_records() == []


def test_delete_record_missing(svc: DeployService) -> None:
    assert svc.delete_record("nonexistent") is False


def test_update_record_status(svc: DeployService) -> None:
    svc.save_record(_make_record("r1"))
    svc.update_record_status("r1", "confirmed", confirmations=3, block_height=200)
    r = svc.get_record("r1")
    assert r is not None
    assert r.status == "confirmed"
    assert r.confirmations == 3
    assert r.block_height == 200


def test_records_persist_across_instances(tmp_path: Path) -> None:
    records_dir = tmp_path / "deploys"
    svc1 = DeployService(records_dir=records_dir)
    svc1.save_record(_make_record("persist-id"))

    svc2 = DeployService(records_dir=records_dir)
    records = svc2.list_records()
    assert any(r.id == "persist-id" for r in records)


# ---------------------------------------------------------------------------
# DeployRecord serialization
# ---------------------------------------------------------------------------


def test_deploy_record_roundtrip() -> None:
    r = _make_record("round")
    d = r.to_dict()
    r2 = DeployRecord.from_dict(d)
    assert r2.id == r.id
    assert r2.contract_address == r.contract_address
    assert r2.metadata == {}


def test_deploy_record_from_dict_defaults() -> None:
    r = DeployRecord.from_dict({})
    assert r.status == "pending"
    assert r.confirmations == 0
    assert isinstance(r.id, str)


# ---------------------------------------------------------------------------
# Constructor arg helpers
# ---------------------------------------------------------------------------


def test_generate_constructor_args_from_abi() -> None:
    abi = [
        {"type": "constructor", "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "supply", "type": "uint256"},
        ]}
    ]
    args = generate_constructor_args_from_abi(abi)
    assert len(args) == 2
    assert args[0].name == "owner"
    assert args[0].type == "address"
    assert args[1].name == "supply"


def test_generate_constructor_args_no_constructor() -> None:
    abi = [{"type": "function", "name": "foo", "inputs": []}]
    args = generate_constructor_args_from_abi(abi)
    assert args == []


def test_encode_constructor_args() -> None:
    args = [
        ConstructorArg("owner", "address", value="0xabc"),
        ConstructorArg("supply", "uint256", value=1000),
    ]
    encoded = encode_constructor_args(args)
    assert encoded == [
        {"name": "owner", "type": "address", "value": "0xabc"},
        {"name": "supply", "type": "uint256", "value": 1000},
    ]


# ---------------------------------------------------------------------------
# Deploy pipeline — validation failures
# ---------------------------------------------------------------------------


def test_deploy_fails_on_validation_error(svc: DeployService, tmp_path: Path) -> None:
    bad_project = tmp_path / "bad"
    bad_project.mkdir()
    (bad_project / "contract.py").write_text("import random\ndef bad(): pass\n")
    (bad_project / "manifest.json").write_text('{"name": "Bad", "version": "1.0.0"}')

    request = DeployRequest(
        project_path=str(bad_project),
        network=NetworkProfile(name="devnet", rpc_url="", chain_id=1337),
        signer_address="0xSigner",
        dry_run=False,
    )
    result = svc.deploy(request)
    assert not result.success
    assert result.error


def test_deploy_fails_on_no_entry_file(svc: DeployService, tmp_path: Path) -> None:
    empty_project = tmp_path / "empty"
    empty_project.mkdir()
    (empty_project / "manifest.json").write_text('{"name": "Empty", "version": "1.0.0"}')

    request = DeployRequest(
        project_path=str(empty_project),
        network=NetworkProfile(name="devnet", rpc_url="", chain_id=1337),
        signer_address="0xSigner",
        dry_run=False,
    )
    result = svc.deploy(request)
    assert not result.success


def test_deploy_fails_no_rpc_url(svc: DeployService, project: Path) -> None:
    request = DeployRequest(
        project_path=str(project),
        network=NetworkProfile(name="devnet", rpc_url="", chain_id=1337),
        signer_address="0xSigner",
        dry_run=False,
    )
    result = svc.deploy(request)
    # With no RPC URL, TX submission fails
    assert not result.success
    assert "rpc" in result.error.lower() or "url" in result.error.lower()


# ---------------------------------------------------------------------------
# TX polling — timeout / offline behavior
# ---------------------------------------------------------------------------


def test_poll_tx_no_hash(svc: DeployService) -> None:
    r = svc.poll_tx_status("", "http://localhost:8545", timeout_s=1)
    assert r["status"] == "failed"
    assert r["error"]


def test_poll_tx_offline_times_out(svc: DeployService) -> None:
    # Use a non-routable address with very short timeout
    r = svc.poll_tx_status(
        "0xfakehash",
        "http://10.0.0.0:19999",  # non-routable
        timeout_s=0.5,
        poll_interval_s=0.1,
    )
    # Should timeout or fail, not hang
    assert r["status"] in ("timeout", "failed")
