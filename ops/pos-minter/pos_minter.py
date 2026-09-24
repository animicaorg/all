#!/usr/bin/env python3
"""
Animica PoS minter — produces hybrid-PoS blocks for a staked validator.

Runs OUTSIDE the node, on the host, for one reason: signing needs the validator's
secret key, and the node container deliberately cannot read
``/root/.animica/wallets.json``. The key is loaded here at runtime, used only to
sign the block preimage, and never logged or transmitted.

Each slot it:
  1. asks the node for a block template (`miner.getBlockTemplate`),
  2. rebuilds that template's header with ``workType = WORKTYPE_POS``,
  3. nests a signed PoS proof inside the header's existing ``extra`` map —
     preserving the coinbase commitment the template put there,
  4. submits the block (`miner.submitBlock`).

It does not check locally whether it is this slot's leader: the node re-derives
the leader from committed stake and rejects a block from anyone else. Attempting
and being rejected is cheap, and it keeps exactly one authority for that rule.

Environment
-----------
  ANIMICA_RPC_URL              node RPC (default http://127.0.0.1:8545/rpc)
  ANIMICA_POS_WALLET_LABEL     wallet label to mint with (default "tresure")
  ANIMICA_POS_WALLETS_PATH     keystore path (default /root/.animica/wallets.json)
  ANIMICA_POS_TARGET_BLOCK_S   slot length; MUST match chain block.target_seconds
  ANIMICA_POS_DRY_RUN=1        build and sign, but never submit
  ANIMICA_POS_ONCE=1           single attempt, then exit (for smoke tests)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import replace
from typing import Any, Dict, Optional
from urllib import request as urlrequest

REPO_ROOT = os.environ.get("ANIMICA_POS_REPO", "/root/animica")
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from core.pos import WORKTYPE_POS, build_pos_extra  # noqa: E402
from core.staking import slot_for_timestamp  # noqa: E402
from core.types.header import Header  # noqa: E402
from core.utils.address_codec import account_key_from_pubkey  # noqa: E402

RPC_URL = os.environ.get("ANIMICA_RPC_URL", "http://127.0.0.1:8545/rpc")
WALLET_LABEL = os.environ.get("ANIMICA_POS_WALLET_LABEL", "tresure")
WALLETS_PATH = os.environ.get("ANIMICA_POS_WALLETS_PATH", "/root/.animica/wallets.json")
TARGET_BLOCK_S = float(os.environ.get("ANIMICA_POS_TARGET_BLOCK_S", "60"))
DRY_RUN = os.environ.get("ANIMICA_POS_DRY_RUN", "").strip().lower() in {"1", "true", "yes"}
ONCE = os.environ.get("ANIMICA_POS_ONCE", "").strip().lower() in {"1", "true", "yes"}
RPC_TIMEOUT = float(os.environ.get("ANIMICA_POS_RPC_TIMEOUT", "90"))
# Set 0 to always mint coinbase-only blocks (fastest path to a moving head).
INCLUDE_MEMPOOL = os.environ.get(
    "ANIMICA_POS_INCLUDE_MEMPOOL", "1"
).strip().lower() in {"1", "true", "yes"}
# Budget for the mempool-inclusive template attempt. Must stay below the slot
# length: a template that arrives after its slot is useless, and waiting for one
# starves the coinbase-only fallback that actually advances the head.
MEMPOOL_TEMPLATE_TIMEOUT = float(
    os.environ.get("ANIMICA_POS_MEMPOOL_TEMPLATE_TIMEOUT", "40")
)

# ML-DSA-65. core.pos pins the PoS lane to this scheme; see its module docstring.
POS_SCHEME_ID = 11

log = logging.getLogger("animica.pos.minter")

# 32-byte header fields, rebuilt from the template's hex strings.
_HEADER_BYTES_FIELDS = (
    "parentHash", "stateRoot", "txsRoot", "receiptsRoot", "proofsRoot",
    "daRoot", "mixSeed", "poiesPolicyRoot", "pqAlgPolicyRoot",
)
_HEADER_INT_FIELDS = ("v", "chainId", "height", "timestamp", "thetaMicro", "workType", "nonce")


class MintError(RuntimeError):
    """A mint attempt could not be completed this slot."""


def _rpc(method: str, params: Any, *, timeout: float | None = None) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    req = urlrequest.Request(
        RPC_URL, data=body.encode(), headers={"content-type": "application/json"}
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout or RPC_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode() or "{}")
    except (OSError, ValueError) as exc:
        # A restarting or overloaded node resets the connection; that is an
        # ordinary missed slot, not a minter fault. Classifying it as MintError
        # keeps it out of the unexpected-error path (which backs off to two
        # minutes and would then miss slots long after the node recovered).
        raise MintError(f"{method}: {type(exc).__name__}: {exc}") from exc
    if isinstance(payload, dict) and payload.get("error"):
        raise MintError(f"{method}: {payload['error']}")
    return payload.get("result") if isinstance(payload, dict) else payload


def _unhex(v: Any) -> bytes:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, str):
        return bytes.fromhex(v[2:] if v.startswith("0x") else v)
    raise MintError(f"expected hex string, got {type(v).__name__}")


def _hex(b: bytes) -> str:
    return "0x" + bytes(b).hex()


def load_validator(label: str, path: str) -> Dict[str, Any]:
    """
    Load the minting keypair. The secret never leaves this process and is never
    logged — only the derived account key and public fingerprint are.
    """
    with open(path, "r") as fh:
        raw = json.load(fh)
    wallets = raw if isinstance(raw, list) else raw.get("wallets", raw)
    if isinstance(wallets, dict):
        wallets = list(wallets.values())
    matches = [
        w for w in wallets
        if isinstance(w, dict) and (w.get("label") or "") == label
    ]
    if not matches:
        raise MintError(f"no wallet labelled {label!r} in {path}")
    w = matches[0]
    if (w.get("alg_name") or "") != "ml_dsa_65":
        raise MintError(
            f"wallet {label!r} is {w.get('alg_name')!r}; the PoS lane accepts only ml_dsa_65"
        )
    pubkey = bytes.fromhex(w["public_key_hex"])
    secret = bytes.fromhex(w["secret_key_hex"])
    account_key = account_key_from_pubkey(pubkey, None)
    return {
        "label": label,
        "address": w.get("address", ""),
        "account_key": account_key,
        "pubkey": pubkey,
        "secret": secret,
    }


def header_from_template(tpl_header: Dict[str, Any]) -> Header:
    """Rebuild a Header object from the template's JSON header map."""
    kwargs: Dict[str, Any] = {}
    for f in _HEADER_BYTES_FIELDS:
        kwargs[f] = _unhex(tpl_header[f])
    for f in _HEADER_INT_FIELDS:
        kwargs[f] = int(tpl_header.get(f, 0) or 0)
    extra = tpl_header.get("extra", b"")
    kwargs["extra"] = _unhex(extra) if isinstance(extra, str) else bytes(extra or b"")
    return Header(**kwargs)


def header_to_wire(header: Header) -> Dict[str, Any]:
    """Serialize a Header back into the hex-string map submitBlock expects."""
    out: Dict[str, Any] = {f: _hex(getattr(header, f)) for f in _HEADER_BYTES_FIELDS}
    for f in _HEADER_INT_FIELDS:
        out[f] = int(getattr(header, f))
    out["extra"] = _hex(header.extra)
    return out


def mint_once(validator: Dict[str, Any]) -> Optional[str]:
    """
    One mint attempt. Returns the submitted block hash on success, None when
    there was simply nothing to do; raises MintError on a real failure.
    """
    from pq.py.algs import ml_dsa_65 as mldsa

    # Assembling a template over a large mempool executes every candidate tx and
    # can take minutes on a busy node — long enough to miss the slot entirely.
    # Fall back to a coinbase-only template rather than mint nothing: an empty
    # block still advances the head, and the mempool drains on later blocks.
    tpl = None
    for include_mempool in ([True, False] if INCLUDE_MEMPOOL else [False]):
        # The mempool attempt gets a SHORT budget: if it cannot beat the slot it
        # is worth nothing, and spending the full RPC timeout on it would block
        # the coinbase-only fallback that actually moves the head. The fallback
        # gets the full budget, because it is the attempt that must succeed.
        budget = min(RPC_TIMEOUT, MEMPOOL_TEMPLATE_TIMEOUT) if include_mempool else RPC_TIMEOUT
        try:
            tpl = _rpc(
                "miner.getBlockTemplate",
                {
                    "address": validator["address"],
                    "include_mempool": include_mempool,
                    "sync_peer_mempools": False,
                },
                timeout=budget,
            )
            break
        except Exception as exc:
            if not include_mempool:
                raise
            log.info(
                "mempool template failed in %.0fs (%s); retrying coinbase-only",
                budget, str(exc)[:120],
            )
    if not isinstance(tpl, dict):
        raise MintError("template response was not an object")
    if not tpl.get("enabled", True):
        log.info("template unavailable: %s", tpl.get("reason") or "unknown")
        return None

    header = header_from_template(tpl["header"])
    header = replace(header, workType=WORKTYPE_POS, nonce=0)

    slot = slot_for_timestamp(header.timestamp, TARGET_BLOCK_S)
    extra = build_pos_extra(
        header,
        staker=validator["account_key"],
        slot=slot,
        scheme=POS_SCHEME_ID,
        pubkey=validator["pubkey"],
        sign=lambda msg: mldsa.sign(validator["secret"], msg),
    )
    signed = replace(header, extra=extra)

    log.info(
        "minting height=%d slot=%d theta=%d txs=%d extra=%dB",
        signed.height, slot, signed.thetaMicro, len(tpl.get("txs") or []), len(extra),
    )
    if DRY_RUN:
        log.info("DRY RUN — not submitting")
        return None

    txs = []
    for entry in tpl.get("txs") or []:
        if isinstance(entry, dict) and entry.get("raw"):
            txs.append(entry["raw"])
        elif isinstance(entry, str):
            txs.append(entry)

    result = _rpc(
        "miner.submitBlock",
        [{
            "templateId": tpl.get("templateId"),
            "header": header_to_wire(signed),
            "txs": txs,
            "parentHash": _hex(signed.parentHash),
        }],
    )
    if isinstance(result, dict) and not result.get("accepted", True):
        raise MintError(f"block rejected: {result.get('reason') or result}")
    block_hash = (result or {}).get("hash") if isinstance(result, dict) else None
    log.info("BLOCK ACCEPTED height=%d hash=%s", signed.height, block_hash)
    return block_hash


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("ANIMICA_POS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        validator = load_validator(WALLET_LABEL, WALLETS_PATH)
    except Exception as exc:
        log.error("cannot load validator key: %s", exc)
        return 2

    log.info(
        "PoS minter up: label=%s account=%s slot=%.0fs rpc=%s%s",
        validator["label"], validator["account_key"].hex()[:16],
        TARGET_BLOCK_S, RPC_URL, " [DRY RUN]" if DRY_RUN else "",
    )

    backoff = 5.0
    while True:
        started = time.time()
        minted = False
        try:
            mint_once(validator)
            minted = True
            backoff = 5.0
        except MintError as exc:
            # Losing a slot is normal: not the leader, stale template, node
            # restarting, RPC busy. Retry inside the slot rather than waiting a
            # full one — we have not minted yet, so there is still work to do.
            log.info("mint attempt did not land: %s", str(exc)[:200])
        except Exception:
            log.exception("unexpected minter error")
            backoff = min(backoff * 2, 120.0)
            time.sleep(backoff)
            continue
        if ONCE:
            return 0
        elapsed = time.time() - started
        if minted:
            # Align to the next slot rather than drifting by attempt cost.
            time.sleep(max(2.0, TARGET_BLOCK_S - elapsed))
        else:
            time.sleep(max(5.0, min(15.0, TARGET_BLOCK_S - elapsed)))


if __name__ == "__main__":
    raise SystemExit(main())
