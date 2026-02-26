from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Callable

import torch
import torch.nn.functional as F
from torch import optim

from animica_studio.ena_ml.model.transformer import DecoderLM

from .checkpoint import save_checkpoint
from .config import TrainerConfig
from .metrics import perplexity


class Trainer:
    def __init__(self, config: TrainerConfig, model: DecoderLM | None = None) -> None:
        self.cfg = config
        self.device = torch.device(config.device)
        self.model = model or DecoderLM(max_seq_len=config.seq_len)
        self.model.to(self.device)
        self.optimizer = optim.AdamW(self.model.parameters(), lr=config.lr)

    def _lr_for_step(self, step: int) -> float:
        if step <= self.cfg.warmup_steps:
            return self.cfg.lr * step / max(1, self.cfg.warmup_steps)
        progress = (step - self.cfg.warmup_steps) / max(1, self.cfg.total_steps - self.cfg.warmup_steps)
        return self.cfg.lr * 0.5 * (1 + math.cos(math.pi * min(max(progress, 0.0), 1.0)))

    def train(self, token_ids: list[int], run_dir: Path, log_fn: Callable[[dict], None] | None = None) -> dict:
        run_dir.mkdir(parents=True, exist_ok=True)
        metrics_path = run_dir / "train_metrics.jsonl"
        seq_len = self.cfg.seq_len
        data = torch.tensor(token_ids, dtype=torch.long)
        if data.numel() <= seq_len + 1:
            raise ValueError("dataset too small for configured seq_len")

        best_eval: float | None = None
        t0 = time.time()

        for step in range(1, self.cfg.total_steps + 1):
            lr = self._lr_for_step(step)
            for g in self.optimizer.param_groups:
                g["lr"] = lr
            self.optimizer.zero_grad(set_to_none=True)

            total_loss = 0.0
            for _ in range(max(1, self.cfg.grad_accum_steps)):
                starts = torch.randint(0, data.numel() - seq_len - 1, (self.cfg.batch_size,))
                x = torch.stack([data[s : s + seq_len] for s in starts]).to(self.device)
                y = torch.stack([data[s + 1 : s + seq_len + 1] for s in starts]).to(self.device)
                logits = self.model(x)
                loss = F.cross_entropy(logits.reshape(-1, logits.size(-1)), y.reshape(-1))
                (loss / self.cfg.grad_accum_steps).backward()
                total_loss += float(loss.item())

            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
            self.optimizer.step()

            rec = {
                "step": step,
                "loss": total_loss / max(1, self.cfg.grad_accum_steps),
                "lr": lr,
                "tokens_per_sec": (step * self.cfg.batch_size * seq_len) / max(time.time() - t0, 1e-6),
            }
            if step % self.cfg.eval_interval == 0 or step == self.cfg.total_steps:
                rec["eval_loss"] = rec["loss"]
                rec["ppl"] = perplexity(rec["eval_loss"])
                if best_eval is None or rec["eval_loss"] < best_eval:
                    best_eval = rec["eval_loss"]
                    save_checkpoint(run_dir / "best.pt", model=self.model, optimizer=self.optimizer, step=step, best_eval_loss=best_eval)
            if step % self.cfg.checkpoint_interval == 0 or step == self.cfg.total_steps:
                ckpt = run_dir / f"step-{step}.pt"
                save_checkpoint(ckpt, model=self.model, optimizer=self.optimizer, step=step, best_eval_loss=best_eval)
                rec["checkpoint_path"] = str(ckpt)

            with metrics_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            if log_fn:
                log_fn(rec)

        return {"total_steps": self.cfg.total_steps, "best_eval_loss": best_eval, "run_dir": str(run_dir)}
