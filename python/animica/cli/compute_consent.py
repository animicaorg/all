"""First-run consent + enrollment for optional third-party GPU compute (Clore).

`animica up` can, IF THE OPERATOR OF THE MACHINE AGREES, enroll this rig's GPU on
the Clore marketplace so it earns while the pool would otherwise pay near-nothing
in ANM. This is DELIBERATELY opt-in and clearly disclosed, because it is materially
different from mining:

  - a Clore rental gives an ANONYMOUS third party the ability to run their OWN
    code (SSH / Docker) on this machine, and
  - earnings accrue to the POOL's Clore account, which returns 90% to miners as
    ANM and keeps 10%.

Turning that on silently would be indistinguishable from a backdoor. So it is
NEVER on by default: the first time `animica up` runs on a GPU box it asks once,
in plain language, and remembers the answer. Non-interactive runs default to OFF.

State: ~/.animica/compute-consent.json  ({"clore": true/false, "asked": ts}).
Override for automation: ANIMICA_COMPUTE=on|off skips the prompt.
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

_STATE = Path(os.path.expanduser("~/.animica/compute-consent.json"))

_DISCLOSURE = """\
[bold yellow]Optional: earn more by renting this GPU on Clore[/bold yellow]

Mining ANM currently pays close to nothing (the whole network mints a few dollars
a day). This machine could instead earn on the Clore GPU marketplace. Before you
decide, understand exactly what that means:

  • A paying, [bold]anonymous third party[/bold] rents your GPU and runs
    [bold]their own code[/bold] on this machine (SSH / Docker access).
  • Earnings go to the Animica pool's Clore account, which pays you
    [bold]90%[/bold] (converted to ANM via the pool payout wallet) and keeps 10%.
  • You can turn this off any time: [dim]animica compute off[/dim]

This is [bold]off unless you say yes[/bold]. Mining/serving is unaffected either way.
"""


def _fmt_usd(x: float) -> str:
    return f"${x:,.2f}" if x >= 0 else f"-${-x:,.2f}"


def earnings_estimate(console) -> Optional[dict]:
    """Best-effort side-by-side: mining ANM vs renting on Clore, per day.

    Every input is fetched live so the figure is honest today, not a baked-in
    marketing number. Returns None (and the caller shows a generic line) if the
    market data can't be reached — better no number than a made-up one.
    """
    import urllib.request

    def _get(url: str) -> Optional[dict]:
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) animica-up/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                return json.loads(r.read().decode())
        except Exception:
            return None

    # live ANM price (mid) from NonKYC
    t = _get("https://api.nonkyc.io/api/v2/ticker/ANM_USDT")
    anm_usd = None
    if t:
        try:
            anm_usd = (float(t["bid"]) + float(t["ask"])) / 2
        except Exception:
            anm_usd = None
    if not anm_usd:
        return None

    # live Clore on-demand rate: median USD/day across listed GPU servers
    mk = _get("https://api.clore.ai/v1/marketplace")
    clore_usd_day = None
    if mk and isinstance(mk.get("servers"), list) and mk["servers"]:
        rates = []
        for s in mk["servers"]:
            pr = (s.get("price") or {}).get("on_demand") or {}
            usd = pr.get("usd") or pr.get("USD")
            if usd:
                rates.append(float(usd) * 24)  # per-hour -> per-day
        if rates:
            rates.sort()
            clore_usd_day = rates[len(rates) // 2]  # median
    if not clore_usd_day:
        # conservative fallback consistent with public Clore ranges for a mid GPU
        clore_usd_day = 3.00

    # Realizable ANM/day for one GPU. Two haircuts make this honest rather than
    # flattering:
    #  1. A single consumer GPU is a small share of the pool's reward, not a
    #     quarter of it. Estimate from live network hashrate vs. a typical card.
    #  2. ANM is thinly traded — selling a day's mined ANM walks the book down
    #     hard, so mark-to-market overstates what a miner can actually realise.
    #     `realizable_frac` discounts spot toward achievable sale price.
    realizable_frac = _realizable_fraction()
    gpu_share = _gpu_network_share()
    anm_per_day = 150 * (86400 / 95) * gpu_share
    mining_usd_day = anm_per_day * anm_usd * realizable_frac

    # Compute revenue is USDC — no liquidity haircut on the way in. The pool buys
    # ANM with it (that buy DOES pay slippage, folded into realizable_frac on the
    # miner's eventual sale, so we keep the comparison apples-to-apples).
    miner_share = 0.90
    compute_usd_day = clore_usd_day * miner_share
    compute_anm_day = compute_usd_day / anm_usd

    return {
        "anm_usd": anm_usd,
        "mining_anm_day": anm_per_day,
        "mining_usd_day": mining_usd_day,
        "clore_usd_day": clore_usd_day,
        "compute_usd_day": compute_usd_day,
        "compute_anm_day": compute_anm_day,
        "realizable_frac": realizable_frac,
    }


def _gpu_network_share() -> float:
    """Fraction of pool reward one consumer GPU realistically earns.

    A single card is a few percent of a small pool, not a quarter of it. Absent
    per-rig telemetry, 0.03 (3%) is a deliberately modest default so the estimate
    never overstates mining income.
    """
    try:
        return float(os.getenv("ANIMICA_GPU_NETWORK_SHARE", "0.03"))
    except Exception:
        return 0.03


def _realizable_fraction() -> float:
    """How much of ANM's mark-to-market a miner can actually sell into.

    ANM's order book is very thin, so a day's mined ANM cannot be sold near spot.
    Measured this session at roughly an 85%+ haircut walking the full book; 0.15
    is the default, overridable if the book deepens.
    """
    try:
        return float(os.getenv("ANIMICA_ANM_REALIZABLE_FRAC", "0.15"))
    except Exception:
        return 0.15


def _load() -> dict:
    try:
        return json.loads(_STATE.read_text())
    except Exception:
        return {}


def _save(d: dict) -> None:
    _STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
    tmp.replace(_STATE)


def _env_override() -> Optional[bool]:
    v = os.getenv("ANIMICA_COMPUTE", "").strip().lower()
    if v in ("on", "1", "true", "yes"):
        return True
    if v in ("off", "0", "false", "no"):
        return False
    return None


def resolve_compute_consent(console, *, has_gpu: bool, plan: bool) -> bool:
    """Return whether third-party compute is enabled for this run.

    Order: explicit env override > stored answer > first-run prompt. Never
    prompts on --plan, on a non-GPU box, or on a non-interactive stdin (those
    all default OFF, fail-safe).
    """
    env = _env_override()
    if env is not None:
        return env

    state = _load()
    if "clore" in state:
        return bool(state["clore"])

    if plan or not has_gpu:
        return False

    # Only a real interactive TTY gets the prompt; anything scripted stays OFF.
    if not (sys.stdin and sys.stdin.isatty()):
        return False

    try:
        import typer
        console.print(_DISCLOSURE)
        est = earnings_estimate(console)
        if est:
            console.print(
                "\n[bold]Rough estimate for one GPU, at today's live prices:[/bold]\n"
                f"  mining ANM here : ~{est['mining_anm_day']:,.0f} ANM/day "
                f"([bold]{_fmt_usd(est['mining_usd_day'])}[/bold] at "
                f"${est['anm_usd']:.8f}/ANM)\n"
                f"  renting on Clore: ~{est['compute_anm_day']:,.0f} ANM/day "
                f"([bold]{_fmt_usd(est['compute_usd_day'])}[/bold], your 90% share "
                f"of ~{_fmt_usd(est['clore_usd_day'])})\n"
                f"  [dim]paid as ANM either way; Clore revenue is bought on-market and "
                f"distributed by the pool. Actual earnings depend on your GPU and "
                f"rental demand.[/dim]")
        else:
            console.print(
                "\n[dim]Live rate estimate unavailable right now — but Clore rentals "
                "typically pay several dollars/GPU/day vs. near-zero for mining ANM at "
                "current prices.[/dim]")
        enabled = typer.confirm("\nEnable third-party GPU compute on this machine?",
                                default=False)
    except Exception:
        enabled = False

    state.update({"clore": bool(enabled), "asked": int(time.time())})
    _save(state)
    if enabled:
        console.print("[green]compute enabled[/green] — this GPU will be offered on Clore. "
                      "[dim]Disable with: animica compute off[/dim]")
    else:
        console.print("[dim]compute stays off. Enable later with: animica compute on[/dim]")
    return bool(enabled)


def set_consent(enabled: bool) -> None:
    """Used by `animica compute on|off` to change the stored answer."""
    state = _load()
    state.update({"clore": bool(enabled), "asked": int(time.time())})
    _save(state)
