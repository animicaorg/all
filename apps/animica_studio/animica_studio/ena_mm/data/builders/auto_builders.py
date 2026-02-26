from __future__ import annotations

import json
from pathlib import Path


def build_text_dataset(out_dir: str) -> str:
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    rows = [
        {"prompt": "Explain Animica in one sentence.", "target": "Animica is a local-first blockchain and AI studio platform."},
        {"prompt": "What is ENA-MM?", "target": "ENA-MM is a shared-backbone multimodal model package for text, image and video."},
    ]
    p = root / "text.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return str(p)


def validate_user_dataset(path: str, modality: str) -> tuple[bool, str]:
    p = Path(path)
    if not p.exists():
        return False, f"{modality} dataset path does not exist"
    if modality in {"image", "video"} and not (p / "captions.txt").exists():
        return False, f"{modality} dataset requires captions.txt"
    return True, "ok"
