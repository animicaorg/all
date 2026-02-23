from __future__ import annotations

from animica_studio.services.dataset_evolution_engine import DatasetEvolutionEngine, EvolutionQuotas


def test_preview_plan_contains_safe_sources() -> None:
    engine = DatasetEvolutionEngine()
    plan = engine.preview_next_plan(
        run_report={"metrics": {"loss": 1.2, "eval_metrics": {"eval_math": 0.4, "eval_reasoning": 0.6}}},
        quotas=EvolutionQuotas(),
        quality_level="quality",
    )
    assert plan["approved_sources"] == ["wikipedia", "arxiv"]
    assert plan["additions"]


def test_apply_plan_registers_version(tmp_path, monkeypatch) -> None:
    engine = DatasetEvolutionEngine()
    monkeypatch.setattr(engine, "_registry_path", tmp_path / "registry.json")
    engine._init_registry()
    plan = {
        "approved_sources": ["wikipedia", "arxiv"],
        "additions": [{"topic": "math", "max_documents": 20}],
    }
    out = engine.apply_plan(plan, EvolutionQuotas(retain_last_versions=2), "unit")
    reg = engine.load_registry()
    assert out["manifest_path"]
    assert len(reg["versions"]) == 1
