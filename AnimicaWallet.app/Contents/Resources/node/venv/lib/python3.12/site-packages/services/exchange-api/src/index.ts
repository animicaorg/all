/**
 * Exchange API Service - Main Entry Point
 * 
 * Exports all core services and utilities for use in API routes,
 * background jobs, and scripts.
 */

export { prisma } from './db/client.js';
export { LedgerService } from './services/ledger.js';
export { ReconciliationService } from './services/reconciliation.js';

export * from './invariants/ledger.js';

// Re-export Prisma types for convenience
export type {
  User,
  UserProfile,
  ApiKey,
  Session,
  KycCase,
  KycDocument,
  Network,
  Asset,
  AssetNetwork,
  Wallet,
  UserDepositAddress,
  Market,
  Order,
  OrderEvent,
  Trade,
  LedgerAccount,
  LedgerTransaction,
  LedgerEntry,
  BalanceCache,
  Deposit,
  Withdrawal,
  WithdrawalApproval,
  FeeSchedule,
  AuditLog,
  IdempotencyKey,
} from '@prisma/client';

export {
  UserStatus,
  UserRole,
  KycProvider,
  KycStatus,
  RiskTier,
  NetworkKind,
  AssetKind,
  WalletPurpose,
  WalletProvider,
  DepositAddressStatus,
  MarketStatus,
  OrderSide,
  OrderType,
  TimeInForce,
  OrderStatus,
  OrderEventType,
  LedgerAccountOwnerType,
  LedgerAccountType,
  LedgerTransactionType,
  EntryDirection,
  DepositStatus,
  DepositSource,
  WithdrawalStatus,
  WithdrawalProvider,
  WithdrawalApprovalAction,
  FeeScope,
  AuditActorType,
  IdempotencyScope,
} from '@prisma/client';
