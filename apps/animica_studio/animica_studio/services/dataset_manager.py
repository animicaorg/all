from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from threading import Event
from typing import Any
from urllib.request import urlopen

from animica_studio.services.dataset_bootstrap_service import BootstrapOptions, DatasetBootstrapService
from animica_studio.util.paths import app_data_dir


class DatasetManager:
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
    ) -> dict[str, Any]:
        langs = [l.strip().lower() for l in (languages or ["en"]) if l.strip()]
        topic_tokens = [t.strip().lower() for t in (topics or []) if t.strip()]
        run_dir = self._root / f"auto-{re.sub(r'[^a-zA-Z0-9_-]+', '-', name).strip('-') or 'dataset'}"
        run_dir.mkdir(parents=True, exist_ok=True)

        docs: list[dict[str, Any]] = []
        docs.extend(self._fetch_wikipedia(max_documents=max_documents, languages=langs, topics=topic_tokens))
        docs.extend(self._fetch_arxiv(max_documents=max_documents, topics=topic_tokens))

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
        return self._write_dataset(run_dir, records, source="auto")

    def build_custom_dataset(self, paths: list[str], name: str = "custom") -> dict[str, Any]:
        run_dir = self._root / f"custom-{re.sub(r'[^a-zA-Z0-9_-]+', '-', name).strip('-') or 'dataset'}"
        run_dir.mkdir(parents=True, exist_ok=True)
        docs: list[dict[str, Any]] = []
        for raw in paths:
            p = Path(raw).expanduser()
            if p.is_dir():
                for child in sorted(p.rglob("*")):
                    if child.is_file() and child.suffix.lower() in {".txt", ".jsonl"}:
                        docs.extend(self._read_custom_file(child))
            elif p.is_file():
                docs.extend(self._read_custom_file(p))
        if not docs:
            raise ValueError("No valid records found in selected dataset paths.")
        return self._write_dataset(run_dir, docs, source="custom")

    def _write_dataset(self, run_dir: Path, records: list[dict[str, Any]], source: str) -> dict[str, Any]:
        shard = run_dir / "shard-00000.jsonl"
        with shard.open("w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        manifest = {
            "schema": "animica.ena.dataset.v1",
            "source": source,
            "num_documents": len(records),
            "shards": [{"path": str(shard), "records": len(records)}],
        }
        manifest_path = run_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        return {"dataset_dir": str(run_dir), "manifest_path": str(manifest_path), "manifest": manifest}

    def _fetch_wikipedia(self, max_documents: int, languages: list[str], topics: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        lang = languages[0] if languages else "en"
        search = topics[0] if topics else "machine learning"
        try:
            url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{search.replace(' ', '%20')}"
            with urlopen(url, timeout=8) as resp:  # noqa: S310
                data = json.loads(resp.read().decode("utf-8"))
            txt = str(data.get("extract") or "")
            if txt:
                out.append({"text": txt, "source": "wikipedia", "language": lang})
        except Exception:
            pass
        return out[:max_documents]

    def _fetch_arxiv(self, max_documents: int, topics: list[str]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        query = topics[0] if topics else "all:machine+learning"
        try:
            url = f"http://export.arxiv.org/api/query?search_query={query}&start=0&max_results=3"
            with urlopen(url, timeout=8) as resp:  # noqa: S310
                raw = resp.read().decode("utf-8", errors="ignore")
            for abstract in re.findall(r"<summary>(.*?)</summary>", raw, flags=re.S):
                text = re.sub(r"\s+", " ", abstract).strip()
                if text:
                    out.append({"text": text, "source": "arxiv", "language": "en"})
        except Exception:
            pass
        return out[:max_documents]

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
