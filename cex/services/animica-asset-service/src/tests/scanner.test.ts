/**
 * Deposit Scanner Tests
 *
 * These are integration-style tests against a test PostgreSQL database with
 * the CEX schema applied.
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

  const USER_ID = "11111111-1111-1111-1111-111111111111";
  const ASSET_ID = "22222222-2222-2222-2222-222222222222";
  const NETWORK_ID = "33333333-3333-3333-3333-333333333333";
  const WALLET_UUID = "44444444-4444-4444-4444-444444444444";
  const ASSET_NETWORK_ID = "55555555-5555-5555-5555-555555555555";
  const DEPOSIT_ADDRESS = "anim1testaddress";

  beforeEach(async () => {
    const database = process.env.TEST_DB_NAME || "cex_test";
    if (!database.includes("test") && process.env.TEST_DB_ALLOW_NON_TEST !== "1") {
      throw new Error("Refusing to run scanner tests against a non-test database");
    }

    pool = new Pool({
      host: process.env.TEST_DB_HOST || "localhost",
      port: Number(process.env.TEST_DB_PORT) || 5432,
      user: process.env.TEST_DB_USER || "test",
      password: process.env.TEST_DB_PASSWORD || "test",
      database,
    });

    await resetFixtureData(pool);
    await insertFixtureData(pool);

    mockRpc = new MockAnimicaRpc();
    const rpcClient = createMockRpcClient(mockRpc);

    scanStateRepo = new ScanStateRepository(pool, logger);
    depositsRepo = new DepositsRepository(pool, logger);
    addressesRepo = new AddressesRepository(pool, logger);

    await scanStateRepo.initialize(ASSET_NETWORK_ID, 0);
    await addressesRepo.getOrCreate(
      USER_ID,
      ASSET_NETWORK_ID,
      WALLET_UUID,
      DEPOSIT_ADDRESS
    );

    scanner = new BlockScanner(
      pool,
      rpcClient,
      {
        assetNetworkId: ASSET_NETWORK_ID,
        confirmationsRequired: 3,
        scanBatch: 10,
        maxReorgDepth: 100,
        walletId: "ANIMICA_NODE",
        mempoolScanEnabled: true,
      },
      logger
    );
  });

  afterEach(async () => {
    await resetFixtureData(pool);
    await pool.end();
  });

  it("fresh sync scans from genesis when start height is zero", async () => {
    const scanned = await scanner.scan();

    const state = await scanStateRepo.get(ASSET_NETWORK_ID);
    expect(scanned).toBe(1);
    expect(state?.cursor_height).toBe(0);
    expect(state?.cursor_hash).toBe("genesis");
  });

  it("resumes from the existing cursor without rescanning prior blocks", async () => {
    await scanner.scan();
    mockRpc.mineBlock();

    const scanned = await scanner.scan();
    const state = await scanStateRepo.get(ASSET_NETWORK_ID);

    expect(scanned).toBe(1);
    expect(state?.cursor_height).toBe(1);
  });

  it("discovers historical deposits to currently assigned addresses", async () => {
    const txid = "0xhistorical";
    mockRpc.addTransaction({
      txid,
      from: "0xsender",
      to: DEPOSIT_ADDRESS,
      value: "1000000000000000000",
      nonce: 0,
      gas_limit: 21000,
      gas_price: "1000000000",
    });
    mockRpc.mineBlock([txid]);

    await scanner.scan();

    const deposits = await depositsRepo.getByStatus(ASSET_NETWORK_ID, "DETECTED");
    expect(deposits).toHaveLength(1);
    expect(deposits[0].txid).toBe(txid);
    expect(deposits[0].user_id).toBe(USER_ID);
  });

  it("pre-detects matching mempool transactions as pending", async () => {
    const txid = "0xpending";
    mockRpc.addTransaction({
      txid,
      from: "0xsender",
      to: DEPOSIT_ADDRESS,
      value: "2000000000000000000",
      nonce: 0,
      gas_limit: 21000,
      gas_price: "1000000000",
    });

    await scanner.scan();

    const deposits = await depositsRepo.getByStatus(ASSET_NETWORK_ID, "PENDING");
    expect(deposits).toHaveLength(1);
    expect(deposits[0].block_height).toBeNull();
  });

  it("credits confirmed address balance deltas when historical tx details are unavailable", async () => {
    mockRpc.setConfirmedBalance(DEPOSIT_ADDRESS, "26000000000");

    await scanner.scan();
    mockRpc.mineBlock();
    await scanner.scan();
    mockRpc.mineBlock();
    await scanner.scan();
    await scanner.scan();

    const deposits = await depositsRepo.getByStatus(ASSET_NETWORK_ID, "CONFIRMED");
    expect(deposits).toHaveLength(1);
    expect(deposits[0].txid).toBe(`balance:${DEPOSIT_ADDRESS}:0`);
    expect(deposits[0].amount_atoms).toBe("26000000000");
    expect(deposits[0].user_id).toBe(USER_ID);

    const outbox = await pool.query(
      "SELECT COUNT(*)::int AS count FROM deposit_outbox WHERE deposit_id = $1::uuid",
      [deposits[0].id]
    );
    expect(outbox.rows[0].count).toBe(1);
  });

  it("syncs forward normally without false reorgs", async () => {
    await scanner.scan();
    mockRpc.mineBlock();
    mockRpc.mineBlock();

    const scanned = await scanner.scan();
    const reorged = await depositsRepo.getByStatus(ASSET_NETWORK_ID, "REORGED");

    expect(scanned).toBe(2);
    expect(reorged).toHaveLength(0);
  });

  it("handles a real reorg by marking affected deposits reorged", async () => {
    const txid = "0xreorged";
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
    mockRpc.mineBlock();
    mockRpc.mineBlock();
    await scanner.scan();

    mockRpc.simulateReorg(1, [
      { hash: "0xreorg1" },
      { hash: "0xreorg2" },
      { hash: "0xreorg3" },
    ]);

    await scanner.scan();

    const deposits = await depositsRepo.getByStatus(ASSET_NETWORK_ID, "REORGED");
    expect(deposits).toHaveLength(1);
    expect(deposits[0].txid).toBe(txid);
  });

  it("prevents duplicate deposits on repeated scans", async () => {
    const txid = "0xduplicate";
    mockRpc.addTransaction({
      txid,
      from: "0xsender",
      to: DEPOSIT_ADDRESS,
      value: "4000000000000000000",
      nonce: 0,
      gas_limit: 21000,
      gas_price: "1000000000",
    });
    mockRpc.mineBlock([txid]);

    await scanner.scan();
    await scanner.scan();

    const count = await pool.query(
      "SELECT COUNT(*)::int AS count FROM deposits WHERE asset_network_id = $1::uuid AND txid = $2",
      [ASSET_NETWORK_ID, txid]
    );
    expect(count.rows[0].count).toBe(1);
  });

  it("does numeric cursor math when PostgreSQL returns bigint heights as strings", async () => {
    mockRpc.mineBlock();
    mockRpc.mineBlock();

    await pool.query(
      `
        UPDATE animica_scan_state
        SET cursor_height = $2::bigint, cursor_hash = $3
        WHERE asset_network_id = $1::uuid
      `,
      [ASSET_NETWORK_ID, "1", "0x0000000000000000000000000000000000000000000000000000000000000001"]
    );

    const scanned = await scanner.scan();
    const state = await scanStateRepo.get(ASSET_NETWORK_ID);

    expect(scanned).toBe(1);
    expect(state?.cursor_height).toBe(2);
  });

  async function resetFixtureData(pgPool: Pool): Promise<void> {
    await pgPool.query("DELETE FROM deposit_outbox WHERE deposit_id IN (SELECT id FROM deposits WHERE asset_network_id = $1::uuid)", [ASSET_NETWORK_ID]);
    await pgPool.query("DELETE FROM animica_seen_txs WHERE asset_network_id = $1::uuid", [ASSET_NETWORK_ID]);
    await pgPool.query("DELETE FROM animica_blocks WHERE asset_network_id = $1::uuid", [ASSET_NETWORK_ID]);
    await pgPool.query("DELETE FROM animica_scan_state WHERE asset_network_id = $1::uuid", [ASSET_NETWORK_ID]);
    await pgPool.query("DELETE FROM deposits WHERE asset_network_id = $1::uuid", [ASSET_NETWORK_ID]);
    await pgPool.query("DELETE FROM user_deposit_addresses WHERE asset_network_id = $1::uuid", [ASSET_NETWORK_ID]);
    await pgPool.query("DELETE FROM wallets WHERE id = $1::uuid", [WALLET_UUID]);
    await pgPool.query("DELETE FROM asset_networks WHERE id = $1::uuid", [ASSET_NETWORK_ID]);
    await pgPool.query("DELETE FROM assets WHERE id = $1::uuid", [ASSET_ID]);
    await pgPool.query("DELETE FROM networks WHERE id = $1::uuid", [NETWORK_ID]);
    await pgPool.query("DELETE FROM users WHERE id = $1::uuid", [USER_ID]);
  }

  async function insertFixtureData(pgPool: Pool): Promise<void> {
    await pgPool.query(
      "INSERT INTO users (id, email) VALUES ($1::uuid, 'animica-scanner-test@example.com')",
      [USER_ID]
    );
    await pgPool.query(
      "INSERT INTO assets (id, symbol, name, decimals, active) VALUES ($1::uuid, 'TANM', 'Test Animica', 18, true)",
      [ASSET_ID]
    );
    await pgPool.query(
      "INSERT INTO networks (id, code, name, type, confirmations_required, active) VALUES ($1::uuid, 'TANIMICA', 'Test Animica', 'ACCOUNT', 3, true)",
      [NETWORK_ID]
    );
    await pgPool.query(
      `
        INSERT INTO asset_networks (
          id, asset_id, network_id, deposits_enabled, withdrawals_enabled, metadata
        )
        VALUES ($1::uuid, $2::uuid, $3::uuid, true, true, '{"provider":"ANIMICA_NODE"}'::jsonb)
      `,
      [ASSET_NETWORK_ID, ASSET_ID, NETWORK_ID]
    );
    await pgPool.query(
      `
        INSERT INTO wallets (id, provider, wallet_id, asset_network_id, status)
        VALUES ($1::uuid, 'ANIMICA_NODE', 'test-animica-wallet', $2::uuid, 'ACTIVE')
      `,
      [WALLET_UUID, ASSET_NETWORK_ID]
    );
  }
});
