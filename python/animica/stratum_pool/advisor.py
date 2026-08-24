"""animica.stratum_pool.advisor
================================

Backend for the site-wide "Animica setup advisor" chat widget. Given a
description of the visitor's hardware it recommends the exact ``animica up``
command for that box, and answers follow-up questions.

Design: the *recommendation itself* is DETERMINISTIC — ``recommend()`` encodes
the same tier/eligibility rules the client ships (see agent_runtime.hardware),
so the widget always returns a correct command even when network inference is
cold. On top of that, ``chat()`` does exhaustive RAG over the Animica docs and
runs the answer through Animica's own AI network (``animica-chat-flagship`` on
animica.dev/v1), with the deterministic recommendation injected as ground truth
so the model phrases/explains but never invents a wrong command. If the network
is unavailable the deterministic answer is returned verbatim — the widget never
dead-ends.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

try:  # earnings is best-effort: a dead price feed must not break advice
    from . import earnings as _earn
except Exception:  # pragma: no cover
    _earn = None  # type: ignore

# Canonical foundation treasury (consensus/rewards.py). Offered as the payout
# target for operators who want their node's earnings to go to the project.
FOUNDATION_TREASURY = (
    "anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga"
)

# Network inference endpoint (Animica's own AI network, keyless).
_AI_ENDPOINT = os.environ.get(
    "ANIMICA_ADVISOR_AI_ENDPOINT", "https://animica.dev/v1/chat/completions")
# Models tried in order until one answers — flagship first for quality, then the
# smaller/faster tiers (which have more serving capacity, so the advisor still
# gets a live AI answer when flagship has no worker). Grounded by the
# deterministic engine, so a small model is perfectly adequate here.
_AI_MODELS = [m.strip() for m in os.environ.get(
    "ANIMICA_ADVISOR_AI_MODELS",
    # Small/tiny FIRST: it's fast and has the most serving capacity, so the
    # advisor actually lands a live AI answer; flagship is the last fallback
    # (higher quality but slower + scarcer workers). The deterministic engine is
    # the quality floor either way, so a small model is ideal here.
    "animica-chat-small,animica-chat,kimi-k3,animica-chat-flagship"
).split(",") if m.strip()]

# Tier thresholds — kept in lockstep with ai/configs/model_catalog.yaml and
# agent_runtime.hardware.eligible_tiers. (id, min_vram_gb, min_ram_gb,
# total_params_b, precision, blurb).
_TIERS = [
    ("tiny", 0, 8, 1.5, "fp32", "1.5B, CPU-native — the guaranteed floor"),
    ("small", 16, 16, 7.0, "bf16", "7B dense — everyday single-GPU workhorse"),
    ("flagship", 24, 32, 16.0, "bf16",
     "16B MoE (2.4B active) — headline quality, CPU-viable on big RAM"),
    ("large", 80, 128, 236.0, "bf16", "236B MoE — datacenter GPUs only"),
]
_PRECISION_BYTES = {"bf16": 2.0, "fp16": 2.0, "fp32": 4.0, "int8": 1.0, "int4": 0.5}
_CPU_RESERVE_GB = 8.0
_CPU_MAX_FRACTION = 0.70


def _tier_cpu_ram_gb(total_params_b: float, precision: str) -> float:
    return total_params_b * _PRECISION_BYTES.get(precision, 4.0) * 1.25


# --------------------------------------------------------------------------- #
# Hardware parsing                                                            #
# --------------------------------------------------------------------------- #

def parse_hardware(text: str, explicit: Optional[Dict[str, Any]] = None,
                   *, latest: Optional[str] = None,
                   last_asked: Optional[str] = None) -> Dict[str, Any]:
    """Best-effort structured hardware from free text + any explicit fields.
    ``latest`` (the newest user message) + ``last_asked`` ('vram'|'ram'|'vendor')
    let a bare answer like "1024 gb" attach to the question just asked, so the
    conversation advances instead of looping. Never raises."""
    hw: Dict[str, Any] = {
        "gpu_vendor": None,      # nvidia | amd | apple | none
        "vram_gb": None,
        "ram_gb": None,
        "os": None,              # linux | mac | windows
        "donate_treasury": None,
        "has_gpu": None,         # True when a GPU is mentioned even w/o a vendor
        "gpu_count": None,       # rough number of GPUs
        "scale": None,           # "big" for a multi-GPU rig/cluster
        "raw": text or "",
    }
    if explicit:
        for k in ("gpu_vendor", "vram_gb", "ram_gb", "os", "donate_treasury", "address"):
            if explicit.get(k) not in (None, ""):
                hw[k] = explicit[k]
    t = (text or "").lower()

    if hw["gpu_vendor"] is None:
        if re.search(r"\b(nvidia|rtx|gtx|geforce|tesla|a100|h100|a40|a6000|l40|3090|4090|5090|quadro|cuda)\b", t):
            hw["gpu_vendor"] = "nvidia"
        elif re.search(r"\b(apple|macbook|mac ?mini|mac ?studio|imac|m[1-4]\b|metal|mps)\b", t):
            hw["gpu_vendor"] = "apple"
        elif re.search(r"\b(amd|radeon|rocm|instinct|mi\d{2,3})\b", t):
            hw["gpu_vendor"] = "amd"
        elif re.search(r"\b(cpu[- ]?only|no gpu|without a gpu|headless|xeon|epyc|dell r\d|poweredge)\b", t):
            hw["gpu_vendor"] = "none"

    # GPU present even without a named vendor ("hundreds of gpus", "a few cards").
    if re.search(r"\b(gpus?|graphics? cards?|video cards?|accelerators?)\b", t) or \
            hw["gpu_vendor"] in ("nvidia", "amd", "apple"):
        hw["has_gpu"] = True
    elif hw["gpu_vendor"] == "none":
        hw["has_gpu"] = False

    # Scale / count — a multi-GPU rig gets very different advice.
    # "200 nvidia gpus" and "8x rtx 4090" both used to parse as ONE card, which
    # was harmless when the count only picked advice wording — it is not harmless
    # now that it multiplies a dollar figure. Allow a vendor/model word between
    # the number and the noun, and accept the bare "<n>x <model>" rig shorthand.
    # Allow GPU-ish filler between the count and the noun. Without the filler
    # "200 nvidia rtx 4090 gpus" matched at "4090 gpus" and priced a 4,090-card
    # rig; the filler is a bounded whitelist so it cannot swallow "512gb ram".
    _FILLER = (r"(?:(?:nvidia|amd|apple|radeon|geforce|tesla|rtx|gtx|rx|a100|h100"
               r"|l40|a6000|ti|super|\d{3,4})\s+){0,3}?")
    m = re.search(r"(\d{1,5})\s*(?:x\s*)?" + _FILLER +
                  r"(?:gpus?|cards?|accelerators?)\b", t)
    if not m:
        m = re.search(r"\b(\d{1,4})\s*x\s*"
                      r"(?:rtx|gtx|rx|a100|h100|l40|a6000|m[1-4]\b|\d{4})", t)
    if m:
        hw["gpu_count"] = int(m.group(1))
    if re.search(r"\b(hundreds?|thousands?|dozens?)\b.{0,20}\b(gpus?|cards?|rigs?)\b"
                 r"|\b(gpus?|cards?)\b.{0,20}\b(hundreds?|thousands?|dozens?)\b", t):
        hw["gpu_count"] = hw["gpu_count"] or 200
    if hw.get("gpu_count") and hw["gpu_count"] > 1 or \
            re.search(r"\b(rig|cluster|farm|datacent|data ?cent|many gpus?|multi[- ]?gpu|big rig|full rig|nodes?)\b", t):
        hw["scale"] = "big"
        hw["has_gpu"] = True if hw["has_gpu"] is None else hw["has_gpu"]

    if hw["os"] is None:
        if re.search(r"\b(macos|osx|macbook|imac|mac ?mini|mac ?studio|m[1-4]\b)\b", t):
            hw["os"] = "mac"
        elif re.search(r"\bwindows|win ?1[01]|wsl\b", t):
            hw["os"] = "windows"
        elif re.search(r"\blinux|ubuntu|debian|centos|fedora|proxmox\b", t):
            hw["os"] = "linux"

    if hw["vram_gb"] is None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*gb?\s*(?:of\s*)?vram", t) or \
            re.search(r"vram[^0-9]{0,6}(\d+(?:\.\d+)?)\s*gb", t)
        if m:
            hw["vram_gb"] = float(m.group(1))
    if hw["ram_gb"] is None:
        m = re.search(r"(\d+(?:\.\d+)?)\s*gb?\s*(?:of\s*)?(?:ram|memory|ecc)", t) or \
            re.search(r"(?:ram|memory)[^0-9]{0,6}(\d+(?:\.\d+)?)\s*gb", t)
        if m:
            hw["ram_gb"] = float(m.group(1))

    # Bare-number attribution: the user just answered a specific question with a
    # number (e.g. we asked RAM, they said "1024 gb"). Attach it to that field so
    # we stop re-asking. A number too large for any single GPU's VRAM is RAM.
    if last_asked and latest:
        bm = re.search(r"(\d+(?:\.\d+)?)\s*(?:gb|g|gigs?|tb)?\b", str(latest).strip().lower())
        if bm:
            val = float(bm.group(1))
            if re.search(r"\btb\b", str(latest).lower()):
                val *= 1024.0
            if last_asked == "ram" and hw["ram_gb"] is None:
                hw["ram_gb"] = val
            elif last_asked == "vram" and hw["vram_gb"] is None:
                if val > 192 and hw["ram_gb"] is None:
                    hw["ram_gb"] = val          # no single GPU has >192 GB VRAM
                else:
                    hw["vram_gb"] = val

    if hw["donate_treasury"] is None and re.search(r"\b(treasury|donate|foundation|to the project)\b", t):
        hw["donate_treasury"] = True
    return hw


# --------------------------------------------------------------------------- #
# Deterministic recommender                                                   #
# --------------------------------------------------------------------------- #

def _eligible_serve_tiers(hw: Dict[str, Any], cpu_serve: bool) -> List[str]:
    vram = float(hw.get("vram_gb") or 0)
    ram = float(hw.get("ram_gb") or 0)
    out: List[str] = []
    for (tid, min_vram, min_ram, params, prec, _blurb) in _TIERS:
        if ram and ram + 0.5 < min_ram:
            continue
        if min_vram == 0:
            out.append(tid)
        elif vram and vram + 0.5 >= min_vram:
            out.append(tid)
        elif cpu_serve and ram:
            need = _tier_cpu_ram_gb(params, prec)
            if ram - need >= _CPU_RESERVE_GB and need <= _CPU_MAX_FRACTION * ram:
                out.append(tid)
    return out or (["tiny"] if ram else [])



# --------------------------------------------------------------------------- #
# earnings                                                                     #
# --------------------------------------------------------------------------- #

# When the visitor names no specific model we still owe them a number, so each
# vendor gets a deliberately MID-RANGE stand-in rather than a halo part — an
# estimate that flatters is worse than no estimate.
_VENDOR_DEFAULT = {"nvidia": "rtx3060", "amd": "rx6800xt", "apple": "m3pro"}


def _cpu_device(hw: Dict[str, Any]):
    """A CPU row sized to the core count the visitor mentioned."""
    if _earn is None:
        return None
    cores = hw.get("cpu_cores")
    if not cores:
        m = re.search(r"(\d{1,3})\s*(?:-|\s)?(?:core|cores|vcpu|threads?)\b",
                      (hw.get("raw") or "").lower())
        cores = int(m.group(1)) if m else 8
    cores = max(1, min(int(cores), 256))
    base = _earn.DEVICES["cpu_core"]
    return _earn.Device("cpu_n", f"{cores}-core CPU", base.sha3_mhs * cores,
                        max(25.0, 7.0 * cores), "measured",
                        f"{cores} x measured single-core rate")


def _earnings_for(hw: Dict[str, Any]) -> Optional[Tuple[Any, Any]]:
    """(Device, Estimate) for the described hardware, or None.

    Never raises — the advisor answers with or without a dollar figure.
    """
    if _earn is None:
        return None
    try:
        raw = hw.get("raw") or ""
        vendor = hw.get("gpu_vendor")
        count = int(hw.get("gpu_count") or 1)

        dev = _earn.match_device(raw)
        if dev is None:
            if vendor == "none" or vendor is None and hw.get("has_gpu") is False:
                dev = _cpu_device(hw)
            elif vendor in _VENDOR_DEFAULT:
                dev = _earn.DEVICES[_VENDOR_DEFAULT[vendor]]
        if dev is None:
            return None
        est = _earn.estimate(dev, count=max(1, count))
        return dev, est
    except Exception:
        return None

def recommend(hw: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic recommendation. Returns {command, env, flags, tiers,
    mine, clore, notes, warnings, missing}."""
    vendor = hw.get("gpu_vendor")
    vram = float(hw.get("vram_gb") or 0)
    ram = float(hw.get("ram_gb") or 0)
    os_name = hw.get("os")
    donate = bool(hw.get("donate_treasury"))
    address = hw.get("address")

    nvidia = vendor == "nvidia"
    vendor_known = vendor is not None
    gpu_count = int(hw.get("gpu_count") or 0)
    is_rig = hw.get("scale") == "big" or gpu_count > 1

    # Ask ONE thing at a time so a bare "1024 gb" answer attaches cleanly. NVIDIA
    # needs only the vendor — `animica up` auto-detects each card's VRAM and picks
    # tiers per machine; only the CPU-only path needs RAM (for the CPU serve tier).
    missing: List[str] = []
    if not vendor_known:
        missing.append("Is it NVIDIA GPUs, AMD, an Apple-Silicon Mac, or CPU-only?")
    elif vendor == "none" and not ram:
        missing.append("How much system RAM does it have (GB)?")

    env: Dict[str, str] = {}
    flags: List[str] = []
    notes: List[str] = []
    warnings: List[str] = []
    mine: Optional[bool] = None
    clore = False
    cpu_serve = False
    tiers: List[str] = []

    # --- mining: decided by ARITHMETIC, not by vendor reputation ---
    #
    # This used to be a vendor lookup that asserted "NVIDIA competitive / AMD
    # poor / Apple unproductive / CPU loses money". On a ~48 MH/s network those
    # last three are simply false: an RX 7900 XTX and an M3 Max each clear ~$14
    # a day net, and a 16-core CPU clears ~$1.87. Whether to mine is now
    # whichever way the live numbers point, and the number is shown.
    earn = _earnings_for(hw) if vendor_known else None
    est = earn[1] if earn else None

    if est is not None:
        mine = est.mining_net_usd_day > 0
        if mine:
            notes.append(
                f"Mining pays here: **{_earn.summary_line(est)}** "
                f"(your ~{est.device_mhs:,.0f} MH/s vs a {est.network_mhs:,.1f} MH/s network)."
            )
        else:
            flags.append("--without miner")
            notes.append(
                f"Mining would cost more in power than it earns for this box "
                f"(≈${est.mining_usd_day:.3f}/day mined vs ${est.power_usd_day:.2f}/day of "
                f"electricity) — serve inference instead."
            )
    else:
        # No usable estimate (unknown device, price feed down). Keep the old
        # vendor heuristic rather than guessing a number.
        if nvidia:
            mine = True
            notes.append("NVIDIA GPUs mine ANM competitively — keep the miner on.")
        elif vendor in ("apple", "amd", "none"):
            mine = False
            flags.append("--without miner")
            notes.append("Serving inference is the better use of this hardware.")

    # --- Clore (NVIDIA + Linux only) ---
    if nvidia:
        clore = os_name in ("linux", None)
        if os_name in ("mac", "windows"):
            warnings.append("Clore GPU rental needs Linux — on this OS you'll mine + serve but not rent on Clore.")
        elif clore:
            notes.append("Say yes to Clore when `animica up` asks — real dollars for idle GPU time, you keep 90% (paid in ANM).")

    # --- serve tiers ---
    if vendor_known:
        if not nvidia and vendor != "apple" and ram >= 40:
            cpu_serve = True
            env["ANIMICA_AICF_CPU_SERVE"] = "1"
        if nvidia and not vram:
            # No exact VRAM yet — `animica up` auto-detects per card. Don't guess.
            notes.append("Each GPU serves the model tiers its VRAM supports — `animica up` detects that automatically.")
        else:
            tiers = _eligible_serve_tiers(hw, cpu_serve)
            if cpu_serve and "flagship" in tiers:
                notes.append("That RAM lets it serve the MoE flagship on CPU — slow, but real ~16B-class quality.")

    # --- scale ---
    if is_rig and vendor_known:
        n = f"~{gpu_count}" if gpu_count > 1 else "many"
        notes.append(f"With {n} GPUs this is a serious operation: run **one `animica up` per machine** "
                     "(they all point at the same pool + payout address). Use a payout wallet you control.")

    # --- payout address ---
    if donate:
        address = FOUNDATION_TREASURY
        notes.append("Earnings routed to the foundation treasury.")
    elif not address and vendor_known:
        notes.append("No address given → `animica up` auto-creates a wallet here (back it up), or pass `--address <your anim1…>`.")

    # --- assemble command ---
    parts: List[str] = []
    for k, v in env.items():
        parts.append(f"{k}={v}")
    cmd = " ".join(parts)
    cmd = (cmd + " " if cmd else "") + "animica up"
    if flags:
        cmd += " " + " ".join(flags)
    if address:
        cmd += f" \\\n  --address {address}"

    return {
        "command": f"pip install -U animica\n\n{cmd}",
        "env": env, "flags": flags, "tiers": tiers,
        "mine": mine, "clore": clore,
        "notes": notes, "warnings": warnings, "missing": missing,
        "hardware": hw,
        "earnings": ({
            "device": est.device,
            "device_mhs": round(est.device_mhs, 1),
            "network_mhs": round(est.network_mhs, 2),
            "share_pct": round(est.share * 100, 2),
            "anm_usd": est.anm_usd,
            "mining_anm_day": round(est.mining_anm_day, 1),
            "mining_usd_day": round(est.mining_usd_day, 4),
            "power_usd_day": round(est.power_usd_day, 4),
            "mining_net_usd_day": round(est.mining_net_usd_day, 4),
            "serving_anm_day": round(est.serving_anm_day, 1),
            "serving_usd_day": round(est.serving_usd_day, 4),
            "provenance": est.provenance,
            "best_path": _earn.best_path(est),
            # The ceiling, so the model can never imply unbounded upside.
            "max_network_mining_anm_day": _earn.MAX_MINING_ANM_DAY,
            "max_network_serving_anm_day": _earn.MAX_SERVING_ANM_DAY,
            "max_network_total_usd_day": round(
                _earn.MAX_TOTAL_ANM_DAY * est.anm_usd, 2),
            "text": _earn.format_estimate(est),
        } if est is not None else None),
    }


# --------------------------------------------------------------------------- #
# RAG + network LLM                                                           #
# --------------------------------------------------------------------------- #

def _rag_context(query: str, *, top_k: int = 10, max_chars: int = 6000) -> str:
    try:
        from animica.stratum_pool.aicf_rag import retrieve_context
        return retrieve_context(query, top_k=top_k, max_chars=max_chars)
    except Exception:    # noqa: BLE001 — RAG best-effort
        return ""


_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think(s: str) -> str:
    return _THINK_RE.sub("", s or "").strip()


def _system_prompt(rec: Dict[str, Any], rag: str) -> str:
    rec_json = json.dumps({
        "recommended_command": rec["command"],
        "serve_tiers": rec["tiers"], "mining": rec["mine"], "clore": rec["clore"],
        "notes": rec["notes"], "warnings": rec["warnings"],
        "still_needed_from_user": rec["missing"],
        "earnings": rec.get("earnings"),
    }, indent=2)
    return (
        "You are the Animica setup advisor, a concise, honest expert on running "
        "`animica up` (Animica's unified mine + serve-inference + GPU-rent client). "
        "Help the visitor find the exact command for THEIR hardware.\n\n"
        "GROUND TRUTH — a deterministic engine already computed the correct "
        "recommendation for the hardware described so far. NEVER contradict its "
        "`recommended_command`; present it, and explain it. If "
        "`still_needed_from_user` is non-empty, ask those follow-up questions "
        "FIRST (briefly) before giving a final command, since the command may "
        "change once you know them.\n\n"
        f"ENGINE OUTPUT:\n{rec_json}\n\n"
        "Hard facts you must respect: Clore rental is NVIDIA+Linux only (never on "
        "Mac/AMD/CPU). A big-RAM CPU box can serve the MoE flagship on CPU (slow). "
        "Amounts are ANM.\n\n"
        "EARNINGS: if ENGINE OUTPUT has an `earnings` object, those numbers are "
        "computed from the live NonKYC ANM price, the live network hashrate and the "
        "measured block interval. Quote them EXACTLY — never round them up, never "
        "invent a figure, and never give an earnings number when `earnings` is null. "
        "The emission is capped: 150 ANM/block mining + 75 ANM/block serving, so no "
        "amount of hardware earns more than the whole network emits. Say that plainly "
        "rather than implying unbounded upside.\n\n"
        + (f"REFERENCE DOCS (ground truth, cite when relevant):\n{rag}\n\n" if rag else "")
        + "RULES: Sound like a friendly human, not a form. OPEN by acknowledging "
        "their SPECIFIC hardware in one short line (e.g. 'A rig with 200 NVIDIA "
        "GPUs — serious firepower' or 'A 1 TB CPU box, nice'). You already have "
        "their hardware and the correct command — NEVER ask them to rephrase or "
        "say you need more info (unless `still_needed_from_user` is non-empty, in "
        "which case ask exactly ONE of those, conversationally). ALWAYS include "
        "the exact `recommended_command` verbatim in a fenced ```bash block``` "
        "(unless you're still asking a question). Keep it short and warm."
    )


# Markers of a stub / grace-fallback / refusal reply — the AICF network returns
# canned "how can I assist" text when a tier is served by the stub bridge rather
# than a real model. Showing these is worse than the deterministic answer, so we
# reject them and fall back.
_STUB_MARKERS = (
    "how can i assist", "how may i assist", "didn't receive", "did not receive",
    "please rephrase", "i need a specific", "i don't have enough",
    "i need more info", "could you please", "i cannot assist",
    "i'm not able to help", "as an ai language model", "i don't understand",
    "provide more details", "provide your question", "your question or",
)


def _reply_is_useful(text: str, rec: Dict[str, Any]) -> bool:
    """True only if the reply is a real, on-topic answer (not a stub/refusal).
    The network currently serves some tiers via a stub that emits canned
    'how can I assist' text; those must fall back to the deterministic answer."""
    if not text or len(text.strip()) < 40:
        return False
    low = text.lower()
    if any(m in low for m in _STUB_MARKERS):
        return False
    if "animica up" in low:
        return True    # it produced the command itself → genuinely engaged
    # Otherwise require it to actually engage with the setup topic.
    return any(w in low for w in (
        "animica", "gpu", "cpu", "vram", " ram", "mining", "serve",
        "tier", "clore", "flagship", "inference"))


def warm_rag() -> None:
    """Load the RAG encoder/index once (slow first load) so the first real
    advisor answer isn't stuck behind a ~12s cold start. Best-effort."""
    try:
        _rag_context("animica up mining inference command", top_k=1, max_chars=200)
    except Exception:    # noqa: BLE001
        pass


def _try_model(model: str, messages: List[Dict[str, str]], *, timeout: float,
               max_tokens: int = 450) -> Optional[str]:
    """One non-streaming call to a single model. None on any failure."""
    body = json.dumps({
        "model": model, "messages": messages,
        "max_tokens": max_tokens, "temperature": 0.3, "stream": False,
    }).encode()
    req = urllib.request.Request(
        _AI_ENDPOINT, data=body,
        headers={"content-type": "application/json",
                 "user-agent": "animica-advisor/1.0"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        txt = _strip_think((data.get("choices") or [{}])[0]
                           .get("message", {}).get("content") or "")
        return txt or None
    except Exception:    # noqa: BLE001 — this model unavailable; try the next
        return None


# A model's FIRST call cold-starts a worker (load weights → generate), which can
# take 30–120s. We must not make a user wait for that. Instead a background loop
# keeps a worker warm and records which model is live; user requests hit the warm
# worker with a generous-but-bounded timeout. A circuit breaker keeps the
# user path instant while the network is fully down.
_BREAKER = {"open_until": 0.0}
_BREAKER_COOLDOWN = 60.0
_LIVE = {"model": None}          # a model confirmed serving by the warm loop
# 110s (was 40): phone/CPU AICF workers legitimately take 1-2 min end-to-end, and
# the nginx route allows 120s. The deterministic core still answers instantly when
# the AI network is slow — this only widens how long we give a REAL model answer.
_USER_TIMEOUT = float(os.environ.get("ANIMICA_ADVISOR_USER_TIMEOUT", "110"))
_WARM_TIMEOUT = float(os.environ.get("ANIMICA_ADVISOR_WARM_TIMEOUT", "150"))


def _call_network_llm(messages: List[Dict[str, str]]) -> Optional[str]:
    """Ask the network, but ONLY a worker the warm loop already confirmed live —
    so the user never eats a cold start (that wait lives in _warm_loop). Returns
    the answer or None (→ deterministic fallback, instant). One bounded attempt
    against the warm worker; if it fails, open the breaker and let the warm loop
    re-find a live model."""
    import time
    now = time.monotonic()
    if now < _BREAKER["open_until"]:
        return None                          # network known-down → instant
    model = _LIVE["model"]
    if not model:
        return None                          # no confirmed-warm worker yet → instant
    txt = _try_model(model, messages, timeout=_USER_TIMEOUT)
    if txt:
        return txt
    _BREAKER["open_until"] = now + _BREAKER_COOLDOWN
    _LIVE["model"] = None                     # warm worker died → warm loop recovers
    return None


def _warm_loop() -> None:
    """Background: keep an AI worker warm so user requests don't eat the cold
    start. Pings each model with a tiny request and a LONG timeout (that's where
    the cold-start wait lives — off the user path); records the first live one
    and holds the breaker closed. Re-pings before a worker idles out."""
    import time
    while True:
        live = None
        for model in _AI_MODELS:
            if _try_model(model, [{"role": "user", "content": "ping"}],
                          timeout=_WARM_TIMEOUT, max_tokens=1):
                live = model
                break
        if live:
            _LIVE["model"] = live
            _BREAKER["open_until"] = 0.0
            time.sleep(70)                   # re-warm before a worker idles
        else:
            _LIVE["model"] = None
            _BREAKER["open_until"] = time.monotonic() + _BREAKER_COOLDOWN
            time.sleep(45)                   # retry warming sooner


def start_warm() -> None:
    """Start the background warm loop (idempotent)."""
    import threading
    if getattr(start_warm, "_started", False):
        return
    start_warm._started = True    # type: ignore[attr-defined]
    threading.Thread(target=_warm_loop, name="advisor-ai-warm", daemon=True).start()


def _ack(hw: Dict[str, Any]) -> str:
    """A short, human acknowledgment of what the visitor just described, so the
    reply feels like a reply — not a template."""
    vendor = hw.get("gpu_vendor")
    gc = int(hw.get("gpu_count") or 0)
    ram, vram = hw.get("ram_gb"), hw.get("vram_gb")
    bits: List[str] = []
    vlabel = {"nvidia": "NVIDIA ", "amd": "AMD ", "apple": "Apple ",
              "none": ""}.get(vendor or "", "")
    if hw.get("scale") == "big" or gc > 1:
        who = f"{gc} GPUs" if gc > 1 else "a multi-GPU rig"
        article = "An" if vlabel else "A"   # An NVIDIA/AMD/Apple; A (bare) rig
        bits.append(f"{article} {vlabel}rig with {who} — serious firepower. 🔥")
    elif vendor == "nvidia":
        bits.append("Nice, an NVIDIA setup.")
    elif vendor == "amd":
        bits.append("Got it — AMD.")
    elif vendor == "apple":
        bits.append("An Apple-Silicon Mac, got it.")
    elif vendor == "none":
        bits.append("A CPU-only box, got it.")
    extra = []
    if vram:
        extra.append(f"{vram:.0f} GB VRAM")
    if ram:
        extra.append(f"{ram:.0f} GB RAM")
    if extra:
        bits.append("(" + ", ".join(extra) + ").")
    return " ".join(bits).strip()


def _deterministic_answer(rec: Dict[str, Any]) -> str:
    """Human-readable, lightly personalized answer straight from the engine."""
    hw = rec.get("hardware") or {}
    ack = _ack(hw)
    # Still gathering → acknowledge + ask ONE question. No premature command.
    if rec["missing"]:
        parts: List[str] = []
        if ack:
            parts.append(ack)
        parts.append(rec["missing"][0])
        return "\n\n".join(parts)
    # Enough to answer → personalized command.
    lines: List[str] = []
    lines.append(ack or "Here's your command:")
    lines.append("")
    lines.append("```bash")
    lines.append(rec["command"])
    lines.append("```")
    if rec["tiers"]:
        lines.append(f"Serves these tiers: **{', '.join(rec['tiers'])}**.")
    for n in rec["notes"]:
        lines.append(f"- {n}")
    for w in rec["warnings"]:
        lines.append(f"- ⚠️ {w}")
    # Real money, live inputs. Shown whenever we could compute it — "what can I
    # earn" is the question behind most of these conversations.
    if rec.get("earnings"):
        lines.append("")
        lines.append("**What it earns**")
        lines.append("")
        lines.append(rec["earnings"]["text"])
    return "\n".join(lines)


def chat(messages: List[Dict[str, str]],
         explicit_hw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Main entry: full conversation in, {reply, command, hardware, used_ai} out.

    ``messages`` is the running OpenAI-style history (the widget remembers it), so
    hardware is accumulated across turns. Never raises.
    """
    start_warm()    # ensure the background keep-warm loop is running (idempotent)
    user_text = " \n".join(m.get("content", "") for m in messages
                           if m.get("role") == "user")
    latest = next((m.get("content", "") for m in reversed(messages)
                   if m.get("role") == "user"), "")
    # What did we last ASK? so a bare "1024 gb" answer attaches to that field and
    # the conversation advances instead of re-asking the same thing.
    last_assistant = next((m.get("content", "") for m in reversed(messages)
                           if m.get("role") == "assistant"), "").lower()
    last_asked = None
    if "vram" in last_assistant:
        last_asked = "vram"
    elif "ram" in last_assistant or "memory" in last_assistant:
        last_asked = "ram"
    elif any(w in last_assistant for w in ("nvidia", "cpu-only", "gpu", "amd", "apple")):
        last_asked = "vendor"

    hw = parse_hardware(user_text, explicit_hw, latest=latest, last_asked=last_asked)
    rec = recommend(hw)

    # Still missing a load-bearing fact → acknowledge + ask ONE question, instantly
    # (no RAG/network). Now that parsing attributes the answer, this advances each
    # turn instead of looping.
    if rec["missing"]:
        return {"reply": _deterministic_answer(rec), "command": rec["command"],
                "hardware": hw, "tiers": rec["tiers"], "used_ai": False,
                "earnings": rec.get("earnings"), "need_more": True}

    # Complete enough: enrich with exhaustive RAG + Animica's AI network, with
    # the deterministic recommendation as ground truth. Fall back to the
    # deterministic answer if the network is slow/unavailable.
    last_user = next((m.get("content", "") for m in reversed(messages)
                      if m.get("role") == "user"), "")
    rag = _rag_context((last_user + " " + user_text)[:600], top_k=10, max_chars=6000)
    sys_msg = {"role": "system", "content": _system_prompt(rec, rag)}
    convo = [sys_msg] + [m for m in messages if m.get("role") in ("user", "assistant")]
    ai = _call_network_llm(convo)
    if ai and _reply_is_useful(ai, rec):
        # Guarantee the correct command is present even if the model omitted it.
        if rec["command"] and "animica up" not in ai:
            ai = ai.rstrip() + "\n\nRecommended command:\n```bash\n" + \
                rec["command"] + "\n```"
        return {"reply": ai, "command": rec["command"], "hardware": hw,
                "tiers": rec["tiers"], "earnings": rec.get("earnings"),
                "used_ai": True}
    # AI unavailable or flaked → the always-correct deterministic answer.
    return {"reply": _deterministic_answer(rec), "command": rec["command"],
            "hardware": hw, "tiers": rec["tiers"],
            "earnings": rec.get("earnings"), "used_ai": False}


__all__ = ["chat", "recommend", "parse_hardware", "FOUNDATION_TREASURY"]
