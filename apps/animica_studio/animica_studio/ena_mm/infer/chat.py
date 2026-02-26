from __future__ import annotations


def generate_text(prompt: str, history: list[dict[str, str]] | None = None) -> str:
    context = history[-1]["assistant"] + " " if history else ""
    return f"ENA-MM: {context}You asked: {prompt[:120]}"
