from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .ingest import TEXT_EXTENSIONS, extract_local_path
from .models import EnaConfigModel, IndexRecord, RepoChunk, SearchHit
from .providers import ProviderError, create_embedding_provider
from .store import EnaStore
from .text import normalize_text, sha256_hex, stable_id


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "target",
    ".venv",
    "venv",
}


def _chunk_markdown(path: Path, text: str, chunk_size: int) -> List[Dict[str, Any]]:
    lines = text.splitlines()
    chunks: List[Dict[str, Any]] = []
    current: List[str] = []
    current_start = 1
    current_title = path.name
    for index, line in enumerate(lines, start=1):
        if line.startswith("#") and current:
            content = "\n".join(current).strip()
            if content:
                chunks.append(
                    {
                        "chunk_id": stable_id("chunk", str(path), str(current_start), str(index)),
                        "source": str(path),
                        "title": current_title,
                        "content": content[:chunk_size],
                        "metadata": {"path": str(path), "start_line": current_start, "end_line": index - 1},
                    }
                )
            current = []
            current_start = index
            current_title = line.lstrip("# ").strip() or path.name
        current.append(line)
    if current:
        content = "\n".join(current).strip()
        if content:
            chunks.append(
                {
                    "chunk_id": stable_id("chunk", str(path), str(current_start), str(len(lines))),
                    "source": str(path),
                    "title": current_title,
                    "content": content[:chunk_size],
                    "metadata": {"path": str(path), "start_line": current_start, "end_line": len(lines)},
                }
            )
    return chunks


def _chunk_text_by_lines(path: Path, text: str, chunk_lines: int, overlap: int) -> List[Dict[str, Any]]:
    lines = text.splitlines()
    chunks: List[Dict[str, Any]] = []
    start = 0
    while start < len(lines):
        end = min(len(lines), start + chunk_lines)
        content = "\n".join(lines[start:end]).strip()
        if content:
            chunk = RepoChunk(
                chunk_id=stable_id("chunk", str(path), str(start + 1), str(end)),
                path=str(path),
                content=content,
                start_line=start + 1,
                end_line=end,
                content_sha256=sha256_hex(content),
                provenance={"path": str(path)},
            )
            chunks.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "source": chunk.path,
                    "title": path.name,
                    "content": chunk.content,
                    "metadata": chunk.model_dump(mode="json"),
                }
            )
        if end >= len(lines):
            break
        start = max(end - overlap, start + 1)
    return chunks


def _chunk_path(path: Path, *, chunk_lines: int = 80, overlap: int = 10) -> List[Dict[str, Any]]:
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".wasm", ".bin"}:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    if not normalize_text(text):
        return []
    if path.suffix.lower() in {".md", ".rst"}:
        return _chunk_markdown(path, text, chunk_lines * 120)
    return _chunk_text_by_lines(path, text, chunk_lines, overlap)


class IndexManager:
    def __init__(self, store: EnaStore, config: EnaConfigModel):
        self.store = store
        self.config = config

    def list_indexes(self) -> List[IndexRecord]:
        return self.store.list_indexes()

    def embeddings_test(self, provider_name: Optional[str] = None) -> Dict[str, Any]:
        provider = create_embedding_provider(self.config, provider_name=provider_name)
        return provider.test()

    def index_path(
        self,
        path: Path,
        *,
        index_name: Optional[str] = None,
        reset: bool = False,
        chunk_lines: int = 80,
        overlap: int = 10,
        embedding_provider_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        path = path.resolve()
        name = index_name or stable_id("index", str(path))
        if reset:
            self.store.clear_index(name)

        chunks: List[Dict[str, Any]] = []
        total_files = 0
        for candidate in self._iter_files(path):
            total_files += 1
            chunks.extend(_chunk_path(candidate, chunk_lines=chunk_lines, overlap=overlap))

        total_chunks = self._store_chunks(name, chunks, embedding_provider_name=embedding_provider_name)
        return self._finalize_index(
            name,
            root=str(path),
            chunks=chunks,
            total_files=total_files,
            total_chunks=total_chunks,
            embedding_provider_name=embedding_provider_name,
        )

    def index_jsonl_records(
        self,
        jsonl_path: Path,
        *,
        index_name: Optional[str] = None,
        reset: bool = False,
        embedding_provider_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        name = index_name or stable_id("index", str(jsonl_path.resolve()))
        if reset:
            self.store.clear_index(name)

        chunks: List[Dict[str, Any]] = []
        total_records = 0
        with jsonl_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
                total_records += 1
                text = normalize_text(
                    record.get("content_text")
                    or record.get("content")
                    or record.get("answer")
                    or json.dumps(record, ensure_ascii=False)
                )
                if not text:
                    continue
                chunks.append(
                    {
                        "chunk_id": stable_id("chunk", str(jsonl_path), str(line_number)),
                        "source": record.get("canonical_url") or record.get("url") or str(jsonl_path),
                        "title": record.get("title") or record.get("question"),
                        "content": text,
                        "metadata": {"record": record, "line_number": line_number, "path": str(jsonl_path)},
                    }
                )

        total_chunks = self._store_chunks(name, chunks, embedding_provider_name=embedding_provider_name)
        return self._finalize_index(
            name,
            root=str(jsonl_path.resolve()),
            chunks=chunks,
            total_files=total_records,
            total_chunks=total_chunks,
            embedding_provider_name=embedding_provider_name,
        )

    def search(
        self,
        query: str,
        *,
        index_name: Optional[str] = None,
        limit: int = 8,
        semantic: Optional[bool] = None,
        strategy: Optional[str] = None,
        embedding_provider_name: Optional[str] = None,
    ) -> List[SearchHit]:
        if strategy is None:
            if semantic is False:
                strategy = "keyword"
            elif semantic is True:
                strategy = "semantic"
            else:
                strategy = "hybrid"

        query_embedding = None
        if strategy in {"semantic", "hybrid"}:
            try:
                provider = create_embedding_provider(self.config, provider_name=embedding_provider_name)
                if provider.capabilities().get("semantic"):
                    embeddings = provider.embed_texts([query])
                    query_embedding = embeddings[0] if embeddings else None
                else:
                    strategy = "keyword"
            except ProviderError:
                strategy = "keyword"

        return self.store.search_chunks(
            query,
            index_name=index_name,
            limit=limit,
            query_embedding=query_embedding,
            strategy=strategy,
        )

    def summarize_hits(self, hits: Iterable[SearchHit]) -> List[Dict[str, Any]]:
        return [hit.model_dump(mode="json") for hit in hits]

    def _store_chunks(
        self,
        index_name: str,
        chunks: List[Dict[str, Any]],
        *,
        embedding_provider_name: Optional[str] = None,
    ) -> int:
        provider_name = embedding_provider_name or self.config.default_embedding_provider
        try:
            provider = create_embedding_provider(self.config, provider_name=provider_name)
            if provider.capabilities().get("semantic"):
                batch_size = max(int(provider.config.batch_size), 1)
                for start in range(0, len(chunks), batch_size):
                    batch = chunks[start : start + batch_size]
                    vectors = provider.embed_texts([item["content"] for item in batch])
                    for item, vector in zip(batch, vectors):
                        item["embedding"] = vector
            elif provider_name == "disabled":
                for item in chunks:
                    item["embedding"] = []
        except ProviderError:
            for item in chunks:
                item["embedding"] = []
        return self.store.upsert_chunks(index_name, chunks)

    def _finalize_index(
        self,
        index_name: str,
        *,
        root: str,
        chunks: List[Dict[str, Any]],
        total_files: int,
        total_chunks: int,
        embedding_provider_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        provider_name = embedding_provider_name or self.config.default_embedding_provider
        provider_model = None
        retrieval_mode = "keyword"
        try:
            provider = create_embedding_provider(self.config, provider_name=provider_name)
            provider_model = provider.config.model or None
            retrieval_mode = "hybrid" if provider.capabilities().get("semantic") else "keyword"
        except ProviderError:
            provider_name = "disabled"
        record = IndexRecord(
            index_name=index_name,
            root=root,
            chunk_count=total_chunks,
            source_count=total_files,
            embedding_provider=provider_name,
            embedding_model=provider_model,
            retrieval_mode=retrieval_mode,
            metadata={"chunked_sources": len({item["source"] for item in chunks})},
        )
        self.store.save_index(record)
        return {
            "index_name": index_name,
            "root": root,
            "files_indexed": total_files,
            "chunks_indexed": total_chunks,
            "embedding_provider": provider_name,
            "embedding_model": provider_model,
            "retrieval_mode": retrieval_mode,
        }

    def _iter_files(self, root: Path) -> Iterable[Path]:
        if root.is_file():
            yield root
            return
        for candidate in root.rglob("*"):
            if candidate.is_dir():
                continue
            if any(part in SKIP_DIRS for part in candidate.parts):
                continue
            if candidate.suffix.lower() in TEXT_EXTENSIONS or candidate.suffix.lower() in {".c", ".h", ".cpp", ".java"}:
                yield candidate
                continue
            try:
                if candidate.stat().st_size <= 200_000:
                    extract_local_path(candidate)
                    yield candidate
            except Exception:
                continue
