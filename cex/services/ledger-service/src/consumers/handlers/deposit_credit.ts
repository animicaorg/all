/**
 * Deposit Credit Handler
 *
 * Processes deposit credit commands from the deposit service
 * Credits user balances using double-entry accounting
 */

import type { PoolClient } from "pg";
import type { Logger } from "pino";
import {
  AccountsRepo,
  LedgerRepo,
  BalancesRepo,
  IdempotencyRepo,
} from "../../db/repositories/index.js";

export interface DepositCreditCommand {
  idempotencyKey: string;
  userId: string;
  assetId: string;
  amountAtoms: string;
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

export async function handleDepositCredit(
  command: DepositCreditCommand,
  client: PoolClient,
  logger: Logger
): Promise<void> {
  const {
    idempotencyKey,
    userId,
    assetId,
    amountAtoms,
    source,
    depositId,
  } = command;

  const idempotencyRepo = new IdempotencyRepo(client);
  const accountsRepo = new AccountsRepo(client);
  const ledgerRepo = new LedgerRepo(client);
  const balancesRepo = new BalancesRepo(client);

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

  // ----------------------------
  // Idempotency check
  // ----------------------------
  const existing = await idempotencyRepo.get(idempotencyKey);
  if (existing) {
    logger.info(
      { idempotencyKey, existingResult: existing.result },
      "Deposit credit already processed (idempotent)"
    );
    return;
  }

  const amount = BigInt(amountAtoms);
  if (amount <= 0n) {
    throw new Error("Amount must be positive");
  }

  // ----------------------------
  // Accounts (FIXED: no ensureAccount)
  // ----------------------------
  const userAccount = await accountsRepo.ensureUserAccounts(
    userId,
    assetId
  );

  const systemAccounts = await accountsRepo.ensureSystemAccount(
    "CLEARING",
    assetId
  );

  const clearingAccount = systemAccounts;

  // ----------------------------
  // Ledger transaction
  // ----------------------------
  const txMetadata = {
    depositId,
    txid: source.txid,
    address: source.address,
    transferId: source.transferId,
    provider: source.provider,
    coin: source.coin,
    network: source.network,
  };

  const ledgerTx = await ledgerRepo.createTransaction(
    "DEPOSIT",
    null,
    null,
    txMetadata
  );

  const ledgerTxId = ledgerTx.id;

  // ----------------------------
  // Double-entry accounting
  // ----------------------------
  await ledgerRepo.addEntry(
    ledgerTxId,
    userAccount.available.id,
    assetId,
    "DEBIT",
    amount,
    `Deposit ${source.txid}`
  );

  await ledgerRepo.addEntry(
    ledgerTxId,
    clearingAccount.id,
    assetId,
    "CREDIT",
    amount,
    `Deposit ${source.txid}`
  );

  // ----------------------------
  // Balance cache update
  // ----------------------------
  const existingBalance = await balancesRepo.getBalance(userId, assetId);
  await balancesRepo.updateBalance(
    userId,
    assetId,
    (existingBalance?.availableAtoms || 0n) + amount,
    existingBalance?.lockedAtoms || 0n
  );

  // ----------------------------
  // Idempotency record
  // ----------------------------
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
    7 * 24 * 60 * 60
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
