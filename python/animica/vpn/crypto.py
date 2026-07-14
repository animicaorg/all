"""
Wallet crypto for the dVPN client/exit: ML-DSA-65 signing + anim1 address derivation,
byte-for-byte compatible with the marketplace (apps/animica-marketplace lib/wallet-verify.ts
and lib/address.ts). Verified bidirectionally: Python-signed messages verify under
@noble/post-quantum, and Python-encoded addresses pass the marketplace's bech32m + address
binding checks.

  * signature scheme : ML-DSA-65 (FIPS 204, algId 0x1003)
  * message domain   : "animica:signMessage:" + <message>
  * address          : bech32m("anim", algId(2B BE=0x1003) || sha3_256(pubkey)(32B))
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass

from . import SIGN_DOMAIN

# Vendored pure-Python ML-DSA-65 (same FIPS-204 impl used across the node).
from animica._vendor.dilithium_py_v2.ml_dsa import ML_DSA_65  # noqa: E402

ALG_ID_ML_DSA_65 = 0x1003
_HRP = "anim"

# ------------------------------------------------------------------ bech32m (dep-free)
_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
_CHARREV = {c: i for i, c in enumerate(_CHARSET)}
_GEN = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
_BECH32M_CONST = 0x2bc830a3


def _polymod(values: list[int]) -> int:
    chk = 1
    for v in values:
        top = chk >> 25
        chk = ((chk & 0x1ffffff) << 5) ^ v
        for i in range(5):
            chk ^= _GEN[i] if ((top >> i) & 1) else 0
    return chk


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _convertbits(data: list[int], frm: int, to: int, pad: bool) -> list[int]:
    acc = 0
    bits = 0
    ret: list[int] = []
    maxv = (1 << to) - 1
    for b in data:
        acc = (acc << frm) | b
        bits += frm
        while bits >= to:
            bits -= to
            ret.append((acc >> bits) & maxv)
    if pad and bits:
        ret.append((acc << (to - bits)) & maxv)
    return ret


def _bech32m_encode(hrp: str, payload: bytes) -> str:
    data = _convertbits(list(payload), 8, 5, True)
    pm = _polymod(_hrp_expand(hrp) + data + [0] * 6) ^ _BECH32M_CONST
    chk = [(pm >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_CHARSET[d] for d in data + chk)


# ------------------------------------------------------------------ public API

def address_from_pubkey(pubkey: bytes) -> str:
    digest = hashlib.sha3_256(pubkey).digest()
    payload = bytes([(ALG_ID_ML_DSA_65 >> 8) & 0xFF, ALG_ID_ML_DSA_65 & 0xFF]) + digest
    return _bech32m_encode(_HRP, payload)


def sign(secret_key: bytes, message: bytes) -> bytes:
    return ML_DSA_65.sign(secret_key, message)


def verify(pubkey: bytes, message: bytes, signature: bytes) -> bool:
    try:
        return bool(ML_DSA_65.verify(pubkey, message, signature))
    except Exception:
        return False


def sign_login(secret_key: bytes, challenge: str) -> bytes:
    """Sign a marketplace challenge exactly as lib/wallet-verify.ts expects."""
    return sign(secret_key, (SIGN_DOMAIN + challenge).encode())


@dataclass
class Wallet:
    secret_key: bytes
    public_key: bytes
    address: str

    @classmethod
    def generate(cls) -> "Wallet":
        pk, sk = ML_DSA_65.keygen()
        return cls(secret_key=sk, public_key=pk, address=address_from_pubkey(pk))

    def sign_login(self, challenge: str) -> str:
        return self.sign_login_bytes(challenge).hex()

    def sign_login_bytes(self, challenge: str) -> bytes:
        return sign_login(self.secret_key, challenge)

    @property
    def public_key_hex(self) -> str:
        return self.public_key.hex()

    # --- persistence: a 0600 JSON key file (v1; AES-GCM vault is a future upgrade) ---
    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), mode=0o700, exist_ok=True)
        blob = json.dumps({
            "scheme": "ml_dsa_65", "algId": ALG_ID_ML_DSA_65, "address": self.address,
            "publicKey": self.public_key.hex(), "secretKey": self.secret_key.hex(),
        })
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, blob.encode())
        finally:
            os.close(fd)

    @classmethod
    def load(cls, path: str) -> "Wallet":
        with open(path) as f:
            d = json.load(f)
        pk = bytes.fromhex(d["publicKey"])
        sk = bytes.fromhex(d["secretKey"])
        return cls(secret_key=sk, public_key=pk, address=d.get("address") or address_from_pubkey(pk))

    @classmethod
    def load_or_create(cls, path: str) -> "Wallet":
        if os.path.exists(path):
            return cls.load(path)
        w = cls.generate()
        w.save(path)
        return w
