from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class TrainingConfig:
    run_name: str = "ena-train"
    iterations: int | None = 10000
    epochs: float | int | None = None
    batch_size: int = 4
    learning_rate: float = 2e-5
    optimizer: str = "adamw"
    dataset_path: str = ""
    dataset_id: str | None = None
    base_model: str = ""
    output_dir: str = "./ena-training-runs"
    eval_interval_steps: int = 100
    checkpoint_interval_steps: int = 500
    max_runtime_minutes: int | None = None
    early_stop_patience: int | None = None
    device: str = "auto"
    gpu_id: int | None = None
    num_workers: int | None = None
    threads: int | None = None
    gradient_accumulation_steps: int | None = None
    seed: int | None = None
    precision: str = "fp32"
    lora_enabled: bool = False
    lora_rank: int | None = None
    resume_checkpoint: str | None = None
    submit_to_aicf: bool = False
    budget_anm: str = "10"
    ena_submit_mode: str = "local"
    aicf_services_url: str = ""

    def effective_iterations(self) -> int | None:
        if self.iterations and int(self.iterations) > 0:
            return int(self.iterations)
        return None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TrainingConfig":
        if not isinstance(data, dict):
            return cls()
        merged = cls().to_dict()
        merged.update(data)
        return cls(**merged)

    def ensure_output_dir(self) -> Path:
        out = Path(self.output_dir).expanduser()
        out.mkdir(parents=True, exist_ok=True)
        return out


@dataclass
class TrainingMetrics:
    current_step: int = 0
    total_steps: int | None = None
    loss: float | None = None
    steps_per_sec: float | None = None
    eval_metrics: dict[str, float] | None = None
    last_checkpoint_path: str | None = None


@dataclass
class TrainingRun:
    run_id: str
    started_at: float
    config: dict[str, Any]
    status: str
    job_id: str | None = None
    ended_at: float | None = None
    last_metrics: dict[str, Any] | None = None
    error: str | None = None
