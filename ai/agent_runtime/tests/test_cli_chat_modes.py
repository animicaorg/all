"""Tests for the parts of `animica chat` that decide what happens to your machine.

The permission modes, the entitlement caps, the reasoning stripper and the swarm
orchestrator all share one property: when they are wrong, the failure is silent.
An approval mode that quietly auto-approves `bash`, a cap that never bites, a
reply that is actually the model's scratchpad, a "reviewed" answer nothing
reviewed — none of them raise. So they are tested here rather than trusted.
"""

from __future__ import annotations

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
    def pricey(prompt: str):
        if "Decompose" in prompt:
            return ('["a","b","c","d"]', 0.9, 10)
        return ("done", 0.9, 10)

    r = run_swarm(task="T", submit_turn=pricey, policy=PermissionPolicy("plan"),
                  permission_prompter=_deny, cwd=str(tmp_path),
                  max_agents=2, max_cost=1.0)
    assert r.stop_reason == "max_cost"
    # The cap bounds work STARTED, not a round of in-flight turns, so an overrun
    # of up to one turn per running agent is expected and documented. What must
    # not happen is unbounded spending.
    assert r.total_cost < 1.0 + 2 * 0.9 + 0.01
    assert any(x.error and "budget" in x.error for x in r.results)


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
