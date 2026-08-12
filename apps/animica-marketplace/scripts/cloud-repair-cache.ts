// Cache-coherence repair for Account.balanceNanm (2026-08-06).
//
//   npx tsx scripts/cloud-repair-cache.ts            # report only
//   npx tsx scripts/cloud-repair-cache.ts --apply    # repair
//
// WHAT THIS IS AND IS NOT.
//
// The append-only LedgerEntry table is the AUTHORITATIVE record of every nANM. Account.balanceNanm
// is a derived cache that lib/ledger.ts post() maintains transactionally. The invariant is
// therefore `balanceNanm == SUM(LedgerEntry.deltaNanm)` for every account.
//
// This script does NOT modify the ledger and does NOT create or destroy money in the accounting
// sense. It only re-derives the CACHE from the authoritative ledger where the two have diverged,
// which can only happen if something wrote balanceNanm directly, bypassing post(). That is exactly
// what happened once during the 2026-08-06 adversarial security review: a red-team script seeded
// the marketplace treasury by writing balanceNanm directly to fund a promotional-credit test,
// leaving the cache 1 ANM above the ledger.
//
// Every repair writes a ReconciliationReport row and a FinanceAlert so the change is auditable and
// never silent (§91: never silently modify balances to make reconciliation pass). If the divergence
// is on a USER account rather than a platform account, the script refuses to touch it and asks for
// a human decision — a user's cached balance being wrong may mean they were over- or under-served,
// and that is not something to paper over automatically.

import { prisma } from '../lib/db';
import { config } from '../lib/config';

const APPLY = process.argv.includes('--apply');

const PLATFORM_ADDRESSES = new Set(
  [config.treasuryAddress, config.foundationAddress, 'anim1marketplace-treasury-unset'].filter(Boolean),
);

async function main() {
  // NB: SUM() over a bigint column returns NUMERIC in Postgres, which Prisma surfaces as a
  // Decimal — mixing that with a bigint throws. Cast both sides to text and rebuild the BigInts
  // in JS so the comparison stays exact at every magnitude.
  const raw = await prisma.$queryRaw<
    Array<{ id: string; address: string; displayName: string | null; cached: string; ledger: string }>
  >`
    SELECT a.id, a.address, a."displayName",
           a."balanceNanm"::text AS cached,
           COALESCE(l.s, 0)::text AS ledger
    FROM "Account" a
    LEFT JOIN (SELECT "accountId", SUM("deltaNanm") s FROM "LedgerEntry" GROUP BY 1) l
      ON l."accountId" = a.id
    WHERE a."balanceNanm" <> COALESCE(l.s, 0)::bigint
  `;
  const rows = raw.map((r) => ({ ...r, cached: BigInt(r.cached), ledger: BigInt(r.ledger) }));

  if (rows.length === 0) {
    console.log(JSON.stringify({ at: new Date().toISOString(), msg: 'no divergence — every account reconciles' }));
    return;
  }

  const day = new Date().toISOString().slice(0, 10);
  for (const r of rows) {
    const drift = r.cached - r.ledger;
    const isPlatform = PLATFORM_ADDRESSES.has(r.address);
    const record = {
      accountId: r.id,
      address: r.address,
      displayName: r.displayName,
      cachedNanm: r.cached.toString(),
      ledgerNanm: r.ledger.toString(),
      driftNanm: drift.toString(),
      isPlatformAccount: isPlatform,
    };
    console.log(JSON.stringify({ at: new Date().toISOString(), msg: 'divergence', ...record }));

    if (!isPlatform) {
      console.log(
        JSON.stringify({
          at: new Date().toISOString(),
          level: 'error',
          msg: 'REFUSING to auto-repair a USER account — a human must decide',
          accountId: r.id,
        }),
      );
      continue;
    }

    if (!APPLY) continue;

    await prisma.$transaction(async (tx) => {
      // Re-derive the cache from the authoritative ledger. The ledger itself is untouched.
      await tx.account.update({ where: { id: r.id }, data: { balanceNanm: r.ledger } });
      await tx.reconciliationReport.upsert({
        where: { day_scope: { day, scope: `cache_repair:${r.id}` } },
        create: {
          day,
          scope: `cache_repair:${r.id}`,
          ok: false,
          expected: r.ledger.toString(),
          observed: r.cached.toString(),
          deltaAbs: (drift < 0n ? -drift : drift).toString(),
          detail: JSON.stringify({
            ...record,
            action: 'cache re-derived from the authoritative ledger; ledger unmodified',
            cause:
              'a 2026-08-06 adversarial-security-review script seeded this platform treasury by writing balanceNanm directly, bypassing lib/ledger.ts post()',
          }),
        },
        update: {},
      });
      await tx.financeAlert.create({
        data: {
          kind: 'ledger_mismatch',
          severity: 'critical',
          title: `Cache repaired on platform account ${r.displayName ?? r.address.slice(0, 20)}`,
          subject: r.id,
          detail: JSON.stringify(record),
        },
      });
    });
    console.log(JSON.stringify({ at: new Date().toISOString(), msg: 'repaired', accountId: r.id }));
  }

  if (!APPLY) console.log(JSON.stringify({ at: new Date().toISOString(), msg: 'REPORT ONLY — pass --apply to repair' }));
}

main()
  .catch((e) => {
    console.error(JSON.stringify({ level: 'error', msg: String(e?.message ?? e) }));
    process.exitCode = 1;
  })
  .finally(() => prisma.$disconnect());
