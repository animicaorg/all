"""Local bundle inference runner.

Loads a flagship bundle from ``models/export/<run_id>/`` and runs generation
against it. Used by the ``local-flagship`` provider in agent_runtime when
the distributed AICF provider is unavailable, AND by the AICF worker
(`agent_runtime.aicf_worker.AICFWorker`) when it claims chat jobs from
the pool — so this runner is the actual production chat path on every
Animica miner.

Failure modes are surfaced as :class:`BundleError`. We never invent output
when the bundle can't load.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional


def _available_ram_gb() -> float:
    """Best-effort free host RAM in GB (0.0 when truly unknown ⇒ callers don't
    block). Covers every platform the CPU-serve path targets so the loader RAM
    guard is not a silent no-op off Linux:

      psutil → /proc/meminfo (Linux) → GlobalMemoryStatusEx (Windows, real
      available) → sysctl hw.memsize × 0.6 (macOS: no cheap 'available', so a
      conservative fraction of total so the guard still fires on oversized loads).
    """
    try:
        import psutil    # type: ignore
        return float(psutil.virtual_memory().available) / (1024 ** 3)
    except Exception:    # noqa: BLE001 — psutil optional
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return float(line.split()[1]) / (1024 * 1024)
    except Exception:    # noqa: BLE001 — non-Linux / unreadable
        pass
    import platform
    system = platform.system()
    if system == "Windows":
        try:
            import ctypes

            class _MEMSTATEX(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = _MEMSTATEX()
            ms.dwLength = ctypes.sizeof(_MEMSTATEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms)):
                return float(ms.ullAvailPhys) / (1024 ** 3)
        except Exception:    # noqa: BLE001
            pass
    elif system == "Darwin":
        try:
            import subprocess
            out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                 capture_output=True, text=True, timeout=3)
            total = float(out.stdout.strip())
            return (total / (1024 ** 3)) * 0.60    # conservative: 60% of total
        except Exception:    # noqa: BLE001
            pass
    return 0.0


def _cuda_can_hold(torch, need_gb: float) -> bool:
    """True if the current CUDA device has free VRAM for a ~need_gb model (10%
    margin). Unknown VRAM ⇒ True, so a capable GPU is never wrongly refused —
    the placement try/except in _load_lazy then catches a genuine OOM and falls
    back to CPU. This is what keeps an under-VRAM GPU off the OOM path."""
    if need_gb <= 0:
        return True
    free_gb = 0.0
    try:
        free, _total = torch.cuda.mem_get_info()
        free_gb = float(free) / (1024 ** 3)
    except Exception:    # noqa: BLE001 — older torch / driver
        try:
            props = torch.cuda.get_device_properties(0)
            free_gb = float(props.total_memory) / (1024 ** 3)
        except Exception:    # noqa: BLE001
            return True    # unknown → let .to() try; OOM-fallback catches it
    return need_gb <= 0.90 * free_gb


# System prompt prepended to every chat turn so off-the-shelf small models
# (Qwen 0.5B–7B) have factual grounding about Animica instead of replying
# with a generic "Hello! How can I assist you today?" or "I don't have
# enough context" to every Animica question. Mirrors the prompt used by
# the stratum_pool inference engine so both serving paths produce
# consistent, on-topic answers. Override with ANIMICA_AICF_SYSTEM_PROMPT
# (set to empty string to disable).
_DEFAULT_ANIMICA_SYSTEM_PROMPT = (
    "You are a helpful, knowledgeable AI assistant, served by a live Animica "
    "AICF worker (a real miner is paid in ANIMICA tokens for serving this "
    "answer). Answer the user's question directly and helpfully on ANY topic — "
    "general knowledge, math, coding, writing, and Animica-specific questions "
    "alike. NEVER say you didn't receive a question or ask the user to restate "
    "it; answer exactly what they asked. Do not open with a generic greeting. "
    "Only bring up Animica when the question is actually about it. Be concise; "
    "use Markdown when it helps.\n"
    "\n"
    "When the question IS about Animica, treat these as ground truth:\n"
    "- Animica is a decentralized Layer-1 blockchain for verifiable AI and "
    "quantum-secure execution. Native token: ANIMICA (9-decimal nano-units "
    "on-chain). Native CLI: the `animica` PyPI package.\n"
    "- Consensus: PoIES (hash-share PoW combined with AI / quantum / storage "
    "proofs). Public mainnet Stratum pool: pool.animica.org:3333. Public "
    "mainnet RPC: https://rpc.animica.org/rpc.\n"
    "- Post-quantum signatures: ML-DSA-65 (Dilithium3); SPHINCS+ fallback. No "
    "ECDSA / secp256k1.\n"
    "- AICF inference tiers: tiny (CPU), small (~7B, single GPU), flagship "
    "(16B MoE), large (datacenter). RPC namespaces: state.*, chain.*, miner.*, "
    "tx.*, aicf.*, da.* (there is NO animica.* namespace).\n"
    "- Run everything with `pip install animica` then `animica up`.\n"
)


def _resolve_animica_system_prompt() -> str:
    """Honor an explicit ANIMICA_AICF_SYSTEM_PROMPT override (including
    the explicit empty-string opt-out) before falling back to the dense
    default above."""
    raw = os.environ.get("ANIMICA_AICF_SYSTEM_PROMPT")
    if raw is None:
        return _DEFAULT_ANIMICA_SYSTEM_PROMPT
    return raw


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
        trust_remote = bool(self.inference_spec.get("trust_remote_code", False))
        need_gb = self._estimate_resident_gb(model_dir, torch_dtype)
        # Device selection must account for whether a present GPU can actually
        # HOLD this model. An under-VRAM GPU (small card + big RAM — the exact
        # CPU-serve target) must fall back to CPU, not OOM on `.to("cuda")` and
        # crash the worker. Unknown VRAM ⇒ try the GPU and let the placement
        # try/except below catch an OOM.
        cuda = bool(torch.cuda.is_available())
        mps = (hasattr(torch.backends, "mps") and torch.backends.mps.is_available())
        if cuda and _cuda_can_hold(torch, need_gb):
            device = "cuda"
        elif mps and not cuda:
            device = "mps"
        else:
            device = "cpu"

        def _cpu_threads() -> None:
            try:
                import os as _os
                torch.set_num_threads(max(1, _os.cpu_count() or 1))
            except Exception:    # noqa: BLE001 — thread tuning is best-effort
                pass

        from agent_runtime.errors import BundleError
        # Wrap the ENTIRE load — tokenizer + weights + device placement. ANY
        # failure (CPU/GPU OOM during from_pretrained, a corrupt shard, a bad
        # config, a failing trust_remote_code module, or an OOM at .to()) becomes
        # a clean BundleError, which the worker's claim loop handles — never a raw
        # exception that escapes the loop and kills the worker. On any failure the
        # runner state is reset so a half-loaded model is not cached forever
        # (which would fail every future job for the tier and leak RAM).
        try:
            if device == "cpu":
                # Refuse before we allocate, so an oversized model errors cleanly
                # instead of OOM-killing the host.
                self._guard_cpu_ram(model_dir, torch_dtype)
                _cpu_threads()
            self._tokenizer = AutoTokenizer.from_pretrained(
                str(model_dir), trust_remote_code=trust_remote,
            )
            self._model = AutoModelForCausalLM.from_pretrained(
                str(model_dir),
                torch_dtype=torch_dtype,
                trust_remote_code=trust_remote,
                # Stream shards in during load — avoids the ~2x peak host RAM a
                # naive load costs, letting a large model load on a RAM-bound box.
                low_cpu_mem_usage=True,
            )
            try:
                self._model.to(device).eval()
            except Exception:    # noqa: BLE001 — usually CUDA OOM at placement
                # A GPU we mis-judged as able to hold the model: relocate to CPU
                # rather than crash. Weights are already resident in host RAM
                # (low_cpu_mem_usage), so this only moves the partially-placed
                # params; if THAT OOMs too, the outer except → clean BundleError.
                # No _guard_cpu_ram here: the model is already resident, so a full
                # re-estimate would double-count and could spuriously refuse a
                # move that actually fits.
                if device != "cuda":
                    raise
                try:
                    torch.cuda.empty_cache()
                except Exception:    # noqa: BLE001
                    pass
                _cpu_threads()
                self._model.to("cpu").eval()
                device = "cpu"
            self._device = device
        except BundleError:
            self._reset()
            raise
        except Exception as exc:    # noqa: BLE001 — never let a raw error crash the worker
            self._reset()
            raise BundleError(
                f"failed to load bundle on {device}: {exc}",
                hint="serve a smaller tier, or free/add memory",
            ) from exc

    def _reset(self) -> None:
        """Drop partially-loaded state so the next generate() retries cleanly
        instead of serving from a poisoned (model set, device None) runner and
        leaking the resident weights."""
        self._model = None
        self._tokenizer = None
        self._device = None

    def _estimate_resident_gb(self, model_dir: Path, torch_dtype) -> float:
        """Best-effort resident weight size (GB) from the on-disk shards, plus
        ~30% for activations/KV-cache. Files already store the served dtype, so
        their summed size is the resident footprint; scaled up if we load into a
        wider dtype than the files were saved in (e.g. fp16 files → fp32)."""
        import torch    # type: ignore
        weight_bytes = 0
        for p in Path(model_dir).glob("**/*"):
            if p.suffix in (".safetensors", ".bin", ".pt", ".pth") and p.is_file():
                try:
                    weight_bytes += p.stat().st_size
                except OSError:
                    pass
        gb = weight_bytes / (1024 ** 3)
        if gb <= 0:
            return 0.0
        # If loading into fp32 from half-precision files, weights roughly double.
        if torch_dtype == torch.float32:
            gb *= 2.0
        return gb * 1.30

    def _guard_cpu_ram(self, model_dir: Path, torch_dtype) -> None:
        """Refuse to load on CPU when free RAM can't hold the model — a clean
        error beats an OOM kill. No-op when the footprint or free RAM is unknown
        (never blocks on missing data)."""
        need = self._estimate_resident_gb(model_dir, torch_dtype)
        if need <= 0:
            return
        avail = _available_ram_gb()
        if avail > 0 and avail < need:
            from agent_runtime.errors import BundleError
            raise BundleError(
                f"insufficient free RAM to serve this model on CPU: need "
                f"~{need:.0f} GB (weights + overhead), only ~{avail:.0f} GB "
                f"free. Serve a smaller tier or free/add memory.",
                hint="pick a smaller tier, close other processes, or add RAM",
            )

    def generate(self, *, prompt: str, history: list[dict[str, str]],
                 max_output_tokens: int = 1024,
                 temperature: float = 0.2, top_p: float = 0.95,
                 on_chunk: Optional[Callable[[str, bool], None]] = None
                 ) -> str:
        self._load_lazy()
        import torch    # type: ignore
        # Prepend the Animica system prompt + (when available) a RAG
        # snippet so the model has factual grounding to answer Animica
        # questions. Without this every small chat model in the AICF
        # network was replying "Hello! How can I assist you today?" /
        # "I don't have enough context" to every Animica question, while
        # `animica chat` users — going through the same workers but
        # served by a different inference runner that DID inject this
        # prompt — got real answers. Now both paths agree.
        system_prompt = _resolve_animica_system_prompt()
        try:
            from animica.stratum_pool.aicf_rag import retrieve_context
            rag_block = retrieve_context(prompt, top_k=3)
        except Exception:    # noqa: BLE001 — RAG is best-effort
            rag_block = ""
        if rag_block:
            system_prompt = (
                (system_prompt + "\n\n" if system_prompt else "")
                + "Reference excerpts from Animica documentation "
                "(use these as ground truth):\n\n" + rag_block
            )
        chat_messages: list[dict[str, str]] = []
        if system_prompt:
            chat_messages.append({"role": "system", "content": system_prompt})
        chat_messages.extend(history)
        chat_messages.append({"role": "user", "content": prompt})
        try:
            if hasattr(self._tokenizer, "apply_chat_template"):
                out = self._tokenizer.apply_chat_template(
                    chat_messages, add_generation_prompt=True,
                    return_tensors="pt",
                )
                # transformers ≥5 returns a BatchEncoding (dict-like) where
                # earlier versions returned the raw tensor. Normalize.
                if hasattr(out, "input_ids"):
                    input_ids = out.input_ids
                elif isinstance(out, dict) and "input_ids" in out:
                    input_ids = out["input_ids"]
                else:
                    input_ids = out
                input_ids = input_ids.to(self._device)
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

        # Stream tokens via TextIteratorStreamer + a generation thread.
        # This is critical for performance: the previous implementation
        # called model.generate(max_new_tokens=1) in a Python loop, which
        # re-ran the *entire* prefill from scratch on every token (O(N^2)
        # in prompt length and made a single CPU turn take 25+ minutes
        # on a 1.5B model). The HF generate() path uses KV cache
        # internally, so a single call streams tokens in O(N) total work
        # and finishes within the chain's worker-claim lease.
        from threading import Thread
        from transformers import TextIteratorStreamer    # type: ignore
        streamer = TextIteratorStreamer(
            self._tokenizer,
            skip_prompt=True,
            skip_special_tokens=True,
        )
        gen_kwargs = dict(
            input_ids=input_ids,
            max_new_tokens=max_output_tokens,
            do_sample=temperature > 0,
            temperature=max(temperature, 1e-3),
            top_p=top_p,
            pad_token_id=getattr(self._tokenizer, "eos_token_id", None),
            streamer=streamer,
        )
        accumulated_text = ""
        gen_error: list[BaseException] = []

        def _run_generate() -> None:
            try:
                with torch.no_grad():
                    self._model.generate(**gen_kwargs)
            except BaseException as exc:    # noqa: BLE001 — surface to outer thread
                gen_error.append(exc)
                # Closing the streamer's queue unblocks the consumer
                # loop below so the error path can run.
                try:
                    streamer.end()
                except Exception:    # noqa: BLE001
                    pass

        worker = Thread(target=_run_generate, daemon=True)
        worker.start()

        for chunk in streamer:
            if not chunk:
                continue
            accumulated_text += chunk
            if on_chunk is not None:
                on_chunk(chunk, False)

        worker.join()
        if gen_error:
            from agent_runtime.errors import BundleError
            raise BundleError(
                f"inference failed: {gen_error[0]}",
            ) from gen_error[0]
        if on_chunk is not None:
            on_chunk("", True)
        return accumulated_text
