import os
import sys
import random
import hashlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Iterable, DefaultDict
from collections import defaultdict
import pytest

# Make project importable if tests want to peek at real modules (optional)
sys.path.insert(0, os.path.expanduser("~/animica"))


# ------------------------------
# Minimal primitives for blocks
# ------------------------------

def sha3_256(b: bytes) -> bytes:
    return hashlib.sha3_256(b).digest()

def merkle_root(leaves: List[bytes]) -> bytes:
    """
    Canonical toy Merkle:
      leaf = H(0x00 || data)
      node = H(0x01 || left || right)
      odd node duplication at each level
    """
    if not leaves:
        return sha3_256(b"")
    lvl = [sha3_256(b"\x00" + x) for x in leaves]
    while len(lvl) > 1:
        if len(lvl) % 2 == 1:
            lvl.append(lvl[-1])
        nxt = []
        for i in range(0, len(lvl), 2):
            nxt.append(sha3_256(b"\x01" + lvl[i] + lvl[i+1]))
        lvl = nxt
    return lvl[0]

def _h2b(h: str) -> bytes:
    return bytes.fromhex(h[2:] if h.startswith("0x") else h)

def _b2h(b: bytes) -> str:
    return "0x" + b.hex()


@dataclass(frozen=True)
class FakeHeader:
    """
    Header binds (parent_hash, height, body_root) → header.hash
    """
    hash: str
    parent: Optional[str]
    height: int
    body_root: str  # hex

    @staticmethod
    def make(parent: Optional["FakeHeader"], height: int, body_root: bytes) -> "FakeHeader":
        parent_hash = b"\x00" * 32 if parent is None else _h2b(parent.hash)
        hasher = hashlib.sha3_256()
        hasher.update(parent_hash)
        hasher.update(height.to_bytes(8, "big"))
        hasher.update(body_root)
        digest = _b2h(hasher.digest())
        return FakeHeader(hash=digest, parent=None if parent is None else parent.hash, height=height, body_root=_b2h(body_root))


@dataclass(frozen=True)
class FakeBlock:
    """
    Block = (header, txs).
    Integrity rules:
      - header.body_root == merkle_root(txs)
      - header.hash == H(parent_hash || height || body_root)
      - height == parent.height + 1 (except genesis)
    """
    header: FakeHeader
    txs: List[bytes]


# ------------------------------
# In-memory block DB & importer
# ------------------------------

class IntegrityError(Exception): ...
class TemporaryError(Exception): ...
class NotFound(Exception): ...

class InMemoryBlockDB:
    def __init__(self) -> None:
        self.headers: Dict[str, FakeHeader] = {}
        self.blocks: Dict[str, FakeBlock] = {}
        self.children: Dict[str, List[str]] = {}
        self.best_tip: Optional[str] = None

    def has_header(self, h: str) -> bool:
        return h in self.headers

    def get_header(self, h: str) -> FakeHeader:
        return self.headers[h]

    def put_block(self, blk: FakeBlock) -> None:
        # Basic linkage
        if blk.header.parent is not None and blk.header.parent not in self.headers:
            raise IntegrityError("parent unknown; import parent first or buffer")
        # Body root matches
        calc_root = _b2h(merkle_root(blk.txs))
        if calc_root != blk.header.body_root:
            raise IntegrityError("body_root mismatch")
        # Recompute header.hash
        parent_hash = b"\x00"*32 if blk.header.parent is None else _h2b(blk.header.parent)
        hcalc = _b2h(sha3_256(parent_hash + blk.header.height.to_bytes(8, "big") + _h2b(blk.header.body_root)))
        if hcalc != blk.header.hash:
            raise IntegrityError("header hash mismatch")
        # Height rule
        if blk.header.parent is None:
            if blk.header.height != 0:
                raise IntegrityError("genesis must have height 0")
        else:
            ph = self.headers[blk.header.parent]
            if blk.header.height != ph.height + 1:
                raise IntegrityError("non-sequential height")
        # Store
        self.headers[blk.header.hash] = blk.header
        self.blocks[blk.header.hash] = blk
        if blk.header.parent is not None:
            self.children.setdefault(blk.header.parent, []).append(blk.header.hash)
        # Update tip (height first; tie-break by hash-bytes)
        if self.best_tip is None:
            self.best_tip = blk.header.hash
        else:
            cur = self.headers[self.best_tip]
            cand = blk.header
            if (cand.height, _h2b(cand.hash)) > (cur.height, _h2b(cur.hash)):
                self.best_tip = cand.hash

    def head(self) -> FakeHeader:
        assert self.best_tip is not None, "empty"
        return self.headers[self.best_tip]


class BlockImporter:
    """
    Accepts blocks in arbitrary order; buffers until parents arrive.
    """
    def __init__(self, db: InMemoryBlockDB) -> None:
        self.db = db
        self._pending_by_parent: DefaultDict[str, List[FakeBlock]] = defaultdict(list)

    def import_block(self, blk: FakeBlock) -> bool:
        parent = blk.header.parent
        if parent is not None and not self.db.has_header(parent):
            # Buffer and return
            self._pending_by_parent[parent].append(blk)
            return False
        # Try import
        self.db.put_block(blk)
        # Drain children
        q = [blk.header.hash]
        while q:
            h = q.pop()
            if h in self._pending_by_parent:
                # Iterate over a snapshot and clear to avoid reentrancy issues
                buffered = self._pending_by_parent.pop(h)
                for ch in buffered:
                    if self.db.has_header(h):
                        try:
                            self.db.put_block(ch)
                            q.append(ch.header.hash)
                        except IntegrityError:
                            # If child invalid, drop; tests will cover that we don't loop forever
                            pass
        return True

    def import_many(self, blks: Iterable[FakeBlock]) -> int:
        imported = 0
        for b in blks:
            try:
                if self.import_block(b):
                    imported += 1
            except IntegrityError:
                # For tests we don't propagate; caller can assert on final state
                pass
        return imported


# ------------------------------
# Utilities to build fake chains
# ------------------------------

def make_block(parent: Optional[FakeBlock], height: int, tx_count: int, seed: int) -> FakeBlock:
    rnd = random.Random(seed)
    txs = [f"tx-{height}-{i}-{rnd.randint(0, 1<<30)}".encode() for i in range(tx_count)]
    root = merkle_root(txs)
    hdr = FakeHeader.make(None if parent is None else parent.header, height=height, body_root=root)
    return FakeBlock(header=hdr, txs=txs)

def build_chain(length: int, txs_per_block: int = 3, seed: int = 1337) -> List[FakeBlock]:
    random.seed(seed)
    chain: List[FakeBlock] = []
    # genesis
    g = make_block(parent=None, height=0, tx_count=0, seed=seed ^ 0xABC)
    chain.append(g)
    parent = g
    for h in range(1, length + 1):
        b = make_block(parent, height=h, tx_count=txs_per_block, seed=seed ^ h)
        chain.append(b)
        parent = b
    return chain  # includes genesis at index 0


# ------------------------------
# Fake peers & fetch routines
# ------------------------------

class FakePeer:
    def __init__(self, blocks: Dict[str, FakeBlock]) -> None:
        self.blocks = blocks
        self._drops_once: Dict[str, bool] = {}
        self._tamper_payload: Dict[str, FakeBlock] = {}

    def set_drop_once(self, h: str) -> None:
        self._drops_once[h] = True

    def set_tamper(self, h: str, tampered_block: FakeBlock) -> None:
        self._tamper_payload[h] = tampered_block

    def get_block(self, h: str) -> FakeBlock:
        # Simulate transient failure
        if self._drops_once.pop(h, False):
            raise TemporaryError("transient network error")
        if h in self._tamper_payload:
            return self._tamper_payload[h]
        if h not in self.blocks:
            raise NotFound(h)
        return self.blocks[h]


def fetch_blocks_with_retries(peer: FakePeer, hashes: List[str], max_attempts: int = 3, shuffle: bool = True) -> List[FakeBlock]:
    """
    Simple loop that retries transient failures. Returns blocks in arbitrary order if shuffle=True.
    """
    pending = set(hashes)
    attempts: DefaultDict[str, int] = defaultdict(int)
    res: Dict[str, FakeBlock] = {}
    while pending:
        h = pending.pop()
        attempts[h] += 1
        try:
            blk = peer.get_block(h)
            res[h] = blk
        except TemporaryError:
            if attempts[h] < max_attempts:
                pending.add(h)
            else:
                raise
        except NotFound:
            raise
    out = list(res.values())
    if shuffle:
        random.Random(4242).shuffle(out)
    return out


# ------------------------------
# Tests
# ------------------------------

def test_parallel_block_fetch_and_import_ordering():
    """
    Fetch 40 blocks in parallel-ish (random order), ensure importer buffers children until parents
    arrive and final tip equals source.
    """
    chain = build_chain(40, txs_per_block=2, seed=2025)
    # Source peer has all blocks
    src_blocks = {b.header.hash: b for b in chain}
    peer = FakePeer(src_blocks)

    # Target DB starts empty; import genesis first to anchor
    db = InMemoryBlockDB()
    importer = BlockImporter(db)
    importer.import_block(chain[0])  # genesis

    # Request blocks 1..40 (exclude genesis)
    want = [b.header.hash for b in chain[1:]]
    fetched = fetch_blocks_with_retries(peer, want, shuffle=True)

    imported = importer.import_many(fetched)
    assert imported == 40  # including genesis we inserted earlier? No, importer counted only fetched
    # Wait — imported counts only those returned; length of fetched is 40, so match:
    assert len(fetched) == 40
    # Verify head
    assert db.head().hash == chain[-1].header.hash
    assert db.head().height == 40


def test_integrity_rejects_tampered_body():
    """
    Tweak txs of block at height 5 but keep header identical → body_root mismatch → reject.
    Children depending on that block will remain buffered/unimported.
    """
    chain = build_chain(12, txs_per_block=3, seed=7)
    src_blocks = {b.header.hash: b for b in chain}
    peer = FakePeer(src_blocks)

    # Create a tampered variant for height 5
    target_blk = chain[5]
    tampered_txs = target_blk.txs[:] + [b"evil"]
    tampered = FakeBlock(header=target_blk.header, txs=tampered_txs)
    peer.set_tamper(target_blk.header.hash, tampered)

    db = InMemoryBlockDB()
    importer = BlockImporter(db)
    importer.import_block(chain[0])  # genesis

    want = [b.header.hash for b in chain[1:]]
    fetched = fetch_blocks_with_retries(peer, want, shuffle=True)

    imported = importer.import_many(fetched)
    # Blocks up to height 4 can import; height 5 rejected; >5 remain pending
    assert db.head().height == 4
    assert imported < len(fetched)
    # Ensure the invalid block was not stored
    assert target_blk.header.hash not in db.blocks


def test_missing_then_retry_succeeds():
    """
    Simulate a transient drop on a specific block hash. Retry logic should fetch it on second try,
    allowing full import to tip.
    """
    chain = build_chain(12, txs_per_block=2, seed=99)
    src_blocks = {b.header.hash: b for b in chain}
    peer = FakePeer(src_blocks)

    # Drop first attempt for height 7
    drop_hash = chain[7].header.hash
    peer.set_drop_once(drop_hash)

    db = InMemoryBlockDB()
    importer = BlockImporter(db)
    importer.import_block(chain[0])  # genesis

    want = [b.header.hash for b in chain[1:]]
    fetched = fetch_blocks_with_retries(peer, want, max_attempts=3, shuffle=True)
    imported = importer.import_many(fetched)

    # We fetched 12 (1..12) blocks; importer should import all 12
    assert len(fetched) == 12
    assert imported == 12
    assert db.head().hash == chain[-1].header.hash
    assert db.head().height == 12


# Sanity: importing out-of-order but with multiple invalid descendants does not hang
def test_buffering_does_not_spin_on_invalid_parent():
    chain = build_chain(8, txs_per_block=1, seed=555)
    src_blocks = {b.header.hash: b for b in chain}
    peer = FakePeer(src_blocks)

    # Tamper block at height 3
    bad = chain[3]
    peer.set_tamper(bad.header.hash, FakeBlock(header=bad.header, txs=[b"not-the-same"]))

    db = InMemoryBlockDB()
    importer = BlockImporter(db)
    importer.import_block(chain[0])  # genesis

    want = [b.header.hash for b in chain[1:]]
    fetched = fetch_blocks_with_retries(peer, want, shuffle=True)
    imported = importer.import_many(fetched)

    # Head should be height 2, and importer should not deadlock
    assert db.head().height == 2
    assert imported < len(fetched)

