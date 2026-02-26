from __future__ import annotations

import torch

from animica_studio.ena_ml.model.tokenizer import ByteTokenizer
from animica_studio.ena_ml.model.transformer import DecoderLM


def generate_text(model: DecoderLM, tokenizer: ByteTokenizer, prompt: str, *, max_new_tokens: int = 64, temperature: float = 0.8, top_p: float = 0.95) -> str:
    model.eval()
    ids = tokenizer.encode(prompt)
    idx = torch.tensor([ids], dtype=torch.long, device=next(model.parameters()).device)
    out = model.generate(idx, max_new_tokens=max_new_tokens, temperature=temperature, top_p=top_p)
    return tokenizer.decode(out[0].tolist())
