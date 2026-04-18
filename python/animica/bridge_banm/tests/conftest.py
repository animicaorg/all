from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from web3 import Web3

from animica.bridge_banm.adapters.animica import (
    AnimicaAdapter,
    AnimicaDepositObservation,
    AnimicaReleaseResult,
    AnimicaTxStatus,
)
from animica.bridge_banm.adapters.evm import EvmAdapter, EvmDepositObservation, EvmSettlementResult, EvmTxStatus
from animica.bridge_banm.config import BridgeBanmConfig
from animica.bridge_banm.db import Base, build_engine, build_sessionmaker
from animica.bridge_banm.engine import BridgeEngine
from animica.bridge_banm.repository import BridgeRepository


@dataclass
class _FakeRelease:
    tx_hash: str
    from_address: str
    to_address: str
    amount: int


class FakeAnimicaAdapter(AnimicaAdapter):
    def __init__(self):
        self.balance_by_address: dict[str, int] = {}
        self.tx_status_by_hash: dict[str, AnimicaTxStatus] = {}
        self.deposit_by_hash: dict[str, AnimicaDepositObservation] = {}
        self.releases: list[_FakeRelease] = []

    def get_balance(self, address: str) -> int:
        return self.balance_by_address.get(address, 0)

    def get_tx_status(self, tx_hash: str) -> AnimicaTxStatus:
        if tx_hash not in self.tx_status_by_hash:
            raise RuntimeError("unknown tx hash")
        return self.tx_status_by_hash[tx_hash]

    def inspect_deposit(self, *, tx_hash: str, expected_to: str) -> AnimicaDepositObservation:
        if tx_hash not in self.deposit_by_hash:
            raise RuntimeError("unknown deposit hash")
        obs = self.deposit_by_hash[tx_hash]
        if obs.to_address != expected_to:
            raise RuntimeError("wrong destination")
        return obs

    def submit_release(
        self,
        *,
        from_address: str,
        to_address: str,
        amount_base_units: int,
        order_id: str,
    ) -> AnimicaReleaseResult:
        tx_hash = "0x" + Web3.keccak(text=f"release:{order_id}:{len(self.releases)}").hex()
        self.releases.append(
            _FakeRelease(
                tx_hash=tx_hash,
                from_address=from_address,
                to_address=to_address,
                amount=amount_base_units,
            )
        )
        self.tx_status_by_hash[tx_hash] = AnimicaTxStatus(
            tx_hash=tx_hash,
            confirmations=0,
            included=False,
            block_height=None,
            raw={"status": "pending"},
        )
        return AnimicaReleaseResult(tx_hash=tx_hash, raw_output="fake")


class FakeEvmAdapter(EvmAdapter):
    def __init__(self):
        self.total_supply_value = 0
        self.tx_status_by_hash: dict[str, EvmTxStatus] = {}
        self.deposit_by_hash: dict[str, EvmDepositObservation] = {}
        self._submitted: list[tuple[str, str]] = []

    def total_supply(self) -> int:
        return self.total_supply_value

    def tx_status(self, tx_hash: str) -> EvmTxStatus:
        if tx_hash not in self.tx_status_by_hash:
            raise RuntimeError("unknown evm tx hash")
        return self.tx_status_by_hash[tx_hash]

    def inspect_router_deposit(self, tx_hash: str) -> EvmDepositObservation:
        if tx_hash not in self.deposit_by_hash:
            raise RuntimeError("unknown evm deposit hash")
        return self.deposit_by_hash[tx_hash]

    def submit_mint(self, *, order_id: str, to_address: str, amount: int, fee_amount: int) -> EvmSettlementResult:
        tx_hash = "0x" + Web3.keccak(text=f"mint:{order_id}").hex()
        self._submitted.append(("mint", order_id))
        self.tx_status_by_hash[tx_hash] = EvmTxStatus(
            tx_hash=tx_hash,
            confirmations=0,
            included=False,
            block_number=None,
            success=None,
            raw=None,
        )
        self.total_supply_value += amount
        return EvmSettlementResult(tx_hash=tx_hash, nonce=len(self._submitted))

    def submit_burn(self, *, order_id: str) -> EvmSettlementResult:
        tx_hash = "0x" + Web3.keccak(text=f"burn:{order_id}").hex()
        self._submitted.append(("burn", order_id))
        self.tx_status_by_hash[tx_hash] = EvmTxStatus(
            tx_hash=tx_hash,
            confirmations=0,
            included=False,
            block_number=None,
            success=None,
            raw=None,
        )
        return EvmSettlementResult(tx_hash=tx_hash, nonce=len(self._submitted))

    @staticmethod
    def order_id_to_bytes32(order_id: str) -> bytes:
        return Web3.keccak(text=order_id)


@pytest.fixture
def bridge_config(tmp_path: Path) -> BridgeBanmConfig:
    return BridgeBanmConfig(
        database_url=f"sqlite:///{tmp_path / 'banm_test.db'}",
        api_host="127.0.0.1",
        api_port=8660,
        bridge_public_base_url="http://localhost:5177",
        bridge_admin_token_secret="test-secret",
        animica_rpc_url="http://127.0.0.1:8545/rpc",
        animica_bridge_custody_address="anim1custodyaddress0000000000000000000",
        animica_bridge_custody_key_ref="wallet:bridge",
        animica_confirmations_required=3,
        animica_release_confirm_policy="confirmed",
        evm_rpc_url="http://127.0.0.1:8545",
        evm_chain_id=97,
        evm_banm_token_address="0x0000000000000000000000000000000000000001",
        evm_bridge_controller_address="0x0000000000000000000000000000000000000002",
        evm_bridge_vault_address="0x0000000000000000000000000000000000000003",
        evm_bridge_deposit_router_address="0x0000000000000000000000000000000000000004",
        evm_bridge_operator_key_ref="env:EVM_OPERATOR_PRIVATE_KEY",
        evm_operator_private_key="0x" + "1" * 64,
        evm_confirmations_required=2,
        order_expiry_minutes=30,
        enable_claim_code_confirmation=True,
        require_evm_signatures=True,
        enable_animica_signatures_if_available=False,
        bridge_paused=False,
        bridge_paused_forward=False,
        bridge_paused_reverse=False,
        max_order_amount_anm=10_000_000_000_000,
        max_order_amount_banm_wei=10**27,
        min_order_amount_anm=1,
        min_order_amount_banm_wei=1,
        daily_mint_cap_banm_wei=10**28,
        daily_release_cap_anm=10_000_000_000_000,
        bridge_fee_bps_forward=25,
        bridge_fee_bps_reverse=25,
        worker_enabled=False,
        worker_poll_interval_seconds=0.5,
    )


@pytest.fixture
def bridge_engine(bridge_config: BridgeBanmConfig):
    sql_engine = build_engine(bridge_config.database_url)
    Base.metadata.create_all(bind=sql_engine)
    repository = BridgeRepository(build_sessionmaker(bridge_config.database_url))
    animica = FakeAnimicaAdapter()
    evm = FakeEvmAdapter()
    engine = BridgeEngine(config=bridge_config, repository=repository, animica=animica, evm=evm)
    return engine, repository, animica, evm

