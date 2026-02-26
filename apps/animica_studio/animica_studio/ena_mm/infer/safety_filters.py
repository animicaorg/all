from __future__ import annotations

DISALLOWED = {"sexual minor", "csam", "terror bomb", "self-harm instructions"}


def validate_prompt(prompt: str) -> tuple[bool, str]:
    low = prompt.lower()
    for key in DISALLOWED:
        if key in low:
            return False, f"Blocked by safety filter ({key})."
    return True, "ok"
