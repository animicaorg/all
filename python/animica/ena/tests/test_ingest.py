from __future__ import annotations

from pathlib import Path

from animica.ena.config import load_ena_config
from animica.ena.ingest import Crawler, FetchOutcome, Fetcher, records_from_fetch


def test_fetch_and_crawl_respect_policy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    config = load_ena_config()
    config.network.allow_domains = ["docs.animica.test"]

    index_html = b"""
    <html><head><title>Docs Home</title></head>
    <body>
      <h1>Docs</h1>
      <p>Sync keeps nodes aligned.</p>
      <a href="/page.html">Page</a>
    </body></html>
    """
    page_html = b"""
    <html><head><title>Sync FAQ</title></head>
    <body>
      <h2>How does sync work?</h2>
      <p>It downloads headers, validates them, and fetches state on demand.</p>
    </body></html>
    """

    first = FetchOutcome(
        url="https://docs.animica.test/index.html",
        canonical_url="https://docs.animica.test/index.html",
        status_code=200,
        mime_type="text/html",
        content=index_html,
        text="Docs Sync keeps nodes aligned.",
        links=["/page.html"],
        metadata={"title": "Docs Home", "description": None, "headings": ["Docs"]},
    )
    second = FetchOutcome(
        url="https://docs.animica.test/page.html",
        canonical_url="https://docs.animica.test/page.html",
        status_code=200,
        mime_type="text/html",
        content=page_html,
        text="How does sync work? It downloads headers, validates them, and fetches state on demand.",
        links=[],
        metadata={"title": "Sync FAQ", "description": None, "headings": ["How does sync work?"]},
    )

    class FakeFetcher:
        def __init__(self, policy):
            self.policy = policy

        def fetch(self, url: str) -> FetchOutcome:
            return second if url.endswith("page.html") else first

    records = records_from_fetch(first)
    assert records[0]["title"] == "Docs Home"
    assert any("page.html" in link for link in records[0]["links"])

    crawler = Crawler(FakeFetcher(config.network))
    crawled = crawler.crawl(["https://docs.animica.test/index.html"], max_depth=1, max_requests=5)
    assert len(crawled) >= 2
    assert any("How does sync work?" in row.get("content_text", "") for row in crawled)


def test_fetch_blocked_by_allowlist(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    config = load_ena_config()
    config.network.allow_domains = ["docs.animica.org"]
    fetcher = Fetcher(config.network)
    try:
        fetcher.fetch("https://example.com")
    except ValueError as exc:
        assert "blocked by policy" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected allowlist policy failure")
