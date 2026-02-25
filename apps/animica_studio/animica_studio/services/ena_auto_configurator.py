from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from animica_studio.models.training_models import TrainingConfig
from animica_studio.services.dataset_profile import DatasetProfile
from animica_studio.services.hardware_probe import HardwareProfile


@dataclass
class Recommendation:
    config: TrainingConfig
    rationale: str
    estimated_runtime_minutes: int
    memory_risk: str


class EnaAutoConfigurator:
    QUALITY_EPOCHS = {"fast": 0.4, "balanced": 1.0, "quality": 3.0, "max_quality": 6.0}

    @classmethod
    def recommend(
        cls,
        config_in: TrainingConfig,
        hw: HardwareProfile,
        ds: DatasetProfile,
        quality_level: str,
    ) -> TrainingConfig:
        rec = cls.build_recommendation(config_in, hw, ds, quality_level)
        out = rec.config
        out.auto_config_rationale = rec.rationale
        out.estimated_runtime_minutes = rec.estimated_runtime_minutes
        out.memory_risk = rec.memory_risk
        out.hardware_profile = hw.to_dict()
        out.dataset_profile = ds.to_dict()
        out.quality_level = quality_level
        out.smart_defaults = True
        return out

    @classmethod
    def build_recommendation(
        cls, config_in: TrainingConfig, hw: HardwareProfile, ds: DatasetProfile, quality_level: str
    ) -> Recommendation:
        q = (quality_level or "balanced").lower().replace(" ", "_")
        epochs = cls.QUALITY_EPOCHS.get(q, 1.0)
        docs = max(1, ds.document_count)
        tokenish = max(1000, int(ds.avg_chars * docs / 4))
        steps_per_epoch = max(100, tokenish // 4096)
        total_steps = int(max(100, steps_per_epoch * epochs))

        available_ram = max(1.0, hw.ram_available_gib)
        per_batch_gib = 0.45 if hw.gpu_name else 0.2
        safe_batch = max(1, int(available_ram * 0.5 / per_batch_gib))
        batch = min(64, safe_batch)
        eff_target = 64 if q in {"quality", "max_quality"} else 32
        grad_accum = max(1, (eff_target + batch - 1) // batch)

        lr = 2e-5 * (batch * grad_accum / 32)
        lr = min(8e-5, max(8e-6, lr))
        warmup = min(200, max(50, int(total_steps * 0.03)))

        threads = max(1, hw.physical_cores - 1)
        if q == "max_quality":
            threads = max(1, hw.physical_cores)
        workers = max(1, min(threads, hw.logical_cores // 2))

        ckpt = max(50, min(1000, total_steps // 10))
        evl = max(25, min(500, total_steps // 20))
        precision = "fp32"
        device = "cpu"
        if hw.gpu_name:
            device = "cuda"
            precision = "bf16" if hw.gpu_supports_bf16 else "fp16"

        est_runtime = max(5, int(total_steps / max(1.0, hw.logical_cores * 2.5)))
        risk = "low" if available_ram > 6 else "medium"
        if available_ram < 3:
            risk = "high"

        cfg = TrainingConfig.from_dict(config_in.to_dict())
        cfg.total_steps = total_steps
        cfg.iterations = total_steps
        cfg.epochs = None
        cfg.batch_size = batch
        cfg.gradient_accumulation_steps = grad_accum
        cfg.learning_rate = lr
        cfg.optimizer = "adamw"
        cfg.eval_interval_steps = evl
        cfg.checkpoint_interval_steps = ckpt
        cfg.num_workers = workers
        cfg.threads = threads
        cfg.device = device
        cfg.precision = precision
        cfg.warmup_steps = warmup
        cfg.auto_tune_warmup_steps = warmup
        cfg.resume_checkpoint = cfg.resume_checkpoint
        rationale = (
            f"Because you have {hw.physical_cores} physical cores and {hw.ram_total_gib:.1f}GiB RAM, "
            f"ENA selected batch={batch} with grad_accum={grad_accum} (effective batch={batch * grad_accum}), "
            f"steps={total_steps}, lr={lr:.2e}, threads={threads}. Dataset has {ds.document_count} docs "
            f"({ds.bytes_total / (1024**2):.2f}MiB), so eval/checkpoints are set for steady feedback without thrashing."
        )
        return Recommendation(cfg, rationale, est_runtime, risk)
