from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import EnaMMTrainConfig
from .eval import run_eval
from .losses import diffusion_loss_proxy, text_loss_proxy
from .mixed_batch_sampler import MixedBatchSampler


class EnaMMTrainer:
    def __init__(self, config: EnaMMTrainConfig, run_dir: str) -> None:
        self.config = config
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)

    def train(self) -> dict[str, Any]:
        ratios = {"text": self.config.ratio_text if self.config.enable_text else 0, "image": self.config.ratio_image if self.config.enable_image else 0, "video": self.config.ratio_video if self.config.enable_video else 0}
        sampler = MixedBatchSampler(ratios)
        last_eval: dict[str, float] = {}
        for step in range(1, self.config.steps + 1):
            modality = sampler.next_modality()
            loss = text_loss_proxy(step) if modality == "text" else diffusion_loss_proxy(step)
            if step % self.config.checkpoint_every == 0:
                (self.run_dir / f"ena-mm-step-{step}.ckpt.json").write_text(json.dumps({"step": step, "modality": modality, "loss": loss}, indent=2), encoding="utf-8")
            if step % self.config.eval_every == 0:
                last_eval = run_eval(step)
        report = {"steps": self.config.steps, "eval": last_eval, "device": self.config.device, "modality_flags": {"text": self.config.enable_text, "image": self.config.enable_image, "video": self.config.enable_video}}
        (self.run_dir / "run_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
