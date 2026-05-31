"""XMR payout cron — splits Monero block rewards 95% miners / 5% pool.

This module does NOT execute transfers itself. monero-wallet-rpc is a
separate process for security (the pool stratum doesn't get to touch
the wallet directly). Workflow:

  1. Pool's XmrShareLedger accumulates share weights per anim1 address.
  2. When the upstream monerod detects an unlock-able block reward
     (blocks need ~60 confirmations to mature), the pool snapshots the
     ledger, computes per-miner XMR owed (95% of reward × weight share),
     and writes a JSON ledger entry to /var/lib/animica-pool/xmr-payouts.jsonl.
  3. A separate `animica pool xmr-payout` CLI command (run as a cron or
     systemd timer) reads the ledger, calls monero-wallet-rpc `transfer`
     for each owed miner, marks the entry as paid, and updates the
     persistent state.
  4. The 5% pool fee stays in the pool's wallet primary account — no
     transfer needed.

Why split this from the live pool process:

- monero-wallet-rpc needs the wallet password in memory. We don't want
  to keep that loaded inside the long-running stratum process.
- Transfers can take 1-60s each (network broadcast + mempool); doing
  them inside the stratum event loop would block job dispatch.
- Cron timing lets the operator batch payouts daily — fewer transactions
  means lower aggregate XMR fees for the pool wallet to absorb.

Miner registration:

Miners register their XMR receive address by POSTing to
  POST https://pool.animica.org/api/xmr/register
with body
  {"animica_address": "anim1…", "xmr_address": "4…", "signature": "…hex…"}
where signature proves control of the anim1 address. See portal.py for
the endpoint and validation. Until a miner registers, their XMR earnings
accrue in the pool's ledger and the miner can claim retroactively.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx


_LOG = logging.getLogger("animica.stratum_pool.xmr_payouts")

DEFAULT_LEDGER_PATH = Path(
    os.environ.get(
        "ANIMICA_POOL_XMR_LEDGER",
        "/var/lib/animica-pool/xmr-payouts.jsonl",
    )
)
DEFAULT_REGISTRY_PATH = Path(
    os.environ.get(
        "ANIMICA_POOL_XMR_REGISTRY",
        "/var/lib/animica-pool/xmr-miner-registry.json",
    )
)
DEFAULT_STATE_PATH = Path(
    os.environ.get(
        "ANIMICA_POOL_XMR_STATE",
        "/var/lib/animica-pool/xmr-payouts.state.json",
    )
)
DEFAULT_WALLET_RPC = os.environ.get(
    "ANIMICA_POOL_XMR_WALLET_RPC", "http://127.0.0.1:18083/json_rpc"
)

POOL_FEE_BPS = 500  # 5.00 %, in basis points


@dataclass
class PayoutEntry:
    """One pending XMR payout. Lives in xmr-payouts.jsonl."""
    created_at: float
    monero_block_height: int
    monero_block_reward_atomic: int     # XMR atomic units (10^-12 XMR)
    pool_fee_atomic: int
    miner_credits_atomic: Dict[str, int]  # anim1addr → atomic units owed
    total_weight: int
    weight_snapshot: Dict[str, int]
    paid_anim1: List[str] = field(default_factory=list)  # already-paid addresses
    paid_txids: Dict[str, str] = field(default_factory=dict)  # anim1 → xmr txid

    def is_fully_paid(self) -> bool:
        return set(self.paid_anim1) >= set(self.miner_credits_atomic)


def split_block_reward(
    reward_atomic: int,
    weights: Dict[str, int],
) -> tuple[int, Dict[str, int]]:
    """Compute the 95/5 split for a given block. Returns
    (pool_fee_atomic, {miner_addr: owed_atomic}).

    Rounding: integer-rounded floor; any leftover atomic units go to
    the pool fee to keep total = reward exactly.
    """
    total = sum(weights.values())
    if total <= 0 or reward_atomic <= 0:
        return reward_atomic, {}

    pool_fee = (reward_atomic * POOL_FEE_BPS) // 10_000
    miner_pool = reward_atomic - pool_fee
    credits: Dict[str, int] = {}
    distributed = 0
    items = sorted(weights.items(), key=lambda kv: kv[0])
    for addr, w in items:
        share = (miner_pool * w) // total
        credits[addr] = share
        distributed += share
    # Round-down remainder back to pool fee
    leftover = miner_pool - distributed
    pool_fee += leftover
    return pool_fee, credits


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, separators=(",", ":")) + "\n")


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> Dict[str, Dict[str, str]]:
    """Load anim1 → {xmr_address, payout_currency} registry.

    Backward compat: legacy entries stored as a plain `xmr_addr` string
    are upgraded in-memory to {"xmr_address": "...", "payout_currency": "xmr"}.
    """
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text("utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    out: Dict[str, Dict[str, str]] = {}
    for anim1, value in raw.items():
        if isinstance(value, str):
            out[anim1] = {"xmr_address": value, "payout_currency": "xmr"}
        elif isinstance(value, dict):
            out[anim1] = {
                "xmr_address": str(value.get("xmr_address") or ""),
                "payout_currency": str(value.get("payout_currency") or "xmr"),
            }
    return out


def save_registry(
    reg: Dict[str, Dict[str, str]], path: Path = DEFAULT_REGISTRY_PATH
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg, indent=2, sort_keys=True), "utf-8")


async def record_block_reward(
    *,
    monero_block_height: int,
    block_reward_atomic: int,
    weights: Dict[str, int],
    ledger_path: Path = DEFAULT_LEDGER_PATH,
) -> PayoutEntry:
    """Called by the pool when monerod confirms a block we found.

    Writes one PayoutEntry to the JSONL ledger and returns it. The
    actual transfers happen later via `process_pending(...)`.
    """
    pool_fee, credits = split_block_reward(block_reward_atomic, weights)
    entry = PayoutEntry(
        created_at=time.time(),
        monero_block_height=monero_block_height,
        monero_block_reward_atomic=block_reward_atomic,
        pool_fee_atomic=pool_fee,
        miner_credits_atomic=credits,
        total_weight=sum(weights.values()),
        weight_snapshot=dict(weights),
    )
    _append_jsonl(ledger_path, asdict(entry))
    _LOG.info(
        "xmr payout queued: height=%d reward=%d pool_fee=%d miners=%d",
        monero_block_height, block_reward_atomic, pool_fee, len(credits),
    )
    return entry


async def _wallet_call(
    url: str, method: str, params: Optional[Dict[str, Any]] = None,
    timeout: float = 60.0,
) -> Dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": "0", "method": method}
    if params is not None:
        payload["params"] = params
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        body = resp.json()
    if body.get("error"):
        raise RuntimeError(f"wallet-rpc {method}: {body['error']}")
    return body.get("result", {})


async def _send_anm(anim1_to: str, anm_nano: int) -> Dict[str, Any]:
    """Send ANM from the mldsamain wallet via the local animica CLI.

    Returns {"tx_hash": "...", "value_nanm": N}. Shells out because the
    CLI already has the wallet store and signing logic; calling it
    keeps the pool out of the key-material business.
    """
    import subprocess

    cli = os.environ.get("ANIMICA_POOL_ANM_CLI", "/root/animica/.venv/bin/animica")
    from_label = os.environ.get("ANIMICA_POOL_ANM_FROM_LABEL", "mldsamain")
    # Look up the from-address by label so the user can change wallets
    # without code edits.
    try:
        listing = subprocess.run(
            [cli, "wallet", "list"],
            check=True, capture_output=True, text=True, timeout=20,
        )
    except Exception as exc:
        raise RuntimeError(f"animica wallet list failed: {exc}")
    from_addr = ""
    for line in listing.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] == from_label:
            # idx, label, address, alg_name
            from_addr = parts[2]
            break
        if len(parts) >= 4 and from_label in line:
            from_addr = parts[2]
            break
    if not from_addr:
        raise RuntimeError(
            f"animica CLI has no wallet with label {from_label!r}. "
            "Set ANIMICA_POOL_ANM_FROM_LABEL or create the wallet."
        )
    try:
        result = subprocess.run(
            [cli, "tx", "send",
             "--from", from_addr,
             "--to", anim1_to,
             "--value-nanm", str(anm_nano),
             "--gas-price", "0",
             "--max-fee", "0"],
            check=True, capture_output=True, text=True, timeout=60,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"animica tx send failed: {exc.stderr[:300]}"
        ) from exc
    # Parse the tx_hash from stdout
    tx_hash = ""
    for line in result.stdout.splitlines():
        if "tx_hash" in line.lower() or line.startswith("Tx Hash:"):
            for token in line.replace("'", " ").replace(",", " ").split():
                if token.startswith("0x") and len(token) >= 40:
                    tx_hash = token
                    break
        if tx_hash:
            break
    return {"tx_hash": tx_hash, "value_nanm": anm_nano, "from_addr": from_addr}


async def process_pending(
    *,
    ledger_path: Path = DEFAULT_LEDGER_PATH,
    state_path: Path = DEFAULT_STATE_PATH,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    wallet_rpc_url: str = DEFAULT_WALLET_RPC,
    min_payout_atomic: int = 100_000_000_000,  # 0.0001 XMR — cover network fees
    min_payout_anm_nano: int = 1_000_000_000,  # 1 ANM — covers chain dust filter
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Walk the JSONL ledger, find unpaid entries, attempt transfers.

    For each miner credit:
      • If miner registered XMR payout → transfer XMR via monero-wallet-rpc.
      • If miner registered ANM payout → convert atomic→nano at current
        prices via xmr_pricing, then send ANM from mldsamain via the
        animica CLI. Pool keeps the XMR.
      • If miner has no registration → accrue (no transfer); they keep
        their claim and can register later to collect.
    """
    entries = _load_jsonl(ledger_path)
    registry = load_registry(registry_path)
    state: Dict[str, Any] = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text("utf-8"))
        except Exception:
            state = {}

    paid_set = set(state.get("paid_entry_keys", []))
    xmr_transfers: List[Dict[str, Any]] = []
    anm_transfers: List[Dict[str, Any]] = []
    skipped_no_registration: Dict[str, int] = {}
    pricing_used: Optional[Dict[str, str]] = None

    for idx, raw in enumerate(entries):
        key = f"{raw['monero_block_height']}-{int(raw['created_at'])}"
        if key in paid_set:
            continue
        credits = raw.get("miner_credits_atomic", {})
        xmr_dest: List[Dict[str, Any]] = []
        anm_jobs: List[Dict[str, Any]] = []
        for anim1, atomic in credits.items():
            if atomic < min_payout_atomic:
                continue
            reg = registry.get(anim1)
            if not reg:
                skipped_no_registration[anim1] = (
                    skipped_no_registration.get(anim1, 0) + atomic
                )
                continue
            payout_ccy = reg.get("payout_currency", "xmr")
            if payout_ccy == "anm":
                try:
                    from animica.stratum_pool.xmr_pricing import (
                        xmr_atomic_to_anm_nano,
                        get_anm_usd_price,
                        get_xmr_usd_price,
                    )
                    anm_nano = await xmr_atomic_to_anm_nano(atomic)
                    if pricing_used is None:
                        pricing_used = {
                            "anm_usd": str(await get_anm_usd_price()),
                            "xmr_usd": str(await get_xmr_usd_price()),
                        }
                except Exception as exc:
                    _LOG.warning(
                        "ANM payout pricing failed for %s: %s",
                        anim1, exc,
                    )
                    continue
                if anm_nano < min_payout_anm_nano:
                    skipped_no_registration[f"{anim1}:anm_below_dust"] = anm_nano
                    continue
                anm_jobs.append({
                    "anim1_to": anim1,
                    "anm_nano": anm_nano,
                    "xmr_atomic_basis": atomic,
                })
            else:
                xmr_addr = reg.get("xmr_address") or ""
                if not xmr_addr:
                    skipped_no_registration[anim1] = (
                        skipped_no_registration.get(anim1, 0) + atomic
                    )
                    continue
                xmr_dest.append({"address": xmr_addr, "amount": atomic})

        if not xmr_dest and not anm_jobs:
            continue

        if dry_run:
            if xmr_dest:
                xmr_transfers.append({
                    "block_height": raw["monero_block_height"],
                    "destinations": xmr_dest,
                    "dry_run": True,
                })
            if anm_jobs:
                anm_transfers.append({
                    "block_height": raw["monero_block_height"],
                    "payouts": anm_jobs,
                    "dry_run": True,
                })
            continue

        # Execute XMR transfers
        if xmr_dest:
            try:
                result = await _wallet_call(
                    wallet_rpc_url, "transfer_split",
                    params={
                        "destinations": xmr_dest,
                        "priority": 1,
                        "ring_size": 16,
                        "get_tx_keys": True,
                    },
                )
                xmr_transfers.append({
                    "block_height": raw["monero_block_height"],
                    "destinations": xmr_dest,
                    "tx_hash_list": result.get("tx_hash_list", []),
                })
            except Exception as exc:
                _LOG.warning("xmr payout transfer failed: %s", exc)
                continue

        # Execute ANM transfers
        if anm_jobs:
            done = []
            for job in anm_jobs:
                try:
                    sent = await _send_anm(job["anim1_to"], job["anm_nano"])
                    done.append({**job, **sent})
                except Exception as exc:
                    _LOG.warning(
                        "ANM payout to %s failed: %s",
                        job["anim1_to"], exc,
                    )
            if done:
                anm_transfers.append({
                    "block_height": raw["monero_block_height"],
                    "payouts": done,
                })

        paid_set.add(key)

    if not dry_run:
        state["paid_entry_keys"] = sorted(paid_set)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, indent=2), "utf-8")

    return {
        "xmr_transfers": xmr_transfers,
        "anm_transfers": anm_transfers,
        "pricing_used": pricing_used,
        "skipped_no_registration": skipped_no_registration,
        "dry_run": dry_run,
    }


# ── CLI entrypoint, surfaced as `animica pool xmr-payout` ──────────────

def _cli_main() -> int:
    import argparse, asyncio
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="compute + display, don't transfer")
    ap.add_argument("--wallet-rpc", default=DEFAULT_WALLET_RPC)
    ap.add_argument("--min-payout-atomic", type=int, default=100_000_000_000)
    args = ap.parse_args()
    out = asyncio.run(process_pending(
        wallet_rpc_url=args.wallet_rpc,
        min_payout_atomic=args.min_payout_atomic,
        dry_run=args.dry_run,
    ))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
