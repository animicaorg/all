/* Retire the legacy AI-marketplace catalog: set status=DELISTED on every AI-type Listing
 * (RAG_ASSISTANT / AGENT / WORKFLOW / KNOWLEDGE_AI / MEDIA) so the public catalog empties
 * naturally — GET /api/mkt/v1/listings and the store surfaces only show PUBLISHED rows.
 *
 * DRY-RUN BY DEFAULT: prints the exact plan and exits without writing. Pass --apply to
 * execute. Idempotent — already-DELISTED rows are skipped, re-running is a no-op.
 *
 * This script NEVER deletes rows. Listing rows stay (License.purchaseId and
 * StorePaymentIntent.purchaseId carry REQUIRED FKs through Purchase; reviews/versions/usage
 * hang off Listing), Purchase rows stay (real purchases exist), and LedgerEntry/Account are
 * append-only by platform law. Store listings (APP / DIGITAL_GOOD) are never touched.
 *
 * Run from the app dir with production env:
 *   npx tsx scripts/retire-ai-listings.ts            # dry-run (default)
 *   npx tsx scripts/retire-ai-listings.ts --apply    # write
 */
import { PrismaClient, ListingType, ListingStatus } from '@prisma/client';

const prisma = new PrismaClient();

// The retired AI-listing surface. STORE types (APP, DIGITAL_GOOD) are deliberately absent.
const AI_TYPES: ListingType[] = [
  ListingType.RAG_ASSISTANT,
  ListingType.AGENT,
  ListingType.WORKFLOW,
  ListingType.KNOWLEDGE_AI,
  ListingType.MEDIA,
];

async function main() {
  const apply = process.argv.includes('--apply');
  const mode = apply ? 'APPLY' : 'DRY-RUN (pass --apply to write)';
  console.log(`retire-ai-listings — ${mode}`);

  // Row-count snapshot up front so the "nothing deleted" invariant is provable, not asserted.
  const [listingsBefore, purchasesBefore, ledgerBefore, accountsBefore] = await Promise.all([
    prisma.listing.count(),
    prisma.purchase.count(),
    prisma.ledgerEntry.count(),
    prisma.account.count(),
  ]);

  const targets = await prisma.listing.findMany({
    where: { type: { in: AI_TYPES }, status: { not: ListingStatus.DELISTED } },
    select: {
      id: true, slug: true, type: true, status: true,
      _count: { select: { purchases: true } },
    },
    orderBy: { slug: 'asc' },
  });
  const alreadyDone = await prisma.listing.count({
    where: { type: { in: AI_TYPES }, status: ListingStatus.DELISTED },
  });

  if (!targets.length) {
    console.log(`nothing to do: 0 AI-type listings need delisting (${alreadyDone} already DELISTED).`);
    return;
  }

  console.log(`plan: DELIST ${targets.length} AI-type listing(s) (${alreadyDone} already DELISTED):`);
  for (const t of targets) {
    console.log(
      `  ${t.status.padEnd(9)} -> DELISTED  ${t.type.padEnd(13)} ${t.slug}` +
      (t._count.purchases ? `  (${t._count.purchases} purchase(s) KEPT)` : ''),
    );
  }

  if (!apply) {
    console.log('dry-run: no rows written.');
    return;
  }

  // One guarded updateMany keyed by the ids we just printed — the plan IS the write set.
  const res = await prisma.listing.updateMany({
    where: { id: { in: targets.map((t) => t.id) }, status: { not: ListingStatus.DELISTED } },
    data: { status: ListingStatus.DELISTED },
  });
  console.log(`delisted ${res.count} listing(s).`);

  // Prove append-only: every table the retirement must not shrink still has every row.
  const [listingsAfter, purchasesAfter, ledgerAfter, accountsAfter] = await Promise.all([
    prisma.listing.count(),
    prisma.purchase.count(),
    prisma.ledgerEntry.count(),
    prisma.account.count(),
  ]);
  const drift =
    listingsAfter !== listingsBefore || purchasesAfter !== purchasesBefore ||
    ledgerAfter !== ledgerBefore || accountsAfter !== accountsBefore;
  console.log(
    `row counts (before -> after): Listing ${listingsBefore}->${listingsAfter}, ` +
    `Purchase ${purchasesBefore}->${purchasesAfter}, LedgerEntry ${ledgerBefore}->${ledgerAfter}, ` +
    `Account ${accountsBefore}->${accountsAfter}`,
  );
  if (drift) {
    // A concurrent writer creating rows mid-run is benign; a SHRINK would be a bug worth a scream.
    console.error('WARNING: row counts changed during the run — verify no rows were deleted.');
    process.exitCode = 2;
  }
}

main()
  .catch((e) => { console.error(e); process.exitCode = 1; })
  .finally(() => prisma.$disconnect());
