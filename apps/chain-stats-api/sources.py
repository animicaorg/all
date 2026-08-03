"""Data sources for the Animica chain-stats + supply API.

Everything network-facing goes through this module so it can be mocked in
tests (httpx clients are created via :func:`_make_client`).

Upstreams
---------
* Node JSON-RPC        http://127.0.0.1:8545/rpc   (chain.getHead, state.getTotalSupply,
                       state.getBalance, chain.getNetworkHashrate, chain.getBlockByHeight)
* Price feed           /var/www/animica.org/anm-price.json (file), falling back to
                       https://animica.org/anm-price.json (NonKYC ANM/USDT, systemd timer)
* Mining pool API      http://127.0.0.1:8550 (/v1/pool/status, /api/pool/summary)

Chain constants below are mirrored from /root/animica/consensus/rewards.py and
verified against live RPC on 2026-08-03. The subsidy math reproduces
consensus.rewards._subsidy_total_for_height exactly (integer math, no floats).
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

# ---------------------------------------------------------------------------
# Chain constants (mirrored from consensus/rewards.py + spec/params.yaml)
# ---------------------------------------------------------------------------

COIN = 10**9  # 1 ANM = 1e9 nANM base units
DECIMALS = 9

MAX_MONEY_BASE_UNITS = 900_000_000 * COIN          # hard cap (MAX_MONEY)
INITIAL_BLOCK_REWARD_BASE_UNITS = 300 * COIN        # 300 ANM/block, epoch 0
PREMINE_BASE_UNITS = 81_000_000 * COIN

EPOCH_LENGTH_BLOCKS = 1_350_000                     # halving interval (~2.57y @ 60s)
DECAY_PCT_PER_EPOCH = 50                            # 50% halving per epoch
TAIL_BASE_UNITS = 100_000                           # 0.0001 ANM/block tail
MAX_HALVINGS = 64

TARGET_BLOCK_TIME_S = 60

FOUNDATION_SPLIT_HEIGHT = 42_001                    # FORK_FOUNDATION_SPLIT (7.1.0)
FOUNDATION_SPLIT_PCT = 15                           # 15% of subsidy -> foundation
IOU_SETTLEMENT_HEIGHT = 50_000                      # FORK_IOU_SETTLEMENT (8.0.1/9.0.0)
IOU_CAP_BASE_UNITS = 50 * COIN                      # <= 50 ANM/block, halving with emission

HASHSHARE_TRIALS = 2**32                            # rpc/hashrate.py HASHSHARE_TRIALS

CHAIN_NAME = "Animica"
CHAIN_SYMBOL = "ANM"
CHAIN_ID = 1
ALGORITHM = "SHA3-256 PoW (PoIES)"

FOUNDATION_TREASURY_ADDRESS = (
    "anim1zqpsmegc0qcvzjfukm89xs0zeu3eqyyyel7kelehuszvwfarqypky2gr946ga"
)
GENESIS_PREMINE_ADDRESS = (
    "anim1zqp2rdpnhwvvfe03ts9tf9rnp7p449xhnvh0u0wpvle3wahtce64zwgz8m208"
)

# The four params system_addresses (chain.getParams) are non-decoding
# placeholders; they hold 0 today but are protocol-reserved, so they stay on
# the non-circulating list.
DEFAULT_NON_CIRCULATING: List[Tuple[str, str]] = [
    (
        FOUNDATION_TREASURY_ADDRESS,
        "Animica Foundation treasury (receives 15% of every block subsidy since height 42,001)",
    ),
    ("anim1treasuryxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "protocol treasury (params system address, reserved)"),
    ("anim1aicfxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "AICF treasury (params system address, reserved)"),
    ("anim1foundationxxxxxxxxxxxxxxxxxxxxxxxxxx", "foundation lockup (params system address, reserved)"),
    ("anim1coinbasexxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "coinbase default (params system address, reserved)"),
]
_KNOWN_LABELS = dict(DEFAULT_NON_CIRCULATING)
_KNOWN_LABELS[GENESIS_PREMINE_ADDRESS] = "genesis premine address (81M ANM at height 0, since distributed)"

POOL_PUBLIC_NAME = "Animica Official Pool"
POOL_PUBLIC_URL = "https://pool.animica.org"
POOL_PUBLIC_STRATUM = "stratum+tcp://pool.animica.org:3333"
POOL_PUBLIC_STRATUM_SOLO = "stratum+tcp://pool.animica.org:3334"
# Fees are static pool config (no HTTP endpoint exposes them); values verified
# read-only from /etc/animica/pool.env on 2026-08-03.
POOL_DEFAULT_FEE_BPS = 0        # PPS fee (ANIMICA_POOL_ENA_FEE_BPS=0)
POOL_DEFAULT_SOLO_FEE_BPS = 500  # solo port :3334 (finder keeps 95%)


class SourceError(RuntimeError):
    """Raised when a critical upstream (the node RPC) cannot be read."""


# ---------------------------------------------------------------------------
# Config (env-overridable, read lazily so tests can monkeypatch os.environ)
# ---------------------------------------------------------------------------

def _env(name: str, default: str) -> str:
    v = os.environ.get(name, "").strip()
    return v if v else default


def rpc_url() -> str:
    return _env("ANIMICA_STATS_RPC_URL", "http://127.0.0.1:8545/rpc")


def pool_api_url() -> str:
    return _env("ANIMICA_STATS_POOL_API_URL", "http://127.0.0.1:8550").rstrip("/")


def price_file_path() -> str:
    return _env("ANIMICA_STATS_PRICE_FILE", "/var/www/animica.org/anm-price.json")


def price_fallback_url() -> str:
    return _env("ANIMICA_STATS_PRICE_URL", "https://animica.org/anm-price.json")


def http_timeout() -> float:
    try:
        t = float(_env("ANIMICA_STATS_HTTP_TIMEOUT", "8"))
    except ValueError:
        t = 8.0
    return min(t, 10.0)  # hard requirement: all upstream timeouts <= 10s


def price_fresh_window_s() -> float:
    try:
        return float(_env("ANIMICA_STATS_PRICE_FRESH_S", "1800"))
    except ValueError:
        return 1800.0


def pool_fee_bps() -> int:
    try:
        return int(_env("ANIMICA_STATS_POOL_FEE_BPS", str(POOL_DEFAULT_FEE_BPS)))
    except ValueError:
        return POOL_DEFAULT_FEE_BPS


def non_circulating_list() -> List[Tuple[str, str]]:
    """Non-circulating addresses: the built-in defaults (foundation treasury +
    4 params system addresses) UNION any extras from ANIMICA_NON_CIRCULATING
    (comma-separated). The env var is strictly ADDITIVE — it can never drop a
    default, so the foundation treasury is always excluded from circulating."""
    out: List[Tuple[str, str]] = list(DEFAULT_NON_CIRCULATING)
    seen = {addr for addr, _ in out}
    raw = os.environ.get("ANIMICA_NON_CIRCULATING", "").strip()
    for part in raw.split(","):
        addr = part.strip()
        if not addr or addr in seen:
            continue
        seen.add(addr)
        out.append((addr, _KNOWN_LABELS.get(addr, "non-circulating (configured via ANIMICA_NON_CIRCULATING)")))
    return out


# ---------------------------------------------------------------------------
# HTTP plumbing (single seam for tests: monkeypatch _make_client)
# ---------------------------------------------------------------------------

def _make_client(timeout: Optional[float] = None) -> httpx.Client:
    return httpx.Client(timeout=timeout if timeout is not None else http_timeout())


_rpc_id = 0


def rpc_call(method: str, params: Optional[dict] = None, client: Optional[httpx.Client] = None) -> Any:
    """JSON-RPC 2.0 call against the node. Raises SourceError on any failure."""
    global _rpc_id
    _rpc_id += 1
    payload = {"jsonrpc": "2.0", "id": _rpc_id, "method": method, "params": params or {}}
    own = client is None
    c = client if client is not None else _make_client()
    try:
        r = c.post(rpc_url(), json=payload)
        r.raise_for_status()
        body = r.json()
        if body.get("error"):
            raise SourceError(f"rpc {method}: {body['error']!r}")
        return body.get("result")
    except httpx.HTTPError as exc:  # connect/timeout/protocol/status errors
        raise SourceError(f"rpc {method}: {exc}") from exc
    except (json.JSONDecodeError, ValueError) as exc:
        raise SourceError(f"rpc {method}: bad JSON: {exc}") from exc
    finally:
        if own:
            c.close()


# ---------------------------------------------------------------------------
# Amount parsing / formatting (exact integer math; never floats, never sci-notation)
# ---------------------------------------------------------------------------

def parse_hex_amount(value: Any) -> int:
    """Parse an RPC amount: '0x...' hex string, decimal string, or int -> base units."""
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.lower().startswith("0x"):
            return int(s, 16)
        if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
            return int(s, 10)
    raise SourceError(f"unparseable amount: {value!r}")


def base_units_to_anm_str(base_units: int) -> str:
    """Base units (nANM) -> whole-ANM decimal string. Full precision, plain
    decimal notation: no exponent, no commas, no trailing zeros."""
    sign = "-" if base_units < 0 else ""
    bu = abs(int(base_units))
    whole, frac = divmod(bu, COIN)
    if frac == 0:
        return f"{sign}{whole}"
    frac_s = str(frac).rjust(DECIMALS, "0").rstrip("0")
    return f"{sign}{whole}.{frac_s}"


def base_units_to_anm_float(base_units: int) -> float:
    return base_units / COIN


# ---------------------------------------------------------------------------
# Emission schedule (mirrors consensus/rewards.py exactly)
# ---------------------------------------------------------------------------

def epoch_for_height(height: int) -> int:
    if height <= 0:
        return 0
    e = (height - 1) // EPOCH_LENGTH_BLOCKS
    return min(e, MAX_HALVINGS - 1)


def subsidy_total_base_units(height: int) -> int:
    """Block subsidy at `height` in base units (consensus/rewards.py
    _subsidy_total_for_height: floor(300e9 * 50^e / 100^e), tail floor)."""
    if height <= 0:
        return 0
    e = epoch_for_height(height)
    num = (100 - DECAY_PCT_PER_EPOCH) ** e
    subsidy = (INITIAL_BLOCK_REWARD_BASE_UNITS * num) // (100 ** e)
    return max(subsidy, TAIL_BASE_UNITS)


def subsidy_split_base_units(height: int) -> Tuple[int, int, int]:
    """(total, miner, foundation) base units at `height`; 85/15 split since
    FORK_FOUNDATION_SPLIT (42,001), same total — redistribution, not issuance."""
    total = subsidy_total_base_units(height)
    if height >= FOUNDATION_SPLIT_HEIGHT:
        foundation = (total * FOUNDATION_SPLIT_PCT) // 100
        return total, total - foundation, foundation
    return total, total, 0


# ---------------------------------------------------------------------------
# Price feed
# ---------------------------------------------------------------------------

def read_price() -> Optional[Dict[str, Any]]:
    """ANM/USDT feed: local file first, HTTPS fallback. None if unavailable —
    never fabricated."""
    raw: Optional[dict] = None
    try:
        with open(price_file_path(), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        try:
            with _make_client() as c:
                r = c.get(price_fallback_url())
                r.raise_for_status()
                raw = r.json()
        except Exception:
            return None
    if not isinstance(raw, dict):
        return None
    ts = raw.get("ts")
    fresh = isinstance(ts, (int, float)) and (time.time() - float(ts)) <= price_fresh_window_s()
    return {
        "last_usd": raw.get("last"),
        "bid_usd": raw.get("bid"),
        "ask_usd": raw.get("ask"),
        "change_24h_pct": raw.get("change_percent"),
        "volume_24h_anm": raw.get("base_volume"),
        "volume_24h_usd": raw.get("target_volume"),
        "market_url": raw.get("market_url"),
        "source": raw.get("source"),
        "pair": raw.get("symbol"),
        "ts": ts,
        "fresh": bool(fresh),
    }


# ---------------------------------------------------------------------------
# Pool API (best-effort; None / nulls when unreachable — never fabricated)
# ---------------------------------------------------------------------------

def fetch_pool() -> Optional[Dict[str, Any]]:
    base = pool_api_url()
    out: Dict[str, Any] = {}
    try:
        with _make_client() as c:
            try:
                r = c.get(f"{base}/v1/pool/status")
                r.raise_for_status()
                s = r.json()
                out["connected_miners"] = s.get("connected_miners")
                out["synced"] = s.get("synced")
            except Exception:
                pass
            try:
                r = c.get(f"{base}/api/pool/summary")
                r.raise_for_status()
                s = r.json()
                out["num_miners"] = s.get("num_miners")
                out["num_workers"] = s.get("num_workers")
                out["blocks_found_total"] = s.get("blocks_found_total")
                out["pool_mode"] = s.get("pool_mode")
                # raw H/s from per-share expected work (NOT the legacy ratio fields)
                out["pool_hashrate_raw_1h_hs"] = s.get("hashrate_raw_1h")
                out["pool_observed_network_hashrate_hs"] = s.get("network_hashrate_hps")
            except Exception:
                pass
    except Exception:
        return None
    return out or None


# ---------------------------------------------------------------------------
# The aggregate gather (one call = one refresh; cached by the app layer)
# ---------------------------------------------------------------------------

def gather() -> Dict[str, Any]:
    """Fetch everything. Node-RPC failure raises SourceError (the app then
    serves the persisted last-good snapshot); price/pool failures degrade to
    nulls."""
    with _make_client() as c:
        head = rpc_call("chain.getHead", client=c)
        height = int(head["height"])
        theta_micro = int(head["thetaMicro"])

        supply_res = rpc_call("state.getTotalSupply", client=c)
        total_bu = parse_hex_amount(supply_res["totalSupply"])
        address_count = supply_res.get("addressCount")

        non_circ: List[Dict[str, Any]] = []
        non_circ_sum = 0
        for addr, label in non_circulating_list():
            bal = parse_hex_amount(rpc_call("state.getBalance", {"address": addr}, client=c))
            non_circ.append({"address": addr, "label": label, "balance_base_units": bal})
            non_circ_sum += bal
        circulating_bu = max(0, total_bu - non_circ_sum)

        # Network hashrate: raw H/s = hashrate_hsps * 2^32.
        # chain.getNetworkHashrate = sum(e^(thetaMicro_i/1e6)) / (ts_end - ts_start) / 2^32
        # (rpc/methods/chain.py; expected hashes/block = e^(thetaMicro/1e6)).
        network_hashrate_hs: Optional[float] = None
        hashshare_per_s: Optional[float] = None
        hr_window: Dict[str, Any] = {}
        try:
            hr = rpc_call("chain.getNetworkHashrate", {"window_blocks": 120}, client=c)
            hashshare_per_s = float(hr["hashrate_hsps"])
            network_hashrate_hs = hashshare_per_s * HASHSHARE_TRIALS
            hr_window = {
                "window_blocks": hr.get("window_blocks"),
                "window_seconds": hr.get("window_seconds"),
                "method": hr.get("method"),
            }
        except (SourceError, KeyError, TypeError, ValueError):
            pass  # non-fatal: hashrate is null rather than fabricated

        # Last block time + 1h average block time from real headers (cheap: 2 calls).
        last_block_ts: Optional[int] = None
        avg_block_time_1h_s: Optional[float] = None
        try:
            head_blk = rpc_call("chain.getBlockByHeight", {"height": height}, client=c)
            if not isinstance(head_blk, dict):
                head_blk = {}  # null / non-dict RPC result -> optional data missing
            ts_head = int(head_blk.get("timestamp") or 0)
            if ts_head > 0:
                last_block_ts = ts_head
                prev_height = max(1, height - 60)
                if prev_height < height:
                    prev_blk = rpc_call("chain.getBlockByHeight", {"height": prev_height}, client=c)
                    if not isinstance(prev_blk, dict):
                        prev_blk = {}
                    ts_prev = int(prev_blk.get("timestamp") or 0)
                    if 0 < ts_prev < ts_head:
                        avg_block_time_1h_s = round((ts_head - ts_prev) / (height - prev_height), 2)
        except (SourceError, AttributeError, KeyError, TypeError, ValueError):
            pass  # block-time is optional; its failure must never poison the refresh

    price = read_price()
    pool = fetch_pool()

    total_sub, miner_sub, foundation_sub = subsidy_split_base_units(height)

    return {
        "schema": 1,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "height": height,
        "theta_micro": theta_micro,
        "expected_hashes_per_block": math.exp(theta_micro / 1e6),
        "supply": {
            "total_base_units": total_bu,
            "circulating_base_units": circulating_bu,
            "max_base_units": MAX_MONEY_BASE_UNITS,
            "address_count": address_count,
            "non_circulating": non_circ,
        },
        "hashrate": {
            "network_hashrate_hs": network_hashrate_hs,
            "hashshare_per_s": hashshare_per_s,
            **hr_window,
            "pool_hashrate_raw_1h_hs": (pool or {}).get("pool_hashrate_raw_1h_hs"),
            "pool_observed_network_hashrate_hs": (pool or {}).get("pool_observed_network_hashrate_hs"),
        },
        "blocks": {
            "last_block_timestamp": last_block_ts,
            "avg_block_time_1h_s": avg_block_time_1h_s,
            "block_time_target_s": TARGET_BLOCK_TIME_S,
        },
        "reward": {
            "block_subsidy_base_units": total_sub,
            "miner_base_units": miner_sub,
            "foundation_base_units": foundation_sub,
        },
        "price": price,
        "pool": pool,
    }
