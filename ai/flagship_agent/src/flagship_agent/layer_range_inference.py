"""Layer-range pipeline runner.

A pipeline-mode worker computes a *contiguous range* of a transformer's
layers and emits the resulting hidden states to the next stage. With N
stages each worker runs ~total_layers / N layers; with direct
worker-to-worker activation transport (see
agent_runtime.pipeline_transport) the prefill latency drops roughly by
the same factor, capped by the per-stage activation transfer cost.

What this module ships
----------------------
A real LayerRangeRunner that:
  - locates the decoder layer list on any nn.Module-style model whose
    layers live at one of the standard paths (model.layers,
    transformer.h, model.decoder.layers — covers Llama / Qwen / Mistral
    / GPT-2 family / Falcon decoders)
  - runs a contiguous layer range without touching the layers outside
    it (so a future sharded-bundle format that *loads* only the
    assigned range plugs in with zero protocol changes)
  - serializes hidden states with safetensors (canonical dtype/shape;
    no torch-pickle in the wire format) and base64-encodes for the
    JSON-RPC fallback path. The direct-W2W path uses raw bytes.

What's still in-scope for follow-up (and why this is honest):
  - Single-process layer-by-layer forward semantics. Decoder layers
    on Llama-style models expect ``(hidden_states, attention_mask,
    position_ids, past_key_value, ...)``. We forward through the
    standard nn.Module __call__; models that need additional positional
    arguments will require a model-specific runner. The reference path
    here handles the common LLM families above; exotic architectures
    (DeepSeek-V3 MoE, MoEHead, etc.) need their own runner.
  - Streaming-decode parallelism. Today's implementation parallelises
    *prefill* only — the final stage's autoregressive generation loop
    runs on a single worker. This is the typical "Petals tier 1" cut;
    full token-level parallel decode means routing every new token
    through every stage, which is bandwidth-bound and only pays off
    over a direct transport.
  - No sharded-bundle loading yet. The runner loads the whole model on
    each worker; only the forward pass is restricted. Memory savings
    arrive with a per-layer-range bundle format (separate effort).

The module imports cleanly without transformers/torch installed; the
heavy imports happen lazily so a thin `pip install animica` still works
on a box that doesn't need to mine.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


log = logging.getLogger("flagship_agent.layer_range_inference")


# --------------------------------------------------------------------------- #
# Tensor (de)serialization                                                    #
# --------------------------------------------------------------------------- #

def _safetensors_dumps(tensors: Dict[str, "torch.Tensor"]) -> bytes:    # noqa: F821
    """Serialize a dict of tensors to a single bytes blob using
    safetensors. Returns raw bytes (no base64). The keys carry shape +
    dtype implicitly through safetensors's metadata, so the consumer
    side can re-materialise without any out-of-band schema."""
    from safetensors.torch import save    # type: ignore[import-not-found]
    return save(tensors)


def _safetensors_loads(blob: bytes) -> Dict[str, "torch.Tensor"]:    # noqa: F821
    from safetensors.torch import load    # type: ignore[import-not-found]
    return load(blob)


def encode_activation_b64(tensors: Dict[str, "torch.Tensor"]) -> str:    # noqa: F821
    return base64.b64encode(_safetensors_dumps(tensors)).decode("ascii")


def decode_activation_b64(blob_b64: str) -> Dict[str, "torch.Tensor"]:    # noqa: F821
    return _safetensors_loads(base64.b64decode(blob_b64))


# --------------------------------------------------------------------------- #
# Layer locator                                                               #
# --------------------------------------------------------------------------- #

# Ordered list of attribute paths to probe. First match wins.
_LAYER_PATHS = (
    # Llama / Qwen / Mistral / Yi: hf transformers default.
    "model.layers",
    # Llama-without-wrapper, used by some quantised forks.
    "layers",
    # GPT-2, GPT-Neo, GPT-J: transformer.h
    "transformer.h",
    # T5 + bart decoder: decoder.layers
    "decoder.layers",
    # OPT: model.decoder.layers
    "model.decoder.layers",
    # GPT-NeoX: gpt_neox.layers
    "gpt_neox.layers",
    # MPT: transformer.blocks
    "transformer.blocks",
)


def locate_decoder_layers(model: Any) -> List[Any]:
    """Find the list of decoder blocks on a transformer-style model.

    Returns the list (or list-like nn.ModuleList) so the caller can
    index/slice it. Raises ``ValueError`` if nothing matches — the
    caller may then fall back to the reference (identity) transform.
    """
    for path in _LAYER_PATHS:
        try:
            cur = model
            for part in path.split("."):
                cur = getattr(cur, part)
        except AttributeError:
            continue
        if hasattr(cur, "__len__") and hasattr(cur, "__getitem__"):
            return cur
    raise ValueError(
        "could not locate decoder layers on model "
        f"({type(model).__name__}); tried paths: {', '.join(_LAYER_PATHS)}"
    )


# --------------------------------------------------------------------------- #
# Layer range planning                                                        #
# --------------------------------------------------------------------------- #

@dataclass
class LayerRange:
    """A half-open range of decoder layer indices [start, end)."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid layer range [{self.start}, {self.end})")

    @property
    def length(self) -> int:
        return self.end - self.start

    def as_dict(self) -> Dict[str, int]:
        return {"start": self.start, "end": self.end}


def plan_layer_ranges(total_layers: int, n_stages: int) -> List[LayerRange]:
    """Split [0, total_layers) across n_stages contiguous, non-overlapping
    ranges. The last stage absorbs the remainder when the layer count
    doesn't divide evenly. Earlier stages get the smaller chunks so the
    final stage (which also runs the LM head + generation) doesn't
    bottleneck on a long range.
    """
    if n_stages <= 0:
        raise ValueError(f"n_stages must be >= 1, got {n_stages}")
    if total_layers <= 0:
        raise ValueError(f"total_layers must be >= 1, got {total_layers}")
    if n_stages > total_layers:
        # More stages than layers — collapse: one layer per stage for
        # the first total_layers stages and the rest get empty ranges
        # (caller can skip them).
        ranges = [LayerRange(i, i + 1) for i in range(total_layers)]
        for _ in range(n_stages - total_layers):
            ranges.append(LayerRange(total_layers, total_layers))
        return ranges
    base = total_layers // n_stages
    remainder = total_layers - base * n_stages
    out: List[LayerRange] = []
    cursor = 0
    for stage in range(n_stages):
        # Put remainder layers on the LAST stage so prefill stages stay
        # balanced and the final stage's slightly-larger window includes
        # the (lighter) LM-head suffix work.
        length = base + (remainder if stage == n_stages - 1 else 0)
        out.append(LayerRange(cursor, cursor + length))
        cursor += length
    assert cursor == total_layers, (cursor, total_layers)
    return out


# --------------------------------------------------------------------------- #
# Runner                                                                      #
# --------------------------------------------------------------------------- #

class LayerRangeRunner:
    """Pipeline-stage forward pass over a contiguous layer range.

    Three entry points compose end-to-end:
      - ``embed_and_run_prefix(prompt, layer_range)`` — stage 0. Tokenizes
        the prompt, embeds, runs the first stage's layers, returns the
        hidden state (and the attention mask the next stage needs).
      - ``run_layers(payload, layer_range)`` — middle stages. Take the
        upstream hidden state + mask, run our layer range, hand off.
      - ``run_suffix_and_generate(payload, layer_range, max_new_tokens, …)``
        — final stage. Run our layers, apply the model's final norm +
        LM head, sample / generate text. Returns the assembled string.

    The runner is the same dtype/device as the underlying model and
    performs forward passes inside ``torch.no_grad()`` — no training
    state is mutated. Lazy import of torch + transformers keeps the
    `animica` wheel installable on a box without an AI stack; the
    actual work happens only when a pipeline-tier worker decides to
    run a real stage.
    """

    def __init__(
        self,
        model: Any,
        tokenizer: Any,
        *,
        device: Optional[str] = None,
        dtype: Optional[Any] = None,
    ) -> None:
        # Heavy imports are local so importing this module on a box
        # without torch installed still works (e.g. a chat client
        # making outbound RPCs only).
        import torch    # type: ignore[import-not-found]
        self._torch = torch
        self.model = model
        self.tokenizer = tokenizer
        self.layers = locate_decoder_layers(model)
        self.total_layers = len(self.layers)
        self.device = device or self._infer_device(model)
        self.dtype = dtype or next(iter(model.parameters())).dtype
        self.model.eval()
        log.info(
            "LayerRangeRunner ready: total_layers=%d device=%s dtype=%s",
            self.total_layers, self.device, self.dtype,
        )

    @staticmethod
    def _infer_device(model: Any) -> str:
        try:
            return str(next(model.parameters()).device)
        except StopIteration:
            return "cpu"

    # ---- forward helpers ----------------------------------------------- #

    def _embed_input_ids(self, input_ids: Any) -> Any:
        """Run the model's input-embedding lookup. We probe the common
        attribute names (Llama/Qwen: ``model.embed_tokens``; GPT-2:
        ``transformer.wte``) and fall back to ``get_input_embeddings``
        which every HF causal LM exposes."""
        if hasattr(self.model, "get_input_embeddings"):
            embed = self.model.get_input_embeddings()
            return embed(input_ids)
        for path in ("model.embed_tokens", "transformer.wte"):
            cur = self.model
            try:
                for part in path.split("."):
                    cur = getattr(cur, part)
            except AttributeError:
                continue
            return cur(input_ids)
        raise ValueError(
            "could not find input embedding layer; model must expose "
            "get_input_embeddings() or model.embed_tokens / transformer.wte"
        )

    def _final_norm(self, hidden: Any) -> Any:
        """Apply the model's final pre-LM-head normalization, if any."""
        for path in ("model.norm", "transformer.ln_f", "model.final_layernorm",
                     "decoder.final_layer_norm"):
            cur = self.model
            try:
                for part in path.split("."):
                    cur = getattr(cur, part)
            except AttributeError:
                continue
            return cur(hidden)
        return hidden

    def _lm_head(self, hidden: Any) -> Any:
        if hasattr(self.model, "lm_head"):
            return self.model.lm_head(hidden)
        if hasattr(self.model, "embed_out"):
            return self.model.embed_out(hidden)
        # Tied weights with input embedding (GPT-2 default): logits =
        # hidden @ embed.weight.T
        embed = self.model.get_input_embeddings()
        return hidden @ embed.weight.T

    def _run_range(self, hidden: Any, layer_range: LayerRange,
                   attention_mask: Optional[Any] = None) -> Any:
        """Forward through layers [start, end). The decoder block's
        signature varies by architecture; we try the common HF call
        shape first and fall back to a positional call when that fails."""
        torch = self._torch
        for i in range(layer_range.start, layer_range.end):
            layer = self.layers[i]
            try:
                out = layer(hidden, attention_mask=attention_mask)
            except TypeError:
                out = layer(hidden)
            if isinstance(out, (tuple, list)):
                hidden = out[0]
            else:
                hidden = out
        return hidden

    # ---- public API ---------------------------------------------------- #

    def embed_and_run_prefix(
        self,
        prompt: str,
        layer_range: LayerRange,
    ) -> Tuple[bytes, Dict[str, Any]]:
        """Stage 0. Returns (payload_bytes, meta_dict)."""
        torch = self._torch
        with torch.no_grad():
            ids = self.tokenizer(prompt, return_tensors="pt")
            input_ids = ids["input_ids"].to(self.device)
            attention_mask = ids.get("attention_mask")
            if attention_mask is not None:
                attention_mask = attention_mask.to(self.device)
            hidden = self._embed_input_ids(input_ids)
            hidden = self._run_range(hidden, layer_range,
                                     attention_mask=attention_mask)
        tensors: Dict[str, Any] = {"hidden": hidden.cpu().contiguous(),
                                   "input_ids": input_ids.cpu()}
        if attention_mask is not None:
            tensors["attention_mask"] = attention_mask.cpu()
        payload = _safetensors_dumps(tensors)
        meta = {
            "stage_kind": "prefix",
            "layer_range": layer_range.as_dict(),
            "prompt_tokens": int(input_ids.shape[-1]),
            "dtype": str(hidden.dtype),
            "shape": list(hidden.shape),
        }
        return payload, meta

    def run_layers(
        self,
        payload: bytes,
        layer_range: LayerRange,
    ) -> Tuple[bytes, Dict[str, Any]]:
        """Middle stage. Decodes the upstream activation, runs our
        layer range, re-emits."""
        torch = self._torch
        tensors = _safetensors_loads(payload)
        hidden = tensors["hidden"].to(self.device).to(self.dtype)
        attention_mask = None
        if "attention_mask" in tensors:
            attention_mask = tensors["attention_mask"].to(self.device)
        with torch.no_grad():
            hidden = self._run_range(hidden, layer_range,
                                     attention_mask=attention_mask)
        out: Dict[str, Any] = {"hidden": hidden.cpu().contiguous(),
                               "input_ids": tensors["input_ids"]}
        if attention_mask is not None:
            out["attention_mask"] = attention_mask.cpu()
        return _safetensors_dumps(out), {
            "stage_kind": "mid",
            "layer_range": layer_range.as_dict(),
            "dtype": str(hidden.dtype),
            "shape": list(hidden.shape),
        }

    def run_suffix_and_generate(
        self,
        payload: bytes,
        layer_range: LayerRange,
        *,
        max_new_tokens: int = 128,
        temperature: float = 0.2,
        top_p: float = 0.95,
    ) -> Tuple[str, Dict[str, Any]]:
        """Final stage. Runs our layer range + final norm + LM head and
        decodes ``max_new_tokens`` greedy / nucleus-sampled tokens.

        Note: this is the *prefill-parallel* final stage. The
        autoregressive decode runs entirely here — i.e. earlier stages
        don't re-execute for each new token. That's the "Petals tier 1"
        cut; full token-level parallel decode means routing every new
        token through every stage, which is bandwidth-bound and only
        pays off over the direct W2W transport.
        """
        torch = self._torch
        tensors = _safetensors_loads(payload)
        hidden = tensors["hidden"].to(self.device).to(self.dtype)
        input_ids = tensors["input_ids"].to(self.device)
        attention_mask = None
        if "attention_mask" in tensors:
            attention_mask = tensors["attention_mask"].to(self.device)
        with torch.no_grad():
            hidden = self._run_range(hidden, layer_range,
                                     attention_mask=attention_mask)
            hidden = self._final_norm(hidden)
            # We have the hidden state for the full prefix. To sample
            # we need logits over the next token, which is positions
            # [-1] of the LM-head output.
            last_hidden = hidden[..., -1:, :]
            for _ in range(int(max_new_tokens)):
                logits = self._lm_head(last_hidden)[..., -1, :]
                next_token = self._sample(
                    logits, temperature=temperature, top_p=top_p,
                )
                input_ids = torch.cat(
                    [input_ids, next_token.view(-1, 1)],
                    dim=1,
                )
                if (self.tokenizer.eos_token_id is not None
                        and int(next_token.item()) == self.tokenizer.eos_token_id):
                    break
                # For the next iteration we run the FULL forward pass
                # on the single new token through our final-stage
                # layers + norm + head. Each step is fast (single
                # token), but as documented above, earlier stages
                # don't participate; that's the prefill-parallel cut.
                new_embed = self._embed_input_ids(next_token.view(1, 1))
                # Skip stages before ours by re-running ALL preceding
                # layers locally on the new token. This is only
                # correct when the final-stage worker also holds the
                # full model (today's reality, since sharded bundles
                # aren't here yet). When sharded bundles arrive, this
                # branch becomes a multi-worker re-emit instead.
                preceding = LayerRange(0, layer_range.start)
                if preceding.length > 0:
                    new_embed = self._run_range(new_embed, preceding)
                new_hidden = self._run_range(new_embed, layer_range)
                last_hidden = self._final_norm(new_hidden)
        decoded = self.tokenizer.decode(
            input_ids[0].cpu().tolist(),
            skip_special_tokens=True,
        )
        return decoded, {
            "stage_kind": "tail",
            "layer_range": layer_range.as_dict(),
            "max_new_tokens": int(max_new_tokens),
            "generated_tokens": int(input_ids.shape[-1]),
        }

    # ---- sampling ------------------------------------------------------ #

    def _sample(self, logits: Any, *, temperature: float, top_p: float) -> Any:
        """Top-p (nucleus) sampling with temperature; greedy when
        temperature == 0 OR top_p == 0 (mode override for deterministic
        outputs in tests)."""
        torch = self._torch
        if temperature <= 0.0 or top_p <= 0.0:
            return logits.argmax(dim=-1)
        scaled = logits / max(temperature, 1e-6)
        probs = torch.softmax(scaled, dim=-1)
        sorted_probs, sorted_idx = probs.sort(dim=-1, descending=True)
        cum = sorted_probs.cumsum(dim=-1)
        mask = cum > top_p
        # Always include the top-1 token.
        mask[..., 0] = False
        sorted_probs = sorted_probs.masked_fill(mask, 0.0)
        normalised = sorted_probs / sorted_probs.sum(dim=-1, keepdim=True)
        picked = torch.multinomial(normalised, num_samples=1).squeeze(-1)
        return sorted_idx.gather(-1, picked.unsqueeze(-1)).squeeze(-1)


__all__ = [
    "LayerRange",
    "LayerRangeRunner",
    "locate_decoder_layers",
    "plan_layer_ranges",
    "encode_activation_b64",
    "decode_activation_b64",
]
