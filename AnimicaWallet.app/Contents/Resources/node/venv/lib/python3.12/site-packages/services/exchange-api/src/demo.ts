/**
 * Example script demonstrating the Exchange API ledger system
 * This shows how to use the core services for basic operations
 */

import { prisma, LedgerService, ReconciliationService } from './index.js';
import { Decimal } from '@prisma/client/runtime/library';

async function main() {
  console.log('🚀 Exchange API Demo\n');

  const ledger = new LedgerService(prisma);
  const reconciliation = new ReconciliationService(prisma, ledger);

  // 1. Create a test user
  console.log('1. Creating test user...');
  const user = await prisma.user.create({
    data: {
      email: 'demo@example.com',
      status: 'ACTIVE',
      role: 'USER',
      profile: {
        create: {
          displayName: 'Demo User',
          country: 'US',
        },
      },
    },
  });
  console.log(`   ✓ Created user: ${user.email} (${user.id})\n`);

  // 2. Create a test asset
  console.log('2. Creating test asset (BTC)...');
  const btc = await prisma.asset.create({
    data: {
      symbol: 'BTC',
      name: 'Bitcoin',
      decimals: 8,
      kind: 'NATIVE',
      isEnabled: true,
    },
  });
  console.log(`   ✓ Created asset: ${btc.symbol} (${btc.id})\n`);

  // 3. Credit a deposit
  console.log('3. Crediting deposit of 1.5 BTC...');
  const depositAmount = new Decimal('1.5');
  const { transactionId: depositTxId } = await ledger.creditDeposit(
    user.id,
    btc.id,
    depositAmount,
    'demo-deposit-tx-123',
    'demo-deposit-idempotency-key'
  );
  console.log(`   ✓ Deposit credited: ${depositAmount} BTC`);
  console.log(`   Transaction ID: ${depositTxId}\n`);

  // 4. Check balance
  console.log('4. Checking user balance...');
  const availableAccount = await ledger.getOrCreateAccount(
    user.id,
    'USER',
    'AVAILABLE',
    btc.id
  );
  const balance = await ledger.getBalance(availableAccount.accountId);
  console.log(`   ✓ Available: ${balance.available} BTC`);
  console.log(`   ✓ Locked: ${balance.locked} BTC\n`);

  // 5. Lock funds for an order
  console.log('5. Locking 0.5 BTC for order...');
  const lockAmount = new Decimal('0.5');
  const { transactionId: lockTxId } = await ledger.lockFunds(
    user.id,
    btc.id,
    lockAmount,
    'demo-order-123'
  );
  console.log(`   ✓ Funds locked: ${lockAmount} BTC`);
  console.log(`   Transaction ID: ${lockTxId}\n`);

  // 6. Check updated balance
  console.log('6. Checking updated balance...');
  const updatedBalance = await ledger.getBalance(availableAccount.accountId);
  const lockedAccount = await ledger.getOrCreateAccount(
    user.id,
    'USER',
    'LOCKED',
    btc.id
  );
  const lockedBalance = await ledger.getBalance(lockedAccount.accountId);
  console.log(`   ✓ Available: ${updatedBalance.available} BTC`);
  console.log(`   ✓ Locked: ${lockedBalance.locked} BTC\n`);

  // 7. Verify double-entry balance
  console.log('7. Verifying transaction balance...');
  const verification = await reconciliation.verifyTransactionBalance(depositTxId);
  console.log(`   ✓ Transaction balanced: ${verification.balanced}`);
  for (const [assetId, balance] of verification.assetBalances) {
    console.log(`   Asset: ${assetId}`);
    console.log(`   - Debits:  ${balance.debits}`);
    console.log(`   - Credits: ${balance.credits}`);
  }
  console.log();

  // 8. Reconcile accounts
  console.log('8. Running reconciliation...');
  const reconResult = await reconciliation.reconcileAllBalances();
  console.log(`   ✓ Total accounts: ${reconResult.totalAccounts}`);
  console.log(`   ✓ Reconciled: ${reconResult.reconciledAccounts}`);
  console.log(`   ✓ Mismatches: ${reconResult.mismatches.length}\n`);

  // Cleanup
  console.log('9. Cleaning up test data...');
  await prisma.ledgerEntry.deleteMany({
    where: { account: { ownerId: user.id } },
  });
  await prisma.ledgerTransaction.deleteMany();
  await prisma.balanceCache.deleteMany({
    where: { account: { ownerId: user.id } },
  });
  await prisma.ledgerAccount.deleteMany({
    where: { ownerId: user.id },
  });
  await prisma.asset.delete({ where: { id: btc.id } });
  await prisma.userProfile.delete({ where: { userId: user.id } });
  await prisma.user.delete({ where: { id: user.id } });
  console.log('   ✓ Cleanup complete\n');

  console.log('✅ Demo completed successfully!');
}

main()
  .catch((error) => {
    console.error('❌ Error:', error);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
