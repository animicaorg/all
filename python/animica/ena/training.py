from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from .datasets import DatasetManager
from .models import EnaConfigModel, TrainingManifest, TrainingRunRecord
from .providers import create_model_provider
from .store import EnaStore
from .text import normalize_text, sha3_hex, stable_id, utc_now_iso


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _manifest_from_payload(payload: Dict[str, Any]) -> TrainingManifest:
    if "train" in payload:
        return TrainingManifest.model_validate(payload)
    return TrainingManifest.model_validate(
        {
            "run_name": payload.get("run_name") or Path(payload.get("train_dataset", "run")).stem,
            "backend": payload.get("backend", "command"),
            "base_model": payload.get("base_model", "unknown"),
            "output_dir": payload.get("output_dir"),
            "train": {
                "split": "train",
                "path": payload["train_dataset"],
                "row_count": payload.get("rows", 0),
                "sha256": payload.get("train_sha256", ""),
                "metadata": {},
            },
            "eval": {
                "split": "eval",
                "path": payload["eval_dataset"],
                "row_count": payload.get("eval_rows", 0),
                "sha256": payload.get("eval_sha256", ""),
                "metadata": {},
            }
            if payload.get("eval_dataset")
            else None,
            "test": {
                "split": "test",
                "path": payload["test_dataset"],
                "row_count": payload.get("test_rows", 0),
                "sha256": payload.get("test_sha256", ""),
                "metadata": {},
            }
            if payload.get("test_dataset")
            else None,
            "hyperparameters": payload.get("hyperparameters", {}),
            "launcher": payload.get("launcher", {}),
            "metadata": payload.get("metadata", {}),
            "created_at": payload.get("created_at", utc_now_iso()),
        }
    )


class BaseTrainingRunner:
    backend_name = "base"

    def __init__(self, store: EnaStore, config: EnaConfigModel):
        self.store = store
        self.config = config

    def run(self, manifest: TrainingManifest, output_dir: Path, record: TrainingRunRecord) -> Dict[str, Any]:
        raise NotImplementedError


class CommandTrainingRunner(BaseTrainingRunner):
    backend_name = "command"

    def run(self, manifest: TrainingManifest, output_dir: Path, record: TrainingRunRecord) -> Dict[str, Any]:
        command = list(manifest.launcher.get("command") or [])
        if not command:
            raise RuntimeError("command training backend requires launcher.command in the manifest or CLI override")
        command = [
            item.replace("{manifest}", record.manifest_path).replace("{output_dir}", str(output_dir))
            for item in command
        ]
        completed = subprocess.run(command, capture_output=True, text=True, cwd=str(self.config.workspace))
        log_payload = {
            "command": command,
            "returncode": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
        log_path = output_dir / "command.log.json"
        _dump_json(log_path, log_payload)
        if completed.returncode != 0:
            raise RuntimeError(f"training command failed with exit code {completed.returncode}")
        metrics = {}
        metrics_path = output_dir / "metrics.json"
        if metrics_path.exists():
            metrics = _load_json(metrics_path)
        checkpoints = sorted(str(path) for path in output_dir.glob("**/checkpoint*"))
        return {
            "command": command,
            "metrics": metrics,
            "checkpoint_paths": checkpoints,
            "log_path": str(log_path),
        }


class PythonTransformersTrainingRunner(BaseTrainingRunner):
    backend_name = "python_transformers"

    def run(self, manifest: TrainingManifest, output_dir: Path, record: TrainingRunRecord) -> Dict[str, Any]:
        try:
            from datasets import Dataset as HFDataset  # type: ignore
            from transformers import (  # type: ignore
                AutoModelForCausalLM,
                AutoTokenizer,
                DataCollatorForLanguageModeling,
                Trainer,
                TrainingArguments,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"python_transformers backend requires datasets and transformers: {exc}")

        train_rows = self._load_rows(Path(manifest.train.path))
        eval_rows = self._load_rows(Path(manifest.eval.path)) if manifest.eval else []
        if not train_rows:
            raise RuntimeError("training dataset is empty")

        tokenizer = AutoTokenizer.from_pretrained(manifest.base_model)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(manifest.base_model)

        def render(row: Dict[str, Any]) -> str:
            return (
                "Instruction:\n"
                f"{row.get('input_text', '')}\n\n"
                "Response:\n"
                f"{row.get('output_text', '')}"
            )

        train_dataset = HFDataset.from_list([{"text": render(row)} for row in train_rows])
        eval_dataset = HFDataset.from_list([{"text": render(row)} for row in eval_rows]) if eval_rows else None

        max_length = int(manifest.hyperparameters.get("max_length", 512))

        def tokenize(batch: Dict[str, List[str]]) -> Dict[str, Any]:
            encoded = tokenizer(batch["text"], truncation=True, max_length=max_length)
            encoded["labels"] = list(encoded["input_ids"])
            return encoded

        train_dataset = train_dataset.map(tokenize, batched=True, remove_columns=["text"])
        if eval_dataset is not None:
            eval_dataset = eval_dataset.map(tokenize, batched=True, remove_columns=["text"])

        training_args = TrainingArguments(
            output_dir=str(output_dir / "checkpoints"),
            num_train_epochs=float(manifest.hyperparameters.get("epochs", 1.0)),
            learning_rate=float(manifest.hyperparameters.get("learning_rate", 2e-5)),
            per_device_train_batch_size=int(manifest.hyperparameters.get("batch_size", 1)),
            per_device_eval_batch_size=int(manifest.hyperparameters.get("eval_batch_size", manifest.hyperparameters.get("batch_size", 1))),
            save_strategy="epoch",
            evaluation_strategy="epoch" if eval_dataset is not None else "no",
            logging_steps=int(manifest.hyperparameters.get("logging_steps", 10)),
            report_to=[],
            remove_unused_columns=False,
            fp16=bool(manifest.hyperparameters.get("fp16", False)),
        )
        trainer = Trainer(
            model=model,
            args=training_args,
            train_dataset=train_dataset,
            eval_dataset=eval_dataset,
            data_collator=DataCollatorForLanguageModeling(tokenizer=tokenizer, mlm=False),
        )
        train_result = trainer.train()
        model_dir = output_dir / "model"
        trainer.save_model(str(model_dir))
        tokenizer.save_pretrained(str(model_dir))
        metrics: Dict[str, Any] = dict(train_result.metrics)
        if eval_dataset is not None:
            metrics["eval"] = trainer.evaluate()
        metrics_path = output_dir / "metrics.json"
        _dump_json(metrics_path, metrics)
        checkpoints = sorted(str(path) for path in (output_dir / "checkpoints").glob("checkpoint-*"))
        return {
            "command": ["python_transformers"],
            "metrics": metrics,
            "checkpoint_paths": checkpoints,
            "log_path": str(metrics_path),
        }

    def _load_rows(self, path: Path) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows


class TrainingManager:
    def __init__(self, store: EnaStore, config: EnaConfigModel):
        self.store = store
        self.config = config
        self.datasets = DatasetManager(store, config)

    def prepare(
        self,
        dataset_path: Path,
        *,
        out_path: Path,
        base_model: str,
        backend: str = "command",
        eval_dataset_path: Optional[Path] = None,
        test_dataset_path: Optional[Path] = None,
        auto_split: bool = False,
        launcher: Optional[Dict[str, Any]] = None,
        hyperparameters: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.datasets.training_manifest(
            dataset_path,
            out_path=out_path,
            eval_dataset_path=eval_dataset_path,
            test_dataset_path=test_dataset_path,
            metadata=metadata,
            base_model=base_model,
            backend=backend,
            launcher=launcher,
            hyperparameters=hyperparameters,
            auto_split=auto_split,
        )

    def run(
        self,
        manifest_path: Path,
        *,
        backend: Optional[str] = None,
        command: Optional[Sequence[str]] = None,
    ) -> TrainingRunRecord:
        manifest_payload = _load_json(manifest_path)
        if command:
            manifest_payload.setdefault("launcher", {})
            manifest_payload["launcher"]["command"] = list(command)
        if backend:
            manifest_payload["backend"] = backend
        manifest = _manifest_from_payload(manifest_payload)
        run_id = stable_id("trainrun", str(manifest_path.resolve()), utc_now_iso())
        output_dir = Path(manifest.output_dir or (self.config.default_output_dir / "training" / run_id)).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        record = TrainingRunRecord(
            run_id=run_id,
            status="running",
            backend=manifest.backend,
            manifest_path=str(manifest_path.resolve()),
            base_model=manifest.base_model,
            output_dir=str(output_dir),
            metadata={"manifest": manifest.model_dump(mode="json")},
        )
        self.store.save_training_run(record)

        runner = self._runner(manifest.backend)
        try:
            result = runner.run(manifest, output_dir, record)
            record.status = "completed"
            record.updated_at = utc_now_iso()
            record.command = list(result.get("command", []))
            record.checkpoint_paths = list(result.get("checkpoint_paths", []))
            record.metrics = dict(result.get("metrics", {}))
            artifact_ids = self._materialize_run_artifacts(record, output_dir, result)
            record.artifact_ids = artifact_ids
            self.store.add_memory(
                kind="training_run",
                content=f"training run {record.run_id} completed for base model {record.base_model}",
                source=record.manifest_path,
                confidence=0.9,
                metadata={"run_id": record.run_id, "backend": record.backend},
            )
        except Exception as exc:  # noqa: BLE001
            record.status = "failed"
            record.updated_at = utc_now_iso()
            record.error = str(exc)
            self._materialize_run_artifacts(record, output_dir, {"error": str(exc), "command": record.command})
            self.store.save_training_run(record)
            raise
        self.store.save_training_run(record)
        return record

    def eval(
        self,
        *,
        run_id: Optional[str] = None,
        manifest_path: Optional[Path] = None,
        dataset_path: Optional[Path] = None,
        model_provider: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        record = self.store.get_training_run(run_id) if run_id else None
        if record is not None:
            manifest_path = Path(record.manifest_path)
        if manifest_path is None:
            raise ValueError("train eval requires --run-id or --manifest")
        manifest = _manifest_from_payload(_load_json(manifest_path))
        target_dataset = Path(dataset_path or (manifest.eval.path if manifest.eval else manifest.train.path))
        provider = create_model_provider(self.config, provider_name=model_provider)
        if model:
            provider.config = provider.config.model_copy(update={"model": model})

        total = 0
        exact = 0
        overlap_scores: List[float] = []
        with target_dataset.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                prompt = row.get("input_text") or row.get("prompt") or ""
                expected = normalize_text(row.get("output_text") or row.get("expected") or "")
                if not prompt:
                    continue
                total += 1
                response = provider.chat([{"role": "user", "content": prompt}])
                actual = normalize_text(response.content)
                if actual == expected:
                    exact += 1
                overlap_scores.append(self._token_overlap(actual, expected))
        summary = {
            "run_id": record.run_id if record else None,
            "dataset": str(target_dataset),
            "rows": total,
            "exact_match_rate": (exact / total) if total else 0.0,
            "token_overlap": (sum(overlap_scores) / len(overlap_scores)) if overlap_scores else 0.0,
            "model_provider": model_provider or self.config.default_model_provider,
            "model": provider.config.model,
            "created_at": utc_now_iso(),
        }
        if record is not None:
            record.eval_report = summary
            record.updated_at = utc_now_iso()
            artifact = self.store.put_artifact(
                "training_eval_report",
                json.dumps(summary, indent=2),
                metadata={"run_id": record.run_id},
                suffix=".json",
            )
            record.artifact_ids = sorted(set(record.artifact_ids + [artifact.artifact_id]))
            self.store.save_training_run(record)
        return summary

    def status(self, run_id: str) -> Optional[TrainingRunRecord]:
        return self.store.get_training_run(run_id)

    def list_runs(self, limit: int = 100) -> List[TrainingRunRecord]:
        return self.store.list_training_runs(limit=limit)

    def export(self, run_id: str, out_path: Path) -> Dict[str, Any]:
        record = self.store.get_training_run(run_id)
        if record is None:
            raise ValueError(f"training run not found: {run_id}")
        out_path = out_path.resolve()
        export_payload = {
            "run": record.model_dump(mode="json"),
            "manifest": _load_json(Path(record.manifest_path)),
        }
        if out_path.suffix.lower() == ".json":
            _dump_json(out_path, export_payload)
            return {"path": str(out_path), "format": "json"}

        out_path.mkdir(parents=True, exist_ok=True)
        shutil.copy2(record.manifest_path, out_path / Path(record.manifest_path).name)
        summary_path = out_path / "training_run.json"
        _dump_json(summary_path, export_payload)
        return {"path": str(out_path), "format": "directory"}

    def _runner(self, backend: str) -> BaseTrainingRunner:
        if backend == "command":
            return CommandTrainingRunner(self.store, self.config)
        if backend in {"python_transformers", "local"}:
            return PythonTransformersTrainingRunner(self.store, self.config)
        raise ValueError(f"unsupported training backend: {backend}")

    def _materialize_run_artifacts(
        self,
        record: TrainingRunRecord,
        output_dir: Path,
        result: Dict[str, Any],
    ) -> List[str]:
        artifacts: List[str] = []
        summary_artifact = self.store.put_artifact(
            "training_run_summary",
            json.dumps(
                {
                    "run_id": record.run_id,
                    "backend": record.backend,
                    "manifest_path": record.manifest_path,
                    "output_dir": str(output_dir),
                    "result": result,
                },
                indent=2,
            ),
            metadata={"run_id": record.run_id},
            suffix=".json",
        )
        artifacts.append(summary_artifact.artifact_id)

        file_manifest: List[Dict[str, Any]] = []
        if output_dir.exists():
            for path in sorted(output_dir.rglob("*")):
                if path.is_file():
                    file_manifest.append(
                        {
                            "path": str(path.relative_to(output_dir)),
                            "sha256": sha3_hex(path.read_bytes()),
                            "size_bytes": path.stat().st_size,
                        }
                    )
        manifest_artifact = self.store.put_artifact(
            "training_output_manifest",
            json.dumps(file_manifest, indent=2),
            metadata={"run_id": record.run_id},
            suffix=".json",
        )
        artifacts.append(manifest_artifact.artifact_id)
        return artifacts

    def _token_overlap(self, actual: str, expected: str) -> float:
        actual_tokens = set(actual.lower().split())
        expected_tokens = set(expected.lower().split())
        if not actual_tokens or not expected_tokens:
            return 0.0
        return len(actual_tokens & expected_tokens) / len(expected_tokens)
