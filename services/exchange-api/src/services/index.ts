/**
 * Service Clients Export
 * 
 * Central export point for all service client modules.
 */

export { MarketDataCache, type OrderBookLevel, type OrderBookSnapshot, type OrderBookDiff, type Ticker } from './market_data_cache.js';

export {
  MatchingEngineClient,
  OrderSide,
  OrderType,
  OrderStatus,
  type OrderSubmitRequest,
  type OrderCancelRequest,
  type OrderSubmitResponse,
  type OrderCancelResponse,
  type MatchingEngineEvent,
  type TradeEvent,
  type OrderBookEvent,
  type OrderStatusEvent,
  type MatchingEngineConfig,
} from './matching_engine_client.js';

export { LedgerClient, type UserBalance, type AccountBalance } from './ledger_client.js';

export { UsersClient, type UserWithProfile, type UserFilters } from './users_client.js';

export {
  DepositsClient,
  DepositStatus,
  type Deposit,
  type DepositFilters,
  type DepositsResponse,
} from './deposits_client.js';

export {
  WithdrawalsClient,
  WithdrawalStatus,
  type Withdrawal,
  type WithdrawalFilters,
  type WithdrawalsResponse,
  type CreateWithdrawalRequest,
  type CreateWithdrawalResponse,
} from './withdrawals_client.js';

// Re-export existing services
export { LedgerService, type LedgerEntryInput, type CreateTransactionInput } from './ledger.js';
export { ReconciliationService } from './reconciliation.js';
