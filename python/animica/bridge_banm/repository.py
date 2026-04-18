from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, sessionmaker

from .db import session_scope, utcnow
from .enums import BridgeDirection, BridgeStatus
from .models import (
    AdminUser,
    AnmRelease,
    AuditLog,
    BanmBurn,
    BanmMint,
    BridgeDepositAnimica,
    BridgeDepositEvm,
    BridgeOrder,
    BridgeOrderEvent,
    BridgeSignature,
    IdempotencyKey,
    ReconciliationRun,
    ServiceLock,
)
from .state_machine import assert_transition


def _hash_idempotency_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_timedelta(minutes: int) -> datetime:
    return utcnow() + timedelta(minutes=minutes)


class BridgeRepository:
    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def get_order(self, order_id: str) -> BridgeOrder | None:
        with session_scope(self._session_factory) as session:
            return session.get(BridgeOrder, order_id)

    def get_order_or_raise(self, order_id: str) -> BridgeOrder:
        order = self.get_order(order_id)
        if order is None:
            raise ValueError("order not found")
        return order

    def get_order_with_events(self, order_id: str) -> tuple[BridgeOrder, list[BridgeOrderEvent]]:
        with session_scope(self._session_factory) as session:
            order = session.get(BridgeOrder, order_id)
            if order is None:
                raise ValueError("order not found")
            events = list(
                session.scalars(
                    select(BridgeOrderEvent)
                    .where(BridgeOrderEvent.order_id == order_id)
                    .order_by(BridgeOrderEvent.id.asc())
                )
            )
            return order, events

    def list_orders(
        self,
        *,
        status: BridgeStatus | None = None,
        direction: BridgeDirection | None = None,
        limit: int = 100,
    ) -> list[BridgeOrder]:
        with session_scope(self._session_factory) as session:
            stmt = select(BridgeOrder).order_by(BridgeOrder.created_at.desc()).limit(limit)
            if status is not None:
                stmt = stmt.where(BridgeOrder.status == status.value)
            if direction is not None:
                stmt = stmt.where(BridgeOrder.direction == direction.value)
            return list(session.scalars(stmt))

    def create_order(
        self,
        *,
        order_id: str,
        direction: BridgeDirection,
        source_chain: str,
        destination_chain: str,
        source_address: str,
        destination_address: str,
        amount_in: int,
        amount_out_expected: int,
        fee_amount: int,
        asset_in: str,
        asset_out: str,
        deposit_instruction_type: str,
        deposit_address: str | None,
        deposit_reference: str | None,
        deposit_contract_address: str | None,
        deposit_function: str | None,
        chain_id: int,
        expires_in_minutes: int,
        claim_code_hash: str | None,
        metadata_json: dict[str, Any] | None,
        idempotency_key: str | None,
    ) -> BridgeOrder:
        with session_scope(self._session_factory) as session:
            if idempotency_key:
                payload = self._resolve_idempotency_existing(
                    session=session,
                    scope="create_order",
                    idempotency_key=idempotency_key,
                )
                if payload and payload.get("order_id"):
                    existing = session.get(BridgeOrder, payload["order_id"])
                    if existing is not None:
                        return existing

            order = BridgeOrder(
                order_id=order_id,
                direction=direction.value,
                source_chain=source_chain,
                destination_chain=destination_chain,
                source_address=source_address,
                destination_address=destination_address,
                amount_in=int(amount_in),
                amount_out_expected=int(amount_out_expected),
                fee_amount=int(fee_amount),
                asset_in=asset_in,
                asset_out=asset_out,
                deposit_instruction_type=deposit_instruction_type,
                deposit_address=deposit_address,
                deposit_reference=deposit_reference,
                deposit_contract_address=deposit_contract_address,
                deposit_function=deposit_function,
                status=BridgeStatus.CREATED.value,
                confirmation_count_required=0,
                confirmation_count_current=0,
                chain_id=int(chain_id),
                claim_code_hash=claim_code_hash,
                expires_at=_normalize_timedelta(expires_in_minutes),
                metadata_json=metadata_json or {},
                idempotency_key=idempotency_key,
            )
            session.add(order)
            session.add(
                BridgeOrderEvent(
                    order_id=order_id,
                    from_status=None,
                    to_status=BridgeStatus.CREATED.value,
                    reason="order_created",
                    actor="public_api",
                    payload={"direction": direction.value},
                )
            )
            self._audit(
                session=session,
                actor="public_api",
                action="order.create",
                resource_type="bridge_order",
                resource_id=order_id,
                payload={"direction": direction.value},
            )

            if idempotency_key:
                self._store_idempotency(
                    session=session,
                    scope="create_order",
                    idempotency_key=idempotency_key,
                    order_id=order_id,
                    response_payload={"order_id": order_id},
                )
            session.flush()
            return order

    def set_signature_challenge(
        self,
        *,
        order_id: str,
        nonce: str,
        payload: dict[str, Any],
    ) -> None:
        with session_scope(self._session_factory) as session:
            order = session.get(BridgeOrder, order_id)
            if order is None:
                raise ValueError("order not found")
            order.signature_challenge_nonce = nonce
            order.signature_payload = payload
            self._audit(
                session=session,
                actor="system",
                action="order.challenge.set",
                resource_type="bridge_order",
                resource_id=order_id,
                payload={"nonce": nonce},
            )

    def add_signature(
        self,
        *,
        order_id: str,
        signer_address: str,
        signature: str,
        signature_type: str,
        challenge_hash: str,
        verified: bool,
    ) -> BridgeSignature:
        with session_scope(self._session_factory) as session:
            bridge_signature = BridgeSignature(
                order_id=order_id,
                signer_address=signer_address,
                signature=signature,
                signature_type=signature_type,
                challenge_hash=challenge_hash,
                verified=verified,
                verified_at=utcnow() if verified else None,
            )
            session.add(bridge_signature)
            self._audit(
                session=session,
                actor=signer_address,
                action="order.signature.verify",
                resource_type="bridge_order",
                resource_id=order_id,
                payload={"signature_type": signature_type, "verified": verified},
            )
            session.flush()
            return bridge_signature

    def mark_signature_verified(self, order_id: str, signer_address: str, signature_method: str) -> BridgeOrder:
        with session_scope(self._session_factory) as session:
            order = session.get(BridgeOrder, order_id)
            if order is None:
                raise ValueError("order not found")
            current = BridgeStatus(order.status)
            nxt = BridgeStatus.AWAITING_DEPOSIT
            assert_transition(current, nxt)
            order.status = nxt.value
            order.signed_evm_address = signer_address
            order.signature_verified_at = utcnow()
            order.signature_method = signature_method
            session.add(
                BridgeOrderEvent(
                    order_id=order_id,
                    from_status=current.value,
                    to_status=nxt.value,
                    reason="signature_verified",
                    actor=signer_address,
                )
            )
            self._audit(
                session=session,
                actor=signer_address,
                action="order.signature.accepted",
                resource_type="bridge_order",
                resource_id=order_id,
                payload={"signature_method": signature_method},
            )
            session.flush()
            return order

    def attach_animica_deposit(
        self,
        *,
        order_id: str,
        tx_hash: str,
        from_address: str,
        to_address: str,
        amount: int,
        confirmations: int,
        block_height: int | None,
        raw_payload: dict[str, Any] | None,
    ) -> BridgeDepositAnimica:
        with session_scope(self._session_factory) as session:
            existing = session.scalar(select(BridgeDepositAnimica).where(BridgeDepositAnimica.tx_hash == tx_hash))
            if existing is not None:
                return existing
            deposit = BridgeDepositAnimica(
                order_id=order_id,
                tx_hash=tx_hash,
                from_address=from_address,
                to_address=to_address,
                amount=int(amount),
                confirmations=int(confirmations),
                block_height=block_height,
                raw_payload=raw_payload,
                confirmed_at=utcnow() if confirmations > 0 else None,
            )
            session.add(deposit)
            self._audit(
                session=session,
                actor="system",
                action="deposit.animica.attach",
                resource_type="bridge_order",
                resource_id=order_id,
                payload={"tx_hash": tx_hash, "confirmations": confirmations},
            )
            session.flush()
            return deposit

    def attach_evm_deposit(
        self,
        *,
        order_id: str,
        tx_hash: str,
        sender: str,
        token_address: str,
        amount: int,
        confirmations: int,
        block_number: int | None,
        log_index: int | None,
        raw_payload: dict[str, Any] | None,
    ) -> BridgeDepositEvm:
        with session_scope(self._session_factory) as session:
            existing = session.scalar(select(BridgeDepositEvm).where(BridgeDepositEvm.tx_hash == tx_hash))
            if existing is not None:
                return existing
            deposit = BridgeDepositEvm(
                order_id=order_id,
                tx_hash=tx_hash,
                sender=sender,
                token_address=token_address,
                amount=int(amount),
                confirmations=int(confirmations),
                block_number=block_number,
                log_index=log_index,
                raw_payload=raw_payload,
                confirmed_at=utcnow() if confirmations > 0 else None,
            )
            session.add(deposit)
            self._audit(
                session=session,
                actor="system",
                action="deposit.evm.attach",
                resource_type="bridge_order",
                resource_id=order_id,
                payload={"tx_hash": tx_hash, "confirmations": confirmations},
            )
            session.flush()
            return deposit

    def latest_animica_deposit(self, order_id: str) -> BridgeDepositAnimica | None:
        with session_scope(self._session_factory) as session:
            return session.scalar(
                select(BridgeDepositAnimica)
                .where(BridgeDepositAnimica.order_id == order_id)
                .order_by(BridgeDepositAnimica.id.desc())
                .limit(1)
            )

    def latest_evm_deposit(self, order_id: str) -> BridgeDepositEvm | None:
        with session_scope(self._session_factory) as session:
            return session.scalar(
                select(BridgeDepositEvm).where(BridgeDepositEvm.order_id == order_id).order_by(BridgeDepositEvm.id.desc()).limit(1)
            )

    def update_order_confirmation_count(
        self,
        *,
        order_id: str,
        required: int,
        current: int,
    ) -> None:
        with session_scope(self._session_factory) as session:
            order = session.get(BridgeOrder, order_id)
            if order is None:
                raise ValueError("order not found")
            order.confirmation_count_required = int(required)
            order.confirmation_count_current = int(current)
            order.updated_at = utcnow()

    def transition_order(
        self,
        *,
        order_id: str,
        to_status: BridgeStatus,
        reason: str,
        actor: str = "system",
        payload: dict[str, Any] | None = None,
    ) -> BridgeOrder:
        with session_scope(self._session_factory) as session:
            order = session.get(BridgeOrder, order_id)
            if order is None:
                raise ValueError("order not found")
            current = BridgeStatus(order.status)
            assert_transition(current, to_status)
            order.status = to_status.value
            order.updated_at = utcnow()
            session.add(
                BridgeOrderEvent(
                    order_id=order_id,
                    from_status=current.value,
                    to_status=to_status.value,
                    reason=reason,
                    actor=actor,
                    payload=payload,
                )
            )
            self._audit(
                session=session,
                actor=actor,
                action=f"order.transition.{to_status.value.lower()}",
                resource_type="bridge_order",
                resource_id=order_id,
                payload={"from": current.value, "to": to_status.value, "reason": reason, "payload": payload or {}},
            )
            session.flush()
            return order

    def mark_manual_review(self, order_id: str, reason: str, *, actor: str = "system") -> BridgeOrder:
        with session_scope(self._session_factory) as session:
            order = session.get(BridgeOrder, order_id)
            if order is None:
                raise ValueError("order not found")
            current = BridgeStatus(order.status)
            target = BridgeStatus.MANUAL_REVIEW
            if current != target:
                assert_transition(current, target)
                order.status = target.value
                session.add(
                    BridgeOrderEvent(
                        order_id=order_id,
                        from_status=current.value,
                        to_status=target.value,
                        reason=reason,
                        actor=actor,
                    )
                )
            order.manual_review_required = True
            order.manual_review_reason = reason
            self._audit(
                session=session,
                actor=actor,
                action="order.manual_review",
                resource_type="bridge_order",
                resource_id=order_id,
                payload={"reason": reason},
            )
            session.flush()
            return order

    def set_admin_notes(self, order_id: str, notes: str, *, actor: str) -> None:
        with session_scope(self._session_factory) as session:
            order = session.get(BridgeOrder, order_id)
            if order is None:
                raise ValueError("order not found")
            order.admin_notes = notes
            self._audit(
                session=session,
                actor=actor,
                action="order.notes.update",
                resource_type="bridge_order",
                resource_id=order_id,
                payload={"notes": notes},
            )

    def upsert_mint(
        self,
        *,
        order_id: str,
        to_address: str,
        amount: int,
        fee_amount: int,
        tx_hash: str,
        tx_status: str,
        confirmed: bool,
    ) -> BanmMint:
        with session_scope(self._session_factory) as session:
            mint = session.scalar(select(BanmMint).where(BanmMint.order_id == order_id))
            if mint is None:
                mint = BanmMint(
                    order_id=order_id,
                    to_address=to_address,
                    amount=int(amount),
                    fee_amount=int(fee_amount),
                    tx_hash=tx_hash,
                    tx_status=tx_status,
                    confirmed_at=utcnow() if confirmed else None,
                )
                session.add(mint)
            else:
                mint.tx_hash = tx_hash
                mint.tx_status = tx_status
                if confirmed:
                    mint.confirmed_at = utcnow()
            session.flush()
            return mint

    def upsert_burn(
        self,
        *,
        order_id: str,
        from_address: str,
        amount: int,
        tx_hash: str,
        tx_status: str,
        confirmed: bool,
    ) -> BanmBurn:
        with session_scope(self._session_factory) as session:
            burn = session.scalar(select(BanmBurn).where(BanmBurn.order_id == order_id))
            if burn is None:
                burn = BanmBurn(
                    order_id=order_id,
                    from_address=from_address,
                    amount=int(amount),
                    tx_hash=tx_hash,
                    tx_status=tx_status,
                    confirmed_at=utcnow() if confirmed else None,
                )
                session.add(burn)
            else:
                burn.tx_hash = tx_hash
                burn.tx_status = tx_status
                if confirmed:
                    burn.confirmed_at = utcnow()
            session.flush()
            return burn

    def upsert_release(
        self,
        *,
        order_id: str,
        to_address: str,
        amount: int,
        tx_hash: str,
        tx_status: str,
        confirmed: bool,
    ) -> AnmRelease:
        with session_scope(self._session_factory) as session:
            release = session.scalar(select(AnmRelease).where(AnmRelease.order_id == order_id))
            if release is None:
                release = AnmRelease(
                    order_id=order_id,
                    to_address=to_address,
                    amount=int(amount),
                    tx_hash=tx_hash,
                    tx_status=tx_status,
                    confirmed_at=utcnow() if confirmed else None,
                )
                session.add(release)
            else:
                release.tx_hash = tx_hash
                release.tx_status = tx_status
                if confirmed:
                    release.confirmed_at = utcnow()
            session.flush()
            return release

    def set_settlement_hashes(
        self,
        *,
        order_id: str,
        settlement_tx_hash: str | None = None,
        release_tx_hash: str | None = None,
    ) -> None:
        with session_scope(self._session_factory) as session:
            order = session.get(BridgeOrder, order_id)
            if order is None:
                raise ValueError("order not found")
            if settlement_tx_hash:
                order.settlement_tx_hash = settlement_tx_hash
            if release_tx_hash:
                order.release_tx_hash = release_tx_hash

    def set_deposit_tx_hash(self, *, order_id: str, tx_hash: str) -> None:
        with session_scope(self._session_factory) as session:
            order = session.get(BridgeOrder, order_id)
            if order is None:
                raise ValueError("order not found")
            order.deposit_tx_hash = tx_hash

    def confirm_claim_code(self, *, order_id: str) -> None:
        with session_scope(self._session_factory) as session:
            order = session.get(BridgeOrder, order_id)
            if order is None:
                raise ValueError("order not found")
            order.claim_code_confirmed_at = utcnow()
            self._audit(
                session=session,
                actor="public_api",
                action="order.claim_code.confirmed",
                resource_type="bridge_order",
                resource_id=order_id,
            )

    def set_expires_at(self, *, order_id: str, expires_at: datetime) -> None:
        with session_scope(self._session_factory) as session:
            order = session.get(BridgeOrder, order_id)
            if order is None:
                raise ValueError("order not found")
            order.expires_at = expires_at

    def orders_for_worker(self, statuses: list[BridgeStatus], limit: int = 200) -> list[BridgeOrder]:
        with session_scope(self._session_factory) as session:
            values = [item.value for item in statuses]
            stmt = (
                select(BridgeOrder)
                .where(BridgeOrder.status.in_(values))
                .order_by(BridgeOrder.created_at.asc())
                .limit(limit)
            )
            return list(session.scalars(stmt))

    def expired_orders(self, now: datetime | None = None, limit: int = 200) -> list[BridgeOrder]:
        now = now or utcnow()
        with session_scope(self._session_factory) as session:
            return list(
                session.scalars(
                    select(BridgeOrder)
                    .where(
                        and_(
                            BridgeOrder.expires_at <= now,
                            BridgeOrder.status.in_(
                                [
                                    BridgeStatus.CREATED.value,
                                    BridgeStatus.AWAITING_DEPOSIT.value,
                                    BridgeStatus.DEPOSIT_SEEN.value,
                                    BridgeStatus.CONFIRMING.value,
                                ]
                            ),
                        )
                    )
                    .order_by(BridgeOrder.expires_at.asc())
                    .limit(limit)
                )
            )

    def get_pause_flags(self) -> dict[str, bool]:
        with session_scope(self._session_factory) as session:
            out = {
                "bridge_paused": False,
                "bridge_paused_forward": False,
                "bridge_paused_reverse": False,
            }
            rows = list(
                session.scalars(
                    select(ServiceLock).where(ServiceLock.name.in_(["flag:bridge_paused", "flag:bridge_paused_forward", "flag:bridge_paused_reverse"]))
                )
            )
            for row in rows:
                out[row.name.replace("flag:", "")] = row.owner == "true"
            return out

    def set_pause_flag(self, flag_name: str, paused: bool, *, actor: str) -> None:
        if flag_name not in {"bridge_paused", "bridge_paused_forward", "bridge_paused_reverse"}:
            raise ValueError("unsupported pause flag")
        with session_scope(self._session_factory) as session:
            key = f"flag:{flag_name}"
            row = session.get(ServiceLock, key)
            if row is None:
                row = ServiceLock(
                    name=key,
                    owner="true" if paused else "false",
                    expires_at=datetime(2999, 1, 1, tzinfo=timezone.utc),
                )
                session.add(row)
            else:
                row.owner = "true" if paused else "false"
            self._audit(
                session=session,
                actor=actor,
                action=f"bridge.pause.{flag_name}",
                resource_type="bridge_flag",
                resource_id=flag_name,
                payload={"paused": paused},
            )

    def get_or_create_admin_user(
        self,
        *,
        username: str,
        role: str,
        password_salt: str,
        password_hash: str,
    ) -> AdminUser:
        with session_scope(self._session_factory) as session:
            user = session.scalar(select(AdminUser).where(AdminUser.username == username))
            if user is None:
                user = AdminUser(
                    username=username,
                    role=role,
                    password_salt=password_salt,
                    password_hash=password_hash,
                    is_active=True,
                )
                session.add(user)
                session.flush()
            return user

    def find_admin_user(self, username: str) -> AdminUser | None:
        with session_scope(self._session_factory) as session:
            return session.scalar(select(AdminUser).where(AdminUser.username == username))

    def touch_admin_login(self, username: str) -> None:
        with session_scope(self._session_factory) as session:
            user = session.scalar(select(AdminUser).where(AdminUser.username == username))
            if user is not None:
                user.last_login_at = utcnow()

    def record_reconciliation_run(
        self,
        *,
        run_key: str,
        summary: dict[str, Any],
    ) -> None:
        with session_scope(self._session_factory) as session:
            run = ReconciliationRun(
                run_key=run_key,
                status="completed",
                summary=summary,
                started_at=utcnow(),
                finished_at=utcnow(),
                discrepancy_count=int(summary.get("discrepancy_count", 0)),
                reserve_anm=int(summary.get("reserve_anm", 0)),
                banm_total_supply_wei=int(summary.get("banm_total_supply_wei", 0)),
                pending_forward_wei=int(summary.get("pending_forward_mints_wei", 0)),
                pending_reverse_anm=int(summary.get("pending_reverse_releases_anm", 0)),
            )
            session.add(run)

    def compute_pending_liabilities(self) -> dict[str, int]:
        with session_scope(self._session_factory) as session:
            pending_forward_wei = (
                session.scalar(
                    select(func.coalesce(func.sum(BridgeOrder.amount_out_expected), 0)).where(
                        and_(
                            BridgeOrder.direction == BridgeDirection.ANM_TO_BANM.value,
                            BridgeOrder.status.in_(
                                [
                                    BridgeStatus.CONFIRMED.value,
                                    BridgeStatus.READY_TO_SETTLE.value,
                                    BridgeStatus.SETTLEMENT_SUBMITTED.value,
                                ]
                            ),
                        )
                    )
                )
                or 0
            )
            pending_reverse_anm = (
                session.scalar(
                    select(func.coalesce(func.sum(BridgeOrder.amount_out_expected), 0)).where(
                        and_(
                            BridgeOrder.direction == BridgeDirection.BANM_TO_ANM.value,
                            BridgeOrder.status.in_(
                                [
                                    BridgeStatus.CONFIRMED.value,
                                    BridgeStatus.READY_TO_SETTLE.value,
                                    BridgeStatus.SETTLEMENT_SUBMITTED.value,
                                    BridgeStatus.SETTLEMENT_CONFIRMED.value,
                                ]
                            ),
                        )
                    )
                )
                or 0
            )
            return {
                "pending_forward_mints_wei": int(pending_forward_wei),
                "pending_reverse_releases_anm": int(pending_reverse_anm),
            }

    def _resolve_idempotency_existing(
        self,
        *,
        session: Session,
        scope: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        key_hash = _hash_idempotency_key(idempotency_key)
        row = session.scalar(
            select(IdempotencyKey).where(
                and_(IdempotencyKey.scope == scope, IdempotencyKey.key_hash == key_hash)
            )
        )
        return row.response_payload if row is not None else None

    def _store_idempotency(
        self,
        *,
        session: Session,
        scope: str,
        idempotency_key: str,
        order_id: str | None,
        response_payload: dict[str, Any] | None,
    ) -> None:
        session.add(
            IdempotencyKey(
                scope=scope,
                key_hash=_hash_idempotency_key(idempotency_key),
                order_id=order_id,
                response_payload=response_payload,
            )
        )

    def _audit(
        self,
        *,
        session: Session,
        actor: str,
        action: str,
        resource_type: str,
        resource_id: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        session.add(
            AuditLog(
                actor=actor,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                payload=payload,
            )
        )
