from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from animica.bridge_banm.adapters.animica import AnimicaDepositObservation, AnimicaTxStatus
from animica.bridge_banm.adapters.evm import EvmDepositObservation, EvmTxStatus
from animica.bridge_banm.enums import BridgeDirection, BridgeStatus
from animica.bridge_banm.schemas import CreateOrderRequest


def _sign_typed_data(challenge: dict, private_key: str) -> str:
    signable = encode_typed_data(full_message=challenge["typed_data"])
    signed = Account.sign_message(signable, private_key=private_key)
    return signed.signature.hex()


def test_forward_order_signature_and_completion(bridge_engine):
    engine, repo, animica, evm = bridge_engine
    acct = Account.create()

    created = engine.create_order(
        CreateOrderRequest(
            direction=BridgeDirection.ANM_TO_BANM,
            connected_evm_address=acct.address,
            amount="12.5",
            source_address="anim1sourceaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        )
    )
    assert created.order.status == BridgeStatus.CREATED
    sig = _sign_typed_data(created.challenge, acct.key.hex())
    verified = engine.verify_signature(order_id=created.order.order_id, signature=sig, signature_type="EIP712")
    assert verified.status == BridgeStatus.AWAITING_DEPOSIT

    animica.deposit_by_hash["0x" + "a" * 64] = AnimicaDepositObservation(
        tx_hash="0x" + "a" * 64,
        from_address=created.order.source_address,
        to_address=created.order.deposit_address or engine.cfg.animica_bridge_custody_address,
        amount=created.order.amount_in,
        confirmations=3,
        block_height=123,
        raw={},
    )
    attached = engine.attach_animica_deposit_tx(order_id=created.order.order_id, tx_hash="0x" + "a" * 64)
    assert attached.status == BridgeStatus.READY_TO_SETTLE

    order = repo.get_order_or_raise(created.order.order_id)
    engine.poll_order_progress(order)
    order = repo.get_order_or_raise(created.order.order_id)
    assert BridgeStatus(order.status) == BridgeStatus.SETTLEMENT_SUBMITTED
    assert order.settlement_tx_hash

    evm.tx_status_by_hash[order.settlement_tx_hash] = EvmTxStatus(
        tx_hash=order.settlement_tx_hash,
        confirmations=2,
        included=True,
        block_number=100,
        success=True,
        raw={},
    )
    engine.poll_order_progress(order)
    final = repo.get_order_or_raise(created.order.order_id)
    assert BridgeStatus(final.status) == BridgeStatus.COMPLETED


def test_forward_wrong_amount_routes_to_manual_review(bridge_engine):
    engine, repo, animica, _ = bridge_engine
    acct = Account.create()

    created = engine.create_order(
        CreateOrderRequest(
            direction=BridgeDirection.ANM_TO_BANM,
            connected_evm_address=acct.address,
            amount="1.0",
            source_address="anim1sourcebbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )
    )
    sig = _sign_typed_data(created.challenge, acct.key.hex())
    engine.verify_signature(order_id=created.order.order_id, signature=sig, signature_type="EIP712")

    animica.deposit_by_hash["0x" + "b" * 64] = AnimicaDepositObservation(
        tx_hash="0x" + "b" * 64,
        from_address=created.order.source_address,
        to_address=created.order.deposit_address or engine.cfg.animica_bridge_custody_address,
        amount=created.order.amount_in + 1,
        confirmations=5,
        block_height=20,
        raw={},
    )
    with pytest.raises(ValueError, match="amount mismatch"):
        engine.attach_animica_deposit_tx(order_id=created.order.order_id, tx_hash="0x" + "b" * 64)

    order = repo.get_order_or_raise(created.order.order_id)
    assert BridgeStatus(order.status) == BridgeStatus.MANUAL_REVIEW
    assert order.manual_review_required is True


def test_reverse_flow_claim_code_burn_release(bridge_engine):
    engine, repo, animica, evm = bridge_engine
    acct = Account.create()

    created = engine.create_order(
        CreateOrderRequest(
            direction=BridgeDirection.BANM_TO_ANM,
            connected_evm_address=acct.address,
            amount="5",
            destination_address="anim1destccccccccccccccccccccccccccccccc",
        )
    )
    sig = _sign_typed_data(created.challenge, acct.key.hex())
    engine.verify_signature(order_id=created.order.order_id, signature=sig, signature_type="EIP712")

    order_key_hex = "0x" + evm.order_id_to_bytes32(created.order.order_id).hex()
    evm.deposit_by_hash["0x" + "c" * 64] = EvmDepositObservation(
        tx_hash="0x" + "c" * 64,
        sender=acct.address,
        order_id_hex=order_key_hex,
        amount=created.order.amount_in,
        token_address=engine.cfg.evm_banm_token_address,
        confirmations=2,
        block_number=111,
        log_index=0,
        raw={},
    )
    attached = engine.attach_evm_deposit_tx(order_id=created.order.order_id, tx_hash="0x" + "c" * 64)
    # claim code enabled; order stays CONFIRMED until code is confirmed
    assert attached.status == BridgeStatus.CONFIRMED
    code = str(created.ui["claim_code"])
    engine.confirm_claim_code(order_id=created.order.order_id, claim_code=code)

    order = repo.get_order_or_raise(created.order.order_id)
    engine.poll_order_progress(order)
    order = repo.get_order_or_raise(created.order.order_id)
    assert BridgeStatus(order.status) == BridgeStatus.SETTLEMENT_SUBMITTED
    assert order.settlement_tx_hash

    evm.tx_status_by_hash[order.settlement_tx_hash] = EvmTxStatus(
        tx_hash=order.settlement_tx_hash,
        confirmations=2,
        included=True,
        block_number=120,
        success=True,
        raw={},
    )
    engine.poll_order_progress(order)
    order = repo.get_order_or_raise(created.order.order_id)
    assert BridgeStatus(order.status) == BridgeStatus.SETTLEMENT_CONFIRMED
    assert order.release_tx_hash

    animica.tx_status_by_hash[order.release_tx_hash] = AnimicaTxStatus(
        tx_hash=order.release_tx_hash,
        confirmations=3,
        included=True,
        block_height=999,
        raw={},
    )
    engine.poll_order_progress(order)
    final = repo.get_order_or_raise(created.order.order_id)
    assert BridgeStatus(final.status) == BridgeStatus.COMPLETED


def test_reverse_banm_amount_uses_animica_decimal_precision(bridge_engine):
    engine, _, _, _ = bridge_engine
    acct = Account.create()

    created = engine.create_order(
        CreateOrderRequest(
            direction=BridgeDirection.BANM_TO_ANM,
            connected_evm_address=acct.address,
            amount="1.0000000019",
            destination_address="anim1destdecimalbanm00000000000000000",
        )
    )
    # 1.0000000019 BANM is rounded down to 9 decimals before converting to wei.
    assert created.order.amount_in == 1_000_000_001_000_000_000
    assert created.order.amount_out_expected == 997_500_001
    assert created.order.fee_amount == 2_500_000


def test_signature_mismatch_rejected(bridge_engine):
    engine, _, _, _ = bridge_engine
    good = Account.create()
    bad = Account.create()

    created = engine.create_order(
        CreateOrderRequest(
            direction=BridgeDirection.ANM_TO_BANM,
            connected_evm_address=good.address,
            amount="1",
            source_address="anim1sourceddddddddddddddddddddddddddddddd",
        )
    )
    sig = _sign_typed_data(created.challenge, bad.key.hex())
    with pytest.raises(ValueError, match="signer mismatch"):
        engine.verify_signature(order_id=created.order.order_id, signature=sig, signature_type="EIP712")


def test_duplicate_deposit_attachment_is_replay_safe(bridge_engine):
    engine, repo, animica, _ = bridge_engine
    acct = Account.create()
    created = engine.create_order(
        CreateOrderRequest(
            direction=BridgeDirection.ANM_TO_BANM,
            connected_evm_address=acct.address,
            amount="2",
            source_address="anim1source111111111111111111111111111111",
        )
    )
    sig = _sign_typed_data(created.challenge, acct.key.hex())
    engine.verify_signature(order_id=created.order.order_id, signature=sig, signature_type="EIP712")

    tx_hash = "0x" + "d" * 64
    animica.deposit_by_hash[tx_hash] = AnimicaDepositObservation(
        tx_hash=tx_hash,
        from_address=created.order.source_address,
        to_address=created.order.deposit_address or engine.cfg.animica_bridge_custody_address,
        amount=created.order.amount_in,
        confirmations=1,
        block_height=44,
        raw={},
    )
    first = engine.attach_animica_deposit_tx(order_id=created.order.order_id, tx_hash=tx_hash)
    second = engine.attach_animica_deposit_tx(order_id=created.order.order_id, tx_hash=tx_hash)
    assert first.order_id == second.order_id
    order = repo.get_order_or_raise(created.order.order_id)
    assert order.deposit_tx_hash == tx_hash


def test_expiry_and_pause(bridge_engine):
    engine, repo, _, _ = bridge_engine
    acct = Account.create()
    created = engine.create_order(
        CreateOrderRequest(
            direction=BridgeDirection.ANM_TO_BANM,
            connected_evm_address=acct.address,
            amount="1",
            source_address="anim1sourceeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        )
    )
    repo.transition_order(
        order_id=created.order.order_id,
        to_status=BridgeStatus.AWAITING_DEPOSIT,
        reason="force-awaiting",
    )
    repo.set_expires_at(
        order_id=created.order.order_id,
        expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )
    expired_count = engine.expire_open_orders()
    assert expired_count >= 1

    engine.set_pause_flag("bridge_paused_forward", True, actor="test-admin")
    with pytest.raises(ValueError, match="forward bridge is paused"):
        engine.create_order(
            CreateOrderRequest(
                direction=BridgeDirection.ANM_TO_BANM,
                connected_evm_address=acct.address,
                amount="1",
                source_address="anim1sourcefffffffffffffffffffffffffffffff",
            )
        )
