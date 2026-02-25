from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class TrainingConfig:
    run_name: str = "ena-train"
    total_steps: int | None = None
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
    training_mode: str = "local"
    services_url: str = ""
    api_key: str = ""
    warmup_steps: int | None = None
    auto_tune_warmup_steps: int | None = None
    quality_level: str = "balanced"
    smart_defaults: bool = True
    auto_config_rationale: str = ""
    estimated_runtime_minutes: int | None = None
    memory_risk: str = "unknown"
    hardware_profile: dict[str, Any] | None = None
    dataset_profile: dict[str, Any] | None = None
    dataset_version_id: str | None = None

    def effective_iterations(self) -> int | None:
        if self.total_steps and int(self.total_steps) > 0:
            return int(self.total_steps)
        if self.iterations and int(self.iterations) > 0:
            return int(self.iterations)
        return None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        steps = self.effective_iterations()
        if steps:
            out["total_steps"] = steps
            out["iterations"] = steps
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "TrainingConfig":
        if not isinstance(data, dict):
            return cls()
        merged = cls().to_dict()
        # Backward compatibility for older saved keys.
        if "ena_submit_mode" in data and "training_mode" not in data:
            data = {**data, "training_mode": data.get("ena_submit_mode")}
        if "aicf_services_url" in data and "services_url" not in data:
            data = {**data, "services_url": data.get("aicf_services_url")}
        if "total_steps" not in data and "iterations" in data:
            data = {**data, "total_steps": data.get("iterations")}
        if "iterations" not in data and "total_steps" in data:
            data = {**data, "iterations": data.get("total_steps")}
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
