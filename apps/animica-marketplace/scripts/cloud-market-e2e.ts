// End-to-end verification of the Python Cloud MARKETPLACE surface (apps, purchase, authorize,
// grants, reviews, handles, founding seats). Exercises the REAL route handlers against the REAL
// database — no mocks — and cleans up its rows afterwards.
//
//   npx tsx scripts/cloud-market-e2e.ts [--keep]
//
// Proves, with DB-level assertions:
//   * app create -> publish gating -> public catalog/detail/search visibility,
//   * purchase settles exactly through the ledger with the FOUNDING feeBps snapshot (10%),
//     buyer debited / developer credited / treasury fee row, and re-purchase never double-charges,
//   * authorize is fail-closed for SPEND_ANM (caps + expiry mandatory, undeclared caps refused),
//     the grant lands, is listed, and revocation sticks,
//   * the review gate refuses a non-user and the owner, accepts a real purchaser, and the
//     cached ratingSum/ratingCount are recomputed from the rows,
//   * handle claiming (valid, duplicate, reserved, malformed) and the public developer profile,
//   * the FOUNDING SEAT RACE: N+2 concurrent applications can never over-allocate N seats.

import { readFileSync } from 'node:fs';
import { join } from 'node:path';

// Load .env BEFORE any app module reads process.env (tsx does not auto-load it).
try {
  const envFile = readFileSync(join(__dirname, '..', '.env'), 'utf8');
  for (const line of envFile.split('\n')) {
    const m = line.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (m && process.env[m[1]] === undefined) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
} catch {}

const KEEP = process.argv.includes('--keep');
const TAG = 'mkt' + Date.now().toString(36);

let failures = 0;
function check(name: string, cond: boolean, detail = '') {
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  (' + detail + ')' : ''}`);
  if (!cond) failures++;
}

function addr(suffix: string): string {
  return ('anim1' + 'q'.repeat(24) + TAG + suffix).toLowerCase();
}

async function main() {
  console.log(`\n=== Animica Python Cloud — marketplace route E2E (${TAG}) ===\n`);

  const { prisma } = await import('../lib/db');

  // Pin the founding program for THIS process: seats = current max seq + 4 so the race below
  // has exactly 3 free seats after the developer takes one. The live server is untouched.
  const baseSeqRow = await prisma.foundingDeveloper.aggregate({ _max: { seq: true } });
  const baseSeq = baseSeqRow._max.seq ?? 0;
  process.env.FOUNDING_DEV_SEATS = String(baseSeq + 4);
  process.env.FOUNDING_DEV_AUTO_ACCEPT = '1';

  const { postInTx } = await import('../lib/ledger');
  const { issueSession } = await import('../lib/session');
  const { NextRequest } = await import('next/server');
  const { founding } = await import('../lib/cloud/config');

  const appsRoute = await import('../app/api/cloud/v1/apps/route');
  const appDetailRoute = await import('../app/api/cloud/v1/apps/[slug]/route');
  const publishRoute = await import('../app/api/cloud/v1/apps/[slug]/publish/route');
  const purchaseRoute = await import('../app/api/cloud/v1/apps/[slug]/purchase/route');
  const authorizeRoute = await import('../app/api/cloud/v1/apps/[slug]/authorize/route');
  const reviewsRoute = await import('../app/api/cloud/v1/apps/[slug]/reviews/route');
  const grantsRoute = await import('../app/api/cloud/v1/grants/route');
  const agentsRoute = await import('../app/api/cloud/v1/agents/route');
  const agentDetailRoute = await import('../app/api/cloud/v1/agents/[slug]/route');
  const handleRoute = await import('../app/api/cloud/v1/developers/handle/route');
  const profileRoute = await import('../app/api/cloud/v1/developers/[handle]/route');
  const searchRoute = await import('../app/api/cloud/v1/search/route');
  const statsRoute = await import('../app/api/cloud/v1/stats/route');
  const foundingRoute = await import('../app/api/cloud/v1/founding/route');

  function req(method: string, url: string, session?: string, body?: unknown) {
    return new NextRequest(`http://127.0.0.1${url}`, {
      method,
      headers: {
        'content-type': 'application/json',
        ...(session ? { cookie: `anm_mkt_session=${session}` } : {}),
      },
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    });
  }
  async function json(res: Response) {
    return { status: res.status, body: await res.json() };
  }

  // --- identities -----------------------------------------------------------
  const dev = await prisma.account.create({ data: { address: addr('dev'), displayName: 'Mkt E2E Dev' } });
  const buyer = await prisma.account.create({ data: { address: addr('buy'), displayName: 'Mkt E2E Buyer' } });
  const stranger = await prisma.account.create({ data: { address: addr('str'), displayName: 'Mkt E2E Stranger' } });
  const racers = [] as { id: string; session: string }[];
  for (let i = 0; i < 5; i++) {
    const a = await prisma.account.create({ data: { address: addr('rc' + i) } });
    racers.push({ id: a.id, session: issueSession(a.id) });
  }
  const devSess = issueSession(dev.id);
  const buyerSess = issueSession(buyer.id);
  const strangerSess = issueSession(stranger.id);
  const allIds = [dev.id, buyer.id, stranger.id, ...racers.map((r) => r.id)];

  const FUND = 5_000_000_000n; // 5 ANM
  await prisma.$transaction((tx) => postInTx(tx, buyer.id, FUND, 'DEPOSIT', TAG, 'market e2e funding'));

  // --- founding: developer applies (auto-accept) ---------------------------
  console.log('--- founding developer: apply + auto-accept ---');
  let r = await json(await foundingRoute.POST(req('POST', '/api/cloud/v1/founding', devSess, { pitch: 'market e2e' })));
  check('developer application accepted', r.status === 201 && r.body?.application?.status === 'ACCEPTED');
  check(`developer got seat ${baseSeq + 1}`, r.body?.application?.seq === baseSeq + 1, `seq=${r.body?.application?.seq}`);
  check('founding fee benefit is 1000 bps', r.body?.application?.benefits?.feeBps === founding.feeBps);
  const devCredits = await prisma.cloudCredit.findMany({ where: { accountId: dev.id, source: 'founding' } });
  check('exactly one founding credit granted', devCredits.length === 1 && devCredits[0].grantedNanm === founding.creditsNanm);

  // --- handle claiming ------------------------------------------------------
  console.log('--- handle claiming ---');
  const handle = ('mktdev' + TAG).slice(0, 30);
  r = await json(await handleRoute.POST(req('POST', '/api/cloud/v1/developers/handle', devSess, { handle })));
  check('developer claimed a handle', r.status === 201 && r.body?.handle === handle);
  r = await json(await handleRoute.POST(req('POST', '/api/cloud/v1/developers/handle', buyerSess, { handle: handle.toUpperCase() })));
  check('duplicate handle refused case-insensitively', r.status === 409, r.body?.error?.code);
  r = await json(await handleRoute.POST(req('POST', '/api/cloud/v1/developers/handle', buyerSess, { handle: 'admin' })));
  check('reserved handle refused', r.status === 400 && r.body?.error?.code === 'reserved_handle');
  r = await json(await handleRoute.POST(req('POST', '/api/cloud/v1/developers/handle', buyerSess, { handle: 'x' })));
  check('malformed handle refused', r.status === 400 && r.body?.error?.code === 'invalid_handle');

  // --- app create -> publish gate -> publish -------------------------------
  console.log('--- app lifecycle ---');
  const appSlug = `mkt-app-${TAG}`;
  r = await json(
    await appsRoute.POST(
      req('POST', '/api/cloud/v1/apps', devSess, {
        slug: appSlug,
        name: `Market E2E ${TAG}`,
        tagline: 'Route-level end-to-end proof app',
        description: 'Created by scripts/cloud-market-e2e.ts to verify the marketplace money path.',
        category: 'DEVELOPER_TOOLS',
        capabilities: ['AI_INFERENCE', 'SPEND_ANM'],
        pricingModel: 'ONE_TIME',
        priceNanm: '2000000000', // 2 ANM
        tags: ['e2e', TAG],
      }),
    ),
  );
  check('app created as DRAFT', r.status === 201 && r.body?.app?.status === 'DRAFT');
  const appId = r.body?.app?.id as string;

  r = await json(await publishRoute.POST(req('POST', `/api/cloud/v1/apps/${appSlug}/publish`, devSess, {}), { params: { slug: appSlug } }));
  check('publish refused without a live function', r.status === 422 && r.body?.error?.code === 'not_publishable');

  const fn = await prisma.cloudFunction.create({
    data: {
      ownerId: dev.id,
      appId,
      slug: `mkt-fn-${TAG}`,
      name: 'Market E2E fn',
      entrypoint: 'main',
      status: 'PUBLISHED',
      visibility: 'PUBLIC',
      capabilities: ['AI_INFERENCE', 'SPEND_ANM'],
      currentVersion: 1,
    },
  });
  await prisma.cloudFunctionVersion.create({
    data: {
      functionId: fn.id,
      version: 1,
      source: 'def main(request, ctx):\n    return {"ok": True}\n',
      sourceSha3: TAG,
      artifactSha3: TAG,
      sizeBytes: 44,
      entrypoint: 'main',
      createdById: dev.id,
    },
  });

  r = await json(await publishRoute.POST(req('POST', `/api/cloud/v1/apps/${appSlug}/publish`, devSess, {}), { params: { slug: appSlug } }));
  check('publish succeeds with a live function', r.status === 200 && r.body?.published === true);

  r = await json(await appsRoute.GET(req('GET', `/api/cloud/v1/apps?tag=${TAG}`)));
  check('published app appears in the public catalog', r.status === 200 && r.body?.apps?.some((a: any) => a.slug === appSlug));
  r = await json(await appDetailRoute.GET(req('GET', `/api/cloud/v1/apps/${appSlug}`), { params: { slug: appSlug } }));
  check('public detail lists the function + real usage', r.status === 200 && r.body?.functions?.length === 1 && r.body?.usage?.executionsTotal === 0);
  r = await json(await searchRoute.GET(req('GET', `/api/cloud/v1/search?q=${TAG}`)));
  check('search finds the app and the tag', r.status === 200 && r.body?.apps?.some((a: any) => a.slug === appSlug) && r.body?.tags?.includes(TAG.toLowerCase()));

  // --- purchase: founding feeBps snapshot + exact ledger movement ----------
  console.log('--- purchase ---');
  const before = new Map(
    (await prisma.account.findMany({ where: { id: { in: [dev.id, buyer.id] } }, select: { id: true, balanceNanm: true } })).map(
      (a) => [a.id, a.balanceNanm],
    ),
  );
  r = await json(await purchaseRoute.POST(req('POST', `/api/cloud/v1/apps/${appSlug}/purchase`, buyerSess, {}), { params: { slug: appSlug } }));
  const price = 2_000_000_000n;
  check('purchase succeeded', r.status === 201 && r.body?.purchased === true, JSON.stringify(r.body?.error ?? ''));
  check('feeBps SNAPSHOT is the founding 10%', r.body?.purchase?.feeBps === 1000, `feeBps=${r.body?.purchase?.feeBps}`);
  check('developer share is 90%', BigInt(r.body?.purchase?.developerNanm ?? 0) === (price * 9000n) / 10_000n);
  const after = new Map(
    (await prisma.account.findMany({ where: { id: { in: [dev.id, buyer.id] } }, select: { id: true, balanceNanm: true } })).map(
      (a) => [a.id, a.balanceNanm],
    ),
  );
  check('buyer debited exactly the price', before.get(buyer.id)! - after.get(buyer.id)! === price);
  check('developer credited exactly 90%', after.get(dev.id)! - before.get(dev.id)! === (price * 9000n) / 10_000n);
  const feeRows = await prisma.ledgerEntry.findMany({ where: { ref: appId, kind: 'FEE' } });
  check('treasury received the 10% fee', feeRows.length === 1 && feeRows[0].deltaNanm === price / 10n);

  r = await json(await purchaseRoute.POST(req('POST', `/api/cloud/v1/apps/${appSlug}/purchase`, buyerSess, {}), { params: { slug: appSlug } }));
  check('re-purchase is idempotent (alreadyOwned)', r.status === 200 && r.body?.alreadyOwned === true);
  const after2 = await prisma.account.findUnique({ where: { id: buyer.id }, select: { balanceNanm: true } });
  check('re-purchase moved no money', after2!.balanceNanm === after.get(buyer.id));
  r = await json(await purchaseRoute.POST(req('POST', `/api/cloud/v1/apps/${appSlug}/purchase`, devSess, {}), { params: { slug: appSlug } }));
  check('self-purchase refused', r.status === 400 && r.body?.error?.code === 'self_purchase');

  // --- authorize: fail-closed SPEND_ANM + grants dashboard -----------------
  console.log('--- authorize + grants ---');
  r = await json(
    await authorizeRoute.POST(req('POST', `/api/cloud/v1/apps/${appSlug}/authorize`, buyerSess, { capabilities: ['SPEND_ANM'] }), {
      params: { slug: appSlug },
    }),
  );
  check('SPEND_ANM without caps is refused (fail-closed)', r.status === 400 && r.body?.error?.code === 'caps_required');
  r = await json(
    await authorizeRoute.POST(
      req('POST', `/api/cloud/v1/apps/${appSlug}/authorize`, buyerSess, { capabilities: ['READ_CHAIN'] }),
      { params: { slug: appSlug } },
    ),
  );
  check('undeclared capability refused', r.status === 400 && r.body?.error?.code === 'undeclared_capability');
  const expiresAt = new Date(Date.now() + 30 * 24 * 3600 * 1000).toISOString();
  r = await json(
    await authorizeRoute.POST(
      req('POST', `/api/cloud/v1/apps/${appSlug}/authorize`, buyerSess, {
        capabilities: ['SPEND_ANM', 'AI_INFERENCE'],
        maxPerCallNanm: '100000000',
        dailyCapNanm: '500000000',
        allowedPayees: [dev.address],
        expiresAt,
      }),
      { params: { slug: appSlug } },
    ),
  );
  check('capped SPEND_ANM grant created', r.status === 201 && r.body?.grant?.capabilities?.includes('SPEND_ANM'));
  const grantId = r.body?.grant?.id as string;
  const grantRow = await prisma.cloudGrant.findUnique({ where: { id: grantId } });
  check(
    'grant row carries the caps the executor enforces',
    grantRow != null && grantRow.maxPerCallNanm === 100_000_000n && grantRow.dailyCapNanm === 500_000_000n && grantRow.revokedAt == null,
  );
  r = await json(await grantsRoute.GET(req('GET', '/api/cloud/v1/grants', buyerSess)));
  check('grants dashboard lists it with the app resolved', r.status === 200 && r.body?.grants?.some((g: any) => g.id === grantId && g.subject?.slug === appSlug));
  r = await json(await grantsRoute.DELETE(req('DELETE', `/api/cloud/v1/grants?id=${grantId}`, buyerSess)));
  check('revoke succeeds', r.status === 200 && r.body?.revoked === true);
  const revokedRow = await prisma.cloudGrant.findUnique({ where: { id: grantId } });
  check('revocation is durable (executor re-reads this row)', revokedRow?.revokedAt != null);

  // --- review gate ----------------------------------------------------------
  console.log('--- reviews ---');
  r = await json(
    await reviewsRoute.POST(req('POST', `/api/cloud/v1/apps/${appSlug}/reviews`, strangerSess, { rating: 5, body: 'great' }), {
      params: { slug: appSlug },
    }),
  );
  check('non-user review REJECTED', r.status === 403 && r.body?.error?.code === 'not_a_user');
  r = await json(
    await reviewsRoute.POST(req('POST', `/api/cloud/v1/apps/${appSlug}/reviews`, devSess, { rating: 5 }), { params: { slug: appSlug } }),
  );
  check('owner self-review rejected', r.status === 403 && r.body?.error?.code === 'own_app');
  r = await json(
    await reviewsRoute.POST(req('POST', `/api/cloud/v1/apps/${appSlug}/reviews`, buyerSess, { rating: 4, body: 'solid tooling' }), {
      params: { slug: appSlug },
    }),
  );
  check('purchaser review accepted', r.status === 201 && r.body?.basis === 'purchase');
  const appRow = await prisma.cloudApp.findUnique({ where: { id: appId }, select: { ratingSum: true, ratingCount: true } });
  check('app rating recomputed from rows', appRow?.ratingSum === 4 && appRow?.ratingCount === 1);

  // --- developer profile ----------------------------------------------------
  r = await json(await profileRoute.GET(req('GET', `/api/cloud/v1/developers/${handle}`), { params: { handle } }));
  check(
    'public profile: app listed + REAL earnings + genuine founding badge',
    r.status === 200 &&
      r.body?.apps?.some((a: any) => a.slug === appSlug) &&
      BigInt(r.body?.usage?.earnedNanm ?? 0) === (price * 9000n) / 10_000n &&
      r.body?.developer?.founding?.seq === baseSeq + 1,
  );

  // --- agents: server-side budget rules ------------------------------------
  console.log('--- agents ---');
  r = await json(
    await agentsRoute.POST(
      req('POST', '/api/cloud/v1/agents', devSess, { slug: `mkt-agent-${TAG}`, name: 'E2E agent', functionId: fn.id, dailySpendCapNanm: '1000' }),
    ),
  );
  check('daily cap without per-run cap refused', r.status === 400 && r.body?.error?.code === 'budget_invalid');
  r = await json(
    await agentsRoute.POST(
      req('POST', '/api/cloud/v1/agents', devSess, {
        slug: `mkt-agent-${TAG}`,
        name: 'E2E agent',
        functionId: fn.id,
        maxSpendPerRunNanm: '500',
        dailySpendCapNanm: '1000',
        capabilities: ['AI_INFERENCE'],
      }),
    ),
  );
  check('agent created PAUSED with budgets', r.status === 201 && r.body?.agent?.status === 'PAUSED');
  r = await json(
    await agentDetailRoute.PATCH(req('PATCH', `/api/cloud/v1/agents/mkt-agent-${TAG}`, devSess, { status: 'ACTIVE' }), {
      params: { slug: `mkt-agent-${TAG}` },
    }),
  );
  check('agent resumed', r.status === 200 && r.body?.agent?.status === 'ACTIVE');
  r = await json(await agentDetailRoute.DELETE(req('DELETE', `/api/cloud/v1/agents/mkt-agent-${TAG}`, devSess), { params: { slug: `mkt-agent-${TAG}` } }));
  check('agent with no executions hard-deletes', r.status === 200 && r.body?.deleted === true);

  // --- public stats ---------------------------------------------------------
  r = await json(await statsRoute.GET());
  check(
    'public stats are real counts',
    r.status === 200 && r.body?.functionsDeployed >= 1 && typeof r.body?.executions === 'number' && typeof r.body?.anmPaidToDevelopersNanm === 'string',
    `functions=${r.body?.functionsDeployed} apps=${r.body?.appsPublished} devs=${r.body?.developers}`,
  );

  // --- THE FOUNDING SEAT RACE ----------------------------------------------
  console.log(`--- founding seat race: 5 concurrent applications for 3 remaining seats (cap ${baseSeq + 4}) ---`);
  const raceResults = await Promise.all(
    racers.map(async (racer) => json(await foundingRoute.POST(req('POST', '/api/cloud/v1/founding', racer.session, {})))),
  );
  const acceptedRes = raceResults.filter((x) => x.body?.application?.status === 'ACCEPTED');
  const waitlisted = raceResults.filter((x) => x.body?.waitlisted === true);
  check('exactly 3 of 5 racers accepted', acceptedRes.length === 3, `accepted=${acceptedRes.length}`);
  check('exactly 2 of 5 racers waitlisted', waitlisted.length === 2, `waitlisted=${waitlisted.length}`);
  const seqs = acceptedRes.map((x) => x.body.application.seq).sort((a: number, b: number) => a - b);
  check(
    `winner seats are exactly ${baseSeq + 2}..${baseSeq + 4} (no seat ${baseSeq + 5} ever minted)`,
    JSON.stringify(seqs) === JSON.stringify([baseSeq + 2, baseSeq + 3, baseSeq + 4]),
    `seqs=${JSON.stringify(seqs)}`,
  );
  const dbAccepted = await prisma.foundingDeveloper.findMany({ where: { accountId: { in: racers.map((x) => x.id) }, status: 'ACCEPTED' } });
  check('DB agrees: 3 accepted rows, all with unique seq', dbAccepted.length === 3 && new Set(dbAccepted.map((d) => d.seq)).size === 3);
  const raceCredits = await prisma.cloudCredit.count({ where: { accountId: { in: racers.map((x) => x.id) }, source: 'founding' } });
  check('exactly one credit grant per accepted racer', raceCredits === 3, `credits=${raceCredits}`);
  const overCap = await prisma.foundingDeveloper.count({ where: { seq: { gt: baseSeq + 4 } } });
  check('no seat beyond the cap exists anywhere', overCap === 0);

  console.log(`\n=== ${failures === 0 ? 'ALL CHECKS PASSED' : failures + ' CHECK(S) FAILED'} ===\n`);

  // --- cleanup --------------------------------------------------------------
  if (!KEEP) {
    await prisma.cloudApp.deleteMany({ where: { id: appId } }); // cascades purchases + reviews
    await prisma.cloudFunctionVersion.deleteMany({ where: { functionId: fn.id } });
    await prisma.cloudFunction.deleteMany({ where: { id: fn.id } });
    await prisma.cloudAuditLog.deleteMany({ where: { subject: { in: allIds } } });
    await prisma.ledgerEntry.deleteMany({ where: { accountId: { in: allIds } } });
    await prisma.usageCounter.deleteMany({ where: { accountId: { in: allIds } } });
    await prisma.account.deleteMany({ where: { id: { in: allIds } } }); // cascades grants, founding rows, credits
    console.log('cleaned up test data\n');
  } else {
    console.log(`kept: app=${appId} dev=${dev.id} buyer=${buyer.id}\n`);
  }

  process.exitCode = failures === 0 ? 0 : 1;
  await prisma.$disconnect();
}

main().catch(async (e) => {
  console.error('MARKET E2E ERROR:', e);
  process.exitCode = 1;
});
