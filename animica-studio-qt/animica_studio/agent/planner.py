"""Planning for the Animica Studio Agent.

For large / multi-file requests the engine first asks the model to produce a
structured plan (:class:`animica_studio.models.PlanStep` list) before acting.
:class:`Planner` decides *whether* a plan is warranted, prompts the model, and
parses the JSON plan it returns (tolerantly).

No Qt dependency; imports headless.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Optional

from ..models import Message, MessageRole, PlanStep
from . import prompts

if TYPE_CHECKING:  # pragma: no cover
    from ..services.animica_client import AnimicaClient


# Heuristics for deciding a request needs an upfront plan.
_PLAN_TRIGGER_WORDS = (
    "refactor",
    "implement",
    "build",
    "create a",
    "add a",
    "migrate",
    "rewrite",
    "scaffold",
    "set up",
    "整理",  # tolerate non-ascii without crashing
    "feature",
    "across",
    "multiple files",
    "test suite",
    "rename all",
    "endpoint",
    "module",
)
_MIN_CHARS_FOR_PLAN = 160


@dataclass
class PlanResult:
    """Outcome of a planning attempt."""

    summary: str = ""
    steps: list[PlanStep] = None  # type: ignore[assignment]
    files_to_change: list[str] = None  # type: ignore[assignment]
    raw: str = ""

    def __post_init__(self) -> None:
        if self.steps is None:
            self.steps = []
        if self.files_to_change is None:
            self.files_to_change = []

    @property
    def is_empty(self) -> bool:
        return not self.steps

    def steps_as_dicts(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.steps]


class Planner:
    """Produces structured plans for non-trivial requests via the model."""

    def __init__(self, client: Optional["AnimicaClient"] = None) -> None:
        self.client = client

    # ------------------------------------------------------------------ #
    @staticmethod
    def needs_plan(user_request: str) -> bool:
        """Heuristic: does this request warrant an explicit plan first?"""
        if not user_request:
            return False
        text = user_request.lower()
        if len(user_request) >= _MIN_CHARS_FOR_PLAN:
            return True
        return any(w in text for w in _PLAN_TRIGGER_WORDS)

    def make_plan(
        self,
        *,
        system_prompt: str,
        context: str,
        user_request: str,
        model: Optional[str] = None,
        temperature: float = 0.2,
        cancel: Optional[Callable[[], bool]] = None,
    ) -> PlanResult:
        """Ask the model for a plan and parse it. Returns an empty plan on failure."""
        if self.client is None:
            return PlanResult(raw="")
        messages = [
            Message(role=MessageRole.SYSTEM, content=system_prompt),
        ]
        if context.strip():
            messages.append(
                Message(role=MessageRole.SYSTEM, content=prompts.build_context_message(context))
            )
        messages.append(
            Message(
                role=MessageRole.USER,
                content=f"Request:\n{user_request}\n\n{prompts.build_plan_prompt()}",
            )
        )
        try:
            raw = self.client.chat(
                messages,
                stream=False,
                temperature=temperature,
                model=model,
                cancel=cancel,
            )
        except Exception as exc:  # network/model failure shouldn't kill the run
            return PlanResult(raw=f"(planning failed: {exc})")
        return self.parse_plan(raw)

    # ------------------------------------------------------------------ #
    @staticmethod
    def parse_plan(raw: str) -> PlanResult:
        """Parse a plan from model output (fenced JSON, bare JSON, or numbered list)."""
        if not raw:
            return PlanResult(raw=raw or "")
        data = _extract_json_object(raw)
        if isinstance(data, dict):
            summary = str(data.get("summary", "")).strip()
            files = [str(f) for f in (data.get("files_to_change") or []) if str(f).strip()]
            steps: list[PlanStep] = []
            for i, item in enumerate(data.get("steps") or []):
                if isinstance(item, dict):
                    title = str(item.get("title", "")).strip()
                    detail = str(item.get("detail", "")).strip()
                elif isinstance(item, str):
                    title, detail = item.strip(), ""
                else:
                    continue
                if title or detail:
                    steps.append(PlanStep(index=i, title=title or detail[:60], detail=detail))
            return PlanResult(summary=summary, steps=steps, files_to_change=files, raw=raw)

        # Fallback: parse a numbered / bulleted list.
        steps = _parse_list_steps(raw)
        return PlanResult(summary="", steps=steps, files_to_change=[], raw=raw)


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def _extract_json_object(text: str) -> Optional[Any]:
    """Extract the first JSON object from text (fenced or bare)."""
    # Try fenced ```plan / ```json blocks first.
    for tag in (prompts.PLAN_FENCE, "json", ""):
        fence = f"```{tag}" if tag else "```"
        start = text.find(fence)
        while start != -1:
            nl = text.find("\n", start)
            if nl == -1:
                break
            end = text.find("```", nl + 1)
            if end == -1:
                break
            body = text[nl + 1 : end].strip()
            parsed = _try_json(body)
            if parsed is not None:
                return parsed
            start = text.find(fence, end + 3)
    # Try a bare {...} span.
    brace = text.find("{")
    if brace != -1:
        depth = 0
        for idx in range(brace, len(text)):
            ch = text[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    parsed = _try_json(text[brace : idx + 1])
                    if parsed is not None:
                        return parsed
                    break
    return None


def _try_json(s: str) -> Optional[Any]:
    try:
        return json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None


_LIST_RE = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+(.*)$")


def _parse_list_steps(text: str) -> list[PlanStep]:
    steps: list[PlanStep] = []
    for line in text.splitlines():
        m = _LIST_RE.match(line)
        if m:
            title = m.group(1).strip()
            if title:
                steps.append(PlanStep(index=len(steps), title=title, detail=""))
    return steps


__all__ = ["Planner", "PlanResult"]
