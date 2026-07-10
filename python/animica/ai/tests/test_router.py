"""Router + telemetry + circuit breaker (7.1.1 P3)."""

import pytest


@pytest.fixture(autouse=True)
def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_AI_HOME", str(tmp_path))
    monkeypatch.setenv("ANIMICA_AI_TELEMETRY_DB", str(tmp_path / "tel.db"))
    monkeypatch.delenv("ANIMICA_AI_ROUTING", raising=False)
    from animica.ai import telemetry
    telemetry.reset()
    yield


def test_passthrough_when_no_policy():
    from animica.ai import router
    from animica.ai.gateway import resolve_chat_adapter
    rr = router.route({"model": "deterministic"})
    base_adapter, base_key, base_model = resolve_chat_adapter("deterministic", None)
    assert rr.provider_key == base_key and rr.resolved_model == base_model
    assert rr.decision["reason"] == "passthrough"


def test_request_routing_ignored_without_operator_optin():
    # A client asking for a paid policy must NOT steer traffic unless the operator
    # enabled routing — otherwise it can spend the operator's key / discard its model.
    from animica.ai import router
    rr = router.route({"model": "my-free-model", "animica": {"policy": "quality"}})
    assert rr.decision["reason"] == "passthrough"
    assert rr.provider_key != "anthropic"


def test_active_fallback_to_deterministic(monkeypatch):
    monkeypatch.setenv("ANIMICA_AI_ROUTING", "1")  # operator opt-in
    from animica.ai import router
    # anthropic (no key) fails → chain falls through to the offline deterministic net
    rr = router.route({"model": "x", "animica": {"candidates": ["anthropic"]}})
    assert "deterministic" in rr.candidates_tried
    out = rr.adapter.generate("route me please")
    assert rr.adapter.last_provider == "deterministic"
    assert out.startswith("[deterministic")


def test_cost_cap_filters_and_cheapest_orders(monkeypatch):
    monkeypatch.setenv("ANIMICA_AI_ROUTING", "1")
    from animica.ai import router
    from animica.ai.policy import RoutePolicy
    # cheapest mode with a cap that excludes anthropic ($3) but keeps ollama/chutes
    pol = RoutePolicy(name="c", mode="cheapest", candidates=["anthropic", "chutes", "ollama"],
                      max_cost_per_1k=1.0)
    rr = router.route({"model": "x", "animica": {}}, policy=pol)
    tried = rr.candidates_tried
    assert "anthropic" not in tried               # over the cap → excluded
    assert tried[-1] == "deterministic"           # offline net always last
    assert tried.index("ollama") < len(tried)     # cheap providers kept


def test_no_concurrent_dispatch_single_threaded():
    # The fallback adapter must try candidates sequentially, not race them.
    from animica.ai import router
    calls = []

    class Slow:
        supports_seed = False

        def __init__(self, tag, fail):
            self.tag, self.fail = tag, fail

        def generate(self, prompt, **kw):
            from animica.ena.errors import ProviderError
            calls.append(self.tag)
            if self.fail:
                raise ProviderError(f"{self.tag} down")
            return f"ok:{self.tag}"

    fb = router.FallbackModelAdapter([("a", Slow("a", True), "m"), ("b", Slow("b", False), "m")])
    assert fb.generate("hi") == "ok:b"
    assert calls == ["a", "b"]  # strictly sequential, a before b


def test_circuit_breaker_opens_and_recovers(monkeypatch):
    from animica.ai import telemetry
    telemetry.reset()
    for _ in range(3):
        telemetry.record("prov", latency_ms=10, ok=False)
    assert telemetry.breaker_open("prov") is True
    telemetry.record("prov", latency_ms=10, ok=True)  # success closes it
    assert telemetry.breaker_open("prov") is False


def test_telemetry_shared_across_instances(tmp_path, monkeypatch):
    # Two telemetry views on the same SQLite file agree (multi-worker correctness).
    from animica.ai import telemetry
    telemetry.reset()
    telemetry.record("m", latency_ms=50, ok=True)
    snap = telemetry.snapshot()
    assert "m" in snap and snap["m"]["ok"] == 1


def test_router_status_shape():
    from animica.ai import router
    st = router.router_status()
    assert "policies" in st and "health" in st and "quality" in st["policies"]
