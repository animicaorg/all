from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import shutil
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any, Callable, Iterable
from urllib.request import Request, urlopen

from animica_studio.util.paths import app_data_dir


ProgressCb = Callable[[dict[str, Any]], None]


SIZE_PRESETS: dict[str, dict[str, Any]] = {
    "starter": {"label": "Starter", "target_bytes": 10 * 1024**3, "range": "5-20 GB"},
    "big": {"label": "Big", "target_bytes": 75 * 1024**3, "range": "50-100 GB"},
    "huge": {"label": "Huge", "target_bytes": 225 * 1024**3, "range": "200+ GB"},
}


@dataclass(slots=True)
class BootstrapOptions:
    name: str
    size_preset: str = "big"
    output_dir: Path | None = None
    language_filter: str = "en"
    shard_size_bytes: int = 192 * 1024**2
    max_disk_bytes: int | None = None
    max_daily_download_bytes: int | None = None
    max_mbps: float | None = None
    include_optional_owt2: bool = False

    @property
    def target_bytes(self) -> int:
        return int(SIZE_PRESETS.get(self.size_preset, SIZE_PRESETS["big"])["target_bytes"])


class SourceProvider:
    source_name: str = "base"
    source_version: str = "v1"

    def cache_dir(self, base: Path) -> Path:
        p = base / self.source_name / self.source_version
        p.mkdir(parents=True, exist_ok=True)
        return p

    def iter_documents(self, manager: "DownloadManager", progress_cb: ProgressCb, cancel: Event) -> Iterable[dict[str, Any]]:
        return []


class WikipediaAbstractsProvider(SourceProvider):
    source_name = "wikipedia"
    source_version = "enwiki-latest-abstract.xml.gz"
    _URL = "https://dumps.wikimedia.org/enwiki/latest/enwiki-latest-abstract.xml.gz"

    def iter_documents(self, manager: "DownloadManager", progress_cb: ProgressCb, cancel: Event) -> Iterable[dict[str, Any]]:
        cache = self.cache_dir(manager.cache_root)
        dump_path = manager.download(self._URL, cache / self.source_version, progress_cb=progress_cb, cancel=cancel)
        with gzip.open(dump_path, "rt", encoding="utf-8", errors="ignore") as fh:
            buf: list[str] = []
            for line in fh:
                if cancel.is_set():
                    return
                buf.append(line)
                if "</doc>" not in line:
                    continue
                chunk = "".join(buf)
                buf.clear()
                title = _capture_tag(chunk, "title")
                abstract = _capture_tag(chunk, "abstract")
                if abstract:
                    yield {
                        "text": _normalize_text(abstract),
                        "title": title,
                        "language": "en",
                        "source": self.source_name,
                        "source_version": self.source_version,
                        "source_url": self._URL,
                    }


class ArxivApiProvider(SourceProvider):
    source_name = "arxiv"
    source_version = datetime.now(timezone.utc).strftime("api-snapshot-%Y%m%d")

    def iter_documents(self, manager: "DownloadManager", progress_cb: ProgressCb, cancel: Event) -> Iterable[dict[str, Any]]:
        cache = self.cache_dir(manager.cache_root)
        for start in range(0, 4000, 1000):
            if cancel.is_set():
                return
            url = f"https://export.arxiv.org/api/query?search_query=cat:cs.LG+OR+cat:cs.AI&start={start}&max_results=1000"
            xml_path = manager.download(url, cache / f"batch-{start:05d}.xml", progress_cb=progress_cb, cancel=cancel)
            text = xml_path.read_text(encoding="utf-8", errors="ignore")
            for entry in re.findall(r"<entry>(.*?)</entry>", text, flags=re.S):
                title = _capture_xml(entry, "title")
                summary = _capture_xml(entry, "summary")
                if summary:
                    yield {
                        "text": _normalize_text(summary),
                        "title": _normalize_text(title),
                        "language": "en",
                        "source": self.source_name,
                        "source_version": self.source_version,
                        "source_url": url,
                    }


class GutenbergProvider(SourceProvider):
    source_name = "gutenberg"
    source_version = "pg-epub-feeds-v1"
    _CATALOG = "https://www.gutenberg.org/cache/epub/feeds/rdf-files.tar.bz2"

    def iter_documents(self, manager: "DownloadManager", progress_cb: ProgressCb, cancel: Event) -> Iterable[dict[str, Any]]:
        # License-safe default: consume cached local text files only (user-provided or previous runs).
        # We still cache the official catalog blob as provenance.
        cache = self.cache_dir(manager.cache_root)
        manager.download(self._CATALOG, cache / "rdf-files.tar.bz2", progress_cb=progress_cb, cancel=cancel)
        texts = sorted((cache / "texts").glob("*.txt"))
        for path in texts:
            if cancel.is_set():
                return
            txt = path.read_text(encoding="utf-8", errors="ignore")
            clean = _strip_gutenberg_boilerplate(txt)
            if clean:
                yield {
                    "text": clean,
                    "title": path.stem,
                    "language": "en",
                    "source": self.source_name,
                    "source_version": self.source_version,
                    "source_url": self._CATALOG,
                }


class VettedReposProvider(SourceProvider):
    source_name = "vetted_repos"
    source_version = "v1"

    def __init__(self, repos: list[str] | None = None) -> None:
        self._repos = repos or [
            "https://raw.githubusercontent.com/animicaorg/all/refs/heads/main/README.md",
        ]

    def iter_documents(self, manager: "DownloadManager", progress_cb: ProgressCb, cancel: Event) -> Iterable[dict[str, Any]]:
        cache = self.cache_dir(manager.cache_root)
        for idx, url in enumerate(self._repos):
            if cancel.is_set():
                return
            path = manager.download(url, cache / f"repo-{idx:03d}.txt", progress_cb=progress_cb, cancel=cancel)
            txt = _normalize_text(path.read_text(encoding="utf-8", errors="ignore"))
            if txt:
                yield {
                    "text": txt,
                    "title": Path(url).name,
                    "language": "en",
                    "source": self.source_name,
                    "source_version": self.source_version,
                    "source_url": url,
                }


class DownloadManager:
    def __init__(self, cache_root: Path, max_mbps: float | None = None, max_daily_bytes: int | None = None) -> None:
        self.cache_root = cache_root
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.max_mbps = max_mbps
        self.max_daily_bytes = max_daily_bytes
        self._daily_counter_file = self.cache_root / "daily_download_usage.json"

    def download(self, url: str, dest: Path, *, progress_cb: ProgressCb, cancel: Event) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        received = tmp.stat().st_size if tmp.exists() else 0
        headers = {"User-Agent": "animica-studio-dataset-bootstrap/1.0"}
        if received:
            headers["Range"] = f"bytes={received}-"
        req = Request(url, headers=headers)
        with urlopen(req, timeout=30) as resp, tmp.open("ab") as out:  # noqa: S310
            total = _safe_int(resp.headers.get("Content-Length"))
            if total and received and resp.status == 206:
                total += received
            while True:
                if cancel.is_set():
                    break
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                self._guard_daily_limit(len(chunk))
                out.write(chunk)
                received += len(chunk)
                progress_cb({"stage": "downloading", "url": url, "downloaded_bytes": received, "download_total_bytes": total})
                self._throttle(len(chunk))
        if cancel.is_set():
            return dest
        shutil.move(tmp, dest)
        return dest

    def _throttle(self, n_bytes: int) -> None:
        if not self.max_mbps or self.max_mbps <= 0:
            return
        seconds = (n_bytes * 8) / (self.max_mbps * 1_000_000)
        if seconds > 0:
            time.sleep(seconds)

    def _guard_daily_limit(self, delta: int) -> None:
        if not self.max_daily_bytes:
            return
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        data = {"date": today, "bytes": 0}
        if self._daily_counter_file.exists():
            try:
                data = json.loads(self._daily_counter_file.read_text(encoding="utf-8"))
            except Exception:
                data = {"date": today, "bytes": 0}
        if data.get("date") != today:
            data = {"date": today, "bytes": 0}
        data["bytes"] = int(data.get("bytes") or 0) + delta
        if data["bytes"] > self.max_daily_bytes:
            raise RuntimeError("Daily download quota exceeded. Increase quota or resume tomorrow.")
        self._daily_counter_file.write_text(json.dumps(data), encoding="utf-8")


class DatasetBootstrapService:
    def __init__(self) -> None:
        self._root = app_data_dir() / "datasets"
        self._cache_root = self._root / "cache"
        self._root.mkdir(parents=True, exist_ok=True)
        self._cache_root.mkdir(parents=True, exist_ok=True)

    def estimate(self, preset: str) -> dict[str, Any]:
        target = int(SIZE_PRESETS.get(preset, SIZE_PRESETS["big"])["target_bytes"])
        probe_mbps = self._probe_bandwidth_mbps()
        dl = int(target * 0.45)
        low_h = int(dl * 8 / (max(probe_mbps, 5.0) * 1_000_000) / 3600)
        hi_h = int(dl * 8 / (max(probe_mbps, 1.5) * 1_000_000) / 3600)
        return {
            "target_bytes": target,
            "disk_needed_bytes": int(target * 1.25),
            "download_bytes": dl,
            "bandwidth_mbps": probe_mbps,
            "eta_hours_range": [max(1, low_h), max(2, hi_h)],
        }

    def bootstrap(self, options: BootstrapOptions, *, progress_cb: ProgressCb, cancel: Event) -> dict[str, Any]:
        estimates = self.estimate(options.size_preset)
        headroom = estimates["disk_needed_bytes"]
        disk_probe = (options.output_dir or self._root).expanduser()
        disk_probe = disk_probe if disk_probe.exists() else disk_probe.parent
        free = shutil.disk_usage(str(disk_probe)).free
        if free < headroom:
            raise RuntimeError("Insufficient disk space for selected target. Choose Starter or free disk space.")

        target_dir = (options.output_dir or self._root / f"bootstrap-{_safe_name(options.name)}").expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
        state_path = target_dir / "build_state.json"
        state = self._load_state(state_path)
        state.setdefault("target_bytes", options.target_bytes)
        state.setdefault("processed_bytes", 0)
        state.setdefault("doc_count", 0)
        state.setdefault("downloaded_bytes", 0)
        state.setdefault("cancelled", False)

        manager = DownloadManager(self._cache_root, max_mbps=options.max_mbps, max_daily_bytes=options.max_daily_download_bytes)
        providers: list[SourceProvider] = [WikipediaAbstractsProvider(), ArxivApiProvider(), GutenbergProvider(), VettedReposProvider()]

        shard_writer = _ShardWriter(target_dir / "shards", shard_size_bytes=options.shard_size_bytes)
        dedup_seen: set[str] = set()
        lang_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        before_count = 0

        def _progress(p: dict[str, Any]) -> None:
            if p.get("downloaded_bytes"):
                state["downloaded_bytes"] = max(int(state.get("downloaded_bytes") or 0), int(p["downloaded_bytes"]))
            p["processed_bytes"] = state.get("processed_bytes", 0)
            p["doc_count"] = state.get("doc_count", 0)
            p["target_bytes"] = options.target_bytes
            progress_cb(p)

        for provider in providers:
            if cancel.is_set() or state["processed_bytes"] >= options.target_bytes:
                break
            for doc in provider.iter_documents(manager, _progress, cancel):
                if cancel.is_set() or state["processed_bytes"] >= options.target_bytes:
                    break
                before_count += 1
                text = _normalize_text(str(doc.get("text") or ""))
                if not text:
                    continue
                if options.language_filter and str(doc.get("language") or "").lower() not in {options.language_filter.lower()}:
                    continue
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if digest in dedup_seen:
                    continue
                dedup_seen.add(digest)
                rec = {
                    "text": text,
                    "language": doc.get("language") or "unknown",
                    "source": doc.get("source") or "unknown",
                    "source_version": doc.get("source_version") or "unknown",
                    "source_url": doc.get("source_url") or "",
                    "title": doc.get("title") or "",
                    "sha256": digest,
                }
                written = shard_writer.write(rec)
                state["processed_bytes"] = int(state.get("processed_bytes") or 0) + written
                state["doc_count"] = int(state.get("doc_count") or 0) + 1
                source = str(rec["source"])
                lang = str(rec["language"])
                source_counts[source] = source_counts.get(source, 0) + 1
                lang_counts[lang] = lang_counts.get(lang, 0) + 1
                state["cancelled"] = False
                self._save_state(state_path, state)
                progress_cb(
                    {
                        "stage": "processing",
                        "processed_bytes": state["processed_bytes"],
                        "doc_count": state["doc_count"],
                        "target_bytes": options.target_bytes,
                        "shards": shard_writer.shard_count,
                        "dedup_percent": (1.0 - (len(dedup_seen) / max(before_count, 1))) * 100,
                    }
                )

        if cancel.is_set():
            state["cancelled"] = True
            self._save_state(state_path, state)
            return {"dataset_dir": str(target_dir), "build_state": str(state_path), "cancelled": True}

        shards = shard_writer.close()
        manifest = {
            "schema_version": "animica.ena.dataset.v2",
            "dataset_name": options.name,
            "total_bytes": int(state["processed_bytes"]),
            "doc_count": int(state["doc_count"]),
            "shards": shards,
            "provenance": [
                {"source": p.source_name, "version": p.source_version, "cache_path": str((self._cache_root / p.source_name / p.source_version))}
                for p in providers
            ],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        manifest_path = target_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        dedup_ratio = 1.0 - (len(dedup_seen) / max(before_count, 1))
        stats = {
            "dedup_ratio": dedup_ratio,
            "language_counts": lang_counts,
            "source_counts": source_counts,
            "length_histogram": _length_histogram(shard_writer.lengths),
        }
        stats_path = target_dir / "stats.json"
        stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

        state.update({"completed": True, "cancelled": False, "manifest_path": str(manifest_path)})
        self._save_state(state_path, state)
        return {
            "dataset_dir": str(target_dir),
            "manifest_path": str(manifest_path),
            "stats_path": str(stats_path),
            "build_state": str(state_path),
            "manifest": manifest,
            "stats": stats,
        }

    def _probe_bandwidth_mbps(self) -> float:
        host = "dumps.wikimedia.org"
        started = time.time()
        try:
            socket.gethostbyname(host)
            elapsed = max(0.05, time.time() - started)
            return max(8.0, min(300.0, 50.0 / elapsed))
        except Exception:
            return 25.0

    @staticmethod
    def _load_state(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _save_state(path: Path, state: dict[str, Any]) -> None:
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")


class _ShardWriter:
    def __init__(self, out_dir: Path, shard_size_bytes: int = 192 * 1024**2) -> None:
        self._dir = out_dir
        self._dir.mkdir(parents=True, exist_ok=True)
        self._shard_size_bytes = shard_size_bytes
        self._idx = 0
        self._fh = None
        self._bytes = 0
        self._records = 0
        self._current_hash = hashlib.sha256()
        self._out: list[dict[str, Any]] = []
        self.lengths: list[int] = []

    @property
    def shard_count(self) -> int:
        return len(self._out) + (1 if self._fh else 0)

    def write(self, rec: dict[str, Any]) -> int:
        payload = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
        if self._fh is None:
            self._open_next()
        if self._bytes and self._bytes + len(payload) > self._shard_size_bytes:
            self._finish_current()
            self._open_next()
        self._fh.write(payload.decode("utf-8"))
        self._bytes += len(payload)
        self._records += 1
        self._current_hash.update(payload)
        self.lengths.append(len(rec.get("text") or ""))
        return len(payload)

    def close(self) -> list[dict[str, Any]]:
        self._finish_current()
        return list(self._out)

    def _open_next(self) -> None:
        path = self._dir / f"shard-{self._idx:05d}.jsonl"
        self._fh = path.open("w", encoding="utf-8")
        self._bytes = 0
        self._records = 0
        self._current_hash = hashlib.sha256()
        self._idx += 1

    def _finish_current(self) -> None:
        if not self._fh:
            return
        path = Path(self._fh.name)
        self._fh.close()
        self._fh = None
        self._out.append(
            {
                "path": str(path),
                "size_bytes": self._bytes,
                "records": self._records,
                "sha256": self._current_hash.hexdigest(),
            }
        )


def _capture_tag(xml_chunk: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", xml_chunk, flags=re.S)
    if not m:
        return ""
    return _normalize_text(m.group(1))


def _capture_xml(entry: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", entry, flags=re.S)
    return _normalize_text(m.group(1) if m else "")


def _normalize_text(text: str) -> str:
    text = text.encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _strip_gutenberg_boilerplate(text: str) -> str:
    text = _normalize_text(text)
    text = re.sub(r"\*\*\* START OF THE PROJECT GUTENBERG EBOOK.*?\*\*\*", "", text, flags=re.I)
    text = re.sub(r"\*\*\* END OF THE PROJECT GUTENBERG EBOOK.*", "", text, flags=re.I)
    return text.strip()


def _length_histogram(lengths: list[int]) -> dict[str, int]:
    bins = {"<256": 0, "256-1023": 0, "1024-4095": 0, "4096+": 0}
    for n in lengths:
        if n < 256:
            bins["<256"] += 1
        elif n < 1024:
            bins["256-1023"] += 1
        elif n < 4096:
            bins["1024-4095"] += 1
        else:
            bins["4096+"] += 1
    return bins


def _safe_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-") or "dataset"
