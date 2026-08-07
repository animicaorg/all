"""Multi-agent orchestration — the `ultracode` shape, for `animica chat`.

One agent working alone is limited by two things at once here: a single context,
and a single request at a time against a network where a cold miner takes two
minutes to answer. Fanning out fixes both, and the second one is why it matters
more on this network than on a fast hosted API — eight subagents working in
parallel cost roughly the wall-clock of the slowest one, not eight times the wait.

The pipeline, which is the same one the reviews in this repo use:

    plan  →  fan out  →  verify (adversarially)  →  synthesize

* **plan** asks the model to decompose the task into independent subtasks. It is a
  single cheap turn, and its output is parsed defensively: a model that returns
  prose instead of a list must degrade to "one subtask, the original task" rather
  than crash.
* **fan out** runs each subtask as a full agentic loop — the same `run_agent_loop`
  the single-agent path uses, so tools, permission modes and cost caps behave
  identically. Threads, because every subagent is network-bound.
* **verify** is the part that is easy to skip and shouldn't be. Each finding is
  handed to a *separate* agent told to refute it, defaulting to "refuted" when
  unsure. A plausible-but-wrong answer that survives one reviewer is much rarer
  than one that survives none.
* **synthesize** merges what survived into one answer.

Budgets are enforced, not advisory: `max_agents`, `max_cost` across the whole
swarm, and `max_iterations` per subagent. A swarm that silently spends ten times
the single-agent cost is not a feature.

Entitlements bound the width — the free tier gets a narrower swarm because this
is the expensive path. See `agent_runtime.entitlements`.
"""

from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional

from agent_runtime.agentic import (
    AgentRunResult,
    PermissionPolicy,
    ToolSpec,
    run_agent_loop,
)

# Width limits. Free gets a real swarm, just a narrow one: two agents already
# demonstrate the shape and halve the wall clock.
FREE_MAX_AGENTS = 2
PRO_MAX_AGENTS = 8
HARD_MAX_AGENTS = 16          # backstop against a runaway plan

DEFAULT_MAX_COST = 2.0        # ANIMICA, across the entire swarm


@dataclass
class SubtaskResult:
    index: int
    subtask: str
    text: str
    completed: bool
    cost: float
    iterations: int
    error: Optional[str] = None
    verdict: Optional[str] = None       # "confirmed" | "refuted" | None
    verdict_reason: str = ""


@dataclass
class SwarmResult:
    task: str
    plan: list[str]
    results: list[SubtaskResult]
    synthesis: str
    total_cost: float
    wall_ms: int
    stop_reason: str = "done"
    verified: bool = False

    @property
    def surviving(self) -> list[SubtaskResult]:
        return [r for r in self.results if r.completed and r.verdict != "refuted"]


# --------------------------------------------------------------------------- #
# Planning                                                                    #
# --------------------------------------------------------------------------- #

_PLAN_PROMPT = """Decompose this task into {n} INDEPENDENT subtasks that can be \
worked on in parallel without waiting for each other.

Task: {task}

Rules:
- Each subtask must be self-contained and useful on its own.
- No subtask may depend on another's output.
- If the task genuinely cannot be split, return exactly one subtask.

Reply with ONLY a JSON array of strings, nothing else. Example:
["inspect X and report Y", "check Z for W"]"""


def _parse_plan(text: str, task: str, limit: int) -> list[str]:
    """Pull a subtask list out of whatever the model said.

    Defensive by design: planning is one model turn and models return prose,
    fenced JSON, or a numbered list about equally often. Any parse failure means
    "one subtask, the original task", because a swarm that refuses to start
    because its planner was chatty is worse than a swarm of one.
    """
    if not text:
        return [task]
    # A fenced block, if present, is the most likely place the JSON lives.
    fenced = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else None
    if candidate is None:
        bare = re.search(r"\[[^\[\]]*\]", text, re.DOTALL)
        candidate = bare.group(0) if bare else None
    if candidate:
        try:
            items = json.loads(candidate)
            subtasks = [str(s).strip() for s in items
                        if isinstance(s, (str, int, float)) and str(s).strip()]
            if subtasks:
                return subtasks[:limit]
        except ValueError:
            pass
    # Fall back to a numbered / bulleted list — but only for lines that actually
    # carry a list marker. Accepting any long line turned a chatty planner's
    # "I think we should just do it all at once." into the subtask itself, which
    # is worse than not splitting: the swarm would then work on the commentary
    # instead of the task.
    marker = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+(.+)$")
    lines = []
    for ln in text.splitlines():
        m = marker.match(ln)
        if not m:
            continue
        item = m.group(1).strip()
        if len(item) > 12 and not item.lower().startswith("task:"):
            lines.append(item)
    if lines:
        return lines[:limit]
    return [task]


# --------------------------------------------------------------------------- #
# Verification                                                                #
# --------------------------------------------------------------------------- #

_VERIFY_PROMPT = """You are reviewing another agent's work. Try to REFUTE it.

Original subtask: {subtask}

What the agent reported:
---
{report}
---

Check it against the actual files and commands in this working directory. Look for
a claim that is not supported by what is really there, a file it says it changed
that it did not, or a conclusion that does not follow.

Default to REFUTED when you are not sure — a wrong answer that survives review is
worse than a right one that gets a second look.

Reply with exactly one line, then your reasoning:
VERDICT: CONFIRMED
or
VERDICT: REFUTED"""


def _parse_verdict(text: str) -> tuple[str, str]:
    if not text:
        return "refuted", "reviewer returned nothing"
    m = re.search(r"VERDICT:\s*(CONFIRMED|REFUTED)", text, re.IGNORECASE)
    if not m:
        # No verdict line means the review did not actually conclude. Treating
        # that as confirmation is how unreviewed work gets marked reviewed.
        return "refuted", "reviewer gave no verdict line"
    verdict = m.group(1).lower()
    reason = text[m.end():].strip()[:400]
    return verdict, reason


# --------------------------------------------------------------------------- #
# The swarm                                                                   #
# --------------------------------------------------------------------------- #

def max_agents_for(entitlements) -> int:
    """How wide this install may fan out."""
    if entitlements is not None and getattr(entitlements, "is_pro", False):
        return PRO_MAX_AGENTS
    return FREE_MAX_AGENTS


def run_swarm(
    *,
    task: str,
    submit_turn: Callable[[str], tuple[str, float, int]],
    policy: PermissionPolicy,
    permission_prompter: Callable[[ToolSpec, dict], str],
    cwd: Optional[str] = None,
    max_agents: int = FREE_MAX_AGENTS,
    max_iterations: int = 10,
    max_cost: float = DEFAULT_MAX_COST,
    verify: bool = True,
    on_event: Optional[Callable[[str, dict], None]] = None,
) -> SwarmResult:
    """Plan, fan out, verify, synthesize.

    `submit_turn` must be safe to call from several threads at once — the chat
    REPL's cascade is, because each call is an independent HTTP request.
    """
    started = time.monotonic()
    agents = max(1, min(int(max_agents), HARD_MAX_AGENTS))
    spent = 0.0
    spend_lock = threading.Lock()
    stop_reason = "done"

    def emit(kind: str, **fields) -> None:
        if on_event:
            try:
                on_event(kind, fields)
            except Exception:  # noqa: BLE001 — display must not break the run
                pass

    def charge(amount: float) -> bool:
        """Book cost and report whether the budget still allows more work."""
        nonlocal spent
        with spend_lock:
            spent += float(amount or 0.0)
            return spent < max_cost

    # ---- plan ------------------------------------------------------------
    emit("phase", name="plan")
    plan: list[str]
    if agents == 1:
        plan = [task]
    else:
        try:
            text, cost, _ = submit_turn(_PLAN_PROMPT.format(n=agents, task=task))
            charge(cost)
            plan = _parse_plan(text, task, agents)
        except Exception as exc:  # noqa: BLE001
            emit("warn", message=f"planning failed ({exc}); running the task whole")
            plan = [task]
    emit("plan", subtasks=list(plan))

    if spent >= max_cost:
        return SwarmResult(task=task, plan=plan, results=[], synthesis="",
                           total_cost=spent,
                           wall_ms=int((time.monotonic() - started) * 1000),
                           stop_reason="max_cost")

    # ---- fan out ---------------------------------------------------------
    emit("phase", name="work", count=len(plan))
    per_agent_cost = max(0.01, (max_cost - spent) / max(1, len(plan)))

    def work(idx: int, subtask: str) -> SubtaskResult:
        # Pre-flight budget check, inside the worker rather than before the fan-out.
        # A pool of N workers over M > N subtasks starts only N immediately; the
        # queued ones reach here later, by which time the budget may be gone, and
        # this is where they can still decline to spend.
        #
        # What this CANNOT do is make the cap exact. A turn's cost is unknown until
        # it returns, so the worst case is the budget plus one turn per agent
        # running concurrently — with 4 agents and a 0.9 turn against a 1.0 cap,
        # that is 4.5. The cap bounds how much work is *started*, not how much a
        # single round of in-flight turns can cost. Narrow the swarm or lower
        # --max-iterations to tighten it; pretending otherwise would be worse.
        if spent >= max_cost:
            emit("agent_skipped", index=idx, reason="budget exhausted")
            return SubtaskResult(index=idx, subtask=subtask, text="",
                                 completed=False, cost=0.0, iterations=0,
                                 error="skipped: swarm budget exhausted")
        emit("agent_start", index=idx, subtask=subtask)
        try:
            run: AgentRunResult = run_agent_loop(
                user_task=subtask,
                submit_turn=submit_turn,
                policy=policy,
                permission_prompter=permission_prompter,
                cwd=cwd,
                max_iterations=max_iterations,
                max_cost=per_agent_cost,
            )
            charge(run.total_cost)
            emit("agent_done", index=idx, completed=run.completed,
                 cost=run.total_cost, iterations=len(run.turns))
            return SubtaskResult(
                index=idx, subtask=subtask, text=run.final_text,
                completed=run.completed, cost=run.total_cost,
                iterations=len(run.turns),
                error=None if run.completed else run.stop_reason,
            )
        except Exception as exc:  # noqa: BLE001 — one agent must not kill the swarm
            emit("agent_error", index=idx, error=str(exc)[:200])
            return SubtaskResult(index=idx, subtask=subtask, text="",
                                 completed=False, cost=0.0, iterations=0,
                                 error=str(exc)[:200])

    results: list[SubtaskResult] = []
    with ThreadPoolExecutor(max_workers=agents) as pool:
        futures = {pool.submit(work, i, s): i for i, s in enumerate(plan)}
        for fut in as_completed(futures):
            results.append(fut.result())
    results.sort(key=lambda r: r.index)

    if spent >= max_cost:
        stop_reason = "max_cost"

    # ---- verify ----------------------------------------------------------
    did_verify = False
    if verify and stop_reason == "done":
        candidates = [r for r in results if r.completed and r.text.strip()]
        if candidates:
            emit("phase", name="verify", count=len(candidates))
            did_verify = True

            def review(r: SubtaskResult) -> None:
                if spent >= max_cost:
                    r.verdict, r.verdict_reason = None, "budget exhausted before review"
                    return
                try:
                    text, cost, _ = submit_turn(
                        _VERIFY_PROMPT.format(subtask=r.subtask, report=r.text[:4000]))
                    charge(cost)
                    r.verdict, r.verdict_reason = _parse_verdict(text)
                    emit("verdict", index=r.index, verdict=r.verdict)
                except Exception as exc:  # noqa: BLE001
                    # An unreachable reviewer is not a confirmation.
                    r.verdict, r.verdict_reason = None, f"review failed: {exc}"[:200]
                    emit("warn", message=f"review of subtask {r.index} failed")

            with ThreadPoolExecutor(max_workers=agents) as pool:
                list(pool.map(review, candidates))

    # ---- synthesize ------------------------------------------------------
    surviving = [r for r in results if r.completed and r.verdict != "refuted"]
    if len(plan) == 1 and surviving:
        synthesis = surviving[0].text
    elif surviving and spent < max_cost:
        emit("phase", name="synthesize")
        joined = "\n\n".join(
            f"### {r.subtask}\n{r.text[:2000]}" for r in surviving)
        try:
            text, cost, _ = submit_turn(
                "Merge these findings into one clear answer for the original task. "
                "Do not repeat them verbatim; resolve any disagreement and say what "
                "is actually true.\n\n"
                f"Original task: {task}\n\n{joined}")
            charge(cost)
            synthesis = text.strip()
        except Exception as exc:  # noqa: BLE001
            synthesis = joined
            emit("warn", message=f"synthesis failed ({exc}); returning raw findings")
    else:
        synthesis = "\n\n".join(f"### {r.subtask}\n{r.text}" for r in surviving)

    return SwarmResult(
        task=task, plan=plan, results=results, synthesis=synthesis,
        total_cost=spent, wall_ms=int((time.monotonic() - started) * 1000),
        stop_reason=stop_reason, verified=did_verify,
    )


__all__ = [
    "run_swarm", "SwarmResult", "SubtaskResult", "max_agents_for",
    "FREE_MAX_AGENTS", "PRO_MAX_AGENTS", "HARD_MAX_AGENTS", "DEFAULT_MAX_COST",
    "_parse_plan", "_parse_verdict",
]
