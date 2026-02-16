from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

ANIMICA_STRICT_GUARDRAILS = """
You are Animica's contract generation engine.
Rules:
1) Output deploy-ready Animica contract source only.
2) Never include markdown fences.
3) Keep ABI and manifest JSON-serializable.
4) If unsafe or unclear input is provided, return a safe fallback contract skeleton.
""".strip()


@dataclass
class GuardrailResult:
    content: str
    abi: list[dict[str, Any]]
    manifest: dict[str, Any]


def _extract_json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, (dict, list)):
                return parsed
        except json.JSONDecodeError:
            return fallback
    return fallback


def enforce_output(raw: dict[str, Any]) -> GuardrailResult:
    content = str(raw.get("content") or "").strip()
    content = re.sub(r"```[a-zA-Z]*", "", content).replace("```", "").strip()
    if not content:
        content = "contract Generated {\n  fn placeholder() -> string { return \"animica\" }\n}"
    if "contract" not in content:
        content = f"contract Generated {{\n  {content}\n}}"

    abi = _extract_json(raw.get("abi"), [])
    if not isinstance(abi, list):
        abi = []

    manifest = _extract_json(raw.get("manifest"), {})
    if not isinstance(manifest, dict):
        manifest = {}

    return GuardrailResult(content=content, abi=abi, manifest=manifest)
