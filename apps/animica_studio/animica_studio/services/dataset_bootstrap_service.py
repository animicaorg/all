from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import re
import shutil
import socket
import tarfile
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path
from threading import Event
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
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


@dataclass(slots=True)
class ProviderUrlCandidate:
    name: str
    url: str


@dataclass(slots=True)
class DownloadFailure:
    source: str
    url: str
    status: int | None
    content_type: str
    excerpt: str
    message: str


@dataclass(slots=True)
class WorkItem:
    key: str
    provider: str
    params: dict[str, Any]


class SourceScheduler:
    def __init__(
        self,
        *,
        target_bytes: int,
        source_settings: dict[str, Any],
        plan_data: dict[str, Any] | None = None,
    ) -> None:
        self.target_bytes = int(target_bytes)
        self.source_settings = source_settings
        self.providers_enabled = list((source_settings.get("provider_order") or ["vetted_repos", "wikipedia", "arxiv", "gutenberg"]))
        self.auto_expand = bool(source_settings.get("auto_expand_until_target", True))
        self._queued: list[WorkItem] = []
        self._completed: dict[str, dict[str, Any]] = {}
        self._failed: dict[str, str] = {}
        if plan_data:
            self._queued = [WorkItem(**item) for item in plan_data.get("queued", []) if isinstance(item, dict)]
            self._completed = dict(plan_data.get("completed", {})) if isinstance(plan_data.get("completed"), dict) else {}
            self._failed = dict(plan_data.get("failed", {})) if isinstance(plan_data.get("failed"), dict) else {}
        self._expanded = set(plan_data.get("expanded_providers", [])) if isinstance(plan_data, dict) else set()

    def ensure_queue(self, processed_bytes: int) -> None:
        if int(processed_bytes) >= self.target_bytes:
            return
        if self._queued or not self.auto_expand:
            return
        for provider in self.providers_enabled:
            if provider in self._expanded:
                continue
            items = self._expand_provider(provider)
            self._expanded.add(provider)
            for item in items:
                if item.key in self._completed or item.key in self._failed:
                    continue
                if not any(q.key == item.key for q in self._queued):
                    self._queued.append(item)
            if self._queued:
                return

    def pop_next(self, processed_bytes: int) -> WorkItem | None:
        self.ensure_queue(processed_bytes)
        if not self._queued:
            return None
        return self._queued.pop(0)

    def mark_completed(self, item: WorkItem, *, bytes_contributed: int, docs: int) -> None:
        self._completed[item.key] = {
            "provider": item.provider,
            "params": item.params,
            "bytes": int(bytes_contributed),
            "docs": int(docs),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }

    def mark_failed(self, item: WorkItem, reason: str) -> None:
        self._failed[item.key] = reason

    def stop_reason(self, processed_bytes: int) -> tuple[bool, str]:
        if int(processed_bytes) >= self.target_bytes:
            return True, "TARGET_MET"
        self.ensure_queue(processed_bytes)
        if self._queued:
            return False, "CONTINUE"
        return True, "SOURCES_EXHAUSTED"

    def diagnostics(self) -> dict[str, Any]:
        return {
            "queued": [{"key": item.key, "provider": item.provider, "params": item.params} for item in self._queued],
            "completed": self._completed,
            "failed": self._failed,
            "expanded_providers": sorted(self._expanded),
            "providers_enabled": self.providers_enabled,
            "target_bytes": self.target_bytes,
            "auto_expand": self.auto_expand,
        }

    def _expand_provider(self, provider: str) -> list[WorkItem]:
        providers_cfg = self.source_settings.get("providers") if isinstance(self.source_settings.get("providers"), dict) else {}
        cfg = providers_cfg.get(provider, {}) if isinstance(providers_cfg, dict) else {}
        if provider == "vetted_repos":
            repos = cfg.get("repos") if isinstance(cfg, dict) and isinstance(cfg.get("repos"), list) else []
            defaults = [
                {"owner": "animicaorg", "repo": "all", "ref": "main"},
                {"owner": "python", "repo": "cpython", "ref": "main"},
                {"owner": "pallets", "repo": "flask", "ref": "main"},
            ]
            items = repos or defaults
            out = []
            for repo in items:
                owner = str(repo.get("owner") or "").strip()
                name = str(repo.get("repo") or "").strip()
                ref = str(repo.get("ref") or "main").strip() or "main"
                if not owner or not name:
                    continue
                key = f"vetted_repos:{owner}/{name}@{ref}"
                out.append(WorkItem(key=key, provider=provider, params={"repos": [{"owner": owner, "repo": name, "ref": ref}]}))
            return out
        if provider == "wikipedia":
            versions = cfg.get("versions") if isinstance(cfg, dict) and isinstance(cfg.get("versions"), list) else []
            if not versions:
                versions = [cfg.get("version") or "latest", "20240501", "20240101"]
            return [WorkItem(key=f"wikipedia:{v}", provider=provider, params={"version": str(v)}) for v in versions]
        if provider == "arxiv":
            starts = cfg.get("starts") if isinstance(cfg, dict) and isinstance(cfg.get("starts"), list) else [0, 1000, 2000, 3000, 4000, 5000]
            return [WorkItem(key=f"arxiv:{int(s)}", provider=provider, params={"shard_start": int(s), "shard_size": 1000}) for s in starts]
        if provider == "gutenberg" and bool(cfg.get("enabled", True)):
            return [WorkItem(key="gutenberg:catalog", provider=provider, params={})]
        return []


class SourceProvider:
    source_name: str = "base"
    source_version: str = "v1"

    def cache_dir(self, base: Path) -> Path:
        p = base / self.source_name / self.source_version
        p.mkdir(parents=True, exist_ok=True)
        return p

    def resolve_latest(self, overrides: dict[str, Any] | None = None) -> tuple[str, list[ProviderUrlCandidate]]:
        return self.source_version, []

    def iter_documents(
        self,
        manager: "DownloadManager",
        *,
        source_settings: dict[str, Any],
        progress_cb: ProgressCb,
        cancel: Event,
        work_item: WorkItem | None = None,
    ) -> Iterable[dict[str, Any]]:
        return []


class WikipediaAbstractsProvider(SourceProvider):
    source_name = "wikipedia"
    source_version = "enwiki-latest-abstract.xml.gz"

    def resolve_latest(self, overrides: dict[str, Any] | None = None) -> tuple[str, list[ProviderUrlCandidate]]:
        cfg = overrides or {}
        version = str(cfg.get("version") or "latest").strip() or "latest"
        base_override = str(cfg.get("base_url") or "").strip().rstrip("/")
        base_urls = [
            "https://dumps.wikimedia.org/enwiki",
            "https://wikimedia.bringyour.com/enwiki",
        ]
        if base_override:
            base_urls.insert(0, base_override)
        url_path = f"{version}/enwiki-{version}-abstract.xml.gz"
        candidates = [ProviderUrlCandidate(name=f"mirror-{idx+1}", url=f"{base}/{url_path}") for idx, base in enumerate(base_urls)]
        return f"enwiki-{version}-abstract.xml.gz", candidates

    def iter_documents(self, manager: "DownloadManager", *, source_settings: dict[str, Any], progress_cb: ProgressCb, cancel: Event, work_item: WorkItem | None = None) -> Iterable[dict[str, Any]]:
        merged = dict(source_settings)
        if work_item:
            merged.update(work_item.params)
        version, candidates = self.resolve_latest(merged)
        cache = self.cache_dir(manager.cache_root)
        dump_path = manager.download_with_mirrors(
            source=self.source_name,
            candidates=candidates,
            dest=cache / version,
            progress_cb=progress_cb,
            cancel=cancel,
        )
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
                        "source_version": version,
                        "source_url": manager.last_success_url(self.source_name),
                    }


class ArxivApiProvider(SourceProvider):
    source_name = "arxiv"
    source_version = datetime.now(timezone.utc).strftime("api-snapshot-%Y%m%d")

    def resolve_latest(self, overrides: dict[str, Any] | None = None) -> tuple[str, list[ProviderUrlCandidate]]:
        cfg = overrides or {}
        version = str(cfg.get("version") or datetime.now(timezone.utc).strftime("%Y%m%d")).strip()
        base_override = str(cfg.get("base_url") or "").strip().rstrip("/")
        base_urls = [
            "https://export.arxiv.org/api/query",
            "https://arxiv.org/api/query",
        ]
        if base_override:
            base_urls.insert(0, base_override)
        return f"api-snapshot-{version}", [ProviderUrlCandidate(name=f"mirror-{i+1}", url=u) for i, u in enumerate(base_urls)]

    def iter_documents(self, manager: "DownloadManager", *, source_settings: dict[str, Any], progress_cb: ProgressCb, cancel: Event, work_item: WorkItem | None = None) -> Iterable[dict[str, Any]]:
        merged = dict(source_settings)
        if work_item:
            merged.update(work_item.params)
        version, bases = self.resolve_latest(merged)
        cache = self.cache_dir(manager.cache_root)
        shard_size = int(merged.get("shard_size") or 1000)
        starts = [int(merged.get("shard_start"))] if merged.get("shard_start") is not None else list(range(0, 6000, shard_size))
        for start in starts:
            if cancel.is_set():
                return
            candidates = [
                ProviderUrlCandidate(
                    name=f"{base.name}-batch-{start}",
                    url=f"{base.url}?search_query=cat:cs.LG+OR+cat:cs.AI&start={start}&max_results={shard_size}",
                )
                for base in bases
            ]
            xml_path = manager.download_with_mirrors(
                source=self.source_name,
                candidates=candidates,
                dest=cache / f"batch-{start:05d}.xml",
                progress_cb=progress_cb,
                cancel=cancel,
            )
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
                        "source_version": version,
                        "source_url": manager.last_success_url(self.source_name),
                    }


class GutenbergProvider(SourceProvider):
    source_name = "gutenberg"
    source_version = "pg-epub-feeds-v1"
    _CATALOG = "https://www.gutenberg.org/cache/epub/feeds/rdf-files.tar.bz2"

    def resolve_latest(self, overrides: dict[str, Any] | None = None) -> tuple[str, list[ProviderUrlCandidate]]:
        cfg = overrides or {}
        if str(cfg.get("base_url") or "").strip():
            custom = str(cfg["base_url"]).rstrip("/")
            return self.source_version, [ProviderUrlCandidate(name="override", url=f"{custom}/cache/epub/feeds/rdf-files.tar.bz2")]
        return self.source_version, [ProviderUrlCandidate(name="primary", url=self._CATALOG)]

    def iter_documents(self, manager: "DownloadManager", *, source_settings: dict[str, Any], progress_cb: ProgressCb, cancel: Event, work_item: WorkItem | None = None) -> Iterable[dict[str, Any]]:
        merged = dict(source_settings)
        if work_item:
            merged.update(work_item.params)
        version, candidates = self.resolve_latest(merged)
        cache = self.cache_dir(manager.cache_root)
        manager.download_with_mirrors(
            source=self.source_name,
            candidates=candidates,
            dest=cache / "rdf-files.tar.bz2",
            progress_cb=progress_cb,
            cancel=cancel,
        )
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
                    "source_version": version,
                    "source_url": manager.last_success_url(self.source_name),
                }


class VettedReposProvider(SourceProvider):
    source_name = "vetted_repos"
    source_version = "v1"
    _include_exts = {
        ".py", ".rs", ".ts", ".tsx", ".js", ".jsx", ".md", ".txt", ".toml", ".yaml", ".yml", ".json", ".go", ".cpp", ".c", ".h", ".hpp", ".sh", ".sol", ".java", ".kt", ".swift", ".sql", ".html", ".css", ".xml", ".ini", ".cfg", ".conf",
    }
    _exclude_globs = [
        "**/.git/**", "**/.venv/**", "**/venv/**", "**/node_modules/**", "**/dist/**", "**/build/**", "**/target/**", "**/__pycache__/**", "**/.next/**", "**/.cache/**",
    ]

    def __init__(self, repos: list[dict[str, str]] | None = None) -> None:
        self._repos = repos or [{"owner": "animicaorg", "repo": "all", "ref": "main"}]

    def resolve_latest(self, overrides: dict[str, Any] | None = None) -> tuple[str, list[ProviderUrlCandidate]]:
        return self.source_version, []

    def iter_documents(self, manager: "DownloadManager", *, source_settings: dict[str, Any], progress_cb: ProgressCb, cancel: Event, work_item: WorkItem | None = None) -> Iterable[dict[str, Any]]:
        merged = dict(source_settings)
        if work_item:
            merged.update(work_item.params)
        repos = self._parse_repos(merged)
        max_file_size = int(merged.get("max_file_size_bytes") or (3 * 1024 * 1024))
        include_globs = merged.get("include_patterns") if isinstance(merged.get("include_patterns"), list) else []
        exclude_globs = merged.get("exclude_patterns") if isinstance(merged.get("exclude_patterns"), list) else []
        for repo in repos:
            if cancel.is_set():
                return
            owner = repo.get("owner", "")
            name = repo.get("repo", "")
            if not owner or not name:
                continue
            refs = [repo.get("ref", "").strip()] if repo.get("ref") else []
            refs.extend(["main", "master"])
            refs = [r for i, r in enumerate(refs) if r and r not in refs[:i]]
            cache = manager.cache_root / "github" / owner / name
            tarball_path: Path | None = None
            used_ref = ""
            download_exc: str = ""
            for ref in refs:
                try:
                    tarball_path = manager.download_with_mirrors(
                        source=self.source_name,
                        candidates=[ProviderUrlCandidate(name=f"{owner}-{name}-{ref}", url=f"https://codeload.github.com/{owner}/{name}/tar.gz/{ref}")],
                        dest=cache / f"{ref}.tar.gz",
                        progress_cb=progress_cb,
                        cancel=cancel,
                    )
                    used_ref = ref
                    break
                except Exception as _dl_exc:
                    download_exc = str(_dl_exc)
                    continue
            if tarball_path is None:
                manager._record_failure(
                    source=self.source_name,
                    url=f"https://codeload.github.com/{owner}/{name}/tar.gz/<refs={refs}>",
                    status=None,
                    content_type="",
                    excerpt="",
                    message=download_exc or "all refs failed",
                )
                continue
            progress_cb({"stage": "extracting", "source": self.source_name, "repo": f"{owner}/{name}", "ref": used_ref})
            yield from self._iter_repo_docs(
                tarball_path,
                owner=owner,
                repo=name,
                ref=used_ref,
                max_file_size=max_file_size,
                include_globs=[str(p) for p in include_globs],
                exclude_globs=[str(p) for p in exclude_globs],
                progress_cb=progress_cb,
                cancel=cancel,
            )

    def _parse_repos(self, source_settings: dict[str, Any]) -> list[dict[str, str]]:
        raw = source_settings.get("repos") if isinstance(source_settings, dict) else None
        repos: list[dict[str, str]] = []
        if isinstance(raw, list) and raw:
            for item in raw:
                if isinstance(item, dict):
                    repos.append({
                        "owner": str(item.get("owner") or "").strip(),
                        "repo": str(item.get("repo") or "").strip(),
                        "ref": str(item.get("ref") or "").strip(),
                    })
                elif isinstance(item, str):
                    repos.append(self._repo_from_text(item))
        return [r for r in (repos or list(self._repos)) if r.get("owner") and r.get("repo")]

    def _repo_from_text(self, item: str) -> dict[str, str]:
        text = item.strip().replace("https://github.com/", "")
        text = text.removeprefix("github.com/")
        if text.endswith(".git"):
            text = text[:-4]
        parts = [p for p in text.split("/") if p]
        if len(parts) < 2:
            return {"owner": "", "repo": "", "ref": ""}
        return {"owner": parts[0], "repo": parts[1], "ref": ""}

    def _iter_repo_docs(
        self,
        tarball_path: Path,
        *,
        owner: str,
        repo: str,
        ref: str,
        max_file_size: int,
        include_globs: list[str],
        exclude_globs: list[str],
        progress_cb: ProgressCb,
        cancel: Event,
    ) -> Iterator[dict[str, Any]]:
        extracted = 0
        excludes = self._exclude_globs + exclude_globs
        with tarfile.open(tarball_path, "r:gz") as tf:
            for member in tf.getmembers():
                if cancel.is_set():
                    return
                if not member.isfile() or member.size <= 0 or member.size > max_file_size:
                    continue
                repo_path = "/".join(Path(member.name).parts[1:]) if len(Path(member.name).parts) > 1 else member.name
                if self._skip_file(repo_path, include_globs, excludes):
                    continue
                fh = tf.extractfile(member)
                if fh is None:
                    continue
                raw = fh.read(max_file_size + 1)
                if len(raw) > max_file_size or _looks_binary(raw):
                    continue
                text = _normalize_text(raw.decode("utf-8", errors="ignore"))
                if not text:
                    continue
                extracted += member.size
                progress_cb({
                    "stage": "processing",
                    "source": self.source_name,
                    "repo": f"{owner}/{repo}",
                    "ref": ref,
                    "path": repo_path,
                    "extracted_bytes": extracted,
                })
                yield {
                    "text": text,
                    "title": repo_path,
                    "language": "en",
                    "source": self.source_name,
                    "source_version": f"{owner}/{repo}@{ref}",
                    "source_url": f"https://github.com/{owner}/{repo}/blob/{ref}/{repo_path}",
                    "repo": f"{owner}/{repo}",
                    "ref": ref,
                    "path": repo_path,
                    "size": member.size,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }

    def _skip_file(self, path: str, include_globs: list[str], exclude_globs: list[str]) -> bool:
        normalized = path.replace("\\", "/")
        ext = Path(normalized.lower()).suffix
        if include_globs and not any(fnmatch(normalized, p) for p in include_globs):
            return True
        if any(fnmatch(normalized, p) for p in exclude_globs):
            return True
        if ext in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".mp3", ".mp4", ".mov", ".pdf", ".zip", ".gz", ".7z", ".exe", ".dll", ".so", ".bin", ".woff", ".woff2", ".ttf"}:
            return True
        return ext not in self._include_exts


class DownloadManager:
    def __init__(self, cache_root: Path, *, source_settings: dict[str, Any] | None = None, max_mbps: float | None = None, max_daily_bytes: int | None = None) -> None:
        self.cache_root = cache_root
        self.cache_root.mkdir(parents=True, exist_ok=True)
        self.max_mbps = max_mbps
        self.max_daily_bytes = max_daily_bytes
        self.source_settings = source_settings or {}
        self._daily_counter_file = self.cache_root / "daily_download_usage.json"
        self._failures: list[DownloadFailure] = []
        self._last_success_by_source: dict[str, str] = {}

    def diagnostics(self) -> list[dict[str, Any]]:
        return [
            {
                "source": f.source,
                "url": f.url,
                "status": f.status,
                "content_type": f.content_type,
                "excerpt": f.excerpt,
                "message": f.message,
            }
            for f in self._failures
        ]

    def last_success_url(self, source: str) -> str:
        return self._last_success_by_source.get(source, "")

    def download_with_mirrors(self, *, source: str, candidates: list[ProviderUrlCandidate], dest: Path, progress_cb: ProgressCb, cancel: Event) -> Path:
        if not candidates:
            raise RuntimeError(f"No source URLs configured for provider '{source}'.")

        if bool(self.source_settings.get("offline_mode")):
            if dest.exists():
                progress_cb({"stage": "cached", "source": source, "url": str(dest), "from_cache": True})
                return dest
            raise RuntimeError(f"Offline mode is enabled and cached source is missing for {source}: {dest}")

        last_error = ""
        for candidate in candidates:
            try:
                downloaded = self._download_single(source=source, url=candidate.url, dest=dest, progress_cb=progress_cb, cancel=cancel)
                self._last_success_by_source[source] = candidate.url
                return downloaded
            except Exception as exc:  # noqa: BLE001
                last_error = str(exc)
                continue

        summary = self._failure_summary_for_source(source)
        raise RuntimeError(
            f"All mirrors failed for {source}. {last_error}\n{summary}\nSuggestions: Pick a different version, paste a custom URL, or use Starter dataset."
        )

    def _download_single(self, *, source: str, url: str, dest: Path, progress_cb: ProgressCb, cancel: Event) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        received = tmp.stat().st_size if tmp.exists() else 0
        headers = {"User-Agent": "animica-studio-dataset-bootstrap/1.0"}
        if received:
            headers["Range"] = f"bytes={received}-"
        req = Request(url, headers=headers)
        try:
            with urlopen(req, timeout=30) as resp, tmp.open("ab") as out:  # noqa: S310
                total = _safe_int(resp.headers.get("Content-Length"))
                content_type = str(resp.headers.get("Content-Type") or "")
                # Reject HTML responses early — these indicate error pages (rate limits, 302→HTML, etc.)
                if content_type.startswith("text/html"):
                    excerpt = resp.read(512).decode("utf-8", errors="replace")
                    self._record_failure(
                        source=source,
                        url=url,
                        status=getattr(resp, "status", None),
                        content_type=content_type,
                        excerpt=excerpt[:200],
                        message=f"Expected tarball but got HTML (content-type: {content_type})",
                    )
                    raise RuntimeError(f"Download returned HTML instead of tarball from {url}")
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
                    progress_cb({
                        "stage": "downloading",
                        "source": source,
                        "url": url,
                        "downloaded_bytes": received,
                        "download_delta_bytes": len(chunk),
                        "download_total_bytes": total,
                        "content_type": content_type,
                    })
                    self._throttle(len(chunk))
        except HTTPError as exc:
            excerpt = _read_error_excerpt(exc)
            self._record_failure(
                source=source,
                url=url,
                status=exc.code,
                content_type=str(exc.headers.get("Content-Type") or "") if exc.headers else "",
                excerpt=excerpt,
                message=f"HTTP {exc.code}",
            )
            raise RuntimeError(f"Download failed ({exc.code}) from {url}") from exc
        except URLError as exc:
            self._record_failure(source=source, url=url, status=None, content_type="", excerpt="", message=str(exc.reason))
            raise RuntimeError(f"Download failed from {url}: {exc.reason}") from exc
        except Exception as exc:  # noqa: BLE001 — catch timeout/SSL/other OS errors
            self._record_failure(source=source, url=url, status=None, content_type="", excerpt="", message=str(exc))
            raise RuntimeError(f"Download failed from {url}: {exc}") from exc
        if cancel.is_set():
            return dest
        shutil.move(tmp, dest)
        return dest

    def _record_failure(self, *, source: str, url: str, status: int | None, content_type: str, excerpt: str, message: str) -> None:
        self._failures.append(DownloadFailure(source=source, url=url, status=status, content_type=content_type, excerpt=excerpt, message=message))

    def _failure_summary_for_source(self, source: str) -> str:
        lines = []
        for f in self._failures:
            if f.source != source:
                continue
            lines.append(f"- {f.url} -> status={f.status} content_type={f.content_type!r} excerpt={f.excerpt[:200]!r}")
        return "\n".join(lines) if lines else "No detailed diagnostics available."

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
    def __init__(self, source_settings: dict[str, Any] | None = None) -> None:
        self._root = app_data_dir() / "datasets"
        self._cache_root = self._root / "cache"
        self._root.mkdir(parents=True, exist_ok=True)
        self._cache_root.mkdir(parents=True, exist_ok=True)
        self._source_settings = source_settings or {}

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
        plan_path = target_dir / "bootstrap_plan.json"
        state = self._load_state(state_path)
        state.setdefault("target_bytes", options.target_bytes)
        state.setdefault("processed_bytes", 0)
        state.setdefault("doc_count", 0)
        state.setdefault("downloaded_bytes", 0)
        state.setdefault("extracted_bytes", 0)
        state.setdefault("cancelled", False)

        manager = DownloadManager(
            self._cache_root,
            source_settings=self._source_settings,
            max_mbps=options.max_mbps,
            max_daily_bytes=options.max_daily_download_bytes,
        )
        provider_settings = self._provider_settings()
        providers: dict[str, SourceProvider] = {
            "vetted_repos": VettedReposProvider(),
            "wikipedia": WikipediaAbstractsProvider(),
            "arxiv": ArxivApiProvider(),
            "gutenberg": GutenbergProvider(),
        }

        prior_plan = self._load_state(plan_path)
        scheduler = SourceScheduler(
            target_bytes=options.target_bytes,
            source_settings=self._source_settings,
            plan_data=prior_plan,
        )

        shard_writer = _ShardWriter(target_dir / "shards", shard_size_bytes=options.shard_size_bytes)
        dedup_seen: set[str] = set()
        lang_counts: dict[str, int] = {}
        source_counts: dict[str, int] = {}
        before_count = 0

        def _persist_plan() -> None:
            payload = scheduler.diagnostics()
            payload["processed_bytes"] = int(state.get("processed_bytes") or 0)
            payload["downloaded_bytes"] = int(state.get("downloaded_bytes") or 0)
            payload["doc_count"] = int(state.get("doc_count") or 0)
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            payload["target_bytes"] = int(options.target_bytes)
            plan_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        def _progress(p: dict[str, Any]) -> None:
            if p.get("download_delta_bytes"):
                state["downloaded_bytes"] = int(state.get("downloaded_bytes") or 0) + int(p.get("download_delta_bytes") or 0)
            if p.get("extracted_bytes"):
                state["extracted_bytes"] = max(int(state.get("extracted_bytes") or 0), int(p.get("extracted_bytes") or 0))
            p["processed_bytes"] = state.get("processed_bytes", 0)
            p["doc_count"] = state.get("doc_count", 0)
            p["target_bytes"] = options.target_bytes
            p["queue_remaining"] = len(scheduler.diagnostics().get("queued", []))
            p["percent"] = min(100.0, float(state.get("processed_bytes", 0)) * 100.0 / max(1, options.target_bytes))
            progress_cb(p)

        stop_reason = "CONTINUE"
        exhausted_info: list[dict[str, str]] = []
        while not cancel.is_set() and int(state.get("processed_bytes") or 0) < int(options.target_bytes):
            item = scheduler.pop_next(int(state.get("processed_bytes") or 0))
            if item is None:
                should_stop, reason = scheduler.stop_reason(int(state.get("processed_bytes") or 0))
                stop_reason = reason
                if should_stop:
                    break
                continue
            provider = providers.get(item.provider)
            if provider is None:
                scheduler.mark_failed(item, "provider_missing")
                exhausted_info.append({"provider": item.provider, "reason": "provider_missing"})
                _persist_plan()
                continue
            _progress({"stage": "work_item_started", "active_source": f"{item.provider}:{item.key}", "work_item": item.key, "provider": item.provider})
            item_docs = 0
            item_bytes = 0
            try:
                p_settings = provider_settings.get(provider.source_name, {})
                for doc in provider.iter_documents(manager, source_settings=p_settings, progress_cb=_progress, cancel=cancel, work_item=item):
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
                        "repo": doc.get("repo") or "",
                        "ref": doc.get("ref") or "",
                        "path": doc.get("path") or "",
                        "size": int(doc.get("size") or len(text.encode("utf-8"))),
                    }
                    shard_writer.write(rec)
                    real_bytes = len(text.encode("utf-8"))
                    state["processed_bytes"] = int(state.get("processed_bytes") or 0) + real_bytes
                    state["doc_count"] = int(state.get("doc_count") or 0) + 1
                    item_docs += 1
                    item_bytes += real_bytes
                    source = str(rec["source"])
                    lang = str(rec["language"])
                    source_counts[source] = source_counts.get(source, 0) + 1
                    lang_counts[lang] = lang_counts.get(lang, 0) + 1
                    state["cancelled"] = False
                    self._save_state(state_path, state)
                    _persist_plan()
                    progress_cb(
                        {
                            "stage": "sharding",
                            "processed_bytes": state["processed_bytes"],
                            "doc_count": state["doc_count"],
                            "target_bytes": options.target_bytes,
                            "shards": shard_writer.shard_count,
                            "dedup_percent": (1.0 - (len(dedup_seen) / max(before_count, 1))) * 100,
                            "queue_remaining": len(scheduler.diagnostics().get("queued", [])),
                            "active_source": f"{item.provider}:{item.key}",
                        }
                    )
                scheduler.mark_completed(item, bytes_contributed=item_bytes, docs=item_docs)
            except Exception as exc:  # noqa: BLE001
                scheduler.mark_failed(item, str(exc))
                exhausted_info.append({"provider": provider.source_name, "reason": str(exc)})
                progress_cb({
                    "stage": "provider_failed",
                    "source": provider.source_name,
                    "error": str(exc),
                    "work_item": item.key,
                })
            finally:
                _persist_plan()

        if cancel.is_set():
            state["cancelled"] = True
            self._save_state(state_path, state)
            _persist_plan()
            return {"dataset_dir": str(target_dir), "build_state": str(state_path), "cancelled": True, "bootstrap_plan": str(plan_path)}

        shards = shard_writer.close()
        if not shards:
            diags = manager.diagnostics()
            diag_summary = json.dumps(diags[:8], indent=2) if diags else (
                "No download failures recorded. Possible causes:\n"
                "  - All repository files were excluded by the file-type filter\n"
                "  - The repo tarball was empty or contained only binary files\n"
                "  - The scheduler produced no work items (check source config)\n"
                "  - A download exception was not captured (network/SSL/timeout)"
            )
            raise RuntimeError(
                "Dataset bootstrap produced no documents. Diagnostics:\n"
                + diag_summary
                + "\nSuggestions: Pick a different version, paste a custom URL, or use Starter dataset."
            )

        done_state = "DONE" if int(state["processed_bytes"]) >= int(options.target_bytes) else "DONE_EXHAUSTED"
        if done_state == "DONE_EXHAUSTED" and stop_reason == "CONTINUE":
            stop_reason = "SOURCES_EXHAUSTED"
        progress_cb(
            {
                "stage": "done",
                "done_state": done_state,
                "processed_bytes": int(state["processed_bytes"]),
                "downloaded_bytes": int(state.get("downloaded_bytes") or 0),
                "doc_count": int(state["doc_count"]),
                "shards": len(shards),
                "target_bytes": int(options.target_bytes),
                "percent": min(100.0, float(int(state["processed_bytes"])) * 100.0 / max(1, int(options.target_bytes))),
                "sources_exhausted": done_state == "DONE_EXHAUSTED",
                "stop_reason": stop_reason,
                "queue_remaining": len(scheduler.diagnostics().get("queued", [])),
                "providers_exhausted": exhausted_info,
            }
        )

        manifest = {
            "schema_version": "animica.ena.dataset.v2",
            "dataset_name": options.name,
            "target_bytes": int(options.target_bytes),
            "total_bytes": int(state["processed_bytes"]),
            "doc_count": int(state["doc_count"]),
            "state": done_state,
            "stop_reason": stop_reason,
            "providers_exhausted": exhausted_info,
            "shards": shards,
            "provenance": [
                {"source": p.source_name, "version": p.source_version, "cache_path": str((self._cache_root / p.source_name / p.source_version))}
                for p in providers.values()
            ],
            "download_diagnostics": manager.diagnostics(),
            "downloaded_bytes": int(state.get("downloaded_bytes") or 0),
            "extracted_bytes": int(state.get("extracted_bytes") or 0),
            "sources_exhausted_before_target": done_state == "DONE_EXHAUSTED",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "bootstrap_plan": str(plan_path),
        }
        manifest_path = target_dir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        dedup_ratio = 1.0 - (len(dedup_seen) / max(before_count, 1))
        stats = {
            "dedup_ratio": dedup_ratio,
            "language_counts": lang_counts,
            "source_counts": source_counts,
            "length_histogram": _length_histogram(shard_writer.lengths),
            "download_diagnostics": manager.diagnostics(),
        }
        stats_path = target_dir / "stats.json"
        stats_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")

        state.update({"completed": done_state == "DONE", "cancelled": False, "manifest_path": str(manifest_path), "state": done_state})
        self._save_state(state_path, state)
        _persist_plan()
        return {
            "dataset_dir": str(target_dir),
            "manifest_path": str(manifest_path),
            "stats_path": str(stats_path),
            "build_state": str(state_path),
            "bootstrap_plan": str(plan_path),
            "manifest": manifest,
            "stats": stats,
            "diagnostics": manager.diagnostics(),
            "state": done_state,
            "stop_reason": stop_reason,
        }

    def _provider_settings(self) -> dict[str, Any]:
        raw = self._source_settings.get("providers")
        return dict(raw) if isinstance(raw, dict) else {}

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


def _read_error_excerpt(exc: HTTPError) -> str:
    try:
        raw = exc.read(200)
    except Exception:
        return ""
    return raw.decode("utf-8", errors="replace")


def _looks_binary(raw: bytes) -> bool:
    if not raw:
        return False
    if b"\x00" in raw:
        return True
    sample = raw[:1024]
    non_text = sum(1 for b in sample if b < 9 or (13 < b < 32))
    return non_text / max(1, len(sample)) > 0.25
