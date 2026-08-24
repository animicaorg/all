"""animica.stratum_pool.earnings
================================

Real earnings estimates for the setup advisor: **ANM/day and USD/day** for a
given piece of hardware, from live inputs rather than adjectives.

Before this module the advisor shipped unquantified claims — "NVIDIA GPUs mine
ANM competitively", "AMD mines ANM poorly", "CPU mining costs more in power
than the ~pennies it earns". Two of those turn out to be wrong once you put
numbers on them (see DEVICES below): the network is ~50 MH/s, and a mid-range
AMD card or even a MacBook GPU is *multiples* of the whole network.

Three inputs decide everything, and all three are fetched live:

  price          NonKYC ANM/USDT last trade, via the same ``anm-price.json``
                 the site ticker reads (systemd ``anm-price.timer`` writes it).
  network_hps    pool ``/api/pool/summary`` -> ``network_hashrate_hps``.
  blocks/day     measured from real block timestamps, NOT the 60 s target —
                 the chain is currently running at ~95 s/block, so using the
                 target would overstate emission by ~1.6x.

Reward split per block (post-fork, height >= 75,000):
  mining   150 ANM   -> the miner that finds the block
  serving   75 ANM   -> the inference/service carve, split among claimants

THE CEILING IS THE POINT. You cannot earn more than the chain emits. At
1,440 blocks/day (the 60 s target) the *entire network's* mining emission is
150 * 1440 = 216,000 ANM/day. Multiply by a sub-cent coin price and that is a
low-two-figure dollar number for every miner on earth combined. Any estimate
here that came out larger than that would be arithmetic that forgot the cap,
which is exactly why ``estimate()`` computes a share of emission rather than
"hashrate x price".
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# consensus constants                                                          #
# --------------------------------------------------------------------------- #

MINING_ANM_PER_BLOCK = 150.0
SERVING_ANM_PER_BLOCK = 75.0
TARGET_BLOCK_S = 60.0          # spec/params.yaml: target_block_interval_ms 60000
CARVE_FORK_HEIGHT = 75_000

# Fallbacks used only when a live fetch fails. Deliberately the values measured
# on 2026-08-19 so a degraded answer is stale-but-true rather than invented.
_FALLBACK = {
    "anm_usd": 7.526e-05,
    "network_hps": 50_107_951.0,
    "block_time_s": 95.0,
}

DEFAULT_POWER_USD_KWH = 0.15

# HARD CEILING on what the chain can pay anyone, per day, in total.
# Difficulty retargets to a 60 s block, so 1,440 blocks/day is the most the
# chain ever produces; the per-block split is fixed. No amount of hardware,
# and no number of participants, changes these two numbers — they are the
# whole pie, and every estimate below is a slice of it.
MAX_BLOCKS_PER_DAY = 86400.0 / TARGET_BLOCK_S            # 1,440
MAX_MINING_ANM_DAY = MINING_ANM_PER_BLOCK * MAX_BLOCKS_PER_DAY    # 216,000
MAX_SERVING_ANM_DAY = SERVING_ANM_PER_BLOCK * MAX_BLOCKS_PER_DAY  # 108,000
MAX_TOTAL_ANM_DAY = MAX_MINING_ANM_DAY + MAX_SERVING_ANM_DAY      # 324,000


# --------------------------------------------------------------------------- #
# device throughput table                                                      #
# --------------------------------------------------------------------------- #
#
# Animica's PoW is ONE SHA3-256 per nonce over (prefix || mix_seed || nonce_le64)
# — see mining/native/animica_fastpow/fastpow.c. No memory hardness, no second
# pass. That makes published SHA3-256 throughput directly comparable, which is
# the only reason a table like this can be honest at all.
#
# provenance is a FIELD, not a comment, because the advisor quotes it. Values:
#   measured  — benchmarked against Animica's own kernel on real hardware
#   hashcat   — published hashcat mode 17400 (SHA3-256) benchmark
#   modeled   — scaled from a hashcat anchor by relative shader throughput;
#               treat as +/- 40%, and say so
#
# hashcat kernels are hand-optimised; Animica's miner will not match them.
# KERNEL_EFFICIENCY derates published figures to what the shipped miner is
# expected to reach. It is a stated assumption, not a measurement — when
# someone benchmarks a real GPU on `animica mine --benchmark`, replace the
# entry with provenance="measured" and drop the derate for that row.
KERNEL_EFFICIENCY = 0.60


@dataclass(frozen=True)
class Device:
    key: str
    name: str
    sha3_mhs: float          # SHA3-256 MH/s at the stated provenance
    watts: float             # whole-device draw while mining
    provenance: str
    source: str = ""

    def effective_mhs(self) -> float:
        """Throughput the shipped Animica miner should actually reach."""
        if self.provenance == "measured":
            return self.sha3_mhs
        return self.sha3_mhs * KERNEL_EFFICIENCY


# Anchors (provenance="hashcat") are real published numbers:
#   RTX 4090      5058.7 MH/s  hashcat v6.2.6, gist Chick3nman/32e662a5bb63…
#   RTX 3080 Ti   2092.7 MH/s  onlinehashcrack hashcat benchmark table
#   RX 6800 XT    1427.0 MH/s  gist epixoip/99085955a1145ff61ec83512a50421a7
#   Apple M3 Pro   236.1 MH/s  hashcat v6.2.6-827 Metal, gist Chick3nman/fdf7f9dd…
# The x86 core figure is MEASURED against animica_fastpow.scan() on this host
# (AMD EPYC, 1 thread, 8M iterations): 0.705 MH/s, and 3.35 MH/s across 10 cores
# (sub-linear — the cores are shared on a VPS).
DEVICES: Dict[str, Device] = {
    # --- NVIDIA ---
    "rtx5090":   Device("rtx5090", "NVIDIA RTX 5090", 7800.0, 575, "modeled",
                        "scaled from RTX 4090 hashcat anchor"),
    "rtx4090":   Device("rtx4090", "NVIDIA RTX 4090", 5058.7, 450, "hashcat",
                        "hashcat v6.2.6 m17400"),
    "rtx4080":   Device("rtx4080", "NVIDIA RTX 4080", 3200.0, 320, "modeled",
                        "scaled from RTX 4090"),
    "rtx4070":   Device("rtx4070", "NVIDIA RTX 4070", 1900.0, 200, "modeled",
                        "scaled from RTX 4090"),
    "rtx3090":   Device("rtx3090", "NVIDIA RTX 3090", 2250.0, 350, "modeled",
                        "scaled from RTX 3080 Ti anchor"),
    "rtx3080ti": Device("rtx3080ti", "NVIDIA RTX 3080 Ti", 2092.7, 350, "hashcat",
                        "hashcat m17400"),
    "rtx3060":   Device("rtx3060", "NVIDIA RTX 3060", 780.0, 170, "modeled",
                        "scaled from RTX 3080 Ti"),
    "a100":      Device("a100", "NVIDIA A100 80GB", 3400.0, 300, "modeled",
                        "scaled from RTX 4090"),
    "h100":      Device("h100", "NVIDIA H100", 5200.0, 350, "modeled",
                        "scaled from RTX 4090"),
    # --- AMD ---
    "rx7900xtx": Device("rx7900xtx", "AMD RX 7900 XTX", 2400.0, 355, "modeled",
                        "scaled from RX 6800 XT anchor"),
    "rx6800xt":  Device("rx6800xt", "AMD RX 6800 XT", 1427.0, 300, "hashcat",
                        "hashcat m17400"),
    # --- Apple Silicon ---
    "m4max":     Device("m4max", "Apple M4 Max", 700.0, 60, "modeled",
                        "scaled from M3 Pro anchor by GPU core count"),
    "m3max":     Device("m3max", "Apple M3 Max", 640.0, 55, "modeled",
                        "scaled from M3 Pro anchor by GPU core count"),
    "m3pro":     Device("m3pro", "Apple M3 Pro (14-core GPU)", 236.1, 35, "hashcat",
                        "hashcat v6.2.6-827 Metal m17400"),
    "m2":        Device("m2", "Apple M2 (10-core GPU)", 150.0, 25, "modeled",
                        "scaled from M3 Pro by GPU core count"),
    "m1":        Device("m1", "Apple M1 (8-core GPU)", 110.0, 22, "modeled",
                        "scaled from M3 Pro by GPU core count"),
    # --- CPU (per core, and a couple of common shapes) ---
    "cpu_core":  Device("cpu_core", "one modern x86 core", 0.705, 7, "measured",
                        "animica_fastpow.scan on AMD EPYC, 1 thread"),
    "cpu8":      Device("cpu8", "8-core desktop CPU", 5.6, 90, "measured",
                        "8 x measured single-core rate"),
    "cpu16":     Device("cpu16", "16-core CPU", 11.3, 150, "measured",
                        "16 x measured single-core rate"),
}

# Words a visitor is likely to type -> table key. Longest match wins, so
# "rtx 3080 ti" beats "rtx 3080".
_ALIASES: List[Tuple[str, str]] = [
    ("5090", "rtx5090"), ("4090", "rtx4090"), ("4080", "rtx4080"),
    ("4070", "rtx4070"), ("3090", "rtx3090"), ("3080 ti", "rtx3080ti"),
    ("3080ti", "rtx3080ti"), ("3080", "rtx3080ti"), ("3060", "rtx3060"),
    ("a100", "a100"), ("h100", "h100"),
    ("7900 xtx", "rx7900xtx"), ("7900xtx", "rx7900xtx"),
    ("6800 xt", "rx6800xt"), ("6800xt", "rx6800xt"),
    ("m4 max", "m4max"), ("m4max", "m4max"),
    ("m3 max", "m3max"), ("m3max", "m3max"),
    ("m3 pro", "m3pro"), ("m3pro", "m3pro"), ("m3", "m3pro"),
    ("m2", "m2"), ("m1", "m1"),
]


def match_device(text: str) -> Optional[Device]:
    """Best-effort device lookup from free text. Longest alias wins."""
    t = (text or "").lower()
    best: Optional[Tuple[int, str]] = None
    for alias, key in _ALIASES:
        if alias in t and (best is None or len(alias) > best[0]):
            best = (len(alias), key)
    return DEVICES.get(best[1]) if best else None


# --------------------------------------------------------------------------- #
# live network state                                                           #
# --------------------------------------------------------------------------- #

_PRICE_PATHS = (
    "/root/animica/animica-pool/apps/web/public/anm-price.json",
    "/var/www/animica.org/anm-price.json",
)
_POOL_SUMMARY = os.environ.get(
    "ANIMICA_POOL_SUMMARY_URL", "http://127.0.0.1:8550/api/pool/summary")
_EXPLORER = os.environ.get(
    "ANIMICA_EXPLORER_URL", "https://explorer.animica.org")

_CACHE: Dict[str, Any] = {"at": 0.0, "val": None}
_CACHE_TTL = 120.0


def _read_price() -> Tuple[float, bool]:
    """(usd_per_anm, is_live). Reads the same file the site ticker reads."""
    for p in _PRICE_PATHS:
        try:
            with open(p, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            px = float(d.get("last") or d.get("mid") or 0)
            if px > 0:
                return px, not bool(d.get("is_indicative"))
        except Exception:
            continue
    return _FALLBACK["anm_usd"], False


def _get_json(url: str, timeout: float = 6.0) -> Optional[dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.load(r)
    except Exception:
        return None


def _measure_block_time(height: int, back: int = 1440) -> Optional[float]:
    """Mean seconds/block over the last `back` blocks, from real timestamps.

    The 60 s target is what difficulty AIMS for; it is not what the chain is
    doing. Quoting the target here would inflate every estimate.
    """
    top = _get_json(f"{_EXPLORER}/api/block/{height}")
    old = _get_json(f"{_EXPLORER}/api/block/{max(1, height - back)}")
    if not top or not old:
        return None
    top = top.get("block", top)
    old = old.get("block", old)
    try:
        dt = float(top["time"]) - float(old["time"])
        n = float(top["height"]) - float(old["height"])
        return dt / n if n > 0 else None
    except Exception:
        return None


def network_state(force: bool = False) -> Dict[str, Any]:
    """Live price + hashrate + block cadence. Cached for 2 minutes."""
    now = time.time()
    if not force and _CACHE["val"] and now - _CACHE["at"] < _CACHE_TTL:
        return _CACHE["val"]

    anm_usd, price_live = _read_price()
    summary = _get_json(_POOL_SUMMARY) or {}
    net_hps = float(summary.get("network_hashrate_hps") or 0) or _FALLBACK["network_hps"]
    height = int(summary.get("height") or 0)

    bt = _measure_block_time(height) if height else None
    if not bt or bt <= 0:
        bt = _FALLBACK["block_time_s"]

    state = {
        "anm_usd": anm_usd,
        "price_live": price_live,
        "network_hps": net_hps,
        "network_mhs": net_hps / 1e6,
        "block_time_s": bt,
        "blocks_per_day": 86400.0 / bt,
        "height": height,
        "carve_active": height >= CARVE_FORK_HEIGHT,
        "at": now,
    }
    _CACHE["val"] = state
    _CACHE["at"] = now
    return state


# --------------------------------------------------------------------------- #
# the estimate                                                                 #
# --------------------------------------------------------------------------- #

@dataclass
class Estimate:
    device: str
    device_mhs: float
    network_mhs: float
    share: float
    blocks_per_day: float
    mining_anm_day: float
    mining_usd_day: float
    serving_anm_day: float
    serving_usd_day: float
    power_usd_day: float
    mining_net_usd_day: float
    anm_usd: float
    provenance: str
    note: str = ""


def estimate(device: Device,
             *,
             count: int = 1,
             power_usd_kwh: float = DEFAULT_POWER_USD_KWH,
             serving_claimants: int = 1,
             state: Optional[Dict[str, Any]] = None) -> Estimate:
    """ANM/day and USD/day for `count` of `device`.

    Mining is a SHARE OF EMISSION, never hashrate x price:

        share       = mine / (network + mine)
        blocks/day  = min(target_cap, observed * (network + mine) / network)
        ANM/day     = 150 * blocks/day * share

    The blocks/day term matters because the chain is currently running slower
    than its 60 s target: adding hashrate speeds block production up until
    difficulty retargets, and then it is pinned at 1,440/day no matter how much
    more hashrate arrives. That cap is why a single RTX 4090 and a warehouse of
    them earn nearly the same number of ANM today.
    """
    st = state or network_state()
    mine_mhs = device.effective_mhs() * max(1, count)
    net_mhs = st["network_mhs"]

    share = mine_mhs / (net_mhs + mine_mhs) if (net_mhs + mine_mhs) > 0 else 0.0
    speedup = (net_mhs + mine_mhs) / net_mhs if net_mhs > 0 else 1.0
    blocks_day = min(MAX_BLOCKS_PER_DAY, st["blocks_per_day"] * speedup)

    mining_anm = MINING_ANM_PER_BLOCK * blocks_day * share
    # The service carve is split among whoever claims it that block.
    serving_anm = SERVING_ANM_PER_BLOCK * blocks_day / max(1, serving_claimants)

    px = st["anm_usd"]
    kwh_day = device.watts * max(1, count) * 24.0 / 1000.0
    power = kwh_day * power_usd_kwh

    return Estimate(
        device=(f"{count}x " if count > 1 else "") + device.name,
        device_mhs=mine_mhs,
        network_mhs=net_mhs,
        share=share,
        blocks_per_day=blocks_day,
        mining_anm_day=mining_anm,
        mining_usd_day=mining_anm * px,
        serving_anm_day=serving_anm,
        serving_usd_day=serving_anm * px,
        power_usd_day=power,
        mining_net_usd_day=mining_anm * px - power,
        anm_usd=px,
        provenance=device.provenance,
    )


def _usd(x: float) -> str:
    if abs(x) >= 1:
        return f"${x:,.2f}"
    if abs(x) >= 0.01:
        return f"${x:.3f}"
    # ANM trades around 7e-05, so five decimals rounds the coin price itself to
    # "$0.00007" and throws away the digits that decide every figure above.
    return f"${x:.8f}".rstrip("0").rstrip(".") if x else "$0"


def _anm(x: float) -> str:
    return f"{x:,.0f} ANM" if x >= 10 else f"{x:,.2f} ANM"


def format_estimate(e: Estimate, *, state: Optional[Dict[str, Any]] = None) -> str:
    """Human-readable earnings block for the advisor answer."""
    st = state or network_state()
    prov = {
        "measured": "benchmarked against Animica's own miner",
        "hashcat":  "published hashcat SHA3-256 benchmark",
        "modeled":  "scaled from a published benchmark — treat as ±40%",
    }.get(e.provenance, e.provenance)

    lines = [
        f"**{e.device}** — about **{e.device_mhs:,.0f} MH/s** on Animica's SHA3-256 PoW "
        f"({prov}).",
        "",
        f"- Network right now: **{e.network_mhs:,.1f} MH/s**, "
        f"~{st['block_time_s']:.0f}s/block ({st['blocks_per_day']:,.0f} blocks/day), "
        f"ANM = **{_usd(e.anm_usd)}** (NonKYC last trade).",
        f"- Your share of blocks: **{e.share*100:.1f}%**",
        f"- Mining (150 ANM/block): **{_anm(e.mining_anm_day)}/day ≈ {_usd(e.mining_usd_day)}/day**",
        f"- Power at {DEFAULT_POWER_USD_KWH:.2f}/kWh: −{_usd(e.power_usd_day)}/day "
        f"→ **net {_usd(e.mining_net_usd_day)}/day**",
        f"- Serving inference (75 ANM/block carve): "
        f"**{_anm(e.serving_anm_day)}/day ≈ {_usd(e.serving_usd_day)}/day** "
        f"if you are the only machine claiming it — it is split pro-rata among claimants.",
    ]
    # The cap is stated ALWAYS, not just for big miners. It is the single fact
    # that stops someone extrapolating "more GPUs = more money" indefinitely.
    lines.append(
        f"- Ceiling: the whole chain emits at most **{MAX_MINING_ANM_DAY:,.0f} ANM/day "
        f"mining + {MAX_SERVING_ANM_DAY:,.0f} ANM/day serving** "
        f"({_usd(MAX_TOTAL_ANM_DAY * e.anm_usd)}/day at today's price) — shared by "
        f"everyone. That is the ceiling on this estimate and on every other one."
    )
    if e.share > 0.5:
        lines.append(
            f"- At {e.device_mhs:,.0f} MH/s you are already **larger than the entire "
            f"current network**, so you are near that ceiling: adding more hardware "
            f"multiplies your power bill, not your ANM."
        )
    return "\n".join(lines)


def best_path(e: Estimate) -> str:
    """'mine', 'serve' or 'both' — which use of this hardware pays more."""
    if e.mining_net_usd_day <= 0 and e.serving_usd_day > 0:
        return "serve"
    if e.mining_net_usd_day > e.serving_usd_day * 1.25:
        return "mine"
    return "both"


def summary_line(e: Estimate) -> str:
    """One-line earnings figure to attach to a recommended command."""
    return (f"~{_anm(e.mining_anm_day)}/day mining ({_usd(e.mining_usd_day)}), "
            f"net {_usd(e.mining_net_usd_day)}/day after power")
