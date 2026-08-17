"""BC3 (BitcoinIII) proof-of-work support for the Animica miner.

Animica and BC3 use different hashes, so a single hash can never satisfy both:

    Animica  sha3_256(CBOR(header))          ~525 bytes, ONE round
    BC3      sha3_256^3(header80)            80 bytes,  THREE rounds

This module gives the miner the BC3 side so one rig can work both chains from
one pool connection. Every constant here was verified against the live BC3
chain (block 57,687) rather than taken from documentation:

    sha3_256(sha3_256(sha3_256(header80)))[::-1].hex() == block hash
    merkle rebuilt with SHA-256d                       == header merkleroot
    BIP34 scriptSig prefix                             == 022076 / 0357e100

THE TRAP: BC3 selects its PoW per block from a version bit —
``if (nVersion & SHA3_VBIT) GetSHA3_256dHash() else GetSHA256dHash()`` — and the
bit is height-gated by ``consensus.SHA3Height`` (mainnet 30,240; testnet, signet
and regtest 2016). Set it below the height and the node rejects the block with
``bad-version-bits, bit 12 set``; leave it clear at/above and the node hashes
with SHA-256d and rejects a perfectly good SHA3 solution as ``high-hash``. Both
were observed before this was written. ``pow_hash`` therefore takes the version
and decides, instead of assuming SHA3.
"""
from __future__ import annotations

import hashlib
import struct
from typing import List, Optional, Tuple

__all__ = [
    "SHA3_VBIT",
    "SHA3_HEIGHT_MAIN",
    "SHA3_HEIGHT_TEST",
    "sha256d",
    "sha3_256t",
    "pow_hash",
    "swap_words",
    "target_from_nbits",
    "target_from_diff",
    "Bc3Job",
]

SHA3_VBIT = 0x1000
SHA3_HEIGHT_MAIN = 30_240
SHA3_HEIGHT_TEST = 2016

# Bitcoin difficulty-1 target; BC3 keeps Bitcoin's nBits semantics unchanged.
DIFF1 = 0x00000000FFFF0000000000000000000000000000000000000000000000000000


def sha256d(b: bytes) -> bytes:
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def sha3_256t(b: bytes) -> bytes:
    """BC3's "SHA3-256t": SHA3-256 applied three times."""
    d = hashlib.sha3_256(b).digest()
    d = hashlib.sha3_256(d).digest()
    return hashlib.sha3_256(d).digest()


def pow_hash(header80: bytes, version: int) -> bytes:
    """Hash a BC3 header with whichever PoW its version bit selects."""
    return sha3_256t(header80) if (version & SHA3_VBIT) else sha256d(header80)


def swap_words(b: bytes) -> bytes:
    """Reverse each 4-byte word — the stratum prevhash convention.

    Self-inverse: applying it to the value the pool sends yields the internal
    little-endian prevhash the header wants. Reversing again puts it back into
    display order and silently breaks every share.
    """
    if len(b) % 4:
        raise ValueError("length must be a multiple of 4")
    return b"".join(b[i:i + 4][::-1] for i in range(0, len(b), 4))


def target_from_nbits(nbits: int) -> int:
    exp, mant = nbits >> 24, nbits & 0x007FFFFF
    return mant >> (8 * (3 - exp)) if exp <= 3 else mant << (8 * (exp - 3))


def target_from_diff(diff: float) -> int:
    return int(DIFF1 / (diff if diff > 0 else 1e-9))


class Bc3Job:
    """A BC3 stratum job, assembled from a standard ``mining.notify`` payload."""

    __slots__ = ("job_id", "prevhash_le", "coinb1", "coinb2", "branch",
                 "version", "nbits", "ntime", "clean", "target", "block_target",
                 "_pre")

    def __init__(self, params: List, extranonce1: bytes, share_diff: float) -> None:
        (self.job_id, prevhash, coinb1, coinb2,
         branch, version, nbits, ntime, self.clean) = params[:9]
        self.prevhash_le = swap_words(bytes.fromhex(prevhash))
        self.coinb1 = bytes.fromhex(coinb1)
        self.coinb2 = bytes.fromhex(coinb2)
        self.branch = [bytes.fromhex(b)[::-1] for b in branch]
        self.version = int(version, 16)
        self.nbits = int(nbits, 16)
        self.ntime = int(ntime, 16)
        # Two SEPARATE thresholds. The share target is the easy one the pool
        # accepts; the block target is the hard one the network requires. Do NOT
        # combine them with min() — min picks the numerically smaller value,
        # i.e. the HARDER target, which makes the miner demand a full block and
        # never report a share.
        self.target = target_from_diff(share_diff)
        self.block_target = target_from_nbits(self.nbits)
        self._pre = extranonce1

    def is_block(self, digest: bytes) -> bool:
        """True when a digest also satisfies the network (block) target."""
        return int.from_bytes(digest[::-1], "big") <= self.block_target

    def coinbase_txid(self, en2: bytes) -> bytes:
        """txid commits to the LEGACY (witness-stripped) serialization."""
        full = self.coinb1 + self._pre + en2 + self.coinb2
        ver, rest = full[:4], full[6:]                 # drop segwit marker+flag
        w = rest.rfind(b"\x01\x20" + b"\x00" * 32)     # witness reserved value
        return sha256d(ver + rest[:w] + rest[w + 34:])

    def merkle_root(self, en2: bytes) -> bytes:
        h = self.coinbase_txid(en2)
        for b in self.branch:
            h = sha256d(h + b)
        return h

    def header(self, en2: bytes, ntime: int, nonce: int) -> bytes:
        return (struct.pack("<i", self.version)
                + self.prevhash_le
                + self.merkle_root(en2)
                + struct.pack("<I", ntime)
                + struct.pack("<I", self.nbits)
                + struct.pack("<I", nonce))

    def scan(self, en2: bytes, start: int, count: int,
             ntime: Optional[int] = None) -> Optional[Tuple[int, bytes]]:
        """Grind ``count`` nonces. Returns (nonce, digest) or None.

        The merkle root and everything left of ntime are constant for a given
        extranonce2, so they are computed once and only the last 8 bytes change.
        """
        t = ntime if ntime is not None else self.ntime
        pre = (struct.pack("<i", self.version) + self.prevhash_le
               + self.merkle_root(en2) + struct.pack("<I", t)
               + struct.pack("<I", self.nbits))
        sha3 = bool(self.version & SHA3_VBIT)
        tgt = self.target
        for nonce in range(start, start + count):
            hdr = pre + struct.pack("<I", nonce)
            d = sha3_256t(hdr) if sha3 else sha256d(hdr)
            if int.from_bytes(d[::-1], "big") <= tgt:
                return nonce, d
        return None
