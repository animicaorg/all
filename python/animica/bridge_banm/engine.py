from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from .addressing import (
    anm_base_to_banm_wei,
    apply_fee,
    banm_wei_to_anm_base,
    clamp_order_amount,
    to_anm_base_units_from_human,
    to_banm_wei_from_human,
    validate_animica_address,
    validate_evm_address,
)
from .adapters import AnimicaAdapter, EvmAdapter
from .config import BridgeBanmConfig
from .enums import BridgeDirection, BridgeStatus
from .models import BridgeOrder
from .repository import BridgeRepository
from .schemas import CreateOrderRequest, CreateOrderResponse, OrderResponse, OrderStatusResponse, SolvencyResponse
from .signatures import build_signature_challenge, verify_order_signature


def _hash_claim_code(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _order_id() -> str:
    return f"banm_{datetime.now(timezone.utc).strftime('%Y%m%d')}_{uuid.uuid4().hex[:12]}"


def _to_order_response(order: BridgeOrder) -> OrderResponse:
    return OrderResponse(
        order_id=order.order_id,
        direction=BridgeDirection(order.direction),
        source_chain=order.source_chain,
        destination_chain=order.destination_chain,
        source_address=order.source_address,
        destination_address=order.destination_address,
        signed_evm_address=order.signed_evm_address,
        amount_in=int(order.amount_in),
        amount_out_expected=int(order.amount_out_expected),
        fee_amount=int(order.fee_amount),
        asset_in=order.asset_in,
        asset_out=order.asset_out,
        deposit_instruction_type=order.deposit_instruction_type,
        deposit_address=order.deposit_address,
        deposit_reference=order.deposit_reference,
        deposit_contract_address=order.deposit_contract_address,
        deposit_function=order.deposit_function,
        deposit_tx_hash=order.deposit_tx_hash,
        status=BridgeStatus(order.status),
        confirmation_count_required=order.confirmation_count_required,
        confirmation_count_current=order.confirmation_count_current,
        settlement_tx_hash=order.settlement_tx_hash,
        release_tx_hash=order.release_tx_hash,
        created_at=order.created_at,
        expires_at=order.expires_at,
        claim_code_required=bool(order.claim_code_hash),
        claim_code_confirmed=order.claim_code_confirmed_at is not None,
        claim_code_confirmed_at=order.claim_code_confirmed_at,
        admin_notes=order.admin_notes,
        manual_review_required=order.manual_review_required,
        manual_review_reason=order.manual_review_reason,
    )


class BridgeEngine:
    def __init__(
        self,
        *,
        config: BridgeBanmConfig,
        repository: BridgeRepository,
        animica: AnimicaAdapter,
        evm: EvmAdapter,
    ):
        self.cfg = config
        self.repo = repository
        self.animica = animica
        self.evm = evm

    def get_pause_flags(self) -> dict[str, bool]:
        persisted = self.repo.get_pause_flags()
        return {
            "bridge_paused": self.cfg.bridge_paused or persisted["bridge_paused"],
            "bridge_paused_forward": self.cfg.bridge_paused_forward or persisted["bridge_paused_forward"],
            "bridge_paused_reverse": self.cfg.bridge_paused_reverse or persisted["bridge_paused_reverse"],
        }

    def create_order(
        self,
        request: CreateOrderRequest,
        *,
        idempotency_key: str | None = None,
    ) -> CreateOrderResponse:
        flags = self.get_pause_flags()
        if flags["bridge_paused"]:
            raise ValueError("bridge is paused")
        if request.direction == BridgeDirection.ANM_TO_BANM and flags["bridge_paused_forward"]:
            raise ValueError("forward bridge is paused")
        if request.direction == BridgeDirection.BANM_TO_ANM and flags["bridge_paused_reverse"]:
            raise ValueError("reverse bridge is paused")

        evm_addr = validate_evm_address(request.connected_evm_address)
        order_id = _order_id()
        warnings = [
            "Custodial bridge: operator custody controls settlement.",
            "MetaMask signature only proves control of the connected EVM address.",
            "Unless Animica signing is enabled, common ownership across chains is not cryptographically proven.",
            "Destination and amount are immutable after order creation.",
        ]

        if request.direction == BridgeDirection.ANM_TO_BANM:
            source_address = validate_animica_address(request.source_address or "")
            destination_address = evm_addr
            amount_in = to_anm_base_units_from_human(request.amount)
            clamp_order_amount(
                amount_in,
                self.cfg.min_order_amount_anm,
                self.cfg.max_order_amount_anm,
                field_name="ANM amount",
            )
            gross_out = anm_base_to_banm_wei(amount_in)
            amount_out_expected, fee_amount = apply_fee(gross_out, self.cfg.bridge_fee_bps_forward)
            order = self.repo.create_order(
                order_id=order_id,
                direction=request.direction,
                source_chain=request.source_chain or "ANIMICA",
                destination_chain=request.destination_chain or "BNB",
                source_address=source_address,
                destination_address=destination_address,
                amount_in=amount_in,
                amount_out_expected=amount_out_expected,
                fee_amount=fee_amount,
                asset_in="ANM",
                asset_out="BANM",
                deposit_instruction_type="ANIMICA_CUSTODY_ADDRESS_EXACT_AMOUNT_REFERENCE",
                deposit_address=self.cfg.animica_bridge_custody_address,
                deposit_reference=order_id,
                deposit_contract_address=None,
                deposit_function=None,
                chain_id=request.chain_id or self.cfg.evm_chain_id,
                expires_in_minutes=self.cfg.order_expiry_minutes,
                claim_code_hash=None,
                metadata_json={"connected_evm_address": evm_addr},
                idempotency_key=idempotency_key,
            )
            ui = {
                "order_id": order_id,
                "exact_anm_amount": amount_in,
                "deposit_address": self.cfg.animica_bridge_custody_address,
                "deposit_reference": order_id,
                "destination_evm_address": destination_address,
                "fee_bps": self.cfg.bridge_fee_bps_forward,
                "net_output_wei": amount_out_expected,
            }
        else:
            source_address = evm_addr
            destination_address = validate_animica_address(request.destination_address or "")
            amount_in = to_banm_wei_from_human(request.amount)
            clamp_order_amount(
                amount_in,
                self.cfg.min_order_amount_banm_wei,
                self.cfg.max_order_amount_banm_wei,
                field_name="BANM amount",
            )
            gross_out = banm_wei_to_anm_base(amount_in)
            amount_out_expected, fee_amount = apply_fee(gross_out, self.cfg.bridge_fee_bps_reverse)
            claim_code = request.claim_code or (secrets.token_hex(3) if self.cfg.enable_claim_code_confirmation else "")
            claim_hash = _hash_claim_code(claim_code) if claim_code else None
            order = self.repo.create_order(
                order_id=order_id,
                direction=request.direction,
                source_chain=request.source_chain or "BNB",
                destination_chain=request.destination_chain or "ANIMICA",
                source_address=source_address,
                destination_address=destination_address,
                amount_in=amount_in,
                amount_out_expected=amount_out_expected,
                fee_amount=fee_amount,
                asset_in="BANM",
                asset_out="ANM",
                deposit_instruction_type="EVM_ROUTER_DEPOSIT_ORDER_ID",
                deposit_address=None,
                deposit_reference=order_id,
                deposit_contract_address=self.cfg.evm_bridge_deposit_router_address,
                deposit_function="deposit(bytes32 orderId,uint256 amount)",
                chain_id=request.chain_id or self.cfg.evm_chain_id,
                expires_in_minutes=self.cfg.order_expiry_minutes,
                claim_code_hash=claim_hash,
                metadata_json={"connected_evm_address": evm_addr},
                idempotency_key=idempotency_key,
            )
            ui = {
                "order_id": order_id,
                "exact_banm_amount_wei": amount_in,
                "deposit_router": self.cfg.evm_bridge_deposit_router_address,
                "banm_token": self.cfg.evm_banm_token_address,
                "deposit_method": "deposit(bytes32 orderId,uint256 amount)",
                "source_evm_address": source_address,
                "destination_animica_address": destination_address,
                "fee_bps": self.cfg.bridge_fee_bps_reverse,
                "net_output_anm": amount_out_expected,
                "claim_code": claim_code if claim_code else None,
            }

        challenge = build_signature_challenge(
            order_id=order.order_id,
            direction=BridgeDirection(order.direction),
            source_chain=order.source_chain,
            destination_chain=order.destination_chain,
            source_address=order.source_address,
            destination_address=order.destination_address,
            exact_amount=int(order.amount_in),
            chain_id=order.chain_id,
            verifying_contract=self.cfg.evm_bridge_controller_address,
            expires_at=order.expires_at,
        )
        self.repo.set_signature_challenge(order_id=order.order_id, nonce=challenge.nonce, payload=challenge.typed_data)
        response_order = self.repo.get_order_or_raise(order.order_id)
        return CreateOrderResponse(
            order=_to_order_response(response_order),
            challenge={
                "id": challenge.challenge_id,
                "typed_data": challenge.typed_data,
                "fallback_message": challenge.text_fallback,
                "challenge_hash": challenge.challenge_hash,
            },
            warnings=warnings,
            ui=ui,
        )

    def verify_signature(
        self,
        *,
        order_id: str,
        signature: str,
        signature_type: str,
    ) -> OrderResponse:
        order = self.repo.get_order_or_raise(order_id)
        if BridgeStatus(order.status) not in {BridgeStatus.CREATED, BridgeStatus.AWAITING_DEPOSIT}:
            raise ValueError(f"order status does not allow signature verification: {order.status}")
        if not order.signature_payload:
            raise ValueError("signature challenge missing")

        expected_signer = order.destination_address if order.direction == BridgeDirection.ANM_TO_BANM.value else order.source_address
        recovered = verify_order_signature(
            signature=signature,
            expected_signer=expected_signer,
            typed_data=order.signature_payload,
            fallback_message=f"BANM bridge order {order.order_id}",
            signature_type=signature_type,
        )
        challenge_hash = "sha256:" + hashlib.sha256(str(order.signature_payload).encode("utf-8")).hexdigest()
        self.repo.add_signature(
            order_id=order_id,
            signer_address=recovered,
            signature=signature,
            signature_type=signature_type,
            challenge_hash=challenge_hash,
            verified=True,
        )
        if self.cfg.require_evm_signatures:
            order = self.repo.mark_signature_verified(order_id, recovered, signature_type)
        else:
            order = self.repo.get_order_or_raise(order_id)
        return _to_order_response(order)

    def confirm_claim_code(self, *, order_id: str, claim_code: str) -> OrderResponse:
        order = self.repo.get_order_or_raise(order_id)
        if not order.claim_code_hash:
            raise ValueError("claim code confirmation is not enabled for this order")
        if _hash_claim_code(claim_code) != order.claim_code_hash:
            raise ValueError("invalid claim code")
        self.repo.confirm_claim_code(order_id=order_id)
        return _to_order_response(self.repo.get_order_or_raise(order_id))

    def attach_animica_deposit_tx(self, *, order_id: str, tx_hash: str) -> OrderResponse:
        order = self.repo.get_order_or_raise(order_id)
        if BridgeDirection(order.direction) != BridgeDirection.ANM_TO_BANM:
            raise ValueError("order direction does not accept Animica deposit tx")
        if BridgeStatus(order.status) not in {BridgeStatus.AWAITING_DEPOSIT, BridgeStatus.CREATED, BridgeStatus.DEPOSIT_SEEN, BridgeStatus.CONFIRMING}:
            raise ValueError(f"order status does not allow deposit attachment: {order.status}")

        obs = self.animica.inspect_deposit(tx_hash=tx_hash, expected_to=order.deposit_address or self.cfg.animica_bridge_custody_address)
        if obs.to_address != (order.deposit_address or self.cfg.animica_bridge_custody_address):
            self.repo.mark_manual_review(order_id, "animica_deposit_wrong_destination")
            raise ValueError("deposit destination mismatch")
        if obs.from_address != order.source_address:
            self.repo.mark_manual_review(order_id, "animica_deposit_wrong_sender")
            raise ValueError("deposit source mismatch")
        if int(obs.amount) != int(order.amount_in):
            self.repo.mark_manual_review(order_id, "animica_deposit_wrong_amount")
            raise ValueError("deposit amount mismatch")

        self.repo.attach_animica_deposit(
            order_id=order_id,
            tx_hash=obs.tx_hash,
            from_address=obs.from_address,
            to_address=obs.to_address,
            amount=obs.amount,
            confirmations=obs.confirmations,
            block_height=obs.block_height,
            raw_payload=obs.raw,
        )
        self.repo.set_deposit_tx_hash(order_id=order_id, tx_hash=obs.tx_hash)
        self._advance_confirmation_states(order_id=order_id, current_confirmations=obs.confirmations, required=self.cfg.animica_confirmations_required)
        return _to_order_response(self.repo.get_order_or_raise(order_id))

    def attach_evm_deposit_tx(self, *, order_id: str, tx_hash: str) -> OrderResponse:
        order = self.repo.get_order_or_raise(order_id)
        if BridgeDirection(order.direction) != BridgeDirection.BANM_TO_ANM:
            raise ValueError("order direction does not accept EVM deposit tx")
        if BridgeStatus(order.status) not in {BridgeStatus.AWAITING_DEPOSIT, BridgeStatus.CREATED, BridgeStatus.DEPOSIT_SEEN, BridgeStatus.CONFIRMING}:
            raise ValueError(f"order status does not allow deposit attachment: {order.status}")

        obs = self.evm.inspect_router_deposit(tx_hash)
        expected_order_key = "0x" + self.evm.order_id_to_bytes32(order.order_id).hex()
        if obs.order_id_hex.lower() != expected_order_key.lower():
            self.repo.mark_manual_review(order_id, "evm_deposit_wrong_order_id")
            raise ValueError("deposit order id mismatch")
        if obs.sender.lower() != order.source_address.lower():
            self.repo.mark_manual_review(order_id, "evm_deposit_wrong_sender")
            raise ValueError("deposit sender mismatch")
        if int(obs.amount) != int(order.amount_in):
            self.repo.mark_manual_review(order_id, "evm_deposit_wrong_amount")
            raise ValueError("deposit amount mismatch")

        self.repo.attach_evm_deposit(
            order_id=order_id,
            tx_hash=obs.tx_hash,
            sender=obs.sender,
            token_address=obs.token_address,
            amount=obs.amount,
            confirmations=obs.confirmations,
            block_number=obs.block_number,
            log_index=obs.log_index,
            raw_payload=obs.raw,
        )
        self.repo.set_deposit_tx_hash(order_id=order_id, tx_hash=obs.tx_hash)
        self._advance_confirmation_states(order_id=order_id, current_confirmations=obs.confirmations, required=self.cfg.evm_confirmations_required)
        return _to_order_response(self.repo.get_order_or_raise(order_id))

    def get_order_status(self, order_id: str) -> OrderStatusResponse:
        order, events = self.repo.get_order_with_events(order_id)
        return OrderStatusResponse(
            order=_to_order_response(order),
            events=[
                {
                    "id": event.id,
                    "from_status": event.from_status,
                    "to_status": event.to_status,
                    "reason": event.reason,
                    "actor": event.actor,
                    "payload": event.payload or {},
                    "created_at": event.created_at,
                }
                for event in events
            ],
        )

    def set_pause_flag(self, flag_name: str, paused: bool, *, actor: str) -> None:
        self.repo.set_pause_flag(flag_name, paused, actor=actor)

    def compute_solvency(self) -> SolvencyResponse:
        reserve_anm = self.animica.get_balance(self.cfg.animica_bridge_custody_address)
        total_supply = self.evm.total_supply()
        pending = self.repo.compute_pending_liabilities()
        liabilities = int(total_supply) + int(pending["pending_forward_mints_wei"]) - int(
            pending["pending_reverse_releases_anm"] * (10**9)
        )
        available = int(reserve_anm) - int(pending["pending_reverse_releases_anm"])
        if available < 0:
            available = 0
        return SolvencyResponse(
            reserve_anm_confirmed=int(reserve_anm),
            banm_total_supply_wei=int(total_supply),
            pending_forward_mints_wei=int(pending["pending_forward_mints_wei"]),
            pending_reverse_releases_anm=int(pending["pending_reverse_releases_anm"]),
            effective_liabilities_wei=int(liabilities),
            available_redeemable_anm=int(available),
            generated_at=datetime.now(timezone.utc),
        )

    def expire_open_orders(self) -> int:
        count = 0
        for order in self.repo.expired_orders():
            self.repo.transition_order(
                order_id=order.order_id,
                to_status=BridgeStatus.EXPIRED,
                reason="order_expired",
            )
            count += 1
        return count

    def poll_order_progress(self, order: BridgeOrder) -> None:
        status = BridgeStatus(order.status)
        if status in {
            BridgeStatus.COMPLETED,
            BridgeStatus.REJECTED,
            BridgeStatus.EXPIRED,
            BridgeStatus.FAILED,
            BridgeStatus.CANCELLED,
            BridgeStatus.MANUAL_REVIEW,
        }:
            return

        if order.deposit_tx_hash and status in {
            BridgeStatus.AWAITING_DEPOSIT,
            BridgeStatus.DEPOSIT_SEEN,
            BridgeStatus.CONFIRMING,
            BridgeStatus.CONFIRMED,
            BridgeStatus.READY_TO_SETTLE,
        }:
            if BridgeDirection(order.direction) == BridgeDirection.ANM_TO_BANM:
                obs = self.animica.inspect_deposit(
                    tx_hash=order.deposit_tx_hash,
                    expected_to=order.deposit_address or self.cfg.animica_bridge_custody_address,
                )
                self._advance_confirmation_states(
                    order_id=order.order_id,
                    current_confirmations=obs.confirmations,
                    required=self.cfg.animica_confirmations_required,
                )
            else:
                obs = self.evm.inspect_router_deposit(order.deposit_tx_hash)
                self._advance_confirmation_states(
                    order_id=order.order_id,
                    current_confirmations=obs.confirmations,
                    required=self.cfg.evm_confirmations_required,
                )

        refreshed = self.repo.get_order_or_raise(order.order_id)
        refreshed_status = BridgeStatus(refreshed.status)
        if refreshed_status == BridgeStatus.READY_TO_SETTLE:
            self._submit_settlement(refreshed)
            refreshed = self.repo.get_order_or_raise(order.order_id)
            refreshed_status = BridgeStatus(refreshed.status)

        if refreshed_status == BridgeStatus.SETTLEMENT_SUBMITTED:
            self._poll_settlement_confirmation(refreshed)

        if refreshed_status == BridgeStatus.SETTLEMENT_CONFIRMED and BridgeDirection(refreshed.direction) == BridgeDirection.BANM_TO_ANM:
            # Reverse bridge waits for Animica release confirmation after release tx exists.
            self._poll_release_confirmation(refreshed)

    def _advance_confirmation_states(self, *, order_id: str, current_confirmations: int, required: int) -> None:
        order = self.repo.get_order_or_raise(order_id)
        status = BridgeStatus(order.status)
        self.repo.update_order_confirmation_count(order_id=order_id, required=required, current=current_confirmations)

        if status == BridgeStatus.CREATED:
            # no signature yet, keep CREATED
            return

        if status == BridgeStatus.AWAITING_DEPOSIT:
            self.repo.transition_order(
                order_id=order_id,
                to_status=BridgeStatus.DEPOSIT_SEEN,
                reason="deposit_seen",
            )
            status = BridgeStatus.DEPOSIT_SEEN

        if current_confirmations < required:
            if status in {BridgeStatus.DEPOSIT_SEEN, BridgeStatus.AWAITING_DEPOSIT}:
                self.repo.transition_order(
                    order_id=order_id,
                    to_status=BridgeStatus.CONFIRMING,
                    reason="deposit_confirming",
                )
            return

        if status in {BridgeStatus.DEPOSIT_SEEN, BridgeStatus.CONFIRMING, BridgeStatus.AWAITING_DEPOSIT}:
            self.repo.transition_order(
                order_id=order_id,
                to_status=BridgeStatus.CONFIRMED,
                reason="deposit_confirmed",
            )

        refreshed = self.repo.get_order_or_raise(order_id)
        if BridgeDirection(refreshed.direction) == BridgeDirection.BANM_TO_ANM and refreshed.claim_code_hash and not refreshed.claim_code_confirmed_at:
            return

        if BridgeStatus(refreshed.status) == BridgeStatus.CONFIRMED:
            self.repo.transition_order(
                order_id=order_id,
                to_status=BridgeStatus.READY_TO_SETTLE,
                reason="ready_to_settle",
            )

    def _submit_settlement(self, order: BridgeOrder) -> None:
        if BridgeDirection(order.direction) == BridgeDirection.ANM_TO_BANM:
            submitted = self.evm.submit_mint(
                order_id=order.order_id,
                to_address=order.destination_address,
                amount=int(order.amount_out_expected),
                fee_amount=int(order.fee_amount),
            )
            self.repo.upsert_mint(
                order_id=order.order_id,
                to_address=order.destination_address,
                amount=int(order.amount_out_expected),
                fee_amount=int(order.fee_amount),
                tx_hash=submitted.tx_hash,
                tx_status="submitted",
                confirmed=False,
            )
            self.repo.set_settlement_hashes(order_id=order.order_id, settlement_tx_hash=submitted.tx_hash)
            self.repo.transition_order(
                order_id=order.order_id,
                to_status=BridgeStatus.SETTLEMENT_SUBMITTED,
                reason="mint_submitted",
                payload={"tx_hash": submitted.tx_hash},
            )
            return

        # BANM -> ANM: burn on EVM first
        submitted = self.evm.submit_burn(order_id=order.order_id)
        self.repo.upsert_burn(
            order_id=order.order_id,
            from_address=order.source_address,
            amount=int(order.amount_in),
            tx_hash=submitted.tx_hash,
            tx_status="submitted",
            confirmed=False,
        )
        self.repo.set_settlement_hashes(order_id=order.order_id, settlement_tx_hash=submitted.tx_hash)
        self.repo.transition_order(
            order_id=order.order_id,
            to_status=BridgeStatus.SETTLEMENT_SUBMITTED,
            reason="burn_submitted",
            payload={"tx_hash": submitted.tx_hash},
        )

    def _poll_settlement_confirmation(self, order: BridgeOrder) -> None:
        if not order.settlement_tx_hash:
            self.repo.mark_manual_review(order.order_id, "missing_settlement_tx_hash")
            return
        tx_status = self.evm.tx_status(order.settlement_tx_hash)
        if tx_status.success is False:
            self.repo.transition_order(order_id=order.order_id, to_status=BridgeStatus.FAILED, reason="evm_settlement_failed")
            return
        if tx_status.confirmations < self.cfg.evm_confirmations_required:
            return

        if BridgeDirection(order.direction) == BridgeDirection.ANM_TO_BANM:
            self.repo.upsert_mint(
                order_id=order.order_id,
                to_address=order.destination_address,
                amount=int(order.amount_out_expected),
                fee_amount=int(order.fee_amount),
                tx_hash=order.settlement_tx_hash,
                tx_status="confirmed",
                confirmed=True,
            )
            self.repo.transition_order(
                order_id=order.order_id,
                to_status=BridgeStatus.SETTLEMENT_CONFIRMED,
                reason="mint_confirmed",
            )
            self.repo.transition_order(
                order_id=order.order_id,
                to_status=BridgeStatus.COMPLETED,
                reason="forward_complete",
            )
            return

        # reverse path burn confirmed; submit ANM release exactly once
        self.repo.upsert_burn(
            order_id=order.order_id,
            from_address=order.source_address,
            amount=int(order.amount_in),
            tx_hash=order.settlement_tx_hash,
            tx_status="confirmed",
            confirmed=True,
        )
        if not order.release_tx_hash:
            released = self.animica.submit_release(
                from_address=self.cfg.animica_bridge_custody_address,
                to_address=order.destination_address,
                amount_base_units=int(order.amount_out_expected),
                order_id=order.order_id,
            )
            self.repo.upsert_release(
                order_id=order.order_id,
                to_address=order.destination_address,
                amount=int(order.amount_out_expected),
                tx_hash=released.tx_hash,
                tx_status="submitted",
                confirmed=False,
            )
            self.repo.set_settlement_hashes(order_id=order.order_id, release_tx_hash=released.tx_hash)
        self.repo.transition_order(
            order_id=order.order_id,
            to_status=BridgeStatus.SETTLEMENT_CONFIRMED,
            reason="burn_confirmed_release_submitted",
        )

    def _poll_release_confirmation(self, order: BridgeOrder) -> None:
        refreshed = self.repo.get_order_or_raise(order.order_id)
        if not refreshed.release_tx_hash:
            return
        tx = self.animica.get_tx_status(refreshed.release_tx_hash)
        if tx.confirmations < self.cfg.animica_confirmations_required:
            return
        self.repo.upsert_release(
            order_id=order.order_id,
            to_address=refreshed.destination_address,
            amount=int(refreshed.amount_out_expected),
            tx_hash=refreshed.release_tx_hash,
            tx_status="confirmed",
            confirmed=True,
        )
        self.repo.transition_order(
            order_id=order.order_id,
            to_status=BridgeStatus.COMPLETED,
            reason="reverse_complete",
        )
