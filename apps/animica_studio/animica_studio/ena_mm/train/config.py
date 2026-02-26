from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class EnaMMTrainConfig:
    enable_text: bool = True
    enable_image: bool = True
    enable_video: bool = False
    ratio_text: int = 70
    ratio_image: int = 20
    ratio_video: int = 10
    device: str = "cpu"
    steps: int = 100
    checkpoint_every: int = 50
    eval_every: int = 50
    batch_size: int = 4
    grad_accum: int = 1
    threads: int = 4
