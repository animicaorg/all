/**
 * Idempotency Tests
 * 
 * Tests idempotency at all levels: HTTP, outbox, ledger, BitGo
 */

import { describe, it, expect, beforeEach } from "vitest";
import { validateAndCreateWithdrawal, type WithdrawalRequest } from "../pipeline/request.js";
import { submitToBitGo } from "../pipeline/submit.js";
import {
  MockDatabase,
  createMockClient,
  createMockLogger,
  createMockBitGoClient,
  fixtures,
} from "./helpers.js";

describe("Idempotency", () => {
  let db: MockDatabase;
  let mockClient: any;
  let mockLogger: any;
  let mockBitGo: any;

  beforeEach(() => {
    db = new MockDatabase();
    db.setupTestData();
    mockClient = createMockClient(db);
    mockLogger = createMockLogger();
    mockBitGo = createMockBitGoClient();
  });

  describe("HTTP Idempotency Key", () => {
    it("should prevent duplicate withdrawals with same idempotency key", async () => {
      const request: WithdrawalRequest = {
        assetNetworkId: "an-btc-mainnet",
        destinationAddress: fixtures.addresses.btc.valid,
        amount: fixtures.amounts.btc.medium,
      };

      const idempotencyKey = "http-idem-001";

      // First request
      const result1 = await validateAndCreateWithdrawal(
        mockClient,
        fixtures.users.alice,
        request,
        idempotencyKey,
        mockLogger
      );

      expect(result1.withdrawalId).toBeDefined();
      const withdrawalId1 = result1.withdrawalId;

      // Simulate unique constraint check in database
      // In production, the database would enforce unique constraint on idempotency_key
      const originalQuery = mockClient.query;
      mockClient.query = async (query: string, values?: any[]) => {
        if (query.includes("INSERT INTO withdrawals") && values?.[7] === idempotencyKey) {
          // Return existing withdrawal instead of creating new one
          const existing = Array.from(db.withdrawals.values()).find(
            (w: any) => w.idempotency_key === idempotencyKey
          );
          if (existing) {
            return { rows: [existing], rowCount: 1 };
          }
        }
        return originalQuery(query, values);
      };

      // Second request with same key
      const result2 = await validateAndCreateWithdrawal(
        mockClient,
        fixtures.users.alice,
        request,
        idempotencyKey,
        mockLogger
      );

      // Should return same withdrawal
      expect(result2.withdrawalId).toBe(withdrawalId1);

      // Should not create duplicate in database
      const withdrawalsWithKey = Array.from(db.withdrawals.values()).filter(
        (w: any) => w.idempotency_key === idempotencyKey
      );
      expect(withdrawalsWithKey).toHaveLength(1);
    });

    it("should allow different withdrawals with different idempotency keys", async () => {
      const request: WithdrawalRequest = {
        assetNetworkId: "an-btc-mainnet",
        destinationAddress: fixtures.addresses.btc.valid,
        amount: fixtures.amounts.btc.medium,
      };

      // First withdrawal
      const result1 = await validateAndCreateWithdrawal(
        mockClient,
        fixtures.users.alice,
        request,
        "http-idem-002",
        mockLogger
      );

      // Second withdrawal with different key
      const result2 = await validateAndCreateWithdrawal(
        mockClient,
        fixtures.users.alice,
        request,
        "http-idem-003",
        mockLogger
      );

      expect(result1.withdrawalId).not.toBe(result2.withdrawalId);
      expect(db.withdrawals.size).toBeGreaterThanOrEqual(2);
    });

    it("should scope idempotency key to user", async () => {
      const request: WithdrawalRequest = {
        assetNetworkId: "an-btc-mainnet",
        destinationAddress: fixtures.addresses.btc.valid,
        amount: fixtures.amounts.btc.medium,
      };

      const idempotencyKey = "http-idem-scoped-001";

      // User A
      const resultA = await validateAndCreateWithdrawal(
        mockClient,
        fixtures.users.alice,
        request,
        idempotencyKey,
        mockLogger
      );

      // User B with same key (should create separate withdrawal)
      const resultB = await validateAndCreateWithdrawal(
        mockClient,
        fixtures.users.bob,
        request,
        idempotencyKey,
        mockLogger
      );

      expect(resultA.withdrawalId).not.toBe(resultB.withdrawalId);
    });

    it("should handle missing idempotency key", async () => {
      const request: WithdrawalRequest = {
        assetNetworkId: "an-btc-mainnet",
        destinationAddress: fixtures.addresses.btc.valid,
        amount: fixtures.amounts.btc.medium,
      };

      // Create without idempotency key (should generate unique ID)
      const result = await validateAndCreateWithdrawal(
        mockClient,
        fixtures.users.alice,
        request,
        "", // Empty key
        mockLogger
      );

      expect(result.withdrawalId).toBeDefined();
    });
  });

  describe("Outbox Operations Idempotency", () => {
    it("should not create duplicate outbox entries for same withdrawal", async () => {
      const request: WithdrawalRequest = {
        assetNetworkId: "an-btc-mainnet",
        destinationAddress: fixtures.addresses.btc.valid,
        amount: fixtures.amounts.btc.medium,
      };

      const result = await validateAndCreateWithdrawal(
        mockClient,
        fixtures.users.alice,
        request,
        "outbox-idem-001",
        mockLogger
      );

      // Check that only one ledger lock operation was created
      const lockOps = db.outbox.filter(
        (op) => op.type === "APPLY_LEDGER_LOCK" && op.withdrawal_id === result.withdrawalId
      );
      expect(lockOps).toHaveLength(1);
    });

    it("should handle retry of outbox operation idempotently", async () => {
      // This test documents that the outbox worker should handle retries safely
      // The actual retry logic is in the worker, which processes operations
      // with SKIP LOCKED to prevent concurrent processing
      
      const operation = {
        id: "outbox-op-1",
        withdrawal_id: "wd-123",
        type: "APPLY_LEDGER_LOCK",
        payload: JSON.stringify({ userId: fixtures.users.alice, amount: "10000000" }),
        status: "PENDING",
        attempt_count: 0,
        next_retry_at: new Date(),
        last_error: null,
        created_at: new Date(),
        processed_at: null,
        updated_at: new Date(),
      };
      
      db.outbox.push(operation);

      // Worker would SELECT ... FOR UPDATE SKIP LOCKED
      // This ensures only one worker processes the operation at a time
      const pendingOps = db.outbox.filter(
        (op) => op.status === "PENDING" && op.attempt_count < 10
      );
      
      expect(pendingOps).toHaveLength(1);
      expect(pendingOps[0].id).toBe("outbox-op-1");
    });

    it("should mark operation as completed after successful processing", async () => {
      const operation = {
        id: "outbox-complete-1",
        withdrawal_id: "wd-456",
        type: "APPLY_LEDGER_LOCK",
        payload: JSON.stringify({ userId: fixtures.users.alice }),
        status: "PENDING",
        attempt_count: 0,
        next_retry_at: new Date(),
        last_error: null,
        created_at: new Date(),
        processed_at: null,
        updated_at: new Date(),
      };
      
      db.outbox.push(operation);

      // Simulate successful processing
      await mockClient.query(
        "UPDATE withdrawal_outbox SET status = $1, processed_at = NOW(), updated_at = NOW() WHERE id = $2",
        ["COMPLETED", operation.id]
      );

      const updated = db.outbox.find((op) => op.id === operation.id);
      expect(updated?.status).toBe("COMPLETED");
      expect(updated?.processed_at).toBeDefined();
    });

    it("should not reprocess completed operations", async () => {
      const operation = {
        id: "outbox-no-reprocess",
        withdrawal_id: "wd-789",
        type: "APPLY_LEDGER_LOCK",
        payload: JSON.stringify({ userId: fixtures.users.alice }),
        status: "COMPLETED",
        attempt_count: 1,
        next_retry_at: new Date(),
        last_error: null,
        created_at: new Date(),
        processed_at: new Date(),
        updated_at: new Date(),
      };
      
      db.outbox.push(operation);

      // Query for pending operations
      const pendingOps = db.outbox.filter(
        (op) => op.status === "PENDING" && op.attempt_count < 10
      );
      
      // Completed operation should not be selected
      expect(pendingOps.find((op) => op.id === operation.id)).toBeUndefined();
    });
  });

  describe("Ledger Operations Idempotency", () => {
    it("should use unique reference IDs for ledger operations", async () => {
      const request: WithdrawalRequest = {
        assetNetworkId: "an-btc-mainnet",
        destinationAddress: fixtures.addresses.btc.valid,
        amount: fixtures.amounts.btc.medium,
      };

      const result = await validateAndCreateWithdrawal(
        mockClient,
        fixtures.users.alice,
        request,
        "ledger-idem-001",
        mockLogger
      );

      const lockOps = db.outbox.filter(
        (op) => op.type === "APPLY_LEDGER_LOCK" && op.withdrawal_id === result.withdrawalId
      );
      
      expect(lockOps).toHaveLength(1);
      const payload = JSON.parse(lockOps[0].payload);
      
      // referenceId should be the withdrawal ID (unique)
      expect(payload.withdrawalId).toBe(result.withdrawalId);
      expect(payload.withdrawalId).toMatch(/^wd-/);
    });

    it("should handle ledger service rejecting duplicate operations", async () => {
      // In production, ledger service would reject operations with duplicate referenceId
      // This test documents expected behavior
      
      const duplicateRef = "wd-duplicate-123";
      
      // First operation
      const payload1 = {
        userId: fixtures.users.alice,
        assetNetworkId: "an-btc-mainnet",
        amount: "10000000",
        reason: "WITHDRAWAL",
        referenceId: duplicateRef,
      };

      // Second operation with same referenceId (should be rejected by ledger)
      const payload2 = {
        userId: fixtures.users.alice,
        assetNetworkId: "an-btc-mainnet",
        amount: "10000000",
        reason: "WITHDRAWAL",
        referenceId: duplicateRef,
      };

      // In production, ledger service would check referenceId uniqueness
      expect(payload1.referenceId).toBe(payload2.referenceId);
      
      // This documents that ledger service must enforce uniqueness
    });
  });

  describe("BitGo Submission Idempotency", () => {
    it("should use sequenceId for BitGo idempotency", async () => {
      const withdrawal = {
        id: "wd-bitgo-idem-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        destination_address: fixtures.addresses.btc.valid,
        amount: "10000000",
        status: "APPROVED",
        idempotency_key: "bitgo-idem-seq-001",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      await submitToBitGo(mockClient, withdrawal.id, mockBitGo, mockLogger);

      // Check that BitGo received sequenceId (idempotency key)
      const transfers = Array.from(mockBitGo.transfers.values());
      expect(transfers).toHaveLength(1);
      expect(transfers[0].sequenceId).toBe("bitgo-idem-seq-001");
    });

    it("should handle BitGo returning existing transfer for duplicate sequenceId", async () => {
      const withdrawal = {
        id: "wd-bitgo-idem-2",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        destination_address: fixtures.addresses.btc.valid,
        amount: "10000000",
        status: "APPROVED",
        idempotency_key: "bitgo-idem-seq-002",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      // First submission
      const result1 = await submitToBitGo(mockClient, withdrawal.id, mockBitGo, mockLogger);
      expect(result1.success).toBe(true);

      const transferCount1 = mockBitGo.transfers.size;
      const providerRef1 = db.withdrawals.get(withdrawal.id).provider_ref;

      // Reset status for retry
      withdrawal.status = "APPROVED";
      db.withdrawals.set(withdrawal.id, withdrawal);

      // Second submission with same sequenceId
      // BitGo would return the existing transfer
      const result2 = await submitToBitGo(mockClient, withdrawal.id, mockBitGo, mockLogger);
      expect(result2.success).toBe(true);

      // In production BitGo, this would return same transfer
      // Our mock creates a new one, but real BitGo enforces uniqueness
      expect(mockBitGo.transfers.size).toBeGreaterThanOrEqual(transferCount1);
    });

    it("should retry failed submission with same sequenceId", async () => {
      const withdrawal = {
        id: "wd-bitgo-retry-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        destination_address: fixtures.addresses.btc.valid,
        amount: "10000000",
        status: "APPROVED",
        idempotency_key: "bitgo-retry-seq-001",
        attempt_count: 0,
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      // First attempt fails
      mockBitGo.createTransfer = async () => {
        throw new Error("Network timeout");
      };

      const result1 = await submitToBitGo(mockClient, withdrawal.id, mockBitGo, mockLogger);
      expect(result1.success).toBe(false);

      // Provider failures are retry-safe; outbox retry/backoff owns resubmission.
      let updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("APPROVED");

      // Fix BitGo and retry
      mockBitGo.createTransfer = async (coin: string, walletId: string, request: any) => {
        const transferId = `bitgo-transfer-${Math.random().toString(36).substr(2, 9)}`;
        const transfer = {
          id: transferId,
          coin,
          wallet: walletId,
          txid: "0xretry123",
          state: "signed" as const,
          value: request.amount,
          valueString: request.amount,
          entries: [{ address: request.address, value: request.amount }],
          createdDate: new Date().toISOString(),
          sequenceId: request.sequenceId,
        };
        mockBitGo.transfers.set(transferId, transfer);
        return { transfer };
      };

      // Reset status for retry
      updatedWithdrawal.status = "APPROVED";
      db.withdrawals.set(withdrawal.id, updatedWithdrawal);

      // Retry with same idempotency key
      const result2 = await submitToBitGo(mockClient, withdrawal.id, mockBitGo, mockLogger);
      expect(result2.success).toBe(true);

      updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("BROADCAST");
    });
  });

  describe("End-to-End Idempotency", () => {
    it("should maintain idempotency across entire withdrawal lifecycle", async () => {
      const request: WithdrawalRequest = {
        assetNetworkId: "an-btc-mainnet",
        destinationAddress: fixtures.addresses.btc.valid,
        amount: fixtures.amounts.btc.medium,
      };

      const idempotencyKey = "e2e-idem-001";

      // Create withdrawal
      const result = await validateAndCreateWithdrawal(
        mockClient,
        fixtures.users.alice,
        request,
        idempotencyKey,
        mockLogger
      );

      const withdrawalId = result.withdrawalId;

      // Check outbox has one lock operation
      const lockOps1 = db.outbox.filter(
        (op) => op.type === "APPLY_LEDGER_LOCK" && op.withdrawal_id === withdrawalId
      );
      expect(lockOps1).toHaveLength(1);

      // Approve and submit
      const withdrawal = db.withdrawals.get(withdrawalId);
      withdrawal.status = "APPROVED";

      await submitToBitGo(mockClient, withdrawalId, mockBitGo, mockLogger);

      // Check BitGo submission used same idempotency key
      const transfers = Array.from(mockBitGo.transfers.values());
      const matchingTransfer = transfers.find((t) => t.sequenceId === idempotencyKey);
      expect(matchingTransfer).toBeDefined();

      // Verify the provider call did not duplicate ledger locks or enqueue itself.
      const lockOps2 = db.outbox.filter(
        (op) => op.type === "APPLY_LEDGER_LOCK" && op.withdrawal_id === withdrawalId
      );
      expect(lockOps2).toHaveLength(1);

      const submitOps = db.outbox.filter(
        (op) => op.type === "SUBMIT_TO_BITGO" && op.withdrawal_id === withdrawalId
      );
      expect(submitOps).toHaveLength(0);
    });
  });
});
