"""BC3 (BitcoinIII) job manager and share validator for the Animica pool.

Runs alongside Animica mining on the same stratum listeners. BC3's proof-of-work
is ``sha3_256^3`` over an 80-byte Bitcoin header, while Animica's is
``sha3_256(CBOR(header))`` — one hash can never satisfy both, so this is separate
work carried on the same connection, not merged mining.

BC3 block rewards are paid to the Animica foundation address configured in
``BC3_PAYOUT_ADDRESS``; miners are credited nothing for BC3 work, so nothing here
touches the Animica payout ledger and it cannot create an obligation.

Every constant was verified against the live BC3 chain before this shipped — see
``animica.mining.bc3`` for the version-bit trap (bit 12, height-gated) and the
BIP34 ``OP_N`` encoding rule, both of which silently produce zero valid work when
wrong.

SAFETY: every entry point is exception-isolated. A BC3 node that is down, wedged
or serving garbage must never disturb Animica mining, which is the pool's actual
business.
"""
from __future__ import annotations

import asyncio
import binascii
import json
import logging
import os
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

try:  # packaged path
    from mining.bc3 import (SHA3_VBIT, SHA3_HEIGHT_MAIN, SHA3_HEIGHT_TEST,
                            pow_hash, sha256d, swap_words, target_from_diff,
                            target_from_nbits)
except Exception:  # pragma: no cover - repo-layout fallback
    from animica.mining.bc3 import (  # type: ignore[no-redef]
        SHA3_VBIT, SHA3_HEIGHT_MAIN, SHA3_HEIGHT_TEST, pow_hash, sha256d,
        swap_words, target_from_diff, target_from_nbits)

log = logging.getLogger("animica.stratum_pool.bc3")

EXTRANONCE2_SIZE = 4


def _script_num_push(n: int) -> bytes:
    """Reproduce ``CScript() << n`` exactly for the BIP34 height.

    OP_0 for 0 and OP_1..OP_16 for 1..16 — single opcodes, NOT data pushes.
    Emitting a push for those heights is rejected as ``bad-cb-height``; it
    passes on a mature chain and fails on a young one, so it is easy to miss.
    """
    if n == 0:
        return b"\x00"
    if 1 <= n <= 16:
        return bytes([0x50 + n])
    v = b""
    a = n
    while a:
        v += bytes([a & 0xFF])
        a >>= 8
    if v[-1] & 0x80:
        v += b"\x00"
    return bytes([len(v)]) + v


def _varint(n: int) -> bytes:
    if n < 0xFD:
        return bytes([n])
    if n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    if n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    return b"\xff" + struct.pack("<Q", n)


class Bc3Rpc:
    """Minimal JSON-RPC client for bitcoinIIId using cookie auth."""

    def __init__(self, url: str, cookie: str) -> None:
        self._url, self._cookie, self._id = url, cookie, 0

    async def call(self, method: str, params: Optional[List[Any]] = None) -> Any:
        import urllib.request
        self._id += 1
        body = json.dumps({"jsonrpc": "1.0", "id": self._id,
                           "method": method, "params": params or []}).encode()
        with open(self._cookie, "r") as fh:
            auth = binascii.b2a_base64(fh.read().strip().encode(), newline=False).decode()
        req = urllib.request.Request(
            self._url, data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Basic {auth}"})

        def _do() -> Any:
            with urllib.request.urlopen(req, timeout=20) as r:
                d = json.load(r)
            if d.get("error"):
                raise RuntimeError(f"{method}: {d['error']}")
            return d.get("result")

        return await asyncio.get_running_loop().run_in_executor(None, _do)


class Bc3PoolJob:
    """Pool-side BC3 job: owns the coinbase so it can rebuild and re-verify."""

    __slots__ = ("job_id", "template", "coinb1", "coinb2", "branch", "version",
                 "nbits", "ntime", "height", "block_target", "created", "sha3")

    def __init__(self, job_id: str, t: Dict[str, Any], spk: bytes,
                 sha3_height: int, tag: str) -> None:
        self.job_id, self.template = job_id, t
        self.height = int(t["height"])
        self.sha3 = self.height >= sha3_height
        self.version = int(t["version"]) | (SHA3_VBIT if self.sha3 else 0)
        self.nbits = int(t["bits"], 16)
        self.ntime = int(t["curtime"])
        self.block_target = target_from_nbits(self.nbits)
        self.created = time.time()

        prefix = _script_num_push(self.height) + bytes([len(tag)]) + tag.encode()
        slen = len(prefix) + 4 + EXTRANONCE2_SIZE
        head = struct.pack("<i", 2) + b"\x00\x01" + _varint(1)
        head += b"\x00" * 32 + b"\xff\xff\xff\xff" + _varint(slen) + prefix

        n_out, value = 1, int(t["coinbasevalue"])
        outs = struct.pack("<q", value) + _varint(len(spk)) + spk
        wc = t.get("default_witness_commitment")
        if wc:
            w = bytes.fromhex(wc)
            n_out += 1
            outs += struct.pack("<q", 0) + _varint(len(w)) + w
        tail = b"\xff\xff\xff\xff" + _varint(n_out) + outs
        tail += _varint(1) + b"\x20" + b"\x00" * 32 + struct.pack("<I", 0)
        self.coinb1, self.coinb2 = head, tail

        txids = [bytes.fromhex(tx["txid"])[::-1] for tx in t.get("transactions", [])]
        branch: List[bytes] = []
        lvl = [b"\x00" * 32] + txids
        while len(lvl) > 1:
            branch.append(lvl[1])
            if len(lvl) % 2:
                lvl = lvl + [lvl[-1]]
            lvl = [b"\x00" * 32] + [sha256d(lvl[i] + lvl[i + 1])
                                    for i in range(2, len(lvl), 2)]
        self.branch = branch

    def _coinbase_txid(self, en1: bytes, en2: bytes) -> bytes:
        full = self.coinb1 + en1 + en2 + self.coinb2
        ver, rest = full[:4], full[6:]
        w = rest.rfind(b"\x01\x20" + b"\x00" * 32)
        return sha256d(ver + rest[:w] + rest[w + 34:])

    def merkle_root(self, en1: bytes, en2: bytes) -> bytes:
        h = self._coinbase_txid(en1, en2)
        for b in self.branch:
            h = sha256d(h + b)
        return h

    def header(self, en1: bytes, en2: bytes, ntime: int, nonce: int) -> bytes:
        return (struct.pack("<i", self.version)
                + bytes.fromhex(self.template["previousblockhash"])[::-1]
                + self.merkle_root(en1, en2)
                + struct.pack("<I", ntime) + struct.pack("<I", self.nbits)
                + struct.pack("<I", nonce))

    def notify_params(self, clean: bool) -> List[Any]:
        prev = bytes.fromhex(self.template["previousblockhash"])
        return [self.job_id, swap_words(prev[::-1]).hex(),
                self.coinb1.hex(), self.coinb2.hex(),
                [b[::-1].hex() for b in self.branch],
                f"{self.version:08x}", f"{self.nbits:08x}", f"{self.ntime:08x}",
                clean]

    def full_block(self, en1: bytes, en2: bytes, hdr: bytes) -> bytes:
        coinbase = self.coinb1 + en1 + en2 + self.coinb2
        txs = [bytes.fromhex(t["data"]) for t in self.template.get("transactions", [])]
        return hdr + _varint(1 + len(txs)) + coinbase + b"".join(txs)


class Bc3JobManager:
    """Polls the BC3 node, pushes jobs to the stratum server, validates shares."""

    def __init__(self, server: Any, *, rpc_url: str, cookie: str,
                 payout_address: str, share_diff: float = 0.05,
                 poll_s: float = 5.0, tag: str = "/animica/") -> None:
        self._server = server
        self._rpc = Bc3Rpc(rpc_url, cookie)
        self._payout_address = payout_address
        self._share_diff = share_diff
        self._poll_s = poll_s
        self._tag = tag
        self._spk: Optional[bytes] = None
        self._sha3_height = SHA3_HEIGHT_MAIN
        self._jobs: Dict[str, Bc3PoolJob] = {}
        self._seq = 0
        self._last_prev = ""
        self._task: Optional[asyncio.Task] = None
        self.blocks_found = 0
        self.shares_accepted = 0

    async def start(self) -> None:
        info = await self._rpc.call("getaddressinfo", [self._payout_address])
        self._spk = bytes.fromhex(info["scriptPubKey"])
        chain = (await self._rpc.call("getblockchaininfo"))["chain"]
        self._sha3_height = SHA3_HEIGHT_MAIN if chain == "main" else SHA3_HEIGHT_TEST
        self._server._on_bc3_result = self.on_share
        self._task = asyncio.get_running_loop().create_task(self._loop())
        log.info("bc3: started chain=%s SHA3Height=%d payout=%s",
                 chain, self._sha3_height, self._payout_address)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                t = await self._rpc.call("getblocktemplate", [{"rules": ["segwit"]}])
                prev = t["previousblockhash"]
                fresh = prev != self._last_prev
                if fresh or not self._jobs or \
                        (time.time() - max(j.created for j in self._jobs.values())) > 30:
                    self._seq += 1
                    jid = f"b{self._seq:x}"
                    job = Bc3PoolJob(jid, t, self._spk, self._sha3_height, self._tag)
                    self._jobs[jid] = job
                    for old in list(self._jobs)[:-8]:
                        self._jobs.pop(old, None)
                    self._last_prev = prev
                    await self._server.push_bc3_job(
                        job_params=job.notify_params(fresh),
                        share_diff=self._share_diff, height=job.height)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Never let a BC3 problem touch Animica mining.
                log.debug("bc3: template poll failed: %s", exc)
            await asyncio.sleep(self._poll_s)

    async def on_share(self, session: Any, params: Dict[str, Any]) -> None:
        """Validate a bc3.submit. The miner's claim is re-derived, never trusted."""
        try:
            job = self._jobs.get(str(params.get("jobId") or ""))
            if job is None:
                return
            en1 = self._server.bc3_extranonce1(session.session_id)
            en2 = bytes.fromhex(str(params["extranonce2"]))
            ntime = int(str(params["ntime"]), 16)
            nonce = int(str(params["nonce"]), 16)
            if len(en2) != EXTRANONCE2_SIZE:
                return
            if not (int(job.template["mintime"]) <= ntime <= job.ntime + 7200):
                return

            hdr = job.header(en1, en2, ntime, nonce)
            digest = pow_hash(hdr, job.version)
            val = int.from_bytes(digest[::-1], "big")
            if val > target_from_diff(self._share_diff):
                return
            self.shares_accepted += 1
            if val <= job.block_target:
                await self._submit_block(job, en1, en2, hdr, digest)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.debug("bc3: share validation failed: %s", exc)

    async def _submit_block(self, job: Bc3PoolJob, en1: bytes, en2: bytes,
                            hdr: bytes, digest: bytes) -> None:
        bhash = digest[::-1].hex()
        block = job.full_block(en1, en2, hdr)
        log.warning("bc3: BLOCK CANDIDATE height=%d hash=%s", job.height, bhash)
        try:
            res = await self._rpc.call("submitblock", [block.hex()])
        except Exception as exc:
            res = f"rpc error: {exc}"
        if res in (None, ""):
            self.blocks_found += 1
            log.warning("bc3: block ACCEPTED height=%d hash=%s value=%d sat",
                        job.height, bhash, int(job.template["coinbasevalue"]))
        else:
            log.warning("bc3: block REJECTED height=%d -> %r", job.height, res)

    def stats(self) -> Dict[str, Any]:
        return {"enabled": self._task is not None and not self._task.done(),
                "jobs_cached": len(self._jobs),
                "shares_accepted": self.shares_accepted,
                "blocks_found": self.blocks_found,
                "payout_address": self._payout_address}
