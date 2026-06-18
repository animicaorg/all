"""Dynamic tool registry tests — governance-first, GPU-free.

Verifies: proposals are never auto-active; the AST scan flags dangerous code;
approval is impossible without a configured admin token; approved tools become
listable + teachable; execution is off by default, sandboxed when enabled, and
only runs approved tools.
"""

from __future__ import annotations

import pytest

from animica.ena import ENA
from animica.ena.config import load_config
from animica.ena.tools import scan_code, ADMIN_TOKEN_ENV, EXEC_ENV_FLAG

SAFE = "def run(args):\n    return args.get('a', 0) + args.get('b', 0)\n"
DANGEROUS = "import os\ndef run(args):\n    return os.popen('id').read()\n"


def _ena(home, monkeypatch) -> ENA:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(home))
    return ENA(cfg=load_config())


def test_scan_flags_dangerous_code():
    assert scan_code(SAFE)["flagged"] is False
    rep = scan_code(DANGEROUS)
    assert rep["flagged"] is True and any("os" in r for r in rep["reasons"])


def test_propose_is_not_active(tmp_path, monkeypatch):
    e = _ena(tmp_path / "e", monkeypatch)
    rec = e.tools.propose("adder", "add two numbers", {"a": "int", "b": "int"}, SAFE,
                          proposer="worker-1")
    assert rec["status"] == "proposed"
    assert e.tools.approved_tools() == []          # nothing active
    assert e.tools.get("adder")["status"] == "proposed"


def test_name_validation_and_reserved(tmp_path, monkeypatch):
    e = _ena(tmp_path / "e", monkeypatch)
    with pytest.raises(ValueError):
        e.tools.propose("Bad Name!", "x", {}, SAFE)
    with pytest.raises(ValueError):
        e.tools.propose("read_file", "shadow a builtin", {}, SAFE)  # reserved


def test_approval_requires_configured_token(tmp_path, monkeypatch):
    e = _ena(tmp_path / "e", monkeypatch)
    e.tools.propose("adder", "x", {}, SAFE)
    monkeypatch.delenv(ADMIN_TOKEN_ENV, raising=False)
    with pytest.raises(PermissionError):           # no admin token configured
        e.tools.approve("adder", approver="op", admin_token="whatever")
    monkeypatch.setenv(ADMIN_TOKEN_ENV, "s3cret")
    with pytest.raises(PermissionError):           # wrong token
        e.tools.approve("adder", approver="op", admin_token="nope")
    rec = e.tools.approve("adder", approver="op", admin_token="s3cret")
    assert rec["status"] == "approved"
    assert [t["name"] for t in e.tools.approved_tools()] == ["adder"]


def test_execution_off_by_default(tmp_path, monkeypatch):
    e = _ena(tmp_path / "e", monkeypatch)
    e.tools.propose("adder", "x", {}, SAFE)
    monkeypatch.setenv(ADMIN_TOKEN_ENV, "t")
    e.tools.approve("adder", approver="op", admin_token="t")
    monkeypatch.delenv(EXEC_ENV_FLAG, raising=False)
    res = e.tools.execute("adder", {"a": 2, "b": 3})
    assert res["ok"] is False and "disabled" in res["error"]


def test_execute_approved_tool_in_sandbox(tmp_path, monkeypatch):
    e = _ena(tmp_path / "e", monkeypatch)
    e.tools.propose("adder", "x", {}, SAFE)
    monkeypatch.setenv(ADMIN_TOKEN_ENV, "t")
    e.tools.approve("adder", approver="op", admin_token="t")
    monkeypatch.setenv(EXEC_ENV_FLAG, "1")
    res = e.tools.execute("adder", {"a": 2, "b": 3})
    assert res["ok"] is True and res["result"] == "5"
    # an unapproved tool never runs even with execution enabled
    e.tools.propose("other", "x", {}, SAFE)
    assert e.tools.execute("other", {"a": 1})["ok"] is False


def test_approved_dynamic_tool_becomes_teachable(tmp_path, monkeypatch):
    e = _ena(tmp_path / "e", monkeypatch)
    e.tools.propose("adder", "Add two numbers", {"a": "int", "b": "int"}, SAFE)
    monkeypatch.setenv(ADMIN_TOKEN_ENV, "t")
    e.tools.approve("adder", approver="op", admin_token="t")
    specs = e.curriculum._resolve_tools({"teach_tools": True})
    assert any(s["name"] == "adder" for s in specs)
    rows = e.curriculum._generate_tool_rows(
        [s for s in specs if s["name"] == "adder"], 1)
    from animica.ena.curriculum import _parse_tool_call
    assert _parse_tool_call(rows[0]["response"])[0] == "adder"
