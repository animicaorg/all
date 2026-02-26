from __future__ import annotations

import torch
from torch import nn


class TextHead(nn.Module):
    def __init__(self, hidden_size: int = 128, vocab: int = 256) -> None:
        super().__init__()
        self.proj = nn.Linear(hidden_size, vocab)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.proj(x)
