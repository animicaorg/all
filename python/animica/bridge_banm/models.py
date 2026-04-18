from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, utcnow


class BridgeOrder(Base):
    __tablename__ = "bridge_orders"

    order_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    direction: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_chain: Mapped[str] = mapped_column(String(32), nullable=False)
    destination_chain: Mapped[str] = mapped_column(String(32), nullable=False)
    source_address: Mapped[str] = mapped_column(String(255), nullable=False)
    destination_address: Mapped[str] = mapped_column(String(255), nullable=False)
    signed_evm_address: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)

    amount_in: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False)
    amount_out_expected: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False)
    fee_amount: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False, default=0)
    asset_in: Mapped[str] = mapped_column(String(16), nullable=False)
    asset_out: Mapped[str] = mapped_column(String(16), nullable=False)

    deposit_instruction_type: Mapped[str] = mapped_column(String(64), nullable=False)
    deposit_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deposit_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deposit_contract_address: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deposit_function: Mapped[str | None] = mapped_column(String(128), nullable=True)
    deposit_tx_hash: Mapped[str | None] = mapped_column(String(132), nullable=True, index=True)

    status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    confirmation_count_required: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confirmation_count_current: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    signature_challenge_nonce: Mapped[str | None] = mapped_column(String(128), nullable=True)
    signature_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    signature_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signature_method: Mapped[str | None] = mapped_column(String(32), nullable=True)

    settlement_tx_hash: Mapped[str | None] = mapped_column(String(132), nullable=True)
    release_tx_hash: Mapped[str | None] = mapped_column(String(132), nullable=True)

    claim_code_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_code_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    chain_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    manual_review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    manual_review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, unique=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    events: Mapped[list["BridgeOrderEvent"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        CheckConstraint("amount_in > 0", name="ck_bridge_orders_amount_in_positive"),
        CheckConstraint("amount_out_expected >= 0", name="ck_bridge_orders_amount_out_nonneg"),
        CheckConstraint("fee_amount >= 0", name="ck_bridge_orders_fee_nonneg"),
    )


class BridgeOrderEvent(Base):
    __tablename__ = "bridge_order_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(
        String(80),
        ForeignKey("bridge_orders.order_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    from_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    to_status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, default="system")
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    order: Mapped[BridgeOrder] = relationship(back_populates="events")


class BridgeDepositAnimica(Base):
    __tablename__ = "bridge_deposits_animica"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(80), ForeignKey("bridge_orders.order_id", ondelete="CASCADE"), nullable=False, index=True)
    tx_hash: Mapped[str] = mapped_column(String(132), nullable=False, unique=True)
    from_address: Mapped[str] = mapped_column(String(255), nullable=False)
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False)
    confirmations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    block_height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class BridgeDepositEvm(Base):
    __tablename__ = "bridge_deposits_evm"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(80), ForeignKey("bridge_orders.order_id", ondelete="CASCADE"), nullable=False, index=True)
    tx_hash: Mapped[str] = mapped_column(String(132), nullable=False, unique=True)
    sender: Mapped[str] = mapped_column(String(255), nullable=False)
    token_address: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False)
    confirmations: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    block_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    log_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class BridgeSignature(Base):
    __tablename__ = "bridge_signatures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(80), ForeignKey("bridge_orders.order_id", ondelete="CASCADE"), nullable=False, index=True)
    signer_address: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    signature_type: Mapped[str] = mapped_column(String(32), nullable=False, default="EIP712")
    challenge_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class BanmMint(Base):
    __tablename__ = "banm_mints"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(80), ForeignKey("bridge_orders.order_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False)
    fee_amount: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False, default=0)
    tx_hash: Mapped[str] = mapped_column(String(132), nullable=False, unique=True)
    tx_status: Mapped[str] = mapped_column(String(32), nullable=False, default="submitted")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BanmBurn(Base):
    __tablename__ = "banm_burns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(80), ForeignKey("bridge_orders.order_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    from_address: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(132), nullable=False, unique=True)
    tx_status: Mapped[str] = mapped_column(String(32), nullable=False, default="submitted")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AnmRelease(Base):
    __tablename__ = "anm_releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(80), ForeignKey("bridge_orders.order_id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    to_address: Mapped[str] = mapped_column(String(255), nullable=False)
    amount: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False)
    tx_hash: Mapped[str] = mapped_column(String(132), nullable=False, unique=True)
    tx_status: Mapped[str] = mapped_column(String(32), nullable=False, default="submitted")
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ChainConfig(Base):
    __tablename__ = "chain_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain_name: Mapped[str] = mapped_column(String(64), nullable=False)
    chain_id: Mapped[int] = mapped_column(Integer, nullable=False, unique=True, index=True)
    rpc_url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    confirmations_required: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class CustodyWallet(Base):
    __tablename__ = "custody_wallets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chain_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    key_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="viewer")
    password_salt: Mapped[str] = mapped_column(String(128), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, index=True)


class ServiceLock(Base):
    __tablename__ = "service_locks"

    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    response_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    __table_args__ = (UniqueConstraint("scope", "key_hash", name="uq_idempotency_scope_key_hash"),)


class ReconciliationRun(Base):
    __tablename__ = "reconciliation_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    summary: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    discrepancy_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserve_anm: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False, default=0)
    banm_total_supply_wei: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False, default=0)
    pending_forward_wei: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False, default=0)
    pending_reverse_anm: Mapped[int] = mapped_column(Numeric(78, 0), nullable=False, default=0)
