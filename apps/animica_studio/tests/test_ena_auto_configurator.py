from __future__ import annotations

from animica_studio.models.training_models import TrainingConfig
from animica_studio.services.dataset_profile import DatasetProfile
from animica_studio.services.ena_auto_configurator import EnaAutoConfigurator
from animica_studio.services.hardware_probe import HardwareProfile


def test_auto_configurator_balanced_cpu() -> None:
    cfg = TrainingConfig(dataset_path="/tmp/missing", output_dir="/tmp")
    hw = HardwareProfile(
        logical_cores=8,
        physical_cores=4,
        ram_total_gib=16,
        ram_available_gib=12,
        disk_free_gib=100,
        gpu_name=None,
    )
    ds = DatasetProfile(bytes_total=1_000_000, document_count=500, avg_chars=800, dedup_ratio=0.95, language_mix={"en": 1.0})
    out = EnaAutoConfigurator.recommend(cfg, hw, ds, "balanced")
    assert out.iterations and out.iterations >= 100
    assert out.batch_size >= 1
    assert out.threads == 3
    assert out.device == "cpu"
    assert "Because you have" in out.auto_config_rationale


def test_auto_configurator_gpu_precision() -> None:
    cfg = TrainingConfig()
    hw = HardwareProfile(
        logical_cores=16,
        physical_cores=8,
        ram_total_gib=64,
        ram_available_gib=48,
        disk_free_gib=100,
        gpu_name="RTX",
        gpu_supports_bf16=True,
        gpu_supports_fp16=True,
    )
    ds = DatasetProfile(bytes_total=100, document_count=10, avg_chars=20, dedup_ratio=1.0, language_mix={"en": 1.0})
    out = EnaAutoConfigurator.recommend(cfg, hw, ds, "max_quality")
    assert out.device == "cuda"
    assert out.precision == "bf16"
    assert out.threads == 8
