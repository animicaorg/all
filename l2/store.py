"""L2 storage engine: atomic batch commits, WAL, snapshots, crash recovery.

Throughput rule from the spec: never one synchronous disk write per transaction.
We persist at **batch** granularity — a batch aggregates thousands of txs — so the
fsync cost is amortized. Hot state lives in memory (the :class:`StateTree` and
:class:`Bridge`); the store durably records each committed batch and periodic
authenticated state snapshots.

Crash safety (the invariant "a crash must never leave a partially committed
canonical batch"):

* A batch's DA blob + header are written to temp files, fsync'd, then atomically
  ``os.replace``d into place.
* Only after those files exist do we append a single **commit marker** line to the
  WAL (append + fsync). The marker is the atomic commit point: it names the batch
  number, batch id, new_state_root, and a state-snapshot file.
* The state snapshot is itself written temp→fsync→replace *before* the marker.

So the canonical head is "the highest batch number whose WAL marker AND snapshot
both exist and whose snapshot hashes to the marker's committed root." A crash at
any point leaves the previous consistent head intact; the half-written batch has
no marker and is ignored on recovery.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .batch import Batch, BatchHeader
from .bridge import Bridge, Deposit, ForcedRequest, Withdrawal, WithdrawState
from .constants import L1Confirmation
from .executor import Escrow
from .state import Account, StateTree


def _fsync_file(path: str) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: str) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(path: str, data: bytes) -> None:
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    _fsync_dir(os.path.dirname(path) or ".")


@dataclass
class CommitMarker:
    batch_number: int
    batch_id: str
    new_state_root: str
    snapshot_file: str
    blob_file: str


class L2Store:
    def __init__(self, data_dir: str) -> None:
        self.dir = data_dir
        self.blobs_dir = os.path.join(data_dir, "blobs")
        self.snaps_dir = os.path.join(data_dir, "snapshots")
        self.headers_dir = os.path.join(data_dir, "headers")
        self.wal_path = os.path.join(data_dir, "wal.log")
        for d in (self.dir, self.blobs_dir, self.snaps_dir, self.headers_dir):
            os.makedirs(d, exist_ok=True)

    # ── snapshots ────────────────────────────────────────────────────────────

    def _snapshot_state(
        self, tree: StateTree, escrows: Dict[bytes, Escrow], bridge: Bridge
    ) -> dict:
        return {
            "accounts": {
                a.hex(): {"balance": str(acc.balance), "nonce": acc.nonce,
                          "metadata": acc.metadata.hex()}
                for a, acc in tree.accounts().items()
            },
            "escrows": {
                eid.hex(): {
                    "depositor": e.depositor.hex(),
                    "beneficiary": e.beneficiary.hex(),
                    "amount": str(e.amount),
                    "refund_after": e.refund_after,
                    "open_height": e.open_height,
                }
                for eid, e in escrows.items()
            },
            "bridge": {
                "locked_on_l1": str(bridge.locked_on_l1),
                "credited_total": str(bridge.credited_total),
                "burned_total": str(bridge.burned_total),
                "claimed_on_l1_total": str(bridge.claimed_on_l1_total),
                "deposits": {
                    d.deposit_id.hex(): {
                        "l1_txid": d.l1_txid.hex(),
                        "beneficiary": d.beneficiary.hex(),
                        "amount": str(d.amount),
                        "seen_height": d.seen_height,
                        "confirmation": d.confirmation.value,
                        "credited": d.credited,
                    }
                    for d in bridge.deposits.values()
                },
                "withdrawals": {
                    w.nullifier.hex(): {
                        "l2_txid": w.l2_txid.hex(),
                        "l1_recipient": w.l1_recipient.hex(),
                        "amount": str(w.amount),
                        "batch_number": w.batch_number,
                        "state": w.state.value,
                    }
                    for w in bridge.withdrawals.values()
                },
                "forced": {
                    r.request_id.hex(): {
                        "raw_l2_tx": r.raw_l2_tx.hex(),
                        "submit_l1_height": r.submit_l1_height,
                        "deadline_height": r.deadline_height,
                        "included": r.included,
                    }
                    for r in bridge.forced.values()
                },
            },
        }

    @staticmethod
    def _restore_state(snap: dict, l2_chain_id: int) -> Tuple[StateTree, Dict[bytes, Escrow], Bridge]:
        tree = StateTree()
        for a_hex, acc in snap["accounts"].items():
            tree.set(
                bytes.fromhex(a_hex),
                Account(int(acc["balance"]), acc["nonce"], bytes.fromhex(acc["metadata"])),
            )
        escrows: Dict[bytes, Escrow] = {}
        for eid_hex, e in snap["escrows"].items():
            escrows[bytes.fromhex(eid_hex)] = Escrow(
                depositor=bytes.fromhex(e["depositor"]),
                beneficiary=bytes.fromhex(e["beneficiary"]),
                amount=int(e["amount"]),
                refund_after=e["refund_after"],
                open_height=e["open_height"],
            )
        bridge = Bridge(l2_chain_id)
        b = snap["bridge"]
        bridge.locked_on_l1 = int(b["locked_on_l1"])
        bridge.credited_total = int(b["credited_total"])
        bridge.burned_total = int(b["burned_total"])
        bridge.claimed_on_l1_total = int(b["claimed_on_l1_total"])
        for did_hex, d in b["deposits"].items():
            bridge.deposits[bytes.fromhex(did_hex)] = Deposit(
                deposit_id=bytes.fromhex(did_hex),
                l1_txid=bytes.fromhex(d["l1_txid"]),
                beneficiary=bytes.fromhex(d["beneficiary"]),
                amount=int(d["amount"]),
                seen_height=d["seen_height"],
                confirmation=L1Confirmation(d["confirmation"]),
                credited=d["credited"],
            )
        for nf_hex, w in b["withdrawals"].items():
            bridge.withdrawals[bytes.fromhex(nf_hex)] = Withdrawal(
                nullifier=bytes.fromhex(nf_hex),
                l2_txid=bytes.fromhex(w["l2_txid"]),
                l1_recipient=bytes.fromhex(w["l1_recipient"]),
                amount=int(w["amount"]),
                batch_number=w["batch_number"],
                state=WithdrawState(w["state"]),
            )
        for rid_hex, r in b["forced"].items():
            bridge.forced[bytes.fromhex(rid_hex)] = ForcedRequest(
                request_id=bytes.fromhex(rid_hex),
                raw_l2_tx=bytes.fromhex(r["raw_l2_tx"]),
                submit_l1_height=r["submit_l1_height"],
                deadline_height=r["deadline_height"],
                included=r["included"],
            )
        return tree, escrows, bridge

    # ── commit ─────────────────────────────────────────────────────────────────

    def commit_batch(
        self,
        batch: Batch,
        tree: StateTree,
        escrows: Dict[bytes, Escrow],
        bridge: Bridge,
    ) -> CommitMarker:
        n = batch.header.number
        blob_file = os.path.join(self.blobs_dir, f"batch-{n:012d}.bin")
        header_file = os.path.join(self.headers_dir, f"batch-{n:012d}.json")
        snap_file = os.path.join(self.snaps_dir, f"state-{n:012d}.json")

        # Order matters for crash safety: durable data first, marker last.
        _atomic_write(blob_file, batch.da_blob)
        _atomic_write(header_file, json.dumps(batch.header.to_json()).encode())
        _atomic_write(
            snap_file,
            json.dumps(self._snapshot_state(tree, escrows, bridge)).encode(),
        )

        marker = CommitMarker(
            batch_number=n,
            batch_id="0x" + batch.id().hex(),
            new_state_root="0x" + batch.header.new_state_root.hex(),
            snapshot_file=os.path.basename(snap_file),
            blob_file=os.path.basename(blob_file),
        )
        # The append+fsync of this line is the atomic commit point.
        line = json.dumps(marker.__dict__, sort_keys=True) + "\n"
        with open(self.wal_path, "a") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        return marker

    # ── recovery ───────────────────────────────────────────────────────────────

    def read_markers(self) -> List[CommitMarker]:
        if not os.path.exists(self.wal_path):
            return []
        markers: List[CommitMarker] = []
        with open(self.wal_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    markers.append(CommitMarker(**obj))
                except Exception:
                    # A torn final line (crash mid-append) is ignored: it has no
                    # complete marker, so its batch is not canonical.
                    continue
        return markers

    def canonical_head(self) -> Optional[CommitMarker]:
        """Highest batch whose marker AND snapshot file both exist and whose
        snapshot restores to the marker's committed root."""
        for marker in reversed(self.read_markers()):
            snap_path = os.path.join(self.snaps_dir, marker.snapshot_file)
            if not os.path.exists(snap_path):
                continue
            try:
                snap = json.loads(open(snap_path).read())
            except Exception:
                continue
            # Verify the snapshot hashes to the committed root — a corrupt
            # snapshot is rejected, never silently trusted.
            tree, _, _ = self._restore_state(snap, l2_chain_id=0)
            if "0x" + tree.root().hex() == marker.new_state_root:
                return marker
        return None

    def recover(self, l2_chain_id: int) -> Tuple[StateTree, Dict[bytes, Escrow], Bridge, int]:
        """Return (tree, escrows, bridge, head_batch_number). Empty genesis state
        when there is nothing committed yet."""
        head = self.canonical_head()
        if head is None:
            return StateTree(), {}, Bridge(l2_chain_id), -1
        snap = json.loads(open(os.path.join(self.snaps_dir, head.snapshot_file)).read())
        tree, escrows, bridge = self._restore_state(snap, l2_chain_id)
        return tree, escrows, bridge, head.batch_number

    def load_header(self, number: int) -> Optional[dict]:
        p = os.path.join(self.headers_dir, f"batch-{number:012d}.json")
        if not os.path.exists(p):
            return None
        return json.loads(open(p).read())

    def load_blob(self, number: int) -> Optional[bytes]:
        p = os.path.join(self.blobs_dir, f"batch-{number:012d}.bin")
        if not os.path.exists(p):
            return None
        return open(p, "rb").read()

    def prune_snapshots(self, keep: int = 16) -> int:
        """Retain only the newest ``keep`` state snapshots (headers + blobs are
        kept for DA/history). Returns count removed."""
        markers = self.read_markers()
        if len(markers) <= keep:
            return 0
        removed = 0
        for marker in markers[:-keep]:
            p = os.path.join(self.snaps_dir, marker.snapshot_file)
            if os.path.exists(p):
                try:
                    os.remove(p)
                    removed += 1
                except OSError:
                    pass
        return removed
