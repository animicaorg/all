from __future__ import annotations

from pathlib import Path

import torch


def save_checkpoint(path: Path, *, model, optimizer, step: int, best_eval_loss: float | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.save(
        {
            "step": step,
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "best_eval_loss": best_eval_loss,
        },
        path,
    )


def load_checkpoint(path: Path, *, model, optimizer) -> tuple[int, float | None]:
    state = torch.load(path, map_location="cpu")
    model.load_state_dict(state["model"])
    optimizer.load_state_dict(state["optimizer"])
    return int(state.get("step", 0)), state.get("best_eval_loss")
