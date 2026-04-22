from __future__ import annotations

import uuid

from animica_studio.models.profile_models import ProfileType, RpcProfile
from animica_studio.models.studio_models import NodeSummary
from animica_studio.services.job_runner import ResolvedCli
from animica_studio.services.profile_service import ProfileService
from animica_studio.services.studio_status_service import ServiceActionResult
from animica_studio.storage.config import Config
from animica_studio.ui.main_window import _startup_runtime_bootstrap


def _profile(profile_type: ProfileType) -> RpcProfile:
    if profile_type == ProfileType.LOCAL_NODE:
        profile = RpcProfile.make_default_local(name="Local Startup")
    else:
        profile = RpcProfile.make_default_remote(name="Remote Startup")
    profile.id = str(uuid.uuid4())
    return profile


class _FakeStatusService:
    def __init__(
        self,
        *,
        rpc_before: bool,
        rpc_after: bool = True,
        start_ok: bool = True,
    ) -> None:
        self._rpc_before = rpc_before
        self._rpc_after = rpc_after
        self._start_ok = start_ok
        self.start_calls = 0

    def collect_node_summary(self) -> NodeSummary:
        if self.start_calls == 0:
            return NodeSummary(rpc_reachable=self._rpc_before, running=self._rpc_before)
        return NodeSummary(rpc_reachable=self._rpc_after, running=self._rpc_after)

    def start_node(self) -> ServiceActionResult:
        self.start_calls += 1
        if self._start_ok:
            return ServiceActionResult(True, "Node start requested.")
        return ServiceActionResult(False, "Node did not start.", "failed")


def test_startup_bootstrap_starts_local_node_when_rpc_is_down(monkeypatch) -> None:
    profile = _profile(ProfileType.LOCAL_NODE)
    cfg = Config(rpc_profiles=[profile.to_dict()], active_profile_id=profile.id)
    profile_service = ProfileService(cfg)
    status_service = _FakeStatusService(rpc_before=False, rpc_after=True, start_ok=True)

    monkeypatch.setattr(
        "animica_studio.services.job_runner.resolve_animica_cli",
        lambda _cfg: ResolvedCli(argv_prefix=["/tmp/animica"], env={}),
    )
    monkeypatch.setattr("animica_studio.services.cli_capabilities.refresh_cli_registry", lambda _cfg: None)

    result = _startup_runtime_bootstrap(cfg, profile_service, status_service)

    assert result["profile_type"] == ProfileType.LOCAL_NODE.value
    assert result["cli_ok"] is True
    assert result["node_start_attempted"] is True
    assert result["node_start_ok"] is True
    assert result["node_rpc_after"] is True
    assert status_service.start_calls == 1


def test_startup_bootstrap_skips_local_start_when_cli_is_missing(monkeypatch) -> None:
    profile = _profile(ProfileType.LOCAL_NODE)
    cfg = Config(rpc_profiles=[profile.to_dict()], active_profile_id=profile.id)
    profile_service = ProfileService(cfg)
    status_service = _FakeStatusService(rpc_before=False, rpc_after=False, start_ok=False)

    monkeypatch.setattr(
        "animica_studio.services.job_runner.resolve_animica_cli",
        lambda _cfg: ResolvedCli(argv_prefix=[], env={}, error="missing"),
    )
    monkeypatch.setattr("animica_studio.services.cli_capabilities.refresh_cli_registry", lambda _cfg: None)

    result = _startup_runtime_bootstrap(cfg, profile_service, status_service)

    assert result["profile_type"] == ProfileType.LOCAL_NODE.value
    assert result["cli_ok"] is False
    assert result["node_start_attempted"] is False
    assert result["node_start_skipped"] == "cli_unavailable"
    assert status_service.start_calls == 0


def test_startup_bootstrap_does_not_start_remote_profiles(monkeypatch) -> None:
    profile = _profile(ProfileType.REMOTE_RPC)
    cfg = Config(rpc_profiles=[profile.to_dict()], active_profile_id=profile.id)
    profile_service = ProfileService(cfg)
    status_service = _FakeStatusService(rpc_before=False, rpc_after=False, start_ok=False)

    monkeypatch.setattr(
        "animica_studio.services.job_runner.resolve_animica_cli",
        lambda _cfg: ResolvedCli(argv_prefix=["/tmp/animica"], env={}),
    )
    monkeypatch.setattr("animica_studio.services.cli_capabilities.refresh_cli_registry", lambda _cfg: None)

    result = _startup_runtime_bootstrap(cfg, profile_service, status_service)

    assert result["profile_type"] == ProfileType.REMOTE_RPC.value
    assert result["node_start_attempted"] is False
    assert status_service.start_calls == 0
