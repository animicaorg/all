from __future__ import annotations

import json
from pathlib import Path


class TextJsonlDataset:
    def __init__(self, path: str) -> None:
        self.path = Path(path)

    def __iter__(self):
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            prompt = str(item.get("prompt") or item.get("text") or "").strip()
            target = str(item.get("target") or item.get("completion") or prompt).strip()
            if prompt:
                yield {"prompt": prompt, "target": target}
