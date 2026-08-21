'use strict';
/**
 * Free trials — let an agent evaluate a product before it spends anything.
 *
 * THE PROBLEM THIS SOLVES. An autonomous buyer arriving at a 402 has to decide
 * whether to pay before it has ever seen what it would get. The catalog
 * describes the output shape, but a description is not a sample: it cannot show
 * that the service is actually up, how long it takes, or whether the quality is
 * worth the price. Every one of this gateway's payments to date has come from
 * our own test wallet, and the measured blocker was never price — it was that
 * nothing external ever got far enough to form an opinion.
 *
 * THE DESIGN. Each eligible product gains ONE extra route, `<path>/trial`,
 * outside the paywall. It runs the SAME validate → availability → handler chain
 * as the paid route, so the trial response is the real thing, not a mock —
 * a mock would defeat the purpose, since the agent is trying to learn what it
 * would actually receive. Trials are capped per client per UTC day.
 *
 * WHY THE CAP IS DELIBERATELY WEAK. The quota key is the client IP, which an
 * agent can rotate. That is accepted: the cap exists to stop casual over-use of
 * a sample, not to be an authorization boundary. What makes it safe is that
 * eligibility is opt-in per product and the caps are sized to what a product
 * costs US to serve — generous for a deterministic node read, one per day for a
 * GPU render.
 *
 * WHAT A TRIAL MUST NEVER DO: take a payment, create a paid-side effect, or
 * report success for a service that is down. It reuses the product's own
 * availability gate, so a product that would 503 a payer also 503s a trial.
 */

const { ProductError, teachingError } = require('./errors');

/** UTC day key. Trials reset at midnight UTC with no sweeper. */
function utcDay(nowMs) {
  return new Date(nowMs).toISOString().slice(0, 10);
}

/**
 * Client identity for quota purposes. Prefers the left-most X-Forwarded-For
 * entry (nginx sits in front), falling back to the socket address. Deliberately
 * NOT a fingerprint: see the module note on why this control is weak by design.
 */
function clientKey(ctx) {
  const h = (ctx && ctx.headers) || {};
  const xff = h['x-forwarded-for'] || h['X-Forwarded-For'];
  if (typeof xff === 'string' && xff.trim()) return xff.split(',')[0].trim();
  const real = h['x-real-ip'] || h['X-Real-IP'];
  if (typeof real === 'string' && real.trim()) return real.trim();
  return (ctx && ctx.remoteAddress) || 'unknown';
}

/**
 * Build the free-trial route for one product.
 *
 * `limitPerDay` is the number of unpaid calls one client may make per UTC day.
 * 0 disables the trial entirely (the route is not created).
 */
function createTrialRoute({ product, cfg, gatewayStore, limitPerDay, now = Date.now, logger = null }) {
  const log = logger || { error() {} };
  const cap = Math.max(0, Number(limitPerDay) || 0);
  if (!cap || !gatewayStore) return null;

  const paidPath = product.path;
  const trialPath = `${paidPath}/trial`;
  // Accept the path with and without the /x402 prefix: nginx may strip it.
  const bare = trialPath.replace(/^\/x402/, '');
  const method = (product.routes && product.routes[0] && product.routes[0].method) || 'POST';

  return {
    method,
    path: trialPath,
    // A trial takes exactly the paid route's input, so it publishes exactly the
    // paid route's schema. Without this the OpenAPI described 27 POST trials
    // with no body at all — the free sample an agent tries first was the one
    // endpoint it could not construct a call to.
    bodyFields: (product.outputSchema && product.outputSchema.input
      && product.outputSchema.input.bodyFields) || undefined,
    description:
      `FREE TRIAL of ${product.id}: up to ${cap} call${cap === 1 ? '' : 's'} per client per UTC day, ` +
      `no payment. Runs the same code as the paid endpoint and returns the same response shape, ` +
      `so you can check the service is live and judge the output before paying. ` +
      `When the quota is spent this answers 402-equivalent guidance pointing at ${paidPath}.`,

    match(pathname) {
      const p = String(pathname || '').replace(/\/+$/, '') || '/';
      return (p === trialPath || p === bare) ? {} : null;
    },

    async handler(ctxIn) {
      // The trial promises to run the SAME chain as the paid route, so it must
      // hand the product the same ctx shape. `route` was missing: the paywall
      // sets it and products read it (bulk-chain picks its export type from
      // `ctx.route.path`), so /x402/chain/export/trial threw a TypeError inside
      // validate() — reported to the caller as `invalid_request` with a raw JS
      // message. It mirrors the PAID route, which is what validate() expects to
      // be looking at.
      const ctx = { route: { path: paidPath, method }, ...ctxIn };

      // 1. Availability FIRST — never hand out a free sample of a dead service
      //    and let the agent conclude the product is broken.
      let avail;
      try {
        avail = await product.cachedAvailability();
      } catch (e) {
        avail = { available: false, reason: 'availability_check_failed', detail: e.message };
      }
      if (!avail || avail.available === false) {
        return {
          status: 503,
          bodyObj: {
            error: 'trial_unavailable',
            product: product.id,
            reason: avail && avail.reason,
            detail: (avail && avail.detail) || 'the product is not currently serving',
            paid_endpoint: paidPath,
            note: 'the paid endpoint is refusing for the same reason — you are not being singled out',
          },
        };
      }

      // 2. Validate BEFORE spending quota, so a malformed request does not
      //    burn a trial the agent never got value from.
      let validated = {};
      if (typeof product.validate === 'function') {
        try {
          validated = product.validate(ctx) || {};
        } catch (e) {
          // ONLY a ProductError is the caller's fault. Anything else is a bug
          // in our own validate(), and calling it `invalid_request` sends the
          // agent off to fix a request that was fine while echoing a raw JS
          // message back over the wire. 500, no internals, no quota spent.
          if (!(e instanceof ProductError)) {
            log.error && log.error('trial_validate_crashed', { product: product.id, error: e.message });
            return {
              status: 500,
              bodyObj: {
                error: 'trial_error',
                detail: 'the trial failed inside this service, not in your request',
                product: product.id,
                trial: true,
                quota_spent: false,
              },
            };
          }
          const body = e.body || { error: 'invalid_request', detail: e.message };
          // Same rule as the paid path: a refusal must teach, not dead-end.
          return {
            status: e.status || 400,
            bodyObj: teachingError(product, { ...body, trial: true, quota_spent: false }, { trial: true }),
          };
        }
      }

      // 3. READINESS + PINNING. The trial used to skip preSettle() on the
      //    grounds that it guards money and no money moves here. That reading
      //    was wrong for this codebase: every preSettle is a read that also
      //    RETURNS state the handler then needs — bulk_chain, chain_balances,
      //    notary and lease all read `ctx.pinned.*`. Skipping it left those
      //    handlers dereferencing an absent `ctx.pinned`, which is a live 502
      //    on /x402/chain/export/trial ("Cannot read properties of undefined
      //    (reading 'head')"). None of the implementations reserve, lease or
      //    spend anything, so running it costs nothing.
      //
      //    Run BEFORE the quota is spent: a readiness refusal must cost the
      //    caller nothing, exactly as refusing pre-settlement costs a payer
      //    nothing.
      const ready = { ...ctx, ...validated, params: validated, trial: true };
      if (typeof product.preSettle === 'function') {
        try {
          ready.pinned = (await product.preSettle(ready)) || {};
        } catch (e) {
          const body = (e instanceof ProductError && e.body)
            ? e.body
            : { error: 'trial_unavailable', detail: 'the product is not ready to serve right now' };
          if (!(e instanceof ProductError)) {
            log.error && log.error('trial_presettle_crashed', { product: product.id, error: e.message });
          }
          return {
            status: e.status || 503,
            bodyObj: { ...body, product: product.id, trial: true, quota_spent: false, paid_endpoint: paidPath },
          };
        }
      }

      // 4. Spend one trial.
      const key = clientKey(ctx);
      const day = utcDay(now());
      const spend = gatewayStore.consumeTrial(product.id, key, day, cap);
      if (!spend.allowed) {
        return {
          status: 429,
          headers: { 'retry-after': String(secondsToUtcMidnight(now())) },
          bodyObj: {
            error: 'trial_quota_exhausted',
            product: product.id,
            limit_per_day: cap,
            // Clamped to the cap: the underlying counter keeps rising on
            // refused attempts (useful as an abuse signal in the DB), but
            // reporting "used 7 of 3" back to a caller is just confusing.
            used: Math.min(spend.used, cap),
            resets_at: `${utcDay(now() + 86400000)}T00:00:00Z`,
            detail: `you have used all ${cap} free trial call${cap === 1 ? '' : 's'} for ${product.id} today`,
            next_step: {
              paid_endpoint: paidPath,
              price_usd: product.priceUsd,
              // The paid route's OWN method — a GET product is not bought with a POST.
              how: `${method} ${paidPath} without payment to receive the 402 challenge and its payment terms`,
            },
          },
        };
      }

      // 5. Run the real handler — on the SAME ctx preSettle saw, pin included.
      try {
        // `params` is how the PAID path hands validated input to a handler
        // (paywall.js sets ctx.params = product.validate(ctx)), and every
        // product reads it from there. Spreading the validated fields flat and
        // omitting `params` meant trials silently ran with NO caller input:
        // /x402/qrng/draw/trial?bytes=8 returned the 32-byte default and
        // /x402/random/int/trial returned an empty array. Nothing threw, so
        // the free sample every agent tries first quietly answered the wrong
        // question. The flat spread is kept for compatibility with anything
        // reading a validated field straight off ctx.
        const out = await product.handler(ready);
        const headers = {
          ...(out && out.headers),
          'x-x402-trial': 'true',
          'x-x402-trial-remaining': String(spend.remaining),
          'x-x402-paid-endpoint': paidPath,
        };
        return { ...out, headers };
      } catch (e) {
        // The trial was spent on a failed call. Refunding the quota would let a
        // client farm failures to reset it, so it stays spent — but say so
        // plainly rather than leaving the agent to guess.
        const body = (e instanceof ProductError && e.body)
          ? e.body
          : { error: 'trial_failed', detail: e.message };
        return {
          status: e.status || 502,
          bodyObj: { ...body, trial: true, quota_spent: true, trial_remaining: spend.remaining },
        };
      }
    },
  };
}

function secondsToUtcMidnight(nowMs) {
  const next = Date.UTC(
    new Date(nowMs).getUTCFullYear(),
    new Date(nowMs).getUTCMonth(),
    new Date(nowMs).getUTCDate() + 1,
  );
  return Math.max(1, Math.ceil((next - nowMs) / 1000));
}

module.exports = { createTrialRoute, utcDay, clientKey };
