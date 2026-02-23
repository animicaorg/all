from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from threading import Event
from typing import Any
from urllib.request import urlopen

from animica_studio.services.dataset_bootstrap_service import BootstrapOptions, DatasetBootstrapService
from animica_studio.services.dataset_profile import DatasetProfiler
from animica_studio.util.paths import app_data_dir


class DatasetManager:
    APPROVED_SOURCE_CATEGORIES = {"wikipedia", "arxiv"}

    def __init__(self) -> None:
        self._root = app_data_dir() / "datasets"
        self._root.mkdir(parents=True, exist_ok=True)
        self._bootstrap = DatasetBootstrapService()

    def bootstrap_large_dataset(
        self,
        name: str,
        size_preset: str = "big",
        *,
        language_filter: str = "en",
        max_disk_bytes: int | None = None,
        max_daily_download_bytes: int | None = None,
        max_mbps: float | None = None,
        progress_cb=None,
        cancel_event=None,
    ) -> dict[str, Any]:
        opts = BootstrapOptions(
            name=name,
            size_preset=size_preset,
            language_filter=language_filter,
            output_dir=self._root / f"bootstrap-{re.sub(r'[^a-zA-Z0-9_-]+', '-', name).strip('-') or 'dataset'}",
            max_disk_bytes=max_disk_bytes,
            max_daily_download_bytes=max_daily_download_bytes,
            max_mbps=max_mbps,
        )
        return self._bootstrap.bootstrap(
            options=opts,
            progress_cb=progress_cb or (lambda _p: None),
            cancel=cancel_event or Event(),
        )

    def estimate_bootstrap(self, size_preset: str) -> dict[str, Any]:
        return self._bootstrap.estimate(size_preset)

    def build_auto_dataset(
        self,
        name: str,
        max_documents: int = 200,
        max_bytes: int = 2_000_000,
        languages: list[str] | None = None,
        topics: list[str] | None = None,
        source_categories: list[str] | None = None,
        synthetic_allowed: bool = False,
    ) -> dict[str, Any]:
        langs = [l.strip().lower() for l in (languages or ["en"]) if l.strip()]
        topic_tokens = [t.strip().lower() for t in (topics or []) if t.strip()]
        categories = [c.strip().lower() for c in (source_categories or ["wikipedia", "arxiv"]) if c.strip()]
        categories = [c for c in categories if c in self.APPROVED_SOURCE_CATEGORIES]

        run_dir = self._root / f"auto-{re.sub(r'[^a-zA-Z0-9_-]+', '-', name).strip('-') or 'dataset'}-{int(time.time())}"
        run_dir.mkdir(parents=True, exist_ok=True)

        docs: list[dict[str, Any]] = []
        provenance: list[dict[str, Any]] = []
        if "wikipedia" in categories:
            out, prov = self._fetch_wikipedia(max_documents=max_documents, languages=langs, topics=topic_tokens)
            docs.extend(out)
            provenance.extend(prov)
        if "arxiv" in categories:
            out, prov = self._fetch_arxiv(max_documents=max_documents, topics=topic_tokens)
            docs.extend(out)
            provenance.extend(prov)

        if synthetic_allowed and docs:
            synth = []
            for d in docs[: min(20, len(docs))]:
                txt = str(d.get("text") or "")
                if txt:
                    synth.append({"text": f"Summarize: {txt[:400]}", "source": "synthetic", "language": d.get("language", "en"), "synthetic": True})
            docs.extend(synth)
            provenance.append(
                {
                    "source_name": "synthetic_templates",
                    "url": "builtin://ena/synthetic/prompts-v1",
                    "retrieved_at": int(time.time()),
                    "license_tag": "internal-open",
                    "category": "synthetic",
                }
            )

        dedup: dict[str, dict[str, Any]] = {}
        bytes_used = 0
        for d in docs:
            txt = str(d.get("text") or "").strip()
            if not txt:
                continue
            h = hashlib.sha256(txt.encode("utf-8")).hexdigest()
            if h in dedup:
                continue
            b = len(txt.encode("utf-8"))
            if bytes_used + b > max_bytes:
                break
            dedup[h] = d
            bytes_used += b
            if len(dedup) >= max_documents:
                break

        records = list(dedup.values())
        return self._write_dataset(run_dir, records, source="auto", provenance=provenance)

    def build_custom_dataset(self, paths: list[str], name: str = "custom") -> dict[str, Any]:
        run_dir = self._root / f"custom-{re.sub(r'[^a-zA-Z0-9_-]+', '-', name).strip('-') or 'dataset'}-{int(time.time())}"
        run_dir.mkdir(parents=True, exist_ok=True)
        docs: list[dict[str, Any]] = []
        provenance: list[dict[str, Any]] = []
        for raw in paths:
            p = Path(raw).expanduser()
            if p.is_dir():
                for child in sorted(p.rglob("*")):
                    if child.is_file() and child.suffix.lower() in {".txt", ".jsonl"}:
                        docs.extend(self._read_custom_file(child))
                        provenance.append({"source_name": "custom", "url": str(child), "retrieved_at": int(time.time()), "license_tag": "user-provided", "category": "custom"})
            elif p.is_file():
                docs.extend(self._read_custom_file(p))
                provenance.append({"source_name": "custom", "url": str(p), "retrieved_at": int(time.time()), "license_tag": "user-provided", "category": "custom"})
        if not docs:
            raise ValueError("No valid records found in selected dataset paths.")
        return self._write_dataset(run_dir, docs, source="custom", provenance=provenance)

    def _write_dataset(self, run_dir: Path, records: list[dict[str, Any]], source: str, provenance: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        shards: list[dict[str, Any]] = []
        max_shard_bytes = 128 * 1024 * 1024
        shard_index = 0
        shard_records: list[dict[str, Any]] = []
        current_bytes = 0

        def flush() -> None:
            nonlocal shard_index, shard_records, current_bytes
            if not shard_records:
                return
            shard = run_dir / f"shard-{shard_index:05d}.jsonl"
            with shard.open("w", encoding="utf-8") as f:
                for rec in shard_records:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            shards.append({"path": str(shard), "records": len(shard_records), "bytes": shard.stat().st_size})
            shard_index += 1
            shard_records = []
            current_bytes = 0

        for rec in records:
            payload = json.dumps(rec, ensure_ascii=False)
            b = len(payload.encode("utf-8")) + 1
            if current_bytes + b > max_shard_bytes:
                flush()
            shard_records.append(rec)
            current_bytes += b
        flush()

        dataset_id = hashlib.sha256("\n".join(json.dumps(r, sort_keys=True, ensure_ascii=False) for r in records).encode("utf-8")).hexdigest()[:16]
        manifest = {
            "schema": "animica.ena.dataset.v2",
            "dataset_id": dataset_id,
            "source": source,
            "num_documents": len(records),
            "created_at": int(time.time()),
            "provenance": provenance or [],
            "shards": shards,
        }
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        stats = DatasetProfiler.profile(str(manifest_path)).to_dict()
        (run_dir / "stats.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
        return {
            "dataset_dir": str(run_dir),
            "manifest_path": str(manifest_path),
            "manifest": manifest,
            "stats": stats,
            "dataset_id": dataset_id,
        }

    def _fetch_wikipedia(self, max_documents: int, languages: list[str], topics: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        out: list[dict[str, Any]] = []
        prov: list[dict[str, Any]] = []
        lang = languages[0] if languages else "en"
        search = topics[0] if topics else "machine learning"
        try:
            url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{search.replace(' ', '%20')}"
            with urlopen(url, timeout=8) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
            txt = str(data.get("extract") or "")
            if txt:
                out.append({"text": txt, "source": "wikipedia", "language": lang})
                prov.append({"source_name": "wikipedia", "url": url, "retrieved_at": int(time.time()), "license_tag": "cc-by-sa", "category": "wikipedia"})
        except Exception:
            pass
        return out[:max_documents], prov

    def _fetch_arxiv(self, max_documents: int, topics: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        out: list[dict[str, Any]] = []
        prov: list[dict[str, Any]] = []
        query = topics[0] if topics else "all:machine+learning"
        try:
            url = f"http://export.arxiv.org/api/query?search_query={query}&start=0&max_results=3"
            with urlopen(url, timeout=8) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8", errors="ignore")
            for abstract in re.findall(r"<summary>(.*?)</summary>", raw, flags=re.S):
                text = re.sub(r"\s+", " ", abstract).strip()
                if text:
                    out.append({"text": text, "source": "arxiv", "language": "en"})
            if out:
                prov.append({"source_name": "arxiv", "url": url, "retrieved_at": int(time.time()), "license_tag": "arxiv-terms", "category": "arxiv"})
        except Exception:
            pass
        return out[:max_documents], prov

    def _read_custom_file(self, path: Path) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        if path.suffix.lower() == ".txt":
            text = path.read_text(encoding="utf-8", errors="ignore").strip()
            if text:
                out.append({"text": text, "source": str(path), "language": "unknown"})
            return out
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
                if isinstance(row, dict) and row.get("text"):
                    out.append({"text": str(row["text"]), "source": str(path), "language": str(row.get("language") or "unknown")})
            except Exception:
                out.append({"text": line, "source": str(path), "language": "unknown"})
        return out
