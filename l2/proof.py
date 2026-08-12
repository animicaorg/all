"""Pluggable proof system.

The spec's end state is a validity/ZK rollup. A succinct ZK proof of the entire
post-quantum (ML-DSA-65) signature stack is not something we can honestly ship as
production cryptography today — so we do exactly what the spec's section 10
prescribes: put the proof system behind stable interfaces and ship the strongest
*actually verifiable* backend now. We never fake a proof.

Backends (selected by :class:`SettlementMode`):

* ``ReExecutionValidityBackend`` (VALIDITY) — the proof IS the DA blob plus the
  public inputs. Verification re-derives everything from the DA blob and the
  prior state: it re-verifies every signature, re-checks nonces/balances, re-runs
  the deterministic executor, and asserts the resulting roots equal the committed
  ones AND that no ANM was minted (Δ balances == deposited − withdrawn − fees
  that stayed in L2). This is a genuine, anyone-can-run verifier — it is simply
  not succinct. Its security is identical to a full node re-executing the batch,
  which is the honest trust-minimization 10.0.0 offers. A future
  ``ZkValidityBackend`` slots in here unchanged (same PublicInputs, same verify
  contract) once a PQ-friendly proving stack exists.
* ``OptimisticBackend`` (OPTIMISTIC) — commit now, allow a challenge window in
  which anyone submits the re-execution disproof; used during migration. Requires
  a bonded sequencer (bond/slashing tracked by the bridge/settlement layer).
* ``DevBackend`` (DEV) — no verification. Local development only; refuses to run
  unless explicitly in DEV mode.

Every backend exposes deterministic, bounded work so an L1 verifier primitive can
be given a hard resource limit.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from . import da
from . import tx as l2tx
from .constants import SettlementMode
from .executor import ExecContext, ExecResult, execute
from .fees import FeeSchedule
from .metrics import L2_METRICS
from .state import Account, StateTree


@dataclass(frozen=True)
class ProofPublicInputs:
    """The statement a proof attests to. Identical across backends so backends
    are swappable. All fields are in the batch header + DA commitment."""

    l2_chain_id: int
    batch_number: int
    prev_state_root: bytes
    new_state_root: bytes
    transactions_root: bytes
    receipts_root: bytes
    escrow_root: bytes
    data_root: bytes
    fees_collected: int
    deposited: int
    withdrawn: int

    def digest(self) -> bytes:
        m = hashlib.sha3_256()
        m.update(b"animica.l2.proof.pi.v1")
        m.update(self.l2_chain_id.to_bytes(8, "big"))
        m.update(self.batch_number.to_bytes(8, "big"))
        for r in (
            self.prev_state_root,
            self.new_state_root,
            self.transactions_root,
            self.receipts_root,
            self.escrow_root,
            self.data_root,
        ):
            m.update(r)
        m.update(self.fees_collected.to_bytes(16, "big"))
        m.update(self.deposited.to_bytes(16, "big"))
        m.update(self.withdrawn.to_bytes(16, "big"))
        return m.digest()


@dataclass
class Proof:
    backend: str
    public_inputs: ProofPublicInputs
    payload: bytes  # backend-specific; for re-execution it's the DA blob

    def to_json(self) -> dict:
        return {
            "backend": self.backend,
            "publicInputsDigest": "0x" + self.public_inputs.digest().hex(),
            "payloadBytes": len(self.payload),
        }


class ProofError(Exception):
    pass


class ProofBackend(ABC):
    mode: SettlementMode
    name: str

    @abstractmethod
    def generate(self, pi: ProofPublicInputs, da_blob: bytes) -> Proof: ...

    @abstractmethod
    def verify(
        self,
        proof: Proof,
        prev_accounts: Dict[bytes, Account],
        prev_escrows: dict,
        *,
        fees: Optional[FeeSchedule] = None,
        fee_recipient: Optional[bytes] = None,
        height: int = 0,
        deposit_authorizer: Optional[Callable[[bytes, bytes, int], bool]] = None,
        verify_signatures: bool = True,
    ) -> bool: ...


class ReExecutionValidityBackend(ProofBackend):
    mode = SettlementMode.VALIDITY
    name = "reexec-validity-v1"

    def generate(self, pi: ProofPublicInputs, da_blob: bytes) -> Proof:
        with L2_METRICS.time("proof_generation_seconds"):
            # Sanity: the blob must reconstruct and match the committed data_root.
            if not da.verify_blob(da_blob, pi.data_root):
                raise ProofError("DA blob does not match committed data_root")
        L2_METRICS.inc("proofs_generated_total")
        return Proof(self.name, pi, da_blob)

    def verify(
        self,
        proof: Proof,
        prev_accounts: Dict[bytes, Account],
        prev_escrows: dict,
        *,
        fees: Optional[FeeSchedule] = None,
        fee_recipient: Optional[bytes] = None,
        height: int = 0,
        deposit_authorizer: Optional[Callable[[bytes, bytes, int], bool]] = None,
        verify_signatures: bool = True,
    ) -> bool:
        """Re-derive the whole transition from the DA blob + prior state and
        assert every committed root/aggregate. This is the actual guarantee."""
        pi = proof.public_inputs
        # 1. DA availability + integrity.
        if not da.verify_blob(proof.payload, pi.data_root):
            return False
        txs = da.decode_batch(proof.payload)

        # 2. Prior state root must match the claimed prev_state_root.
        tree = StateTree(prev_accounts)
        if tree.root() != pi.prev_state_root:
            return False

        # 3. Signatures: every user-signed tx must carry a valid ML-DSA-65 sig
        #    over its body, and the pubkey must hash to the sender. This is the
        #    "all signatures are valid" clause. (Toggleable only for tests.)
        if verify_signatures:
            from .crypto import get_verifier

            v = get_verifier()
            items = []
            checks = []
            from .constants import PROTOCOL_MINTED_TYPES

            for t in txs:
                if t.tx_type in PROTOCOL_MINTED_TYPES:
                    continue
                if l2tx.address_from_pubkey(t.pubkey) != t.sender:
                    return False  # pubkey/sender mismatch
                items.append((t.pubkey, t.signing_hash(), t.signature))
                checks.append(t)
            if items and not all(v.verify_batch(items)):
                return False

        # 4. Re-execute deterministically and compare all roots + aggregates.
        ctx = ExecContext(
            tree=tree,
            escrows=dict(prev_escrows),
            fees=fees or FeeSchedule(),
            height=height or 0,
            fee_recipient=fee_recipient,
            deposit_authorizer=deposit_authorizer,
        )
        result: ExecResult = execute(txs, ctx, workers=1)
        if result.new_state_root != pi.new_state_root:
            return False
        if result.transactions_root != pi.transactions_root:
            return False
        if result.receipts_root != pi.receipts_root:
            return False
        if result.escrow_root != pi.escrow_root:
            return False
        if result.fees_collected != pi.fees_collected:
            return False
        if result.deposited != pi.deposited or result.withdrawn != pi.withdrawn:
            return False

        # 5. No ANM minted: the net change in total L2 balance equals exactly
        #    deposits credited minus amounts withdrawn (burned). Fees moved to
        #    the treasury are inside L2 and cancel.
        before = sum(a.balance for a in prev_accounts.values())
        after = sum(a.balance for a in tree.accounts().values())
        if after - before != result.deposited - result.withdrawn:
            return False
        return True


class OptimisticBackend(ProofBackend):
    mode = SettlementMode.OPTIMISTIC
    name = "optimistic-v1"

    def __init__(self, challenge_window_batches: int = 100) -> None:
        self.challenge_window = challenge_window_batches
        self._reexec = ReExecutionValidityBackend()

    def generate(self, pi: ProofPublicInputs, da_blob: bytes) -> Proof:
        # Optimistic: the "proof" is just the commitment + DA; validity is
        # assumed unless challenged within the window.
        if not da.verify_blob(da_blob, pi.data_root):
            raise ProofError("DA blob does not match committed data_root")
        return Proof(self.name, pi, da_blob)

    def verify(self, proof: Proof, prev_accounts, prev_escrows, **kw) -> bool:
        # A challenger runs the SAME re-execution check to produce a fraud proof.
        return self._reexec.verify(
            Proof(ReExecutionValidityBackend.name, proof.public_inputs, proof.payload),
            prev_accounts,
            prev_escrows,
            **kw,
        )


class DevBackend(ProofBackend):
    mode = SettlementMode.DEV
    name = "dev-noop"

    def generate(self, pi: ProofPublicInputs, da_blob: bytes) -> Proof:
        return Proof(self.name, pi, da_blob)

    def verify(self, proof: Proof, prev_accounts, prev_escrows, **kw) -> bool:
        # DEV mode performs NO cryptographic verification. Never use off-devnet.
        return True


def make_backend(mode: SettlementMode) -> ProofBackend:
    if mode == SettlementMode.VALIDITY:
        return ReExecutionValidityBackend()
    if mode == SettlementMode.OPTIMISTIC:
        return OptimisticBackend()
    if mode == SettlementMode.DEV:
        return DevBackend()
    raise ProofError(f"unknown settlement mode {mode}")
