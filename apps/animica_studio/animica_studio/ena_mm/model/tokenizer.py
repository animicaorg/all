from __future__ import annotations


class SimpleTokenizer:
    def encode(self, text: str) -> list[int]:
        return [ord(ch) % 256 for ch in text][:512]

    def decode(self, tokens: list[int]) -> str:
        return "".join(chr(t) for t in tokens if 31 < t < 127)
