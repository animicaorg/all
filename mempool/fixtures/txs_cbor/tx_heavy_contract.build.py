from hashlib import sha3_256


# --- tiny CBOR encoder (definite lengths, what we need) -----------------------
def _hdr(major, ai):
    return bytes([(major << 5) | ai])


def _n(ai):
    return _hdr(0, ai)


def cbor_uint(n: int) -> bytes:
    assert n >= 0
    if n < 24:
        return _n(n)
    if n < 256:
        return _hdr(0, 24) + n.to_bytes(1, "big")
    if n < 65536:
        return _hdr(0, 25) + n.to_bytes(2, "big")
    if n < 4294967296:
        return _hdr(0, 26) + n.to_bytes(4, "big")
    return _hdr(0, 27) + n.to_bytes(8, "big")


def cbor_bytes(b: bytes) -> bytes:
    l = len(b)
    if l < 24:
        h = _hdr(2, l)
    elif l < 256:
        h = _hdr(2, 24) + bytes([l])
    elif l < 65536:
        h = _hdr(2, 25) + l.to_bytes(2, "big")
    elif l < 4294967296:
        h = _hdr(2, 26) + l.to_bytes(4, "big")
    else:
        h = _hdr(2, 27) + l.to_bytes(8, "big")
    return h + b


def cbor_text(s: str) -> bytes:
    b = s.encode("utf-8")
    l = len(b)
    if l < 24:
        h = _hdr(3, l)
    elif l < 256:
        h = _hdr(3, 24) + bytes([l])
    elif l < 65536:
        h = _hdr(3, 25) + l.to_bytes(2, "big")
    elif l < 4294967296:
        h = _hdr(3, 26) + l.to_bytes(4, "big")
    else:
        h = _hdr(3, 27) + l.to_bytes(8, "big")
    return h + b


def cbor_null() -> bytes:
    return bytes([0xF6])


def cbor_array(xs) -> bytes:
    l = len(xs)
    if l < 24:
        h = _hdr(4, l)
    elif l < 256:
        h = _hdr(4, 24) + bytes([l])
    elif l < 65536:
        h = _hdr(4, 25) + l.to_bytes(2, "big")
    else:
        h = _hdr(4, 26) + l.to_bytes(4, "big")
    return h + b"".join(xs)


def cbor_map(d: dict) -> bytes:
    # Sort keys lexicographically for determinism
    items = sorted(d.items(), key=lambda kv: kv[0])
    l = len(items)
    if l < 24:
        h = _hdr(5, l)
    elif l < 256:
        h = _hdr(5, 24) + bytes([l])
    elif l < 65536:
        h = _hdr(5, 25) + l.to_bytes(2, "big")
    else:
        h = _hdr(5, 26) + l.to_bytes(4, "big")
    body = b""
    for k, v in items:
        body += cbor_text(k)
        body += to_cbor(v)
    return h + body


def to_cbor(x) -> bytes:
    if x is None:
        return cbor_null()
    if isinstance(x, bool):
        return bytes([0xF5 if x else 0xF4])
    if isinstance(x, int):
        return cbor_uint(x) if x >= 0 else None  # not needed
    if isinstance(x, bytes):
        return cbor_bytes(x)
    if isinstance(x, str):
        return cbor_text(x)
    if isinstance(x, list):
        return cbor_array([to_cbor(i) for i in x])
    if isinstance(x, dict):
        return cbor_map(x)
    raise TypeError(f"unsupported: {type(x)}")


# --- construct a heavy deploy tx ---------------------------------------------
FROM = bytes.fromhex("aa" * 20)  # 0xAA… address (20 bytes)
# Deterministic ~64KiB payload using repeated SHA3-256 expansions
seed = b"animica/heavy-contract-payload/v1"
chunks = []
cur = sha3_256(seed).digest()
target_len = 64 * 1024
while len(b"".join(chunks)) < target_len:
    chunks.append(cur)
    cur = sha3_256(cur).digest()
DATA = b"ANIMICA\x00DEPLOY\x00" + b"".join(chunks)
DATA = DATA[:target_len]  # exact

tx = {
    "accessList": [],
    "chainId": 1337,
    "data": DATA,
    "from": FROM,
    "gasLimit": 10_000_000,
    "gasPrice": 2000,
    "nonce": 2,
    "sig": None,
    "to": None,  # deploy
    "value": 0,
}

out = to_cbor(tx)

import hashlib
# Write file
import os
import pathlib

p = pathlib.Path(
    os.path.expanduser("~/animica/mempool/fixtures/txs_cbor/tx_heavy_contract.cbor")
)
p.write_bytes(out)

# Print a tiny manifest for sanity
print("wrote:", p)
print("bytes:", len(out))
print("sha256:", hashlib.sha256(out).hexdigest())
