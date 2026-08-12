// Regression tests for the billing/entitlement defects found in the pre-deploy adversarial
// review (2026-08-01), carried forward onto the Python Cloud catalog, plus the pins added
// with the catalog change (retired-tier degradation, un-gateable workers, BillingPayment
// idempotency). Each test names the bypass it locks out — if one of these fails, a paid tier
// can be obtained (or retained) without paying for it, or USD revenue can be double-counted.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { computeSubPatch, effectivePlan, limitsFor, salePaymentFromResource } from '../lib/planConfig';
import { isPrivateAddress, validateWebhookUrl } from '../lib/workers';

const NOW = new Date('2026-08-01T12:00:00Z');

// Finding: PAYMENT.FAILED on a SUSPENDED row moved it to PAST_DUE — i.e. a FAILED payment
// restored paid limits and opened a fresh grace window on a clawback/abuse suspension.
test('a failed payment can never improve a SUSPENDED subscription', () => {
  const patch = computeSubPatch('BILLING.SUBSCRIPTION.PAYMENT.FAILED', 'SUSPENDED', NOW, 7)!;
  assert.equal(patch.status, undefined, 'must not change status');
  assert.equal(patch.graceUntil, undefined, 'must not grant a grace window');
  assert.equal(patch.failedPayments, 'increment');
  assert.ok(patch.needsAdminEmail);
  // Only a real charge heals a suspension.
  assert.equal(computeSubPatch('PAYMENT.SALE.COMPLETED', 'SUSPENDED', NOW, 7)!.status, 'ACTIVE');
  assert.equal(computeSubPatch('BILLING.SUBSCRIPTION.ACTIVATED', 'SUSPENDED', NOW, 7)!.status, 'ACTIVE');
});

// Finding: cancelling a SUSPENDED (refunded/charged-back) subscription produced a CANCELED row
// whose future currentPeriodEnd granted FULL paid limits again. The cancel routes now end the
// entitlement window; this test pins the effectivePlan semantics that make that fix work.
test('CANCELED grants paid access only while the paid-through date is in the future', () => {
  const ended = effectivePlan(
    { planKey: 'pro', status: 'CANCELED', currentPeriodEnd: new Date(NOW.getTime() - 1000) },
    NOW,
  );
  assert.equal(ended.effectiveKey, 'free', 'a zeroed period must drop to free immediately');

  const stillPaid = effectivePlan(
    { planKey: 'pro', status: 'CANCELED', currentPeriodEnd: new Date(NOW.getTime() + 5 * 86_400_000) },
    NOW,
  );
  assert.equal(stillPaid.effectiveKey, 'pro', 'an honest cancel keeps the period already paid for');
  assert.equal(stillPaid.blockNewPaidResources, true);
});

// Finding: a SUSPENDED row must never grant paid limits on its own.
test('SUSPENDED always means free limits regardless of period end', () => {
  const p = effectivePlan(
    { planKey: 'business', status: 'SUSPENDED', currentPeriodEnd: new Date(NOW.getTime() + 30 * 86_400_000) },
    NOW,
  );
  assert.equal(p.effectiveKey, 'free');
  assert.equal(p.subscribedKey, 'business');
});

// Finding: the webhook/confirm paths could adopt a HIGHER tier from a PayPal revision, which
// charges nothing until the next cycle. The rank comparison that guards this must hold.
test('CANCELED is terminal: replayed events cannot resurrect it', () => {
  for (const type of ['BILLING.SUBSCRIPTION.ACTIVATED', 'PAYMENT.SALE.COMPLETED', 'BILLING.SUBSCRIPTION.PAYMENT.FAILED']) {
    assert.equal(computeSubPatch(type, 'CANCELED', NOW, 7), null, type);
  }
});

// ── Python Cloud catalog pins (2026-08 catalog change) ───────────────────────

// A subscription row left on a RETIRED tier key must degrade to free limits — never keep the
// old paid grants, and never be confused with a new-catalog tier of a similar rank.
test('retired-tier rows (starter/operator) resolve to free in every paid state', () => {
  for (const legacy of ['starter', 'operator']) {
    for (const status of ['ACTIVE', 'PAST_DUE', 'GRACE_PERIOD']) {
      const p = effectivePlan(
        { planKey: legacy, status, currentPeriodEnd: new Date(NOW.getTime() + 20 * 86_400_000) },
        NOW,
      );
      assert.equal(p.effectiveKey, 'free', `${legacy}/${status}`);
    }
  }
});

// Workers were un-gated as a product decision. A stale Plan.limitsJson row from the old
// catalog (e.g. {"workers": 5}) must not be able to resurrect the paywall through the
// admin-override merge path.
test('DB limit overrides can never re-gate workers or scheduled executions', () => {
  for (const key of ['free', 'developer', 'pro', 'business', 'enterprise']) {
    const l = limitsFor(key, JSON.stringify({ workers: 0, scheduled_executions_monthly: 0, external_triggers: true }));
    assert.equal(l.workers, -1, key);
    assert.equal(l.scheduled_executions_monthly, -1, key);
  }
});

// ── BillingPayment (auditable USD revenue) idempotency ───────────────────────

// The webhook records every verified PAYMENT.SALE.COMPLETED as a BillingPayment keyed by the
// PayPal sale id (paypalCaptureId @unique, inserted with skipDuplicates). The draft builder
// must therefore be (a) deterministic on the sale — a redelivery under a fresh webhook event
// id yields the SAME key, so the DB constraint dedupes it — and (b) total: garbage resources
// must produce NO row rather than a fabricated one.
test('one PayPal sale maps to exactly one revenue row key, however often it is delivered', () => {
  const delivery1 = { id: 'SALE-REPLAY', amount: { total: '499.00', currency: 'USD' }, create_time: '2026-08-01T09:00:00Z' };
  const delivery2 = { id: 'SALE-REPLAY', amount: { total: '499.00', currency: 'USD' }, create_time: '2026-08-01T09:00:00Z' };
  const a = salePaymentFromResource(delivery1)!;
  const b = salePaymentFromResource(delivery2)!;
  assert.equal(a.paypalCaptureId, b.paypalCaptureId);
  assert.equal(a.amountCents, 49900);
  assert.equal(b.amountCents, 49900);
  // Two DIFFERENT sales never collide.
  const other = salePaymentFromResource({ id: 'SALE-OTHER', amount: { total: '499.00', currency: 'USD' } })!;
  assert.notEqual(other.paypalCaptureId, a.paypalCaptureId);
});

test('revenue rows cannot be conjured from unprovable sale resources', () => {
  for (const bad of [
    null,
    {},
    { amount: { total: '29.00', currency: 'USD' } }, // no sale id
    { id: '   ', amount: { total: '29.00', currency: 'USD' } }, // blank id
    { id: 'S1' }, // no amount
    { id: 'S1', amount: { total: '0', currency: 'USD' } }, // zero
    { id: 'S1', amount: { total: '-29.00', currency: 'USD' } }, // negative
    { id: 'S1', amount: { total: 'NaN', currency: 'USD' } },
    { id: 'S1', amount: { total: '29.00', currency: '' } }, // no currency
  ]) {
    assert.equal(salePaymentFromResource(bad), null, JSON.stringify(bad));
  }
});

// ── SSRF guard (Worker webhooks) ─────────────────────────────────────────────

test('private/loopback/link-local addresses are recognised across v4 and v6 forms', () => {
  for (const addr of [
    '127.0.0.1', '127.1.2.3', '0.0.0.0', '10.0.0.5', '192.168.1.1', '172.16.0.1', '172.31.255.255',
    '169.254.169.254', '100.64.0.1', '224.0.0.1',
    '::1', '::', 'fe80::1', 'fc00::1', 'fd12:3456::1', '::ffff:127.0.0.1',
  ]) {
    assert.ok(isPrivateAddress(addr), `${addr} must be treated as private`);
  }
  for (const addr of ['8.8.8.8', '1.1.1.1', '172.32.0.1', '192.169.0.1', '2606:4700::1111']) {
    assert.ok(!isPrivateAddress(addr), `${addr} is public`);
  }
});

// Finding: the trailing root-label dot ("localhost.") slipped past the suffix checks.
test('webhook URL validation rejects internal names, dotted variants and IP literals', () => {
  const bad = [
    'http://example.com/hook', // not https
    'https://user:pw@example.com/hook', // credentials
    'https://localhost/hook',
    'https://localhost./hook', // the reported bypass
    'https://LOCALHOST./hook',
    'https://foo.internal./hook',
    'https://box.local/hook',
    'https://127.0.0.1/hook',
    'https://169.254.169.254/latest/meta-data/',
    'https://[::1]/hook',
    'https://2130706433/hook', // bare integer form
  ];
  for (const url of bad) {
    assert.throws(() => validateWebhookUrl(url), /bad_webhook_url|webhookUrl|not allowed|https/i, url);
  }
  // A normal public endpoint still works, and the stored URL is the normalized one.
  assert.equal(validateWebhookUrl('https://hooks.example.com/animica'), 'https://hooks.example.com/animica');
  assert.equal(validateWebhookUrl('https://hooks.example.com./animica'), 'https://hooks.example.com/animica');
});
