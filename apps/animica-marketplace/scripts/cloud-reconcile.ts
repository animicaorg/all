import { prisma } from '../lib/db';
import { CURRENT_STATES } from '../lib/planConfig';
import { acquireAdvisoryLock, makeLogger, parseFlags } from './store-worker-util';
import { rollupFinanceDay, utcDayBounds, previousUtcDay } from './cloud-finance-rollup';

// Animica Python Cloud — daily financial reconciliation (§91, §92). Oneshot run by
// animica-cloud-reconcile.timer shortly after UTC midnight for the day that just closed.
//
// THE PRIME DIRECTIVE: this worker NEVER modifies a balance, a ledger row, or an execution.
// It compares independent records that must agree and writes only ReconciliationReport rows
// (unique on day+scope, so re-runs overwrite their own report) plus a FinanceAlert when a
// scope disagrees. A mismatch is evidence of a bug or an attack — silently "fixing" it would
// destroy the evidence, so surfacing loudly is the entire job.
//
// Four scopes:
//   anm_ledger : every Account.balanceNanm (cache) == SUM(LedgerEntry.deltaNanm) (truth).
//   execution  : for the day's settled executions, priceNanm == platformFee+developer+provider
//                (settle.ts's exact-sum invariant, §45) AND the ledger entries posted under
//                ref=executionId sum to zero (nothing minted, nothing burned).
//   usd_paypal : every verified PayPal capture of the day maps to a PlanSubscription in a
//                state that could legitimately have been paid for.
//   provider   : CloudProvider.earnedNanm (cache) == SUM(CloudExecution.providerNanm) (truth).
//
// It first refreshes the FinanceDaily cache for the target day via rollupFinanceDay() —
// which is why cloud-finance-rollup.ts has no systemd unit of its own.
//
// Ops: deploy/systemd/animica-cloud-reconcile.{service,timer} (FILES ONLY — integrator installs).

const WORKER = 'animica-cloud-reconcile';
const log = makeLogger(WORKER);

const ENABLED = process.env.CLOUD_RECONCILE_ENABLED === '1';
const DETAIL_CAP = 50; // mismatch samples persisted per scope; totals always cover everything

interface ScopeResult {
  scope: 'anm_ledger' | 'execution' | 'usd_paypal' | 'provider';
  ok: boolean;
  expected: bigint;
  observed: bigint;
  deltaAbs: bigint;
  detail: Record<string, unknown>;
}

function abs(v: bigint): bigint {
  return v < 0n ? -v : v;
}

function chunk<T>(items: T[], size: number): T[][] {
  const out: T[][] = [];
  for (let i = 0; i < items.length; i += size) out.push(items.slice(i, i + size));
  return out;
}

// ── Scope: anm_ledger ────────────────────────────────────────────────────────

async function reconcileAnmLedger(): Promise<ScopeResult> {
  const [accounts, sums] = await Promise.all([
    prisma.account.findMany({ select: { id: true, address: true, balanceNanm: true } }),
    prisma.ledgerEntry.groupBy({ by: ['accountId'], _sum: { deltaNanm: true } }),
  ]);
  const ledger = new Map<string, bigint>(sums.map((r) => [r.accountId, r._sum.deltaNanm ?? 0n]));

  let expected = 0n; // sum of cached balances
  let observed = 0n; // sum of ledger truth
  let deltaAbs = 0n;
  const mismatches: Array<Record<string, string>> = [];
  for (const a of accounts) {
    const truth = ledger.get(a.id) ?? 0n; // no ledger rows => the cache must be exactly 0
    expected += a.balanceNanm;
    observed += truth;
    if (truth !== a.balanceNanm) {
      deltaAbs += abs(a.balanceNanm - truth);
      if (mismatches.length < DETAIL_CAP) {
        mismatches.push({
          accountId: a.id,
          address: a.address.slice(0, 20),
          cachedNanm: a.balanceNanm.toString(),
          ledgerNanm: truth.toString(),
        });
      }
    }
    ledger.delete(a.id);
  }
  // Ledger rows pointing at a nonexistent account should be impossible (FK) — belt anyway,
  // because if it ever happens it is exactly the kind of thing this job exists to catch.
  for (const [accountId, truth] of ledger) {
    deltaAbs += abs(truth);
    observed += truth;
    if (mismatches.length < DETAIL_CAP) mismatches.push({ accountId, address: '(no account row)', cachedNanm: '0', ledgerNanm: truth.toString() });
  }

  const bad = deltaAbs !== 0n;
  return {
    scope: 'anm_ledger',
    ok: !bad,
    expected,
    observed,
    deltaAbs,
    detail: { accounts: accounts.length, mismatchedAccounts: bad ? mismatches.length : 0, samples: mismatches },
  };
}

// ── Scope: execution (day-bounded) ───────────────────────────────────────────

async function reconcileExecutions(start: Date, end: Date): Promise<ScopeResult> {
  const execs = await prisma.cloudExecution.findMany({
    where: { billed: true, createdAt: { gte: start, lt: end } },
    select: { id: true, priceNanm: true, platformFeeNanm: true, developerNanm: true, providerNanm: true },
  });

  let expected = 0n; // sum of prices charged
  let observed = 0n; // sum of the recorded splits
  let deltaAbs = 0n;
  const splitBad: Array<Record<string, string>> = [];
  for (const e of execs) {
    const split = e.platformFeeNanm + e.developerNanm + e.providerNanm;
    expected += e.priceNanm;
    observed += split;
    if (split !== e.priceNanm) {
      deltaAbs += abs(e.priceNanm - split);
      if (splitBad.length < DETAIL_CAP) {
        splitBad.push({ executionId: e.id, priceNanm: e.priceNanm.toString(), splitNanm: split.toString() });
      }
    }
  }

  // Ledger conservation per execution: everything posted under ref=executionId (settlement
  // AND in-execution wallet.pay transfers share the ref) must net to zero. Executions with no
  // postings (free tier, zero price) are trivially conserved. Chunked IN() keeps the query
  // bounded on heavy days.
  const ledgerBad: Array<Record<string, string>> = [];
  let unbalancedRefs = 0;
  for (const ids of chunk(execs.map((e) => e.id), 500)) {
    const groups = await prisma.ledgerEntry.groupBy({
      by: ['ref'],
      where: { ref: { in: ids } },
      _sum: { deltaNanm: true },
    });
    for (const g of groups) {
      const net = g._sum.deltaNanm ?? 0n;
      if (net !== 0n) {
        unbalancedRefs += 1;
        deltaAbs += abs(net);
        if (ledgerBad.length < DETAIL_CAP) ledgerBad.push({ executionId: String(g.ref), netNanm: net.toString() });
      }
    }
  }

  const bad = splitBad.length > 0 || unbalancedRefs > 0 || expected !== observed;
  return {
    scope: 'execution',
    ok: !bad,
    expected,
    observed,
    deltaAbs,
    detail: {
      settledExecutions: execs.length,
      splitMismatches: splitBad.length,
      unbalancedLedgerRefs: unbalancedRefs,
      splitSamples: splitBad,
      ledgerSamples: ledgerBad,
    },
  };
}

// ── Scope: usd_paypal (day-bounded) ──────────────────────────────────────────

async function reconcileUsdPaypal(start: Date, end: Date): Promise<ScopeResult> {
  const pays = await prisma.billingPayment.findMany({
    where: { occurredAt: { gte: start, lt: end }, status: 'COMPLETED' },
    select: {
      id: true,
      kind: true,
      amountCents: true,
      occurredAt: true,
      subscriptionId: true,
      paypalSubscriptionId: true,
      paypalCaptureId: true,
    },
  });

  const subIds = [...new Set(pays.map((p) => p.subscriptionId).filter((v): v is string => Boolean(v)))];
  const ppIds = [...new Set(pays.map((p) => p.paypalSubscriptionId).filter((v): v is string => Boolean(v)))];
  const subs = await prisma.planSubscription.findMany({
    where: { OR: [{ id: { in: subIds } }, { paypalSubscriptionId: { in: ppIds } }] },
    select: { id: true, paypalSubscriptionId: true, status: true, currentPeriodEnd: true, canceledAt: true },
  });
  const byId = new Map(subs.map((s) => [s.id, s]));
  const byPaypal = new Map(subs.filter((s) => s.paypalSubscriptionId).map((s) => [s.paypalSubscriptionId as string, s]));

  const current = new Set<string>(CURRENT_STATES as readonly string[]);
  let expected = 0n; // total verified capture cents for the day
  let observed = 0n; // cents that map to a legitimate subscription state
  const orphans: Array<Record<string, string>> = [];
  for (const p of pays) {
    expected += BigInt(p.amountCents);
    // service/enterprise payments are invoice-shaped, not subscription-bound.
    if (p.kind !== 'subscription') {
      observed += BigInt(p.amountCents);
      continue;
    }
    const sub = (p.subscriptionId && byId.get(p.subscriptionId)) || (p.paypalSubscriptionId && byPaypal.get(p.paypalSubscriptionId)) || null;
    // A capture is consistent when its subscription still holds the plan slot, or was canceled
    // AFTER the payment (normal churn/upgrade: the money was legitimately owed when it moved).
    const consistent =
      !!sub &&
      (current.has(sub.status) ||
        (sub.status === 'CANCELED' &&
          ((sub.canceledAt != null && sub.canceledAt >= p.occurredAt) ||
            (sub.currentPeriodEnd != null && sub.currentPeriodEnd > p.occurredAt))));
    if (consistent && p.amountCents > 0) {
      observed += BigInt(p.amountCents);
    } else if (orphans.length < DETAIL_CAP) {
      orphans.push({
        paymentId: p.id,
        captureId: p.paypalCaptureId,
        amountCents: String(p.amountCents),
        subscription: sub ? `${sub.id}:${sub.status}` : '(none found)',
      });
    }
  }

  const deltaAbs = abs(expected - observed);
  return {
    scope: 'usd_paypal',
    ok: deltaAbs === 0n,
    expected,
    observed,
    deltaAbs,
    detail: { payments: pays.length, inconsistentPayments: orphans.length ? orphans.length : 0, samples: orphans },
  };
}

// ── Scope: provider ──────────────────────────────────────────────────────────

async function reconcileProviders(): Promise<ScopeResult> {
  const [providers, sums] = await Promise.all([
    prisma.cloudProvider.findMany({ select: { id: true, name: true, earnedNanm: true } }),
    prisma.cloudExecution.groupBy({
      by: ['providerId'],
      where: { providerId: { not: null } },
      _sum: { providerNanm: true },
    }),
  ]);
  const truth = new Map<string, bigint>();
  for (const g of sums) {
    if (g.providerId) truth.set(g.providerId, g._sum.providerNanm ?? 0n);
  }

  let expected = 0n; // sum of cached earnedNanm
  let observed = 0n; // sum of execution truth
  let deltaAbs = 0n;
  const mismatches: Array<Record<string, string>> = [];
  for (const p of providers) {
    const t = truth.get(p.id) ?? 0n;
    expected += p.earnedNanm;
    observed += t;
    if (t !== p.earnedNanm) {
      deltaAbs += abs(p.earnedNanm - t);
      if (mismatches.length < DETAIL_CAP) {
        mismatches.push({ providerId: p.id, name: p.name.slice(0, 40), cachedNanm: p.earnedNanm.toString(), executionsNanm: t.toString() });
      }
    }
    truth.delete(p.id);
  }
  // providerNanm attributed to a provider row that no longer exists.
  for (const [providerId, t] of truth) {
    deltaAbs += abs(t);
    observed += t;
    if (mismatches.length < DETAIL_CAP) mismatches.push({ providerId, name: '(no provider row)', cachedNanm: '0', executionsNanm: t.toString() });
  }

  const bad = deltaAbs !== 0n;
  return {
    scope: 'provider',
    ok: !bad,
    expected,
    observed,
    deltaAbs,
    detail: { providers: providers.length, mismatchedProviders: bad ? mismatches.length : 0, samples: mismatches },
  };
}

// ── Persistence (reports + alerts — the ONLY writes this worker makes) ───────

async function persistScope(day: string, r: ScopeResult): Promise<void> {
  const fields = {
    ok: r.ok,
    expected: r.expected.toString(),
    observed: r.observed.toString(),
    deltaAbs: r.deltaAbs.toString(),
    detail: JSON.stringify(r.detail).slice(0, 20_000),
  };
  await prisma.reconciliationReport.upsert({
    where: { day_scope: { day, scope: r.scope } },
    create: { day, scope: r.scope, ...fields },
    update: fields,
  });
  if (r.ok) return;

  // One unresolved alert per (day, scope) — re-running a still-broken day must not spam the
  // admin console; a resolved-then-recurring mismatch alerts again.
  const subject = `${day}:${r.scope}`;
  const existing = await prisma.financeAlert.findFirst({ where: { kind: 'ledger_mismatch', subject, resolvedAt: null } });
  if (!existing) {
    await prisma.financeAlert.create({
      data: {
        kind: 'ledger_mismatch',
        severity: 'critical',
        title: `Reconciliation mismatch: ${r.scope} (${day})`,
        subject,
        detail: JSON.stringify({
          scope: r.scope,
          day,
          expected: r.expected.toString(),
          observed: r.observed.toString(),
          deltaAbs: r.deltaAbs.toString(),
          ...r.detail,
        }).slice(0, 20_000),
      },
    });
  }
}

// ── Main ─────────────────────────────────────────────────────────────────────

function argValue(name: string): string | null {
  for (const a of process.argv.slice(2)) {
    if (a.startsWith(`${name}=`)) return a.slice(name.length + 1);
  }
  return null;
}

async function main() {
  const { dryRun } = parseFlags();
  const day = argValue('--day') ?? previousUtcDay();
  const bounds = utcDayBounds(day);
  if (!bounds) {
    log('error', 'bad_day', { day, detail: 'expected --day=YYYY-MM-DD (UTC)' });
    process.exitCode = 1;
    return;
  }
  // Disarmed or --dry-run: the FULL comparison still runs (it is read-only by nature) — only
  // the report/alert/rollup writes are withheld. That makes a dry run a genuine audit.
  const write = ENABLED && !dryRun;

  if (!(await acquireAdvisoryLock(WORKER))) {
    log('info', 'another_instance_running', {});
    return;
  }

  // Refresh the FinanceDaily cache first: reconciliation and the rollup then describe the
  // same instant of the underlying records.
  try {
    const rollup = await rollupFinanceDay(day, { write });
    log('info', rollup.written ? 'finance_rollup_written' : 'finance_rollup_computed_only', { ...rollup });
  } catch (e: any) {
    log('error', 'finance_rollup_error', { day, error: String(e?.message ?? e) });
  }

  const results: ScopeResult[] = [];
  results.push(await reconcileAnmLedger());
  results.push(await reconcileExecutions(bounds.start, bounds.end));
  results.push(await reconcileUsdPaypal(bounds.start, bounds.end));
  results.push(await reconcileProviders());

  let mismatched = 0;
  for (const r of results) {
    if (!r.ok) mismatched += 1;
    if (write) await persistScope(day, r);
    log(r.ok ? 'info' : 'critical', 'scope_result', {
      day,
      scope: r.scope,
      ok: r.ok,
      expected: r.expected,
      observed: r.observed,
      deltaAbs: r.deltaAbs,
      persisted: write,
      ...(!r.ok ? { detail: JSON.stringify(r.detail).slice(0, 2000) } : {}),
    });
  }

  log(mismatched ? 'critical' : 'info', 'run_done', {
    day,
    scopes: results.length,
    mismatched,
    persisted: write,
    enabled: ENABLED,
    dryRun,
  });
}

main()
  .catch((e) => {
    log('error', 'run_crashed', { error: String(e?.stack ?? e) });
    process.exitCode = 1;
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
