#!/usr/bin/env python
"""Regenerate the golden vectors in test/tx_golden_vectors_test.dart.

Run from the repo root with the node's own interpreter, so the vectors come
from the code that actually verifies transactions rather than from a
re-implementation:

    cd /root/animica && .venv/bin/python \
        apps/wallet-mobile-flutter/test/golden/gen_tx_vectors.py

Every value printed is produced by:
  core.utils.tx.normalize_tx_body      (what the node puts at preimage key 7)
  animica.tx.signing.tx_signing_preimage
  core.encoding.cbor.dumps             (canonical CBOR)
  pq.py.sign.build_sign_bytes          (the bytes ML-DSA-65 signs)

The assertions below are the point of the file as much as the output is: they
prove `normalize_tx_body` is an IDENTITY passthrough for the canonical bodies
the wallet now emits (so the wallet's preimage == the node's preimage), and
that it was NOT for the flat `{to,data,from,…}` bodies the wallet used to emit.
"""

import hashlib
import json

from animica.tx.signing import ChainContext, tx_signing_preimage
from core.encoding.cbor import dumps as canonical_cbor
from core.utils.tx import _pad_addr, normalize_tx_body
from pq.py.address import address_from_pubkey
from pq.py.sign import build_sign_bytes

ALG = 0x1003  # ML-DSA-65
FROM = address_from_pubkey(b"\x11" * 1952, ALG)
TO = address_from_pubkey(b"\x22" * 1952, ALG)
GENESIS = bytes.fromhex(
    "8f2c1d4b6a90e3f57c81b24d0e6a9f3b5d7c8e1a2b4c6d8e0f1a3b5c7d9e0f21"
)
NETWORK = "unknown"  # mainnet's identity has no `network` key
CHAIN_ID = 1
FORK_ID = None
NONCE = 7
AMOUNT = 1234

MEMO = (
    b'ANMSTORE1{"v":1,"pid":"pur_abc123","b":"anim1source",'
    b'"l":"lst_x","a":1234,"n":"00112233445566778899aabbccddeeff"}'
)
CALLDATA = bytes.fromhex("a9059cbb" + "33" * 32 + "00" * 24 + "0000000005f5e100")
CODE = b"# animica python-vm package\nprint('hi')\n"
MANIFEST = b'{"name":"demo","version":"1.0.0","entry":"main"}'

CTX = ChainContext(
    chain_id=CHAIN_ID, genesis_hash=GENESIS, network=NETWORK, fork_id=FORK_ID
)


def canonical(kind: str) -> dict:
    base = {
        "v": 1,
        "chainId": CHAIN_ID,
        "from": _pad_addr(FROM),
        "gas": {"price": 1_000_000_000, "limit": 21000},
        "accessList": [],
        "nonce": NONCE,
    }
    if kind == "transfer":
        base["payload"] = {"t": 0, "v": {"to": _pad_addr(TO), "amount": AMOUNT, "data": b""}}
    elif kind == "transfer_memo":
        base["payload"] = {"t": 0, "v": {"to": _pad_addr(TO), "amount": AMOUNT, "data": MEMO}}
    elif kind == "call":
        base["gas"] = {"price": 1_000_000_000, "limit": 200_000}
        base["payload"] = {"t": 2, "v": {"to": _pad_addr(TO), "data": CALLDATA}}
    elif kind == "deploy":
        base["gas"] = {"price": 1_000_000_000, "limit": 2_000_000}
        base["payload"] = {"t": 1, "v": {"code": CODE, "manifest": MANIFEST}}
    else:
        raise AssertionError(kind)
    return base


out: dict = {"from": FROM, "to": TO, "genesis": GENESIS.hex(), "kinds": {}}

for kind in ("transfer", "transfer_memo", "call", "deploy"):
    body = canonical(kind)
    norm = normalize_tx_body(body)
    assert canonical_cbor(norm) == canonical_cbor(
        body
    ), f"{kind}: normalize_tx_body is NOT an identity passthrough"

    preimage = tx_signing_preimage(body, CTX)
    manual = canonical_cbor(
        {
            1: "animica.tx.v1",
            2: CHAIN_ID,
            3: GENESIS,
            4: NETWORK,
            5: "tx",
            6: 1,
            7: norm,
        }
    )
    assert manual == preimage, f"{kind}: preimage layout drifted"

    out["kinds"][kind] = {
        "bodyCbor": canonical_cbor(body).hex(),
        "preimage": preimage.hex(),
        "preimageSha3_512": hashlib.sha3_512(preimage).digest().hex(),
        "signBytes": build_sign_bytes(
            preimage,
            domain="tx",
            chain_id=CHAIN_ID,
            fork_id=FORK_ID,
            alg_id=ALG,
            prehash="sha3-512",
        ).hex(),
    }

# The two defects this test suite exists to prevent, restated as assertions.
flat = {
    "to": TO,
    "data": b"",
    "from": FROM,
    "nonce": NONCE,
    "value": AMOUNT,
    "maxFee": 1_000_000_000,
    "chainId": CHAIN_ID,
    "gasLimit": 21000,
}
# (1) A flat body placed at key 7 verbatim -- what a Dart preimage builder with
#     no normalizer produces -- is NOT the node's preimage.
flat_at_7 = canonical_cbor(
    {1: "animica.tx.v1", 2: CHAIN_ID, 3: GENESIS, 4: NETWORK, 5: "tx", 6: 1, 7: flat}
)
assert flat_at_7 != bytes.fromhex(out["kinds"]["transfer"]["preimage"])
# (2) ...because the node normalizes it first, into exactly the canonical body
#     the wallet now builds itself.
assert canonical_cbor(normalize_tx_body(flat)) == bytes.fromhex(
    out["kinds"]["transfer"]["bodyCbor"]
)

print(json.dumps(out, indent=2))
