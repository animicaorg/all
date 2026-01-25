/**
 * Webhook Processing and Status Tracking Tests
 * 
 * Tests webhook processing, state transitions, and ledger operations
 */

import { describe, it, expect, beforeEach } from "vitest";
import { processWebhook } from "../pipeline/tracker.js";
import type { WithdrawalObservation } from "../bitgo/types.js";
import {
  MockDatabase,
  createMockClient,
  createMockLogger,
  fixtures,
} from "./helpers.js";

describe("Webhook Processing and Status Tracking", () => {
  let db: MockDatabase;
  let mockClient: any;
  let mockLogger: any;

  beforeEach(() => {
    db = new MockDatabase();
    db.setupTestData();
    mockClient = createMockClient(db);
    mockLogger = createMockLogger();
  });

  describe("Webhook Processing", () => {
    it("should process BROADCAST webhook and trigger ledger operation exactly once", async () => {
      const withdrawal = {
        id: "wd-webhook-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        destination_address: fixtures.addresses.btc.valid,
        amount: "10000000",
        status: "SIGNING",
        provider_ref: "bitgo-transfer-123",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const observation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "bitgo-transfer-123",
        walletId: "bitgo-wallet-btc-hot",
        txid: "0xabc123",
        state: "BROADCAST",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: {},
      };

      const result = await processWebhook(mockClient, observation, mockLogger);
      expect(result.success).toBe(true);

      // Check withdrawal updated to BROADCAST
      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("BROADCAST");
      expect(updatedWithdrawal.txid).toBe("0xabc123");

      // Check ledger broadcast operation queued exactly once
      const broadcastOps = db.outbox.filter(
        (op) => op.type === "APPLY_LEDGER_BROADCAST" && op.withdrawal_id === withdrawal.id
      );
      expect(broadcastOps).toHaveLength(1);
      
      const payload = JSON.parse(broadcastOps[0].payload);
      expect(payload.txid).toBe("0xabc123");
    });

    it("should process FAILED webhook pre-broadcast and release lock", async () => {
      const withdrawal = {
        id: "wd-fail-pre-broadcast",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "SIGNING",
        provider_ref: "bitgo-transfer-456",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const observation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "bitgo-transfer-456",
        walletId: "bitgo-wallet-btc-hot",
        txid: null,
        state: "FAILED",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: {},
      };

      const result = await processWebhook(mockClient, observation, mockLogger);
      expect(result.success).toBe(true);

      // Check withdrawal marked as FAILED
      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("FAILED");
      expect(updatedWithdrawal.failure_code).toBe("BITGO_FAILED");

      // Check ledger cancel operation queued
      const cancelOps = db.outbox.filter(
        (op) => op.type === "APPLY_LEDGER_CANCEL" && op.withdrawal_id === withdrawal.id
      );
      expect(cancelOps).toHaveLength(1);
      
      const payload = JSON.parse(cancelOps[0].payload);
      expect(payload.reason).toBe("FAILED");
    });

    it("should process CONFIRMED webhook", async () => {
      const withdrawal = {
        id: "wd-confirm-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "BROADCAST",
        provider_ref: "bitgo-transfer-789",
        txid: "0xdef456",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const observation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "bitgo-transfer-789",
        walletId: "bitgo-wallet-btc-hot",
        txid: "0xdef456",
        state: "CONFIRMED",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: {},
      };

      const result = await processWebhook(mockClient, observation, mockLogger);
      expect(result.success).toBe(true);

      // Check withdrawal updated to CONFIRMED
      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("CONFIRMED");

      // CONFIRMED doesn't trigger additional ledger operations
      // (broadcast already moved the funds)
    });

    it("should process SIGNING webhook", async () => {
      const withdrawal = {
        id: "wd-signing-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "APPROVED",
        provider_ref: "bitgo-transfer-sign",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const observation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "bitgo-transfer-sign",
        walletId: "bitgo-wallet-btc-hot",
        txid: null,
        state: "SIGNING",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: {},
      };

      const result = await processWebhook(mockClient, observation, mockLogger);
      expect(result.success).toBe(true);

      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("SIGNING");
    });

    it("should handle webhook for unknown withdrawal", async () => {
      const observation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "unknown-transfer",
        walletId: "bitgo-wallet-btc-hot",
        txid: null,
        state: "BROADCAST",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: {},
      };

      const result = await processWebhook(mockClient, observation, mockLogger);
      expect(result.success).toBe(false);
      expect(result.message).toContain("not found");
    });

    it("should create audit log for state updates", async () => {
      const withdrawal = {
        id: "wd-audit-webhook-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "SIGNING",
        provider_ref: "bitgo-transfer-audit",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const observation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "bitgo-transfer-audit",
        walletId: "bitgo-wallet-btc-hot",
        txid: "0xaudit123",
        state: "BROADCAST",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: { bitgoField: "value" },
      };

      await processWebhook(mockClient, observation, mockLogger);

      const auditEntries = db.auditLog.filter(
        (log) => log.event_type === "WITHDRAWAL_STATE_UPDATED"
      );
      expect(auditEntries.length).toBeGreaterThan(0);
      expect(auditEntries[0].actor_type).toBe("SYSTEM");
    });
  });

  describe("State Transitions", () => {
    it("should transition APPROVED -> SIGNING", async () => {
      const withdrawal = {
        id: "wd-transition-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "APPROVED",
        provider_ref: "bitgo-transfer-t1",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const observation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "bitgo-transfer-t1",
        walletId: "bitgo-wallet-btc-hot",
        txid: null,
        state: "SIGNING",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: {},
      };

      await processWebhook(mockClient, observation, mockLogger);

      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("SIGNING");
    });

    it("should transition SIGNING -> BROADCAST", async () => {
      const withdrawal = {
        id: "wd-transition-2",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "SIGNING",
        provider_ref: "bitgo-transfer-t2",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const observation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "bitgo-transfer-t2",
        walletId: "bitgo-wallet-btc-hot",
        txid: "0xtrans2",
        state: "BROADCAST",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: {},
      };

      await processWebhook(mockClient, observation, mockLogger);

      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("BROADCAST");
    });

    it("should transition BROADCAST -> CONFIRMED", async () => {
      const withdrawal = {
        id: "wd-transition-3",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "BROADCAST",
        provider_ref: "bitgo-transfer-t3",
        txid: "0xtrans3",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const observation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "bitgo-transfer-t3",
        walletId: "bitgo-wallet-btc-hot",
        txid: "0xtrans3",
        state: "CONFIRMED",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: {},
      };

      await processWebhook(mockClient, observation, mockLogger);

      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("CONFIRMED");
    });

    it("should not regress state from CONFIRMED", async () => {
      const withdrawal = {
        id: "wd-no-regress-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "CONFIRMED",
        provider_ref: "bitgo-transfer-confirmed",
        txid: "0xconfirmed",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      // Late webhook for BROADCAST state
      const observation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "bitgo-transfer-confirmed",
        walletId: "bitgo-wallet-btc-hot",
        txid: "0xconfirmed",
        state: "BROADCAST",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: {},
      };

      await processWebhook(mockClient, observation, mockLogger);

      // Status should remain CONFIRMED
      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("CONFIRMED");
    });

    it("should not transition if already FAILED", async () => {
      const withdrawal = {
        id: "wd-already-failed",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "FAILED",
        provider_ref: "bitgo-transfer-failed",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const observation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "bitgo-transfer-failed",
        walletId: "bitgo-wallet-btc-hot",
        txid: null,
        state: "FAILED",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: {},
      };

      await processWebhook(mockClient, observation, mockLogger);

      // No new cancel operation should be created
      const cancelOps = db.outbox.filter(
        (op) => op.type === "APPLY_LEDGER_CANCEL" && op.withdrawal_id === withdrawal.id
      );
      expect(cancelOps).toHaveLength(0);
    });
  });

  describe("Idempotency", () => {
    it("should handle duplicate webhook deliveries idempotently", async () => {
      const withdrawal = {
        id: "wd-idem-webhook-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "SIGNING",
        provider_ref: "bitgo-transfer-idem",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const observation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "bitgo-transfer-idem",
        walletId: "bitgo-wallet-btc-hot",
        txid: "0xidem123",
        state: "BROADCAST",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: {},
      };

      // First webhook
      const result1 = await processWebhook(mockClient, observation, mockLogger);
      expect(result1.success).toBe(true);

      const broadcastOpsCount1 = db.outbox.filter(
        (op) => op.type === "APPLY_LEDGER_BROADCAST" && op.withdrawal_id === withdrawal.id
      ).length;

      // Update withdrawal to reflect the change (simulate DB state)
      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      updatedWithdrawal.status = "BROADCAST";

      // Duplicate webhook
      const result2 = await processWebhook(mockClient, observation, mockLogger);
      expect(result2.success).toBe(true);

      // Should not create duplicate ledger operation
      const broadcastOpsCount2 = db.outbox.filter(
        (op) => op.type === "APPLY_LEDGER_BROADCAST" && op.withdrawal_id === withdrawal.id
      ).length;
      
      expect(broadcastOpsCount2).toBe(broadcastOpsCount1); // No new operations
    });

    it("should handle out-of-order webhooks correctly", async () => {
      const withdrawal = {
        id: "wd-out-of-order-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "APPROVED",
        provider_ref: "bitgo-transfer-ooo",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      // BROADCAST arrives first
      const broadcastObservation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "bitgo-transfer-ooo",
        walletId: "bitgo-wallet-btc-hot",
        txid: "0xooo123",
        state: "BROADCAST",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: {},
      };

      await processWebhook(mockClient, broadcastObservation, mockLogger);

      let updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("BROADCAST");
      updatedWithdrawal.status = "BROADCAST"; // Update mock state

      // SIGNING arrives late (should not regress state)
      const signingObservation: WithdrawalObservation = {
        provider: "BITGO",
        providerRef: "bitgo-transfer-ooo",
        walletId: "bitgo-wallet-btc-hot",
        txid: null,
        state: "SIGNING",
        amountAtoms: 10000000n,
        observedAt: new Date(),
        raw: {},
      };

      await processWebhook(mockClient, signingObservation, mockLogger);

      updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("BROADCAST"); // Should remain BROADCAST
    });
  });

  describe("Signature Verification", () => {
    it("should verify webhook signatures (placeholder test)", () => {
      // Note: Actual signature verification would be implemented in the webhook handler
      // This test documents that signature verification should be performed
      
      // In production:
      // 1. Extract signature header from webhook request
      // 2. Compute HMAC of request body with shared secret
      // 3. Compare computed signature with received signature
      // 4. Reject webhook if signatures don't match
      
      expect(true).toBe(true); // Placeholder
    });
  });
});
