from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TokenizerArtifacts:
    kind: str
    path: Path


class ByteTokenizer:
    """Deterministic byte-level tokenizer fallback."""

    vocab_size = 256

    def encode(self, text: str) -> list[int]:
        return list(text.encode("utf-8", errors="replace"))

    def decode(self, ids: list[int]) -> str:
        return bytes(max(0, min(255, int(i))) for i in ids).decode("utf-8", errors="replace")

    def save(self, out_dir: Path) -> TokenizerArtifacts:
        out_dir.mkdir(parents=True, exist_ok=True)
        p = out_dir / "tokenizer.json"
        p.write_text(json.dumps({"kind": "byte", "vocab_size": self.vocab_size}, indent=2), encoding="utf-8")
        return TokenizerArtifacts(kind="byte", path=p)


def load_tokenizer(path: Path) -> ByteTokenizer:
    _ = json.loads(path.read_text(encoding="utf-8"))
    return ByteTokenizer()
