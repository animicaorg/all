from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TrainerConfig:
    total_steps: int = 1000
    batch_size: int = 8
    seq_len: int = 128
    lr: float = 3e-4
    warmup_steps: int = 50
    grad_accum_steps: int = 1
    eval_interval: int = 100
    checkpoint_interval: int = 100
    keep_last_k: int = 3
    device: str = "cpu"
