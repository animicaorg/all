/**
 * Deposit Credit Handler
 * 
 * Processes deposit credit commands from the deposit service
 * Credits user balances using double-entry accounting
 */

import type { PoolClient } from "pg";
import type { Logger } from "pino";
import { AccountsRepo, LedgerRepo, BalancesRepo, IdempotencyRepo } from "../../db/repositories/index.js";

export interface DepositCreditCommand {
  idempotencyKey: string;
  userId: string;
  assetId: string;
  amountAtoms: string; // BigInt as string
  source: {
    provider: string;
    txid: string;
    address: string;
    transferId?: string;
    coin: string;
    network: string;
  };
  depositId: string;
}

/**
 * Process deposit credit command
 * Credits user's AVAILABLE account from SYSTEM:CLEARING
 */
export async function handleDepositCredit(
  command: DepositCreditCommand,
  client: PoolClient,
  logger: Logger
): Promise<void> {
  const { idempotencyKey, userId, assetId, amountAtoms, source, depositId } = command;

  logger.info(
    {
      idempotencyKey,
      userId,
      assetId,
      amountAtoms,
      depositId,
      txid: source.txid,
    },
    "Processing deposit credit command"
  );

  // Check idempotency
  const idempotencyRepo = new IdempotencyRepo(client);
  const existing = await idempotencyRepo.get(idempotencyKey);
  
  if (existing) {
    logger.info(
      { idempotencyKey, existingResult: existing.result },
      "Deposit credit already processed (idempotent)"
    );
    return;
  }

  // Initialize repositories
  const accountsRepo = new AccountsRepo(client);
  const ledgerRepo = new LedgerRepo(client);
  const balancesRepo = new BalancesRepo(client);

  // Get or create user AVAILABLE account
  const userAccount = await accountsRepo.getOrCreateAccount(
    userId,
    assetId,
    "AVAILABLE"
  );

  // Get or create SYSTEM CLEARING account
  const clearingAccount = await accountsRepo.getOrCreateAccount(
    null, // system account
    assetId,
    "CLEARING"
  );

  // Convert amount
  const amountAtomsBigInt = BigInt(amountAtoms);

  if (amountAtomsBigInt <= 0n) {
    throw new Error("Amount must be positive");
  }

  // Create ledger transaction
  const txMetadata = {
    depositId,
    txid: source.txid,
    address: source.address,
    transferId: source.transferId,
    provider: source.provider,
    coin: source.coin,
    network: source.network,
  };

  const ledgerTxId = await ledgerRepo.createTransaction(
    "DEPOSIT",
    null, // no market
    null, // no sequence
    txMetadata
  );

  // Create double-entry:
  // DEBIT: SYSTEM:CLEARING (money leaving system clearing)
  // CREDIT: USER:AVAILABLE (money entering user available balance)
  await ledgerRepo.createEntry(
    ledgerTxId,
    clearingAccount.id,
    assetId,
    "DEBIT",
    amountAtomsBigInt,
    `Deposit ${source.txid}`
  );

  await ledgerRepo.createEntry(
    ledgerTxId,
    userAccount.id,
    assetId,
    "CREDIT",
    amountAtomsBigInt,
    `Deposit ${source.txid}`
  );

  // Update cached balances
  await balancesRepo.incrementAvailable(userId, assetId, amountAtomsBigInt);

  // Record idempotency
  await idempotencyRepo.set(
    idempotencyKey,
    "ledger-deposit-credit",
    {
      success: true,
      ledgerTxId,
      userId,
      assetId,
      amountAtoms,
      depositId,
    },
    7 * 24 * 60 * 60 // 7 days TTL
  );

  logger.info(
    {
      ledgerTxId,
      userId,
      assetId,
      amountAtoms,
      depositId,
      idempotencyKey,
    },
    "Deposit credit completed successfully"
  );
}
