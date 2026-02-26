from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal

from animica_studio.ena_mm.data.builders.auto_builders import build_text_dataset, validate_user_dataset
from animica_studio.ena_mm.data.manifest import MultimodalManifest, ProvenanceEntry
from animica_studio.ena_mm.model.checkpoint_io import write_checkpoint_package
from animica_studio.ena_mm.train.config import EnaMMTrainConfig
from animica_studio.ena_mm.train.trainer import EnaMMTrainer
from animica_studio.services.ena_full_auto_engine import _put_blob_with_strategy, _rpc_call_with_backoff
from animica_studio.services.rpc_client import RpcClient


class MMState(str, Enum):
    IDLE = "idle"
    BOOTSTRAP_DATASETS = "bootstrap_datasets"
    TRAIN_CHUNK = "train_chunk"
    EVAL = "eval"
    CHECKPOINT = "checkpoint"
    PUBLISH_DA = "publish_da"
    SYNC_DA = "sync_da"
    ERROR = "error"


@dataclass(slots=True)
class EnaMMFullAutoConfig:
    enabled: bool = False
    enable_text: bool = True
    enable_image: bool = True
    enable_video: bool = False
    text_dataset: str = ""
    image_dataset: str = ""
    video_dataset: str = ""
    ratio_text: int = 70
    ratio_image: int = 20
    ratio_video: int = 10
    device: str = "cpu"
    steps_per_cycle: int = 100
    da_namespace: int = 0
    model_channel: str = "ena-mm-main"


class EnaMultimodalFullAutoEngine(QObject):
    stateChanged = Signal(str, str)
    logLine = Signal(str, str)

    def __init__(self, rpc_url: str, storage_dir: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.rpc_url = rpc_url
        self.storage = Path(storage_dir)
        self.storage.mkdir(parents=True, exist_ok=True)
        self.config = EnaMMFullAutoConfig()
        self.state = MMState.IDLE
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._run_cycle)
        self._running = False
        self._single_flight = False
        self._backoff = 2

    def apply_config(self, cfg: EnaMMFullAutoConfig) -> None:
        self.config = cfg

    def start(self) -> None:
        self._running = True
        self._schedule(0)

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        self.state = MMState.IDLE
        self.stateChanged.emit(self.state.value, "stopped")

    def _schedule(self, sec: int) -> None:
        self._timer.start(max(0, sec) * 1000)

    def _transition(self, state: MMState, detail: str) -> None:
        self.state = state
        self.stateChanged.emit(state.value, detail)

    def _run_cycle(self) -> None:
        if not self._running or self._single_flight or not self.config.enabled:
            return
        self._single_flight = True
        try:
            self._bootstrap_datasets()
            run_dir = self._train_chunk()
            package_dir = self._checkpoint_package(run_dir)
            self._publish_da(package_dir)
            self._sync_da(package_dir)
            self._backoff = 2
            self._schedule(1)
        except Exception as exc:  # noqa: BLE001
            self._transition(MMState.ERROR, str(exc))
            self.logLine.emit("error", str(exc))
            self._schedule(self._backoff)
            self._backoff = min(60, self._backoff * 2)
        finally:
            self._single_flight = False

    def _bootstrap_datasets(self) -> None:
        self._transition(MMState.BOOTSTRAP_DATASETS, "building datasets")
        droot = self.storage / "datasets"
        droot.mkdir(parents=True, exist_ok=True)
        text_path = self.config.text_dataset or (build_text_dataset(str(droot / "text")) if self.config.enable_text else "")
        if self.config.enable_image and self.config.image_dataset:
            ok, msg = validate_user_dataset(self.config.image_dataset, "image")
            if not ok:
                raise ValueError(msg)
        if self.config.enable_video and self.config.video_dataset:
            ok, msg = validate_user_dataset(self.config.video_dataset, "video")
            if not ok:
                raise ValueError(msg)

        manifest = MultimodalManifest(
            text_path=text_path,
            image_path=self.config.image_dataset,
            video_path=self.config.video_dataset,
            provenance=[
                ProvenanceEntry(modality="text", source="curated-local", license="mixed-allowed", user_provided=False),
                ProvenanceEntry(modality="image", source=self.config.image_dataset or "none", license="user-provided", user_provided=True),
                ProvenanceEntry(modality="video", source=self.config.video_dataset or "none", license="user-provided", user_provided=True),
            ],
        )
        manifest.save(droot / "manifest.json")

    def _train_chunk(self) -> Path:
        self._transition(MMState.TRAIN_CHUNK, "training mixed batches")
        run_dir = self.storage / "runs" / time.strftime("%Y%m%d-%H%M%S")
        cfg = EnaMMTrainConfig(
            enable_text=self.config.enable_text,
            enable_image=self.config.enable_image,
            enable_video=(self.config.enable_video and self.config.device == "cuda"),
            ratio_text=self.config.ratio_text,
            ratio_image=self.config.ratio_image,
            ratio_video=self.config.ratio_video,
            device=self.config.device,
            steps=self.config.steps_per_cycle,
        )
        if self.config.device != "cuda" and self.config.enable_video:
            self.logLine.emit("warning", "GPU recommended for video; video training disabled on CPU.")
        report = EnaMMTrainer(cfg, str(run_dir)).train()
        self._transition(MMState.EVAL, f"eval text_ppl={report.get('eval', {}).get('text_ppl', '-')}")
        return run_dir

    def _checkpoint_package(self, run_dir: Path) -> Path:
        self._transition(MMState.CHECKPOINT, "packaging checkpoint")
        payloads = {
            "run_report.json": (run_dir / "run_report.json").read_bytes(),
        }
        for ckpt in sorted(run_dir.glob("ena-mm-step-*.ckpt.json"))[-2:]:
            payloads[ckpt.name] = ckpt.read_bytes()
        meta = {
            "model_family": "ENA-MM",
            "single_selectable_model": True,
            "modality_flags": {"text": self.config.enable_text, "image": self.config.enable_image, "video": self.config.enable_video and self.config.device == "cuda"},
            "base_choices": {"cpu_friendly": ["toy-mm-small"], "gpu_recommended": ["toy-mm-video"]},
        }
        package_dir = run_dir / "ena-mm-package"
        write_checkpoint_package(str(package_dir), payloads, meta)
        return package_dir

    def _publish_da(self, package_dir: Path) -> None:
        self._transition(MMState.PUBLISH_DA, "publishing package to DA")
        with RpcClient(self.rpc_url, connect_timeout=3.0, read_timeout=20.0, max_retries=1) as c:
            reg = c.registry()
            blobs: dict[str, str] = {}
            logs: list[tuple[str, str]] = []
            status_method = reg.resolve_any(["da.getStatus", "da_getStatus", "da.status", "da_status"])
            status = _rpc_call_with_backoff(c, status_method, {}) if status_method else {}
            for file in sorted((package_dir / "blobs").glob("*.blob")):
                commitment = _put_blob_with_strategy(c, reg, {"da_namespace": self.config.da_namespace}, file.read_bytes(), logs, status if isinstance(status, dict) else {})
                blobs[file.name] = commitment
            manifest_bytes = (package_dir / "package_manifest.json").read_bytes()
            manifest_commitment = _put_blob_with_strategy(c, reg, {"da_namespace": self.config.da_namespace}, manifest_bytes, logs, status if isinstance(status, dict) else {})
            pointer = json.dumps({"channel": self.config.model_channel, "manifest_commitment": manifest_commitment, "blob_commitments": blobs}, indent=2).encode("utf-8")
            pointer_commitment = _put_blob_with_strategy(c, reg, {"da_namespace": self.config.da_namespace}, pointer, logs, status if isinstance(status, dict) else {})
            (package_dir / "channel_pointer.json").write_text(json.dumps({"pointer_commitment": pointer_commitment}, indent=2), encoding="utf-8")

    def _sync_da(self, package_dir: Path) -> None:
        self._transition(MMState.SYNC_DA, "sync latest package")
        install_dir = self.storage / "installed" / self.config.model_channel
        install_dir.mkdir(parents=True, exist_ok=True)
        (install_dir / "package_manifest.json").write_bytes((package_dir / "package_manifest.json").read_bytes())
