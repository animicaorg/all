"""Tests for the backend unified runner (no Qt required).

These exercise the BACKEND-ONLY ``unified_runner`` module: it must import and
run without PySide6, and ``plan_preview`` must return the expected unified
"mine + AI" components for a given capability tier.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from animica.unified import Capabilities

from animica_miner_gui.backend import unified_runner

_PKG_ROOT = Path(__file__).resolve().parents[1]


def test_imports_without_pyside6():
    """The unified runner must import in a clean interpreter without pulling Qt.

    This is checked in a fresh subprocess because pytest-qt loads PySide6 into
    the test process at session start, which would otherwise mask a leak.
    """
    code = (
        "import sys; "
        "import animica_miner_gui.backend.unified_runner as u; "
        "assert 'PySide6' not in sys.modules, 'unified_runner imported PySide6'; "
        "print('ok')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(_PKG_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok" in result.stdout


def test_plan_preview_cpu_only():
    """CPU-only box => PoW+AICF miner plus ENA useful-work; nothing GPU-only."""
    caps = Capabilities(cpu_count=4)  # CPU-only: no gpu, vram=0
    summary = unified_runner.plan_preview({"address": "anim1cpuonly"}, caps=caps)

    assert summary["will_run"] == ["miner", "useful-work"]
    # GPU-only components must not be scheduled to run on a CPU box.
    assert "trainer" not in summary["will_run"]
    assert "server" not in summary["will_run"]
    assert "bittensor" not in summary["will_run"]
    assert summary["address"] == "anim1cpuonly"
    assert summary["version"] == unified_runner.UNIFIED_VERSION


def test_plan_preview_monkeypatched_detect_cpu(monkeypatch):
    """When detect_capabilities is monkeypatched to CPU, preview is CPU-only."""
    monkeypatch.setattr(
        unified_runner, "detect_capabilities",
        lambda: Capabilities(cpu_count=8),
    )
    # No explicit caps => uses (patched) detect_capabilities under the hood.
    summary = unified_runner.plan_preview({"address": "anim1detected"})
    assert summary["will_run"] == ["miner", "useful-work"]
    assert summary["capabilities"]["gpu"] is False
    assert summary["capabilities"]["cpu_count"] == 8


def test_plan_preview_gpu_qualified():
    """A qualified GPU box (>=16GB) with a pool id schedules the full AI stack."""
    caps = Capabilities(cpu_count=16, gpu=True, gpu_name="TestGPU",
                        vram_gb=24.0, cuda=True)
    summary = unified_runner.plan_preview(
        {"address": "anim1gpu", "pool_id": "global-1"}, caps=caps)
    will_run = summary["will_run"]
    assert "miner" in will_run
    assert "useful-work" in will_run
    assert "trainer" in will_run
    assert "server" in will_run
    # Bittensor is enabled for qualified GPUs but not yet available (Phase 7),
    # so it shows up as pending rather than running.
    assert "bittensor" in summary["enabled_but_pending"]


def test_make_unified_config_defaults():
    """Plain dict -> UnifiedConfig with sensible pool defaults."""
    ucfg = unified_runner.make_unified_config({"address": "anim1x"})
    assert ucfg.address == "anim1x"
    assert ucfg.pool_host == "pool.animica.org"
    assert ucfg.pool_port == 3333
    assert ucfg.threads == 0  # 0 => miner default
    # worker_id falls back to the address.
    assert ucfg.wid() == "anim1x"


def test_make_unified_config_overrides():
    """Recognised override keys flow through to UnifiedConfig."""
    ucfg = unified_runner.make_unified_config({
        "address": "anim1y",
        "pool_host": "pool.example.org",
        "pool_port": "9999",          # string coerces to int
        "pool_id": "my-pool",
        "worker_id": "rig-1",
        "threads": "6",
        "run_node": True,
    })
    assert ucfg.pool_host == "pool.example.org"
    assert ucfg.pool_port == 9999
    assert ucfg.pool_id == "my-pool"
    assert ucfg.worker_id == "rig-1"
    assert ucfg.threads == 6
    assert ucfg.run_node is True
    assert ucfg.wid() == "rig-1"


def test_runner_status_before_start():
    """A fresh runner is stopped and not running."""
    runner = unified_runner.UnifiedRunner()
    assert not runner.is_running()
    snap = runner.status_dict()
    assert snap["status"] == "stopped"
    assert snap["will_run"] == []
