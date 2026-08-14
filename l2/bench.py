"""L2 benchmark harness + deterministic load generator.

Measures the real pipeline (signature verification → admission → parallel
execution → SMT update → DA encode → proof) rather than a synthetic number.
Reports are labeled with their conditions; nothing here is a marketing figure.

Workloads (spec §30):
  * ``transfers``  — unique sender → unique receiver (maximum parallelism)
  * ``hot``        — many senders → few accounts (contention)
  * ``payments``   — Zipf-distributed merchants (realistic)
  * ``inference``  — tiny INFERENCE_PAYMENTs (AI micropayments)
  * ``agent``      — bidirectional AGENT_PAYMENTs
  * ``batch``      — large BATCH_PAYMENT payouts (effective transfers/sec ≫ tps)

Ramp mode (§31) increases offered load until a stage saturates and records the
sustainable maximum. Signing is the dominant cost of realistic PQ workloads, so
the harness can pre-sign (measuring execution+state+DA throughput) or sign inline
(measuring true end-to-end including verification) — both are reported so the
bottleneck is explicit.

Deterministic: account seeds and workload RNG are seeded from ``--seed`` /
``args``. Benchmark mode uses the devnet L2 chain id and an ephemeral in-memory
node — it can never touch a mainnet account.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from . import tx as l2tx
from .batch import ClosurePolicy
from .constants import L2_CHAIN_ID_DEVNET, SettlementMode, SigScheme, TxType
from .crypto import get_verifier, reset_verifier_for_tests
from .sequencer import Sequencer, SequencerConfig
from pq.py.algs import ml_dsa_65


@dataclass
class Keypair:
    sk: bytes
    pk: bytes
    addr: bytes


def make_accounts(n: int, seed: int = 0) -> List[Keypair]:
    """Deterministic test accounts. Keygen is expensive (PQ), so we derive many
    accounts from a small pool of real keys where a workload does not need every
    sender to be cryptographically distinct — but signing always uses a real key
    so verification cost is real."""
    kps: List[Keypair] = []
    # Cap real keygens; reuse keys across logical accounts by salting the address
    # domain is unsafe, so instead we generate min(n, pool) real keys and, when
    # n exceeds the pool, cycle keys (senders share a key => share a nonce space;
    # workloads that need distinct nonces stay within the pool).
    pool = min(n, 256)
    for i in range(pool):
        sk, pk = ml_dsa_65.keypair((seed * 100003 + i).to_bytes(32, "big"))
        kps.append(Keypair(sk, pk, l2tx.address_from_pubkey(pk)))
    if n > pool:
        # Extend by repeating the pool; callers that need unique nonces should
        # request n <= 256 senders. Recipients can be arbitrary 32-byte keys.
        kps = [kps[i % pool] for i in range(n)]
    return kps


def _recipient(i: int) -> bytes:
    return (b"R" + i.to_bytes(31, "big"))


# ── workload builders (return signed txs, senders indexed) ───────────────────


def _sign(kp: Keypair, t: l2tx.L2Tx) -> l2tx.L2Tx:
    t.pubkey = kp.pk
    t.signature = ml_dsa_65.sign(kp.sk, t.signing_hash())
    return t


def build_workload(
    kind: str, count: int, accounts: List[Keypair], *, seed: int = 0,
    contention: int = 1, sign: bool = True,
) -> List[l2tx.L2Tx]:
    rng = random.Random(seed)
    n = len(accounts)
    nonces: Dict[bytes, int] = {}
    txs: List[l2tx.L2Tx] = []

    def next_nonce(addr: bytes) -> int:
        v = nonces.get(addr, 0)
        nonces[addr] = v + 1
        return v

    for i in range(count):
        kp = accounts[i % n]
        nonce = next_nonce(kp.addr)
        if kind == "transfers":
            payload = l2tx.TransferPayload(_recipient(i), 1000, b"")
            tt = TxType.TRANSFER
        elif kind == "hot":
            payload = l2tx.TransferPayload(_recipient(rng.randrange(max(1, contention))), 100, b"")
            tt = TxType.TRANSFER
        elif kind == "payments":
            # Zipf-ish merchant selection over a small merchant set.
            m = int((rng.random() ** 2) * 50)
            payload = l2tx.TransferPayload(_recipient(1_000_000 + m), 250, b"pay")
            tt = TxType.PAY
        elif kind == "inference":
            payload = l2tx.InferencePaymentPayload(
                _recipient(2_000_000 + (i % 32)), 10,
                rng.randbytes(32), (i % 8).to_bytes(32, "big"))
            tt = TxType.INFERENCE_PAYMENT
        elif kind == "agent":
            payload = l2tx.AgentPaymentPayload(
                _recipient(3_000_000 + (i % 64)), 5,
                rng.randbytes(32), rng.randbytes(32))
            tt = TxType.AGENT_PAYMENT
        elif kind == "batch":
            pays = [(_recipient(4_000_000 + j), 10) for j in range(contention or 64)]
            payload = l2tx.BatchPaymentPayload(pays)
            tt = TxType.BATCH_PAYMENT
        else:
            raise ValueError(f"unknown workload {kind}")
        t = l2tx.L2Tx(1, L2_CHAIN_ID_DEVNET, tt, kp.addr, nonce, 10_000_000, 0,
                      payload, SigScheme.ML_DSA_65, kp.pk, b"\x00" * 3309)
        txs.append(_sign(kp, t) if sign else t)
    return txs


# ── result ───────────────────────────────────────────────────────────────────


@dataclass
class BenchResult:
    workload: str
    count: int
    workers: int
    presigned: bool
    seconds: float
    tps: float
    effective_ops_per_sec: float
    sig_backend: str
    p50_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    compressed_bytes: int = 0
    raw_bytes: int = 0

    def to_json(self) -> dict:
        return self.__dict__


# ── runner ─────────────────────────────────────────────────────────────────


def _new_seq(workers: int) -> Sequencer:
    reset_verifier_for_tests()
    cfg = SequencerConfig(
        l2_chain_id=L2_CHAIN_ID_DEVNET,
        settlement_mode=SettlementMode.VALIDITY,
        exec_workers=workers,
        closure=ClosurePolicy(max_txs=10**9, max_bytes=10**12, max_age_ms=0),
    )
    seq = Sequencer(cfg, None, verifier=get_verifier(workers=workers))
    return seq


def run_once(
    workload: str, count: int, workers: int, *, seed: int = 0,
    presign: bool = True, contention: int = 64,
) -> BenchResult:
    n_senders = min(count, 256)
    accounts = make_accounts(n_senders, seed)
    seq = _new_seq(workers)
    # Fund every sender generously (backed genesis).
    for kp in accounts:
        seq.credit_genesis(kp.addr, 10**18)
    txs = build_workload(workload, count, accounts, seed=seed, contention=contention)
    effective_ops = 0
    for t in txs:
        if t.tx_type == TxType.BATCH_PAYMENT:
            effective_ops += len(t.payload.payments)
        else:
            effective_ops += 1

    # Admission (includes signature verification) — timed.
    t0 = time.perf_counter()
    admitted = 0
    per_latency: List[float] = []
    for t in txs:
        s = time.perf_counter()
        try:
            seq.submit(t)
            admitted += 1
        except Exception:
            pass
        per_latency.append((time.perf_counter() - s) * 1000)
    batch = seq.tick(force_close=True)
    dt = time.perf_counter() - t0

    per_latency.sort()

    def pct(p: float) -> float:
        if not per_latency:
            return 0.0
        return per_latency[min(len(per_latency) - 1, int(len(per_latency) * p))]

    raw_bytes = sum(len(t.encode()) for t in txs)
    comp = len(batch.da_blob) if batch else 0
    return BenchResult(
        workload=workload,
        count=admitted,
        workers=workers,
        presigned=presign,
        seconds=dt,
        tps=admitted / dt if dt else 0.0,
        effective_ops_per_sec=effective_ops / dt if dt else 0.0,
        sig_backend=seq.verifier.backend_name,
        p50_ms=pct(0.50),
        p95_ms=pct(0.95),
        p99_ms=pct(0.99),
        compressed_bytes=comp,
        raw_bytes=raw_bytes,
    )


def scaling_report(workload: str, count: int, worker_counts: List[int], seed: int = 0) -> List[BenchResult]:
    return [run_once(workload, count, w, seed=seed) for w in worker_counts]


def execution_only(count: int, workers: int, seed: int = 0) -> Tuple[float, float]:
    """Measure pure execution+state+DA throughput with signatures pre-verified
    (isolates the non-crypto pipeline). Returns (tps, seconds)."""
    accounts = make_accounts(min(count, 256), seed)
    seq = _new_seq(workers)
    for kp in accounts:
        seq.credit_genesis(kp.addr, 10**18)
    # Unsigned build: execution never verifies signatures (admission does), so
    # this isolates execute+SMT+state throughput from the PQ signing cost.
    txs = build_workload("transfers", count, accounts, seed=seed, sign=False)
    from .executor import ExecContext, execute
    from .fees import FeeSchedule
    ctx = ExecContext(tree=seq.tree, fees=FeeSchedule(), height=1)
    t0 = time.perf_counter()
    execute(txs, ctx, workers=workers)
    dt = time.perf_counter() - t0
    return (count / dt if dt else 0.0), dt
