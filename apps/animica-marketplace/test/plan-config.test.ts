// Unit tests for the pure subscription/entitlement core (lib/planConfig.ts) — the money and
// state-machine logic every billing decision rests on. Run: npm test
// (node --test --import tsx test/*.test.ts — no DB, no network.)
//
// Catalog era: the PYTHON CLOUD plans (free/developer/pro/business/enterprise), derived from
// lib/cloud/config.ts. The effectivePlan/computeSubPatch/money suites are behavior pins on
// verified webhook state-machine code and must not weaken across catalog changes.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  PLAN_DEFAULTS,
  PLAN_KEYS,
  PLAN_CATALOG,
  isPlanKey,
  isContactSalesPlan,
  planRank,
  limitsFor,
  effectivePlan,
  computeSubPatch,
  assertSubscriptionMoneyCents,
  centsToUsdString,
  periodKeyFor,
  clampIntervalMinutes,
  nextRunAfter,
  safetyCaps,
  salePaymentFromResource,
} from '../lib/planConfig';
import { PLAN_ENTITLEMENTS, CLOUD_PLAN_CATALOG } from '../lib/cloud/config';

// ── plan keys + ranking ──────────────────────────────────────────────────────

test('plan keys are the Cloud tiers, ordered free→enterprise, and rank accordingly', () => {
  assert.deepEqual([...PLAN_KEYS], ['free', 'developer', 'pro', 'business', 'enterprise']);
  assert.ok(planRank('enterprise') > planRank('business'));
  assert.ok(planRank('business') > planRank('pro'));
  assert.ok(planRank('pro') > planRank('developer'));
  assert.ok(planRank('developer') > planRank('free'));
  assert.equal(planRank('nonsense'), 0);
  assert.ok(isPlanKey('developer'));
  assert.ok(isPlanKey('enterprise'));
  assert.ok(!isPlanKey(42));
});

test('the retired legacy tiers are no longer plan keys and rank as free', () => {
  for (const legacy of ['starter', 'operator']) {
    assert.ok(!isPlanKey(legacy), `${legacy} must be retired`);
    assert.equal(planRank(legacy), 0, `${legacy} must never outrank a real tier`);
  }
});

test('catalog mirrors lib/cloud/config (single source of truth) and prices the spec', () => {
  assert.deepEqual(
    PLAN_CATALOG.map((p) => p.key),
    CLOUD_PLAN_CATALOG.map((p) => p.key),
  );
  const byKey = Object.fromEntries(PLAN_CATALOG.map((p) => [p.key, p]));
  assert.equal(byKey.free.priceUsdCents, 0);
  assert.equal(byKey.developer.priceUsdCents, 2900);
  assert.equal(byKey.pro.priceUsdCents, 9900);
  assert.equal(byKey.business.priceUsdCents, 49900);
  assert.equal(byKey.enterprise.priceUsdCents, 150000); // display "from" price
  // Enterprise is contact-sales only; every self-serve tier is not.
  assert.ok(byKey.enterprise.contactSales);
  assert.ok(isContactSalesPlan('enterprise'));
  for (const k of ['free', 'developer', 'pro', 'business'] as const) {
    assert.ok(!byKey[k].contactSales, k);
    assert.ok(!isContactSalesPlan(k), k);
  }
  // Pro carries the "MOST POPULAR" ribbon; sort order matches rank order.
  assert.ok(byKey.pro.featured);
  const sorted = [...PLAN_CATALOG].sort((a, b) => a.sortOrder - b.sortOrder).map((p) => p.key);
  assert.deepEqual(sorted, [...PLAN_KEYS]);
});

test('tier ladders are monotonic for the headline limits (-1 = unlimited tops the ladder)', () => {
  const val = (v: number) => (v === -1 ? Number.POSITIVE_INFINITY : v);
  const ordered = PLAN_KEYS.map((k) => PLAN_DEFAULTS[k]);
  for (const feature of ['anm_deployments', 'api_keys', 'team_members', 'workspaces', 'api_rate_limit', 'execution_priority'] as const) {
    for (let i = 1; i < ordered.length; i++) {
      assert.ok(
        val(ordered[i][feature] as number) >= val(ordered[i - 1][feature] as number),
        `${feature} must not shrink from ${PLAN_KEYS[i - 1]} to ${PLAN_KEYS[i]}`,
      );
    }
  }
});

test('the legacy vocabulary derives from the Cloud entitlements', () => {
  for (const key of PLAN_KEYS) {
    const legacy = PLAN_DEFAULTS[key];
    const cloud = PLAN_ENTITLEMENTS[key];
    assert.equal(legacy.anm_deployments, cloud.max_apps, `${key}: anm_deployments follows max_apps`);
    assert.equal(legacy.api_rate_limit, cloud.api_rate_limit, key);
    assert.equal(legacy.marketplace_selling, cloud.marketplace_publishing, key);
    assert.equal(legacy.advanced_analytics, cloud.premium_analytics, key);
    assert.equal(legacy.execution_priority, cloud.priority_class, key);
    assert.equal(legacy.worker_min_interval_minutes, cloud.min_schedule_minutes, key);
    assert.equal(legacy.worker_max_concurrency, cloud.max_concurrency, key);
  }
});

test('workers are free on EVERY tier — the old paywall is gone', () => {
  for (const key of PLAN_KEYS) {
    assert.equal(PLAN_DEFAULTS[key].workers, -1, `${key}: workers must be unlimited`);
    assert.equal(PLAN_DEFAULTS[key].scheduled_executions_monthly, -1, `${key}: executions must be metered, not walled`);
    assert.ok(PLAN_DEFAULTS[key].external_triggers, `${key}: external triggers ride with free workers`);
  }
});

test('spec headline numbers for the paid gates that remain', () => {
  assert.equal(PLAN_DEFAULTS.free.anm_deployments, 1);
  assert.equal(PLAN_DEFAULTS.free.api_keys, 0);
  assert.equal(PLAN_DEFAULTS.free.team_members, 0);
  assert.ok(!PLAN_DEFAULTS.free.marketplace_selling);
  assert.ok(!PLAN_DEFAULTS.free.private_agents);
  assert.equal(PLAN_DEFAULTS.developer.anm_deployments, 10);
  assert.ok(PLAN_DEFAULTS.developer.marketplace_selling);
  assert.ok(PLAN_DEFAULTS.developer.private_agents);
  assert.ok(!PLAN_DEFAULTS.developer.white_label);
  assert.equal(PLAN_DEFAULTS.pro.anm_deployments, 50);
  assert.ok(PLAN_DEFAULTS.pro.advanced_analytics);
  assert.ok(PLAN_DEFAULTS.pro.white_label);
  assert.ok(!PLAN_DEFAULTS.pro.reseller_features);
  assert.equal(PLAN_DEFAULTS.business.anm_deployments, 250);
  assert.equal(PLAN_DEFAULTS.business.team_members, 50);
  assert.ok(PLAN_DEFAULTS.business.reseller_features);
  assert.equal(PLAN_DEFAULTS.enterprise.anm_deployments, -1);
  assert.equal(PLAN_DEFAULTS.enterprise.api_keys, -1);
});

// ── limitsFor: DB overrides merged over defaults, fail-safe ──────────────────

test('limitsFor merges valid overrides and ignores garbage', () => {
  const base = limitsFor('pro', null);
  assert.equal(base.api_keys, 10);

  const merged = limitsFor('pro', JSON.stringify({ api_keys: 20, marketplace_selling: false, api_rate_limit: 500.9 }));
  assert.equal(merged.api_keys, 20);
  assert.equal(merged.marketplace_selling, false);
  assert.equal(merged.api_rate_limit, 500); // truncated to int

  // Wrong types / unknown keys / broken JSON never widen or crash.
  const junk = limitsFor('pro', JSON.stringify({ api_keys: 'lots', evil_key: true, white_label: 'yes' }));
  assert.equal(junk.api_keys, 10);
  assert.equal((junk as any).evil_key, undefined);
  assert.equal(junk.white_label, true); // pro default survives a wrong-typed override
  assert.deepEqual(limitsFor('pro', '{not json'), base);
  // Unknown AND retired plan keys fall back to free defaults.
  assert.deepEqual(limitsFor('enterprise-nope', null), PLAN_DEFAULTS.free);
  assert.deepEqual(limitsFor('starter', null), PLAN_DEFAULTS.free);
});

test('limitsFor can never re-gate workers from the DB (stale limitsJson defense)', () => {
  const l = limitsFor('pro', JSON.stringify({ workers: 5, scheduled_executions_monthly: 1000 }));
  assert.equal(l.workers, -1, 'a stale worker cap must be ignored');
  assert.equal(l.scheduled_executions_monthly, -1, 'a stale execution cap must be ignored');
  // The worker SAFETY knobs stay tunable — they are infra limits, not a paywall.
  const tuned = limitsFor('pro', JSON.stringify({ worker_max_concurrency: 2 }));
  assert.equal(tuned.worker_max_concurrency, 2);
});

// ── effectivePlan: state → entitlements ──────────────────────────────────────

const NOW = new Date('2026-08-01T12:00:00Z');

test('no subscription / PENDING / unknown ⇒ free', () => {
  assert.equal(effectivePlan(null, NOW).effectiveKey, 'free');
  assert.equal(effectivePlan({ planKey: 'pro', status: 'PENDING' }, NOW).effectiveKey, 'free');
  assert.equal(effectivePlan({ planKey: 'nope', status: 'ACTIVE' }, NOW).effectiveKey, 'free');
});

test('a row on a RETIRED key resolves to free regardless of status', () => {
  for (const legacy of ['starter', 'operator']) {
    const p = effectivePlan({ planKey: legacy, status: 'ACTIVE', currentPeriodEnd: new Date('2026-08-20T00:00:00Z') }, NOW);
    assert.equal(p.effectiveKey, 'free', legacy);
  }
});

test('ACTIVE ⇒ paid limits, no warning', () => {
  const p = effectivePlan({ planKey: 'developer', status: 'ACTIVE', currentPeriodEnd: new Date('2026-08-20T00:00:00Z') }, NOW);
  assert.equal(p.effectiveKey, 'developer');
  assert.equal(p.billingWarning, false);
  assert.equal(p.blockNewPaidResources, false);
  // A hand-minted enterprise subscription row grants enterprise the same way.
  const e = effectivePlan({ planKey: 'enterprise', status: 'ACTIVE', currentPeriodEnd: new Date('2026-08-20T00:00:00Z') }, NOW);
  assert.equal(e.effectiveKey, 'enterprise');
});

test('PAST_DUE / GRACE_PERIOD keep paid limits but warn + block new paid resources', () => {
  for (const status of ['PAST_DUE', 'GRACE_PERIOD']) {
    const p = effectivePlan({ planKey: 'pro', status }, NOW);
    assert.equal(p.effectiveKey, 'pro', status);
    assert.equal(p.billingWarning, true, status);
    assert.equal(p.blockNewPaidResources, true, status);
  }
});

test('SUSPENDED ⇒ free limits, subscribed plan remembered', () => {
  const p = effectivePlan({ planKey: 'business', status: 'SUSPENDED' }, NOW);
  assert.equal(p.effectiveKey, 'free');
  assert.equal(p.subscribedKey, 'business');
  assert.equal(p.state, 'SUSPENDED');
});

test('CANCELED keeps paid access until the paid-through date, then free', () => {
  const before = effectivePlan(
    { planKey: 'pro', status: 'CANCELED', currentPeriodEnd: new Date('2026-08-15T00:00:00Z') },
    NOW,
  );
  assert.equal(before.effectiveKey, 'pro');
  assert.equal(before.blockNewPaidResources, true);
  const after = effectivePlan(
    { planKey: 'pro', status: 'CANCELED', currentPeriodEnd: new Date('2026-07-15T00:00:00Z') },
    NOW,
  );
  assert.equal(after.effectiveKey, 'free');
});

test('stale ACTIVE (period ended >7d ago, webhooks lost) fails safe to free', () => {
  const p = effectivePlan(
    { planKey: 'pro', status: 'ACTIVE', currentPeriodEnd: new Date('2026-07-20T00:00:00Z') },
    NOW,
  );
  assert.equal(p.effectiveKey, 'free');
  assert.equal(p.state, 'SUSPENDED');
  // ...but a recently-ended period (grace for webhook lag) keeps entitlements.
  const fresh = effectivePlan(
    { planKey: 'pro', status: 'ACTIVE', currentPeriodEnd: new Date('2026-07-30T00:00:00Z') },
    NOW,
  );
  assert.equal(fresh.effectiveKey, 'pro');
});

// ── computeSubPatch: webhook event → state transition ────────────────────────

test('ACTIVATED and SALE.COMPLETED restore ACTIVE and clear dunning', () => {
  for (const type of ['BILLING.SUBSCRIPTION.ACTIVATED', 'PAYMENT.SALE.COMPLETED']) {
    const p = computeSubPatch(type, 'PAST_DUE', NOW, 7)!;
    assert.equal(p.status, 'ACTIVE', type);
    assert.equal(p.graceUntil, null, type);
    assert.equal(p.failedPayments, 'reset', type);
  }
});

test('payment failure escalates PAST_DUE → GRACE_PERIOD with a grace window', () => {
  const first = computeSubPatch('BILLING.SUBSCRIPTION.PAYMENT.FAILED', 'ACTIVE', NOW, 7)!;
  assert.equal(first.status, 'PAST_DUE');
  assert.equal(first.failedPayments, 'increment');
  assert.ok(first.needsAdminEmail);
  assert.equal((first.graceUntil as Date).getTime(), NOW.getTime() + 7 * 86_400_000);
  const second = computeSubPatch('BILLING.SUBSCRIPTION.PAYMENT.FAILED', 'PAST_DUE', NOW, 7)!;
  assert.equal(second.status, 'GRACE_PERIOD');
});

test('CANCELLED/EXPIRED are terminal; replays can never resurrect a CANCELED row', () => {
  for (const type of ['BILLING.SUBSCRIPTION.CANCELLED', 'BILLING.SUBSCRIPTION.EXPIRED']) {
    const p = computeSubPatch(type, 'ACTIVE', NOW, 7)!;
    assert.equal(p.status, 'CANCELED', type);
    assert.ok(p.canceledAt instanceof Date, type);
  }
  assert.equal(computeSubPatch('BILLING.SUBSCRIPTION.ACTIVATED', 'CANCELED', NOW, 7), null);
  assert.equal(computeSubPatch('PAYMENT.SALE.COMPLETED', 'CANCELED', NOW, 7), null);
});

test('clawbacks (refund/reversal/dispute) suspend + flag for the operator', () => {
  for (const type of ['PAYMENT.SALE.REFUNDED', 'PAYMENT.SALE.REVERSED', 'CUSTOMER.DISPUTE.CREATED', 'CUSTOMER.DISPUTE.UPDATED']) {
    const p = computeSubPatch(type, 'ACTIVE', NOW, 7)!;
    assert.equal(p.status, 'SUSPENDED', type);
    assert.ok(p.needsAdminEmail, type);
  }
});

test('unknown events are ignored; UPDATED is metadata-only', () => {
  assert.equal(computeSubPatch('SOMETHING.ELSE', 'ACTIVE', NOW, 7), null);
  assert.deepEqual(computeSubPatch('BILLING.SUBSCRIPTION.UPDATED', 'ACTIVE', NOW, 7), {});
});

// ── money assertion (cents-exact; inline plan-override defense) ──────────────

function paypalSub(overrides: any = {}) {
  return {
    plan_overridden: false,
    plan: {
      payment_preferences: { setup_fee: { value: '0.00', currency_code: 'USD' } },
      billing_cycles: [
        {
          tenure_type: 'REGULAR',
          pricing_scheme: { fixed_price: { value: '99.00', currency_code: 'USD' } },
        },
      ],
    },
    ...overrides,
  };
}

test('exact cents price passes; anything else fails (new catalog prices)', () => {
  assert.ok(assertSubscriptionMoneyCents(paypalSub(), 9900).ok);
  assert.ok(!assertSubscriptionMoneyCents(paypalSub(), 2900).ok);
  const dev = paypalSub();
  dev.plan.billing_cycles[0].pricing_scheme.fixed_price.value = '29.00';
  assert.ok(assertSubscriptionMoneyCents(dev, 2900).ok);
  const biz = paypalSub();
  biz.plan.billing_cycles[0].pricing_scheme.fixed_price.value = '499.00';
  assert.ok(assertSubscriptionMoneyCents(biz, 49900).ok);
  // The float trap: 0.1+0.2 style — a .99 legacy price must still compare cents-exactly
  // (grandfathered subscribers keep old plan prices after the catalog change).
  const legacy = paypalSub();
  legacy.plan.billing_cycles[0].pricing_scheme.fixed_price.value = '9.99';
  assert.ok(assertSubscriptionMoneyCents(legacy, 999).ok);
});

test('plan_overridden / non-USD / setup fee / missing snapshot handling', () => {
  assert.ok(!assertSubscriptionMoneyCents(paypalSub({ plan_overridden: true }), 9900).ok);
  const eur = paypalSub();
  eur.plan.billing_cycles[0].pricing_scheme.fixed_price.currency_code = 'EUR';
  assert.ok(!assertSubscriptionMoneyCents(eur, 9900).ok);
  const fee = paypalSub();
  fee.plan.payment_preferences.setup_fee.value = '5.00';
  assert.ok(!assertSubscriptionMoneyCents(fee, 9900).ok);
  // No inline plan snapshot => stored plan pricing applies => ok.
  assert.ok(assertSubscriptionMoneyCents({ plan_overridden: false }, 9900).ok);
});

test('centsToUsdString', () => {
  assert.equal(centsToUsdString(2900), '29.00');
  assert.equal(centsToUsdString(49900), '499.00');
  assert.equal(centsToUsdString(999), '9.99');
  assert.equal(centsToUsdString(0), '0.00');
});

// ── PAYMENT.SALE.COMPLETED → BillingPayment draft (pure) ─────────────────────

test('salePaymentFromResource extracts a cents-exact, deterministic revenue draft', () => {
  const resource = {
    id: 'SALE-8XU12345AB',
    amount: { total: '29.00', currency: 'USD' },
    create_time: '2026-08-01T10:00:00Z',
  };
  const d = salePaymentFromResource(resource)!;
  assert.equal(d.paypalCaptureId, 'SALE-8XU12345AB');
  assert.equal(d.amountCents, 2900);
  assert.equal(d.currency, 'USD');
  assert.equal(d.occurredAt.toISOString(), '2026-08-01T10:00:00.000Z');
  // Float trap: 9.99 must land on 999 cents exactly.
  assert.equal(salePaymentFromResource({ id: 'S1', amount: { total: '9.99', currency: 'USD' } })!.amountCents, 999);
});

test('duplicate deliveries of one sale map to ONE capture id (the @unique dedup anchor)', () => {
  // PayPal may redeliver the same sale under a fresh webhook event id; the draft must be
  // deterministic on the sale so the DB unique constraint makes revenue exactly-once.
  const mk = () => ({ id: 'SALE-DUP-1', amount: { total: '99.00', currency: 'USD' }, create_time: '2026-08-01T10:00:00Z' });
  const a = salePaymentFromResource(mk())!;
  const b = salePaymentFromResource(mk())!;
  assert.equal(a.paypalCaptureId, b.paypalCaptureId);
  assert.equal(a.amountCents, b.amountCents);
});

test('salePaymentFromResource refuses unprovable charges', () => {
  assert.equal(salePaymentFromResource(null), null);
  assert.equal(salePaymentFromResource({}), null);
  assert.equal(salePaymentFromResource({ id: '', amount: { total: '29.00', currency: 'USD' } }), null);
  assert.equal(salePaymentFromResource({ id: 'S1', amount: { total: '0.00', currency: 'USD' } }), null);
  assert.equal(salePaymentFromResource({ id: 'S1', amount: { total: '-5.00', currency: 'USD' } }), null);
  assert.equal(salePaymentFromResource({ id: 'S1', amount: { total: 'abc', currency: 'USD' } }), null);
  assert.equal(salePaymentFromResource({ id: 'S1', amount: { total: '29.00' } }), null);
});

// ── periods + scheduling helpers ─────────────────────────────────────────────

test('periodKeyFor is a UTC calendar month bucket', () => {
  assert.equal(periodKeyFor(new Date('2026-08-01T00:00:00Z')), '2026-08');
  assert.equal(periodKeyFor(new Date('2026-08-31T23:59:59Z')), '2026-08');
  assert.equal(periodKeyFor(new Date('2026-09-01T00:00:00Z')), '2026-09');
  // Local-time trap: 00:30 UTC on the 1st is still the new month regardless of TZ.
  assert.equal(periodKeyFor(new Date('2026-12-01T00:30:00Z')), '2026-12');
});

test('clampIntervalMinutes respects the plan floor and sane bounds', () => {
  assert.equal(clampIntervalMinutes(5, PLAN_DEFAULTS.free), 60); // free floor: hourly
  assert.equal(clampIntervalMinutes(5, PLAN_DEFAULTS.developer), 15);
  assert.equal(clampIntervalMinutes(30, PLAN_DEFAULTS.developer), 30);
  assert.equal(clampIntervalMinutes(1, PLAN_DEFAULTS.pro), 5);
  assert.equal(clampIntervalMinutes(NaN, PLAN_DEFAULTS.developer), 15);
  assert.equal(clampIntervalMinutes(1, PLAN_DEFAULTS.enterprise), 1);
  assert.equal(clampIntervalMinutes(10_000_000, PLAN_DEFAULTS.business), 60 * 24 * 31);
});

test('nextRunAfter adds interval minutes', () => {
  const from = new Date('2026-08-01T00:00:00Z');
  assert.equal(nextRunAfter(15, from).getTime(), from.getTime() + 15 * 60_000);
});

test('safetyCaps: env-tunable with sane defaults', () => {
  const prev = process.env.WORKER_MAX_RUN_SECONDS;
  delete process.env.WORKER_MAX_RUN_SECONDS;
  assert.equal(safetyCaps().maxRunSeconds, 300);
  process.env.WORKER_MAX_RUN_SECONDS = '120';
  assert.equal(safetyCaps().maxRunSeconds, 120);
  process.env.WORKER_MAX_RUN_SECONDS = '-5'; // nonsense → default
  assert.equal(safetyCaps().maxRunSeconds, 300);
  if (prev === undefined) delete process.env.WORKER_MAX_RUN_SECONDS;
  else process.env.WORKER_MAX_RUN_SECONDS = prev;
});
