from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import DatasetRecord, DatasetSplitRecord, EnaConfigModel, TrainingManifest, TrainingSample
from .store import EnaStore
from .text import hamming_distance, normalize_text, sha256_hex, simhash64, stable_id


class DatasetManager:
    def __init__(self, store: EnaStore, config: EnaConfigModel):
        self.store = store
        self.config = config

    def register(self, path: Path, *, kind: str, metadata: Optional[Dict[str, Any]] = None) -> DatasetRecord:
        path = path.resolve()
        row_count = sum(1 for line in path.open("r", encoding="utf-8") if line.strip())
        sha = sha256_hex(path.read_bytes())
        record = DatasetRecord(
            dataset_id=stable_id("dataset", str(path), sha),
            kind=kind,
            path=str(path),
            row_count=row_count,
            sha256=sha,
            metadata=metadata or {},
        )
        return self.store.save_dataset(record)

    def list(self) -> List[DatasetRecord]:
        return self.store.list_datasets()

    def normalize(
        self,
        input_path: Path,
        out_path: Path,
        *,
        task_type: str = "summarize",
    ) -> Dict[str, Any]:
        rows_out: List[Dict[str, Any]] = []
        with input_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                text = normalize_text(row.get("content_text") or row.get("content") or row.get("answer") or "")
                if not text:
                    continue
                title = normalize_text(row.get("title") or row.get("question") or row.get("path") or f"sample {line_number}")
                output_text = row.get("answer") or row.get("summary") or text[:600]
                sample = TrainingSample(
                    sample_id=stable_id("train", title, str(line_number)),
                    task_type=task_type,
                    input_text=title,
                    output_text=normalize_text(output_text),
                    quality_score=float(row.get("quality_score", 1.0)),
                    source_refs=[value for value in [row.get("canonical_url"), row.get("url"), row.get("path")] if value],
                    metadata={"line_number": line_number, "record_type": row.get("record_type")},
                )
                rows_out.append(sample.model_dump(mode="json"))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as handle:
            for row in rows_out:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        record = self.register(out_path, kind="training_sample", metadata={"source_path": str(input_path.resolve())})
        return {"dataset_id": record.dataset_id, "path": str(out_path), "rows": len(rows_out)}

    def dedupe(
        self,
        input_path: Path,
        out_path: Path,
        *,
        near_duplicate_distance: int = 3,
    ) -> Dict[str, Any]:
        seen_hashes = set()
        seen_simhashes: List[int] = []
        kept = 0
        dropped = 0
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with input_path.open("r", encoding="utf-8") as source, out_path.open("w", encoding="utf-8") as dest:
            for line in source:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                text = normalize_text(row.get("content_text") or row.get("content") or row.get("output_text") or json.dumps(row, ensure_ascii=False))
                exact = sha256_hex(text)
                if exact in seen_hashes:
                    dropped += 1
                    continue
                signature = simhash64(text)
                if any(hamming_distance(signature, existing) <= near_duplicate_distance for existing in seen_simhashes):
                    dropped += 1
                    continue
                seen_hashes.add(exact)
                seen_simhashes.append(signature)
                dest.write(json.dumps(row, ensure_ascii=False) + "\n")
                kept += 1
        record = self.register(out_path, kind="deduped", metadata={"source_path": str(input_path.resolve())})
        return {"dataset_id": record.dataset_id, "kept": kept, "dropped": dropped, "path": str(out_path)}

    def shard(self, input_path: Path, out_dir: Path, *, rows_per_shard: int = 500) -> Dict[str, Any]:
        out_dir.mkdir(parents=True, exist_ok=True)
        shard_paths: List[str] = []
        shard_index = 0
        row_index = 0
        current_handle = None
        try:
            with input_path.open("r", encoding="utf-8") as source:
                for line in source:
                    line = line.strip()
                    if not line:
                        continue
                    if row_index % rows_per_shard == 0:
                        if current_handle:
                            current_handle.close()
                        shard_path = out_dir / f"{input_path.stem}.shard{shard_index:04d}.jsonl"
                        current_handle = shard_path.open("w", encoding="utf-8")
                        shard_paths.append(str(shard_path))
                        shard_index += 1
                    assert current_handle is not None
                    current_handle.write(line + "\n")
                    row_index += 1
        finally:
            if current_handle:
                current_handle.close()
        return {"shards": shard_paths, "rows": row_index}

    def split_dataset(
        self,
        input_path: Path,
        out_dir: Path,
        *,
        train_ratio: float = 0.8,
        eval_ratio: float = 0.1,
        test_ratio: float = 0.1,
    ) -> Dict[str, Any]:
        if abs((train_ratio + eval_ratio + test_ratio) - 1.0) > 1e-6:
            raise ValueError("train/eval/test ratios must sum to 1.0")
        out_dir.mkdir(parents=True, exist_ok=True)
        split_paths = {
            "train": out_dir / f"{input_path.stem}.train.jsonl",
            "eval": out_dir / f"{input_path.stem}.eval.jsonl",
            "test": out_dir / f"{input_path.stem}.test.jsonl",
        }
        counts = {"train": 0, "eval": 0, "test": 0}
        handles = {name: path.open("w", encoding="utf-8") for name, path in split_paths.items()}
        try:
            with input_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    row_key = row.get("sample_id") or row.get("item_id") or sha256_hex(line)
                    bucket = int(sha256_hex(str(row_key))[:8], 16) / 0xFFFFFFFF
                    if bucket < train_ratio:
                        split_name = "train"
                    elif bucket < train_ratio + eval_ratio:
                        split_name = "eval"
                    else:
                        split_name = "test"
                    handles[split_name].write(line + "\n")
                    counts[split_name] += 1
        finally:
            for handle in handles.values():
                handle.close()
        return {
            "train": str(split_paths["train"]),
            "eval": str(split_paths["eval"]),
            "test": str(split_paths["test"]),
            "counts": counts,
        }

    def validate(self, path: Path, *, schema_name: str = "training_sample") -> Dict[str, Any]:
        errors: List[str] = []
        valid = 0
        with path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                try:
                    if schema_name == "training_sample":
                        TrainingSample.model_validate(row)
                    valid += 1
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"line {line_number}: {exc}")
        return {"path": str(path), "valid_rows": valid, "errors": errors, "ok": not errors}

    def export(
        self,
        input_path: Path,
        out_path: Path,
        *,
        format_name: str = "jsonl",
    ) -> Dict[str, Any]:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if format_name == "jsonl":
            out_path.write_bytes(input_path.read_bytes())
            return {"path": str(out_path), "format": "jsonl"}
        if format_name == "parquet":
            try:
                import pyarrow as pa  # type: ignore
                import pyarrow.parquet as pq  # type: ignore
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"parquet export requires pyarrow: {exc}")
            rows = []
            with input_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if line:
                        rows.append(json.loads(line))
            table = pa.Table.from_pylist(rows)
            pq.write_table(table, out_path)
            return {"path": str(out_path), "format": "parquet", "rows": len(rows)}
        raise ValueError(f"unsupported export format: {format_name}")

    def training_manifest(
        self,
        dataset_path: Path,
        *,
        out_path: Path,
        eval_dataset_path: Optional[Path] = None,
        test_dataset_path: Optional[Path] = None,
        metadata: Optional[Dict[str, Any]] = None,
        base_model: str = "unknown",
        backend: str = "command",
        launcher: Optional[Dict[str, Any]] = None,
        hyperparameters: Optional[Dict[str, Any]] = None,
        auto_split: bool = False,
        split_ratios: Tuple[float, float, float] = (0.8, 0.1, 0.1),
    ) -> Dict[str, Any]:
        dataset_path = dataset_path.resolve()
        split_result: Optional[Dict[str, Any]] = None
        if auto_split and (eval_dataset_path is None or test_dataset_path is None):
            split_dir = out_path.parent / f"{out_path.stem}.splits"
            split_result = self.split_dataset(
                dataset_path,
                split_dir,
                train_ratio=split_ratios[0],
                eval_ratio=split_ratios[1],
                test_ratio=split_ratios[2],
            )
            dataset_path = Path(split_result["train"])
            eval_dataset_path = Path(split_result["eval"])
            test_dataset_path = Path(split_result["test"])

        train_record = self._split_record("train", dataset_path)
        eval_record = self._split_record("eval", eval_dataset_path) if eval_dataset_path else None
        test_record = self._split_record("test", test_dataset_path) if test_dataset_path else None

        manifest = TrainingManifest(
            run_name=out_path.stem,
            backend=backend,
            base_model=base_model,
            output_dir=str((self.config.default_output_dir / "training" / out_path.stem).resolve()),
            train=train_record,
            eval=eval_record,
            test=test_record,
            hyperparameters=hyperparameters or {},
            launcher=launcher or {},
            metadata={
                **(metadata or {}),
                "split_result": split_result or {},
            },
        )
        payload = manifest.model_dump(mode="json")
        payload.update(
            {
                "train_dataset": train_record.path,
                "train_sha256": train_record.sha256,
                "eval_dataset": eval_record.path if eval_record else None,
                "eval_sha256": eval_record.sha256 if eval_record else None,
                "test_dataset": test_record.path if test_record else None,
                "test_sha256": test_record.sha256 if test_record else None,
                "rows": train_record.row_count,
            }
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return payload

    def _split_record(self, split: str, path: Optional[Path]) -> DatasetSplitRecord:
        assert path is not None
        resolved = path.resolve()
        return DatasetSplitRecord(
            split=split,  # type: ignore[arg-type]
            path=str(resolved),
            row_count=sum(1 for line in resolved.open("r", encoding="utf-8") if line.strip()),
            sha256=sha256_hex(resolved.read_bytes()),
            metadata={},
        )
