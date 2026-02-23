from __future__ import annotations

from pathlib import Path

from animica_studio.services.artifact_service import ArtifactService
from animica_studio.services.ena_automation_service import EnaService
from animica_studio.services.ena_store import EnaStore
from animica_studio.services.fee_routing_service import FeeRoutingService
from animica_studio.services.step_runner import StepRunner
from animica_studio.storage.config import Config


def _mk_store(tmp_path: Path) -> EnaStore:
    return EnaStore(tmp_path / "ena_store.json")


def test_manifest_hashing_stability(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    svc = ArtifactService()
    m1 = svc.build_manifest([f], {"x": 1})
    m2 = svc.build_manifest([f], {"x": 1})
    assert m1["manifest_sha256"] == m2["manifest_sha256"]


def test_verification_failure_messaging(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    svc = ArtifactService()
    m = svc.build_manifest([f], {})
    f.write_text("mutated", encoding="utf-8")
    ok, msg = svc.verify_manifest(m, tmp_path)
    assert not ok
    assert "mismatch" in msg


def test_step_runner_resume(tmp_path: Path) -> None:
    runner = StepRunner(_mk_store(tmp_path))
    run = runner.create_or_resume("flow", ["a", "b"]) 
    resumed = runner.create_or_resume("flow", ["a", "b"], run.run_id)
    assert resumed.run_id == run.run_id


def test_da_stub_publish_local_only(tmp_path: Path) -> None:
    cfg = Config()
    svc = EnaService(cfg, _mk_store(tmp_path))
    svc.store.set("checkpoints", [{"id": "x", "sha256": "abc123"}])
    out = svc.publish_checkpoint("abc123", dev_mode=True)
    assert out["ok"]
    assert out["run"].result["Push to DA"]["mode"] == "local-only"


def test_aicf_submit_stubbed_on_failure(tmp_path: Path, monkeypatch) -> None:
    cfg = Config()
    svc = EnaService(cfg, _mk_store(tmp_path))

    def _bad(*_a, **_k):
        return {"ok": False, "error": "offline"}

    monkeypatch.setattr(svc.aicf, "submit_job", _bad)
    out = svc.run_contribute_flow(tmp_path)
    assert out["receipt"]["job_id"] == "local-dev-job"


def test_inference_toggle_local_and_network(tmp_path: Path) -> None:
    svc = EnaService(Config(), _mk_store(tmp_path))
    local = svc.infer("hi", network_mode=False)
    net = svc.infer("hi", network_mode=True)
    assert local["mode"] == "local"
    assert net["mode"] == "network"


def test_auto_mode_fetches_checkpoint(tmp_path: Path) -> None:
    svc = EnaService(Config(), _mk_store(tmp_path))
    out = svc.run_auto_mode(tmp_path)
    assert out["active_checkpoint"] is not None


def test_duplicate_publish_prevented(tmp_path: Path) -> None:
    svc = EnaService(Config(), _mk_store(tmp_path))
    svc.store.set("checkpoints", [{"id": "x", "sha256": "dup", "commitment": "c1"}])
    out = svc.publish_checkpoint("dup")
    assert not out["ok"]


def test_one_command_export(tmp_path: Path) -> None:
    svc = EnaService(Config(), _mk_store(tmp_path))
    cmd = svc.export_one_command("infer", {"network": True, "prompt": "hello"})
    assert "animica ena infer --network" in cmd


def test_fee_routing_validation() -> None:
    fee = FeeRoutingService()
    ok, _ = fee.validate_credit_increment(1, 3)
    assert ok


def test_publish_remote_put_blocked_returns_structured_step_error(tmp_path: Path, monkeypatch) -> None:
    svc = EnaService(Config(), _mk_store(tmp_path))
    svc.store.set("checkpoints", [{"id": "x", "sha256": "abc123"}])

    monkeypatch.setattr(
        svc.da_status,
        "get_status",
        lambda *_a, **_k: {
            "enabled": True,
            "allow_remote_put": False,
            "configured_dir": "/data/da",
            "rpc_url": "http://127.0.0.1:8545/rpc",
            "raw": {"version": "1.0.0"},
            "configure_param_spec": [{"name": "allow_remote_put", "required": False}],
        },
    )

    out = svc.publish_checkpoint("abc123", dev_mode=False)
    assert out["ok"] is True
    run = out["run"]
    assert run.status == "failed"
    failed = next(s for s in run.steps if s.name == "Push to DA")
    assert failed.error_details is not None
    assert failed.error_details["error_code"] == "DA_REMOTE_PUT_BLOCKED"
    assert any(a["id"] == "enable_remote_put" for a in failed.error_details["actions"])


def test_publish_remote_put_enabled_uploads_via_da_put(tmp_path: Path, monkeypatch) -> None:
    svc = EnaService(Config(), _mk_store(tmp_path))
    svc.store.set("checkpoints", [{"id": "x", "sha256": "feedbeef"}])

    monkeypatch.setattr(
        svc.da_status,
        "get_status",
        lambda *_a, **_k: {
            "enabled": True,
            "allow_remote_put": True,
            "configured_dir": "/data/da",
            "rpc_url": "http://127.0.0.1:8545/rpc",
            "raw": {"version": "1.0.0"},
            "configure_param_spec": [{"name": "allow_remote_put", "required": False}],
        },
    )
    monkeypatch.setattr(svc.da, "upload_bytes", lambda *_a, **_k: {"blob_id": "blob-123"})

    out = svc.publish_checkpoint("feedbeef", dev_mode=False)
    assert out["run"].status == "completed"
    assert out["run"].result["Push to DA"]["commitment"] == "blob-123"
