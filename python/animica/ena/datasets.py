"""
animica.ena.datasets
====================

Dataset preparation for the ENA training flow: ingest → normalize → dedupe →
split → validate → export, plus deterministic content hashing used by training
manifests and useful-work receipts.

The working format is JSONL (one record per line). ``normalize`` coerces
heterogeneous raw records into a small set of training-sample shapes:

* ``{"text": ...}``                       (plain LM corpus)
* ``{"prompt": ..., "response": ...}``     (instruction / SFT pairs)
* ``{"prompt": ..., "chosen": ..., "rejected": ...}``  (preference / DPO)

Heavy formats (parquet) import pandas/pyarrow lazily so the core stays light.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from .errors import DatasetError
from .models import new_uuid, now_ts

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]")


# ---------------------------------------------------------------------------
# IO + hashing
# ---------------------------------------------------------------------------

def read_jsonl(path: str | Path) -> Iterator[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        raise DatasetError(f"dataset not found: {p}")
    with p.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError as exc:
                raise DatasetError(f"{p}:{lineno}: invalid JSON line: {exc}") from exc


def write_jsonl(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with p.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def row_count(path: str | Path) -> int:
    return sum(1 for _ in read_jsonl(path))


# ---------------------------------------------------------------------------
# Pipeline stages
# ---------------------------------------------------------------------------

def ingest(path: str | Path, kind: str, store=None) -> dict[str, Any]:
    """Register a raw dataset file in the ENA store (if provided)."""
    p = Path(path)
    rec = {
        "dataset_id": "ds-" + new_uuid()[:16],
        "kind": kind,
        "path": str(p.resolve()),
        "sha256": sha256_file(p),
        "row_count": row_count(p),
        "created_at": now_ts(),
    }
    if store is not None:
        store.upsert_dataset(rec)
    return rec


def _normalize_record(rec: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Map a raw record to a training-sample shape, or None to drop it."""
    # Preference triples first (most specific).
    if "chosen" in rec and "rejected" in rec:
        prompt = rec.get("prompt") or rec.get("instruction") or rec.get("input") or ""
        return {"prompt": str(prompt), "chosen": str(rec["chosen"]),
                "rejected": str(rec["rejected"])}
    # Instruction / SFT pair.
    for pk in ("prompt", "instruction", "input", "question"):
        if pk in rec and rec[pk]:
            for rk in ("response", "output", "completion", "answer"):
                if rk in rec and rec[rk]:
                    return {"prompt": str(rec[pk]), "response": str(rec[rk])}
    # Plain text corpus.
    for tk in ("text", "content", "body"):
        if tk in rec and rec[tk]:
            return {"text": str(rec[tk])}
    # Fallback: stringify the whole record.
    flat = " ".join(str(v) for v in rec.values() if isinstance(v, (str, int, float)))
    return {"text": flat.strip()} if flat.strip() else None


def normalize(in_path: str | Path, out_path: str | Path) -> dict[str, Any]:
    rows = []
    dropped = 0
    for rec in read_jsonl(in_path):
        norm = _normalize_record(rec)
        if norm is None:
            dropped += 1
            continue
        rows.append(norm)
    written = write_jsonl(out_path, rows)
    return {"in": str(in_path), "out": str(out_path), "rows": written,
            "dropped": dropped, "sha256": sha256_file(out_path)}


def _dedupe_key(rec: dict[str, Any]) -> str:
    """Normalized key for near-duplicate detection (lowercase, depunct, ws-collapse)."""
    blob = " ".join(str(rec.get(k, "")) for k in
                    ("text", "prompt", "response", "chosen", "rejected"))
    blob = _PUNCT.sub(" ", blob.lower())
    blob = _WS.sub(" ", blob).strip()
    return hashlib.sha3_256(blob.encode("utf-8")).hexdigest()


def dedupe(in_path: str | Path, out_path: str | Path) -> dict[str, Any]:
    seen: set[str] = set()
    kept, removed = [], 0
    for rec in read_jsonl(in_path):
        key = _dedupe_key(rec)
        if key in seen:
            removed += 1
            continue
        seen.add(key)
        kept.append(rec)
    written = write_jsonl(out_path, kept)
    return {"in": str(in_path), "out": str(out_path), "rows": written,
            "removed": removed, "sha256": sha256_file(out_path)}


def _stable_bucket(rec: dict[str, Any], buckets: int) -> int:
    h = hashlib.sha3_256(json.dumps(rec, sort_keys=True).encode("utf-8")).digest()
    return int.from_bytes(h[:4], "big") % buckets


def split(in_path: str | Path, out_dir: str | Path,
          ratios: tuple[float, float, float] = (0.8, 0.1, 0.1)) -> dict[str, Any]:
    """Deterministic train/eval/test split by content hash (stable across runs)."""
    if abs(sum(ratios) - 1.0) > 1e-6:
        raise DatasetError(f"split ratios must sum to 1.0, got {ratios}")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    train_cut = int(ratios[0] * 1000)
    eval_cut = train_cut + int(ratios[1] * 1000)
    rows = {"train": [], "eval": [], "test": []}
    for rec in read_jsonl(in_path):
        b = _stable_bucket(rec, 1000)
        target = "train" if b < train_cut else ("eval" if b < eval_cut else "test")
        rows[target].append(rec)
    result = {}
    for name, recs in rows.items():
        path = out / f"{name}.jsonl"
        write_jsonl(path, recs)
        result[name] = {"split": name, "path": str(path), "row_count": len(recs),
                        "sha256": sha256_file(path)}
    return result


def validate(path: str | Path) -> dict[str, Any]:
    """Structural checks: parseable, non-empty, consistent sample shape."""
    checks: list[dict[str, Any]] = []
    n = 0
    shapes: set[str] = set()
    empty = 0
    try:
        for rec in read_jsonl(path):
            n += 1
            if "chosen" in rec and "rejected" in rec:
                shapes.add("preference")
            elif "prompt" in rec and "response" in rec:
                shapes.add("sft")
            elif "text" in rec:
                shapes.add("text")
            else:
                shapes.add("unknown")
            if not any(str(rec.get(k, "")).strip()
                       for k in ("text", "prompt", "response", "chosen")):
                empty += 1
        checks.append({"check": "parseable", "passed": True})
    except DatasetError as exc:
        return {"valid": False, "rows": n,
                "checks": [{"check": "parseable", "passed": False, "detail": exc.message}]}
    checks.append({"check": "non_empty_file", "passed": n > 0})
    checks.append({"check": "consistent_shape", "passed": len(shapes) <= 1,
                   "detail": sorted(shapes)})
    checks.append({"check": "no_empty_rows", "passed": empty == 0, "detail": f"{empty} empty"})
    valid = all(c["passed"] for c in checks)
    return {"valid": valid, "rows": n, "shapes": sorted(shapes), "checks": checks}


def export(in_path: str | Path, out_path: str | Path,
           fmt: str = "jsonl") -> dict[str, Any]:
    rows = list(read_jsonl(in_path))
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fmt = fmt.lower()
    if fmt == "jsonl":
        write_jsonl(out, rows)
    elif fmt == "csv":
        import csv
        keys: list[str] = []
        for r in rows:
            for k in r:
                if k not in keys:
                    keys.append(k)
        with out.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in keys})
    elif fmt == "parquet":
        try:
            import pandas as pd  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise DatasetError("parquet export needs pandas+pyarrow",
                              hint="pip install 'animica[gpu]' or pip install pandas pyarrow") from exc
        pd.DataFrame(rows).to_parquet(out)
    else:
        raise DatasetError(f"unsupported export format: {fmt}")
    return {"out": str(out), "rows": len(rows), "format": fmt}
