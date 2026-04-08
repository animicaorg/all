from __future__ import annotations

import csv
import io
import json
import re
import time
from collections import deque
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser
from xml.etree import ElementTree

import httpx

from .models import CodeFileSummary, DocPageRecord, FAQRecord, NetworkPolicy, WebPageRecord
from .text import normalize_text, sha256_hex, stable_id


TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".rst",
    ".py",
    ".rs",
    ".go",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".tsv",
    ".jsonl",
}


class _HTMLExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.skip_depth = 0
        self.title: List[str] = []
        self.text_parts: List[str] = []
        self.links: List[str] = []
        self.headings: List[str] = []
        self.current_heading_tag: Optional[str] = None
        self.current_heading: List[str] = []
        self.description: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attr_map = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self.skip_depth += 1
        if tag == "title":
            self.in_title = True
        if tag in {"h1", "h2", "h3", "h4"}:
            self.current_heading_tag = tag
            self.current_heading = []
        if tag == "a" and attr_map.get("href"):
            self.links.append(attr_map["href"] or "")
        if tag == "meta" and (attr_map.get("name") or "").lower() == "description":
            self.description = attr_map.get("content")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self.skip_depth > 0:
            self.skip_depth -= 1
        if tag == "title":
            self.in_title = False
        if tag == self.current_heading_tag:
            heading = normalize_text("".join(self.current_heading))
            if heading:
                self.headings.append(heading)
            self.current_heading_tag = None
            self.current_heading = []

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        if self.in_title:
            self.title.append(data)
        if self.current_heading_tag:
            self.current_heading.append(data)
        text = normalize_text(data)
        if text:
            self.text_parts.append(text)


@dataclass
class FetchOutcome:
    url: str
    canonical_url: str
    status_code: int
    mime_type: str
    content: bytes
    text: str
    links: List[str]
    metadata: Dict[str, Any]


def canonicalize_url(url: str) -> str:
    parsed = urlparse(url)
    query_items = sorted(parse_qsl(parsed.query, keep_blank_values=True))
    normalized = parsed._replace(
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc.lower(),
        fragment="",
        query=urlencode(query_items),
    )
    return urlunparse(normalized)


def _hostname(url: str) -> str:
    return urlparse(url).hostname or ""


def _domain_allowed(url: str, policy: NetworkPolicy) -> bool:
    host = _hostname(url)
    if policy.deny_domains and any(host == denied or host.endswith(f".{denied}") for denied in policy.deny_domains):
        return False
    if policy.allow_domains:
        return any(host == allowed or host.endswith(f".{allowed}") for allowed in policy.allow_domains)
    return True


class DomainRateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.per_minute = per_minute
        self.seen: Dict[str, deque[float]] = {}

    def wait_if_needed(self, domain: str) -> None:
        if self.per_minute <= 0:
            return
        bucket = self.seen.setdefault(domain, deque())
        now = time.time()
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= self.per_minute:
            sleep_for = 60 - (now - bucket[0])
            if sleep_for > 0:
                time.sleep(min(sleep_for, 2.0))
        now = time.time()
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        bucket.append(now)


class RobotsGuard:
    def __init__(self, user_agent: str):
        self.user_agent = user_agent
        self.parsers: Dict[str, RobotFileParser] = {}

    def allows(self, url: str) -> bool:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return True
        key = f"{parsed.scheme}://{parsed.netloc}"
        parser = self.parsers.get(key)
        if parser is None:
            parser = RobotFileParser()
            parser.set_url(f"{key}/robots.txt")
            try:
                parser.read()
            except Exception:
                return True
            self.parsers[key] = parser
        try:
            return parser.can_fetch(self.user_agent, url)
        except Exception:
            return True


class Fetcher:
    def __init__(self, policy: NetworkPolicy):
        self.policy = policy
        self.rate_limiter = DomainRateLimiter(policy.rate_limit_per_domain_per_minute)
        self.robots = RobotsGuard(policy.user_agent)

    def fetch(self, url: str) -> FetchOutcome:
        if not _domain_allowed(url, self.policy):
            raise ValueError(f"URL blocked by policy: {url}")
        if self.policy.respect_robots and not self.robots.allows(url):
            raise ValueError(f"URL blocked by robots policy: {url}")

        domain = _hostname(url)
        self.rate_limiter.wait_if_needed(domain)

        last_error: Optional[Exception] = None
        headers = {"User-Agent": self.policy.user_agent}
        timeout = httpx.Timeout(self.policy.request_timeout_seconds)
        for attempt in range(self.policy.retries + 1):
            try:
                with httpx.Client(timeout=timeout, headers=headers, follow_redirects=True) as client:
                    response = client.get(url)
                response.raise_for_status()
                content = response.content[: self.policy.size_limit_bytes]
                mime_type = response.headers.get("content-type", "application/octet-stream").split(";")[0]
                text, links, metadata = extract_text_and_links(url, content, mime_type)
                return FetchOutcome(
                    url=url,
                    canonical_url=canonicalize_url(str(response.url)),
                    status_code=response.status_code,
                    mime_type=mime_type,
                    content=content,
                    text=text,
                    links=links,
                    metadata=metadata,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt < self.policy.retries:
                    time.sleep(self.policy.backoff_seconds * (attempt + 1))
        raise RuntimeError(f"fetch failed for {url}: {last_error}")


def extract_text_and_links(
    source: str,
    content: bytes,
    mime_type: str,
) -> Tuple[str, List[str], Dict[str, Any]]:
    text = ""
    links: List[str] = []
    metadata: Dict[str, Any] = {}
    lower_source = source.lower()

    if mime_type in {"text/html", "application/xhtml+xml"} or lower_source.endswith((".html", ".htm")):
        parser = _HTMLExtractor()
        parser.feed(content.decode("utf-8", errors="ignore"))
        text = normalize_text(" ".join(parser.text_parts))
        links = parser.links
        metadata = {
            "title": normalize_text("".join(parser.title)) or None,
            "description": parser.description,
            "headings": parser.headings,
        }
        return text, links, metadata

    if mime_type in {"text/markdown", "text/plain"} or lower_source.endswith((".md", ".txt", ".rst")):
        text = content.decode("utf-8", errors="ignore")
        headings = [line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")]
        return normalize_text(text), links, {"headings": headings}

    if mime_type in {"application/json", "application/ld+json"} or lower_source.endswith((".json", ".jsonl")):
        raw_text = content.decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(raw_text)
            text = normalize_text(json.dumps(parsed, ensure_ascii=False))
        except Exception:
            text = normalize_text(raw_text)
        return text, links, {}

    if mime_type in {"application/rss+xml", "application/atom+xml", "text/xml", "application/xml"}:
        try:
            root = ElementTree.fromstring(content)
            items = []
            for element in root.iter():
                if element.text and normalize_text(element.text):
                    items.append(normalize_text(element.text))
            text = normalize_text(" ".join(items))
        except Exception:
            text = normalize_text(content.decode("utf-8", errors="ignore"))
        return text, links, {}

    if lower_source.endswith(".csv") or mime_type == "text/csv":
        decoded = content.decode("utf-8", errors="ignore")
        rows = list(csv.reader(io.StringIO(decoded)))
        text = normalize_text(" ".join(" | ".join(row) for row in rows[:50]))
        return text, links, {"rows": len(rows)}

    if lower_source.endswith(".tsv"):
        decoded = content.decode("utf-8", errors="ignore")
        rows = list(csv.reader(io.StringIO(decoded), delimiter="\t"))
        text = normalize_text(" ".join(" | ".join(row) for row in rows[:50]))
        return text, links, {"rows": len(rows)}

    if lower_source.endswith(".pdf") or mime_type == "application/pdf":
        try:
            import pypdf  # type: ignore

            reader = pypdf.PdfReader(io.BytesIO(content))
            text = normalize_text(" ".join(page.extract_text() or "" for page in reader.pages[:10]))
            metadata = {"pages": len(reader.pages)}
            return text, links, metadata
        except Exception:
            return "", links, {"note": "pdf_text_unavailable"}

    return normalize_text(content.decode("utf-8", errors="ignore")), links, {}


def records_from_fetch(outcome: FetchOutcome) -> List[Dict[str, Any]]:
    sha = sha256_hex(outcome.content)
    base_kwargs = dict(
        url=outcome.url,
        canonical_url=outcome.canonical_url,
        title=outcome.metadata.get("title"),
        description=outcome.metadata.get("description"),
        content_text=outcome.text,
        links=outcome.links,
        headings=outcome.metadata.get("headings", []),
        status_code=outcome.status_code,
        mime_type=outcome.mime_type,
        content_sha256=sha,
        source_hash=sha,
        provenance={"fetched_via": "http", "depth": outcome.metadata.get("depth", 0)},
    )

    is_doc = "/docs" in outcome.canonical_url or "doc" in (outcome.metadata.get("title") or "").lower()
    record = DocPageRecord(section_count=len(base_kwargs["headings"]), **base_kwargs) if is_doc else WebPageRecord(**base_kwargs)
    records: List[Dict[str, Any]] = [record.model_dump(mode="json")]
    records.extend(faq_records(record.canonical_url, outcome.text))
    return records


def faq_records(source: str, text: str) -> List[Dict[str, Any]]:
    lines = [line.strip() for line in text.split("  ") if line.strip()]
    faqs: List[Dict[str, Any]] = []
    for index, line in enumerate(lines[:-1]):
        if line.endswith("?") and len(line.split()) <= 30:
            answer = lines[index + 1]
            if answer and not answer.endswith("?"):
                faqs.append(
                    FAQRecord(
                        source=source,
                        question=line,
                        answer=answer,
                        content_sha256=sha256_hex(f"{line}\n{answer}"),
                        provenance={"source": source},
                    ).model_dump(mode="json")
                )
    return faqs


def extract_local_path(path: Path) -> List[Dict[str, Any]]:
    content = path.read_bytes()
    text, _, metadata = extract_text_and_links(str(path), content, _guess_mime_type(path))
    sha = sha256_hex(content)
    if path.suffix in {".py", ".rs", ".go", ".js", ".ts", ".tsx", ".jsx"}:
        symbols = re.findall(r"^\s*(?:def|class|fn|export function|function)\s+([A-Za-z0-9_]+)", path.read_text(encoding="utf-8", errors="ignore"), re.MULTILINE)
        summary = CodeFileSummary(
            path=str(path),
            language=path.suffix.lstrip("."),
            line_count=len(path.read_text(encoding="utf-8", errors="ignore").splitlines()),
            symbol_hints=symbols[:25],
            summary=normalize_text(" ".join(text.split()[:80])),
            content_sha256=sha,
            provenance={"path": str(path)},
        )
        return [summary.model_dump(mode="json")]
    return [
        WebPageRecord(
            url=str(path),
            canonical_url=str(path.resolve()),
            title=path.name,
            description=None,
            content_text=text,
            links=[],
            headings=metadata.get("headings", []),
            status_code=200,
            mime_type=_guess_mime_type(path),
            content_sha256=sha,
            source_hash=sha,
            provenance={"path": str(path)},
        ).model_dump(mode="json")
    ]


def _guess_mime_type(path: Path) -> str:
    if path.suffix in {".html", ".htm"}:
        return "text/html"
    if path.suffix in {".md", ".txt", ".rst"}:
        return "text/plain"
    if path.suffix == ".json":
        return "application/json"
    if path.suffix in {".yaml", ".yml"}:
        return "text/plain"
    if path.suffix == ".pdf":
        return "application/pdf"
    if path.suffix == ".csv":
        return "text/csv"
    return "text/plain"


def export_jsonl(records: Iterable[Dict[str, Any]], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return out_path


class Crawler:
    def __init__(self, fetcher: Fetcher):
        self.fetcher = fetcher

    def crawl(self, seeds: List[str], *, max_depth: Optional[int] = None, max_requests: Optional[int] = None) -> List[Dict[str, Any]]:
        depth_limit = self.fetcher.policy.max_depth if max_depth is None else max_depth
        request_limit = self.fetcher.policy.max_requests if max_requests is None else max_requests
        queue: deque[Tuple[str, int]] = deque((seed, 0) for seed in seeds)
        seen = set()
        results: List[Dict[str, Any]] = []

        while queue and len(seen) < request_limit:
            url, depth = queue.popleft()
            canonical = canonicalize_url(url)
            if canonical in seen:
                continue
            seen.add(canonical)
            outcome = self.fetcher.fetch(url)
            outcome.metadata["depth"] = depth
            records = records_from_fetch(outcome)
            results.extend(records)

            if depth >= depth_limit:
                continue
            for link in outcome.links:
                absolute = canonicalize_url(urljoin(outcome.canonical_url, link))
                if absolute not in seen and _domain_allowed(absolute, self.fetcher.policy):
                    queue.append((absolute, depth + 1))
        return results


def load_seed_file(path: Path) -> List[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
