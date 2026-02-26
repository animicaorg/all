from __future__ import annotations

import math

import torch
from torch import nn


class DecoderLM(nn.Module):
    def __init__(
        self,
        vocab_size: int = 256,
        d_model: int = 256,
        n_heads: int = 4,
        n_layers: int = 4,
        max_seq_len: int = 256,
        dropout: float = 0.1,
    ) -> None:
        super().__init__()
        self.max_seq_len = max_seq_len
        self.tok = nn.Embedding(vocab_size, d_model)
        self.pos = nn.Embedding(max_seq_len, d_model)
        enc_layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=n_heads,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            batch_first=True,
            activation="gelu",
        )
        self.blocks = nn.TransformerEncoder(enc_layer, num_layers=n_layers)
        self.ln = nn.LayerNorm(d_model)
        self.head = nn.Linear(d_model, vocab_size, bias=False)

    def forward(self, idx: torch.Tensor) -> torch.Tensor:
        b, t = idx.shape
        if t > self.max_seq_len:
            idx = idx[:, -self.max_seq_len :]
            t = idx.shape[1]
        pos = torch.arange(0, t, device=idx.device).unsqueeze(0).expand(b, t)
        x = self.tok(idx) + self.pos(pos)
        mask = torch.triu(torch.ones(t, t, device=idx.device), diagonal=1).bool()
        x = self.blocks(x, mask=mask)
        x = self.ln(x)
        return self.head(x)

    @torch.no_grad()
    def generate(self, idx: torch.Tensor, max_new_tokens: int = 64, temperature: float = 0.8, top_p: float = 0.95) -> torch.Tensor:
        for _ in range(max_new_tokens):
            logits = self(idx)[:, -1, :] / max(temperature, 1e-6)
            probs = torch.softmax(logits, dim=-1)
            sorted_probs, sorted_idx = torch.sort(probs, descending=True, dim=-1)
            cdf = torch.cumsum(sorted_probs, dim=-1)
            mask = cdf > top_p
            mask[:, 0] = False
            sorted_probs = sorted_probs.masked_fill(mask, 0)
            sorted_probs = sorted_probs / sorted_probs.sum(dim=-1, keepdim=True)
            next_token = sorted_idx.gather(-1, torch.multinomial(sorted_probs, num_samples=1))
            idx = torch.cat([idx, next_token], dim=1)
        return idx
