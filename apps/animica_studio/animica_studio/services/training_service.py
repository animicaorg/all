from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable


class TrainingService:
    PRESETS = {
        "quick": {"minutes": 5, "steps": 20},
        "medium": {"minutes": 30, "steps": 120},
        "long": {"minutes": 120, "steps": 480},
    }

    def estimate_cpu(self, preset: str) -> str:
        p = self.PRESETS.get(preset, self.PRESETS["quick"])
        if p["minutes"] <= 5:
            return "Low"
        if p["minutes"] <= 30:
            return "Medium"
        return "High"

    def run_training(
        self,
        checkpoint_id: str,
        dataset_id: str,
        preset: str = "quick",
        dev_mode: bool = False,
        progress_cb: Callable[[int, str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict:
        p = self.PRESETS.get(preset, self.PRESETS["quick"])
        seed = 1337 if dev_mode else 2026
        total = p["steps"]
        losses = []
        for step in range(1, total + 1):
            if should_stop and should_stop():
                break
            loss = max(0.001, 1.0 / (step + (seed % 7)))
            losses.append(loss)
            if progress_cb:
                progress_cb(int(step / total * 100), f"step={step} loss={loss:.4f}")
        ckpt_hash = hashlib.sha256(f"{checkpoint_id}:{dataset_id}:{preset}:{seed}:{len(losses)}".encode()).hexdigest()
        return {"seed": seed, "steps": len(losses), "final_loss": losses[-1] if losses else None, "checkpoint_hash": ckpt_hash}

    def save_checkpoint(self, store_dir: Path, model_id: str, payload: dict) -> Path:
        store_dir.mkdir(parents=True, exist_ok=True)
        out = store_dir / f"{model_id}.ckpt.json"
        out.write_text(__import__("json").dumps(payload, indent=2), encoding="utf-8")
        return out
