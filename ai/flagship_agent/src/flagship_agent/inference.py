"""Local bundle inference runner.

Loads a flagship bundle from ``models/export/<run_id>/`` and runs generation
against it. Used by the ``local-flagship`` provider in agent_runtime when
the distributed AICF provider is unavailable.

Failure modes are surfaced as :class:`BundleError`. We never invent output
when the bundle can't load.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


@dataclass
class BundleManifest:
    schema: int
    run_id: str
    tier: str
    base_model: str
    effective_mode: str
    available_for_real_inference: bool
    artifacts: dict[str, str]
    extra: dict[str, Any]

    @classmethod
    def load(cls, path: Path) -> "BundleManifest":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            schema=int(data.get("schema", 1)),
            run_id=str(data.get("run_id", "")),
            tier=str(data.get("tier", "")),
            base_model=str(data.get("base_model", "")),
            effective_mode=str(data.get("effective_mode", "")),
            available_for_real_inference=bool(
                data.get("available_for_real_inference", False)),
            artifacts={k: str(v) for k, v in
                       (data.get("artifacts") or {}).items()},
            extra={k: v for k, v in data.items()
                    if k not in {"schema", "run_id", "tier", "base_model",
                                 "effective_mode",
                                 "available_for_real_inference", "artifacts"}},
        )


class LocalBundleRunner:
    """Generates text from a locally-installed bundle.

    Lazily loads transformers + torch on first generate() call. Importing
    this module never imports them — so agent_runtime can ``is_available()``
    check the bundle without paying torch import time on every chat startup.
    """

    def __init__(self, *, bundle_dir: Path,
                 inference_spec: Mapping[str, Any]) -> None:
        self.bundle_dir = Path(bundle_dir)
        self.inference_spec = dict(inference_spec)
        self._model = None
        self._tokenizer = None
        self._device = None

    def _load_lazy(self) -> None:
        if self._model is not None:
            return
        try:
            import torch                            # type: ignore
            from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore
        except Exception as exc:    # noqa: BLE001
            from agent_runtime.errors import BundleError
            raise BundleError(
                f"transformers/torch unavailable: {exc}",
                hint="install with: `pip install 'flagship_agent[inference]'`",
            ) from exc
        model_dir = self.bundle_dir / self.inference_spec.get(
            "model_subdir", "model")
        if not model_dir.is_dir():
            from agent_runtime.errors import BundleError
            raise BundleError(
                f"bundle model dir not found: {model_dir}",
            )
        torch_dtype = {
            "bf16": torch.bfloat16,
            "fp16": torch.float16,
            "fp32": torch.float32,
        }.get(str(self.inference_spec.get("precision", "fp32")),
              torch.float32)
        self._tokenizer = AutoTokenizer.from_pretrained(
            str(model_dir), trust_remote_code=bool(
                self.inference_spec.get("trust_remote_code", False)),
        )
        self._model = AutoModelForCausalLM.from_pretrained(
            str(model_dir),
            torch_dtype=torch_dtype,
            trust_remote_code=bool(
                self.inference_spec.get("trust_remote_code", False)),
        )
        device = "cuda" if torch.cuda.is_available() else (
            "mps" if (hasattr(torch.backends, "mps") and
                       torch.backends.mps.is_available()) else "cpu")
        self._model.to(device).eval()
        self._device = device

    def generate(self, *, prompt: str, history: list[dict[str, str]],
                 max_output_tokens: int = 1024,
                 temperature: float = 0.2, top_p: float = 0.95,
                 on_chunk: Optional[Callable[[str, bool], None]] = None
                 ) -> str:
        self._load_lazy()
        import torch    # type: ignore
        chat_messages = list(history) + [{"role": "user", "content": prompt}]
        try:
            if hasattr(self._tokenizer, "apply_chat_template"):
                input_ids = self._tokenizer.apply_chat_template(
                    chat_messages, add_generation_prompt=True,
                    return_tensors="pt",
                ).to(self._device)
            else:
                text = "\n".join(f"{m['role']}: {m['content']}"
                                  for m in chat_messages) + "\nassistant: "
                input_ids = self._tokenizer(text,
                                             return_tensors="pt").input_ids.to(
                    self._device)
        except Exception as exc:    # noqa: BLE001
            from agent_runtime.errors import BundleError
            raise BundleError(
                f"failed to prepare input ids: {exc}",
            ) from exc

        accumulated_text = ""
        # Token-by-token generate so we can stream.
        with torch.no_grad():
            for _ in range(max_output_tokens):
                outputs = self._model.generate(
                    input_ids,
                    max_new_tokens=1,
                    do_sample=temperature > 0,
                    temperature=max(temperature, 1e-3),
                    top_p=top_p,
                    pad_token_id=getattr(self._tokenizer, "eos_token_id",
                                          None),
                )
                new_tokens = outputs[0, input_ids.shape[1]:]
                if new_tokens.numel() == 0:
                    break
                new_text = self._tokenizer.decode(
                    new_tokens, skip_special_tokens=True)
                accumulated_text += new_text
                if on_chunk is not None:
                    on_chunk(new_text, False)
                input_ids = outputs
                eos = getattr(self._tokenizer, "eos_token_id", None)
                if eos is not None and int(outputs[0, -1].item()) == eos:
                    break
        if on_chunk is not None:
            on_chunk("", True)
        return accumulated_text
