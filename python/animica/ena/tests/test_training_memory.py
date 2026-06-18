"""Memory-aware trainer auto-configuration tests (pure decision logic, no GPU).

The heavy training path needs torch/transformers + a model, so these exercise the
policy functions that decide HOW to train given the local memory budget — the part
that determines whether a low-memory worker OOMs or succeeds.
"""

from __future__ import annotations

from animica.ena import training as T


def _hp(**kw):
    base = {"method": "lora", "batch_size": 4, "grad_accum": 1, "max_seq_len": 1024,
            "lora": {"enabled": True}, "quant": None}
    base.update(kw)
    return base


def test_low_cuda_enables_qlora_gc_small_batch(monkeypatch):
    monkeypatch.setenv(T.AUTOMEM_ENV, "1")
    monkeypatch.setattr(T, "_bitsandbytes_available", lambda: True)
    prof = {"device": "cuda", "dtype": "bfloat16", "free_gb": 6.0,
            "total_gb": 8.0, "bf16": True}
    hp = T._auto_memory_hparams(_hp(), prof)
    assert hp["torch_dtype"] == "bfloat16"
    assert hp["quant"] == "4bit"                 # QLoRA kicks in on tight CUDA
    assert hp["gradient_checkpointing"] is True
    assert hp["batch_size"] == 1                 # free<8 -> batch 1
    assert hp["grad_accum"] >= 4                 # effective batch preserved
    assert hp["max_seq_len"] == 512              # seq len capped on very low mem


def test_mid_cuda_caps_batch_keeps_quant_off_if_no_bnb(monkeypatch):
    monkeypatch.setenv(T.AUTOMEM_ENV, "1")
    monkeypatch.setattr(T, "_bitsandbytes_available", lambda: False)
    prof = {"device": "cuda", "dtype": "float16", "free_gb": 12.0,
            "total_gb": 16.0, "bf16": False}
    hp = T._auto_memory_hparams(_hp(), prof)
    assert hp.get("quant") in (None, "")         # no bitsandbytes -> no 4-bit
    assert hp["batch_size"] == 2                 # 8<=free<16 -> batch 2
    assert hp["gradient_checkpointing"] is True
    assert hp["torch_dtype"] == "float16"


def test_mid_high_cuda_forces_gc_but_not_quant(monkeypatch):
    """24-32GB band: gradient checkpointing on (cheap OOM cut) but no 4-bit and
    full batch — a full-precision LoRA on a 24-32GB card still gets the cut."""
    monkeypatch.setenv(T.AUTOMEM_ENV, "1")
    monkeypatch.setattr(T, "_bitsandbytes_available", lambda: True)
    prof = {"device": "cuda", "dtype": "bfloat16", "free_gb": 28.0,
            "total_gb": 32.0, "bf16": True}
    hp = T._auto_memory_hparams(_hp(), prof)
    assert hp.get("quant") in (None, "")         # >=24GB -> no 4-bit
    assert hp["gradient_checkpointing"] is True  # <32GB -> GC forced
    assert hp["batch_size"] == 4                 # >=24GB -> batch unchanged


def test_high_cuda_keeps_full_batch_no_quant_no_gc(monkeypatch):
    monkeypatch.setenv(T.AUTOMEM_ENV, "1")
    monkeypatch.setattr(T, "_bitsandbytes_available", lambda: True)
    prof = {"device": "cuda", "dtype": "bfloat16", "free_gb": 40.0,
            "total_gb": 48.0, "bf16": True}
    hp = T._auto_memory_hparams(_hp(), prof)
    assert hp.get("quant") in (None, "")
    assert hp["batch_size"] == 4                 # ample memory -> unchanged
    assert "gradient_checkpointing" not in hp    # not forced when memory is ample
    assert hp["max_seq_len"] == 1024


def test_cpu_no_quant_fp32(monkeypatch):
    monkeypatch.setenv(T.AUTOMEM_ENV, "1")
    monkeypatch.setattr(T, "_bitsandbytes_available", lambda: True)
    prof = {"device": "cpu", "dtype": "float32", "free_gb": 10.0,
            "total_gb": 16.0, "bf16": False}
    hp = T._auto_memory_hparams(_hp(), prof)
    assert hp.get("quant") in (None, "")         # bitsandbytes 4-bit is CUDA-only
    assert hp["torch_dtype"] == "float32"
    assert hp["batch_size"] == 2                 # free 10 -> cap 2


def test_disabled_passthrough(monkeypatch):
    monkeypatch.setenv(T.AUTOMEM_ENV, "0")
    prof = {"device": "cuda", "dtype": "bfloat16", "free_gb": 4.0,
            "total_gb": 8.0, "bf16": True}
    hp_in = _hp()
    assert T._auto_memory_hparams(hp_in, prof) == hp_in   # untouched


def test_never_raises_batch_above_request(monkeypatch):
    monkeypatch.setenv(T.AUTOMEM_ENV, "1")
    monkeypatch.setattr(T, "_bitsandbytes_available", lambda: True)
    prof = {"device": "cuda", "dtype": "bfloat16", "free_gb": 80.0,
            "total_gb": 80.0, "bf16": True}
    hp = T._auto_memory_hparams(_hp(batch_size=2), prof)
    assert hp["batch_size"] == 2                 # ample mem never raises batch


def test_detect_profile_shape():
    prof = T._detect_memory_profile()
    assert {"device", "dtype", "free_gb", "total_gb"}.issubset(prof)
    assert prof["device"] in ("cpu", "cuda", "mps")
    assert isinstance(prof["free_gb"], (int, float))
