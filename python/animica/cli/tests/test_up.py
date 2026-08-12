"""Tests for `animica up` component selection (5.2.0) — additive flags that must
not change default behavior. The pure selection helpers are unit-tested; `--plan`
is exercised end-to-end (it launches nothing, so it's safe offline)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from animica.cli import up as up_mod
from animica.cli.main import app as root_app

runner = CliRunner()


def _fake_plan():
    # Mirrors unified.build_plan output on a CPU box (no GPU).
    return [
        SimpleNamespace(name="node", enabled=False, available=True, reason="off"),
        SimpleNamespace(name="miner", enabled=True, available=True, reason="pow"),
        SimpleNamespace(name="useful-work", enabled=True, available=True, reason="cpu"),
        SimpleNamespace(name="studio", enabled=True, available=True, reason="studio"),
        SimpleNamespace(name="trainer", enabled=False, available=False, reason="no GPU"),
        SimpleNamespace(name="server", enabled=False, available=False, reason="no GPU"),
        SimpleNamespace(name="bittensor", enabled=False, available=False, reason="no GPU"),
    ]


def _enabled(plan):
    return {c.name for c in plan if c.enabled}


def test_resolve_names_aliases():
    assert up_mod._resolve_names(["mine", "ai", "bt"]) == {"miner", "useful-work", "bittensor"}
    assert up_mod._resolve_names(["serve", "train"]) == {"server", "trainer"}


def test_resolve_names_unknown_raises():
    with pytest.raises(typer.BadParameter):
        up_mod._resolve_names(["frobnicate"])


def test_profile_miner_disables_ai_components():
    plan = _fake_plan()
    up_mod._apply_selection(plan, "miner", [], [])
    assert _enabled(plan) == {"miner"}  # node was already off; useful-work/studio disabled


def test_profile_ai_keeps_ai_disables_miner():
    plan = _fake_plan()
    up_mod._apply_selection(plan, "ai", [], [])
    assert "miner" not in _enabled(plan)
    assert {"useful-work", "studio"} <= _enabled(plan)


def test_only_restricts_to_named():
    plan = _fake_plan()
    up_mod._apply_selection(plan, "all", ["studio"], [])
    assert _enabled(plan) == {"studio"}


def test_without_disables_named():
    plan = _fake_plan()
    up_mod._apply_selection(plan, "all", [], ["studio", "ai"])
    assert _enabled(plan) == {"miner"}


def test_unknown_profile_raises():
    with pytest.raises(typer.BadParameter):
        up_mod._apply_selection(_fake_plan(), "bogus", [], [])


def test_up_plan_default_backward_compatible():
    # --plan launches nothing; default behavior must list the usual components.
    res = runner.invoke(root_app, ["up", "--plan", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert "will_run" in data and "components" in data
    assert "selection" not in data  # no selection flags => no selection note
    names = {c["name"] for c in data["components"]}
    assert {"miner", "useful-work", "studio", "bittensor"} <= names


def test_up_plan_profile_filters_will_run():
    res = runner.invoke(root_app, ["up", "--plan", "--profile", "miner", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert set(data["will_run"]) <= {"node", "miner"}
    assert data.get("selection") == ["profile=miner"]


def test_up_plan_rejects_unknown_component():
    res = runner.invoke(root_app, ["up", "--plan", "--only", "frobnicate"])
    assert res.exit_code != 0
    assert "unknown component" in res.output.lower()
