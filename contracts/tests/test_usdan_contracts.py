from __future__ import annotations

import hashlib
import hmac
import importlib.util
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest


class Revert(Exception):
    pass


@dataclass
class RuntimeContract:
    address: bytes
    module: types.ModuleType
    storage: dict[bytes, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    caller: bytes = b""

    def call(self, fn_name: str, *args: Any, caller: bytes | None = None) -> Any:
        if fn_name not in self.module.__dict__:
            raise AttributeError(f"function not found: {fn_name}")
        prev_runtime = _CURRENT.runtime
        prev_caller = self.caller
        _CURRENT.runtime = self
        self.caller = self.address if caller is None else bytes(caller)
        try:
            return self.module.__dict__[fn_name](*args)
        finally:
            self.caller = prev_caller
            _CURRENT.runtime = prev_runtime


@dataclass
class RuntimeState:
    runtime: RuntimeContract | None = None
    contracts: dict[bytes, RuntimeContract] = field(default_factory=dict)
    block_height: int = 1
    chain_id: int = 1337


_CURRENT = RuntimeState()


# stdlib.storage shim
storage_mod = types.ModuleType("stdlib.storage")


def _storage_get(key: bytes, default: Any = None) -> Any:
    rt = _require_runtime()
    return rt.storage.get(bytes(key), default)


def _storage_set(key: bytes, value: Any) -> None:
    rt = _require_runtime()
    rt.storage[bytes(key)] = value


def _storage_delete(key: bytes) -> None:
    rt = _require_runtime()
    rt.storage.pop(bytes(key), None)


storage_mod.get = _storage_get  # type: ignore[attr-defined]
storage_mod.set = _storage_set  # type: ignore[attr-defined]
storage_mod.delete = _storage_delete  # type: ignore[attr-defined]


# stdlib.events shim
events_mod = types.ModuleType("stdlib.events")


def _events_emit(name: bytes, args: Any | None = None) -> None:
    rt = _require_runtime()
    rt.events.append({"name": bytes(name), "args": args})


events_mod.emit = _events_emit  # type: ignore[attr-defined]


# stdlib.hash shim
hash_mod = types.ModuleType("stdlib.hash")


def _sha3_256(data: bytes) -> bytes:
    return hashlib.sha3_256(bytes(data)).digest()


hash_mod.sha3_256 = _sha3_256  # type: ignore[attr-defined]


# stdlib.pq_verify shim
pq_verify_mod = types.ModuleType("stdlib.pq_verify")


def _pq_verify(pubkey: bytes, message: bytes, sig: bytes, scheme: str = "Dilithium3") -> bool:
    _ = scheme
    expected = hmac.new(bytes(pubkey), bytes(message), hashlib.sha256).digest()
    return hmac.compare_digest(expected, bytes(sig))


pq_verify_mod.verify = _pq_verify  # type: ignore[attr-defined]


# stdlib.abi shim
abi_mod = types.ModuleType("stdlib.abi")


def _require_runtime() -> RuntimeContract:
    if _CURRENT.runtime is None:
        raise RuntimeError("no active runtime")
    return _CURRENT.runtime


def _abi_require(cond: bool, message: bytes | str = b"require_failed") -> None:
    if not cond:
        raise Revert(message)


def _abi_revert(message: bytes | str = b"revert") -> None:
    raise Revert(message)


def _abi_caller() -> bytes:
    return _require_runtime().caller


def _abi_msg_sender() -> bytes:
    return _abi_caller()


def _abi_contract_address() -> bytes:
    return _require_runtime().address


def _abi_chain_id() -> int:
    return int(_CURRENT.chain_id)


def _abi_block_height() -> int:
    return int(_CURRENT.block_height)


def _abi_value() -> int:
    return 0


def _abi_call_contract(
    contract_address: bytes,
    fn_name: str | bytes,
    args: list[Any],
    value: int | None = None,
    read_only: bool = False,
):
    _ = value
    _ = read_only

    caller_rt = _require_runtime()
    addr = bytes(contract_address)
    target = _CURRENT.contracts.get(addr)
    if target is None:
        raise Revert(b"missing_contract")

    fn = fn_name.decode("utf-8") if isinstance(fn_name, (bytes, bytearray)) else str(fn_name)
    return target.call(fn, *list(args), caller=caller_rt.address)


abi_mod.require = _abi_require  # type: ignore[attr-defined]
abi_mod.revert = _abi_revert  # type: ignore[attr-defined]
abi_mod.caller = _abi_caller  # type: ignore[attr-defined]
abi_mod.msg_sender = _abi_msg_sender  # type: ignore[attr-defined]
abi_mod.contract_address = _abi_contract_address  # type: ignore[attr-defined]
abi_mod.chain_id = _abi_chain_id  # type: ignore[attr-defined]
abi_mod.block_height = _abi_block_height  # type: ignore[attr-defined]
abi_mod.value = _abi_value  # type: ignore[attr-defined]
abi_mod.call_contract = _abi_call_contract  # type: ignore[attr-defined]


# install synthetic stdlib into import graph before loading contracts
stdlib_mod = types.ModuleType("stdlib")
stdlib_mod.storage = storage_mod  # type: ignore[attr-defined]
stdlib_mod.events = events_mod  # type: ignore[attr-defined]
stdlib_mod.abi = abi_mod  # type: ignore[attr-defined]
stdlib_mod.hash = hash_mod  # type: ignore[attr-defined]
stdlib_mod.pq_verify = pq_verify_mod  # type: ignore[attr-defined]

sys.modules["stdlib"] = stdlib_mod
sys.modules["stdlib.storage"] = storage_mod
sys.modules["stdlib.events"] = events_mod
sys.modules["stdlib.abi"] = abi_mod
sys.modules["stdlib.hash"] = hash_mod
sys.modules["stdlib.pq_verify"] = pq_verify_mod


REPO_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_ROOT = REPO_ROOT / "contracts" / "packages"

TOKEN_ADDR = b"usdan_token"
MINT_ADDR = b"usdan_mint_ctrl"
REDEEM_ADDR = b"usdan_redeem_ctrl"
COMP_ADDR = b"usdan_compliance"
RESERVE_ADDR = b"usdan_reserve"

OWNER = b"owner_admin"
MINT_OP = b"mint_operator"
REDEEM_OP = b"redeem_operator"
COMP_OP = b"compliance_operator"
ALICE = b"alice_wallet"
BOB = b"bob_wallet"

MINT_SIGNER = b"mint_signer_pub"
ALICE_SIGNER = b"alice_signer_pub"
ATTEST_SIGNER = b"attestation_signer_pub"


def _load_contract(path: Path, module_name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _deploy_contract(address: bytes, package: str) -> RuntimeContract:
    module = _load_contract(CONTRACT_ROOT / package / "contract.py", f"{package}_{address.decode('utf-8')}")
    return RuntimeContract(address=address, module=module)


def _sign(pubkey: bytes, message: bytes) -> bytes:
    return hmac.new(pubkey, message, hashlib.sha256).digest()


def _mint(
    contracts: dict[bytes, RuntimeContract],
    recipient: bytes,
    amount: int,
    request_id: bytes,
    nonce: bytes,
    block_height: int,
) -> None:
    _CURRENT.block_height = block_height
    mint = contracts[MINT_ADDR]
    msg = mint.call(
        "mint_message",
        recipient,
        amount,
        b"fiat:settled",
        request_id,
        nonce,
        block_height - 5,
        block_height + 50,
        caller=OWNER,
    )
    sig = _sign(MINT_SIGNER, msg)
    mint.call(
        "execute_mint",
        recipient,
        amount,
        b"fiat:settled",
        request_id,
        nonce,
        block_height - 5,
        block_height + 50,
        MINT_SIGNER,
        sig,
        caller=MINT_OP,
    )


@pytest.fixture()
def deployed() -> dict[bytes, RuntimeContract]:
    _CURRENT.runtime = None
    _CURRENT.contracts = {}
    _CURRENT.block_height = 100
    _CURRENT.chain_id = 1337

    contracts = {
        TOKEN_ADDR: _deploy_contract(TOKEN_ADDR, "usdan_token"),
        MINT_ADDR: _deploy_contract(MINT_ADDR, "usdan_mint_controller"),
        REDEEM_ADDR: _deploy_contract(REDEEM_ADDR, "usdan_redemption_controller"),
        COMP_ADDR: _deploy_contract(COMP_ADDR, "usdan_compliance_controller"),
        RESERVE_ADDR: _deploy_contract(RESERVE_ADDR, "usdan_reserve_attestation"),
    }
    _CURRENT.contracts = contracts

    token = contracts[TOKEN_ADDR]
    mint = contracts[MINT_ADDR]
    redeem = contracts[REDEEM_ADDR]
    comp = contracts[COMP_ADDR]
    reserve = contracts[RESERVE_ADDR]

    token.call("init", OWNER, b"", b"", b"", b"ipfs://usdan/metadata", 1_000_000_000_000, 6, False, caller=OWNER)
    mint.call("init", OWNER, TOKEN_ADDR, 0, 2_000, caller=OWNER)
    redeem.call("init", OWNER, TOKEN_ADDR, True, caller=OWNER)
    comp.call("init", OWNER, TOKEN_ADDR, b"https://sanctions.example/v1", caller=OWNER)
    reserve.call("init", OWNER, caller=OWNER)

    token.call("set_mint_controller", MINT_ADDR, caller=OWNER)
    token.call("set_redemption_controller", REDEEM_ADDR, caller=OWNER)
    token.call("set_compliance_controller", COMP_ADDR, caller=OWNER)

    mint.call("set_operator", MINT_OP, True, caller=OWNER)
    mint.call("set_signer", hashlib.sha3_256(MINT_SIGNER).digest(), True, caller=OWNER)

    redeem.call("set_operator", REDEEM_OP, True, caller=OWNER)
    redeem.call("set_user_signer", ALICE, hashlib.sha3_256(ALICE_SIGNER).digest(), caller=REDEEM_OP)

    comp.call("set_admin", COMP_OP, True, caller=OWNER)

    return contracts


def test_mint_controller_signature_and_replay_protection(deployed: dict[bytes, RuntimeContract]):
    token = deployed[TOKEN_ADDR]
    mint = deployed[MINT_ADDR]

    _mint(deployed, ALICE, 12_500_000, b"req-1", b"nonce-1", block_height=120)

    assert token.call("balance_of", ALICE, caller=OWNER) == 12_500_000
    assert token.call("total_supply", caller=OWNER) == 12_500_000
    assert mint.call("nonce_used", b"nonce-1", caller=OWNER) is True

    msg = mint.call(
        "mint_message",
        ALICE,
        1,
        b"fiat:settled",
        b"req-2",
        b"nonce-2",
        100,
        200,
        caller=OWNER,
    )
    sig = _sign(MINT_SIGNER, msg)

    with pytest.raises(Revert):
        mint.call(
            "execute_mint",
            ALICE,
            1,
            b"fiat:settled",
            b"req-2",
            b"nonce-1",
            100,
            200,
            MINT_SIGNER,
            sig,
            caller=MINT_OP,
        )

    with pytest.raises(Revert):
        mint.call(
            "execute_mint",
            ALICE,
            1,
            b"fiat:settled",
            b"req-2",
            b"nonce-2",
            100,
            200,
            MINT_SIGNER,
            sig,
            caller=BOB,
        )


def test_compliance_pause_freeze_allowlist_and_denylist(deployed: dict[bytes, RuntimeContract]):
    token = deployed[TOKEN_ADDR]
    comp = deployed[COMP_ADDR]

    _mint(deployed, ALICE, 2_000_000, b"req-10", b"nonce-10", block_height=140)

    comp.call("pause_token", True, caller=COMP_OP)
    with pytest.raises(Revert):
        token.call("transfer", BOB, 100, caller=ALICE)

    comp.call("pause_token", False, caller=COMP_OP)
    comp.call("freeze_account", ALICE, True, caller=COMP_OP)
    with pytest.raises(Revert):
        token.call("transfer", BOB, 100, caller=ALICE)

    comp.call("freeze_account", ALICE, False, caller=COMP_OP)

    comp.call("set_allowlist_enforced", True, caller=COMP_OP)
    comp.call("set_allow", ALICE, True, caller=COMP_OP)
    comp.call("set_allow", BOB, True, caller=COMP_OP)
    token.call("transfer", BOB, 100, caller=ALICE)
    assert token.call("balance_of", BOB, caller=OWNER) == 100

    comp.call("set_deny", BOB, True, caller=COMP_OP)
    with pytest.raises(Revert):
        token.call("transfer", BOB, 10, caller=ALICE)


def test_redemption_escrow_cancel_and_resolve(deployed: dict[bytes, RuntimeContract]):
    token = deployed[TOKEN_ADDR]
    redeem = deployed[REDEEM_ADDR]

    _mint(deployed, ALICE, 5_000_000, b"req-20", b"nonce-20", block_height=160)

    token.call("approve", REDEEM_ADDR, 5_000_000, caller=ALICE)

    _CURRENT.block_height = 170
    msg = redeem.call(
        "redemption_message",
        ALICE,
        2_000_000,
        b"bank-hash-1",
        b"alice-r-1",
        220,
        caller=OWNER,
    )
    sig = _sign(ALICE_SIGNER, msg)
    req_id = redeem.call(
        "initiate_redemption",
        2_000_000,
        b"bank-hash-1",
        b"alice-r-1",
        220,
        ALICE_SIGNER,
        sig,
        caller=ALICE,
    )

    assert token.call("balance_of", ALICE, caller=OWNER) == 3_000_000
    assert token.call("balance_of", REDEEM_ADDR, caller=OWNER) == 2_000_000

    redeem.call("cancel_redemption", req_id, b"manual_review_failed", caller=REDEEM_OP)

    info = redeem.call("get_redemption", req_id, caller=OWNER)
    assert info["status"] == b"CANCELLED"
    assert token.call("balance_of", ALICE, caller=OWNER) == 5_000_000
    assert token.call("balance_of", REDEEM_ADDR, caller=OWNER) == 0

    _CURRENT.block_height = 180
    msg2 = redeem.call(
        "redemption_message",
        ALICE,
        1_500_000,
        b"bank-hash-2",
        b"alice-r-2",
        260,
        caller=OWNER,
    )
    sig2 = _sign(ALICE_SIGNER, msg2)
    req_id2 = redeem.call(
        "initiate_redemption",
        1_500_000,
        b"bank-hash-2",
        b"alice-r-2",
        260,
        ALICE_SIGNER,
        sig2,
        caller=ALICE,
    )

    redeem.call("resolve_redemption", req_id2, b"payout-123", caller=REDEEM_OP)

    info2 = redeem.call("get_redemption", req_id2, caller=OWNER)
    assert info2["status"] == b"RESOLVED"
    assert token.call("balance_of", REDEEM_ADDR, caller=OWNER) == 0
    assert token.call("total_supply", caller=OWNER) == 3_500_000


def test_reserve_attestation_snapshot_integrity(deployed: dict[bytes, RuntimeContract]):
    reserve = deployed[RESERVE_ADDR]

    attestation_id = b"2026-04-22T00:00:00Z"
    statement_hash = hashlib.sha3_256(b"attestation:usd:2026-04-22").digest()

    msg = reserve.call(
        "attestation_message",
        attestation_id,
        statement_hash,
        b"ipfs://bafybeigdyr.../attestation-2026-04-22.pdf",
        150_000_000,
        149_500_000,
        190,
        caller=OWNER,
    )
    sig = _sign(ATTEST_SIGNER, msg)

    reserve.call(
        "submit_attestation",
        attestation_id,
        statement_hash,
        b"ipfs://bafybeigdyr.../attestation-2026-04-22.pdf",
        150_000_000,
        149_500_000,
        190,
        ATTEST_SIGNER,
        sig,
        caller=OWNER,
    )

    snapshot = reserve.call("get_attestation", attestation_id, caller=OWNER)
    assert snapshot["exists"] is True
    assert snapshot["reserveAmount"] == 150_000_000
    assert snapshot["liabilityAmount"] == 149_500_000
    assert snapshot["coverageBps"] >= 10_000
    assert reserve.call("latest_attestation_id", caller=OWNER) == attestation_id

    with pytest.raises(Revert):
        reserve.call(
            "submit_attestation",
            attestation_id,
            statement_hash,
            b"ipfs://dup",
            1,
            1,
            191,
            ATTEST_SIGNER,
            sig,
            caller=OWNER,
        )
