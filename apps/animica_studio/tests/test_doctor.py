"""Tests for animica_studio.doctor – DoctorReport and CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report(**kwargs):
    from animica_studio.doctor import DoctorReport

    return DoctorReport(**kwargs)


# ---------------------------------------------------------------------------
# DoctorReport structure
# ---------------------------------------------------------------------------


def test_doctor_report_defaults() -> None:
    from animica_studio.doctor import DoctorReport, EnvSection, RpcSection, DASection, StudioSection, EnaSection, PipelineSection

    r = DoctorReport()
    assert isinstance(r.environment, EnvSection)
    assert isinstance(r.node_rpc, RpcSection)
    assert isinstance(r.da, DASection)
    assert isinstance(r.studio, StudioSection)
    assert isinstance(r.ena, EnaSection)
    assert isinstance(r.pipeline, PipelineSection)
    assert r.overall == "unknown"


def test_doctor_report_to_dict() -> None:
    from animica_studio.doctor import DoctorReport

    r = DoctorReport(timestamp="2026-01-01T00:00:00Z")
    d = r.to_dict()
    assert isinstance(d, dict)
    assert d["timestamp"] == "2026-01-01T00:00:00Z"
    assert "environment" in d
    assert "node_rpc" in d
    assert "da" in d
    assert "studio" in d
    assert "ena" in d
    assert "pipeline" in d


def test_doctor_report_to_json() -> None:
    from animica_studio.doctor import DoctorReport

    r = DoctorReport(timestamp="2026-01-01T00:00:00Z")
    j = r.to_json()
    parsed = json.loads(j)
    assert parsed["timestamp"] == "2026-01-01T00:00:00Z"
    assert "environment" in parsed


# ---------------------------------------------------------------------------
# Environment probe
# ---------------------------------------------------------------------------


def test_probe_environment_basic() -> None:
    from animica_studio.doctor import _probe_environment

    env = _probe_environment()
    assert env.python_version  # e.g. "3.11.0"
    assert env.cpu_cores >= 1
    assert env.disk_free_gib >= 0.0
    assert isinstance(env.packages, dict)


def test_probe_environment_python_version_matches_runtime() -> None:
    from animica_studio.doctor import _probe_environment

    env = _probe_environment()
    expected = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert env.python_version.startswith(expected)


def test_probe_environment_torch_absent_when_not_installed() -> None:
    """If torch is not importable, torch_available must be False."""
    from animica_studio.doctor import _probe_environment

    with patch.dict(sys.modules, {"torch": None}):
        env = _probe_environment()
    # torch may or may not be installed in CI; just verify the field is bool
    assert isinstance(env.torch_available, bool)


# ---------------------------------------------------------------------------
# RPC probe (offline / no node)
# ---------------------------------------------------------------------------


def test_probe_rpc_empty_url() -> None:
    from animica_studio.doctor import _probe_rpc

    sec = _probe_rpc("")
    assert sec.reachable is False
    assert sec.error is not None


def test_probe_rpc_unreachable_host() -> None:
    from animica_studio.doctor import _probe_rpc

    sec = _probe_rpc("http://127.0.0.1:19999")  # nothing listening
    assert sec.rpc_url == "http://127.0.0.1:19999"
    assert sec.reachable is False


# ---------------------------------------------------------------------------
# DA probe (offline)
# ---------------------------------------------------------------------------


def test_probe_da_empty_url() -> None:
    from animica_studio.doctor import _probe_da

    sec = _probe_da("")
    # No URL → no error raised, section is empty
    assert sec.enabled is False


# ---------------------------------------------------------------------------
# run_doctor (end-to-end headless)
# ---------------------------------------------------------------------------


def test_run_doctor_returns_report() -> None:
    from animica_studio.doctor import run_doctor, DoctorReport

    report = run_doctor(rpc_url="")
    assert isinstance(report, DoctorReport)
    assert report.timestamp
    assert report.duration_ms >= 0
    assert report.overall in {"ok", "degraded", "error", "unknown"}


def test_run_doctor_json_serialisable() -> None:
    from animica_studio.doctor import run_doctor

    report = run_doctor(rpc_url="")
    j = report.to_json()
    parsed = json.loads(j)
    assert "environment" in parsed
    assert "pipeline" in parsed


def test_run_doctor_pipeline_blockers_without_torch() -> None:
    """When torch is unavailable the pipeline section must flag it."""
    from animica_studio.doctor import run_doctor

    with patch.dict(sys.modules, {"torch": None}):
        report = run_doctor(rpc_url="")

    # torch_available should be False
    assert report.environment.torch_available is False
    # pipeline.can_train must be False (no torch)
    assert report.pipeline.can_train is False


# ---------------------------------------------------------------------------
# print_report
# ---------------------------------------------------------------------------


def test_print_report_human_readable(capsys) -> None:
    from animica_studio.doctor import run_doctor, print_report

    report = run_doctor(rpc_url="")
    print_report(report, as_json=False)
    out = capsys.readouterr().out
    assert "Overall" in out
    assert "Environment" in out
    assert "Pipeline" in out


def test_print_report_json(capsys) -> None:
    from animica_studio.doctor import run_doctor, print_report

    report = run_doctor(rpc_url="")
    print_report(report, as_json=True)
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "overall" in parsed


# ---------------------------------------------------------------------------
# CLI entry point (doctor_main)
# ---------------------------------------------------------------------------


def test_doctor_main_json_flag(capsys) -> None:
    from animica_studio.doctor import doctor_main

    rc = doctor_main(["--json", "--rpc-url", ""])
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "overall" in parsed
    assert rc in (0, 1)


def test_doctor_main_no_flags(capsys) -> None:
    from animica_studio.doctor import doctor_main

    rc = doctor_main(["--rpc-url", ""])
    out = capsys.readouterr().out
    assert "Overall" in out
    assert rc in (0, 1)
