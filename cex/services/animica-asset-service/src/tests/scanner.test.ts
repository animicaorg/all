/**
 * Deposit Scanner Tests
 * 
 * Tests:
 * - Basic deposit detection
 * - Confirmation tracking
 * - Idempotency
 * - Reorg handling
 */

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { Pool } from "pg";
import { createLogger } from "@cex/common";
import { MockAnimicaRpc, createMockRpcClient } from "./mock_rpc.js";
import { BlockScanner } from "../deposits/scanner.js";
import { ScanStateRepository } from "../db/repositories/scan_state_repo.js";
import { DepositsRepository } from "../db/repositories/deposits_repo.js";
import { AddressesRepository } from "../db/repositories/addresses_repo.js";

describe("Deposit Scanner", () => {
  let pool: Pool;
  let mockRpc: MockAnimicaRpc;
  let scanner: BlockScanner;
  let scanStateRepo: ScanStateRepository;
  let depositsRepo: DepositsRepository;
  let addressesRepo: AddressesRepository;
  const logger = createLogger("test", "error");
  
  const ASSET_NETWORK_ID = "test-asset-network-id";
  const WALLET_ID = "test-wallet-id";
  const USER_ID = "test-user-id";
  const DEPOSIT_ADDRESS = "anim1testaddress";
  
  beforeEach(async () => {
    // Setup test database (would use test DB in real scenario)
    pool = new Pool({
      host: process.env.TEST_DB_HOST || "localhost",
      port: Number(process.env.TEST_DB_PORT) || 5432,
      user: process.env.TEST_DB_USER || "test",
      password: process.env.TEST_DB_PASSWORD || "test",
      database: process.env.TEST_DB_NAME || "cex_test",
    });
    
    // Setup mock RPC
    mockRpc = new MockAnimicaRpc();
    const rpcClient = createMockRpcClient(mockRpc);
    
    // Setup repositories
    scanStateRepo = new ScanStateRepository(pool, logger);
    depositsRepo = new DepositsRepository(pool, logger);
    addressesRepo = new AddressesRepository(pool, logger);
    
    // Initialize scan state
    await scanStateRepo.initialize(ASSET_NETWORK_ID, 0);
    
    // Create deposit address
    await addressesRepo.getOrCreate(
      USER_ID,
      ASSET_NETWORK_ID,
      WALLET_ID,
      DEPOSIT_ADDRESS
    );
    
    // Setup scanner
    scanner = new BlockScanner(
      pool,
      rpcClient,
      {
        assetNetworkId: ASSET_NETWORK_ID,
        confirmationsRequired: 3,
        scanBatch: 10,
        maxReorgDepth: 100,
        walletId: WALLET_ID,
      },
      logger
    );
  });
  
  afterEach(async () => {
    await pool.end();
  });
  
  it("should detect deposits in new blocks", async () => {
    // Add transaction to mock
    const txid = "0xdeposit1";
    mockRpc.addTransaction({
      txid,
      from: "0xsender",
      to: DEPOSIT_ADDRESS,
      value: "1000000000000000000", // 1 ANM
      nonce: 0,
      gas_limit: 21000,
      gas_price: "1000000000",
    });
    
    // Mine block with transaction
    mockRpc.mineBlock([txid]);
    mockRpc.mineBlock(); // +1 confirmation
    mockRpc.mineBlock(); // +2 confirmations
    
    // Scan
    await scanner.scan();
    
    // Verify deposit detected
    const deposits = await depositsRepo.getByStatus(ASSET_NETWORK_ID, "DETECTED");
    expect(deposits).toHaveLength(1);
    expect(deposits[0].txid).toBe(txid);
    expect(deposits[0].address).toBe(DEPOSIT_ADDRESS);
    expect(deposits[0].amount_atoms).toBe("1000000000000000000");
    expect(deposits[0].user_id).toBe(USER_ID);
  });
  
  it("should track confirmations and mark as confirmed", async () => {
    // Add and mine transaction
    const txid = "0xdeposit2";
    mockRpc.addTransaction({
      txid,
      from: "0xsender",
      to: DEPOSIT_ADDRESS,
      value: "2000000000000000000",
      nonce: 0,
      gas_limit: 21000,
      gas_price: "1000000000",
    });
    
    mockRpc.mineBlock([txid]); // Block 1
    
    // Scan - should detect
    await scanner.scan();
    
    let deposits = await depositsRepo.getByStatus(ASSET_NETWORK_ID, "DETECTED");
    expect(deposits).toHaveLength(1);
    
    // Mine 2 more blocks (total 3 confirmations)
    mockRpc.mineBlock(); // Block 2
    mockRpc.mineBlock(); // Block 3
    
    // Scan again - should confirm
    await scanner.scan();
    
    deposits = await depositsRepo.getByStatus(ASSET_NETWORK_ID, "CONFIRMED");
    expect(deposits).toHaveLength(1);
    expect(deposits[0].confirmations).toBeGreaterThanOrEqual(3);
  });
  
  it("should be idempotent - no duplicate deposits on rescan", async () => {
    // Add and mine transaction
    const txid = "0xdeposit3";
    mockRpc.addTransaction({
      txid,
      from: "0xsender",
      to: DEPOSIT_ADDRESS,
      value: "3000000000000000000",
      nonce: 0,
      gas_limit: 21000,
      gas_price: "1000000000",
    });
    
    mockRpc.mineBlock([txid]);
    
    // Scan twice
    await scanner.scan();
    await scanner.scan();
    
    // Should only have one deposit
    const deposits = await depositsRepo.getByStatus(ASSET_NETWORK_ID, "DETECTED");
    expect(deposits).toHaveLength(1);
  });
  
  it("should handle reorg and mark deposits as reorged", async () => {
    // Add transaction and mine
    const txid = "0xdeposit4";
    mockRpc.addTransaction({
      txid,
      from: "0xsender",
      to: DEPOSIT_ADDRESS,
      value: "4000000000000000000",
      nonce: 0,
      gas_limit: 21000,
      gas_price: "1000000000",
    });
    
    mockRpc.mineBlock([txid]); // Block 1
    mockRpc.mineBlock(); // Block 2
    mockRpc.mineBlock(); // Block 3
    
    // Scan
    await scanner.scan();
    
    // Verify deposit confirmed
    let deposits = await depositsRepo.getByStatus(ASSET_NETWORK_ID, "CONFIRMED");
    expect(deposits).toHaveLength(1);
    
    // Simulate reorg - replace blocks 1-3
    mockRpc.simulateReorg(1, [
      { hash: "0x0001" },
      { hash: "0x0002" },
      { hash: "0x0003" },
    ]);
    
    // Scan again - should detect reorg
    await scanner.scan();
    
    // Deposit should be marked as reorged
    deposits = await depositsRepo.getByStatus(ASSET_NETWORK_ID, "REORGED");
    expect(deposits).toHaveLength(1);
  });
});
