/**
 * Trade Settlement Handler
 * 
 * Implements double-entry accounting for trade settlements:
 * - Base asset transfer: seller LOCKED → buyer AVAILABLE
 * - Quote asset transfer: buyer LOCKED → seller AVAILABLE
 * - Maker fee: maker → SYSTEM:FEE
 * - Taker fee: taker → SYSTEM:FEE
 * 
 * All entries must balance (debits = credits) per asset.
 */

import type { PoolClient } from "pg";
import type { TradeEvent, Market, LedgerEntry } from "../../domain/types.js";
import { AccountsRepo, LedgerRepo, BalancesRepo } from "../../db/repositories/index.js";
import { verifyBalanced, verifyPositiveAmounts, verifyNonNegativeBalance } from "../../domain/invariants.js";

interface TradeParties {
  makerUserId: string;
  takerUserId: string;
  makerSide: "BUY" | "SELL";
  takerSide: "BUY" | "SELL";
  buyerUserId: string;
  sellerUserId: string;
}

interface TradeAmounts {
  sizeAtoms: bigint;
  quoteAmountAtoms: bigint;
  makerFeeAtoms: bigint;
  takerFeeAtoms: bigint;
}

/**
 * Handle a trade settlement event
 * 
 * @param client - Database client (must be in a transaction)
 * @param tradeEvent - Trade event from matching engine
 * @param market - Market configuration
 * @returns Success indicator and optional error message
 */
export async function handleTradeEvent(
  client: PoolClient,
  tradeEvent: TradeEvent,
  market: Market
): Promise<{ ok: boolean; error?: string }> {
  const log = (msg: string, data?: any) => {
    console.log(`[trade_settle] ${msg}`, data || "");
  };

  try {
    log("Processing trade", {
      tradeId: tradeEvent.tradeId,
      market: market.symbol,
      makerOrderId: tradeEvent.makerOrderId,
      takerOrderId: tradeEvent.takerOrderId
    });

    // Step 1: Parse amounts from string atoms to BigInt
    const amounts = parseTradeAmounts(tradeEvent);
    log("Parsed amounts", {
      size: amounts.sizeAtoms.toString(),
      quote: amounts.quoteAmountAtoms.toString(),
      makerFee: amounts.makerFeeAtoms.toString(),
      takerFee: amounts.takerFeeAtoms.toString()
    });

    // Step 2: Get user IDs from orders table
    const parties = await getTradeParties(client, tradeEvent, market);
    log("Trade parties", {
      maker: parties.makerUserId,
      taker: parties.takerUserId,
      makerSide: parties.makerSide,
      takerSide: parties.takerSide,
      buyer: parties.buyerUserId,
      seller: parties.sellerUserId
    });

    // Step 3: Ensure all accounts exist
    const accounts = await ensureAccounts(client, parties, market);
    log("Accounts ensured", {
      sellerBaseId: accounts.sellerBaseLocked.id,
      buyerBaseId: accounts.buyerBaseAvailable.id,
      buyerQuoteId: accounts.buyerQuoteLocked.id,
      sellerQuoteId: accounts.sellerQuoteAvailable.id
    });

    // Step 4: Create ledger transaction
    const repos = {
      ledger: new LedgerRepo(client),
      balances: new BalancesRepo(client)
    };

    const transaction = await repos.ledger.createTransaction(
      "TRADE_SETTLE",
      market.id,
      BigInt(tradeEvent.sequence),
      {
        tradeId: tradeEvent.tradeId,
        makerOrderId: tradeEvent.makerOrderId,
        takerOrderId: tradeEvent.takerOrderId,
        priceAtoms: tradeEvent.priceAtoms,
        sizeAtoms: tradeEvent.sizeAtoms,
        quoteAmountAtoms: tradeEvent.quoteAmountAtoms
      }
    );

    log("Created transaction", { txId: transaction.id });

    // Step 5: Create balanced ledger entries
    const entries: LedgerEntry[] = [];

    // Base asset settlement: seller LOCKED → buyer AVAILABLE
    entries.push(
      await repos.ledger.addEntry(
        transaction.id,
        accounts.sellerBaseLocked.id,
        market.baseAsset,
        "DEBIT",
        amounts.sizeAtoms,
        `Trade ${tradeEvent.tradeId}: Base asset sold`
      )
    );

    entries.push(
      await repos.ledger.addEntry(
        transaction.id,
        accounts.buyerBaseAvailable.id,
        market.baseAsset,
        "CREDIT",
        amounts.sizeAtoms,
        `Trade ${tradeEvent.tradeId}: Base asset bought`
      )
    );

    // Quote asset settlement: buyer LOCKED → seller AVAILABLE
    entries.push(
      await repos.ledger.addEntry(
        transaction.id,
        accounts.buyerQuoteLocked.id,
        market.quoteAsset,
        "DEBIT",
        amounts.quoteAmountAtoms,
        `Trade ${tradeEvent.tradeId}: Quote asset paid`
      )
    );

    entries.push(
      await repos.ledger.addEntry(
        transaction.id,
        accounts.sellerQuoteAvailable.id,
        market.quoteAsset,
        "CREDIT",
        amounts.quoteAmountAtoms,
        `Trade ${tradeEvent.tradeId}: Quote asset received`
      )
    );

    // Maker fee: maker AVAILABLE → SYSTEM:FEE
    // Deduct from the asset they received (quote for maker)
    if (amounts.makerFeeAtoms > 0n) {
      const makerFeeAccount = parties.makerSide === "BUY" 
        ? accounts.makerBaseAvailable
        : accounts.makerQuoteAvailable;
      
      entries.push(
        await repos.ledger.addEntry(
          transaction.id,
          makerFeeAccount.id,
          market.feeAsset,
          "DEBIT",
          amounts.makerFeeAtoms,
          `Trade ${tradeEvent.tradeId}: Maker fee`
        )
      );

      entries.push(
        await repos.ledger.addEntry(
          transaction.id,
          accounts.systemFee.id,
          market.feeAsset,
          "CREDIT",
          amounts.makerFeeAtoms,
          `Trade ${tradeEvent.tradeId}: Maker fee collected`
        )
      );
    }

    // Taker fee: taker AVAILABLE → SYSTEM:FEE
    // Deduct from the asset they received
    if (amounts.takerFeeAtoms > 0n) {
      const takerFeeAccount = parties.takerSide === "BUY"
        ? accounts.takerBaseAvailable
        : accounts.takerQuoteAvailable;

      entries.push(
        await repos.ledger.addEntry(
          transaction.id,
          takerFeeAccount.id,
          market.feeAsset,
          "DEBIT",
          amounts.takerFeeAtoms,
          `Trade ${tradeEvent.tradeId}: Taker fee`
        )
      );

      entries.push(
        await repos.ledger.addEntry(
          transaction.id,
          accounts.systemFee.id,
          market.feeAsset,
          "CREDIT",
          amounts.takerFeeAtoms,
          `Trade ${tradeEvent.tradeId}: Taker fee collected`
        )
      );
    }

    log("Created entries", { count: entries.length });

    // Step 6: Verify entries balance
    const balanceCheck = verifyBalanced(entries);
    if (!balanceCheck.ok) {
      log("ERROR: Entries not balanced", balanceCheck.errors);
      return { ok: false, error: `Entries not balanced: ${balanceCheck.errors.join(", ")}` };
    }

    const positiveCheck = verifyPositiveAmounts(entries);
    if (!positiveCheck.ok) {
      log("ERROR: Non-positive amounts", positiveCheck.errors);
      return { ok: false, error: `Non-positive amounts: ${positiveCheck.errors.join(", ")}` };
    }

    log("Entries verified as balanced");

    // Step 7: Update balances cache
    await updateBalances(client, parties, market, amounts, accounts);
    log("Balances updated");

    // Step 8: Update order locks (reduce usedAtoms)
    await updateOrderLocks(client, tradeEvent, amounts, parties);
    log("Order locks updated");

    log("Trade settled successfully", { tradeId: tradeEvent.tradeId });
    return { ok: true };

  } catch (error) {
    log("ERROR: Trade settlement failed", {
      tradeId: tradeEvent.tradeId,
      error: error instanceof Error ? error.message : String(error)
    });
    return { 
      ok: false, 
      error: error instanceof Error ? error.message : String(error) 
    };
  }
}

/**
 * Parse trade amounts from string to BigInt
 */
function parseTradeAmounts(tradeEvent: TradeEvent): TradeAmounts {
  return {
    sizeAtoms: BigInt(tradeEvent.sizeAtoms),
    quoteAmountAtoms: BigInt(tradeEvent.quoteAmountAtoms),
    makerFeeAtoms: BigInt(tradeEvent.makerFeeAtoms),
    takerFeeAtoms: BigInt(tradeEvent.takerFeeAtoms)
  };
}

/**
 * Get trade parties (maker/taker user IDs and determine buyer/seller)
 */
async function getTradeParties(
  client: PoolClient,
  tradeEvent: TradeEvent,
  market: Market
): Promise<TradeParties> {
  // Query orders table to get user IDs
  const ordersResult = await client.query(
    `SELECT id, user_id, side
     FROM orders
     WHERE id IN ($1, $2)`,
    [tradeEvent.makerOrderId, tradeEvent.takerOrderId]
  );

  if (ordersResult.rowCount !== 2) {
    throw new Error(
      `Orders not found: maker=${tradeEvent.makerOrderId}, taker=${tradeEvent.takerOrderId}`
    );
  }

  const makerOrder = ordersResult.rows.find((row: any) => row.id === tradeEvent.makerOrderId);
  const takerOrder = ordersResult.rows.find((row: any) => row.id === tradeEvent.takerOrderId);

  if (!makerOrder || !takerOrder) {
    throw new Error("Orders query result mismatch");
  }

  const makerSide = makerOrder.side as "BUY" | "SELL";
  const takerSide = takerOrder.side as "BUY" | "SELL";

  // Determine buyer and seller based on sides
  // BUY side: gets base asset, pays quote asset
  // SELL side: loses base asset, gets quote asset
  const buyerUserId = makerSide === "BUY" ? makerOrder.user_id : takerOrder.user_id;
  const sellerUserId = makerSide === "SELL" ? makerOrder.user_id : takerOrder.user_id;

  return {
    makerUserId: makerOrder.user_id,
    takerUserId: takerOrder.user_id,
    makerSide,
    takerSide,
    buyerUserId,
    sellerUserId
  };
}

/**
 * Ensure all required accounts exist for the trade
 */
async function ensureAccounts(
  client: PoolClient,
  parties: TradeParties,
  market: Market
) {
  const accountsRepo = new AccountsRepo(client);

  // Ensure buyer accounts (base and quote)
  const buyerBaseAccounts = await accountsRepo.ensureUserAccounts(
    parties.buyerUserId,
    market.baseAsset
  );
  const buyerQuoteAccounts = await accountsRepo.ensureUserAccounts(
    parties.buyerUserId,
    market.quoteAsset
  );

  // Ensure seller accounts (base and quote)
  const sellerBaseAccounts = await accountsRepo.ensureUserAccounts(
    parties.sellerUserId,
    market.baseAsset
  );
  const sellerQuoteAccounts = await accountsRepo.ensureUserAccounts(
    parties.sellerUserId,
    market.quoteAsset
  );

  // Ensure system FEE account for the fee asset
  const systemFee = await accountsRepo.ensureSystemAccount("FEE", market.feeAsset);

  // Determine which accounts are maker/taker based on sides
  const makerBaseAccounts = parties.makerSide === "BUY" ? buyerBaseAccounts : sellerBaseAccounts;
  const makerQuoteAccounts = parties.makerSide === "BUY" ? buyerQuoteAccounts : sellerQuoteAccounts;
  const takerBaseAccounts = parties.takerSide === "BUY" ? buyerBaseAccounts : sellerBaseAccounts;
  const takerQuoteAccounts = parties.takerSide === "BUY" ? buyerQuoteAccounts : sellerQuoteAccounts;

  return {
    // Base asset flow: seller LOCKED → buyer AVAILABLE
    sellerBaseLocked: sellerBaseAccounts.locked,
    buyerBaseAvailable: buyerBaseAccounts.available,

    // Quote asset flow: buyer LOCKED → seller AVAILABLE
    buyerQuoteLocked: buyerQuoteAccounts.locked,
    sellerQuoteAvailable: sellerQuoteAccounts.available,

    // Fee accounts (for deducting from what they received)
    makerBaseAvailable: makerBaseAccounts.available,
    makerQuoteAvailable: makerQuoteAccounts.available,
    takerBaseAvailable: takerBaseAccounts.available,
    takerQuoteAvailable: takerQuoteAccounts.available,

    systemFee
  };
}

/**
 * Update balances cache atomically
 */
async function updateBalances(
  client: PoolClient,
  parties: TradeParties,
  market: Market,
  amounts: TradeAmounts,
  accounts: any
): Promise<void> {
  const balancesRepo = new BalancesRepo(client);

  // Get current balances
  const buyerBaseBalance = await balancesRepo.getBalance(parties.buyerUserId, market.baseAsset);
  const buyerQuoteBalance = await balancesRepo.getBalance(parties.buyerUserId, market.quoteAsset);
  const sellerBaseBalance = await balancesRepo.getBalance(parties.sellerUserId, market.baseAsset);
  const sellerQuoteBalance = await balancesRepo.getBalance(parties.sellerUserId, market.quoteAsset);

  // Buyer: gains base (available), loses quote (locked)
  const buyerBaseFee = parties.buyerUserId === parties.makerUserId 
    ? (parties.makerSide === "BUY" ? amounts.makerFeeAtoms : 0n)
    : (parties.takerSide === "BUY" ? amounts.takerFeeAtoms : 0n);

  const newBuyerBaseAvailable = (buyerBaseBalance?.availableAtoms || 0n) + amounts.sizeAtoms - buyerBaseFee;
  const newBuyerBaseLocked = (buyerBaseBalance?.lockedAtoms || 0n);

  const newBuyerQuoteAvailable = (buyerQuoteBalance?.availableAtoms || 0n);
  const newBuyerQuoteLocked = (buyerQuoteBalance?.lockedAtoms || 0n) - amounts.quoteAmountAtoms;

  // Seller: loses base (locked), gains quote (available)
  const sellerQuoteFee = parties.sellerUserId === parties.makerUserId
    ? (parties.makerSide === "SELL" ? amounts.makerFeeAtoms : 0n)
    : (parties.takerSide === "SELL" ? amounts.takerFeeAtoms : 0n);

  const newSellerBaseAvailable = (sellerBaseBalance?.availableAtoms || 0n);
  const newSellerBaseLocked = (sellerBaseBalance?.lockedAtoms || 0n) - amounts.sizeAtoms;

  const newSellerQuoteAvailable = (sellerQuoteBalance?.availableAtoms || 0n) + amounts.quoteAmountAtoms - sellerQuoteFee;
  const newSellerQuoteLocked = (sellerQuoteBalance?.lockedAtoms || 0n);

  // Verify no negative balances
  verifyNonNegativeBalance(newBuyerBaseAvailable, parties.buyerUserId, market.baseAsset);
  verifyNonNegativeBalance(newBuyerBaseLocked, parties.buyerUserId, market.baseAsset);
  verifyNonNegativeBalance(newBuyerQuoteAvailable, parties.buyerUserId, market.quoteAsset);
  verifyNonNegativeBalance(newBuyerQuoteLocked, parties.buyerUserId, market.quoteAsset);
  verifyNonNegativeBalance(newSellerBaseAvailable, parties.sellerUserId, market.baseAsset);
  verifyNonNegativeBalance(newSellerBaseLocked, parties.sellerUserId, market.baseAsset);
  verifyNonNegativeBalance(newSellerQuoteAvailable, parties.sellerUserId, market.quoteAsset);
  verifyNonNegativeBalance(newSellerQuoteLocked, parties.sellerUserId, market.quoteAsset);

  // Update balances
  await balancesRepo.updateBalance(
    parties.buyerUserId,
    market.baseAsset,
    newBuyerBaseAvailable,
    newBuyerBaseLocked
  );

  await balancesRepo.updateBalance(
    parties.buyerUserId,
    market.quoteAsset,
    newBuyerQuoteAvailable,
    newBuyerQuoteLocked
  );

  await balancesRepo.updateBalance(
    parties.sellerUserId,
    market.baseAsset,
    newSellerBaseAvailable,
    newSellerBaseLocked
  );

  await balancesRepo.updateBalance(
    parties.sellerUserId,
    market.quoteAsset,
    newSellerQuoteAvailable,
    newSellerQuoteLocked
  );
}

/**
 * Update order locks to track how much has been used
 */
async function updateOrderLocks(
  client: PoolClient,
  tradeEvent: TradeEvent,
  amounts: TradeAmounts,
  parties: TradeParties
): Promise<void> {
  const makerUsedAtoms = parties.makerSide === "BUY" ? amounts.quoteAmountAtoms : amounts.sizeAtoms;
  const takerUsedAtoms = parties.takerSide === "BUY" ? amounts.quoteAmountAtoms : amounts.sizeAtoms;

  await client.query(
    `UPDATE order_locks
     SET used_atoms = LEAST(locked_atoms, used_atoms + $1),
         updated_at = NOW()
     WHERE order_id = $2`,
    [makerUsedAtoms.toString(), tradeEvent.makerOrderId]
  );

  await client.query(
    `UPDATE order_locks
     SET used_atoms = LEAST(locked_atoms, used_atoms + $1),
         updated_at = NOW()
     WHERE order_id = $2`,
    [takerUsedAtoms.toString(), tradeEvent.takerOrderId]
  );
}
