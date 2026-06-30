"""Tests for the `animica ai` namespace (5.2.0).

Covers CLI wiring (group + commands exist), `ai doctor` behavior (runs, JSON
schema, never crashes on a probe), and the top-level config.toml read/write
round-trip. Network/RPC/GPU probes are best-effort and must degrade gracefully,
so these tests assert structure/contract rather than environment-specific values.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from animica.cli import app_config
from animica.cli import ai as ai_mod
from animica.cli.ai import app as ai_app
from animica.cli.main import app as root_app

runner = CliRunner()


def test_ai_group_registered_on_root():
    names = [g.name for g in root_app.registered_groups]
    assert "ai" in names, "`ai` group must be mounted on the root CLI"


def test_ai_doctor_command_exists():
    cmds = [c.name for c in ai_app.registered_commands]
    assert "doctor" in cmds


def test_ai_doctor_runs_and_emits_table():
    # Invoke through the root app (matches real `animica ai doctor`); invoking the
    # single-command `ai_app` standalone would trigger Typer's single-command
    # collapse and read "doctor" as an argument.
    res = runner.invoke(root_app, ["ai", "doctor"])
    # doctor exits 0 (no failures) or 1 (a hard failure) — never crashes (2+/exception).
    assert res.exit_code in (0, 1), res.output
    assert "doctor" in res.output.lower()
    assert "Python" in res.output


def test_ai_doctor_json_schema():
    res = runner.invoke(root_app, ["ai", "doctor", "--json"])
    assert res.exit_code in (0, 1)
    data = json.loads(res.stdout)
    assert set(data) >= {"ok", "failed", "warnings", "checks"}
    assert isinstance(data["checks"], list) and data["checks"]
    for c in data["checks"]:
        assert set(c) >= {"name", "status", "detail", "fix"}
        assert c["status"] in {"pass", "warn", "fail"}
    # ok must be consistent with the failure count.
    assert data["ok"] == (data["failed"] == 0)
    # core always-present checks
    got = {c["name"] for c in data["checks"]}
    assert {"Python", "OS", "CPU", "Node RPC", "Wallet"} <= got


def test_ai_doctor_json_exit_matches_failures():
    res = runner.invoke(root_app, ["ai", "doctor", "--json"])
    data = json.loads(res.stdout)
    assert res.exit_code == (1 if data["failed"] else 0)


def test_config_toml_roundtrip(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("ANIMICA_CONFIG", str(cfg))
    # missing file -> {}
    assert app_config.load_config() == {}
    # write scalars + a table, including a bracketed string that must survive
    app_config.save_config({
        "mode": "consumer",
        "default_model": "animica/default",
        "ai": {"payout_wallet": "anim1demo", "max_spend_anm": 5, "use_gpu": False},
    })
    loaded = app_config.load_config()
    assert loaded["mode"] == "consumer"
    assert loaded["ai"]["payout_wallet"] == "anim1demo"
    assert loaded["ai"]["max_spend_anm"] == 5
    assert loaded["ai"]["use_gpu"] is False
    # get/set helpers
    assert app_config.get("default_model") == "animica/default"
    assert app_config.get("payout_wallet", section="ai") == "anim1demo"
    app_config.set_value("max_spend_anm", 12, section="ai")
    assert app_config.get("max_spend_anm", section="ai") == 12
    # file perms are restrictive (it can hold a payout address)
    assert (cfg.stat().st_mode & 0o777) == 0o600


# --------------------------------------------------------------------------- #
# setup / models / chat — network mocked (per the 5.2.0 coding rules: no real
# network or GPU in tests).
# --------------------------------------------------------------------------- #
def test_is_embedding_model():
    assert ai_mod.is_embedding_model("nomic-embed-text:latest")
    assert ai_mod.is_embedding_model("mxbai-embed-large")
    assert not ai_mod.is_embedding_model("qwen2.5:7b")
    assert not ai_mod.is_embedding_model("deepseek-coder:6.7b")


def test_pick_chat_model_skips_embeddings():
    assert ai_mod._pick_chat_model(["nomic-embed-text", "qwen2.5:7b"]) == "qwen2.5:7b"
    assert ai_mod._pick_chat_model(["nomic-embed-text"]) == "nomic-embed-text"  # fallback
    assert ai_mod._pick_chat_model([]) is None


def test_setup_noninteractive_writes_config(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_CONFIG", str(tmp_path / "config.toml"))
    monkeypatch.setattr(ai_mod, "_list_ollama_models", lambda *a, **k: ["qwen2.5:7b"])
    res = runner.invoke(root_app, ["ai", "setup", "--yes", "--mode", "provider",
                                   "--provider", "ollama", "--model", "qwen2.5:7b",
                                   "--payout-wallet", "anim1demo", "--max-spend", "5", "--gpu"])
    assert res.exit_code == 0, res.output
    cfg = app_config.load_config()
    assert cfg["mode"] == "provider"
    assert cfg["ai"]["provider"] == "ollama"
    assert cfg["ai"]["default_model"] == "qwen2.5:7b"
    assert cfg["ai"]["payout_wallet"] == "anim1demo"
    assert cfg["ai"]["max_spend_anm"] == 5
    assert cfg["ai"]["use_gpu"] is True


def test_setup_autodetect_prefers_chat_model(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_CONFIG", str(tmp_path / "config.toml"))
    # embedding model listed first must NOT become the default chat model.
    monkeypatch.setattr(ai_mod, "_list_ollama_models",
                        lambda *a, **k: ["nomic-embed-text:latest", "qwen2.5:7b"])
    res = runner.invoke(root_app, ["ai", "setup", "--yes"])
    assert res.exit_code == 0, res.output
    cfg = app_config.load_config()
    assert cfg["ai"]["provider"] == "ollama"
    assert cfg["ai"]["default_model"] == "qwen2.5:7b"


def test_models_json_has_kind(monkeypatch):
    monkeypatch.setattr(ai_mod, "_list_ollama_models",
                        lambda *a, **k: ["qwen2.5:7b", "nomic-embed-text:latest"])
    res = runner.invoke(root_app, ["ai", "models", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert "models" in data and data["models"]
    by_name = {m["name"]: m for m in data["models"]}
    assert by_name["qwen2.5:7b"]["kind"] == "chat"
    assert by_name["nomic-embed-text:latest"]["kind"] == "embed"
    for m in data["models"]:
        assert set(m) >= {"name", "provider", "kind", "source", "default"}


def test_chat_oneshot_mocked(monkeypatch):
    class _FakeAdapter:
        def generate(self, prompt, *, system=None, history=None, max_tokens=None, temperature=None):
            assert history == []  # one-shot has no history
            return "pong: " + prompt

    class _FakeMP:
        provider = "ollama"
        model = "qwen2.5:7b"

    monkeypatch.setattr(ai_mod, "_resolve_adapter",
                        lambda provider, model, local: (_FakeAdapter(), _FakeMP(), "ollama"))
    res = runner.invoke(root_app, ["ai", "chat", "ping", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data == {"provider": "ollama", "model": "qwen2.5:7b", "response": "pong: ping"}


def test_chat_provider_error_is_friendly(monkeypatch):
    from animica.ena.providers import ProviderError

    class _BoomAdapter:
        def generate(self, *a, **k):
            raise ProviderError("connection refused")

    class _FakeMP:
        provider = "ollama"
        model = "qwen2.5:7b"

    monkeypatch.setattr(ai_mod, "_resolve_adapter",
                        lambda *a, **k: (_BoomAdapter(), _FakeMP(), "ollama"))
    res = runner.invoke(root_app, ["ai", "chat", "ping"])
    # graceful: exit 1 with a doctor hint, not a stack trace.
    assert res.exit_code == 1
    assert "doctor" in res.output.lower()


# --------------------------------------------------------------------------- #
# Marketplace — job / provider / earnings / benchmark. RPC + signing mocked, so
# NO node and NO ANM are ever touched (the spend-safety contract is asserted).
# --------------------------------------------------------------------------- #
from animica.ai import market as market_mod  # noqa: E402


def test_job_estimate_quote(monkeypatch):
    monkeypatch.setattr(market_mod, "estimate",
                        lambda *a, **k: {"cost_animica": 0.5, "tier": "standard",
                                         "providers": 3, "latency_ms": 1000})
    res = runner.invoke(root_app, ["ai", "job", "estimate", "hello", "--json"])
    assert res.exit_code == 0, res.output
    assert json.loads(res.stdout)["cost_animica"] == 0.5


def test_job_submit_refuses_without_confirmation(monkeypatch):
    # No --yes and CliRunner has no TTY → must refuse and NEVER sign/spend.
    monkeypatch.setattr(market_mod, "estimate",
                        lambda *a, **k: {"cost_animica": 0.5, "tier": "standard",
                                         "providers": 3, "latency_ms": 1000})
    signed = {"called": False}

    def _no_sign(**kw):
        signed["called"] = True
        return "deadbeef"

    monkeypatch.setattr("animica.wallet.payment.sign_payment_tx", _no_sign)
    res = runner.invoke(root_app, ["ai", "job", "submit", "do a thing"])
    assert res.exit_code == 1
    assert signed["called"] is False  # the key safety property: no spend without consent
    assert "refusing to spend" in res.output.lower() or "no tty" in res.output.lower()


def test_job_submit_estimate_only_never_spends(monkeypatch):
    monkeypatch.setattr(market_mod, "estimate",
                        lambda *a, **k: {"cost_animica": 0.5, "tier": "standard",
                                         "providers": 3, "latency_ms": 1000})
    signed = {"called": False}
    monkeypatch.setattr("animica.wallet.payment.sign_payment_tx",
                        lambda **kw: signed.__setitem__("called", True) or "x")
    res = runner.invoke(root_app, ["ai", "job", "submit", "x", "--estimate-only"])
    assert res.exit_code == 0
    assert signed["called"] is False


def test_job_submit_with_yes_signs_and_records(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIMICA_HOME", str(tmp_path))
    monkeypatch.setattr(market_mod, "estimate",
                        lambda *a, **k: {"cost_animica": 0.5, "tier": "standard",
                                         "providers": 3, "latency_ms": 1000})
    monkeypatch.setattr(market_mod, "treasury_address", lambda **k: "anim1treasury")
    monkeypatch.setattr(market_mod, "call", lambda *a, **k: 0)  # nonce lookup
    monkeypatch.setattr("animica.wallet.payment.sign_payment_tx", lambda **kw: "0xsigned")
    submitted = {}

    def _submit(spec, payment, **kw):
        submitted["spec"] = spec
        submitted["payment"] = payment
        return {"job_id": "0xjob", "accepted_tier": "standard",
                "payment_tx_hash": "0xtx", "payment_accepted": True, "provider_id": "p1"}

    monkeypatch.setattr(market_mod, "submit_job", _submit)
    res = runner.invoke(root_app, ["ai", "job", "submit", "run me", "--yes", "--json"])
    assert res.exit_code == 0, res.output
    assert submitted["payment"] == {"txn_hex": "0xsigned"}
    assert json.loads(res.stdout)["job_id"] == "0xjob"
    # recorded locally
    assert any(j["job_id"] == "0xjob" for j in market_mod.local_jobs())


def test_job_submit_blocks_over_spend_cap(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIMICA_CONFIG", str(tmp_path / "config.toml"))
    app_config.set_value("max_spend_anm", 0.1, section="ai")  # cap below the quote
    monkeypatch.setattr(market_mod, "estimate",
                        lambda *a, **k: {"cost_animica": 0.5, "tier": "standard",
                                         "providers": 3, "latency_ms": 1000})
    signed = {"called": False}
    monkeypatch.setattr("animica.wallet.payment.sign_payment_tx",
                        lambda **kw: signed.__setitem__("called", True) or "x")
    res = runner.invoke(root_app, ["ai", "job", "submit", "x"])
    assert res.exit_code == 1
    assert "cap" in res.output.lower()
    assert signed["called"] is False


def test_provider_register_requires_address(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIMICA_CONFIG", str(tmp_path / "empty.toml"))
    res = runner.invoke(root_app, ["ai", "provider", "register"])
    assert res.exit_code == 1
    assert "address" in res.output.lower()


def test_benchmark_mocked_adapter(monkeypatch):
    class _A:
        def generate(self, prompt, **k):
            return "word " * 20

    class _MP:
        provider = "ollama"
        model = "qwen2.5:7b"

    monkeypatch.setattr(ai_mod, "_resolve_adapter", lambda *a, **k: (_A(), _MP(), "ollama"))
    res = runner.invoke(root_app, ["ai", "benchmark", "-n", "2", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data["runs"] == 2 and data["model"] == "qwen2.5:7b"
    assert "approx_tokens_per_s" in data


# --------------------------------------------------------------------------- #
# Embeddings + RAG — uses the offline `hashing` embedding provider (no network).
# --------------------------------------------------------------------------- #
def test_embed_json_offline():
    res = runner.invoke(root_app, ["ai", "embed", "hello", "--provider", "hashing", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data["data"][0]["embedding"]  # non-empty vector
    assert isinstance(data["data"][0]["embedding"][0], float)


def test_rag_index_query_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_HOME", str(tmp_path))
    doc = tmp_path / "doc.md"
    doc.write_text("Animica pays miners in ANM.\n\nThe stratum pool listens on port 3333.",
                   encoding="utf-8")
    idx = runner.invoke(root_app, ["ai", "rag", "index", str(doc),
                                   "--provider", "hashing", "--name", "t"])
    assert idx.exit_code == 0, idx.output
    q = runner.invoke(root_app, ["ai", "rag", "query", "which port?",
                                 "--name", "t", "--provider", "hashing", "--k", "2", "--json"])
    assert q.exit_code == 0, q.output
    data = json.loads(q.stdout)
    assert data["hits"] and "score" in data["hits"][0]
    lst = runner.invoke(root_app, ["ai", "rag", "list", "--json"])
    assert "t" in json.loads(lst.stdout)["indexes"]


def test_rag_query_missing_index(tmp_path, monkeypatch):
    monkeypatch.setenv("ANIMICA_HOME", str(tmp_path))
    res = runner.invoke(root_app, ["ai", "rag", "query", "x", "--name", "nope", "--provider", "hashing"])
    assert res.exit_code == 1
    assert "index" in res.output.lower()


# --------------------------------------------------------------------------- #
# balance (wallet UX, §9) + --no-color (usability, §8). RPC mocked.
# --------------------------------------------------------------------------- #
def test_balance_json_mocked(monkeypatch, tmp_path):
    monkeypatch.setenv("ANIMICA_CONFIG", str(tmp_path / "c.toml"))
    monkeypatch.setattr(market_mod, "call", lambda *a, **k: 1_800_000_000_000)
    res = runner.invoke(root_app, ["ai", "balance", "--address", "anim1demo", "--json"])
    assert res.exit_code == 0, res.output
    data = json.loads(res.stdout)
    assert data["base_units"] == 1_800_000_000_000
    assert data["anm"] == 1800.0
    assert data["address"] == "anim1demo"


def test_balance_requires_address(monkeypatch, tmp_path):
    # No --address, empty config, and resolve_address finds nothing → friendly error.
    monkeypatch.setenv("ANIMICA_CONFIG", str(tmp_path / "empty.toml"))
    monkeypatch.setattr("animica.unified.resolve_address", lambda *a, **k: ("", "none"))
    res = runner.invoke(root_app, ["ai", "balance"])
    assert res.exit_code == 1
    assert "wallet" in res.output.lower() or "address" in res.output.lower()


def test_no_color_flag_emits_no_ansi(monkeypatch):
    monkeypatch.setattr(market_mod, "call", lambda *a, **k: 42)
    res = runner.invoke(root_app, ["ai", "--no-color", "balance", "--address", "anim1x"])
    assert res.exit_code == 0, res.output
    assert "\x1b[" not in res.output  # no ANSI color escape sequences
