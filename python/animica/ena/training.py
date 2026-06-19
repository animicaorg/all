"""
animica.ena.training
====================

Training-run orchestration: ``prepare`` (manifest generation) → ``run``
(launch) → ``eval`` (score against a model provider) → ``status`` / ``list`` /
``export``.

ENA owns the manifest, bookkeeping, and artifact lineage even when the compute
happens elsewhere. Two backends:

* ``command`` — launch an external trainer with ``{manifest}`` / ``{output_dir}``
  placeholders. GPU/accelerator compute stays external.
* ``python_transformers`` — optional in-process fine-tune when ``transformers``
  and ``datasets`` are installed (CPU small-model and, with the ``gpu`` extra,
  LoRA/QLoRA SFT/DPO — deepened in Phase B).
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger("animica.ena.training")

# Auto memory-safety: adapt the run to the LOCAL machine's memory so a worker
# trains a shard instead of OOMing. Set ANIMICA_ENA_AUTOMEM=0 to disable.
AUTOMEM_ENV = "ANIMICA_ENA_AUTOMEM"

from . import datasets as ds
from .errors import TrainingError
from .models import SplitRecord, TrainingManifest, TrainingRun, new_uuid, now_ts

BACKENDS = ("command", "python_transformers")


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

METHODS = ("sft", "lora", "qlora", "dpo", "distill")


def prepare(cfg, *, dataset: str, out: str, base_model: str,
            backend: str = "command", auto_split: bool = True,
            launcher_command: Optional[str] = None,
            hyperparameters: Optional[dict[str, Any]] = None,
            output_dir: Optional[str] = None,
            method: str = "sft") -> dict[str, Any]:
    if backend not in BACKENDS:
        raise TrainingError(f"unknown backend: {backend}",
                           hint=f"one of: {', '.join(BACKENDS)}")
    if method not in METHODS:
        raise TrainingError(f"unknown method: {method}",
                           hint=f"one of: {', '.join(METHODS)}")
    src = Path(dataset)
    if not src.is_file():
        raise TrainingError(f"dataset not found: {src}")
    run_name = Path(out).stem.replace("_manifest", "") or ("run-" + new_uuid()[:8])
    out_dir = output_dir or str(Path(out).resolve().parent / f"{run_name}-output")

    train_rec = eval_rec = test_rec = None
    if auto_split:
        split_dir = Path(out).resolve().parent / f"{run_name}-splits"
        splits = ds.split(dataset, split_dir)
        train_rec = SplitRecord.from_dict(splits["train"])
        eval_rec = SplitRecord.from_dict(splits["eval"])
        test_rec = SplitRecord.from_dict(splits["test"])
    else:
        train_rec = SplitRecord(split="train", path=str(src.resolve()),
                                row_count=ds.row_count(src), sha256=ds.sha256_file(src))

    launcher: dict[str, Any] = {}
    if backend == "command":
        if not launcher_command:
            raise TrainingError("command backend requires --launcher-command",
                               hint="e.g. \"python trainer.py --manifest {manifest} "
                                    "--output-dir {output_dir}\"")
        launcher = {"command": launcher_command}

    hparams = hyperparameters or _default_hparams(method)
    hparams.setdefault("method", method)
    manifest = TrainingManifest(
        run_name=run_name, backend=backend, base_model=base_model, output_dir=out_dir,
        train=train_rec, eval=eval_rec, test=test_rec,
        hyperparameters=hparams,
        launcher=launcher,
        metadata={"created_at": now_ts(), "method": method,
                  "source_dataset": str(src.resolve()),
                  "source_sha256": ds.sha256_file(src)},
        train_dataset=train_rec.path if train_rec else None,
        train_sha256=train_rec.sha256 if train_rec else None,
    )
    md = manifest.to_dict()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_text(json.dumps(md, indent=2), encoding="utf-8")
    return md


def _default_hparams(method: str = "sft") -> dict[str, Any]:
    lora_on = method in ("lora", "qlora")
    return {
        "method": method,
        "epochs": 1,
        "learning_rate": 2e-4 if lora_on else 2e-5,
        "batch_size": 4,
        "grad_accum": 1,
        "max_seq_len": 1024,
        "lora": {"enabled": lora_on, "r": 16, "alpha": 32, "dropout": 0.05,
                 "target_modules": None},
        "quant": "4bit" if method == "qlora" else None,
        "dpo_beta": 0.1,
    }


# ---------------------------------------------------------------------------
# memory-aware auto-configuration (runs on the worker, per-machine)
# ---------------------------------------------------------------------------

def _system_ram_gb(*, available: bool = False) -> float:
    """Total (or currently-available) system RAM in GiB. Prefers psutil for the
    'available' figure so a busy CPU/MPS host isn't given an over-eager batch
    size; falls back to POSIX sysconf (×0.7 as a rough free-memory margin)."""
    try:
        import psutil  # type: ignore
        vm = psutil.virtual_memory()
        return (vm.available if available else vm.total) / 2 ** 30
    except Exception:  # noqa: BLE001 - psutil optional
        try:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
            return total * 0.7 if available else total
        except Exception:  # noqa: BLE001 - non-POSIX
            return 0.0


def _detect_memory_profile() -> dict[str, Any]:
    """Best-effort device + free-memory + safe compute-dtype probe. Falls back to
    CPU/float32. ``free_gb`` is real free VRAM on CUDA, else total system RAM
    (an upper bound) — callers treat it as a budget hint, not a guarantee."""
    prof = {"device": "cpu", "dtype": "float32", "free_gb": 0.0,
            "total_gb": 0.0, "bf16": False}
    try:
        import torch  # type: ignore
    except Exception:  # noqa: BLE001 - torch optional
        return prof
    try:
        if torch.cuda.is_available():
            free, total = torch.cuda.mem_get_info()
            bf16 = bool(getattr(torch.cuda, "is_bf16_supported", lambda: False)())
            prof.update(device="cuda", free_gb=free / 2 ** 30,
                        total_gb=total / 2 ** 30, bf16=bf16,
                        dtype="bfloat16" if bf16 else "float16")
        elif getattr(getattr(torch, "backends", None), "mps", None) is not None \
                and torch.backends.mps.is_available():
            # bitsandbytes 4-bit is CUDA-only; MPS runs unquantized. Use bfloat16,
            # NOT float16: fp16 on Apple Metal overflows (narrow exponent range),
            # which spikes/diverges the loss (observed train_loss ~33). bf16 has the
            # same range as fp32 at half the memory, so it is stable on MPS. free_gb
            # is *available* RAM (not total) so a loaded host doesn't OOM.
            prof.update(device="mps", free_gb=_system_ram_gb(available=True),
                        total_gb=_system_ram_gb(), dtype="bfloat16")
        else:
            prof.update(device="cpu", free_gb=_system_ram_gb(available=True),
                        total_gb=_system_ram_gb(), dtype="float32")
    except Exception:  # noqa: BLE001 - probe is best-effort
        pass
    return prof


def _bitsandbytes_available() -> bool:
    try:
        import bitsandbytes  # type: ignore  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _auto_memory_hparams(hp: dict[str, Any],
                         profile: dict[str, Any]) -> dict[str, Any]:
    """Adapt hyperparameters to the local memory budget so a low-memory worker
    can train a shard. CONSERVATIVE: only ever lowers peak memory vs the pool's
    request (never raises batch_size above what was asked); preserves the
    effective batch via gradient accumulation. Disabled with ANIMICA_ENA_AUTOMEM=0.
    """
    hp = dict(hp)
    if str(os.environ.get(AUTOMEM_ENV, "1")).lower() in ("0", "false", "no", "off"):
        return hp
    dev = profile.get("device", "cpu")
    free = float(profile.get("free_gb") or 0.0)

    # base-model compute dtype (half precision halves the weight footprint)
    hp.setdefault("torch_dtype", profile.get("dtype", "float32"))

    lora_on = bool((hp.get("lora") or {}).get("enabled"))
    # QLoRA 4-bit on memory-constrained CUDA (bitsandbytes is CUDA-only, needs LoRA)
    if (dev == "cuda" and lora_on and hp.get("quant") in (None, "")
            and free and free < 24 and _bitsandbytes_available()):
        hp["quant"] = "4bit"

    # gradient checkpointing trades compute for a large activation-memory cut;
    # apply it generously (< 32GB) since it's the cheapest big OOM mitigation.
    if free and free < 32:
        hp.setdefault("gradient_checkpointing", True)

    # batch size: cap to the memory budget, never above the requested batch;
    # keep the effective batch with gradient accumulation.
    req_bs = max(1, int(hp.get("batch_size", 4)))
    if free:
        cap = 1 if free < 8 else 2 if free < 16 else 4 if free < 24 else req_bs
        bs = max(1, min(req_bs, cap))
        if bs != req_bs:
            hp["batch_size"] = bs
            hp["grad_accum"] = max(int(hp.get("grad_accum", 1)),
                                   -(-req_bs // bs))  # ceil(req/bs)

    # cap sequence length on very low memory (activations scale with seq len)
    if free and free < 8:
        hp["max_seq_len"] = min(int(hp.get("max_seq_len", 1024)), 512)
    return hp


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def run(cfg, store, *, manifest_path: str,
        backend: Optional[str] = None) -> dict[str, Any]:
    mp = Path(manifest_path)
    if not mp.is_file():
        raise TrainingError(f"manifest not found: {mp}")
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    backend = backend or manifest.get("backend", "command")
    run_id = "run-" + new_uuid()[:16]
    out_dir = manifest.get("output_dir") or str(mp.parent / f"{run_id}-output")
    Path(out_dir).mkdir(parents=True, exist_ok=True)

    rec = TrainingRun(
        run_id=run_id, status="running", backend=backend,
        manifest_path=str(mp.resolve()), base_model=manifest.get("base_model", ""),
        output_dir=out_dir, metadata={"run_name": manifest.get("run_name")},
    ).to_dict()
    store.upsert_run(rec)

    try:
        if backend == "command":
            rec = _run_command(rec, manifest, out_dir)
        elif backend == "python_transformers":
            rec = _run_python_transformers(rec, manifest, out_dir)
        else:
            raise TrainingError(f"unknown backend: {backend}")
        rec["status"] = "completed"
    except TrainingError as exc:
        rec["status"] = "failed"
        rec["error"] = exc.message
    except Exception as exc:  # noqa: BLE001
        rec["status"] = "failed"
        rec["error"] = str(exc)
    rec["checkpoint_paths"] = _collect_checkpoints(out_dir)
    rec["updated_at"] = now_ts()
    store.upsert_run(rec)
    return rec


def _run_command(rec: dict[str, Any], manifest: dict[str, Any],
                 out_dir: str) -> dict[str, Any]:
    template = (manifest.get("launcher") or {}).get("command")
    if not template:
        raise TrainingError("manifest has no launcher.command for command backend")
    cmd = template.replace("{manifest}", rec["manifest_path"]).replace("{output_dir}", out_dir)
    rec["command"] = cmd
    try:
        proc = subprocess.run(shlex.split(cmd), capture_output=True, text=True,
                              timeout=int(manifest.get("metadata", {}).get("timeout_sec", 86400)))
    except FileNotFoundError as exc:
        raise TrainingError(f"launcher not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise TrainingError(f"training command timed out: {exc}") from exc
    log_path = Path(out_dir) / "trainer.log"
    log_path.write_text((proc.stdout or "") + "\n--- stderr ---\n" + (proc.stderr or ""),
                        encoding="utf-8")
    rec["metrics"] = {"returncode": proc.returncode}
    rec["artifact_ids"] = [str(log_path)]
    if proc.returncode != 0:
        raise TrainingError(
            f"trainer exited {proc.returncode}",
            hint=f"see {log_path}; stderr: {(proc.stderr or '')[:200]}")
    return rec


def _require_transformers():
    try:
        import datasets as hf_datasets  # type: ignore
        import transformers  # type: ignore
    except Exception as exc:  # pragma: no cover - optional heavy deps
        raise TrainingError(
            "python_transformers backend needs transformers + datasets (+ torch)",
            hint="pip install 'animica[gpu]'  (adds torch/transformers/datasets); "
                 "for LoRA/QLoRA add peft/bitsandbytes, for DPO add trl") from exc
    return transformers, hf_datasets


def _run_python_transformers(rec: dict[str, Any], manifest: dict[str, Any],
                             out_dir: str) -> dict[str, Any]:
    hp = manifest.get("hyperparameters") or {}
    method = hp.get("method") or manifest.get("metadata", {}).get("method", "sft")
    if method == "dpo":
        return _run_dpo(rec, manifest, out_dir, hp)
    return _run_sft(rec, manifest, out_dir, hp, method)


def _load_tokenizer_and_model(base_model: str, hp: dict[str, Any],
                              init_adapter: Optional[str] = None):
    """Load tokenizer + causal LM, applying QLoRA 4-bit quant / LoRA adapters
    when requested. Returns (tokenizer, model, peft_enabled).

    When ``init_adapter`` points at an existing adapter dir, the LoRA is
    WARM-STARTED from it (continue training the prior round's merged adapter)
    instead of initialising a fresh adapter — this is what makes a pool's rounds
    compound rather than each restart from the pristine base weights."""
    transformers, _ = _require_transformers()
    AutoTokenizer = transformers.AutoTokenizer
    AutoModelForCausalLM = transformers.AutoModelForCausalLM

    import torch  # type: ignore  # transformers pulled torch in already

    tok = AutoTokenizer.from_pretrained(base_model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    gc_on = bool(hp.get("gradient_checkpointing"))
    torch_dtype = getattr(torch, str(hp.get("torch_dtype") or "float32"), None)

    model_kwargs: dict[str, Any] = {"low_cpu_mem_usage": True}
    quant = hp.get("quant")
    if quant in ("4bit", "8bit"):
        try:
            import bitsandbytes  # type: ignore  # noqa: F401
            from transformers import BitsAndBytesConfig  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise TrainingError("QLoRA quant needs bitsandbytes",
                               hint="pip install bitsandbytes (CUDA required)") from exc
        # NF4 + double quant + half-precision compute = the standard QLoRA recipe
        # (lowest memory). compute dtype must be a float dtype, not the 4-bit store.
        compute_dtype = torch_dtype if (torch_dtype and torch_dtype.is_floating_point) \
            else torch.float16
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=(quant == "4bit"), load_in_8bit=(quant == "8bit"),
            bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
            bnb_4bit_compute_dtype=compute_dtype)
    elif torch_dtype is not None and torch_dtype is not torch.float32:
        # half-precision weights (skip for fp32/CPU where it would be slower/unsafe)
        model_kwargs["torch_dtype"] = torch_dtype
    model = AutoModelForCausalLM.from_pretrained(base_model, **model_kwargs)
    if gc_on:
        model.config.use_cache = False  # incompatible with gradient checkpointing

    lora = hp.get("lora") or {}
    peft_enabled = False
    if lora.get("enabled"):
        try:
            from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise TrainingError("LoRA needs the peft package",
                               hint="pip install peft") from exc
        if quant in ("4bit", "8bit"):
            model = prepare_model_for_kbit_training(
                model, use_gradient_checkpointing=gc_on)
        elif gc_on:
            # PEFT + gradient checkpointing on a non-quantized model needs the
            # inputs to require grad, or backprop sees "no input requires grad".
            model.gradient_checkpointing_enable()
            model.enable_input_require_grads()
        adir = None
        if init_adapter:
            ap = Path(init_adapter)
            if ap.is_dir() and (ap / "adapter_config.json").is_file():
                adir = ap
        if adir is not None:
            # Warm-start: continue training the prior round's merged adapter so
            # the pool's rounds COMPOUND. r/alpha/target_modules come from the
            # adapter's own config (consistent with how the round-0 LoRA was made).
            from peft import PeftModel  # type: ignore
            model = PeftModel.from_pretrained(model, str(adir), is_trainable=True)
        else:
            peft_cfg = LoraConfig(
                r=int(lora.get("r", 16)), lora_alpha=int(lora.get("alpha", 32)),
                lora_dropout=float(lora.get("dropout", 0.05)), bias="none",
                task_type="CAUSAL_LM", target_modules=lora.get("target_modules"))
            model = get_peft_model(model, peft_cfg)
        peft_enabled = True
    return tok, model, peft_enabled


def encode_sft_row(tok, r: dict[str, Any], max_len: int, has_chat: bool):
    """Tokenize one SFT row into {input_ids, attention_mask, labels} with the
    PROMPT masked to -100 so loss is taken on the RESPONSE only (completion-style
    SFT). Uses the model's chat template for instruct bases, else "{prompt}\\n{resp}".
    Returns None for rows with no response or whose response truncated away."""
    prompt = str(r.get("prompt") or "")
    resp = str(r.get("response") or r.get("chosen") or r.get("text") or "")
    if not resp.strip():
        return None
    if has_chat and prompt:
        try:
            # Render to STRING then tokenize (cross-version safe — tokenize=True can
            # return an Encoding, not a list, on some transformers builds). The
            # template already embeds the special tokens, so add_special_tokens=False.
            p_text = tok.apply_chat_template(
                [{"role": "user", "content": prompt}],
                add_generation_prompt=True, tokenize=False)
            f_text = tok.apply_chat_template(
                [{"role": "user", "content": prompt},
                 {"role": "assistant", "content": resp}], tokenize=False)
            p_ids = tok(p_text, add_special_tokens=False)["input_ids"]
            f_ids = tok(f_text, add_special_tokens=False)["input_ids"]
        except Exception:  # noqa: BLE001 - bad/absent template → raw fallback
            pre = f"{prompt}\n"
            p_ids = tok(pre)["input_ids"]
            f_ids = tok(pre + resp + (tok.eos_token or ""))["input_ids"]
    else:
        pre = f"{prompt}\n" if prompt else ""
        p_ids = tok(pre)["input_ids"]
        f_ids = tok(pre + resp + (tok.eos_token or ""))["input_ids"]
    f_ids = f_ids[:max_len]
    n_p = min(len(p_ids), len(f_ids))
    labels = [-100] * n_p + f_ids[n_p:]
    if all(t == -100 for t in labels):  # response truncated away → no signal
        return None
    return {"input_ids": f_ids, "attention_mask": [1] * len(f_ids), "labels": labels}


def _run_sft(rec: dict[str, Any], manifest: dict[str, Any], out_dir: str,
             hp: dict[str, Any], method: str) -> dict[str, Any]:
    transformers, hf_datasets = _require_transformers()
    base_model = manifest.get("base_model")
    train_path = (manifest.get("train") or {}).get("path") or manifest.get("train_dataset")
    if not train_path:
        raise TrainingError("manifest has no train split path")

    profile = _detect_memory_profile()
    hp = _auto_memory_hparams(hp, profile)
    log.info("[train] sft on %s: device=%s free=%.1fGB dtype=%s quant=%s "
             "batch=%s grad_accum=%s gc=%s", base_model, profile.get("device"),
             profile.get("free_gb") or 0.0, hp.get("torch_dtype"), hp.get("quant"),
             hp.get("batch_size"), hp.get("grad_accum"),
             hp.get("gradient_checkpointing"))

    tok, model, peft_enabled = _load_tokenizer_and_model(
        base_model, hp, init_adapter=manifest.get("init_adapter"))

    rows = list(ds.read_jsonl(train_path))
    max_len = int(hp.get("max_seq_len", 1024))
    # Build (input_ids, labels) with the PROMPT masked to -100 so the loss is taken
    # on the RESPONSE only (completion-style SFT). Use the model's chat template for
    # instruct bases (Qwen-Instruct etc.) so prompts are wrapped exactly as the
    # model expects; fall back to "{prompt}\n{response}" for raw bases. The old path
    # (DataCollatorForLanguageModeling over "{prompt}\n{response}") trained on the
    # prompt tokens too, which diluted the signal and gave weak instruction-following.
    has_chat = bool(getattr(tok, "chat_template", None))
    encoded = [e for e in (encode_sft_row(tok, r, max_len, has_chat)
                           for r in rows) if e is not None]
    if not encoded:
        raise TrainingError("no usable training rows in train split")
    warm_started = bool(manifest.get("init_adapter"))
    dset = hf_datasets.Dataset.from_list(encoded)
    # Pads input_ids/attention_mask and pads labels with -100 (kept out of loss).
    collator = transformers.DataCollatorForSeq2Seq(
        tok, padding=True, label_pad_token_id=-100)

    bf16 = profile.get("dtype") == "bfloat16" and profile.get("device") == "cuda"
    fp16 = profile.get("dtype") == "float16" and profile.get("device") == "cuda"
    optim = "paged_adamw_8bit" if (profile.get("device") == "cuda"
                                   and _bitsandbytes_available()) else "adamw_torch"
    args = transformers.TrainingArguments(
        output_dir=out_dir, num_train_epochs=float(hp.get("epochs", 1)),
        per_device_train_batch_size=int(hp.get("batch_size", 4)),
        gradient_accumulation_steps=int(hp.get("grad_accum", 1)),
        learning_rate=float(hp.get("learning_rate", 2e-5)),
        gradient_checkpointing=bool(hp.get("gradient_checkpointing")),
        gradient_checkpointing_kwargs={"use_reentrant": False},
        bf16=bf16, fp16=fp16, optim=optim,
        # save_strategy="no": the final adapter is written by trainer.save_model
        # below; per-epoch checkpoint subdirs only bloat the output dir (and the
        # checkpoint upload). We never resume from them — warm-start uses the
        # coordinator's merged adapter.
        logging_steps=10, save_strategy="no", report_to=[])
    trainer = transformers.Trainer(model=model, args=args, train_dataset=dset,
                                   data_collator=collator)
    train_result = trainer.train()
    trainer.save_model(out_dir)
    tok.save_pretrained(out_dir)
    rec["metrics"] = {"method": method, "peft": peft_enabled,
                      "train_loss": float(getattr(train_result, "training_loss", 0.0)),
                      "samples": len(encoded),
                      "warm_started": warm_started,
                      "response_masked": True,
                      "chat_template": has_chat,
                      "epochs": float(hp.get("epochs", 1)),
                      # what the memory-aware auto-config actually ran with
                      "device": profile.get("device"),
                      "torch_dtype": hp.get("torch_dtype"),
                      "quant": hp.get("quant"),
                      "batch_size": hp.get("batch_size"),
                      "grad_accum": hp.get("grad_accum"),
                      "gradient_checkpointing": bool(hp.get("gradient_checkpointing"))}
    return rec


def _run_dpo(rec: dict[str, Any], manifest: dict[str, Any], out_dir: str,
             hp: dict[str, Any]) -> dict[str, Any]:
    _require_transformers()
    try:
        from trl import DPOConfig, DPOTrainer  # type: ignore
        import datasets as hf_datasets  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise TrainingError("DPO needs the trl package",
                           hint="pip install trl") from exc
    base_model = manifest.get("base_model")
    train_path = (manifest.get("train") or {}).get("path") or manifest.get("train_dataset")
    if not train_path:
        raise TrainingError("manifest has no train split path")
    rows = [r for r in ds.read_jsonl(train_path)
            if r.get("prompt") and r.get("chosen") and r.get("rejected")]
    if not rows:
        raise TrainingError("DPO requires {prompt, chosen, rejected} preference rows")
    profile = _detect_memory_profile()
    hp = _auto_memory_hparams(hp, profile)
    tok, model, peft_enabled = _load_tokenizer_and_model(base_model, hp)
    pref = hf_datasets.Dataset.from_list(
        [{"prompt": str(r["prompt"]), "chosen": str(r["chosen"]),
          "rejected": str(r["rejected"])} for r in rows])
    bf16 = profile.get("dtype") == "bfloat16" and profile.get("device") == "cuda"
    fp16 = profile.get("dtype") == "float16" and profile.get("device") == "cuda"
    optim = "paged_adamw_8bit" if (profile.get("device") == "cuda"
                                   and _bitsandbytes_available()) else "adamw_torch"
    args = DPOConfig(
        output_dir=out_dir, num_train_epochs=float(hp.get("epochs", 1)),
        per_device_train_batch_size=int(hp.get("batch_size", 4)),
        gradient_accumulation_steps=int(hp.get("grad_accum", 1)),
        learning_rate=float(hp.get("learning_rate", 5e-6)),
        gradient_checkpointing=bool(hp.get("gradient_checkpointing")),
        gradient_checkpointing_kwargs={"use_reentrant": False},
        bf16=bf16, fp16=fp16, optim=optim,
        beta=float(hp.get("dpo_beta", 0.1)), logging_steps=10, report_to=[])
    trainer = DPOTrainer(model=model, args=args, train_dataset=pref,
                         processing_class=tok)
    result = trainer.train()
    trainer.save_model(out_dir)
    rec["metrics"] = {"method": "dpo", "peft": peft_enabled,
                      "train_loss": float(getattr(result, "training_loss", 0.0)),
                      "pairs": len(rows)}
    return rec


def _collect_checkpoints(out_dir: str) -> list[str]:
    base = Path(out_dir)
    if not base.is_dir():
        return []
    cks = [str(p) for p in base.glob("checkpoint-*") if p.is_dir()]
    for marker in ("pytorch_model.bin", "model.safetensors", "adapter_model.safetensors"):
        if (base / marker).is_file():
            cks.append(str(base / marker))
    return sorted(cks)


# ---------------------------------------------------------------------------
# eval
# ---------------------------------------------------------------------------

def evaluate(cfg, store, *, manifest_path: str,
             model_provider: Optional[str] = None,
             model: Optional[str] = None,
             run_id: Optional[str] = None) -> dict[str, Any]:
    mp = Path(manifest_path)
    if not mp.is_file():
        raise TrainingError(f"manifest not found: {mp}")
    manifest = json.loads(mp.read_text(encoding="utf-8"))
    eval_split = (manifest.get("eval") or manifest.get("test") or manifest.get("train") or {})
    path = eval_split.get("path")
    if not path:
        raise TrainingError("manifest has no eval/test/train split to evaluate")

    from .providers import build_model_adapter
    from .curriculum import loose_hit
    pcfg = cfg.model_provider(model_provider)
    if model:
        pcfg.model = model
    adapter = build_model_adapter(pcfg)

    rows = list(ds.read_jsonl(path))
    total = matched = nonempty = 0
    for r in rows[:200]:
        prompt = str(r.get("prompt") or r.get("text") or "")
        if not prompt:
            continue
        total += 1
        try:
            out = adapter.generate(prompt, max_tokens=128)
        except Exception:  # noqa: BLE001
            out = ""
        if out.strip():
            nonempty += 1
        gold = str(r.get("response") or r.get("chosen") or "")
        if loose_hit(gold, out):
            matched += 1
    report = {
        "eval_id": "ev-" + new_uuid()[:16], "run_id": run_id,
        "manifest": str(mp.resolve()), "provider": pcfg.provider, "model": pcfg.model,
        "metrics": {"evaluated": total, "non_empty": nonempty,
                    "loose_match": matched,
                    "match_rate": round(matched / total, 4) if total else 0.0},
        "created_at": now_ts(),
    }
    store.add_eval(report)
    if run_id:
        rec = store.get_run(run_id)
        if rec:
            rec["eval_report"] = report
            rec["updated_at"] = now_ts()
            store.upsert_run(rec)
    return report


# ---------------------------------------------------------------------------
# status / list / export
# ---------------------------------------------------------------------------

def status(store, run_id: str) -> dict[str, Any]:
    rec = store.get_run(run_id)
    if not rec:
        raise TrainingError(f"run not found: {run_id}")
    return rec


def list_runs(store, limit: int = 200) -> list[dict[str, Any]]:
    return store.list_runs(limit=limit)


def export_run(store, run_id: str, out: Optional[str] = None) -> dict[str, Any]:
    rec = status(store, run_id)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return rec


# ---------------------------------------------------------------------------
# AICF training receipt bridge (Phase D)
# ---------------------------------------------------------------------------

_METHOD_TO_JOB_TYPE = {
    "sft": "ena.train.sft", "lora": "ena.train.sft", "qlora": "ena.train.sft",
    "dpo": "ena.train.dpo", "distill": "ena.train.distill", "eval": "ena.eval",
}


def _to_32(value: Any) -> bytes:
    """Coerce a hex string / bytes / arbitrary string to a 32-byte digest."""
    import hashlib
    if isinstance(value, bytes):
        return value[:32].ljust(32, b"\0") if len(value) != 32 else value
    s = str(value or "")
    if len(s) == 64:
        try:
            return bytes.fromhex(s)
        except ValueError:
            pass
    return hashlib.sha3_256(s.encode("utf-8")).digest()


def _checkpoint_hash(out_dir: str, checkpoint_paths: list[str]) -> bytes:
    import hashlib
    h = hashlib.sha3_256()
    base = Path(out_dir)
    files: list[Path] = []
    for marker in ("adapter_model.safetensors", "model.safetensors", "pytorch_model.bin"):
        if (base / marker).is_file():
            files.append(base / marker)
    if not files and base.is_dir():
        files = sorted(p for p in base.glob("*.safetensors"))[:1]
    for f in files:
        try:
            with f.open("rb") as fh:
                for block in iter(lambda: fh.read(1 << 20), b""):
                    h.update(block)
        except OSError:
            continue
    if not files:
        # no weights on disk (e.g. command backend) — anchor the manifest-derived id
        h.update(b"|".join(p.encode() for p in checkpoint_paths))
    return h.digest()


def build_training_receipt(run: dict[str, Any], manifest: dict[str, Any], *,
                           miner_address: str, provider_id: str, chain_id: int,
                           submitted_at_height: int = 0, cost_paid: int = 0,
                           training_credit: int = 0) -> dict[str, Any]:
    """Build the AICF on-chain training receipt for a completed run.

    Returns a JSON-able dict mirroring ``aicf.aitypes.training_receipt`` plus a
    ``receipt_hash`` (hex). When the ``aicf`` package is importable the real
    dataclass + ``hash_training_receipt`` are used so the hash matches what the
    chain consumes; otherwise an equivalent local hash is computed.
    """
    hp = manifest.get("hyperparameters") or {}
    method = hp.get("method") or manifest.get("metadata", {}).get("method", "sft")
    job_type = _METHOD_TO_JOB_TYPE.get(method, "ena.train.sft")
    md = manifest.get("metadata") or {}
    metrics = run.get("metrics") or {}
    started = int(run.get("created_at", 0))
    completed = int(run.get("updated_at", started))
    gpu_hours = max(0.0, (completed - started) / 3600.0)
    task_id = _to_32(md.get("source_sha256") or run.get("run_id"))
    dataset_hash = _to_32(md.get("source_sha256"))
    ckpt_hash = _checkpoint_hash(run.get("output_dir", ""), run.get("checkpoint_paths", []))

    fields = {
        "task_id": task_id, "job_type": job_type,
        "miner_address": _to_32(miner_address), "provider_id": provider_id,
        "dataset_hash": dataset_hash, "model_checkpoint_hash": ckpt_hash,
        "epochs_completed": int(metrics.get("epochs", hp.get("epochs", 1)) or 0),
        "samples_processed": int(metrics.get("samples", metrics.get("pairs", 0)) or 0),
        "gpu_hours": round(gpu_hours, 4), "cost_paid": int(cost_paid),
        "training_credit": int(training_credit),
        "started_at": started, "completed_at": completed,
        "chain_id": int(chain_id), "submitted_at_height": int(submitted_at_height),
    }
    try:
        from aicf.aitypes.training_receipt import TrainingReceipt, hash_training_receipt  # type: ignore
        receipt = TrainingReceipt(**fields)
        receipt_hash = hash_training_receipt(receipt).hex()
    except Exception:  # noqa: BLE001 - aicf optional in dev installs
        import hashlib
        canon = "|".join(
            f"{k}={v.hex() if isinstance(v, bytes) else v}" for k, v in sorted(fields.items()))
        receipt_hash = hashlib.sha3_256(canon.encode("utf-8")).hexdigest()

    out = {k: (v.hex() if isinstance(v, bytes) else v) for k, v in fields.items()}
    out["receipt_hash"] = receipt_hash
    out["run_id"] = run.get("run_id")
    return out
