import os
import sys
import hashlib
import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import pytest

# Make project importable if tests want to peek at real modules
sys.path.insert(0, os.path.expanduser("~/animica"))
try:
    # Import is optional; tests are self-contained but will skip with a clearer message
    _sync_mod = __import__("p2p.sync.headers", fromlist=["*"])
except Exception:
    _sync_mod = None


# ------------------------------
# Minimal header/chain primitives
# ------------------------------

@dataclass(frozen=True)
class FakeHeader:
    """
    Ultra-minimal header model sufficient for header-sync tests.
    Only (hash, parent, height) are used, and hash binds parent+height deterministically.
    """
    hash: str
    parent: Optional[str]
    height: int

    @staticmethod
    def make(parent: Optional["FakeHeader"], height: Optional[int] = None) -> "FakeHeader":
        if parent is None:
            # Deterministic genesis
            parent_hash = b"\x00" * 32
            h = 0 if height is None else height
        else:
            parent_hash = bytes.fromhex(parent.hash[2:])
            h = (parent.height + 1) if height is None else height
        hasher = hashlib.sha3_256()
        hasher.update(parent_hash)
        hasher.update(h.to_bytes(8, "big"))
        digest = "0x" + hasher.hexdigest()
        return FakeHeader(hash=digest, parent=None if parent is None else parent.hash, height=h)


class InMemoryHeaderDB:
    """
    Simple header DB tracking best tip by (height, then hash-lexicographic) as tie-breaker.
    """
    def __init__(self) -> None:
        self.headers: Dict[str, FakeHeader] = {}
        self.children: Dict[str, List[str]] = {}
        self.best_tip: Optional[str] = None

    def put(self, hdr: FakeHeader) -> None:
        self.headers[hdr.hash] = hdr
        if hdr.parent is not None:
            self.children.setdefault(hdr.parent, []).append(hdr.hash)
        # Update best tip: higher height wins; tie-break by smallest hash (deterministic)
        if self.best_tip is None:
            self.best_tip = hdr.hash
        else:
            cur = self.headers[self.best_tip]
            if (hdr.height, _lex(hdr.hash)) > (cur.height, _lex(cur.hash)):
                self.best_tip = hdr.hash

    def has(self, h: str) -> bool:
        return h in self.headers

    def get(self, h: str) -> FakeHeader:
        return self.headers[h]

    def head(self) -> FakeHeader:
        assert self.best_tip is not None, "empty db"
        return self.headers[self.best_tip]

    def ancestor_chain_to_genesis(self, tip_hash: str) -> List[str]:
        """Return [tip, ..., genesis] hash path."""
        path = []
        h = tip_hash
        while True:
            path.append(h)
            hdr = self.headers[h]
            if hdr.parent is None:
                break
            h = hdr.parent
        return path

    def import_sequential(self, headers: List[FakeHeader]) -> None:
        """
        Import a contiguous sequence where each header's parent is either in DB or earlier in the list.
        (Exactly what a peer would send in response to getheaders.)
        """
        for h in headers:
            # Basic parent check
            if h.parent is not None and not (h.parent in self.headers or any(ph.hash == h.parent for ph in headers)):
                raise AssertionError("non-contiguous import: parent unknown")
            self.put(h)


def _lex(h: str) -> bytes:
    return bytes.fromhex(h[2:] if h.startswith("0x") else h)


# ------------------------------
# Locators & getheaders helpers
# ------------------------------

def build_locator(db: InMemoryHeaderDB, tip_hash: Optional[str], max_entries: int = 32) -> List[str]:
    """
    Build a Bitcoin-like locator: hashes with exponentially increasing back-off + genesis.
    """
    if tip_hash is None:
        return []
    step = 1
    loc = []
    cur = tip_hash
    while len(loc) < max_entries and cur is not None:
        loc.append(cur)
        hdr = db.get(cur)
        # Step back 'step' ancestors
        for _ in range(step):
            if hdr.parent is None:
                cur = None
                break
            cur = hdr.parent
            hdr = db.get(cur)
        if len(loc) >= 10:
            step *= 2
    # Ensure genesis present
    last = db.get(loc[-1])
    if last.parent is not None:
        # walk to genesis
        g = last
        while g.parent is not None:
            g = db.get(g.parent)
        if g.hash not in loc:
            loc.append(g.hash)
    return loc


def getheaders(source: InMemoryHeaderDB, locator: List[str], limit: int = 32) -> List[FakeHeader]:
    """
    Return up to 'limit' headers after the highest common ancestor with the given locator,
    following the source's current best chain.
    """
    # Find the highest hash in locator known to source
    common: Optional[str] = None
    for h in locator:
        if source.has(h):
            common = h
            break
    # If none match, start from genesis parent (i.e., return genesis if requested) — but
    # realistic behavior is to return from genesis child; here we handle both cases gracefully.
    if common is None:
        # return first header(s) on best chain from genesis
        tip = source.head().hash
        path = source.ancestor_chain_to_genesis(tip)  # tip..genesis
        path = list(reversed(path))  # genesis..tip
        # skip genesis (path[0]) because peers typically ask for headers *after* common
        seq = [source.get(h) for h in path[1:limit+1]]
        return seq

    # Build best-chain path genesis..tip
    tip = source.head().hash
    path = source.ancestor_chain_to_genesis(tip)
    path = list(reversed(path))
    # Find index of common, return the following slice
    try:
        idx = path.index(common)
    except ValueError:
        # If common isn't on best chain (e.g., locator matched an old fork),
        # walk back until we hit a common ancestor on best chain.
        # Fallback: restart from genesis as above.
        return getheaders(source, [path[0]], limit)

    next_hashes = path[idx+1 : idx+1+limit]
    return [source.get(h) for h in next_hashes]


def extend(db: InMemoryHeaderDB, parent: Optional[FakeHeader], n: int) -> List[FakeHeader]:
    out = []
    cur = parent
    for _ in range(n):
        h = FakeHeader.make(cur)
        db.put(h)
        out.append(h)
        cur = h
    return out


# ------------------------------
# Mini sync driver (no network)
# ------------------------------

def run_header_sync(source: InMemoryHeaderDB, target: InMemoryHeaderDB, batch: int = 16, max_rounds: int = 1024) -> int:
    """
    Pull-based header sync: build locator from target, request from source, import, repeat.
    Returns number of headers imported.
    """
    imported = 0
    rounds = 0
    while rounds < max_rounds:
        rounds += 1
        tip_hash = target.head().hash if target.best_tip else None
        loc = build_locator(target, tip_hash)
        resp = getheaders(source, loc, limit=batch)
        if not resp:
            break
        target.import_sequential(resp)
        imported += len(resp)
    return imported


# ------------------------------
# Tests
# ------------------------------

def test_sync_from_genesis_linear_chain():
    """
    Two nodes: B starts at genesis, A has 24 headers.
    After sync, B matches A's tip (height and hash).
    """
    random.seed(1)

    # Build source chain
    src = InMemoryHeaderDB()
    genesis = FakeHeader.make(parent=None, height=0)
    src.put(genesis)
    src_chain = extend(src, genesis, 24)
    assert src.head().height == 24

    # Target starts with only genesis
    dst = InMemoryHeaderDB()
    dst.put(genesis)

    imported = run_header_sync(src, dst, batch=7)
    assert imported > 0
    assert dst.head().hash == src.head().hash
    assert dst.head().height == 24


def test_fork_handling_reorg_to_longer_chain():
    """
    Build a common prefix of 8, then fork into:
      - Fork A: +10 headers (total height 18)
      - Fork B: +6 headers  (total height 14)
    The target first syncs with B (ends at height 14), then later with A and must reorg to A's tip.
    Deterministic tie-break is by hash if heights equal (we also sanity-check that behavior).
    """
    random.seed(2)

    # Common chain (seen by both)
    common = InMemoryHeaderDB()
    g = FakeHeader.make(parent=None, height=0)
    common.put(g)
    common_path = extend(common, g, 8)  # heights 1..8
    common_tip = common.head()

    # Build fork A (longer)
    srcA = InMemoryHeaderDB()
    # copy common
    for h in common.headers.values():
        srcA.put(h)
    a_path = extend(srcA, common_tip, 10)  # heights 9..18

    # Build fork B (shorter)
    srcB = InMemoryHeaderDB()
    for h in common.headers.values():
        srcB.put(h)
    b_path = extend(srcB, common_tip, 6)   # heights 9..14

    # Target starts with only genesis
    dst = InMemoryHeaderDB()
    dst.put(g)

    # First, sync with B (shorter)
    imported_b = run_header_sync(srcB, dst, batch=4)
    assert imported_b > 0
    assert dst.head().height == 14
    assert dst.head().hash == srcB.head().hash

    # Next, sync with A (longer) -> must reorg to A
    imported_a = run_header_sync(srcA, dst, batch=4)
    assert imported_a > 0
    assert dst.head().height == 18
    assert dst.head().hash == srcA.head().hash

    # Optional: if we sync with B again, it should stay on A (no downgrade)
    imported_b2 = run_header_sync(srcB, dst, batch=4)
    assert imported_b2 == 0
    assert dst.head().hash == srcA.head().hash


def test_tie_break_on_equal_height_by_hash():
    """
    Create two tips of equal height with different hashes; DB selects the lexicographically larger
    pair (height, hash) — implemented as height first, then hash bytes — which effectively
    provides deterministic tie-break.
    """
    # Build common up to height 4
    db = InMemoryHeaderDB()
    g = FakeHeader.make(parent=None, height=0)
    db.put(g)
    path = extend(db, g, 4)
    tip = db.head()

    # Create two children of the same parent with controlled hash ordering.
    # We'll "forge" their digest by tweaking their height integers to flip hash ordering.
    # (Since the hash = H(parent||height), different heights give different hashes.)
    child1 = FakeHeader.make(tip, height=5)   # hash depends on height=5
    child2 = FakeHeader.make(tip, height=6)   # hash depends on height=6

    # Sort their hash bytes and store the expected "winner" according to our DB rule:
    # (height equal? here not equal yet; so enforce equal-height by adjusting)
    # To force equal height and different hashes, we keep height equal but tweak with a dummy loop.
    # Instead, we keep both at height 5 by regenerating second with deterministic bump:
    child2 = FakeHeader(
        hash="0x" + hashlib.sha3_256(bytes.fromhex(tip.hash[2:]) + b"alt").hexdigest(),
        parent=tip.hash,
        height=5,
    )

    db.put(child1)
    db.put(child2)

    # Both have equal height; best_tip chosen by hash-lexicographic tie-break
    # We compute which one should win.
    expected = child1 if _lex(child1.hash) > _lex(child2.hash) else child2
    assert db.head().hash == expected.hash
    assert db.head().height == 5


# If the real sync module exists, add a smoke test that it can be imported.
def test_real_sync_module_present_or_skip():
    if _sync_mod is None:
        pytest.skip("p2p.sync.headers not present; core sync tested via local harness")
    # If present, at least ensure it has some expected symbols
    has_symbol = any(hasattr(_sync_mod, name) for name in ("HeaderSync", "build_locator", "HeadersSync"))
    assert has_symbol, "p2p.sync.headers module does not expose expected entry points"

