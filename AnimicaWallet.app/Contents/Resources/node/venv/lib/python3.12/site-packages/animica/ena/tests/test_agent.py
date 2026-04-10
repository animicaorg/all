from __future__ import annotations

from pathlib import Path

from animica.ena.agent import AgentRunner
from animica.ena.config import load_ena_config
from animica.ena.models import TaskSpec
from animica.ena.store import EnaStore


def test_agent_can_index_and_answer_from_repo(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ANIMICA_ENA_HOME", str(tmp_path / "ena_home"))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "sync.md").write_text(
        """
        # Sync Architecture

        Sync downloads headers first, validates ancestry, and then pulls state as needed.
        The pipeline keeps nodes aligned with the canonical chain.
        """,
        encoding="utf-8",
    )

    config = load_ena_config()
    store = EnaStore(config)
    runner = AgentRunner(config=config, store=store)

    result = runner.run(
        TaskSpec(
            task="how does sync work?",
            context_paths=[str(repo)],
            output_format="json",
        )
    )
    assert "headers" in result["answer"].lower()
    assert result["citations"]
    assert any("sync.md" in citation["source"] for citation in result["citations"])
