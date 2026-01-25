/**
 * Test Helpers and Mocks
 */

import type { PoolClient } from "pg";
import type { Logger } from "pino";
import type { BitGoClient } from "../bitgo/client.js";
import type { BitGoTransferRequest, BitGoTransferResponse } from "../bitgo/types.js";

/**
 * In-memory database state for testing
 */
export class MockDatabase {
  withdrawals = new Map<string, any>();
  policies = new Map<string, any>();
  networks = new Map<string, any>();
  wallets = new Map<string, any>();
  approvals: any[] = [];
  auditLog: any[] = [];
  outbox: any[] = [];
  idempotencyKeys = new Map<string, any>();
  queryLog: Array<{ query: string; values: any[] }> = [];

  reset() {
    this.withdrawals.clear();
    this.policies.clear();
    this.networks.clear();
    this.wallets.clear();
    this.approvals = [];
    this.auditLog = [];
    this.outbox = [];
    this.idempotencyKeys.clear();
    this.queryLog = [];
  }

  // Helper to setup test data
  setupTestData() {
    // Asset networks
    this.networks.set("an-btc-mainnet", {
      id: "an-btc-mainnet",
      asset_id: "asset-btc",
      network_id: "network-bitcoin-mainnet",
      enabled: true,
      decimals: 8,
    });

    this.networks.set("an-eth-mainnet", {
      id: "an-eth-mainnet",
      asset_id: "asset-eth",
      network_id: "network-ethereum-mainnet",
      enabled: true,
      decimals: 18,
    });

    // Wallets
    this.wallets.set("an-btc-mainnet:HOT", {
      asset_network_id: "an-btc-mainnet",
      wallet_type: "HOT",
      provider: "BITGO",
      provider_wallet_id: "bitgo-wallet-btc-hot",
    });

    this.wallets.set("an-eth-mainnet:HOT", {
      asset_network_id: "an-eth-mainnet",
      wallet_type: "HOT",
      provider: "BITGO",
      provider_wallet_id: "bitgo-wallet-eth-hot",
    });

    // Withdrawal policies
    this.policies.set("an-btc-mainnet", {
      id: "policy-btc",
      asset_network_id: "an-btc-mainnet",
      min_withdrawal_atoms: "10000", // 0.0001 BTC
      max_withdrawal_atoms: "100000000", // 1 BTC
      daily_limit_atoms: "500000000", // 5 BTC
      daily_limit_count: 10,
      kyc_tier_required: ["BASIC"],
      required_approvals: 1,
      high_risk_threshold_atoms: "50000000", // 0.5 BTC
      high_risk_approvals: 2,
      whitelist_only: false,
      enabled: true,
      metadata: {
        withdrawalFeeAtoms: "5000", // 0.00005 BTC
      },
      created_at: new Date(),
      updated_at: new Date(),
    });

    this.policies.set("an-eth-mainnet", {
      id: "policy-eth",
      asset_network_id: "an-eth-mainnet",
      min_withdrawal_atoms: "1000000000000000", // 0.001 ETH
      max_withdrawal_atoms: "10000000000000000000", // 10 ETH
      daily_limit_atoms: "50000000000000000000", // 50 ETH
      daily_limit_count: 20,
      kyc_tier_required: ["BASIC"],
      required_approvals: 1,
      high_risk_threshold_atoms: "5000000000000000000", // 5 ETH
      high_risk_approvals: 2,
      whitelist_only: false,
      enabled: true,
      metadata: {
        withdrawalFeeAtoms: "10000000000000000", // 0.01 ETH
      },
      created_at: new Date(),
      updated_at: new Date(),
    });
  }
}

/**
 * Mock PostgreSQL client
 */
export function createMockClient(db: MockDatabase): PoolClient {
  let transactionActive = false;

  const mockClient = {
    query: async (query: string, values?: any[]) => {
      db.queryLog.push({ query, values: values || [] });

      // BEGIN/COMMIT/ROLLBACK
      if (query.includes("BEGIN")) {
        transactionActive = true;
        return { rows: [], rowCount: 0 };
      }
      if (query.includes("COMMIT")) {
        transactionActive = false;
        return { rows: [], rowCount: 0 };
      }
      if (query.includes("ROLLBACK")) {
        transactionActive = false;
        return { rows: [], rowCount: 0 };
      }

      // Asset networks
      if (query.includes("asset_networks") && query.includes("SELECT")) {
        const id = values?.[0];
        const network = db.networks.get(id);
        return network ? { rows: [network], rowCount: 1 } : { rows: [], rowCount: 0 };
      }

      // Withdrawal policies
      if (query.includes("withdrawal_policies") && query.includes("SELECT")) {
        const assetNetworkId = values?.[0];
        const policy = db.policies.get(assetNetworkId);
        return policy ? { rows: [policy], rowCount: 1 } : { rows: [], rowCount: 0 };
      }

      // Wallets
      if (query.includes("wallets") && query.includes("SELECT")) {
        const assetNetworkId = values?.[0];
        const walletType = values?.[1];
        const wallet = db.wallets.get(`${assetNetworkId}:${walletType}`);
        return wallet ? { rows: [wallet], rowCount: 1 } : { rows: [], rowCount: 0 };
      }

      // Withdrawals - INSERT
      if (query.includes("INSERT INTO withdrawals")) {
        const id = `wd-${Math.random().toString(36).substr(2, 9)}`;
        const withdrawal = {
          id,
          user_id: values?.[0],
          asset_network_id: values?.[1],
          destination_address: values?.[2],
          destination_tag: values?.[3] || null,
          amount: values?.[4],
          fee_amount: values?.[5],
          total_debit_amount: values?.[6],
          idempotency_key: values?.[7],
          client_withdrawal_id: values?.[8] || null,
          risk_score: values?.[9] || null,
          risk_flags: values?.[10] || "[]",
          risk_reason: values?.[11] || null,
          status: "REQUESTED",
          provider: "BITGO",
          provider_ref: null,
          txid: null,
          requested_at: new Date(),
          approved_at: null,
          broadcast_at: null,
          confirmed_at: null,
          failure_code: null,
          failure_message: null,
          attempt_count: 0,
          next_retry_at: null,
          created_at: new Date(),
          updated_at: new Date(),
        };
        db.withdrawals.set(id, withdrawal);
        return { rows: [withdrawal], rowCount: 1 };
      }

      // Withdrawals - SELECT by ID
      if (query.includes("SELECT * FROM withdrawals WHERE id =")) {
        const id = values?.[0];
        const withdrawal = db.withdrawals.get(id);
        return withdrawal ? { rows: [withdrawal], rowCount: 1 } : { rows: [], rowCount: 0 };
      }

      // Withdrawals - SELECT by provider_ref
      if (query.includes("provider_ref =")) {
        const providerRef = values?.[0];
        const withdrawal = Array.from(db.withdrawals.values()).find(
          (w) => w.provider_ref === providerRef
        );
        return withdrawal ? { rows: [withdrawal], rowCount: 1 } : { rows: [], rowCount: 0 };
      }

      // Withdrawals - UPDATE status
      if (query.includes("UPDATE withdrawals SET status =")) {
        const status = values?.[0];
        const id = values?.[values.length - 1];
        const withdrawal = db.withdrawals.get(id);
        if (withdrawal) {
          withdrawal.status = status;
          withdrawal.updated_at = new Date();
          // Handle optional fields based on query
          if (query.includes("provider_ref")) {
            withdrawal.provider_ref = values?.[1];
          }
          if (query.includes("txid")) {
            withdrawal.txid = values?.[query.includes("provider_ref") ? 2 : 1];
          }
          if (query.includes("failure_code")) {
            withdrawal.failure_code = values?.[1];
            withdrawal.failure_message = values?.[2];
          }
          if (status === "APPROVED") withdrawal.approved_at = new Date();
          if (status === "BROADCAST") withdrawal.broadcast_at = new Date();
          if (status === "CONFIRMED") withdrawal.confirmed_at = new Date();
          return { rows: [withdrawal], rowCount: 1 };
        }
        return { rows: [], rowCount: 0 };
      }

      // Velocity checks - SUM
      if (query.includes("COALESCE(SUM(total_debit_amount")) {
        const userId = values?.[0];
        const assetNetworkId = values?.[1];
        const withdrawals = Array.from(db.withdrawals.values()).filter(
          (w) =>
            w.user_id === userId &&
            w.asset_network_id === assetNetworkId &&
            !["REJECTED", "CANCELED", "FAILED"].includes(w.status)
        );
        const total = withdrawals.reduce((sum, w) => sum + BigInt(w.total_debit_amount), 0n);
        return { rows: [{ total: total.toString() }], rowCount: 1 };
      }

      // Velocity checks - COUNT
      if (query.includes("COUNT(*) as count") && query.includes("withdrawals")) {
        const userId = values?.[0];
        const assetNetworkId = values?.[1];
        const withdrawals = Array.from(db.withdrawals.values()).filter(
          (w) =>
            w.user_id === userId &&
            w.asset_network_id === assetNetworkId &&
            !["REJECTED", "CANCELED", "FAILED"].includes(w.status)
        );
        return { rows: [{ count: withdrawals.length.toString() }], rowCount: 1 };
      }

      // New address check
      if (query.includes("destination_address =") && query.includes("CONFIRMED")) {
        const userId = values?.[0];
        const address = values?.[1];
        const exists = Array.from(db.withdrawals.values()).some(
          (w) =>
            w.user_id === userId &&
            w.destination_address === address &&
            w.status === "CONFIRMED"
        );
        return exists ? { rows: [{ exists: 1 }], rowCount: 1 } : { rows: [], rowCount: 0 };
      }

      // Approvals - INSERT
      if (query.includes("INSERT INTO approvals")) {
        const approval = {
          id: `appr-${Math.random().toString(36).substr(2, 9)}`,
          withdrawal_id: values?.[0],
          approver_id: values?.[1],
          approver_role: values?.[2],
          action: values?.[3],
          reason: values?.[4] || null,
          created_at: new Date(),
        };
        db.approvals.push(approval);
        return { rows: [approval], rowCount: 1 };
      }

      // Approvals - Check if already approved
      if (query.includes("SELECT 1 FROM approvals")) {
        const withdrawalId = values?.[0];
        const approverId = values?.[1];
        const exists = db.approvals.some(
          (a) => a.withdrawal_id === withdrawalId && a.approver_id === approverId
        );
        return exists ? { rows: [{ exists: 1 }], rowCount: 1 } : { rows: [], rowCount: 0 };
      }

      // Approvals - COUNT
      if (query.includes("COUNT(*) FROM approvals") && query.includes("APPROVE")) {
        const withdrawalId = values?.[0];
        const count = db.approvals.filter(
          (a) => a.withdrawal_id === withdrawalId && a.action === "APPROVE"
        ).length;
        return { rows: [{ count: count.toString() }], rowCount: 1 };
      }

      // Audit log
      if (query.includes("INSERT INTO audit_log")) {
        const log = {
          id: `audit-${Math.random().toString(36).substr(2, 9)}`,
          event_type: values?.[0],
          withdrawal_id: values?.[1],
          user_id: values?.[2],
          actor_id: values?.[3],
          actor_type: values?.[4],
          changes: values?.[5],
          metadata: values?.[6],
          created_at: new Date(),
        };
        db.auditLog.push(log);
        return { rows: [log], rowCount: 1 };
      }

      // Outbox - INSERT
      if (query.includes("INSERT INTO withdrawal_outbox")) {
        const id = `outbox-${Math.random().toString(36).substr(2, 9)}`;
        const operation = {
          id,
          withdrawal_id: values?.[0],
          type: values?.[1],
          payload: values?.[2],
          status: "PENDING",
          attempt_count: 0,
          next_retry_at: new Date(),
          last_error: null,
          created_at: new Date(),
          processed_at: null,
          updated_at: new Date(),
        };
        db.outbox.push(operation);
        return { rows: [operation], rowCount: 1 };
      }

      // Outbox - SELECT pending
      if (query.includes("SELECT * FROM withdrawal_outbox") && query.includes("PENDING")) {
        const limit = values?.[0] || 10;
        const pending = db.outbox
          .filter((op) => op.status === "PENDING" && op.attempt_count < 10)
          .slice(0, limit);
        return { rows: pending, rowCount: pending.length };
      }

      // Outbox - UPDATE
      if (query.includes("UPDATE withdrawal_outbox SET status =")) {
        const status = values?.[0];
        const id = values?.[values.length - 1];
        const operation = db.outbox.find((op) => op.id === id);
        if (operation) {
          operation.status = status;
          operation.updated_at = new Date();
          if (status === "COMPLETED") {
            operation.processed_at = new Date();
          }
          if (status === "PROCESSING") {
            // No extra fields
          }
          if (status === "PENDING") {
            operation.attempt_count++;
            operation.last_error = values?.[1];
            operation.next_retry_at = new Date(Date.now() + (values?.[2] || 60000));
          }
          return { rows: [operation], rowCount: 1 };
        }
        return { rows: [], rowCount: 0 };
      }

      // Default empty result
      return { rows: [], rowCount: 0 };
    },
    release: () => {},
  } as unknown as PoolClient;

  return mockClient;
}

/**
 * Mock BitGo client
 */
export function createMockBitGoClient(): BitGoClient & {
  transfers: Map<string, any>;
  reset: () => void;
} {
  const transfers = new Map<string, any>();

  return {
    transfers,
    reset: () => transfers.clear(),
    
    createTransfer: async (
      walletId: string,
      request: BitGoTransferRequest
    ): Promise<BitGoTransferResponse> => {
      const transferId = `bitgo-transfer-${Math.random().toString(36).substr(2, 9)}`;
      const transfer = {
        id: transferId,
        coin: walletId.includes("btc") ? "btc" : "eth",
        wallet: walletId,
        txid: `0x${Math.random().toString(36).substr(2, 16)}`,
        state: "signed" as const,
        value: request.amount,
        valueString: request.amount,
        entries: [
          {
            address: request.address,
            value: request.amount,
          },
        ],
        createdDate: new Date().toISOString(),
        sequenceId: request.sequenceId,
      };
      
      transfers.set(transferId, transfer);
      
      return { transfer };
    },

    getTransfer: async (walletId: string, transferId: string) => {
      const transfer = transfers.get(transferId);
      if (!transfer) {
        throw new Error("Transfer not found");
      }
      return { transfer };
    },
  } as any;
}

/**
 * Mock logger
 */
export function createMockLogger(): Logger {
  return {
    info: () => {},
    warn: () => {},
    error: () => {},
    debug: () => {},
    trace: () => {},
    fatal: () => {},
    child: () => createMockLogger(),
  } as any;
}

/**
 * Mock Redis client
 */
export function createMockRedis() {
  const store = new Map<string, string>();

  return {
    get: async (key: string) => store.get(key) || null,
    set: async (key: string, value: string, options?: any) => {
      store.set(key, value);
      return "OK";
    },
    del: async (key: string) => {
      store.delete(key);
      return 1;
    },
    exists: async (key: string) => (store.has(key) ? 1 : 0),
    reset: () => store.clear(),
  };
}

/**
 * Mock ledger service
 */
export function createMockLedgerService() {
  const operations: any[] = [];

  return {
    operations,
    reset: () => operations.splice(0),
    
    lock: async (payload: any) => {
      operations.push({ type: "LOCK", ...payload, timestamp: new Date() });
      return { transactionId: `ledger-tx-${operations.length}` };
    },

    broadcast: async (payload: any) => {
      operations.push({ type: "BROADCAST", ...payload, timestamp: new Date() });
      return { transactionId: `ledger-tx-${operations.length}` };
    },

    cancel: async (payload: any) => {
      operations.push({ type: "CANCEL", ...payload, timestamp: new Date() });
      return { transactionId: `ledger-tx-${operations.length}` };
    },
  };
}

/**
 * Test data fixtures
 */
export const fixtures = {
  users: {
    alice: "user-alice-123",
    bob: "user-bob-456",
    charlie: "user-charlie-789",
  },

  approvers: {
    admin1: "admin-john-001",
    admin2: "admin-jane-002",
    admin3: "admin-mike-003",
  },

  addresses: {
    btc: {
      valid: "1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa",
      another: "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2",
      new: "3J98t1WpEZ73CNmYviecrnyiWrnqRhWNLy",
    },
    eth: {
      valid: "0x742d35Cc6634C0532925a3b844Bc9e7595f0bEb",
      another: "0x0D0707963952f2fBA59dD06f2b425ace40b492Fe",
      new: "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
    },
  },

  amounts: {
    btc: {
      small: 50000n, // 0.0005 BTC
      medium: 10000000n, // 0.1 BTC
      large: 60000000n, // 0.6 BTC (triggers high risk)
      overLimit: 150000000n, // 1.5 BTC (over max)
    },
    eth: {
      small: 5000000000000000n, // 0.005 ETH
      medium: 1000000000000000000n, // 1 ETH
      large: 6000000000000000000n, // 6 ETH (triggers high risk)
      overLimit: 15000000000000000000n, // 15 ETH (over max)
    },
  },
};
