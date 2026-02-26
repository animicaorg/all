from __future__ import annotations

from pathlib import Path

from animica_studio.ena_mm.infer.chat import generate_text
from animica_studio.ena_mm.infer.image_gen import generate_image
from animica_studio.ena_mm.infer.video_gen import generate_video_frames
from animica_studio.ena_mm.model.checkpoint_io import read_checkpoint_package, write_checkpoint_package
from animica_studio.ena_mm.train.config import EnaMMTrainConfig
from animica_studio.ena_mm.train.trainer import EnaMMTrainer


def test_ena_mm_tiny_local_end_to_end(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    report = EnaMMTrainer(EnaMMTrainConfig(steps=100, checkpoint_every=50, eval_every=50, enable_video=False), str(run_dir)).train()
    assert report["steps"] == 100

    package_dir = run_dir / "pkg"
    write_checkpoint_package(str(package_dir), {"run_report.json": (run_dir / "run_report.json").read_bytes()}, {"modality_flags": {"text": True, "image": True, "video": False}})
    manifest = read_checkpoint_package(str(package_dir))
    assert manifest["kind"] == "ena-mm-package"

    text = generate_text("hello")
    assert "ENA-MM" in text

    image = generate_image("cat", 64, 64, 1)
    assert image.size == (64, 64)

    frames = generate_video_frames("cat", 64, 64, 16, 1)
    assert len(frames) == 16
