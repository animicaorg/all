"""Tests for the ENA-native coding agent (animica.ena.coder).

Injects a scripted fake model adapter so the tool/repo loop is exercised without
a live model — verifying tool execution, the workdir sandbox, JSON tolerance, and
the done path.
"""

from __future__ import annotations

import json

from animica.ena.coder import CoderAgent


class _ScriptedModel:
    """Returns the next scripted reply on each generate() call."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.i = 0

    def generate(self, prompt, *, system=None, max_tokens=1024, temperature=0.0):
        r = self.replies[min(self.i, len(self.replies) - 1)]
        self.i += 1
        return r


def _act(tool, **args):
    return json.dumps({"thought": "t", "tool": tool, "args": args})


def test_coder_writes_file_reads_then_done(tmp_path):
    agent = CoderAgent(_ScriptedModel([
        _act("write_file", path="hello.py", content="print('hi')\n"),
        _act("read_file", path="hello.py"),
        _act("done", summary="wrote hello.py"),
    ]), workdir=tmp_path)
    res = agent.run_task("create hello.py")
    assert res["status"] == "done" and "wrote" in res["summary"]
    assert (tmp_path / "hello.py").read_text().startswith("print")
    assert res["steps"] == 3
    assert res["transcript"][1]["observation"].startswith("print")  # read_file


def test_coder_sandbox_blocks_path_escape(tmp_path):
    agent = CoderAgent(_ScriptedModel([
        _act("read_file", path="../../../../etc/passwd"),
        _act("done", summary="x"),
    ]), workdir=tmp_path)
    res = agent.run_task("read outside")
    obs = res["transcript"][0]["observation"]
    assert "escapes workdir" in obs


def test_coder_tolerates_bad_json_then_recovers(tmp_path):
    agent = CoderAgent(_ScriptedModel([
        "sorry, here is the plan (not json)",
        _act("done", summary="ok"),
    ]), workdir=tmp_path)
    res = agent.run_task("x")
    assert res["status"] == "done"
    assert "invalid" in res["transcript"][0]["observation"]


def test_coder_run_tool_executes_in_workdir(tmp_path):
    agent = CoderAgent(_ScriptedModel([
        _act("run", cmd="echo hello-from-tool"),
        _act("done", summary="ran"),
    ]), workdir=tmp_path)
    res = agent.run_task("run echo")
    assert any("hello-from-tool" in h["observation"] for h in res["transcript"])


def test_coder_unknown_tool_is_reported(tmp_path):
    agent = CoderAgent(_ScriptedModel([
        _act("frobnicate", x=1),
        _act("done", summary="ok"),
    ]), workdir=tmp_path)
    res = agent.run_task("x")
    assert "unknown tool" in res["transcript"][0]["observation"]


def test_coder_stops_at_max_steps(tmp_path):
    # never calls done → bounded by max_steps
    agent = CoderAgent(_ScriptedModel([_act("list_dir", path=".")]),
                       workdir=tmp_path, max_steps=3)
    res = agent.run_task("loop")
    assert res["status"] == "max_steps" and res["steps"] == 3
