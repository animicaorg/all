"""Image prompt-fidelity pipeline tests (CPU, no models — fake pipelines + fake judge).

  PYTHONPATH=/root/animica/python /root/animica/.venv/bin/python -m pytest animica/media/test_image_fidelity.py -q
"""

from __future__ import annotations

import io
import json
import types

import pytest
from PIL import Image

from animica.media import image_gen, image_fidelity, prompt_spec
from animica.media.base import MediaError


# ── prompt compiler: shared conformance vectors ──────────────────────────────

def _vector_cases():
    return [pytest.param(c, id=c["name"]) for c in prompt_spec.load_vectors()["cases"]]


@pytest.mark.parametrize("case", _vector_cases())
def test_prompt_compiler_vectors(case):
    out = prompt_spec.compile_image_prompt(case["input"], case.get("negative"))
    e = case["expect"]
    if "prompt_starts" in e:
        assert out.prompt.startswith(e["prompt_starts"]), out.prompt
    for s in e.get("prompt_contains", []):
        assert s in out.prompt, out.prompt
    if "negative" in e:
        assert out.negative == e["negative"]
    for s in e.get("negative_contains", []):
        assert s in out.negative, out.negative
    for s in e.get("negative_not_contains", []):
        assert s not in out.negative, out.negative
    for key in ("counts", "grid", "colors", "layout", "text", "negated"):
        if key in e:
            assert getattr(out.spec, key) == e[key], (key, getattr(out.spec, key))
    if "truncation_risk" in e:
        assert out.truncation_risk == e["truncation_risk"], out.est_tokens
    again = prompt_spec.compile_image_prompt(out.prompt, out.negative)
    assert again.prompt == out.prompt, "compiler must be idempotent"


def test_constraint_views_split_specific_details():
    c = prompt_spec.compile_image_prompt(
        "Make an image of a crib style trailer holding logs with nine logs 3x3 and the top center log highlighted red"
    )
    views = c.constraint_views()
    assert "the top center log highlighted red" in views
    assert "nine logs 3x3" in views


def test_quality_negative_keeps_requested_text():
    spec = prompt_spec.ImagePromptSpec(text=["OPEN 24h"])
    q = prompt_spec.quality_negative("", spec)
    assert "watermark" not in q and "blurry" in q
    q2 = prompt_spec.quality_negative("cars", prompt_spec.ImagePromptSpec())
    assert q2.startswith("cars, ") and "watermark" in q2


# ── model regimes / buckets / chunking ──────────────────────────────────────

def test_model_profiles():
    p = image_gen.model_profile("stabilityai/sd-turbo")
    assert p["turbo"] and p["steps"] == 4 and p["guidance"] == 0.0 and not p["cfg"] and p["native"] == 512
    p = image_gen.model_profile("stabilityai/sdxl-turbo")
    assert p["turbo"] and p["native"] == 512 and p["encoder"] == "clip2"
    p = image_gen.model_profile("stabilityai/stable-diffusion-xl-base-1.0")
    assert not p["turbo"] and p["cfg"] and p["native"] == 1024 and p["steps"] == 30
    p = image_gen.model_profile("black-forest-labs/FLUX.1-schnell")
    assert p["encoder"] == "t5" and p["steps"] == 4 and p["native"] == 1024 and p["align"] == 16
    p = image_gen.model_profile("runwayml/stable-diffusion-v1-5")
    assert p["cfg"] and p["steps"] == 28 and p["native"] == 512
    p = image_gen.model_profile("stabilityai/stable-diffusion-2-1")
    assert p["native"] == 768


def test_native_bucket():
    # in-regime, aligned: untouched
    assert image_gen.native_bucket(512, 512, 512) == (512, 512)
    assert image_gen.native_bucket(512, 576, 512) == (512, 576)
    # 768² on a 512 model → rendered at 512² then upscaled (doubled subjects avoided)
    assert image_gen.native_bucket(768, 768, 512) == (512, 512)
    # aspect preserved at native area
    w, h = image_gen.native_bucket(1024, 576, 512)
    assert w % 64 == 0 and h % 64 == 0 and abs((w / h) - (1024 / 576)) < 0.25 and 0.8 < (w * h) / (512 * 512) < 1.25
    # tiny request renders at a sane size, not 64×64
    assert image_gen.native_bucket(64, 64, 512) == (512, 512)
    # SDXL 1024 regime
    assert image_gen.native_bucket(1024, 1024, 1024) == (1024, 1024)
    assert image_gen.native_bucket(512, 512, 1024) == (1024, 1024)


def test_chunk_token_ids():
    ids = list(range(1000, 1000 + 160))
    rows = image_gen.chunk_token_ids(ids, bos=49406, eos=49407, pad=0, n_chunks=3)
    assert len(rows) == 3 and all(len(r) == 77 for r in rows)
    assert rows[0][0] == 49406 and rows[0][76] == 49407 and rows[0][1:76] == ids[:75]
    assert rows[2][1:11] == ids[150:160] and rows[2][11] == 49407 and rows[2][12] == 0
    assert image_gen._chunks_needed(75) == 1 and image_gen._chunks_needed(76) == 2 and image_gen._chunks_needed(0) == 1


def test_default_candidates(monkeypatch):
    monkeypatch.delenv("ANIMICA_IMAGE_CANDIDATES", raising=False)
    assert image_gen.default_candidates("fast", "cuda", True) == 1
    assert image_gen.default_candidates("balanced", "cuda", True) == 4
    assert image_gen.default_candidates("high", "cuda", True) == 8
    assert image_gen.default_candidates("balanced", "cpu", True) == 2
    assert image_gen.default_candidates("balanced", "cuda", False) == 2
    monkeypatch.setenv("ANIMICA_IMAGE_CANDIDATES", "3")
    assert image_gen.default_candidates("high", "cuda", True) == 3


# ── end-to-end with a fake diffusers pipeline ───────────────────────────────

class _FakeTokenizer:
    bos_token_id, eos_token_id, pad_token_id = 49406, 49407, 49407

    def __call__(self, text, truncation=False, add_special_tokens=False, **_):
        return types.SimpleNamespace(input_ids=[100 + i for i in range(len(text.split()))])


class _FakeEncoder:
    device = "cpu"

    def __call__(self, ids, output_hidden_states=False):
        import torch
        n, w = ids.shape
        return (torch.zeros(n, w, 8),)


class FakePipe:
    """Records every call; paints each candidate a distinct solid color so a judge can tell them apart."""

    def __init__(self, record: list):
        self.record = record
        self.scheduler = types.SimpleNamespace(config={})
        self.tokenizer = _FakeTokenizer()
        self.text_encoder = _FakeEncoder()
        self._execution_device = "cpu"

    def __call__(self, **kw):
        self.record.append(kw)
        seed = kw["generator"].initial_seed()
        color = (seed % 256, (seed * 7) % 256, (seed * 13) % 256)
        img = Image.new("RGB", (kw["width"], kw["height"]), color)
        return types.SimpleNamespace(images=[img])


def _run(monkeypatch, *, prompt="a blue triangle on white", judge=None, **kw):
    calls: list = []
    pipe = FakePipe(calls)
    monkeypatch.setattr(image_gen, "_load_pipeline", lambda model_id, adapter=None, strategy="cuda": pipe)
    monkeypatch.setattr(image_gen, "_refine_to_size", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no refine in tests")))
    import torch
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    if judge is not None:
        monkeypatch.setattr(image_fidelity, "score_candidates", judge)
    out = image_gen.generate_image(prompt, **kw)
    return out, calls


def test_generate_best_of_n_picks_judge_winner(monkeypatch):
    monkeypatch.delenv("ANIMICA_IMAGE_CANDIDATES", raising=False)
    monkeypatch.setenv("ANIMICA_IMAGE_RERANK", "1")

    def judge(images, prompt, views=(), negated=(), **_):
        scores = [0.1, 0.9, 0.3]
        return image_fidelity.FidelityReport(scores=scores, best=1, scorer="fake", views=list(views), negated=list(negated))

    out, calls = _run(monkeypatch, judge=judge, seed=1000, candidates=3, width=512, height=512)
    assert len(calls) == 3
    assert [c["generator"].initial_seed() for c in calls] == [1000, 1001, 1002]
    assert out["seed"] == 1001 and out["candidates"] == 3 and out["rerank"] == "clip" and out["fidelity"] == 0.9
    # turbo regime: 4 steps, guidance 0, negatives NOT passed to a guidance-0 model
    assert calls[0]["num_inference_steps"] == 4 and calls[0]["guidance_scale"] == 0.0
    assert "negative_prompt" not in calls[0]
    # the winner's color is candidate #1's
    img = Image.open(io.BytesIO(out["bytes"]))
    assert img.size == (512, 512) and img.getpixel((0, 0)) == (1001 % 256, (1001 * 7) % 256, (1001 * 13) % 256)
    # PNG carries the recipe (A1111-style + JSON)
    assert "Seed: 1001" in img.text["parameters"] and "stabilityai/sd-turbo" in img.text["parameters"]
    rec = json.loads(img.text["animica"])
    assert rec["seed"] == 1001 and rec["candidate_seeds"] == [1000, 1001, 1002] and rec["prompt"] == "a blue triangle on white"


def test_generate_exact_size_from_native_bucket(monkeypatch):
    monkeypatch.setenv("ANIMICA_IMAGE_RERANK", "0")
    out, calls = _run(monkeypatch, seed=5, candidates=1, width=768, height=768)
    # rendered in the 512 regime, delivered at the exact requested size
    assert calls[0]["width"] == 512 and calls[0]["height"] == 512
    img = Image.open(io.BytesIO(out["bytes"]))
    assert img.size == (768, 768) and out["width"] == 768 and out["render_size"] == "512x512"
    assert out["refined"] is False and out["rerank"] == "single"


def test_judge_failure_falls_back_to_first_candidate(monkeypatch):
    monkeypatch.setenv("ANIMICA_IMAGE_RERANK", "1")

    def judge(*a, **k):
        raise image_fidelity.FidelityUnavailable("no clip here")

    out, calls = _run(monkeypatch, judge=judge, seed=7, candidates=2)
    assert len(calls) == 2 and out["seed"] == 7 and out["rerank"].startswith("unavailable")


def test_compiled_prompt_reaches_the_model(monkeypatch):
    monkeypatch.setenv("ANIMICA_IMAGE_RERANK", "0")
    out, calls = _run(monkeypatch, prompt="Make an image of a street with no cars", seed=1, candidates=1)
    assert calls[0]["prompt"] == "a street"
    assert out["prompt"] == "a street" and "cars" in out["negative_prompt"]
    assert any("negative" in n for n in out["notes"])


def test_cfg_model_gets_negatives_and_dpm_steps(monkeypatch):
    monkeypatch.setenv("ANIMICA_IMAGE_RERANK", "0")
    out, calls = _run(monkeypatch, model="runwayml/stable-diffusion-v1-5", seed=1, candidates=1)
    assert calls[0]["num_inference_steps"] == 28 and calls[0]["guidance_scale"] == 7.0
    assert "blurry" in calls[0]["negative_prompt"]
    # explicit steps are clamped into the family's sane range
    out, calls = _run(monkeypatch, model="runwayml/stable-diffusion-v1-5", seed=1, candidates=1, steps=3)
    assert calls[0]["num_inference_steps"] == 10
    out, calls = _run(monkeypatch, seed=1, candidates=1, steps=30)  # turbo clamp
    assert calls[0]["num_inference_steps"] == 8


def test_long_prompt_is_chunked_not_truncated(monkeypatch):
    monkeypatch.setenv("ANIMICA_IMAGE_RERANK", "0")
    long_prompt = " ".join(f"detail{i}" for i in range(120))  # 120 fake tokens → 2 chunks
    out, calls = _run(monkeypatch, prompt=long_prompt, seed=1, candidates=1)
    assert "prompt_embeds" in calls[0] and "prompt" not in calls[0]
    assert tuple(calls[0]["prompt_embeds"].shape) == (1, 2 * 77, 8)
    assert out["long_prompt"] == "chunked"


def test_empty_prompt_rejected():
    with pytest.raises(MediaError):
        image_gen.generate_image("   ")


def test_time_budget_stops_extra_candidates(monkeypatch):
    monkeypatch.setenv("ANIMICA_IMAGE_RERANK", "0")
    monkeypatch.setenv("ANIMICA_IMAGE_TIME_BUDGET_S", "10")
    # pretend each candidate takes 8s: after the first, a second would overrun 10s
    t = [0.0]
    monkeypatch.setattr(image_gen.time, "monotonic", lambda: t.__setitem__(0, t[0] + 8.0) or t[0])
    out, calls = _run(monkeypatch, seed=1, candidates=4)
    assert len(calls) == 1 and out["candidates"] == 1
