"""End-to-end test for the LayerRangeRunner pipeline.

Uses a synthetic Llama-shaped torch model so we don't need to pull
weights or load a real LLM in CI. The synthetic model has:

  - model.embed_tokens (Embedding)
  - model.layers     (ModuleList of TinyLayer)
  - model.norm       (LayerNorm)
  - lm_head          (Linear, output dim == vocab)

The runner introspects this exactly like it would on a real Llama /
Qwen / Mistral checkpoint — same attribute paths.
"""

from __future__ import annotations

import sys

import pytest


torch = pytest.importorskip("torch")
nn = torch.nn

# Make the runner module importable without installing the wheel.
sys.path.insert(0, "ai/flagship_agent/src")
from flagship_agent.layer_range_inference import (    # noqa: E402
    LayerRange,
    LayerRangeRunner,
    plan_layer_ranges,
)


class TinyDecoderLayer(nn.Module):
    """Single transformer-style decoder layer that accepts the
    HF-style ``attention_mask`` kwarg. The math is a tiny MLP — enough
    to exercise the layer-by-layer forward path."""

    def __init__(self, d: int) -> None:
        super().__init__()
        self.ln = nn.LayerNorm(d)
        self.fc1 = nn.Linear(d, d * 2)
        self.fc2 = nn.Linear(d * 2, d)

    def forward(self, x, attention_mask=None):    # noqa: D401, ARG002
        h = self.ln(x)
        h = self.fc2(torch.relu(self.fc1(h)))
        return (x + h,)    # tuple form, just like HF blocks


class TinyDecoder(nn.Module):
    def __init__(self, vocab: int, d: int, n_layers: int) -> None:
        super().__init__()
        self.embed_tokens = nn.Embedding(vocab, d)
        self.layers = nn.ModuleList([TinyDecoderLayer(d) for _ in range(n_layers)])
        self.norm = nn.LayerNorm(d)


class TinyLLM(nn.Module):
    def __init__(self, vocab: int = 32, d: int = 8, n_layers: int = 4) -> None:
        super().__init__()
        self.model = TinyDecoder(vocab, d, n_layers)
        self.lm_head = nn.Linear(d, vocab, bias=False)

    def get_input_embeddings(self):    # noqa: D401
        return self.model.embed_tokens


class TinyTokenizer:
    """Tiny char-level tokenizer matching the synthetic model's vocab."""

    def __init__(self, vocab: int = 32) -> None:
        self.vocab = vocab
        self.eos_token_id = 0

    def __call__(self, prompt: str, return_tensors: str = "pt"):    # noqa: ARG002
        ids = [(ord(c) % self.vocab) or 1 for c in (prompt or "x")[:8]]
        if not ids:
            ids = [1]
        return {
            "input_ids": torch.tensor([ids], dtype=torch.long),
            "attention_mask": torch.ones(1, len(ids), dtype=torch.long),
        }

    def decode(self, ids, skip_special_tokens=True):    # noqa: ARG002
        return "".join(chr((int(t) % 95) + 32) for t in ids if int(t) >= 0)


def _make_runner(n_layers: int = 4) -> LayerRangeRunner:
    torch.manual_seed(0)
    model = TinyLLM(n_layers=n_layers)
    tok = TinyTokenizer()
    return LayerRangeRunner(model, tok)


def test_runner_locates_layers_and_total():
    runner = _make_runner(n_layers=6)
    assert runner.total_layers == 6


def test_two_stage_pipeline_matches_monolithic_prefill():
    """Stage 0 + stage 1 (with N=2) must reach the same hidden states a
    single-shot full forward pass would. Floating-point equality up to
    a tiny tolerance — the only difference is the serialize/deserialize
    round-trip in between."""
    runner = _make_runner(n_layers=4)
    ranges = plan_layer_ranges(runner.total_layers, 2)

    # Pipeline path
    p0, _ = runner.embed_and_run_prefix("hello", ranges[0])
    p1, meta = runner.run_layers(p0, ranges[1])

    # Monolithic full forward path (re-run the same prompt through all
    # layers in one go).
    p_full, _ = runner.embed_and_run_prefix("hello", LayerRange(0, runner.total_layers))

    # Compare the hidden tensors.
    from flagship_agent.layer_range_inference import _safetensors_loads
    pipe_tensors = _safetensors_loads(p1)
    full_tensors = _safetensors_loads(p_full)
    assert pipe_tensors["hidden"].shape == full_tensors["hidden"].shape
    torch.testing.assert_close(
        pipe_tensors["hidden"], full_tensors["hidden"],
        rtol=1e-5, atol=1e-6,
    )


def test_final_stage_generates_text_deterministically_with_temp_zero():
    runner = _make_runner(n_layers=4)
    ranges = plan_layer_ranges(runner.total_layers, 2)

    p0, _ = runner.embed_and_run_prefix("hi", ranges[0])
    text_a, _ = runner.run_suffix_and_generate(
        p0, ranges[1], max_new_tokens=4, temperature=0.0,
    )
    text_b, _ = runner.run_suffix_and_generate(
        p0, ranges[1], max_new_tokens=4, temperature=0.0,
    )
    # Greedy decode → identical runs produce identical text.
    assert text_a == text_b
    assert len(text_a) > 0


def test_streaming_decode_cache_and_step_apis_compose():
    """Prefill caches K/V, decode_step_layers + decode_step_with_head
    reuse it across a sequence of single-token rounds, and the run is
    deterministic at temperature=0."""
    runner = _make_runner(n_layers=4)
    ranges = plan_layer_ranges(runner.total_layers, 2)
    p0, _ = runner.prefill_with_cache(
        "hi", ranges[0], job_id="JOB", is_first_stage=True,
    )
    p1, _ = runner.prefill_with_cache(
        p0, ranges[1], job_id="JOB", is_first_stage=False,
    )
    assert len(p0) > 0 and len(p1) > 0

    # Streaming-decode 3 tokens.
    seq_ids = []
    seq_texts = []
    next_token = 5
    for _ in range(3):
        d0, _ = runner.decode_step_layers(
            next_token, ranges[0], job_id="JOB", is_first_stage=True,
        )
        token_id, token_text, _ = runner.decode_step_with_head(
            d0, ranges[1], job_id="JOB", temperature=0.0,
        )
        seq_ids.append(token_id)
        seq_texts.append(token_text)
        next_token = token_id

    # Determinism: same starting token + cache state -> same sequence.
    runner2 = _make_runner(n_layers=4)
    runner2.prefill_with_cache(
        "hi", ranges[0], job_id="JOB2", is_first_stage=True,
    )
    runner2.prefill_with_cache(
        p0, ranges[1], job_id="JOB2", is_first_stage=False,
    )
    seq_ids2 = []
    next_token = 5
    for _ in range(3):
        d0, _ = runner2.decode_step_layers(
            next_token, ranges[0], job_id="JOB2", is_first_stage=True,
        )
        token_id, _txt, _ = runner2.decode_step_with_head(
            d0, ranges[1], job_id="JOB2", temperature=0.0,
        )
        seq_ids2.append(token_id)
        next_token = token_id
    assert seq_ids == seq_ids2, (seq_ids, seq_ids2)

    # release_cache drops state
    runner.release_cache("JOB")
    assert "JOB" not in runner._kv_caches


def test_three_stage_pipeline_covers_all_layers():
    runner = _make_runner(n_layers=6)
    ranges = plan_layer_ranges(runner.total_layers, 3)

    p0, _ = runner.embed_and_run_prefix("yo", ranges[0])
    p1, _ = runner.run_layers(p0, ranges[1])
    text, _ = runner.run_suffix_and_generate(
        p1, ranges[2], max_new_tokens=2, temperature=0.0,
    )
    assert text  # something was decoded

    # Sanity: ranges cover [0, total) exactly with no overlap
    covered = set()
    for r in ranges:
        for i in range(r.start, r.end):
            assert i not in covered, f"overlap at layer {i}"
            covered.add(i)
    assert covered == set(range(runner.total_layers))
