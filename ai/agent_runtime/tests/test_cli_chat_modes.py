"""Tests for the parts of `animica chat` that decide what happens to your machine.

The permission modes, the entitlement caps, the reasoning stripper and the swarm
orchestrator all share one property: when they are wrong, the failure is silent.
An approval mode that quietly auto-approves `bash`, a cap that never bites, a
reply that is actually the model's scratchpad, a "reviewed" answer nothing
reviewed — none of them raise. So they are tested here rather than trusted.
"""

from __future__ import annotations

import io
import json
import os
import tempfile

import pytest

from agent_runtime.agentic import (
    DEFAULT_PERMISSION_MODE,
    PERMISSION_MODES,
    PermissionPolicy,
    _TOOL_BY_NAME,
    category_of,
)
from agent_runtime.orchestrator import (
    FREE_MAX_AGENTS,
    PRO_MAX_AGENTS,
    _parse_plan,
    _parse_verdict,
    max_agents_for,
    run_swarm,
)
from agent_runtime.provider_hosted import _is_capacity_apology, strip_reasoning


def _deny(tool, args):
    return "deny"


def _allow(tool, args):
    return "allow"


# --------------------------------------------------------------------------- #
# Permission modes                                                            #
# --------------------------------------------------------------------------- #

def test_default_mode_asks_before_anything_with_a_consequence():
    p = PermissionPolicy()
    assert p.mode == DEFAULT_PERMISSION_MODE == "manual"
    # A read needs no prompt...
    ok, _ = p.evaluate(_TOOL_BY_NAME["read_file"], {}, prompter=_deny)
    assert ok
    # ...and everything else does. `_deny` stands in for the user saying no.
    for name in ("write_file", "edit_file", "delete_file", "bash", "python_eval"):
        ok, why = p.evaluate(_TOOL_BY_NAME[name], {}, prompter=_deny)
        assert not ok, f"{name} must be gated in manual mode"
        assert "denied" in why


def test_plan_mode_refuses_rather_than_asking():
    """plan mode's purpose is that the session cannot change anything, so it must
    not offer a prompt that would let it."""
    p = PermissionPolicy("plan")
    prompted = []

    def spy(tool, args):
        prompted.append(tool.name)
        return "allow"

    for name in ("write_file", "bash", "delete_file", "fetch_url"):
        ok, why = p.evaluate(_TOOL_BY_NAME[name], {}, prompter=spy)
        assert not ok
        assert "plan mode" in why
    assert prompted == [], "plan mode must never prompt — a prompt is a way out of it"


def test_auto_edit_lets_edits_through_but_still_gates_execution():
    """The distinction the single is_safe bit could not express, and the reason
    this mode exists."""
    p = PermissionPolicy("auto-edit")
    for name in ("write_file", "edit_file", "append_file", "mkdir", "move_file"):
        ok, _ = p.evaluate(_TOOL_BY_NAME[name], {}, prompter=_deny)
        assert ok, f"{name} should apply automatically in auto-edit"
    for name in ("bash", "python_eval", "delete_file"):
        ok, _ = p.evaluate(_TOOL_BY_NAME[name], {}, prompter=_deny)
        assert not ok, f"{name} must still be gated in auto-edit"


def test_auto_mode_approves_everything():
    p = PermissionPolicy("auto")
    for name in _TOOL_BY_NAME:
        ok, _ = p.evaluate(_TOOL_BY_NAME[name], {}, prompter=_deny)
        assert ok, f"{name} should be auto-approved in auto mode"


def test_legacy_flags_map_onto_modes():
    assert PermissionPolicy(yolo=True).mode == "auto"
    assert PermissionPolicy(read_only=True).mode == "plan"
    with pytest.raises(ValueError):
        PermissionPolicy(yolo=True, read_only=True)


def test_unknown_mode_is_refused_with_the_valid_list():
    with pytest.raises(ValueError) as exc:
        PermissionPolicy("supervised")
    for name in PERMISSION_MODES:
        assert name in str(exc.value)


def test_a_tool_nobody_classified_is_gated_not_permitted():
    """An unmapped tool must fail closed. Otherwise adding a tool and forgetting
    to categorise it silently grants it."""
    from agent_runtime.agentic import ToolSpec
    mystery = ToolSpec(name="launch_missiles", description="", parameters={},
                       is_safe=False, handler=lambda: "")
    assert category_of(mystery) == "exec"
    ok, _ = PermissionPolicy("auto-edit").evaluate(mystery, {}, prompter=_deny)
    assert not ok


def test_session_allow_remembers_only_that_tool():
    p = PermissionPolicy("manual")
    ok, _ = p.evaluate(_TOOL_BY_NAME["bash"], {}, prompter=lambda t, a: "allow_session")
    assert ok
    # bash no longer prompts...
    ok, why = p.evaluate(_TOOL_BY_NAME["bash"], {}, prompter=_deny)
    assert ok and "session" in why
    # ...but delete_file still does.
    ok, _ = p.evaluate(_TOOL_BY_NAME["delete_file"], {}, prompter=_deny)
    assert not ok


def test_always_widens_the_mode_by_kind_not_to_everything():
    """Answering "always" to a write must not also arm shell access."""
    p = PermissionPolicy("manual")
    ok, _ = p.evaluate(_TOOL_BY_NAME["write_file"], {}, prompter=lambda t, a: "allow_mode")
    assert ok
    assert p.mode == "auto-edit", "a write should widen to auto-edit, not auto"
    ok, _ = p.evaluate(_TOOL_BY_NAME["bash"], {}, prompter=_deny)
    assert not ok, "shell must still be gated after allowing writes"


def test_always_on_an_exec_tool_widens_all_the_way():
    p = PermissionPolicy("manual")
    ok, _ = p.evaluate(_TOOL_BY_NAME["bash"], {}, prompter=lambda t, a: "allow_mode")
    assert ok and p.mode == "auto"


def test_explicit_deny_beats_every_mode():
    p = PermissionPolicy("auto", overrides={"bash": "deny"})
    ok, _ = p.evaluate(_TOOL_BY_NAME["bash"], {}, prompter=_allow)
    assert not ok


def test_would_ask_previews_without_prompting():
    p = PermissionPolicy("auto-edit")
    assert not p.would_ask(_TOOL_BY_NAME["write_file"])
    assert p.would_ask(_TOOL_BY_NAME["bash"])


# --------------------------------------------------------------------------- #
# Reasoning stripper                                                          #
# --------------------------------------------------------------------------- #

def test_unterminated_think_block_is_reasoning_not_the_answer():
    """The live endpoint's actual output when max_tokens cuts it off mid-thought.
    Left in, this prints the model's scratchpad as its reply."""
    raw = "<think>\nOkay, let's see. The user is asking me to act as"
    answer, reasoning = strip_reasoning(raw)
    assert answer == ""
    assert "Okay, let's see" in reasoning


def test_closed_think_block_is_removed_and_kept():
    answer, reasoning = strip_reasoning("<think>plan it</think>The answer is 4.")
    assert answer == "The answer is 4."
    assert reasoning == "plan it"


def test_text_without_reasoning_is_untouched():
    answer, reasoning = strip_reasoning("17 * 3 = 51.")
    assert answer == "17 * 3 = 51."
    assert reasoning == ""


def test_capacity_apology_is_detected_but_a_real_answer_is_not():
    apology = ("⚠️ The Animica AI network couldn't complete your request just now — "
               "the provider that picked it up wasn't able to load a language model. "
               "Running a node? pip install -U animica && animica up serves chat.")
    assert _is_capacity_apology(apology)
    assert not _is_capacity_apology("17 * 3 = 51.")
    # A long genuine answer that happens to discuss providers is not the notice.
    assert not _is_capacity_apology("There is no provider for that. " * 60)


# --------------------------------------------------------------------------- #
# Entitlements                                                                #
# --------------------------------------------------------------------------- #

@pytest.fixture()
def fresh_home(monkeypatch):
    d = tempfile.mkdtemp()
    monkeypatch.setenv("ANIMICA_DATA_DIR", d)
    # Point the entitlement check at something closed so nothing reaches the net.
    monkeypatch.setenv("ANIMICA_ENTITLEMENT_URL", "http://127.0.0.1:1/none")
    monkeypatch.delenv("ANIMICA_LICENCE", raising=False)
    monkeypatch.delenv("ANIMICA_LICENSE", raising=False)
    return d


def test_no_licence_is_free_without_touching_the_network(fresh_home):
    from agent_runtime import entitlements as E
    ent = E.resolve()
    assert ent.tier == E.TIER_FREE
    assert ent.reason == "no licence key"
    assert ent.agent_tasks_per_day == E.FREE_AGENT_TASKS_PER_DAY


def test_free_tier_clamps_a_large_iteration_request(fresh_home):
    from agent_runtime import entitlements as E
    v = E.check_agent_task(E.resolve(), requested_iterations=500)
    assert v.allowed
    assert v.iterations == E.FREE_AGENT_ITERATIONS


def test_free_tier_daily_cap_bites_and_offers_the_upgrade(fresh_home):
    from agent_runtime import entitlements as E
    ent = E.resolve()
    for _ in range(E.FREE_AGENT_TASKS_PER_DAY):
        E.record_agent_task()
    v = E.check_agent_task(ent)
    assert not v.allowed
    assert str(E.FREE_AGENT_TASKS_PER_DAY) in v.reason
    assert "9.99" in v.upgrade_hint


def test_pro_lifts_both_limits(fresh_home):
    from agent_runtime import entitlements as E
    pro = E.Entitlements.pro("test")
    assert pro.agent_tasks_per_day is None
    v = E.check_agent_task(pro, requested_iterations=500)
    assert v.allowed and v.iterations == E.PRO_AGENT_ITERATIONS


def test_an_unreachable_entitlement_api_never_grants_pro(fresh_home):
    """The failure mode that would make the paywall pointless: unplug the network,
    get Pro."""
    from agent_runtime import entitlements as E
    E.write_licence("anmpro_live_whatever")
    ent = E.resolve()
    assert ent.tier == E.TIER_FREE
    assert "failed" in ent.reason


def test_a_verified_licence_survives_the_api_going_down(fresh_home):
    """And the opposite failure: downgrading someone who has paid because our own
    endpoint had a bad minute."""
    import time
    from agent_runtime import entitlements as E
    E.write_licence("anmpro_live_whatever")
    E._save_cache({"tier": E.TIER_PRO, "checked_at": time.time()})
    ent = E.resolve()
    assert ent.tier == E.TIER_PRO
    assert "grace" in ent.reason

    # ...but not forever.
    E._save_cache({"tier": E.TIER_PRO,
                   "checked_at": time.time() - (E.GRACE_DAYS + 1) * 86400})
    assert E.resolve().tier == E.TIER_FREE


def test_the_licence_file_is_not_world_readable(fresh_home):
    from agent_runtime import entitlements as E
    p = E.write_licence("anmpro_live_secret")
    assert oct(p.stat().st_mode & 0o777) == "0o600"


def test_a_licence_key_is_masked_for_display(fresh_home):
    from agent_runtime import entitlements as E
    assert E.masked("anmpro_live_abcdef1234567890").endswith("7890")
    assert "abcdef" not in E.masked("anmpro_live_abcdef1234567890")
    assert E.masked(None) == "(none)"


def test_the_usage_file_does_not_grow_without_bound(fresh_home):
    from agent_runtime import entitlements as E
    E.record_agent_task()
    data = json.loads((E._usage_path()).read_text())
    assert len(data["agent_tasks"]) <= 14


# --------------------------------------------------------------------------- #
# Swarm orchestration                                                         #
# --------------------------------------------------------------------------- #

def _plan_only(plan_json: str, *, cost: float = 0.01):
    """A submit_turn that plans, works, reviews and merges — no network."""
    def submit(prompt: str):
        if "Decompose" in prompt:
            return (plan_json, cost, 5)
        if "REFUTE" in prompt:
            return ("VERDICT: CONFIRMED\nchecked", cost, 5)
        if "Merge these" in prompt:
            return ("merged answer", cost, 5)
        return ('[TOOL_CALL]\n{"tool":"done","args":{"message":"ok"}}\n[/TOOL_CALL]',
                cost, 5)
    return submit


def test_plan_parsing_survives_every_shape_a_model_returns():
    assert _parse_plan('["a","b"]', "T", 8) == ["a", "b"]
    assert _parse_plan('```json\n["x","y"]\n```', "T", 8) == ["x", "y"]
    assert _parse_plan("1. inspect the schema\n2. review the api", "T", 8) == [
        "inspect the schema", "review the api"]
    # Prose is NOT a subtask list: turning commentary into the task is worse than
    # not splitting at all.
    assert _parse_plan("I think we should do it all at once.", "ORIGINAL", 8) == ["ORIGINAL"]
    assert _parse_plan('["a",', "ORIGINAL", 8) == ["ORIGINAL"]
    assert _parse_plan("", "ORIGINAL", 8) == ["ORIGINAL"]


def test_plan_is_capped_at_the_allowed_width():
    assert len(_parse_plan('["a","b","c","d","e"]', "T", 2)) == 2


def test_a_review_without_a_verdict_line_counts_as_refuted():
    """Treating an inconclusive review as confirmation is how unreviewed work gets
    marked reviewed."""
    assert _parse_verdict("VERDICT: CONFIRMED\nfine")[0] == "confirmed"
    assert _parse_verdict("VERDICT: REFUTED\nno such file")[0] == "refuted"
    assert _parse_verdict("it looks probably okay to me")[0] == "refuted"
    assert _parse_verdict("")[0] == "refuted"


def test_width_follows_the_tier():
    from agent_runtime.entitlements import Entitlements
    assert max_agents_for(Entitlements.free()) == FREE_MAX_AGENTS
    assert max_agents_for(Entitlements.pro()) == PRO_MAX_AGENTS
    assert max_agents_for(None) == FREE_MAX_AGENTS


def test_a_swarm_plans_fans_out_verifies_and_merges(tmp_path):
    r = run_swarm(
        task="survey the repo",
        submit_turn=_plan_only('["count py files","count md files"]'),
        policy=PermissionPolicy("plan"),
        permission_prompter=_deny,
        cwd=str(tmp_path),
        max_agents=2, max_iterations=3, max_cost=1.0,
    )
    assert r.plan == ["count py files", "count md files"]
    assert len(r.results) == 2
    assert all(x.verdict == "confirmed" for x in r.results)
    assert r.verified
    assert len(r.surviving) == 2
    assert r.synthesis == "merged answer"
    assert r.stop_reason == "done"


def test_a_refuted_subtask_is_dropped_from_the_result(tmp_path):
    def submit(prompt: str):
        if "Decompose" in prompt:
            return ('["claim X","claim Y"]', 0.01, 5)
        if "REFUTE" in prompt:
            return ("VERDICT: REFUTED\nthe file it cites does not exist", 0.01, 5)
        if "Merge these" in prompt:
            return ("merged", 0.01, 5)
        return ('[TOOL_CALL]\n{"tool":"done","args":{"message":"claimed"}}\n[/TOOL_CALL]',
                0.01, 5)

    r = run_swarm(task="T", submit_turn=submit, policy=PermissionPolicy("plan"),
                  permission_prompter=_deny, cwd=str(tmp_path),
                  max_agents=2, max_cost=5.0)
    assert [x.verdict for x in r.results] == ["refuted", "refuted"]
    assert r.surviving == []


def test_one_agent_crashing_does_not_kill_the_swarm(tmp_path):
    def submit(prompt: str):
        if "Decompose" in prompt:
            return ('["ok task","boom task"]', 0.01, 5)
        if "boom" in prompt:
            raise RuntimeError("worker exploded")
        if "REFUTE" in prompt:
            return ("VERDICT: CONFIRMED", 0.01, 5)
        if "Merge these" in prompt:
            return ("merged", 0.01, 5)
        return ('[TOOL_CALL]\n{"tool":"done","args":{"message":"ok"}}\n[/TOOL_CALL]',
                0.01, 5)

    r = run_swarm(task="T", submit_turn=submit, policy=PermissionPolicy("plan"),
                  permission_prompter=_deny, cwd=str(tmp_path),
                  max_agents=2, max_cost=5.0)
    assert len(r.results) == 2
    assert sorted(x.completed for x in r.results) == [False, True]


def test_the_cost_cap_stops_the_swarm_and_says_so(tmp_path):
    seen: list[str] = []

    def pricey(prompt: str):
        seen.append(prompt)
        if "Decompose" in prompt:
            return ('["a","b","c","d"]', 0.9, 10)
        return ("done", 0.9, 10)

    r = run_swarm(task="T", submit_turn=pricey, policy=PermissionPolicy("plan"),
                  permission_prompter=_deny, cwd=str(tmp_path),
                  max_agents=2, max_cost=1.0)
    assert r.stop_reason == "max_cost"
    # The cap bounds work STARTED, not a round of in-flight turns: a turn's cost
    # is unknown until it returns, so the overrun ceiling is one turn per agent
    # that was already running. What must not happen is unbounded spending.
    assert r.total_cost < 1.0 + 2 * 0.9 + 0.01
    # And once the budget is gone the swarm must not keep buying turns for the
    # phases it has not started yet — that is where a soft cap leaks.
    assert not any("REFUTE" in p for p in seen), "review must not run past the cap"
    assert not any("Synthes" in p for p in seen), "synthesis must not run past the cap"


def test_a_planning_turn_that_exhausts_the_budget_starts_no_agents(tmp_path):
    """The one place the cap is exact: nothing has been spawned yet, so it can
    still refuse outright instead of overrunning by a turn per agent."""
    def pricey(prompt: str):
        assert "Decompose" in prompt, "no agent should have been started"
        return ('["a","b","c","d"]', 5.0, 10)

    r = run_swarm(task="T", submit_turn=pricey, policy=PermissionPolicy("plan"),
                  permission_prompter=_deny, cwd=str(tmp_path),
                  max_agents=4, max_cost=1.0)
    assert r.stop_reason == "max_cost"
    assert r.results == []
    assert r.synthesis == ""


def test_planning_failure_degrades_to_one_agent(tmp_path):
    """A planner that throws must not take the whole task down."""
    def submit(prompt: str):
        if "Decompose" in prompt:
            raise RuntimeError("planner offline")
        if "REFUTE" in prompt:
            return ("VERDICT: CONFIRMED", 0.01, 5)
        return ('[TOOL_CALL]\n{"tool":"done","args":{"message":"did it whole"}}\n[/TOOL_CALL]',
                0.01, 5)

    r = run_swarm(task="the original task", submit_turn=submit,
                  policy=PermissionPolicy("plan"), permission_prompter=_deny,
                  cwd=str(tmp_path), max_agents=4, max_cost=2.0)
    assert r.plan == ["the original task"]
    assert len(r.results) == 1


def test_a_single_agent_swarm_skips_planning_entirely(tmp_path):
    """Width 1 should not pay for a decomposition turn it cannot use."""
    seen = []

    def submit(prompt: str):
        seen.append(prompt[:20])
        if "REFUTE" in prompt:
            return ("VERDICT: CONFIRMED", 0.01, 5)
        return ('[TOOL_CALL]\n{"tool":"done","args":{"message":"solo"}}\n[/TOOL_CALL]',
                0.01, 5)

    r = run_swarm(task="just do it", submit_turn=submit,
                  policy=PermissionPolicy("plan"), permission_prompter=_deny,
                  cwd=str(tmp_path), max_agents=1, max_cost=1.0)
    assert r.plan == ["just do it"]
    assert not any("Decompose" in s for s in seen)


def test_a_failed_review_does_not_count_as_confirmation(tmp_path):
    def submit(prompt: str):
        if "Decompose" in prompt:
            return ('["a"]', 0.01, 5)
        if "REFUTE" in prompt:
            raise RuntimeError("reviewer offline")
        return ('[TOOL_CALL]\n{"tool":"done","args":{"message":"claimed"}}\n[/TOOL_CALL]',
                0.01, 5)

    r = run_swarm(task="T", submit_turn=submit, policy=PermissionPolicy("plan"),
                  permission_prompter=_deny, cwd=str(tmp_path),
                  max_agents=1, max_cost=2.0)
    assert r.results[0].verdict is None
    assert "review failed" in r.results[0].verdict_reason


# --------------------------------------------------------------------------- #
# Streaming, project context, line editing                                    #
# --------------------------------------------------------------------------- #

def _collect(chunks: list[str]) -> str:
    """Feed chunks through the streamer and return what reached the terminal."""
    from agent_runtime.cli.chat import _ProseStreamer
    sink = io.StringIO()
    s = _ProseStreamer(lambda: None, out=sink)
    for c in chunks:
        s.feed(c)
    s.finish()
    return sink.getvalue()


def test_prose_streams_through_unchanged():
    assert _collect(["Hel", "lo th", "ere."]) == "Hello there.\n"


def test_a_tool_call_block_is_never_shown():
    """Streaming raw tool JSON at someone is noise — the `· tool name` line already
    reports the call."""
    got = _collect(['Looking. [TOOL_CALL]{"tool":"read_file","args":{}}[/TOOL_CALL] Found it.'])
    assert "read_file" not in got
    assert "TOOL_CALL" not in got
    assert "Looking." in got and "Found it." in got


def test_a_tag_split_across_chunks_is_still_suppressed():
    """The reason a tail is held back: a naive implementation prints "[TOOL" and then
    swallows the rest."""
    got = _collect(["think [TOOL", "_CALL]{\"tool\":\"bash\"}[/TOOL_", "CALL] done"])
    assert "TOOL" not in got
    assert "bash" not in got
    assert "think" in got and "done" in got


def test_an_unterminated_tool_call_shows_nothing_after_it():
    """A turn cut off mid-tool-call must not dump half a JSON block on screen."""
    got = _collect(['prose [TOOL_CALL]{"tool":"write_file","args":{"path":"x"'])
    assert "write_file" not in got
    assert "prose" in got


def test_the_first_token_stops_the_spinner_exactly_once():
    from agent_runtime.cli.chat import _ProseStreamer
    calls = []
    s = _ProseStreamer(lambda: calls.append(1), out=io.StringIO())
    s.feed("a")
    s.feed("b")
    s.finish()
    assert len(calls) == 1, "the spinner must be stopped once, not per chunk"


def test_a_turn_that_is_only_a_tool_call_never_stops_the_spinner():
    """Nothing was shown, so the caller still needs to render the answer itself."""
    from agent_runtime.cli.chat import _ProseStreamer
    calls = []
    s = _ProseStreamer(lambda: calls.append(1), out=io.StringIO())
    s.feed('[TOOL_CALL]{"tool":"grep"}[/TOOL_CALL]')
    s.finish()
    assert calls == []


def test_project_instructions_are_found_and_appended_after_the_tool_contract(tmp_path):
    """A repo's house rules must not be able to talk the agent out of the tool
    format, so they go after it."""
    from agent_runtime.agentic import build_system_prompt, load_project_context
    (tmp_path / "AGENTS.md").write_text("Always reply in British English.", encoding="utf-8")
    name, text = load_project_context(str(tmp_path))
    assert name == "AGENTS.md"
    assert "British English" in text
    prompt = build_system_prompt(str(tmp_path))
    assert prompt.index("Project instructions") > prompt.rindex("TOOL_CALL")


def test_project_instructions_are_found_from_a_subdirectory(tmp_path):
    from agent_runtime.agentic import load_project_context
    (tmp_path / "AGENTS.md").write_text("root rules", encoding="utf-8")
    deep = tmp_path / "src" / "pkg"
    deep.mkdir(parents=True)
    name, text = load_project_context(str(deep))
    assert text == "root rules"
    assert name.endswith("AGENTS.md")


def test_the_nearest_instruction_file_wins_rather_than_merging(tmp_path):
    """Two files disagreeing about house style is worse than reading only the
    closest one."""
    from agent_runtime.agentic import load_project_context
    (tmp_path / "AGENTS.md").write_text("outer", encoding="utf-8")
    inner = tmp_path / "sub"
    inner.mkdir()
    (inner / "AGENTS.md").write_text("inner", encoding="utf-8")
    _, text = load_project_context(str(inner))
    assert text == "inner"


def test_an_oversized_instruction_file_is_truncated_and_says_so(tmp_path):
    from agent_runtime.agentic import MAX_PROJECT_CONTEXT_BYTES, load_project_context
    (tmp_path / "AGENTS.md").write_text("x" * (MAX_PROJECT_CONTEXT_BYTES + 5000), encoding="utf-8")
    _, text = load_project_context(str(tmp_path))
    assert "truncated" in text
    assert len(text) < MAX_PROJECT_CONTEXT_BYTES + 200


def test_an_empty_instruction_file_is_ignored(tmp_path):
    """An empty AGENTS.md should not announce itself in the banner."""
    from agent_runtime.agentic import load_project_context
    (tmp_path / "AGENTS.md").write_text("   \n\n", encoding="utf-8")
    name, text = load_project_context(str(tmp_path))
    assert name is None and text is None


def test_no_instruction_file_means_no_extra_prompt(tmp_path):
    from agent_runtime.agentic import build_system_prompt
    prompt = build_system_prompt(str(tmp_path))
    assert "Project instructions" not in prompt


# --------------------------------------------------------------------------- #
# Piped stdin                                                                 #
# --------------------------------------------------------------------------- #

class _FakePipe(io.StringIO):
    def isatty(self):
        return False


def test_piped_stdin_is_read(monkeypatch):
    from agent_runtime.cli.chat import _read_piped_stdin
    monkeypatch.setattr("sys.stdin", _FakePipe("Traceback...\nValueError: nope\n"))
    assert "ValueError: nope" in _read_piped_stdin()


def test_a_terminal_is_not_treated_as_piped_input(monkeypatch):
    """Reading a tty here would block the interactive REPL forever."""
    from agent_runtime.cli.chat import _read_piped_stdin

    class Tty(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdin", Tty("should never be read"))
    assert _read_piped_stdin() == ""


def test_a_huge_pipe_is_truncated_rather_than_sent_whole(monkeypatch):
    from agent_runtime.cli.chat import MAX_PIPED_BYTES, _read_piped_stdin
    monkeypatch.setattr("sys.stdin", _FakePipe("x" * (MAX_PIPED_BYTES * 3)))
    got = _read_piped_stdin()
    assert "input truncated" in got
    assert len(got) < MAX_PIPED_BYTES + 100


def test_the_question_survives_the_pipe(monkeypatch):
    """The prompt must not be buried: it goes first, the data second."""
    from agent_runtime.cli.chat import _with_piped_input
    out = _with_piped_input("why did this fail", "line one\nline two")
    assert out.startswith("why did this fail")
    assert "line two" in out


# --------------------------------------------------------------------------- #
# An interrupted turn                                                         #
# --------------------------------------------------------------------------- #

def _session(tmp_path):
    from rich.console import Console
    from agent_runtime.cli.chat import Session
    return Session(Console(), PermissionPolicy("plan"), object(), str(tmp_path),
                   "sess-test", 4)


def test_an_interrupted_turn_leaves_no_unanswered_message(tmp_path, monkeypatch):
    """Otherwise the next turn sends two user messages in a row, and the session
    file records half a turn."""
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path / "state"))

    def boom(**kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("agent_runtime.cli.chat.run_agent_loop", boom)
    s = _session(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        s.ask("do a long thing")
    assert s.turns == []


def test_a_failed_turn_is_not_billed_against_the_daily_cap(tmp_path, monkeypatch):
    """record_agent_task runs after the loop, so a turn that never happened must
    not consume one of a free user's ten."""
    from agent_runtime import entitlements as E
    monkeypatch.setenv("ANIMICA_DATA_DIR", str(tmp_path / "state"))
    before = E.agent_tasks_used_today()

    def boom(**kwargs):
        raise RuntimeError("network gone")

    monkeypatch.setattr("agent_runtime.cli.chat.run_agent_loop", boom)
    s = _session(tmp_path)
    with pytest.raises(RuntimeError):
        s.ask("something")
    assert E.agent_tasks_used_today() == before


def test_leading_blank_lines_are_not_output_and_do_not_count_as_streamed():
    """Observed live: the model opens a turn with "\\n\\n\\n" then a tool call.
    Printed, that shoves everything down the screen; counted as streamed, it
    suppresses the answer the caller would otherwise render."""
    from agent_runtime.cli.chat import _ProseStreamer, _streamed_recently
    calls = []
    s = _ProseStreamer(lambda: calls.append(1), out=io.StringIO())
    s.feed('\n\n\n[TOOL_CALL]{"tool":"read_file"}[/TOOL_CALL]')
    s.finish()
    assert calls == [], "whitespace is not the first token"
    assert _streamed_recently[0] is False


def test_each_turn_reports_its_own_streaming_not_an_earlier_one():
    """The flag decides whether the final answer gets rendered, so an iteration
    that printed prose must not speak for the one after it."""
    from agent_runtime.cli.chat import _ProseStreamer, _streamed_recently
    first = _ProseStreamer(lambda: None, out=io.StringIO())
    first.feed("some prose")
    first.finish()
    assert _streamed_recently[0] is True
    _ProseStreamer(lambda: None)            # a new turn begins
    assert _streamed_recently[0] is False


def test_blank_lines_inside_prose_are_kept():
    """Paragraph breaks in an answer are content; only the leading run is dropped."""
    assert _collect(["\n\nfirst para\n\nsecond para"]) == "first para\n\nsecond para\n"
