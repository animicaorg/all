"""Animica L2 Python SDK (spec §41) — ergonomic, finality-honest client.

This is the developer-facing client for the Animica 10.0.0 ANM-native L2.
It builds, signs (ML-DSA-65), and submits L2 transactions over JSON-RPC and
tracks them through their *explicit* lifecycle. The SDK deliberately does NOT
hide finality levels behind a single ``wait()``: sequencer acceptance is a
promise, a validity proof is a much stronger statement, and only L1 finality
is settlement. Callers must pick one — the three waits are distinct methods.

Lifecycle (see ``l2.constants.TxStatus``)::

    RECEIVED -> VALIDATED -> SOFT_CONFIRMED -> BATCHED -> PROVEN
             -> L1_SUBMITTED -> L1_FINALIZED
    (terminal failures: FAILED / REVERTED)

Finality levels exposed on :class:`L2TxHandle`:

- ``wait_soft_confirmation()`` — the sequencer has executed the tx and will
  include it in the next batch. **This is NOT finality.** A malicious or
  crashed sequencer can drop a soft-confirmed tx; treat it like a payment
  processor's "accepted" — fine for coffee, not for closing escrow.
- ``wait_proven()`` — the tx is inside a closed batch whose state transition
  carries a validity proof over published DA. Anyone can re-verify it.
- ``wait_l1_finalized()`` — the batch's state root is anchored on Animica L1
  and buried past L1 finality depth. This is true settlement.

Example (spec §41)::

    from animica.l2_sdk import AnimicaL2, L2Signer

    signer = L2Signer.from_seed(bytes.fromhex("11" * 32))
    l2 = AnimicaL2(rpc_url="http://127.0.0.1:8545", signer=signer)

    # single transfer — amounts are integer nanos (1 ANM = 10**9 nanos)
    h = l2.send("anim1q...", 5_000_000_000)
    h.wait_soft_confirmation()          # sequencer promise — NOT final
    h.wait_proven()                     # validity-proven batch
    h.wait_l1_finalized(timeout=3600)   # anchored + finalized on L1

    # high-throughput path: ONE signature authorizes many payouts
    h = l2.send_many([(worker_a, 1_000_000), (worker_b, 2_000_000)])
    h.wait_proven()

    # machine-to-machine
    l2.agent_payment(provider, 500_000, agent_id=b"\\xaa" * 32,
                     task_hash=b"\\xbb" * 32)
    l2.inference_payment(provider, 250_000,
                         request_hash=b"\\xcc" * 32, model_id="kimi-k3")

    # exit to L1
    h = l2.withdraw(l1_recipient, 10_000_000_000)
    h.wait_l1_finalized()               # only then is the exit claimable

Concurrency note: the SDK is synchronous (plain blocking HTTP via urllib —
no extra dependencies). For async apps, run calls in a thread
(``asyncio.to_thread(l2.send, ...)``) or supply your own ``transport``
callable; every RPC the SDK makes goes through that single hook.
"""

from __future__ import annotations

import binascii
import dataclasses
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple, Union

from l2 import tx as l2tx
from l2.constants import (
    HASH_LEN,
    L2_CHAIN_ID_DEVNET,
    NANOS_PER_ANM,
    PUBKEY_LEN,
    SIG_LEN,
    SigScheme,
    TxStatus,
    TxType,
)
from l2.fees import FeeSchedule
from pq.py.algs import ml_dsa_65

__all__ = [
    "AnimicaL2",
    "L2Signer",
    "L2TxHandle",
    "L2SdkError",
    "L2RpcError",
    "L2TxFailed",
    "HttpTransport",
    "in_process_transport",
    "NANOS_PER_ANM",
]

# A transport is any callable (method_name, params_list) -> result. The SDK
# never talks to the network except through one of these, which makes it
# trivial to test in-process or to swap in an async/pooled implementation.
Transport = Callable[[str, list], Any]

DEFAULT_RPC_URL = "http://127.0.0.1:8545"
RPC_URL_ENV = "ANIMICA_L2_RPC_URL"

# Ordering of the non-terminal lifecycle used by the wait_* methods. Higher
# rank strictly implies every lower rank has been passed.
_STATUS_RANK = {
    TxStatus.RECEIVED.value: 0,
    TxStatus.VALIDATED.value: 1,
    TxStatus.SOFT_CONFIRMED.value: 2,
    TxStatus.BATCHED.value: 3,
    TxStatus.PROVEN.value: 4,
    TxStatus.L1_SUBMITTED.value: 5,
    TxStatus.L1_FINALIZED.value: 6,
}
_TERMINAL_FAILURES = {TxStatus.FAILED.value, TxStatus.REVERTED.value}


# ── errors ───────────────────────────────────────────────────────────────────


class L2SdkError(Exception):
    """Base class for all SDK errors."""


class L2RpcError(L2SdkError):
    """The RPC endpoint returned a JSON-RPC error (or was unreachable)."""

    def __init__(self, message: str, code: Optional[int] = None, data: Any = None):
        super().__init__(message)
        self.code = code
        self.data = data


class L2TxFailed(L2SdkError):
    """The transaction reached a terminal failure state (FAILED / REVERTED)."""

    def __init__(self, txid_hex: str, status: str, reason: str = ""):
        super().__init__(f"tx {txid_hex} {status}: {reason or 'no reason recorded'}")
        self.txid_hex = txid_hex
        self.status = status
        self.reason = reason


# ── transports ───────────────────────────────────────────────────────────────


class HttpTransport:
    """Blocking JSON-RPC 2.0 over HTTP using stdlib urllib (no dependencies)."""

    def __init__(self, rpc_url: str, timeout: float = 30.0):
        self.rpc_url = rpc_url
        self.timeout = timeout
        self._id = 0

    def __call__(self, method: str, params: list) -> Any:
        self._id += 1
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        ).encode("utf-8")
        req = urllib.request.Request(
            self.rpc_url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            # JSON-RPC servers commonly wrap errors in HTTP 4xx/5xx bodies.
            try:
                body = e.read()
                data = json.loads(body)
            except Exception:
                raise L2RpcError(f"HTTP {e.code} from {self.rpc_url}") from e
            err = data.get("error") or {}
            raise L2RpcError(
                str(err.get("message", f"HTTP {e.code}")),
                code=err.get("code"),
                data=err.get("data"),
            ) from e
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            raise L2RpcError(f"RPC unreachable at {self.rpc_url}: {e}") from e
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            snippet = body[:200].decode("utf-8", "replace")
            raise L2RpcError(f"non-JSON RPC response: {snippet!r}") from e
        if isinstance(data, dict) and data.get("error"):
            err = data["error"]
            if isinstance(err, dict):
                raise L2RpcError(
                    str(err.get("message", err)), code=err.get("code"), data=err.get("data")
                )
            raise L2RpcError(str(err))
        return data.get("result") if isinstance(data, dict) else data


def in_process_transport() -> Transport:
    """Transport that invokes the node's registered ``l2_*`` RPC handlers
    directly in this process — no HTTP, no server. Requires the repo's ``rpc``
    package on the path and an :class:`l2.node.L2Node` singleton (created
    lazily by the handlers via ``get_l2_node()``). Used by tests and by tools
    that embed a sequencer.
    """
    from rpc import methods as rpc_methods
    from rpc import errors as rpc_errors
    from rpc.methods import l2 as _l2_module  # noqa: F401  (registers l2_* handlers)

    table = rpc_methods.get_methods() if hasattr(rpc_methods, "get_methods") else None

    def call(method: str, params: list) -> Any:
        registry = table if table is not None else {}
        m = registry.get(method)
        if m is None:
            # Fall back to the live registry (handles late registration).
            try:
                m = rpc_methods.get_methods()[method]
            except Exception:
                raise L2RpcError(f"method not registered in-process: {method}")
        try:
            return m.func(*params)
        except rpc_errors.RpcError as e:  # structured server-side error
            raise L2RpcError(str(e), code=getattr(e, "code", None)) from e

    return call


# ── signer ───────────────────────────────────────────────────────────────────


class L2Signer:
    """Holds an ML-DSA-65 keypair and signs L2 transactions.

    The L2 account address is ``sha3_256(alg_id || pubkey)[:32]`` — identical
    to the L1 derivation, so an L1 ``anim1…`` account is the same 32-byte key
    on L2.
    """

    def __init__(self, sk: bytes, pk: bytes):
        self.sk = bytes(sk)
        self.pk = bytes(pk)
        if len(self.pk) != PUBKEY_LEN:
            raise L2SdkError(
                f"ML-DSA-65 public key must be {PUBKEY_LEN} bytes, got {len(self.pk)}"
            )
        self.address: bytes = l2tx.address_from_pubkey(self.pk)

    @classmethod
    def from_seed(cls, seed: Union[bytes, str]) -> "L2Signer":
        """Deterministic keypair from a 32-byte seed (bytes or hex string)."""
        if isinstance(seed, str):
            s = seed[2:] if seed.startswith(("0x", "0X")) else seed
            seed = bytes.fromhex(s)
        if len(seed) != 32:
            raise L2SdkError(f"seed must be 32 bytes, got {len(seed)}")
        sk, pk = ml_dsa_65.keypair(seed)
        return cls(sk, pk)

    @classmethod
    def generate(cls) -> "L2Signer":
        """Fresh random keypair from the OS CSPRNG."""
        sk, pk = ml_dsa_65.keypair(os.urandom(32))
        return cls(sk, pk)

    @property
    def address_hex(self) -> str:
        return "0x" + self.address.hex()

    def sign_tx(self, t: l2tx.L2Tx) -> l2tx.L2Tx:
        """Sign ``t`` in place (sets pubkey + signature) and return it. The
        signature covers the domain-separated hash of the body bytes only, so
        sender/nonce/fee/payload are all bound."""
        if t.sender != self.address:
            raise L2SdkError("tx.sender does not match this signer's address")
        t.sig_scheme = SigScheme.ML_DSA_65
        t.pubkey = self.pk
        t.signature = ml_dsa_65.sign(self.sk, t.signing_hash())
        return t


# ── tx handle ────────────────────────────────────────────────────────────────


class L2TxHandle:
    """A submitted L2 transaction plus explicit finality-level waits.

    There is intentionally no generic ``wait()``: soft confirmation, proof,
    and L1 finality are different guarantees and callers must choose one.
    ``SOFT_CONFIRMED`` means only that the sequencer executed the tx and
    promised inclusion — it is **not** settlement.
    """

    def __init__(self, client: "AnimicaL2", txid: bytes, tx: Optional[l2tx.L2Tx] = None):
        self._client = client
        self.txid: bytes = txid
        self.tx = tx  # the signed L2Tx, when built by this SDK

    @property
    def txid_hex(self) -> str:
        return "0x" + self.txid.hex()

    def status(self) -> dict:
        """One ``l2_getTransaction`` snapshot (status/batch/receipt/reason)."""
        return self._client.status(self.txid)

    # ── the three finality levels (distinct on purpose) ──

    def wait_soft_confirmation(self, timeout: float = 30.0, poll: float = 0.05) -> dict:
        """Block until the sequencer has executed the tx into a closing batch
        (``SOFT_CONFIRMED`` or beyond). This is a sequencer *promise*, not
        finality — do not treat it as settlement for high-value flows."""
        return self._wait_rank(_STATUS_RANK[TxStatus.SOFT_CONFIRMED.value], timeout, poll)

    def wait_proven(self, timeout: float = 120.0, poll: float = 0.1) -> dict:
        """Block until the tx's batch carries a validity proof (``PROVEN`` or
        beyond). Anyone can re-verify the state transition from published DA;
        much stronger than soft confirmation, but not yet anchored on L1."""
        return self._wait_rank(_STATUS_RANK[TxStatus.PROVEN.value], timeout, poll)

    def wait_l1_finalized(self, timeout: float = 3600.0, poll: float = 1.0) -> dict:
        """Block until the batch is anchored on Animica L1 and past L1
        finality depth (``L1_FINALIZED``). This is true settlement — the only
        level that survives a malicious sequencer."""
        return self._wait_rank(_STATUS_RANK[TxStatus.L1_FINALIZED.value], timeout, poll)

    def _wait_rank(self, want: int, timeout: float, poll: float) -> dict:
        deadline = time.monotonic() + timeout
        last: dict = {}
        while True:
            last = self.status()
            st = str(last.get("status", "UNKNOWN"))
            if st in _TERMINAL_FAILURES:
                raise L2TxFailed(self.txid_hex, st, str(last.get("reason") or ""))
            rank = _STATUS_RANK.get(st)
            if rank is not None and rank >= want:
                return last
            if time.monotonic() >= deadline:
                want_name = next(k for k, v in _STATUS_RANK.items() if v == want)
                raise TimeoutError(
                    f"tx {self.txid_hex} did not reach {want_name} within {timeout}s "
                    f"(last status: {st})"
                )
            time.sleep(poll)

    def __repr__(self) -> str:  # pragma: no cover
        return f"L2TxHandle({self.txid_hex})"


# ── client ───────────────────────────────────────────────────────────────────


class AnimicaL2:
    """Synchronous Animica L2 client.

    Args:
        rpc_url: JSON-RPC endpoint serving the ``l2_*`` methods. Defaults to
            ``$ANIMICA_L2_RPC_URL`` or ``http://127.0.0.1:8545``.
        l2_chain_id: replay domain baked into every signature. When ``None``
            it is fetched once from the node (``l2_chainId``) — the safe
            default, since signing for the wrong chain id is an admission
            failure, never a silent success.
        signer: default :class:`L2Signer` used by value-moving calls; each
            call also accepts ``key=`` to override per-call.
        transport: optional ``(method, params) -> result`` callable replacing
            HTTP entirely (see :func:`in_process_transport`).
        timeout: HTTP timeout in seconds (ignored for custom transports).

    All amounts everywhere are **integers in nanos** (1 ANM = 10**9 nanos);
    floats are rejected outright to prevent precision-loss bugs.
    """

    def __init__(
        self,
        rpc_url: Optional[str] = None,
        l2_chain_id: Optional[int] = None,
        signer: Optional[L2Signer] = None,
        *,
        transport: Optional[Transport] = None,
        timeout: float = 30.0,
        fee_schedule: Optional[FeeSchedule] = None,
    ):
        if transport is not None:
            self._call: Transport = transport
        else:
            url = rpc_url or os.environ.get(RPC_URL_ENV) or DEFAULT_RPC_URL
            self._call = HttpTransport(url, timeout=timeout)
        self._chain_id = l2_chain_id
        self.signer = signer
        # Local mirror of the sequencer's fee schedule, used to pick a default
        # fee when the caller doesn't pass one. `estimate_fee()` asks the node
        # itself and is authoritative if an operator runs custom fees.
        self._fees = fee_schedule or FeeSchedule()

    # ── identity / reads ──

    @property
    def chain_id(self) -> int:
        if self._chain_id is None:
            self._chain_id = int(self._call("l2_chainId", []))
        return self._chain_id

    def balance(self, addr: Union[bytes, str, None] = None) -> int:
        """Instant (sequencer-view) balance in nanos."""
        info = self._call("l2_getBalance", [self._addr_param(addr)])
        return int(info["balance"])

    def balance_info(self, addr: Union[bytes, str, None] = None) -> dict:
        """Full ``l2_getBalance`` record (balance, nonce, pendingNonce)."""
        return self._call("l2_getBalance", [self._addr_param(addr)])

    def nonce(self, addr: Union[bytes, str, None] = None) -> int:
        """Confirmed next nonce for the address."""
        return int(self._call("l2_getNonce", [self._addr_param(addr)])["nonce"])

    def pending_nonce(self, addr: Union[bytes, str, None] = None) -> int:
        """Pending-aware next nonce — what a new tx from this address should
        use while earlier txs are still queued in the sequencer."""
        return int(self._call("l2_getNonce", [self._addr_param(addr)])["pendingNonce"])

    def status(self, txid: Union[bytes, str]) -> dict:
        """Lifecycle record of a tx: status/batch/receipt/reason/receivedMs.
        ``status`` is one of the ``l2.constants.TxStatus`` names or UNKNOWN."""
        return self._call("l2_getTransaction", [self._hex_param(txid)])

    def state_root(self) -> dict:
        """Current head: ``{"batch": n, "stateRoot": "0x…"}``."""
        return self._call("l2_getStateRoot", [])

    def account_proof(self, addr: Union[bytes, str, None] = None) -> dict:
        """SMT membership/non-membership proof for an address against the
        current state root (balance/nonce + sibling path)."""
        return self._call("l2_getAccountProof", [self._addr_param(addr)])

    def withdrawal_proof(self, nullifier: Union[bytes, str]) -> Optional[dict]:
        """Proof data needed to claim a finalized withdrawal on L1, or None
        if the nullifier is unknown."""
        return self._call("l2_getWithdrawalProof", [self._hex_param(nullifier)])

    def estimate_fee(self, t: l2tx.L2Tx) -> dict:
        """Ask the node for the fee breakdown of a draft or signed tx:
        ``{"base","da","exec","total"}`` in nanos. Drafts (unsigned) are
        padded with a placeholder key/signature purely for wire encoding —
        the node's estimator only measures size, it never verifies."""
        draft = t
        if len(t.pubkey) != PUBKEY_LEN or len(t.signature) != SIG_LEN:
            draft = dataclasses.replace(
                t, pubkey=b"\x00" * PUBKEY_LEN, signature=b"\x00" * SIG_LEN
            )
        return self._call("l2_estimateFee", ["0x" + draft.encode().hex()])

    # ── writes (build + sign + submit) ──

    def send(
        self,
        to: Union[bytes, str],
        amount: int,
        *,
        key: Optional[L2Signer] = None,
        fee: Optional[int] = None,
        expiry: int = 0,
        memo: Union[bytes, str] = b"",
    ) -> L2TxHandle:
        """Plain TRANSFER of ``amount`` nanos to ``to``.

        Returns immediately after sequencer admission with an
        :class:`L2TxHandle`; nothing is final yet — pick a finality level via
        the handle's ``wait_*`` methods."""
        payload = l2tx.TransferPayload(
            self._to_addr(to), self._amount(amount), self._memo(memo)
        )
        return self._submit(TxType.TRANSFER, payload, key=key, fee=fee, expiry=expiry)

    def send_many(
        self,
        payments: Sequence[Tuple[Union[bytes, str], int]],
        *,
        key: Optional[L2Signer] = None,
        fee: Optional[int] = None,
        expiry: int = 0,
    ) -> L2TxHandle:
        """High-throughput path: ONE ``BATCH_PAYMENT`` tx whose single
        ML-DSA-65 signature authorizes every ``(recipient, amount)`` pair.
        Vastly cheaper than N transfers (one signature verification, one
        nonce, shared envelope overhead) — use it for payouts and fan-out.
        The whole batch executes atomically: if the sender can't cover
        ``sum(amounts) + fee``, no recipient is paid."""
        if not payments:
            raise L2SdkError("send_many requires at least one (to, amount) pair")
        pairs: List[Tuple[bytes, int]] = [
            (self._to_addr(to), self._amount(amt)) for to, amt in payments
        ]
        payload = l2tx.BatchPaymentPayload(pairs)
        return self._submit(TxType.BATCH_PAYMENT, payload, key=key, fee=fee, expiry=expiry)

    def pay(
        self,
        to: Union[bytes, str],
        amount: int,
        memo: Union[bytes, str],
        *,
        key: Optional[L2Signer] = None,
        fee: Optional[int] = None,
        expiry: int = 0,
    ) -> L2TxHandle:
        """Animica-Pay transfer: like :meth:`send` but typed ``PAY`` with the
        invoice/order reference in ``memo`` (max 256 bytes)."""
        payload = l2tx.TransferPayload(
            self._to_addr(to), self._amount(amount), self._memo(memo)
        )
        return self._submit(TxType.PAY, payload, key=key, fee=fee, expiry=expiry)

    def agent_payment(
        self,
        provider: Union[bytes, str],
        amount: int,
        agent_id: Union[bytes, str],
        task_hash: Union[bytes, str],
        *,
        key: Optional[L2Signer] = None,
        fee: Optional[int] = None,
        expiry: int = 0,
    ) -> L2TxHandle:
        """Machine-to-machine payment bound to an agent identity and an
        off-chain task/receipt hash (both 32 bytes)."""
        payload = l2tx.AgentPaymentPayload(
            self._to_addr(provider),
            self._amount(amount),
            self._hash32(agent_id, "agent_id"),
            self._hash32(task_hash, "task_hash"),
        )
        return self._submit(TxType.AGENT_PAYMENT, payload, key=key, fee=fee, expiry=expiry)

    def inference_payment(
        self,
        provider: Union[bytes, str],
        amount: int,
        request_hash: Union[bytes, str],
        model_id: Union[bytes, str],
        *,
        key: Optional[L2Signer] = None,
        fee: Optional[int] = None,
        expiry: int = 0,
    ) -> L2TxHandle:
        """Payment bound to an AICF inference request receipt. ``model_id``
        accepts a short string (e.g. ``"kimi-k3"``) and is right-padded to the
        32-byte wire field."""
        payload = l2tx.InferencePaymentPayload(
            self._to_addr(provider),
            self._amount(amount),
            self._hash32(request_hash, "request_hash"),
            self._model_id(model_id),
        )
        return self._submit(
            TxType.INFERENCE_PAYMENT, payload, key=key, fee=fee, expiry=expiry
        )

    def withdraw(
        self,
        l1_recipient: Union[bytes, str],
        amount: int,
        *,
        key: Optional[L2Signer] = None,
        fee: Optional[int] = None,
        expiry: int = 0,
    ) -> L2TxHandle:
        """Burn on L2 and unlock ``amount`` nanos claimable by
        ``l1_recipient`` on Animica L1. The claim only becomes valid once the
        containing batch is anchored — wait for ``wait_l1_finalized()`` before
        attempting the L1 claim; PROVEN is not enough for an exit."""
        payload = l2tx.WithdrawPayload(self._to_addr(l1_recipient), self._amount(amount))
        return self._submit(TxType.WITHDRAW, payload, key=key, fee=fee, expiry=expiry)

    def send_raw(self, raw: Union[bytes, str]) -> L2TxHandle:
        """Submit an already-encoded signed tx (bytes or 0x-hex)."""
        if isinstance(raw, str):
            raw = self._hexb(raw)
        txid_hex = self._call("l2_sendRawTransaction", ["0x" + raw.hex()])
        return L2TxHandle(self, self._hexb(txid_hex))

    # ── internals ──

    def _submit(
        self,
        tx_type: TxType,
        payload: object,
        *,
        key: Optional[L2Signer],
        fee: Optional[int],
        expiry: int,
    ) -> L2TxHandle:
        signer = key or self.signer
        if signer is None:
            raise L2SdkError(
                "no signer: pass signer= to AnimicaL2(...) or key= to this call"
            )
        t = l2tx.L2Tx(
            version=1,
            l2_chain_id=self.chain_id,
            tx_type=tx_type,
            sender=signer.address,
            nonce=self.pending_nonce(signer.address),
            fee=0,
            expiry=int(expiry),
            payload=payload,
            sig_scheme=SigScheme.ML_DSA_65,
            pubkey=b"",
            signature=b"",
        )
        t.fee = self._amount(fee) if fee is not None else self._default_fee(t)
        signer.sign_tx(t)
        txid_hex = self._call("l2_sendRawTransaction", ["0x" + t.encode().hex()])
        txid = self._hexb(txid_hex)
        # Sanity: the node's txid must match the locally computed one.
        local = t.txid()
        if txid != local:
            raise L2SdkError(
                f"node returned txid {txid.hex()} but local encoding hashes to "
                f"{local.hex()} — refusing to track a mismatched tx"
            )
        return L2TxHandle(self, txid, tx=t)

    def _default_fee(self, t: l2tx.L2Tx) -> int:
        """Fixed point of the fee schedule: the fee value is itself inside the
        fee-priced body (uvarint), so iterate until stable (≤ a few rounds)."""
        fee = 0
        for _ in range(6):
            t.fee = fee
            need = self._fees.fee_for(t)
            if need == fee:
                break
            fee = need
        t.fee = fee
        return fee

    def _addr_param(self, addr: Union[bytes, str, None]) -> str:
        if addr is None:
            if self.signer is None:
                raise L2SdkError("no address given and no default signer configured")
            return self.signer.address_hex
        return "0x" + self._to_addr(addr).hex()

    @staticmethod
    def _to_addr(value: Union[bytes, str]) -> bytes:
        """Accept a 32-byte key, 0x-hex, or bech32m ``anim1…`` address."""
        if isinstance(value, (bytes, bytearray)):
            b = bytes(value)
            if len(b) != 32:
                raise L2SdkError(f"address must be 32 bytes, got {len(b)}")
            return b
        if isinstance(value, L2Signer):  # ergonomic: pass a signer as "to"
            return value.address
        if not isinstance(value, str):
            raise L2SdkError(f"unsupported address type {type(value).__name__}")
        s = value.strip()
        if s.startswith(("0x", "0X")):
            try:
                b = binascii.unhexlify(s[2:])
            except binascii.Error as e:
                raise L2SdkError(f"bad hex address {s!r}") from e
            if len(b) != 32:
                raise L2SdkError("hex address must decode to 32 bytes")
            return b
        if s.startswith("anim1"):
            try:
                from pq.py.address import decode_address  # type: ignore

                _hrp, _alg, digest = decode_address(s)
            except Exception as e:
                raise L2SdkError(f"could not decode anim1 address: {e}") from e
            return bytes(digest)[:32]
        raise L2SdkError(f"unrecognized address format: {s!r}")

    @staticmethod
    def _amount(v: Any) -> int:
        """Amounts are integer nanos; floats are rejected to prevent silent
        precision loss (0.1 ANM must be written 100_000_000)."""
        if isinstance(v, bool) or not isinstance(v, int):
            raise L2SdkError(
                f"amount/fee must be an int in nanos (1 ANM = {NANOS_PER_ANM}); "
                f"got {type(v).__name__}"
            )
        if v < 0:
            raise L2SdkError("amount/fee must be non-negative")
        return v

    @staticmethod
    def _memo(memo: Union[bytes, str]) -> bytes:
        return memo.encode("utf-8") if isinstance(memo, str) else bytes(memo)

    @staticmethod
    def _hash32(value: Union[bytes, str], name: str) -> bytes:
        """32-byte identifier: raw bytes, 0x-hex, or any other string which is
        deterministically hashed (sha3-256 of UTF-8) into the field."""
        if isinstance(value, (bytes, bytearray)):
            b = bytes(value)
            if len(b) != HASH_LEN:
                raise L2SdkError(f"{name} must be {HASH_LEN} bytes, got {len(b)}")
            return b
        if isinstance(value, str):
            s = value.strip()
            if s.startswith(("0x", "0X")):
                try:
                    b = binascii.unhexlify(s[2:])
                except binascii.Error as e:
                    raise L2SdkError(f"bad hex for {name}") from e
                if len(b) != HASH_LEN:
                    raise L2SdkError(f"{name} hex must decode to {HASH_LEN} bytes")
                return b
            return hashlib.sha3_256(s.encode("utf-8")).digest()
        raise L2SdkError(f"{name} must be bytes or str")

    @staticmethod
    def _model_id(value: Union[bytes, str]) -> bytes:
        """Model id is an opaque 32-byte wire field; short ids are right-padded
        with zero bytes (matching the payload's documented encoding)."""
        b = value.encode("utf-8") if isinstance(value, str) else bytes(value)
        if len(b) > HASH_LEN:
            raise L2SdkError(f"model_id must be <= {HASH_LEN} bytes, got {len(b)}")
        return b.ljust(HASH_LEN, b"\x00")

    def _hex_param(self, value: Union[bytes, str]) -> str:
        """Normalize a txid/nullifier (bytes or hex string) to 0x-hex."""
        return "0x" + self._hexb(value).hex()

    @staticmethod
    def _hexb(value: Union[bytes, str]) -> bytes:
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        s = value[2:] if value.startswith(("0x", "0X")) else value
        try:
            return binascii.unhexlify(s)
        except binascii.Error as e:
            raise L2SdkError(f"bad hex value {value!r}") from e
