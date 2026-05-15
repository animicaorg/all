"""Shared training-stage utilities for CPT and SFT.

Provides:
- resolve_backend(): wraps flagship_agent.modes.resolve_mode
- ensure_base_model(): downloads + verifies the base model in full mode
- simulate_stage(): writes stub artifacts (simulate mode)
- lite_stage(): tokenizer-only sanity pass
- full_stage(): real HF + PEFT/LoRA training loop
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Iterator, Optional

from flagship_agent.modes import EffectiveBackend, resolve_mode


# --------------------------------------------------------------------------- #
# Dataset reader                                                              #
# --------------------------------------------------------------------------- #

def _iter_jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def text_from_record(rec: dict, *, kind: str) -> str:
    """Convert a record to a single training text string.

    kind="cpt"  -> rec["text"]
    kind="sft"  -> apply_chat_template or concatenate messages
    """
    if kind == "cpt":
        return rec.get("text", "") or ""
    msgs = rec.get("messages") or []
    return "\n\n".join(f"<|{m.get('role', 'user')}|>\n{m.get('content', '')}"
                        for m in msgs) + "\n<|end|>"


# --------------------------------------------------------------------------- #
# Stage entrypoints                                                           #
# --------------------------------------------------------------------------- #

def resolve_backend(*, requested: str, requested_base_model: str,
                    training_cfg: dict) -> EffectiveBackend:
    return resolve_mode(
        requested=requested,
        requested_base_model=requested_base_model,
        training_cfg=training_cfg,
    )


def simulate_stage(*, kind: str, dataset_path: Path,
                   out_dir: Path,
                   backend: EffectiveBackend,
                   stage_cfg: dict) -> dict:
    """No model load. Emit a deterministic stub checkpoint + summary."""
    ckpt_dir = out_dir / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    latest = ckpt_dir / "step-0"
    latest.mkdir(parents=True, exist_ok=True)
    (latest / "MARKER.simulate").write_text(
        f"simulate mode; no real weights; kind={kind}", encoding="utf-8")
    n_records = 0
    for _ in _iter_jsonl(dataset_path):
        n_records += 1
        if n_records > 5:    # cheap: don't iterate the whole file
            break
    if (ckpt_dir / "latest").exists() or (ckpt_dir / "latest").is_symlink():
        (ckpt_dir / "latest").unlink()
    try:
        os.symlink(latest.name, ckpt_dir / "latest")
    except OSError:
        (ckpt_dir / "latest_pointer.txt").write_text(latest.name,
                                                      encoding="utf-8")
    summary = {
        "schema": 1,
        "kind": kind,
        "effective_mode": "simulate",
        "steps_completed": 0,
        "loss_initial": None,
        "loss_final": None,
        "duration_sec": 0,
        "checkpoint_dir": str(latest),
        "n_records_glimpsed": n_records,
    }
    _write_summary(out_dir, summary, backend)
    return summary


def lite_stage(*, kind: str, dataset_path: Path,
               out_dir: Path,
               backend: EffectiveBackend,
               stage_cfg: dict) -> dict:
    """Tokenizer-only sanity pass. Fakes a few training steps."""
    ckpt_dir = out_dir / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    latest = ckpt_dir / "step-10"
    latest.mkdir(parents=True, exist_ok=True)
    n_steps = min(int(stage_cfg.get("max_steps", 10)), 10)
    # Tokenize a handful of records to validate the dataset shape.
    n_records = 0
    n_tokens = 0
    try:
        from transformers import AutoTokenizer    # type: ignore
        tok = AutoTokenizer.from_pretrained(
            backend.loaded_base_model or "hf-internal-testing/tiny-random-LlamaForCausalLM",
            trust_remote_code=True,
        )
        for rec in _iter_jsonl(dataset_path):
            text = text_from_record(rec, kind=kind)
            ids = tok(text, truncation=True,
                      max_length=int(stage_cfg.get("seq_len", 512))).input_ids
            n_tokens += len(ids)
            n_records += 1
            if n_records >= 32:
                break
    except Exception as exc:
        # Lite mode tolerates tokenizer load failures — record and continue.
        (out_dir / "lite_tokenizer_error.txt").write_text(
            str(exc), encoding="utf-8")

    (latest / "MARKER.lite").write_text(
        f"lite mode; tokenizer={backend.loaded_base_model}; "
        f"records={n_records}; tokens={n_tokens}", encoding="utf-8")
    summary = {
        "schema": 1, "kind": kind,
        "effective_mode": "lite",
        "steps_completed": n_steps,
        "loss_initial": 1.0,
        "loss_final": 0.9,
        "duration_sec": 1,
        "checkpoint_dir": str(latest),
        "n_records_tokenized": n_records,
        "n_tokens_seen": n_tokens,
    }
    _write_summary(out_dir, summary, backend)
    return summary


def full_stage(*, kind: str, dataset_path: Path,
               out_dir: Path,
               backend: EffectiveBackend,
               stage_cfg: dict,
               base_checkpoint: Optional[Path] = None) -> dict:
    """Real CPT or SFT training run.

    Loads base_model.id via transformers, attaches a PEFT/LoRA adapter,
    runs ``max_steps`` of training, saves periodic checkpoints.

    Raises RuntimeError on missing deps or strict-mode violations.
    """
    try:
        import torch                          # type: ignore
        from transformers import (
            AutoModelForCausalLM, AutoTokenizer,
            get_linear_schedule_with_warmup,
        )                                     # type: ignore
        from torch.utils.data import Dataset, DataLoader   # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            f"full mode requires torch + transformers: {exc}",
        ) from exc

    seq_len = int(stage_cfg.get("seq_len", 4096))
    max_steps = int(stage_cfg.get("max_steps",
                                  stage_cfg.get("num_epochs", 3) * 1000))
    micro_batch = int(stage_cfg.get("micro_batch", 1))
    grad_accum = int(stage_cfg.get("grad_accum", 1))
    lr = float(stage_cfg.get("learning_rate", 1e-5))
    warmup = int(stage_cfg.get("warmup_steps",
                               int(stage_cfg.get("warmup_ratio", 0.03) *
                                    max_steps)))
    save_every = int(stage_cfg.get("save_every", 200))
    guard_threshold = float(stage_cfg.get("guard_loss_threshold", 1e9))

    device = backend.accelerator_effective
    if device == "cuda":
        dtype = torch.bfloat16
    elif device == "mps":
        dtype = torch.float16
    else:
        dtype = torch.float32
    print(f"[train] loading {backend.loaded_base_model} on {device} ({dtype})")
    tokenizer = AutoTokenizer.from_pretrained(
        backend.loaded_base_model, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        backend.loaded_base_model,
        torch_dtype=dtype,
        trust_remote_code=True,
    )
    if base_checkpoint is not None and base_checkpoint.is_dir():
        try:
            from peft import PeftModel   # type: ignore
            model = PeftModel.from_pretrained(model, str(base_checkpoint))
            print(f"[train] loaded PEFT adapter from {base_checkpoint}")
        except Exception as exc:
            print(f"[train] could not load adapter at {base_checkpoint}: {exc}")
    lora_cfg = stage_cfg.get("lora", {})
    if lora_cfg.get("enabled", True):
        try:
            from peft import LoraConfig, get_peft_model, TaskType  # type: ignore
            lora = LoraConfig(
                task_type=TaskType.CAUSAL_LM,
                r=int(lora_cfg.get("rank", 16)),
                lora_alpha=int(lora_cfg.get("alpha", 32)),
                lora_dropout=float(lora_cfg.get("dropout", 0.05)),
                target_modules=list(lora_cfg.get("target_modules", [])) or None,
            )
            model = get_peft_model(model, lora)
        except ImportError as exc:
            raise RuntimeError(f"LoRA requires peft: {exc}") from exc
    model.to(device)
    model.train()

    class _JsonlDataset(Dataset):
        def __init__(self, path: Path) -> None:
            self.records = list(_iter_jsonl(path))
        def __len__(self) -> int:
            return len(self.records)
        def __getitem__(self, idx: int):
            text = text_from_record(self.records[idx], kind=kind)
            enc = tokenizer(text, truncation=True, max_length=seq_len,
                             padding="max_length", return_tensors="pt")
            return {
                "input_ids": enc.input_ids[0],
                "attention_mask": enc.attention_mask[0],
                "labels": enc.input_ids[0],
            }

    ds = _JsonlDataset(dataset_path)
    if len(ds) == 0:
        raise RuntimeError(f"empty dataset: {dataset_path}")
    loader = DataLoader(ds, batch_size=micro_batch, shuffle=True)
    optim = torch.optim.AdamW(model.parameters(), lr=lr,
                              weight_decay=float(stage_cfg.get(
                                  "weight_decay", 0.01)))
    sched = get_linear_schedule_with_warmup(
        optim, num_warmup_steps=warmup, num_training_steps=max_steps)

    ckpt_dir = out_dir / "checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    step = 0
    accum_loss = 0.0
    loss_initial: Optional[float] = None
    loss_final: Optional[float] = None
    guard_window: list[float] = []
    guard_size = int(stage_cfg.get("guard_window", 3))
    losses: list[float] = []

    optim.zero_grad()
    done = False
    while not done:
        for batch in loader:
            batch = {k: v.to(device) for k, v in batch.items()}
            out = model(**batch)
            loss = out.loss / grad_accum
            loss.backward()
            accum_loss += float(loss.item())
            if (step + 1) % grad_accum == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                optim.step()
                sched.step()
                optim.zero_grad()
                losses.append(accum_loss)
                if loss_initial is None:
                    loss_initial = accum_loss
                loss_final = accum_loss
                guard_window.append(accum_loss)
                if len(guard_window) > guard_size:
                    guard_window.pop(0)
                if (len(guard_window) == guard_size and
                        all(l > guard_threshold for l in guard_window)):
                    raise RuntimeError(
                        f"loss guard tripped: last {guard_size} losses "
                        f"all > {guard_threshold}",
                    )
                accum_loss = 0.0
            step += 1
            if step % save_every == 0:
                _save_ckpt(model, tokenizer, ckpt_dir / f"step-{step}")
            if step >= max_steps:
                done = True
                break
    _save_ckpt(model, tokenizer, ckpt_dir / f"step-{step}")
    duration = round(time.time() - t0, 1)
    summary = {
        "schema": 1, "kind": kind,
        "effective_mode": "full",
        "steps_completed": step,
        "loss_initial": loss_initial,
        "loss_final": loss_final,
        "duration_sec": duration,
        "checkpoint_dir": str(ckpt_dir / f"step-{step}"),
    }
    _write_summary(out_dir, summary, backend)
    return summary


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _save_ckpt(model, tokenizer, path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        model.save_pretrained(str(path))
        tokenizer.save_pretrained(str(path))
    except Exception as exc:    # noqa: BLE001
        (path / "save_error.txt").write_text(str(exc), encoding="utf-8")


def _write_summary(out_dir: Path, summary: dict,
                   backend: EffectiveBackend) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "backend.json").write_text(
        json.dumps(backend.to_dict(), indent=2), encoding="utf-8")
