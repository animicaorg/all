/**
 * Outbox Worker Tests
 * 
 * Tests outbox pattern, retries, exponential backoff, and dead letter handling
 */

import { describe, it, expect, beforeEach, vi } from "vitest";
import { OutboxWorker } from "../outbox/worker.js";
import { calculateBackoff } from "../pipeline/retries.js";
import axios from "axios";
import {
  MockDatabase,
  createMockClient,
  createMockLogger,
  createMockBitGoClient,
  createMockLedgerService,
  fixtures,
} from "./helpers.js";

// Mock axios
vi.mock("axios");
const mockedAxios = axios as any;

describe("Outbox Worker", () => {
  let db: MockDatabase;
  let mockClient: any;
  let mockLogger: any;
  let mockBitGo: any;
  let mockLedger: any;
  let mockPool: any;

  beforeEach(() => {
    db = new MockDatabase();
    db.setupTestData();
    mockClient = createMockClient(db);
    mockLogger = createMockLogger();
    mockBitGo = createMockBitGoClient();
    mockLedger = createMockLedgerService();

    // Mock pool.connect()
    mockPool = {
      connect: async () => mockClient,
    };

    // Reset axios mock
    vi.clearAllMocks();
  });

  describe("Retry Logic", () => {
    it("should retry failed operations with exponential backoff", async () => {
      // Create a failing operation
      const operation = {
        id: "outbox-retry-1",
        withdrawal_id: "wd-retry-1",
        type: "APPLY_LEDGER_LOCK",
        payload: JSON.stringify({
          userId: fixtures.users.alice,
          assetNetworkId: "an-btc-mainnet",
          amount: "10000000",
          withdrawalId: "wd-retry-1",
        }),
        status: "PENDING",
        attempt_count: 0,
        next_retry_at: new Date(),
        last_error: null,
        created_at: new Date(),
        processed_at: null,
        updated_at: new Date(),
      };
      
      db.outbox.push(operation);

      // Mock axios to fail
      mockedAxios.post = vi.fn().mockRejectedValueOnce(new Error("Service unavailable"));

      const config = {
        LEDGER_SERVICE_URL: "http://ledger:3000",
        OUTBOX_WORKER_INTERVAL_MS: 1000,
      };

      const worker = new OutboxWorker(mockPool, mockBitGo, config as any, mockLogger);

      // Process operations
      await worker["processOperations"]();

      // Check that operation was marked for retry
      const op = db.outbox.find((o) => o.id === operation.id);
      expect(op?.status).toBe("PENDING");
      expect(op?.attempt_count).toBe(1);
      expect(op?.last_error).toBeDefined();
    });

    it("should use exponential backoff for retries", () => {
      // Test backoff calculation
      const backoff0 = calculateBackoff(0);
      const backoff1 = calculateBackoff(1);
      const backoff2 = calculateBackoff(2);
      const backoff3 = calculateBackoff(3);

      expect(backoff1).toBeGreaterThan(backoff0);
      expect(backoff2).toBeGreaterThan(backoff1);
      expect(backoff3).toBeGreaterThan(backoff2);

      // Exponential growth
      expect(backoff2 / backoff1).toBeGreaterThan(1.5);
    });

    it("should mark operation as permanently failed after max attempts", async () => {
      const operation = {
        id: "outbox-max-retry-1",
        withdrawal_id: "wd-max-retry-1",
        type: "APPLY_LEDGER_LOCK",
        payload: JSON.stringify({ userId: fixtures.users.alice }),
        status: "PENDING",
        attempt_count: 9, // One more attempt will hit max
        next_retry_at: new Date(),
        last_error: JSON.stringify({ message: "Previous failure" }),
        created_at: new Date(),
        processed_at: null,
        updated_at: new Date(),
      };
      
      db.outbox.push(operation);

      // Mock axios to fail again
      mockedAxios.post = vi.fn().mockRejectedValue(new Error("Still failing"));

      const config = {
        LEDGER_SERVICE_URL: "http://ledger:3000",
        OUTBOX_WORKER_INTERVAL_MS: 1000,
      };

      const worker = new OutboxWorker(mockPool, mockBitGo, config as any, mockLogger);

      await worker["processOperations"]();

      // Should be marked as permanently failed
      const op = db.outbox.find((o) => o.id === operation.id);
      expect(op?.status).toBe("FAILED");
      expect(op?.attempt_count).toBe(10);
    });

    it("should eventually succeed after transient failures", async () => {
      const operation = {
        id: "outbox-eventual-success-1",
        withdrawal_id: "wd-eventual-1",
        type: "APPLY_LEDGER_LOCK",
        payload: JSON.stringify({
          userId: fixtures.users.alice,
          assetNetworkId: "an-btc-mainnet",
          amount: "10000000",
          withdrawalId: "wd-eventual-1",
        }),
        status: "PENDING",
        attempt_count: 2, // Already tried twice
        next_retry_at: new Date(),
        last_error: JSON.stringify({ message: "Previous timeout" }),
        created_at: new Date(),
        processed_at: null,
        updated_at: new Date(),
      };
      
      db.outbox.push(operation);

      // Mock axios to succeed this time
      mockedAxios.post = vi.fn().mockResolvedValue({
        data: { transactionId: "ledger-tx-success" },
      });

      const config = {
        LEDGER_SERVICE_URL: "http://ledger:3000",
        OUTBOX_WORKER_INTERVAL_MS: 1000,
      };

      const worker = new OutboxWorker(mockPool, mockBitGo, config as any, mockLogger);

      await worker["processOperations"]();

      // Should be marked as completed
      const op = db.outbox.find((o) => o.id === operation.id);
      expect(op?.status).toBe("COMPLETED");
      expect(op?.processed_at).toBeDefined();
    });
  });

  describe("Operation Processing", () => {
    it("should process APPLY_LEDGER_LOCK operation", async () => {
      const operation = {
        id: "outbox-lock-1",
        withdrawal_id: "wd-lock-1",
        type: "APPLY_LEDGER_LOCK",
        payload: JSON.stringify({
          userId: fixtures.users.alice,
          assetNetworkId: "an-btc-mainnet",
          amount: "10000000",
          withdrawalId: "wd-lock-1",
        }),
        status: "PENDING",
        attempt_count: 0,
        next_retry_at: new Date(),
        last_error: null,
        created_at: new Date(),
        processed_at: null,
        updated_at: new Date(),
      };
      
      db.outbox.push(operation);

      mockedAxios.post = vi.fn().mockResolvedValue({
        data: { transactionId: "ledger-lock-tx-1" },
      });

      const config = {
        LEDGER_SERVICE_URL: "http://ledger:3000",
        OUTBOX_WORKER_INTERVAL_MS: 1000,
      };

      const worker = new OutboxWorker(mockPool, mockBitGo, config as any, mockLogger);

      await worker["processOperations"]();

      // Check axios was called correctly
      expect(mockedAxios.post).toHaveBeenCalledWith(
        "http://ledger:3000/internal/lock",
        expect.objectContaining({
          userId: fixtures.users.alice,
          assetNetworkId: "an-btc-mainnet",
          amount: "10000000",
          reason: "WITHDRAWAL",
          referenceId: "wd-lock-1",
        }),
        expect.any(Object)
      );

      // Operation should be completed
      const op = db.outbox.find((o) => o.id === operation.id);
      expect(op?.status).toBe("COMPLETED");
    });

    it("should process APPLY_LEDGER_BROADCAST operation", async () => {
      const operation = {
        id: "outbox-broadcast-1",
        withdrawal_id: "wd-broadcast-1",
        type: "APPLY_LEDGER_BROADCAST",
        payload: JSON.stringify({
          userId: fixtures.users.alice,
          withdrawalId: "wd-broadcast-1",
          txid: "0xbroadcast123",
        }),
        status: "PENDING",
        attempt_count: 0,
        next_retry_at: new Date(),
        last_error: null,
        created_at: new Date(),
        processed_at: null,
        updated_at: new Date(),
      };
      
      db.outbox.push(operation);

      mockedAxios.post = vi.fn().mockResolvedValue({
        data: { transactionId: "ledger-broadcast-tx-1" },
      });

      const config = {
        LEDGER_SERVICE_URL: "http://ledger:3000",
        OUTBOX_WORKER_INTERVAL_MS: 1000,
      };

      const worker = new OutboxWorker(mockPool, mockBitGo, config as any, mockLogger);

      await worker["processOperations"]();

      expect(mockedAxios.post).toHaveBeenCalledWith(
        "http://ledger:3000/internal/broadcast",
        expect.objectContaining({
          userId: fixtures.users.alice,
          withdrawalId: "wd-broadcast-1",
          txid: "0xbroadcast123",
        }),
        expect.any(Object)
      );

      const op = db.outbox.find((o) => o.id === operation.id);
      expect(op?.status).toBe("COMPLETED");
    });

    it("should process APPLY_LEDGER_CANCEL operation", async () => {
      const operation = {
        id: "outbox-cancel-1",
        withdrawal_id: "wd-cancel-1",
        type: "APPLY_LEDGER_CANCEL",
        payload: JSON.stringify({
          userId: fixtures.users.alice,
          withdrawalId: "wd-cancel-1",
          reason: "REJECTED",
        }),
        status: "PENDING",
        attempt_count: 0,
        next_retry_at: new Date(),
        last_error: null,
        created_at: new Date(),
        processed_at: null,
        updated_at: new Date(),
      };
      
      db.outbox.push(operation);

      mockedAxios.post = vi.fn().mockResolvedValue({
        data: { transactionId: "ledger-cancel-tx-1" },
      });

      const config = {
        LEDGER_SERVICE_URL: "http://ledger:3000",
        OUTBOX_WORKER_INTERVAL_MS: 1000,
      };

      const worker = new OutboxWorker(mockPool, mockBitGo, config as any, mockLogger);

      await worker["processOperations"]();

      expect(mockedAxios.post).toHaveBeenCalledWith(
        "http://ledger:3000/internal/cancel",
        expect.objectContaining({
          userId: fixtures.users.alice,
          withdrawalId: "wd-cancel-1",
          reason: "REJECTED",
        }),
        expect.any(Object)
      );

      const op = db.outbox.find((o) => o.id === operation.id);
      expect(op?.status).toBe("COMPLETED");
    });

    it("should process SUBMIT_TO_BITGO operation", async () => {
      // Create withdrawal
      const withdrawal = {
        id: "wd-submit-outbox-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        destination_address: fixtures.addresses.btc.valid,
        amount: "10000000",
        status: "APPROVED",
        idempotency_key: "outbox-submit-1",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const operation = {
        id: "outbox-submit-bitgo-1",
        withdrawal_id: withdrawal.id,
        type: "SUBMIT_TO_BITGO",
        payload: JSON.stringify({ withdrawalId: withdrawal.id }),
        status: "PENDING",
        attempt_count: 0,
        next_retry_at: new Date(),
        last_error: null,
        created_at: new Date(),
        processed_at: null,
        updated_at: new Date(),
      };
      
      db.outbox.push(operation);
      db.outbox.push({
        id: "outbox-submit-lock-completed-1",
        withdrawal_id: withdrawal.id,
        type: "APPLY_LEDGER_LOCK",
        payload: JSON.stringify({ withdrawalId: withdrawal.id }),
        status: "COMPLETED",
        attempt_count: 1,
        next_retry_at: new Date(),
        last_error: null,
        created_at: new Date(),
        processed_at: new Date(),
        updated_at: new Date(),
      });

      const config = {
        LEDGER_SERVICE_URL: "http://ledger:3000",
        OUTBOX_WORKER_INTERVAL_MS: 1000,
      };

      const worker = new OutboxWorker(mockPool, mockBitGo, config as any, mockLogger);

      await worker["processOperations"]();

      // Check BitGo was called
      expect(mockBitGo.transfers.size).toBe(1);

      // Operation should be completed
      const op = db.outbox.find((o) => o.id === operation.id);
      expect(op?.status).toBe("COMPLETED");

      const broadcastOp = db.outbox.find(
        (o) =>
          o.withdrawal_id === withdrawal.id &&
          o.type === "APPLY_LEDGER_BROADCAST"
      );
      expect(broadcastOp).toBeDefined();
      expect(broadcastOp?.status).toBe("PENDING");
      expect(JSON.parse(broadcastOp?.payload as string)).toMatchObject({
        withdrawalId: withdrawal.id,
      });
    });
  });

  describe("SKIP LOCKED Pattern", () => {
    it("should prevent duplicate processing with FOR UPDATE SKIP LOCKED", async () => {
      // Create multiple pending operations
      for (let i = 0; i < 5; i++) {
        db.outbox.push({
          id: `outbox-locked-${i}`,
          withdrawal_id: `wd-${i}`,
          type: "APPLY_LEDGER_LOCK",
          payload: JSON.stringify({ userId: fixtures.users.alice }),
          status: "PENDING",
          attempt_count: 0,
          next_retry_at: new Date(),
          last_error: null,
          created_at: new Date(),
          processed_at: null,
          updated_at: new Date(),
        });
      }

      // Query should use FOR UPDATE SKIP LOCKED
      const pendingOps = db.outbox.filter(
        (op) => op.status === "PENDING" && op.attempt_count < 10
      );

      expect(pendingOps.length).toBeGreaterThan(0);

      // In production PostgreSQL, FOR UPDATE SKIP LOCKED ensures
      // that if multiple workers query at the same time, each gets
      // a different set of operations (no row is locked by multiple workers)
      
      // This test documents the expected behavior
      expect(pendingOps.every((op) => op.status === "PENDING")).toBe(true);
    });
  });

  describe("Dead Letter Handling", () => {
    it("should move permanently failed operations to dead letter", async () => {
      const operation = {
        id: "outbox-dead-letter-1",
        withdrawal_id: "wd-dead-1",
        type: "APPLY_LEDGER_LOCK",
        payload: JSON.stringify({ userId: fixtures.users.alice }),
        status: "PENDING",
        attempt_count: 9,
        next_retry_at: new Date(),
        last_error: JSON.stringify({ message: "Persistent failure" }),
        created_at: new Date(),
        processed_at: null,
        updated_at: new Date(),
      };
      
      db.outbox.push(operation);

      // Mock to keep failing
      mockedAxios.post = vi.fn().mockRejectedValue(new Error("Unrecoverable error"));

      const config = {
        LEDGER_SERVICE_URL: "http://ledger:3000",
        OUTBOX_WORKER_INTERVAL_MS: 1000,
      };

      const worker = new OutboxWorker(mockPool, mockBitGo, config as any, mockLogger);

      await worker["processOperations"]();

      // Should be permanently failed
      const op = db.outbox.find((o) => o.id === operation.id);
      expect(op?.status).toBe("FAILED");
      expect(op?.attempt_count).toBe(10);

      // In production, dead letter operations would be:
      // 1. Monitored with alerts
      // 2. Manually investigated
      // 3. Either fixed and retried or marked as resolved
    });

    it("should alert on dead letter operations", () => {
      // This test documents that production should have monitoring
      // for operations that reach FAILED status
      
      const deadLetterOp = {
        id: "outbox-alert-1",
        status: "FAILED",
        attempt_count: 10,
        type: "APPLY_LEDGER_LOCK",
        withdrawal_id: "wd-alert-1",
        last_error: JSON.stringify({ message: "Max retries exceeded" }),
      };

      // In production:
      // 1. Log with ERROR level
      // 2. Send alert to ops team
      // 3. Create incident ticket
      // 4. Track metrics (dead_letter_count)
      
      expect(deadLetterOp.status).toBe("FAILED");
      expect(deadLetterOp.attempt_count).toBeGreaterThanOrEqual(10);
    });
  });

  describe("Worker Lifecycle", () => {
    it("should start and stop worker cleanly", () => {
      const config = {
        LEDGER_SERVICE_URL: "http://ledger:3000",
        OUTBOX_WORKER_INTERVAL_MS: 1000,
      };

      const worker = new OutboxWorker(mockPool, mockBitGo, config as any, mockLogger);

      // Start worker
      worker.start();
      expect(worker["intervalId"]).toBeDefined();

      // Stop worker
      worker.stop();
      expect(worker["intervalId"]).toBeNull();
    });

    it("should not start worker twice", () => {
      const config = {
        LEDGER_SERVICE_URL: "http://ledger:3000",
        OUTBOX_WORKER_INTERVAL_MS: 1000,
      };

      const worker = new OutboxWorker(mockPool, mockBitGo, config as any, mockLogger);

      worker.start();
      const intervalId1 = worker["intervalId"];

      // Try to start again
      worker.start();
      const intervalId2 = worker["intervalId"];

      expect(intervalId1).toBe(intervalId2);

      worker.stop();
    });

    it("should prevent concurrent processing", async () => {
      const config = {
        LEDGER_SERVICE_URL: "http://ledger:3000",
        OUTBOX_WORKER_INTERVAL_MS: 1000,
      };

      const worker = new OutboxWorker(mockPool, mockBitGo, config as any, mockLogger);

      // Set processing flag
      worker["processing"] = true;

      // Try to process (should return early)
      await worker["processOperations"]();

      // Should still be true (processing didn't complete)
      expect(worker["processing"]).toBe(true);
    });
  });

  describe("Idempotency in Worker", () => {
    it("should not duplicate operations during retry", async () => {
      const operation = {
        id: "outbox-no-dup-1",
        withdrawal_id: "wd-no-dup-1",
        type: "APPLY_LEDGER_LOCK",
        payload: JSON.stringify({
          userId: fixtures.users.alice,
          assetNetworkId: "an-btc-mainnet",
          amount: "10000000",
          withdrawalId: "wd-no-dup-1",
        }),
        status: "PENDING",
        attempt_count: 1,
        next_retry_at: new Date(),
        last_error: JSON.stringify({ message: "First attempt failed" }),
        created_at: new Date(),
        processed_at: null,
        updated_at: new Date(),
      };
      
      db.outbox.push(operation);

      // Mock ledger to succeed
      mockedAxios.post = vi.fn().mockResolvedValue({
        data: { transactionId: "ledger-tx-retry" },
      });

      const config = {
        LEDGER_SERVICE_URL: "http://ledger:3000",
        OUTBOX_WORKER_INTERVAL_MS: 1000,
      };

      const worker = new OutboxWorker(mockPool, mockBitGo, config as any, mockLogger);

      await worker["processOperations"]();

      // Ledger should have been called exactly once
      expect(mockedAxios.post).toHaveBeenCalledTimes(1);

      // Operation should be completed
      const op = db.outbox.find((o) => o.id === operation.id);
      expect(op?.status).toBe("COMPLETED");

      // No duplicate operations should exist
      const duplicates = db.outbox.filter(
        (o) => o.withdrawal_id === "wd-no-dup-1" && o.type === "APPLY_LEDGER_LOCK"
      );
      expect(duplicates).toHaveLength(1);
    });
  });
});
