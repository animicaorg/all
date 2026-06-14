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
import shlex
import subprocess
from pathlib import Path
from typing import Any, Optional

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


def _load_tokenizer_and_model(base_model: str, hp: dict[str, Any]):
    """Load tokenizer + causal LM, applying QLoRA 4-bit quant / LoRA adapters
    when requested. Returns (tokenizer, model, peft_enabled)."""
    transformers, _ = _require_transformers()
    AutoTokenizer = transformers.AutoTokenizer
    AutoModelForCausalLM = transformers.AutoModelForCausalLM

    tok = AutoTokenizer.from_pretrained(base_model)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    model_kwargs: dict[str, Any] = {}
    quant = hp.get("quant")
    if quant in ("4bit", "8bit"):
        try:
            import bitsandbytes  # type: ignore  # noqa: F401
            from transformers import BitsAndBytesConfig  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise TrainingError("QLoRA quant needs bitsandbytes",
                               hint="pip install bitsandbytes (CUDA required)") from exc
        model_kwargs["quantization_config"] = BitsAndBytesConfig(
            load_in_4bit=(quant == "4bit"), load_in_8bit=(quant == "8bit"))
    model = AutoModelForCausalLM.from_pretrained(base_model, **model_kwargs)

    lora = hp.get("lora") or {}
    peft_enabled = False
    if lora.get("enabled"):
        try:
            from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise TrainingError("LoRA needs the peft package",
                               hint="pip install peft") from exc
        if quant in ("4bit", "8bit"):
            model = prepare_model_for_kbit_training(model)
        peft_cfg = LoraConfig(
            r=int(lora.get("r", 16)), lora_alpha=int(lora.get("alpha", 32)),
            lora_dropout=float(lora.get("dropout", 0.05)), bias="none",
            task_type="CAUSAL_LM", target_modules=lora.get("target_modules"))
        model = get_peft_model(model, peft_cfg)
        peft_enabled = True
    return tok, model, peft_enabled


def _run_sft(rec: dict[str, Any], manifest: dict[str, Any], out_dir: str,
             hp: dict[str, Any], method: str) -> dict[str, Any]:
    transformers, hf_datasets = _require_transformers()
    base_model = manifest.get("base_model")
    train_path = (manifest.get("train") or {}).get("path") or manifest.get("train_dataset")
    if not train_path:
        raise TrainingError("manifest has no train split path")

    tok, model, peft_enabled = _load_tokenizer_and_model(base_model, hp)

    rows = list(ds.read_jsonl(train_path))

    def _fmt(r: dict[str, Any]) -> str:
        if "prompt" in r and "response" in r:
            return f"{r['prompt']}\n{r['response']}"
        # distillation datasets carry the teacher output under 'response'/'text'
        return str(r.get("text", ""))

    texts = [_fmt(r) for r in rows if _fmt(r).strip()]
    if not texts:
        raise TrainingError("no usable training rows in train split")
    enc = tok(texts, truncation=True, max_length=int(hp.get("max_seq_len", 1024)),
              padding="max_length")
    enc["labels"] = [list(ids) for ids in enc["input_ids"]]
    dset = hf_datasets.Dataset.from_dict(enc)

    args = transformers.TrainingArguments(
        output_dir=out_dir, num_train_epochs=float(hp.get("epochs", 1)),
        per_device_train_batch_size=int(hp.get("batch_size", 4)),
        gradient_accumulation_steps=int(hp.get("grad_accum", 1)),
        learning_rate=float(hp.get("learning_rate", 2e-5)),
        logging_steps=10, save_strategy="epoch", report_to=[])
    trainer = transformers.Trainer(model=model, args=args, train_dataset=dset)
    train_result = trainer.train()
    trainer.save_model(out_dir)
    tok.save_pretrained(out_dir)
    rec["metrics"] = {"method": method, "peft": peft_enabled,
                      "train_loss": float(getattr(train_result, "training_loss", 0.0)),
                      "samples": len(texts),
                      "epochs": float(hp.get("epochs", 1))}
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
    tok, model, peft_enabled = _load_tokenizer_and_model(base_model, hp)
    pref = hf_datasets.Dataset.from_list(
        [{"prompt": str(r["prompt"]), "chosen": str(r["chosen"]),
          "rejected": str(r["rejected"])} for r in rows])
    args = DPOConfig(
        output_dir=out_dir, num_train_epochs=float(hp.get("epochs", 1)),
        per_device_train_batch_size=int(hp.get("batch_size", 4)),
        learning_rate=float(hp.get("learning_rate", 5e-6)),
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
        if gold and gold.strip().lower()[:40] in out.strip().lower():
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
