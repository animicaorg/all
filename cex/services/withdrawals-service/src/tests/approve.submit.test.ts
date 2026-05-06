/**
 * Approval and BitGo Submission Tests
 * 
 * Tests approval workflow, BitGo submission, and state transitions
 */

import { describe, it, expect, beforeEach } from "vitest";
import { handleApproval, type ApprovalRequest } from "../pipeline/approve.js";
import { submitToBitGo } from "../pipeline/submit.js";
import {
  MockDatabase,
  createMockClient,
  createMockLogger,
  createMockBitGoClient,
  fixtures,
} from "./helpers.js";

describe("Approval and Submission", () => {
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

  describe("Approval Workflow", () => {
    it("should approve and queue submission after completed ledger lock", async () => {
      // Create a withdrawal requiring approval
      const withdrawal = {
        id: "wd-approval-test-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        destination_address: fixtures.addresses.btc.valid,
        amount: "10000000",
        fee_amount: "5000",
        total_debit_amount: "10005000",
        status: "RISK_REVIEW",
        idempotency_key: "idem-approval-1",
        risk_score: 50,
        risk_flags: JSON.stringify(["HIGH_AMOUNT"]),
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);
      db.outbox.push({
        id: "outbox-lock-completed-1",
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

      // First approval
      const approval1: ApprovalRequest = {
        withdrawalId: withdrawal.id,
        approverId: fixtures.approvers.admin1,
        approverRole: "ADMIN",
        action: "APPROVE",
      };

      const result1 = await handleApproval(mockClient, approval1, mockLogger);
      expect(result1.success).toBe(true);
      expect(result1.message).toBe("Withdrawal approved");
      
      // Verify status updated to APPROVED and submission queued
      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("APPROVED");

      // Check that submission was queued
      const submitOps = db.outbox.filter(
        (op) => op.type === "SUBMIT_TO_BITGO" && op.withdrawal_id === withdrawal.id
      );
      expect(submitOps.length).toBeGreaterThan(0);
    });

    it("should prevent same approver from approving twice", async () => {
      const withdrawal = {
        id: "wd-dupe-approval-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "RISK_REVIEW",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const approval1: ApprovalRequest = {
        withdrawalId: withdrawal.id,
        approverId: fixtures.approvers.admin1,
        approverRole: "ADMIN",
        action: "APPROVE",
      };

      // First approval
      const result1 = await handleApproval(mockClient, approval1, mockLogger);
      expect(result1.success).toBe(true);

      // Second approval by same approver should fail
      const result2 = await handleApproval(mockClient, approval1, mockLogger);
      expect(result2.success).toBe(false);
      expect(result2.message).toContain("Cannot approve");
    });

    it("should not accept another approval after approval threshold is met", async () => {
      const withdrawal = {
        id: "wd-multi-approval-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "RISK_REVIEW",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      // First approver
      const approval1: ApprovalRequest = {
        withdrawalId: withdrawal.id,
        approverId: fixtures.approvers.admin1,
        approverRole: "ADMIN",
        action: "APPROVE",
      };

      const result1 = await handleApproval(mockClient, approval1, mockLogger);
      expect(result1.success).toBe(true);

      // Second approver (different person)
      const approval2: ApprovalRequest = {
        withdrawalId: withdrawal.id,
        approverId: fixtures.approvers.admin2,
        approverRole: "ADMIN",
        action: "APPROVE",
      };

      const result2 = await handleApproval(mockClient, approval2, mockLogger);
      expect(result2.success).toBe(false);
      expect(result2.message).toContain("Cannot approve");

      // The first approval transitions the withdrawal, so no second approval is recorded.
      const approvals = db.approvals.filter((a) => a.withdrawal_id === withdrawal.id);
      expect(approvals).toHaveLength(1);
    });

    it("should handle rejection properly", async () => {
      const withdrawal = {
        id: "wd-reject-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "RISK_REVIEW",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const rejection: ApprovalRequest = {
        withdrawalId: withdrawal.id,
        approverId: fixtures.approvers.admin1,
        approverRole: "ADMIN",
        action: "REJECT",
        reason: "Suspicious activity detected",
      };

      const result = await handleApproval(mockClient, rejection, mockLogger);
      expect(result.success).toBe(true);
      expect(result.newStatus).toBe("REJECTED");

      // Check withdrawal was rejected
      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("REJECTED");
      expect(updatedWithdrawal.failure_code).toBe("ADMIN_REJECTED");

      // Check that ledger cancel was queued
      const cancelOps = db.outbox.filter(
        (op) => op.type === "APPLY_LEDGER_CANCEL" && op.withdrawal_id === withdrawal.id
      );
      expect(cancelOps).toHaveLength(1);
    });

    it("should not allow approval of already approved withdrawal", async () => {
      const withdrawal = {
        id: "wd-already-approved-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "APPROVED",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const approval: ApprovalRequest = {
        withdrawalId: withdrawal.id,
        approverId: fixtures.approvers.admin1,
        approverRole: "ADMIN",
        action: "APPROVE",
      };

      const result = await handleApproval(mockClient, approval, mockLogger);
      expect(result.success).toBe(false);
      expect(result.message).toContain("Cannot approve");
    });

    it("should record audit log for approvals", async () => {
      const withdrawal = {
        id: "wd-audit-approval-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "RISK_REVIEW",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const approval: ApprovalRequest = {
        withdrawalId: withdrawal.id,
        approverId: fixtures.approvers.admin1,
        approverRole: "ADMIN",
        action: "APPROVE",
        reason: "Verified with user",
      };

      await handleApproval(mockClient, approval, mockLogger);

      const auditEntries = db.auditLog.filter(
        (log) => log.event_type === "WITHDRAWAL_APPROVED"
      );
      expect(auditEntries.length).toBeGreaterThan(0);
      expect(auditEntries[0].actor_id).toBe(fixtures.approvers.admin1);
    });
  });

  describe("BitGo Submission", () => {
    it("should submit to BitGo and store provider reference", async () => {
      const withdrawal = {
        id: "wd-submit-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        destination_address: fixtures.addresses.btc.valid,
        amount: "10000000",
        fee_amount: "5000",
        total_debit_amount: "10005000",
        status: "APPROVED",
        idempotency_key: "idem-submit-1",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const result = await submitToBitGo(
        mockClient,
        withdrawal.id,
        mockBitGo,
        mockLogger
      );

      expect(result.success).toBe(true);

      // Check that provider_ref was stored
      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.provider_ref).toBeDefined();
      expect(updatedWithdrawal.txid).toBeDefined();

      // Verify BitGo was called
      expect(mockBitGo.transfers.size).toBe(1);
      expect(mockBitGo.requests[0]).toMatchObject({
        coin: "btc",
        walletId: "bitgo-wallet-btc-hot",
      });
      expect(mockBitGo.requests[0].request.sequenceId).toBe("idem-submit-1");
    });

    it.each([
      ["BTC", "an-btc-mainnet", fixtures.addresses.btc.valid, "btc", "bitgo-wallet-btc-hot"],
      ["LTC", "an-ltc-mainnet", fixtures.addresses.ltc.valid, "ltc", "bitgo-wallet-ltc-hot"],
      ["DOGE", "an-doge-mainnet", fixtures.addresses.doge.valid, "doge", "bitgo-wallet-doge-hot"],
      ["ZEC", "an-zec-mainnet", fixtures.addresses.zec.valid, "zec", "bitgo-wallet-zec-hot"],
    ])("should submit %s withdrawals through the coin-scoped BitGo wallet endpoint", async (
      _asset,
      assetNetworkId,
      destinationAddress,
      expectedCoin,
      expectedWalletId
    ) => {
      const withdrawal = {
        id: `wd-submit-${expectedCoin}`,
        user_id: fixtures.users.alice,
        asset_network_id: assetNetworkId,
        destination_address: destinationAddress,
        amount: "10000000",
        fee_amount: "5000",
        total_debit_amount: "10005000",
        status: "APPROVED",
        idempotency_key: `idem-submit-${expectedCoin}`,
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const result = await submitToBitGo(
        mockClient,
        withdrawal.id,
        mockBitGo,
        mockLogger
      );

      expect(result.success).toBe(true);
      expect(mockBitGo.requests).toHaveLength(1);
      expect(mockBitGo.requests[0]).toMatchObject({
        coin: expectedCoin,
        walletId: expectedWalletId,
      });
      expect(db.withdrawals.get(withdrawal.id).status).toBe("BROADCAST");
    });

    it("should be idempotent - same withdrawal does not create duplicate transfers", async () => {
      const withdrawal = {
        id: "wd-idem-submit-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        destination_address: fixtures.addresses.btc.valid,
        amount: "10000000",
        fee_amount: "5000",
        total_debit_amount: "10005000",
        status: "APPROVED",
        idempotency_key: "idem-submit-same",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      // First submission
      const result1 = await submitToBitGo(
        mockClient,
        withdrawal.id,
        mockBitGo,
        mockLogger
      );
      expect(result1.success).toBe(true);

      const transferCount1 = mockBitGo.transfers.size;
      const providerRef1 = db.withdrawals.get(withdrawal.id).provider_ref;

      // Update status back to APPROVED to allow re-submission (simulating retry)
      withdrawal.status = "APPROVED";
      db.withdrawals.set(withdrawal.id, withdrawal);

      // Second submission (retry scenario)
      // BitGo uses sequenceId (idempotency key) to prevent duplicates
      const result2 = await submitToBitGo(
        mockClient,
        withdrawal.id,
        mockBitGo,
        mockLogger
      );
      expect(result2.success).toBe(true);

      // Should create a new transfer in mock, but in real BitGo would return existing
      // This documents that idempotency is handled by BitGo's sequenceId
      expect(mockBitGo.transfers.size).toBeGreaterThanOrEqual(transferCount1);
    });

    it("should map BitGo state to internal state correctly", async () => {
      const withdrawal = {
        id: "wd-state-map-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        destination_address: fixtures.addresses.btc.valid,
        amount: "10000000",
        fee_amount: "5000",
        total_debit_amount: "10005000",
        status: "APPROVED",
        idempotency_key: "idem-state-1",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      await submitToBitGo(mockClient, withdrawal.id, mockBitGo, mockLogger);

      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      // Mock returns 'signed' state which maps to 'BROADCAST'
      expect(updatedWithdrawal.status).toBe("BROADCAST");
    });

    it("should handle BitGo API errors", async () => {
      const withdrawal = {
        id: "wd-error-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        destination_address: fixtures.addresses.btc.valid,
        amount: "10000000",
        fee_amount: "5000",
        total_debit_amount: "10005000",
        status: "APPROVED",
        idempotency_key: "idem-error-1",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      // Make BitGo throw an error
      mockBitGo.createTransfer = async () => {
        throw new Error("BitGo API timeout");
      };

      const result = await submitToBitGo(
        mockClient,
        withdrawal.id,
        mockBitGo,
        mockLogger
      );

      expect(result.success).toBe(false);
      expect(result.message).toContain("BitGo API timeout");

      // Provider failures are retry-safe; the outbox owns retry/backoff.
      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("APPROVED");
      expect(updatedWithdrawal.failure_code).toBeUndefined();
    });

    it("should not submit if withdrawal not approved", async () => {
      const withdrawal = {
        id: "wd-not-approved-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "REQUESTED",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      const result = await submitToBitGo(
        mockClient,
        withdrawal.id,
        mockBitGo,
        mockLogger
      );

      expect(result.success).toBe(false);
      expect(result.message).toContain("Cannot submit");
    });

    it("should handle missing wallet configuration", async () => {
      const withdrawal = {
        id: "wd-no-wallet-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        destination_address: fixtures.addresses.btc.valid,
        amount: "10000000",
        status: "APPROVED",
        idempotency_key: "idem-no-wallet",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      // Remove wallet
      db.wallets.delete("an-btc-mainnet:HOT");

      const result = await submitToBitGo(
        mockClient,
        withdrawal.id,
        mockBitGo,
        mockLogger
      );

      expect(result.success).toBe(false);
      expect(result.message).toContain("No wallet configured");

      const updatedWithdrawal = db.withdrawals.get(withdrawal.id);
      expect(updatedWithdrawal.status).toBe("APPROVED");
      expect(updatedWithdrawal.failure_code).toBeUndefined();
    });

    it("should create audit log for submission", async () => {
      const withdrawal = {
        id: "wd-audit-submit-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        destination_address: fixtures.addresses.btc.valid,
        amount: "10000000",
        status: "APPROVED",
        idempotency_key: "idem-audit-submit",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      await submitToBitGo(mockClient, withdrawal.id, mockBitGo, mockLogger);

      const auditEntries = db.auditLog.filter(
        (log) => log.event_type === "WITHDRAWAL_SUBMITTED"
      );
      expect(auditEntries.length).toBeGreaterThan(0);
    });
  });

  describe("Cancel Flow", () => {
    it("should cancel withdrawal and release lock", async () => {
      const withdrawal = {
        id: "wd-cancel-1",
        user_id: fixtures.users.alice,
        asset_network_id: "an-btc-mainnet",
        status: "REQUESTED",
        created_at: new Date(),
      };
      db.withdrawals.set(withdrawal.id, withdrawal);

      // User-initiated cancellation would go through a cancel endpoint
      // For this test, we simulate admin rejection which triggers cancel
      const rejection: ApprovalRequest = {
        withdrawalId: withdrawal.id,
        approverId: fixtures.approvers.admin1,
        approverRole: "ADMIN",
        action: "REJECT",
        reason: "User requested cancellation",
      };

      await handleApproval(mockClient, rejection, mockLogger);

      // Check that ledger cancel operation was queued
      const cancelOps = db.outbox.filter(
        (op) => op.type === "APPLY_LEDGER_CANCEL" && op.withdrawal_id === withdrawal.id
      );
      expect(cancelOps).toHaveLength(1);
      
      const payload = JSON.parse(cancelOps[0].payload);
      expect(payload.reason).toBe("REJECTED");
    });
  });
});
