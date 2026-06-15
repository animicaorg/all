"""Tests for the one-command supervisor (animica.unified) + `animica up --plan`.

Pure plan logic — launches nothing. Verifies capability tiers select the right
components and that every component binds to the single ANM payout address.
"""

from __future__ import annotations

import json

from animica import unified as u


def _cfg(**kw):
    base = dict(address="anim1payme", pool_id="enapool-abc")
    base.update(kw)
    return u.UnifiedConfig(**base)


def _names(plan, *, enabled=True, available=None):
    out = []
    for c in plan:
        if c.enabled != enabled:
            continue
        if available is not None and c.available != available:
            continue
        out.append(c.name)
    return set(out)


def test_cpu_only_tier_runs_miner_and_useful_work():
    caps = u.Capabilities(cpu_count=8)  # no GPU
    plan = u.build_plan(caps, _cfg())
    runnable = _names(plan, enabled=True, available=True)
    assert runnable == {"miner", "useful-work"}
    # trainer/server/bittensor are disabled with a "no GPU" reason
    disabled = {c.name: c.reason for c in plan if not c.enabled}
    assert "trainer" in disabled and "no GPU" in disabled["trainer"]
    assert "bittensor" in disabled and "no GPU" in disabled["bittensor"]


def test_gpu_tier_enables_trainer_and_server_but_not_bittensor_below_threshold():
    caps = u.Capabilities(cpu_count=16, gpu=True, gpu_name="RTX 4070", vram_gb=12.0, cuda=True)
    plan = u.build_plan(caps, _cfg())
    enabled = _names(plan, enabled=True)
    assert {"miner", "useful-work", "trainer", "server"} <= enabled
    assert "bittensor" not in enabled  # 12 GB < 16 GB qualification


def test_qualified_gpu_enables_bittensor():
    caps = u.Capabilities(cpu_count=32, gpu=True, gpu_name="A100", vram_gb=80.0, cuda=True)
    assert caps.qualified_bittensor is True
    plan = u.build_plan(caps, _cfg())
    enabled = _names(plan, enabled=True)
    assert {"miner", "useful-work", "trainer", "server", "bittensor"} <= enabled


def test_gpu_without_pool_id_disables_train_and_serve():
    caps = u.Capabilities(cpu_count=8, gpu=True, vram_gb=24.0, cuda=True)
    plan = u.build_plan(caps, _cfg(pool_id=None))
    disabled = _names(plan, enabled=False)
    assert "trainer" in disabled and "server" in disabled  # need --pool-id


def test_node_is_opt_in():
    caps = u.Capabilities(cpu_count=4)
    assert "node" in _names(u.build_plan(caps, _cfg(run_node=True)), enabled=True)
    assert "node" in _names(u.build_plan(caps, _cfg(run_node=False)), enabled=False)


def test_everything_binds_to_one_anm_address():
    caps = u.Capabilities(cpu_count=16, gpu=True, vram_gb=80.0, cuda=True)
    cfg = _cfg(address="anim1onlyme")
    for c in u.build_plan(caps, cfg):
        # the address appears in argv and/or the payout env on every component
        blob = " ".join(c.argv) + " " + json.dumps(c.env)
        assert "anim1onlyme" in blob, f"{c.name} not bound to the ANM address"
    # the version the miner advertises matches the pool's required minimum
    miner = next(c for c in u.build_plan(caps, cfg) if c.name == "miner")
    assert miner.env["ANIMICA_MINER_VERSION"] == u.UNIFIED_VERSION


def test_detect_capabilities_never_raises_and_reports_cpu(monkeypatch):
    monkeypatch.setattr(u, "_detect_gpu_via_torch", lambda: None)
    monkeypatch.setattr(u, "_detect_gpu_via_smi", lambda: None)
    caps = u.detect_capabilities()
    assert caps.cpu_count >= 1 and caps.gpu is False


def test_up_plan_cli_runs_nothing(monkeypatch):
    from typer.testing import CliRunner
    from animica.cli.main import app
    monkeypatch.setattr(u, "_detect_gpu_via_torch", lambda: None)
    monkeypatch.setattr(u, "_detect_gpu_via_smi", lambda: None)
    runner = CliRunner()
    res = runner.invoke(app, ["up", "--address", "anim1payme", "--plan", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["address"] == "anim1payme"
    assert data["will_run"] == ["miner", "useful-work"]
    assert data["version"] == u.UNIFIED_VERSION


def test_resolve_address_prefers_flag_then_env(monkeypatch):
    assert u.resolve_address("anim1explicit", create=False) == ("anim1explicit", "flag")
    monkeypatch.delenv("ANIMICA_PAYOUT_ADDRESS", raising=False)
    monkeypatch.setenv("ANIMICA_MINER_ADDRESS", "anim1fromenv")
    monkeypatch.setattr(u, "_read_default_address", lambda: None)
    assert u.resolve_address(None, create=False) == ("anim1fromenv", "env:ANIMICA_MINER_ADDRESS")


def test_resolve_address_reads_default_wallet(monkeypatch):
    monkeypatch.delenv("ANIMICA_PAYOUT_ADDRESS", raising=False)
    monkeypatch.delenv("ANIMICA_MINER_ADDRESS", raising=False)
    monkeypatch.setattr(u, "_read_default_address", lambda: "anim1wallet")
    assert u.resolve_address(None, create=False) == ("anim1wallet", "wallet")


def test_resolve_address_auto_creates_when_missing(monkeypatch):
    monkeypatch.delenv("ANIMICA_PAYOUT_ADDRESS", raising=False)
    monkeypatch.delenv("ANIMICA_MINER_ADDRESS", raising=False)
    monkeypatch.setattr(u, "_read_default_address", lambda: None)
    monkeypatch.setattr(u, "_create_wallet", lambda: "anim1created")
    assert u.resolve_address(None, create=True) == ("anim1created", "created")
    # create=False with nothing available → empty, "none" (no side effects)
    assert u.resolve_address(None, create=False) == ("", "none")


def test_read_default_address_parses_wallets_json(tmp_path, monkeypatch):
    import json as _j
    wf = tmp_path / "wallets.json"
    wf.write_text(_j.dumps({"default_address": "anim1default",
                            "wallets": [{"label": "a", "address": "anim1a"}]}), encoding="utf-8")
    monkeypatch.setenv("ANIMICA_WALLETS_FILE", str(wf))
    assert u._read_default_address() == "anim1default"
    # falls back to first wallet when no explicit default
    wf.write_text(_j.dumps({"wallets": [{"label": "a", "address": "anim1a"}]}), encoding="utf-8")
    assert u._read_default_address() == "anim1a"


def test_bittensor_component_real_command_and_anm_binding():
    caps = u.Capabilities(cpu_count=32, gpu=True, gpu_name="A100", vram_gb=80.0, cuda=True)
    # qualified but no token → enabled, not yet runnable, with an enroll hint
    plan = u.build_plan(caps, _cfg(bittensor_token=None))
    bt = next(c for c in plan if c.name == "bittensor")
    assert bt.enabled and not bt.available and "ANIMICA_WORKER_TOKEN" in bt.reason
    assert bt.argv[-3:] == ["bittensor", "up", "--run"]   # the REAL shipped command
    # qualified + token → runnable, token passed via env, still ANM-bound
    plan2 = u.build_plan(caps, _cfg(bittensor_token="anm_worker_xyz"))
    bt2 = next(c for c in plan2 if c.name == "bittensor")
    assert bt2.available is True
    assert bt2.env["ANIMICA_WORKER_TOKEN"] == "anm_worker_xyz"
    assert bt2.env["ANIMICA_PAYOUT_ADDRESS"] == "anim1payme"   # ANM-bound


def test_bittensor_off_without_gpu():
    caps = u.Capabilities(cpu_count=8)
    bt = next(c for c in u.build_plan(caps, _cfg(bittensor_token="t")) if c.name == "bittensor")
    assert not bt.enabled and "no GPU" in bt.reason
