#!/root/animica/.venv/bin/python
"""Post ONE ANMLIC1 license-anchor data-tx from the store treasury.

Called by scripts/license-anchor-worker.ts (STORE_ANCHOR_POST=1 gate lives THERE; this
helper additionally refuses to broadcast without --post). Clones the proven recipe of
python/animica/cli/settle.py prepare_anchor_tx / broadcast_anchor: the exact
`animica tx send` pipeline (_get_chain_identity -> _load_wallet_entry -> _next_nonce ->
_build_tx_body(data=payload) -> pq_sign_tx + pq_verify_tx -> _build_raw_tx), because the
CLI exposes no --data flag. The tx is a 1-nANM self-transfer whose data bytes are:

    ANMLIC1|{"v":1,"seq":N,"root":"<64-hex>","prev":"<txid|''>","n":<count>}

(byte-identical to lib/license.ts buildLicenseAnchorPayload — canonical compact JSON,
exact key order). To every node this is inert opaque data (same as ANMSETL1/DomainAnchor);
tamper-evidence comes from the live txsRoot. Zero consensus change.

Output: ONE final stdout line of JSON — {"ok":true,"txid":"0x..."} or
{"ok":false,"error":"..."}. Without --post nothing is broadcast (the signed raw tx is
prepared and reported, exit 0) — safe default for humans poking at it.

Keys: the store treasury entry must exist in ANIMICA_WALLETS_FILE (~/.animica/wallets.json,
minted via `animica wallet create` like lib/deposit.ts does); encrypted-at-rest secrets are
unlocked via ANIMICA_WALLET_PASSPHRASE (ANM-C07). Nothing secret is ever printed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# scripts/ -> animica-marketplace -> apps -> repo root. Prefer the repo working tree over a
# possibly-lagging installed package (memory: PyPI builds lag GitHub/worktree).
REPO = Path(__file__).resolve().parents[3]
for p in (str(REPO), str(REPO / "python")):
    if p not in sys.path:
        sys.path.insert(0, p)

MAGIC = b"ANMLIC1|"
MAX_PAYLOAD = 300  # matches lib/license.ts LICENSE_ANCHOR_MAX_BYTES (mempool cap is 1 KiB)
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def build_payload(seq: int, root: str, prev: str, count: int) -> bytes:
    """Strict encode; raises on anything lib/license.ts parseLicenseAnchorPayload would
    reject — this helper must never post a dud anchor (ANMSETL1 encode discipline)."""
    root = root.lower()
    prev = (prev or "").lower().removeprefix("0x")
    if not (isinstance(seq, int) and 1 <= seq <= 1_000_000_000):
        raise ValueError(f"bad seq {seq}")
    if not HEX64.match(root):
        raise ValueError("bad root (need 64-hex sha3-256)")
    if prev != "" and not HEX64.match(prev):
        raise ValueError("bad prev txid (need 64-hex or empty)")
    if not (isinstance(count, int) and 1 <= count <= 1_000_000):
        raise ValueError(f"bad leaf count {count}")
    payload = MAGIC + json.dumps(
        {"v": 1, "seq": seq, "root": root, "prev": prev, "n": count},
        separators=(",", ":"),
    ).encode("utf-8")
    if len(payload) > MAX_PAYLOAD:
        raise ValueError(f"payload {len(payload)} bytes > {MAX_PAYLOAD}")
    return payload


def prepare_and_maybe_post(
    *, from_address: str, payload: bytes, rpc_url: str, post: bool
) -> dict:
    """settle.py prepare_anchor_tx, generalized to (from_address, payload). Everything
    fallible happens before broadcast; the ONLY on-chain side effect is behind `post`."""
    import os

    from animica.cli import tx as txcli
    from animica.tx.signing import pq_sign_tx, pq_verify_tx

    head_hint = txcli._ensure_node_ready_for_tx(rpc_url)
    resolution = txcli._get_chain_identity(rpc_url)
    chain_identity = resolution.identity
    cid = int(chain_identity.get("chainId"))
    chain_ctx = txcli._chain_context_from_identity(
        chain_identity,
        chain_id=cid,
        domain=txcli.DEFAULT_DOMAIN,
        prehash=txcli.DEFAULT_PREHASH,
    )

    w = txcli._load_wallet_entry(from_address)
    used_alg_id = int(w.get("alg_id") or w.get("algId") or 0x1003)
    pk = txcli._hex_to_bytes(str(w.get("public_key_hex") or w.get("publicKeyHex") or ""))
    sk = txcli._hex_to_bytes(str(w.get("secret_key_hex") or w.get("secretKeyHex") or ""))
    if not pk or not sk:
        raise RuntimeError("store treasury wallet entry missing key material")

    quote_limit, quote_price = txcli._estimate_fee_quote(rpc_url)
    gas_limit = quote_limit if quote_limit is not None else 21000
    max_fee = quote_price if quote_price is not None else txcli._get_default_max_fee(rpc_url)
    valid_after, valid_until = txcli._resolve_validity_window(
        rpc_url,
        valid_from=None,
        valid_until=None,
        ttl_blocks=None,
        head_height_hint=head_hint,
        verbose=False,
    )

    # Same cross-process nonce lock `animica tx send` holds while it picks + submits.
    with txcli._nonce_lock(from_address):
        nonce = txcli._next_nonce(rpc_url, from_address, refresh=True, verbose=False)
        body = txcli._build_tx_body(
            chain_id=cid,
            from_addr=from_address,
            to_addr=from_address,  # self-transfer: the anchor's value is symbolic
            nonce=nonce,
            value_base_units=1,  # 1 nANM
            gas_limit=int(gas_limit),
            max_fee=int(max_fee),
            data=payload,  # <- the ANMLIC1 anchor payload
            valid_after=valid_after,
            valid_until=valid_until,
            salt=os.urandom(16),
        )
        pq = pq_sign_tx(body, sk, pk, used_alg_id, chain_ctx)
        vr = pq_verify_tx(body, pq, pk, chain_ctx, from_addr=from_address)
        if not vr.ok:
            raise RuntimeError(f"local PQ verify failed before broadcast: {vr.reason}")
        raw = txcli._build_raw_tx(
            body=body,
            alg_id=pq.alg_id,
            pk=pk,
            sig=pq.sig,
            domain=txcli.DEFAULT_DOMAIN,
            prehash=txcli.DEFAULT_PREHASH,
            chain_id=cid,
        )
        raw_hex = "0x" + raw.hex()

        if not post:
            return {"ok": True, "posted": False, "raw_bytes": len(raw), "payload_bytes": len(payload)}

        result = txcli._rpc(rpc_url, "tx.sendRawTransaction", [raw_hex])

    tx_hash = None
    if isinstance(result, str):
        tx_hash = result
    elif isinstance(result, dict):
        for key in ("tx_hash", "hash", "txHash", "transactionHash"):
            if isinstance(result.get(key), str):
                tx_hash = result[key]
                break
    if not tx_hash:
        raise RuntimeError(f"unexpected tx.sendRawTransaction result: {result!r}")
    return {"ok": True, "posted": True, "txid": tx_hash}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from-address", required=True, help="store treasury anim1... (key in wallets.json)")
    ap.add_argument("--seq", type=int, required=True)
    ap.add_argument("--root", required=True, help="64-hex license merkle root")
    ap.add_argument("--prev", default="", help="txid of the previous anchor ('' for the first)")
    ap.add_argument("--count", type=int, required=True, help="leaf count")
    ap.add_argument("--rpc-url", default="http://127.0.0.1:8545/rpc")
    ap.add_argument("--post", action="store_true", help="actually broadcast (default: prepare only)")
    args = ap.parse_args()

    try:
        payload = build_payload(args.seq, args.root, args.prev, args.count)
        out = prepare_and_maybe_post(
            from_address=args.from_address,
            payload=payload,
            rpc_url=args.rpc_url,
            post=args.post,
        )
        print(json.dumps(out))
        return 0
    except Exception as exc:  # noqa: BLE001 — reported as structured output, never a trace to stdout
        print(json.dumps({"ok": False, "error": str(exc)[:500]}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
