from __future__ import annotations


def build_chat_prompt(system_prompt: str, history: list[tuple[str, str]], user_prompt: str) -> str:
    chunks = []
    if system_prompt.strip():
        chunks.append(f"[system]\n{system_prompt.strip()}\n")
    for u, a in history:
        chunks.append(f"[user]\n{u}\n[assistant]\n{a}\n")
    chunks.append(f"[user]\n{user_prompt}\n[assistant]\n")
    return "\n".join(chunks)
