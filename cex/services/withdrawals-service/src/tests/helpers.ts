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
      asset_symbol: "BTC",
      asset_decimals: 8,
      network_id: "network-bitcoin-mainnet",
      network_name: "BTC",
      address_type: "UTXO",
      provider: "BITGO",
      bitgo_coin: "btc",
      enabled: true,
      decimals: 8,
      confirmations_required: 3,
      metadata: {},
    });

    this.networks.set("an-ltc-mainnet", {
      id: "an-ltc-mainnet",
      asset_id: "asset-ltc",
      asset_symbol: "LTC",
      asset_decimals: 8,
      network_id: "network-litecoin-mainnet",
      network_name: "LTC",
      address_type: "UTXO",
      provider: "BITGO",
      bitgo_coin: "ltc",
      enabled: true,
      decimals: 8,
      confirmations_required: 6,
      metadata: {},
    });

    this.networks.set("an-doge-mainnet", {
      id: "an-doge-mainnet",
      asset_id: "asset-doge",
      asset_symbol: "DOGE",
      asset_decimals: 8,
      network_id: "network-dogecoin-mainnet",
      network_name: "DOGE",
      address_type: "UTXO",
      provider: "BITGO",
      bitgo_coin: "doge",
      enabled: true,
      decimals: 8,
      confirmations_required: 20,
      metadata: {},
    });

    this.networks.set("an-zec-mainnet", {
      id: "an-zec-mainnet",
      asset_id: "asset-zec",
      asset_symbol: "ZEC",
      asset_decimals: 8,
      network_id: "network-zcash-mainnet",
      network_name: "ZEC",
      address_type: "UTXO",
      provider: "BITGO",
      bitgo_coin: "zec",
      enabled: true,
      decimals: 8,
      confirmations_required: 24,
      metadata: {},
    });

    // Wallets
    this.wallets.set("an-btc-mainnet:HOT", {
      asset_network_id: "an-btc-mainnet",
      wallet_type: "HOT",
      provider: "BITGO",
      provider_wallet_id: "bitgo-wallet-btc-hot",
      metadata: {},
    });

    this.wallets.set("an-ltc-mainnet:HOT", {
      asset_network_id: "an-ltc-mainnet",
      wallet_type: "HOT",
      provider: "BITGO",
      provider_wallet_id: "bitgo-wallet-ltc-hot",
      metadata: {},
    });

    this.wallets.set("an-doge-mainnet:HOT", {
      asset_network_id: "an-doge-mainnet",
      wallet_type: "HOT",
      provider: "BITGO",
      provider_wallet_id: "bitgo-wallet-doge-hot",
      metadata: {},
    });

    this.wallets.set("an-zec-mainnet:HOT", {
      asset_network_id: "an-zec-mainnet",
      wallet_type: "HOT",
      provider: "BITGO",
      provider_wallet_id: "bitgo-wallet-zec-hot",
      metadata: {},
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
      requiredApprovals: 1,
      high_risk_threshold_atoms: "50000000", // 0.5 BTC
      high_risk_approvals: 2,
      highRiskApprovals: 2,
      whitelist_only: false,
      enabled: true,
      metadata: {
        withdrawalFeeAtoms: "5000", // 0.00005 BTC
      },
      created_at: new Date(),
      updated_at: new Date(),
    });

    this.policies.set("an-ltc-mainnet", {
      id: "policy-ltc",
      asset_network_id: "an-ltc-mainnet",
      min_withdrawal_atoms: "100000", // 0.001 LTC
      max_withdrawal_atoms: "10000000000",
      daily_limit_atoms: "50000000000",
      daily_limit_count: 20,
      kyc_tier_required: ["BASIC"],
      required_approvals: 1,
      requiredApprovals: 1,
      high_risk_threshold_atoms: "5000000000",
      high_risk_approvals: 2,
      highRiskApprovals: 2,
      whitelist_only: false,
      enabled: true,
      metadata: {
        withdrawalFeeAtoms: "10000",
      },
      created_at: new Date(),
      updated_at: new Date(),
    });

    this.policies.set("an-doge-mainnet", {
      id: "policy-doge",
      asset_network_id: "an-doge-mainnet",
      min_withdrawal_atoms: "1000000000", // 10 DOGE
      max_withdrawal_atoms: "1000000000000",
      daily_limit_atoms: "5000000000000",
      daily_limit_count: 20,
      kyc_tier_required: ["BASIC"],
      required_approvals: 1,
      requiredApprovals: 1,
      high_risk_threshold_atoms: "50000000000",
      high_risk_approvals: 2,
      highRiskApprovals: 2,
      whitelist_only: false,
      enabled: true,
      metadata: {
        withdrawalFeeAtoms: "100000000",
      },
      created_at: new Date(),
      updated_at: new Date(),
    });

    this.policies.set("an-zec-mainnet", {
      id: "policy-zec",
      asset_network_id: "an-zec-mainnet",
      min_withdrawal_atoms: "100000", // 0.001 ZEC
      max_withdrawal_atoms: "10000000000",
      daily_limit_atoms: "50000000000",
      daily_limit_count: 20,
      kyc_tier_required: ["BASIC"],
      required_approvals: 1,
      requiredApprovals: 1,
      high_risk_threshold_atoms: "5000000000",
      high_risk_approvals: 2,
      highRiskApprovals: 2,
      whitelist_only: false,
      enabled: true,
      metadata: {
        withdrawalFeeAtoms: "10000",
      },
      created_at: new Date(),
      updated_at: new Date(),
    });
  }
}

function parseJsonValue(value: any, fallback: any) {
  if (value === undefined || value === null) return fallback;
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

function completeWithdrawal(row: any) {
  const now = new Date();
  const amount = String(row.amount ?? "10000000");
  const feeAmount = String(row.fee_amount ?? "5000");
  const totalDebitAmount =
    row.total_debit_amount !== undefined
      ? String(row.total_debit_amount)
      : (BigInt(amount) + BigInt(feeAmount)).toString();

  return {
    id: row.id ?? `wd-${Math.random().toString(36).substr(2, 9)}`,
    user_id: row.user_id ?? fixtures.users.alice,
    asset_network_id: row.asset_network_id ?? "an-btc-mainnet",
    destination_address: row.destination_address ?? fixtures.addresses.btc.valid,
    destination_tag: row.destination_tag ?? null,
    amount,
    fee_amount: feeAmount,
    total_debit_amount: totalDebitAmount,
    status: row.status ?? "REQUESTED",
    idempotency_key: row.idempotency_key ?? `idem-${row.id ?? Math.random().toString(36).substr(2, 9)}`,
    client_withdrawal_id: row.client_withdrawal_id ?? null,
    provider: row.provider ?? "BITGO",
    provider_ref: row.provider_ref ?? null,
    txid: row.txid ?? null,
    risk_score: row.risk_score ?? null,
    risk_flags: parseJsonValue(row.risk_flags, []),
    risk_reason: row.risk_reason ?? null,
    requested_at: row.requested_at ?? row.created_at ?? now,
    approved_at: row.approved_at ?? null,
    broadcast_at: row.broadcast_at ?? null,
    confirmed_at: row.confirmed_at ?? null,
    failure_code: row.failure_code ?? null,
    failure_message: row.failure_message ?? null,
    attempt_count: Number(row.attempt_count ?? 0),
    next_retry_at: row.next_retry_at ?? null,
    created_at: row.created_at ?? now,
    updated_at: row.updated_at ?? now,
  };
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
      if (/INSERT\s+INTO\s+withdrawals/i.test(query)) {
        const withdrawal = completeWithdrawal({
          id: `wd-${Math.random().toString(36).substr(2, 9)}`,
          user_id: values?.[0],
          asset_network_id: values?.[1],
          destination_address: values?.[2],
          destination_tag: values?.[3] || null,
          amount: values?.[4],
          fee_amount: values?.[5],
          total_debit_amount: values?.[6],
          idempotency_key: values?.[7],
          client_withdrawal_id: values?.[8] || null,
          provider: values?.[9] || "BITGO",
          risk_score: values?.[10] || null,
          risk_flags: values?.[11] || "[]",
          risk_reason: values?.[12] || null,
          status: "REQUESTED",
        });
        db.withdrawals.set(withdrawal.id, withdrawal);
        return { rows: [withdrawal], rowCount: 1 };
      }

      // Withdrawals - SELECT by ID
      if (/FROM\s+withdrawals/i.test(query) && /WHERE\s+id\s*=\s*\$1/i.test(query)) {
        const id = values?.[0];
        const withdrawal = db.withdrawals.has(id)
          ? completeWithdrawal(db.withdrawals.get(id))
          : null;
        return withdrawal ? { rows: [withdrawal], rowCount: 1 } : { rows: [], rowCount: 0 };
      }

      // Withdrawals - SELECT by provider_ref
      if (/FROM\s+withdrawals/i.test(query) && /provider_ref\s*=/i.test(query)) {
        const providerRef = values?.[0];
        const withdrawal = Array.from(db.withdrawals.values()).map(completeWithdrawal).find(
          (w) => w.provider_ref === providerRef
        );
        return withdrawal ? { rows: [withdrawal], rowCount: 1 } : { rows: [], rowCount: 0 };
      }

      // Withdrawals - UPDATE status
      if (/UPDATE\s+withdrawals\s+SET/i.test(query) && /status\s*=\s*\$2/i.test(query)) {
        const id = values?.[0];
        const status = values?.[1];
        const withdrawal = db.withdrawals.has(id)
          ? completeWithdrawal(db.withdrawals.get(id))
          : null;
        if (withdrawal) {
          withdrawal.status = status;
          withdrawal.updated_at = new Date();

          let nextValueIndex = 2;
          if (/provider_ref\s*=/i.test(query)) {
            withdrawal.provider_ref = values?.[nextValueIndex++];
          }
          if (/txid\s*=/i.test(query)) {
            withdrawal.txid = values?.[nextValueIndex++];
          }
          if (/failure_code\s*=/i.test(query)) {
            withdrawal.failure_code = values?.[nextValueIndex++];
          }
          if (/failure_message\s*=/i.test(query)) {
            withdrawal.failure_message = values?.[nextValueIndex++];
          }
          if (/attempt_count\s*=\s*attempt_count\s*\+\s*1/i.test(query)) {
            withdrawal.attempt_count += 1;
          }
          if (/next_retry_at\s*=/i.test(query)) {
            withdrawal.next_retry_at = values?.[nextValueIndex++];
          }
          if (status === "APPROVED") withdrawal.approved_at = new Date();
          if (status === "BROADCAST") withdrawal.broadcast_at = new Date();
          if (status === "CONFIRMED") withdrawal.confirmed_at = new Date();
          db.withdrawals.set(id, withdrawal);
          return { rows: [withdrawal], rowCount: 1 };
        }
        return { rows: [], rowCount: 0 };
      }

      // Velocity checks - SUM
      if (query.includes("COALESCE(SUM(total_debit_amount")) {
        const userId = values?.[0];
        const assetNetworkId = values?.[1];
        const withdrawals = Array.from(db.withdrawals.values()).map(completeWithdrawal).filter(
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
        const withdrawals = Array.from(db.withdrawals.values()).map(completeWithdrawal).filter(
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
        const exists = Array.from(db.withdrawals.values()).map(completeWithdrawal).some(
          (w) =>
            w.user_id === userId &&
            w.destination_address === address &&
            w.status === "CONFIRMED"
        );
        return exists ? { rows: [{ exists: 1 }], rowCount: 1 } : { rows: [], rowCount: 0 };
      }

      // Approvals - INSERT
      if (/INSERT\s+INTO\s+withdrawal_approvals/i.test(query)) {
        const approval = {
          id: `appr-${Math.random().toString(36).substr(2, 9)}`,
          withdrawal_id: values?.[0],
          approver_id: values?.[1],
          approver_role: values?.[2],
          action: values?.[3],
          reason: values?.[4] || null,
          metadata: parseJsonValue(values?.[5], {}),
          created_at: new Date(),
        };
        db.approvals.push(approval);
        return { rows: [approval], rowCount: 1 };
      }

      // Approvals - Check if already approved
      if (/SELECT\s+1\s+FROM\s+withdrawal_approvals/i.test(query)) {
        const withdrawalId = values?.[0];
        const approverId = values?.[1];
        const exists = db.approvals.some(
          (a) => a.withdrawal_id === withdrawalId && a.approver_id === approverId
        );
        return exists ? { rows: [{ exists: 1 }], rowCount: 1 } : { rows: [], rowCount: 0 };
      }

      // Approvals - COUNT
      if (/COUNT\(\*\)/i.test(query) && /FROM\s+withdrawal_approvals/i.test(query) && /APPROVE/i.test(query)) {
        const withdrawalId = values?.[0];
        const count = db.approvals.filter(
          (a) => a.withdrawal_id === withdrawalId && a.action === "APPROVE"
        ).length;
        return { rows: [{ count: count.toString() }], rowCount: 1 };
      }

      // Audit log
      if (/INSERT\s+INTO\s+withdrawal_audit_log/i.test(query)) {
        const log = {
          id: `audit-${Math.random().toString(36).substr(2, 9)}`,
          event_type: values?.[0],
          withdrawal_id: values?.[1],
          user_id: values?.[2],
          actor_id: values?.[3],
          actor_type: values?.[4],
          changes: parseJsonValue(values?.[5], {}),
          metadata: parseJsonValue(values?.[6], {}),
          ip_address: values?.[7] || null,
          created_at: new Date(),
        };
        db.auditLog.push(log);
        return { rows: [log], rowCount: 1 };
      }

      // Outbox - completed ledger lock lookup
      if (
        /FROM\s+withdrawal_outbox/i.test(query) &&
        /APPLY_LEDGER_LOCK/i.test(query) &&
        /COMPLETED/i.test(query)
      ) {
        const withdrawalId = values?.[0];
        const exists = db.outbox.some(
          (op) =>
            op.withdrawal_id === withdrawalId &&
            op.type === "APPLY_LEDGER_LOCK" &&
            op.status === "COMPLETED"
        );
        return exists ? { rows: [{ exists: 1 }], rowCount: 1 } : { rows: [], rowCount: 0 };
      }

      // Outbox - duplicate lookup
      if (/SELECT\s+\*\s+FROM\s+withdrawal_outbox/i.test(query) && /type\s*=\s*\$2/i.test(query)) {
        const withdrawalId = values?.[0];
        const type = values?.[1];
        const existing = db.outbox
          .filter((op) => op.withdrawal_id === withdrawalId && op.type === type)
          .sort((a, b) => b.created_at.getTime() - a.created_at.getTime())[0];
        return existing ? { rows: [existing], rowCount: 1 } : { rows: [], rowCount: 0 };
      }

      // Outbox - INSERT
      if (/INSERT\s+INTO\s+withdrawal_outbox/i.test(query)) {
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
      if (/SELECT\s+\*\s+FROM\s+withdrawal_outbox/i.test(query) && /PENDING/i.test(query)) {
        const limit = values?.[0] || 10;
        const pending = db.outbox
          .filter((op) => op.status === "PENDING" && op.attempt_count < 10)
          .slice(0, limit);
        return { rows: pending, rowCount: pending.length };
      }

      // Outbox - UPDATE
      if (/UPDATE\s+withdrawal_outbox/i.test(query)) {
        const id = /WHERE\s+id\s*=\s*\$1/i.test(query)
          ? values?.[0]
          : /WHERE\s+id\s*=\s*\$2/i.test(query)
            ? values?.[1]
            : values?.[values.length - 1];
        const operation = db.outbox.find((op) => op.id === id);
        if (operation) {
          const literalStatus = query.match(/status\s*=\s*'([^']+)'/i)?.[1];
          const status = literalStatus ?? values?.[0] ?? "PENDING";
          operation.status = status;
          operation.updated_at = new Date();
          if (status === "COMPLETED") {
            operation.processed_at = new Date();
          }
          if (status === "PROCESSING") {
            // No extra fields
          }
          if (/attempt_count\s*=\s*attempt_count\s*\+\s*1/i.test(query)) {
            operation.attempt_count++;
          }
          if (status === "PENDING") {
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
  requests: Array<{ coin: string; walletId: string; request: BitGoTransferRequest }>;
  reset: () => void;
} {
  const transfers = new Map<string, any>();
  const requests: Array<{ coin: string; walletId: string; request: BitGoTransferRequest }> = [];

  return {
    transfers,
    requests,
    reset: () => {
      transfers.clear();
      requests.splice(0);
    },
    
    createTransfer: async (
      coin: string,
      walletId: string,
      request: BitGoTransferRequest
    ): Promise<BitGoTransferResponse> => {
      requests.push({ coin, walletId, request });
      const transferId = `bitgo-transfer-${Math.random().toString(36).substr(2, 9)}`;
      const transfer = {
        id: transferId,
        coin,
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

    getTransfer: async (_coin: string, _walletId: string, transferId: string) => {
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
    ltc: {
      valid: "LcHKQ9ECav6QgSzoVj7M9cQf4K6hK7sqv2",
    },
    doge: {
      valid: "D9z6gY9dRSV2bT8yAyoJHdQBLG4YTR2v5Y",
    },
    zec: {
      valid: "t1KzGCHTq9uqUqWcGzYQvh4p3s3Y34sX76m",
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
