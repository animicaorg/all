"""Price oracle for the XMR → ANM payout option.

Miners can register a preference: `payout_currency: "xmr" | "anm"`. For
miners who chose ANM, this module converts XMR atomic units into ANM
nano units at the time of payout, using:

- ANM/USD price from https://buy.animica.org/api/quote/buy — this is the
  same price the gateway charges retail buyers. Using the same oracle
  means miners and gateway customers see consistent valuations.

- XMR/USD price from CoinGecko's free /simple/price endpoint. We don't
  proxy through buy.animica.org because that endpoint doesn't quote
  XMR and the simple/price call is unauthenticated + rate-limited
  generously enough (10-50 calls/min) for our once-per-payout cadence.

Both prices are cached for `CACHE_TTL_SECONDS` (default 300s = 5 min)
so a payout batch doesn't make N RPC calls.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import httpx


_LOG = logging.getLogger("animica.stratum_pool.xmr_pricing")

CACHE_TTL_SECONDS = int(os.environ.get("ANIMICA_POOL_PRICE_CACHE_TTL", "300"))
ANM_PRICE_URL = os.environ.get(
    "ANIMICA_POOL_ANM_PRICE_URL", "https://buy.animica.org/api/quote/buy"
)
XMR_PRICE_URL = os.environ.get(
    "ANIMICA_POOL_XMR_PRICE_URL",
    "https://api.coingecko.com/api/v3/simple/price?ids=monero&vs_currencies=usd",
)

# Used as the throwaway recipient when fetching the ANM price. The
# endpoint requires SOME bech32 address; we send the burn address so
# the oracle treats the request as a price probe.
_PROBE_ADDRESS = "anim1zqp0000000000000000000000000000000000000000000000000000000000"


@dataclass
class CachedPrice:
    usd: Decimal
    fetched_at: float


_cache: dict[str, CachedPrice] = {}
_cache_lock = asyncio.Lock()


async def get_anm_usd_price() -> Decimal:
    """Return current ANM/USD price as Decimal. Cached 5 minutes."""
    async with _cache_lock:
        cached = _cache.get("anm")
        if cached and time.time() - cached.fetched_at < CACHE_TTL_SECONDS:
            return cached.usd

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            ANM_PRICE_URL,
            json={
                "payAmountUsd": "100",  # arbitrary; only the ratio matters
                "payCurrency": "btc",
                "anmReceiveAddress": _PROBE_ADDRESS,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(
            f"buy.animica.org price probe failed: HTTP {resp.status_code} "
            f"{resp.text[:200]}"
        )
    body = resp.json()
    price = body.get("anmPriceUsd")
    if price is None:
        raise RuntimeError(
            f"buy.animica.org price response missing anmPriceUsd: {body}"
        )
    val = Decimal(str(price))
    async with _cache_lock:
        _cache["anm"] = CachedPrice(usd=val, fetched_at=time.time())
    _LOG.info("ANM/USD price: $%s", val)
    return val


async def get_xmr_usd_price() -> Decimal:
    """Return current XMR/USD price as Decimal. Cached 5 minutes."""
    async with _cache_lock:
        cached = _cache.get("xmr")
        if cached and time.time() - cached.fetched_at < CACHE_TTL_SECONDS:
            return cached.usd

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(XMR_PRICE_URL)
    if resp.status_code != 200:
        raise RuntimeError(
            f"CoinGecko XMR price fetch failed: HTTP {resp.status_code} "
            f"{resp.text[:200]}"
        )
    body = resp.json()
    try:
        val = Decimal(str(body["monero"]["usd"]))
    except (KeyError, TypeError) as exc:
        raise RuntimeError(f"unexpected CoinGecko shape: {body!r}") from exc
    async with _cache_lock:
        _cache["xmr"] = CachedPrice(usd=val, fetched_at=time.time())
    _LOG.info("XMR/USD price: $%s", val)
    return val


# Conversion units:
# - XMR atomic = 1e-12 XMR
# - ANM nano   = 1e-9 ANM
async def xmr_atomic_to_anm_nano(xmr_atomic: int) -> int:
    """Convert XMR atomic units to ANM nano units at current prices."""
    if xmr_atomic <= 0:
        return 0
    xmr_usd = await get_xmr_usd_price()
    anm_usd = await get_anm_usd_price()
    if anm_usd <= 0:
        raise RuntimeError("ANM price is zero or negative — refusing conversion")
    xmr_amount = Decimal(xmr_atomic) / Decimal(10) ** 12
    usd_amount = xmr_amount * xmr_usd
    anm_amount = usd_amount / anm_usd
    return int(anm_amount * (Decimal(10) ** 9))


async def selftest() -> None:
    anm = await get_anm_usd_price()
    xmr = await get_xmr_usd_price()
    print(f"ANM/USD: ${anm}")
    print(f"XMR/USD: ${xmr}")
    # 1 XMR (1e12 atomic) → ? ANM
    one_xmr_in_anm = await xmr_atomic_to_anm_nano(10**12)
    print(f"1 XMR = {one_xmr_in_anm / 1e9} ANM at current rates")


if __name__ == "__main__":
    asyncio.run(selftest())
