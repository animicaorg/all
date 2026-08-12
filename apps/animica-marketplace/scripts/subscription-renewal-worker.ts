import { prisma } from '../lib/db';
import { config } from '../lib/config';
import { LedgerError, settlePurchaseInTx } from '../lib/ledger';
import { licenseLeafHash } from '../lib/license';
import { acquireAdvisoryLock, makeLogger, parseFlags } from './store-worker-util';

// Subscription renewal worker — custodial auto-renewal, stated honestly: the chain has no
// pull-payment/allowance primitive, so renewals debit the buyer's MARKETPLACE LEDGER
// balance (withdrawable anytime), and ONLY with a wallet-signed StoreConsent on record.
//
// Per due subscription (autoRenew=true, ACTIVE, expiresAt within STORE_RENEW_AHEAD_HOURS):
//   - no consentId / no StoreConsent row  => SKIP (never debit without signed consent);
//   - sufficient balance => ONE transaction: settlePurchaseInTx (STORE_FEE_BPS 70/30 for
//     APP/DIGITAL_GOOD, MKT_FEE_BPS otherwise, fork royalty as-is) + child Purchase row
//     (parentPurchaseId chain = renewal history, consent carried) + SUBSCRIPTION License
//     for the new period + parent autoRenew=false (the child is now the renewing tail —
//     this is also the double-renewal guard);
//   - insufficient balance => dunning: graceUntil = expiresAt + STORE_GRACE_DAYS (3); the
//     wallet surfaces graceUntil as a top-up prompt; retried every run until grace lapses;
//   - grace lapsed => Purchase EXPIRED + autoRenew=false + license revoked at period end
//     (entitlement already enforces expiresAt — grace never extends access, it only keeps
//     the renewal attempt alive).
// Cancel (autoRenew=false) is handled by the routes; this worker never touches it.
//
// Ops: deploy/systemd/animica-store-renewals.* (flock + advisory lock; FILES ONLY).
// Gate: STORE_RENEWALS_ENABLED=1 arms it; default OFF = observe-only. --dry-run forces.

const WORKER = 'store-subscription-renewals';
const log = makeLogger(WORKER);

const ENABLED = process.env.STORE_RENEWALS_ENABLED === '1';
const RENEW_AHEAD_MS = Number(process.env.STORE_RENEW_AHEAD_HOURS ?? 24) * 3600_000;
const GRACE_DAYS = Number(process.env.STORE_GRACE_DAYS ?? 3);
const BATCH = Number(process.env.STORE_RENEWALS_BATCH ?? 100);

function feeBpsFor(listingType: string): number {
  if (listingType === 'APP' || listingType === 'DIGITAL_GOOD') return config.storeFeeBps;
  return config.feeBps;
}

// Grace lapsed => the subscription is over: EXPIRED, autoRenew off.
async function expireLapsed(dryRun: boolean): Promise<number> {
  const now = new Date();
  const lapsed = await prisma.purchase.findMany({
    where: {
      priceModel: 'SUBSCRIPTION',
      status: 'ACTIVE',
      autoRenew: true,
      expiresAt: { lt: now },
      graceUntil: { not: null, lt: now },
    },
    select: { id: true, buyerId: true, listingId: true, graceUntil: true },
    take: 200,
  });
  for (const p of lapsed) {
    if (dryRun) {
      log('info', 'would_expire_lapsed', { purchaseId: p.id, graceUntil: p.graceUntil });
      continue;
    }
    await prisma.purchase.updateMany({
      where: { id: p.id, status: 'ACTIVE' },
      data: { status: 'EXPIRED', autoRenew: false },
    });
    log('info', 'subscription_expired_after_grace', { purchaseId: p.id, listingId: p.listingId, graceUntil: p.graceUntil });
  }
  return lapsed.length;
}

async function renewOne(p: any, dryRun: boolean): Promise<'renewed' | 'dunning' | 'skipped'> {
  const listing = p.listing;
  const price = p.price;

  // NEVER debit without the wallet-signed consent record (design hard rule).
  const consent = p.consentId ? await prisma.storeConsent.findUnique({ where: { id: p.consentId } }) : null;
  if (!consent) {
    log('warn', 'renewal_skipped_no_consent', { purchaseId: p.id, listingId: listing.id, consentId: p.consentId ?? null });
    return 'skipped';
  }
  if (!price.active) {
    log('warn', 'renewal_skipped_price_inactive', { purchaseId: p.id, priceId: price.id });
    return 'skipped';
  }
  const feeBps = feeBpsFor(listing.type);
  if (!Number.isFinite(feeBps) || feeBps < 0 || feeBps > 10_000) {
    log('error', 'renewal_skipped_fee_unconfigured', { purchaseId: p.id, feeBps, detail: 'STORE_FEE_BPS unset — fail-closed' });
    return 'skipped';
  }

  // New period starts where the old one ends (renewing early must not shorten it).
  const base = p.expiresAt && new Date(p.expiresAt) > new Date() ? new Date(p.expiresAt) : new Date();
  const newExpiresAt = new Date(base.getTime() + price.periodDays * 86400_000);

  if (dryRun) {
    log('info', 'would_renew', { purchaseId: p.id, listingId: listing.id, amountNanm: price.amountNanm, newExpiresAt, feeBps });
    return 'renewed';
  }

  try {
    await prisma.$transaction(async (tx) => {
      // Claim the parent (also the double-renewal guard under concurrency/crash-retry).
      const claimed = await tx.purchase.updateMany({
        where: { id: p.id, status: 'ACTIVE', autoRenew: true },
        data: { autoRenew: false, graceUntil: null },
      });
      if (claimed.count !== 1) throw new Error('parent purchase concurrently claimed');

      await settlePurchaseInTx(tx, {
        buyerId: p.buyerId,
        creatorId: listing.ownerId,
        amountNanm: BigInt(price.amountNanm),
        listingId: listing.id,
        forkParentCreatorId: listing.forkOf?.ownerId,
        forkRoyaltyBps: listing.forkOfId ? listing.forkRoyaltyBps : undefined,
        feeBps,
      });

      const child = await tx.purchase.create({
        data: {
          buyerId: p.buyerId,
          listingId: listing.id,
          priceId: price.id,
          priceModel: 'SUBSCRIPTION',
          amountNanm: price.amountNanm,
          status: 'ACTIVE',
          source: 'balance',
          expiresAt: newExpiresAt,
          autoRenew: true,
          parentPurchaseId: p.id,
          consentId: p.consentId,
        },
      });

      // SUBSCRIPTION license for the new period (leafHash needs the row id: create-then-set).
      const lic = await tx.license.create({
        data: {
          purchaseId: child.id,
          buyerAddress: p.buyer.address,
          listingId: listing.id,
          buildId: null, // subscription covers all builds (design)
          kind: 'SUBSCRIPTION',
          expiresAt: newExpiresAt,
          leafHash: '',
        },
      });
      const leafHash = licenseLeafHash({
        licenseId: lic.id,
        purchaseTxid: null,
        buyerAddress: p.buyer.address,
        listingId: listing.id,
        buildId: null,
        buildSha3: null,
        certSha256: null,
        kind: 'SUBSCRIPTION',
        issuedAt: lic.issuedAt,
        expiresAt: newExpiresAt,
      });
      await tx.license.update({ where: { id: lic.id }, data: { leafHash } });
    });
    log('info', 'subscription_renewed', { purchaseId: p.id, listingId: listing.id, amountNanm: price.amountNanm, newExpiresAt });
    return 'renewed';
  } catch (e: any) {
    if (e instanceof LedgerError && e.code === 'INSUFFICIENT_FUNDS') {
      // Dunning: keep the attempt alive until grace lapses; the wallet shows the flag.
      const graceUntil = new Date((p.expiresAt ? new Date(p.expiresAt) : new Date()).getTime() + GRACE_DAYS * 86400_000);
      await prisma.purchase.updateMany({
        where: { id: p.id, status: 'ACTIVE', autoRenew: true, graceUntil: null },
        data: { graceUntil },
      });
      log('warn', 'renewal_dunning', { purchaseId: p.id, listingId: listing.id, amountNanm: price.amountNanm, graceUntil });
      return 'dunning';
    }
    throw e;
  }
}

async function main() {
  const { dryRun: flagDryRun } = parseFlags();
  const dryRun = flagDryRun || !ENABLED;

  if (!(await acquireAdvisoryLock(WORKER))) {
    log('info', 'another_instance_running', {});
    return;
  }
  log('info', 'run_start', { dryRun, enabled: ENABLED, renewAheadHours: RENEW_AHEAD_MS / 3600_000, graceDays: GRACE_DAYS });

  const expired = await expireLapsed(dryRun);

  const due = await prisma.purchase.findMany({
    where: {
      priceModel: 'SUBSCRIPTION',
      status: 'ACTIVE',
      autoRenew: true,
      expiresAt: { lte: new Date(Date.now() + RENEW_AHEAD_MS) },
      renewals: { none: {} },
    },
    include: {
      buyer: { select: { id: true, address: true } },
      listing: { include: { forkOf: { select: { ownerId: true } } } },
      price: true,
    },
    orderBy: { expiresAt: 'asc' },
    take: BATCH,
  });

  let renewed = 0, dunning = 0, skipped = 0;
  for (const p of due) {
    try {
      const outcome = await renewOne(p, dryRun);
      if (outcome === 'renewed') renewed += 1;
      else if (outcome === 'dunning') dunning += 1;
      else skipped += 1;
    } catch (e: any) {
      log('error', 'renewal_error', { purchaseId: p.id, error: String(e?.message ?? e) });
    }
  }
  log('info', 'run_done', { due: due.length, renewed, dunning, skipped, expiredAfterGrace: expired });
}

main()
  .catch((e) => {
    log('error', 'run_crashed', { error: String(e?.stack ?? e) });
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
