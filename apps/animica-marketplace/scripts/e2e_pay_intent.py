#!/usr/bin/env python3
"""Store-acceptance helper: pay ONE purchase intent from a CLI wallet with the
ANMSTORE1 memo as the transfer's data bytes.

Mirrors post_anchor_tx.py's use of the `animica tx send` internals exactly
(the CLI has no --data flag), so nonce handling, fees, signing, and broadcast
are byte-identical to a production wallet payment. Fail-closed: refuses any
memo that is not an ANMSTORE1 payload and any value above 3 ANM."""
import json
import os
import sys

sys.path.insert(0, "/root/animica/python")

INTENT = json.load(open(sys.argv[1]))
from_address = sys.argv[2]

to_address = INTENT["payTo"]
value = int(INTENT["amountNanm"])
payload = bytes.fromhex(INTENT["memoHex"])
assert payload.startswith(b"ANMSTORE1"), "memo magic mismatch"
assert 0 < value <= 3_000_000_000, "unexpected value"

from animica.cli import tx as txcli
from animica.tx.signing import pq_sign_tx, pq_verify_tx

rpc_url = "http://127.0.0.1:8545/rpc"
resolution = txcli._get_chain_identity(rpc_url)
chain_identity = resolution.identity
cid = int(chain_identity.get("chainId"))
chain_ctx = txcli._chain_context_from_identity(
    chain_identity, chain_id=cid, domain=txcli.DEFAULT_DOMAIN, prehash=txcli.DEFAULT_PREHASH,
)

w = txcli._load_wallet_entry(from_address)
used_alg_id = int(w.get("alg_id") or w.get("algId") or 0x1003)
pk = txcli._hex_to_bytes(str(w.get("public_key_hex") or w.get("publicKeyHex") or ""))
sk = txcli._hex_to_bytes(str(w.get("secret_key_hex") or w.get("secretKeyHex") or ""))
if not pk or not sk:
    raise RuntimeError("buyer wallet entry missing key material")

quote_limit, quote_price = txcli._estimate_fee_quote(rpc_url)
gas_limit = quote_limit if quote_limit is not None else 21000
max_fee = quote_price if quote_price is not None else txcli._get_default_max_fee(rpc_url)
valid_after, valid_until = txcli._resolve_validity_window(
    rpc_url, valid_from=None, valid_until=None, ttl_blocks=None,
    head_height_hint=None, verbose=False,
)

with txcli._nonce_lock(from_address):
    nonce = txcli._next_nonce(rpc_url, from_address, refresh=True, verbose=False)
    body = txcli._build_tx_body(
        chain_id=cid,
        from_addr=from_address,
        to_addr=to_address,
        nonce=nonce,
        value_base_units=value,
        gas_limit=int(gas_limit),
        max_fee=int(max_fee),
        data=payload,
        valid_after=valid_after,
        valid_until=valid_until,
        salt=os.urandom(16),
    )
    pq = pq_sign_tx(body, sk, pk, used_alg_id, chain_ctx)
    vr = pq_verify_tx(body, pq, pk, chain_ctx, from_addr=from_address)
    if not vr.ok:
        raise RuntimeError(f"local PQ verify failed before broadcast: {vr.reason}")
    raw = txcli._build_raw_tx(
        body=body, alg_id=pq.alg_id, pk=pk, sig=pq.sig,
        domain=txcli.DEFAULT_DOMAIN, prehash=txcli.DEFAULT_PREHASH, chain_id=cid,
    )
    result = txcli._rpc(rpc_url, "tx.sendRawTransaction", ["0x" + raw.hex()])

tx_hash = result if isinstance(result, str) else next(
    (result[k] for k in ("tx_hash", "hash", "txHash", "transactionHash") if isinstance(result.get(k), str)), None,
)
if not tx_hash:
    raise RuntimeError(f"unexpected sendRawTransaction result: {result!r}")
print(json.dumps({"txid": tx_hash, "from": from_address, "to": to_address, "value": value, "nonce": nonce}))
