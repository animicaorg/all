from __future__ import annotations

"""
Animica • DA • Retrieval Service

This module orchestrates the three core DA steps:

  1) POST  : Accept a raw blob for a given namespace, compute the commitment
             (Merkle root over namespaced chunks), and persist the blob + meta.
  2) GET   : Return the raw blob bytes by commitment.
  3) PROOF : Rebuild the chunk set and return Merkle branches for requested
             leaf indices sufficient for a light verifier to check availability.

Design goals
------------
- Pure-Python, zero-IO in helpers; all I/O encapsulated in a tiny FS-backed store.
- Stable hashing domains:
    LEAF  = sha3_256( 0x00 || ns_be32 || uvarint(len(chunk)) || chunk )
    INNER = sha3_256( 0x01 || left_hash || right_hash )
- Deterministic chunking using a fixed shard size (default from da.constants).
- Graceful fallbacks: the service does not depend on heavy erasure/NMT modules
  to be present; it produces standard Merkle proofs over namespaced chunks.
  (NMT-specific proofs can be layered later by swapping the commit/proof helpers.)

Returned proof shape (JSON-serializable)
----------------------------------------
{
  "scheme": "sha3-merkle-v1",
  "namespace": <int>,
  "shard_bytes": <int>,
  "commitment": "0x…32-byte root…",
  "total_leaves": <int>,
  "queries": [
    {
      "index": <int>,
      "leaf_hash": "0x…",
      "siblings": ["0x…", "0x…", ...]   # bottom-up order
    },
    ...
  ]
}

This shape is intentionally minimal and sufficient for a light verifier:
given (namespace, shard_bytes, total_leaves, index, leaf_hash, siblings),
one can recompute the Merkle root and compare to 'commitment'.

If you later plug in a full Namespaced Merkle Tree (NMT) pipeline, you can
drop-in replace _chunk_and_leafhash(), _merkle_root(), and _merkle_branch()
with NMT-aware versions (e.g., using da.nmt.*), keeping the service API stable.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# --- Optional imports (graceful stubs if not present) ------------------------

try:
    from da.version import __version__ as DA_VERSION
except Exception:  # pragma: no cover
    DA_VERSION = "0.0.0"

try:
    from da.constants import DEFAULT_SHARD_BYTES  # type: ignore
except Exception:  # pragma: no cover
    DEFAULT_SHARD_BYTES = 1024 * 4  # 4 KiB sensible default

try:
    from da.config import DAConfig  # type: ignore
except Exception:  # pragma: no cover

    @dataclass
    class DAConfig:  # minimal stand-in
        storage_dir: str = ".da_store"
        shard_bytes: int = DEFAULT_SHARD_BYTES


try:
    from da.errors import DAError, InvalidProof, NotFound  # type: ignore
except Exception:  # pragma: no cover

    class DAError(Exception):
        """Base DA error"""

    class NotFound(DAError):
        """Blob not found"""

    class InvalidProof(DAError):
        """Proof request invalid or cannot be satisfied"""


# Hashing helpers
try:
    from da.utils.hash import sha3_256  # type: ignore
except Exception:  # pragma: no cover
    import hashlib

    def sha3_256(data: bytes) -> bytes:
        return hashlib.sha3_256(data).digest()


# Bytes helpers
try:
    from da.utils.bytes import chunk_bytes, uvarint_encode  # type: ignore
except Exception:  # pragma: no cover

    def chunk_bytes(b: bytes, size: int) -> List[bytes]:
        return [b[i : i + size] for i in range(0, len(b), size)]

    def uvarint_encode(n: int) -> bytes:
        """LEB128-like unsigned varint (little loops, big value)"""
        if n < 0:
            raise ValueError("uvarint requires non-negative")
        out = bytearray()
        while True:
            to_write = n & 0x7F
            n >>= 7
            if n:
                out.append(to_write | 0x80)
            else:
                out.append(to_write)
                break
        return bytes(out)


# --- Internal FS store -------------------------------------------------------


class _FSBlobStore:
    """
    Simple filesystem-backed blob store:

    Layout:
      <root>/blobs/<commit-hex>/blob.bin
      <root>/blobs/<commit-hex>/meta.json

    meta.json:
      {
        "namespace": <int>,
        "size": <int>,
        "commitment": "0x…",
        "shard_bytes": <int>,
        "total_leaves": <int>
      }
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        (self.root / "blobs").mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _hex(b: bytes) -> str:
        return "0x" + b.hex()

    @staticmethod
    def _dehex(s: str) -> bytes:
        s = s[2:] if s.startswith(("0x", "0X")) else s
        if len(s) % 2 == 1:
            s = "0" + s
        return bytes.fromhex(s)

    def put(
        self,
        commitment: bytes,
        namespace: int,
        shard_bytes: int,
        total_leaves: int,
        data: bytes,
    ) -> None:
        h = self._hex(commitment)
        base = self.root / "blobs" / h
        base.mkdir(parents=True, exist_ok=True)
        blob_path = base / "blob.bin"
        meta_path = base / "meta.json"
        # atomic-ish write
        tmp = base / ".blob.tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, blob_path)

        meta = {
            "namespace": int(namespace),
            "size": int(len(data)),
            "commitment": h,
            "shard_bytes": int(shard_bytes),
            "total_leaves": int(total_leaves),
        }
        tmpm = base / ".meta.tmp"
        with open(tmpm, "w", encoding="utf-8") as f:
            json.dump(meta, f, separators=(",", ":"), sort_keys=True)
        os.replace(tmpm, meta_path)

    def get(self, commitment: bytes) -> bytes:
        h = self._hex(commitment)
        blob_path = self.root / "blobs" / h / "blob.bin"
        if not blob_path.exists():
            raise NotFound(f"Blob {h} not found")
        return blob_path.read_bytes()

    def meta(self, commitment: bytes) -> Dict:
        h = self._hex(commitment)
        meta_path = self.root / "blobs" / h / "meta.json"
        if not meta_path.exists():
            raise NotFound(f"Meta for {h} not found")
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f)


# --- Merkle over namespaced chunks ------------------------------------------


def _leaf_hash(ns: int, chunk: bytes) -> bytes:
    # LEAF domain = 0x00
    return sha3_256(
        b"\x00" + int(ns).to_bytes(4, "big") + uvarint_encode(len(chunk)) + chunk
    )


def _inner_hash(left: bytes, right: bytes) -> bytes:
    # INNER domain = 0x01
    return sha3_256(b"\x01" + left + right)


def _merkle_root(leaf_hashes: List[bytes]) -> bytes:
    if not leaf_hashes:
        # Empty blob commitment = sha3_256(0x00 || ns=0 || len=0) (convention) — but keep fixed empty root
        return sha3_256(b"\x02empty")
    level = list(leaf_hashes)
    while len(level) > 1:
        nxt: List[bytes] = []
        it = iter(level)
        for a in it:
            try:
                b = next(it)
            except StopIteration:
                # Duplicate last (bitcoin-style padding) for odd count
                b = a
            nxt.append(_inner_hash(a, b))
        level = nxt
    return level[0]


def _merkle_branch(leaf_hashes: List[bytes], index: int) -> List[bytes]:
    """Return sibling hashes bottom-up."""
    if index < 0 or index >= len(leaf_hashes):
        raise InvalidProof(f"Index {index} out of range [0, {len(leaf_hashes)})")
    branch: List[bytes] = []
    level = list(leaf_hashes)
    idx = int(index)
    while len(level) > 1:
        nxt: List[bytes] = []
        it = iter(range(0, len(level), 2))
        for i in it:
            a = level[i]
            b = level[i + 1] if (i + 1) < len(level) else a
            # If our leaf is in this pair, record sibling
            if i == idx or (i + 1) == idx:
                sibling = b if i == idx else a
                branch.append(sibling)
                idx = len(nxt)  # Parent index becomes current pair's position
            nxt.append(_inner_hash(a, b))
        level = nxt
    return branch


def _chunk_and_leafhash(
    ns: int, data: bytes, shard_bytes: int
) -> Tuple[List[bytes], int]:
    chunks = chunk_bytes(data, shard_bytes) if data else [b""]
    leaves = [_leaf_hash(ns, c) for c in chunks]
    return leaves, len(chunks)


# --- Service ----------------------------------------------------------------


class RetrievalService:
    """
    Small façade used by the FastAPI layer.

    Methods are 'awaitable-aware' to play nicely with both sync and async callers.
    """

    def __init__(
        self,
        *,
        config: Optional[DAConfig] = None,
        store_root: Optional[str | Path] = None,
    ):
        self.config = config or DAConfig()
        root = (
            Path(store_root)
            if store_root is not None
            else Path(self.config.storage_dir)
        )
        self._store = _FSBlobStore(root)
        self.version = DA_VERSION
        self._shard_bytes = int(
            getattr(self.config, "shard_bytes", DEFAULT_SHARD_BYTES)
        )

    # -- API used by da.retrieval.api ----------------------------------------

    def post_blob(self, *, namespace: int, data: bytes) -> Dict:
        """
        Accept a blob, compute commitment, persist, and return a receipt-like dict.
        """
        if not isinstance(namespace, int) or namespace < 0:
            raise DAError("Namespace must be a non-negative integer")
        if not isinstance(data, (bytes, bytearray)):
            raise DAError("Body must be bytes")

        leaf_hashes, total = _chunk_and_leafhash(
            namespace, bytes(data), self._shard_bytes
        )
        root = _merkle_root(leaf_hashes)
        self._store.put(root, namespace, self._shard_bytes, total, bytes(data))

        # A minimal “receipt” placeholder; can be extended later to include
        # sig/alg-policy bindings.
        receipt = {
            "scheme": "sha3-merkle-v1",
            "namespace": int(namespace),
            "shard_bytes": int(self._shard_bytes),
            "total_leaves": int(total),
        }
        return {
            "commitment": root,
            "namespace": int(namespace),
            "size": int(len(data)),
            "receipt": receipt,
        }

    def get_blob(self, *, commitment: bytes) -> bytes:
        """
        Return the raw blob bytes by commitment.
        """
        if not isinstance(commitment, (bytes, bytearray)):
            raise DAError("Commitment must be bytes")
        return self._store.get(bytes(commitment))

    def get_proof(
        self,
        *,
        commitment: bytes,
        indices: Iterable[int],
        namespace: Optional[int] = None,
    ) -> Dict:
        """
        Rebuild chunks from stored blob, check the recomputed root equals the
        requested commitment, and return branches for the selected indices.
        """
        commit_b = bytes(commitment)
        if not isinstance(commit_b, (bytes, bytearray)) or len(commit_b) == 0:
            raise DAError("Commitment must be non-empty bytes")

        # Load blob & meta
        blob = self._store.get(commit_b)
        meta = self._store.meta(commit_b)
        ns_meta = int(meta.get("namespace", -1))
        shard_bytes = int(meta.get("shard_bytes", self._shard_bytes))

        if namespace is not None and int(namespace) != ns_meta:
            raise InvalidProof(
                f"Namespace mismatch: requested {namespace}, stored {ns_meta}"
            )

        # Recompute leaf hashes and root deterministically
        leaf_hashes, total = _chunk_and_leafhash(ns_meta, blob, shard_bytes)
        root = _merkle_root(leaf_hashes)
        if root != commit_b:
            # The stored commitment should match; if not, refuse (corruption).
            raise InvalidProof("Stored blob commitment does not match Merkle root")

        q_indices = [int(i) for i in indices]
        if not q_indices:
            raise InvalidProof("No indices requested")
        for i in q_indices:
            if i < 0 or i >= total:
                raise InvalidProof(f"Index {i} out of range [0, {total})")

        queries = []
        for i in q_indices:
            branch = _merkle_branch(leaf_hashes, i)
            queries.append(
                {
                    "index": i,
                    "leaf_hash": "0x" + leaf_hashes[i].hex(),
                    "siblings": ["0x" + s.hex() for s in branch],
                }
            )

        return {
            "scheme": "sha3-merkle-v1",
            "namespace": ns_meta,
            "shard_bytes": shard_bytes,
            "commitment": "0x" + commit_b.hex(),
            "total_leaves": total,
            "queries": queries,
        }


# Convenience for optional async usage ----------------------------------------


async def _maybe_await(x):
    return await x if hasattr(x, "__await__") else x
