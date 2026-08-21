'use strict';
/**
 * POST /x402/buy — Animica buys somebody else's x402 call on your behalf.
 *
 * An agent that has found a service it wants but does not hold USDC, does not
 * want to implement EIP-3009, or does not want a spendable key inside its own
 * loop hands us the resource and we buy it. The result comes back with what it
 * actually cost.
 *
 * This is the one endpoint in the gateway that spends money outward, so the
 * controls ARE the product:
 *
 *  - **Off unless a DEDICATED spender key is configured.** No key, no buying,
 *    and the product is absent from the catalog rather than present in a broken
 *    state that looks purchasable.
 *  - **It refuses to spend from the facilitator key.** The key that settles our
 *    incoming payments must not be the key that spends; one confused purchase
 *    must not be able to drain the float every product settles through. This is
 *    checked when the product is built and again before every signature.
 *  - **Three ceilings, all enforced BEFORE signing**: the caller's own limit for
 *    this purchase, the operator's per-purchase cap, and a per-day total held in
 *    SQLite — a daily cap that lives only in memory is not a cap, because a
 *    restart resets it.
 *  - **Settle first, then spend.** The caller pays Animica before we spend our
 *    own USDC, so a failed collection cannot leave us out of pocket. That is why
 *    this product does not use execute-then-settle.
 */

const dns = require('node:dns').promises;
const { ProductError, ProductUnavailable } = require('./errors');
const { createPayer, PayerError } = require('../payer');
const { parseTarget, resolveSafely } = require('./web');

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

function utcDay(ms) {
  return new Date(ms).toISOString().slice(0, 10);
}

function createBuyProduct({ cfg, gatewayStore, fetchImpl = fetch, now = Date.now, lookup = dns.lookup, logger = null }) {
  // Construction-time refusal: if the operator pointed the spender at the
  // facilitator key, this product does not exist rather than existing unsafely.
  let payer = null;
  let disabledReason = null;
  if (!cfg.execPrivateKey) {
    disabledReason = 'no dedicated spender key is configured (X402_EXEC_PRIVATE_KEY)';
  } else {
    try {
      payer = createPayer({
        privateKeyHex: cfg.execPrivateKey,
        forbiddenAddresses: [cfg.facilitatorSpendGuardAddress].filter(Boolean),
        fetchImpl,
        now,
        timeoutMs: Number(cfg.execTimeoutMs),
      });
    } catch (e) {
      disabledReason = e.message;
      logger && logger.warn && logger.warn('exec_payer_disabled', { code: e.code || 'error' });
    }
  }

  return {
    id: 'buy_x402',
    title: 'Buy an x402 call on your behalf',
    description:
      "Hand Animica any x402 resource and we buy it for you, returning what it delivered and exactly what it cost. For an agent that has found a service it wants but does not hold USDC, does not want to implement EIP-3009 signing, or does not want a spendable key inside its own loop. Every purchase is bounded before anything is signed: your own per-purchase limit, an operator per-purchase cap, and a per-day total held in a database rather than in memory. We buy from a wallet funded for this and nothing else, never from the key that settles Animica's own incoming payments — that combination is refused outright. Run POST /x402/mesh/probe first if you want to read a resource's terms before committing to them.",
    path: '/x402/buy',
    routes: [{ method: 'POST', path: '/x402/buy' }],
    priceUsd: cfg.execFeeUsd,
    enabled: Boolean(cfg.execEnabled && payer),
    mimeType: 'application/json',
    maxBodyBytes: 64 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          resource: { type: 'string', required: true, description: 'absolute http(s) URL of the x402 resource to buy' },
          method: { type: 'string', required: false, description: 'GET or POST (default GET)' },
          body: { type: 'object', required: false, description: 'request body when the resource takes a POST' },
          max_spend_usd: { type: 'number', required: false, description: `your own ceiling for this one purchase; it can only tighten the operator cap of $${cfg.execMaxPerCallUsd}, never loosen it` },
          dry_run: { type: 'boolean', required: false, description: 'ask the merchant for its terms and return them without signing or paying' },
        },
      },
      output: {
        type: 'json',
        description:
          'purchased, spent_usd, quoted_usd, paid_to, network, settled_by_merchant, spender_address, result {status, content_type, json|body}, budget {per_call_cap_usd, daily_cap_usd, spent_today_usd, remaining_today_usd}',
      },
    },

    async availability() {
      if (!payer) return { available: false, reason: 'buying_disabled', detail: disabledReason };
      return { available: true };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      if (typeof b.resource !== 'string' || !b.resource.trim()) {
        throw bad('resource is required and must be an absolute http(s) URL', 'invalid_request');
      }
      const u = parseTarget(b.resource.trim());
      let method = 'GET';
      if (b.method !== undefined) {
        const m = String(b.method).toUpperCase();
        if (!['GET', 'POST'].includes(m)) throw bad('method must be GET or POST', 'invalid_request');
        method = m;
      }
      let ceiling = Number(cfg.execMaxPerCallUsd);
      if (b.max_spend_usd !== undefined) {
        const v = Number(b.max_spend_usd);
        if (!Number.isFinite(v) || v <= 0) throw bad('max_spend_usd must be a positive number', 'invalid_request');
        // A caller may only tighten the operator's cap, never raise it.
        ceiling = Math.min(ceiling, v);
      }
      return { resource: u.toString(), method, body: b.body ?? null, ceiling, dryRun: b.dry_run === true };
    },

    async handler(ctx) {
      if (!payer) throw new ProductUnavailable('buying_disabled', disabledReason || 'buying is not configured');
      const { resource, method, body, ceiling, dryRun } = ctx.params;

      // The target is caller-supplied, so it gets the same SSRF guard as every
      // other URL we take. Spending money at an address inside our own network
      // would be a novel kind of bad.
      const u = parseTarget(resource);
      try {
        await resolveSafely(u.hostname, lookup);
      } catch (e) {
        throw bad(`refusing to buy from ${u.hostname}: ${e.message}`, 'blocked_host');
      }

      const day = utcDay(now());
      const dailyCap = Number(cfg.execMaxPerDayUsd);
      const before = gatewayStore ? gatewayStore.execSpentToday(day) : { total: 0, calls: 0 };
      const remaining = dailyCap - before.total;

      const budgetOf = (s) => ({
        per_call_cap_usd: ceiling,
        operator_per_call_cap_usd: Number(cfg.execMaxPerCallUsd),
        daily_cap_usd: dailyCap,
        spent_today_usd: Math.round(s.total * 1e6) / 1e6,
        purchases_today: s.calls,
        remaining_today_usd: Math.round(Math.max(0, dailyCap - s.total) * 1e6) / 1e6,
      });

      if (!dryRun && remaining <= 0) {
        throw bad(
          `the daily buying cap of $${dailyCap} is spent ($${before.total.toFixed(6)} across ${before.calls} purchases today). `
          + 'Nothing was signed. It resets at 00:00 UTC.',
          'daily_cap_reached',
          { budget: budgetOf(before) },
        );
      }

      // The effective ceiling is the tighter of the per-call limit and what is
      // left today, so a single purchase can never overshoot the daily cap.
      const effective = dryRun ? ceiling : Math.min(ceiling, remaining);

      let out;
      try {
        out = await payer.buy({ resource, method, body, maxSpendUsd: effective, dryRun });
      } catch (e) {
        if (e instanceof PayerError) {
          // A purchase that settled but did not deliver still spent money, and
          // the ledger has to know that or the daily cap is wrong tomorrow.
          if (gatewayStore && e.detail && Number(e.detail.spent_usd) > 0) {
            try {
              gatewayStore.recordExecSpend({
                day, resource, spent_usd: String(e.detail.spent_usd), outcome: 'paid',
                request_id: ctx.requestId || null, spent_at: Math.floor(now() / 1000),
              });
            } catch { /* the ledger must not mask the underlying error */ }
          }
          // NOT `detail:` — bad() puts the message there, and an extra key of
          // the same name silently replaced the human-readable explanation with
          // a bare object. The caller needs to be told why, not handed numbers.
          throw bad(e.message, e.code, { budget: budgetOf(before), payer_detail: e.detail || undefined });
        }
        throw e;
      }

      if (out.paid && gatewayStore) {
        gatewayStore.recordExecSpend({
          day, resource, spent_usd: String(out.spent_usd), outcome: 'paid',
          request_id: ctx.requestId || null, spent_at: Math.floor(now() / 1000),
        });
      }
      const after = gatewayStore ? gatewayStore.execSpentToday(day) : before;

      let parsed = null;
      if (out.body && String(out.content_type || '').includes('json')) {
        try { parsed = JSON.parse(out.body); } catch { /* fall back to text */ }
      }

      return {
        status: 200,
        bodyObj: {
          product: 'buy_x402',
          resource,
          purchased: Boolean(out.paid),
          dry_run: Boolean(out.dry_run),
          spent_usd: out.spent_usd ?? 0,
          quoted_usd: out.quoted_usd ?? out.spent_usd ?? null,
          paid_to: out.paid_to || out.would_pay_to || null,
          network: out.network || null,
          settled_by_merchant: out.settled ?? null,
          spender_address: payer.address,
          result: (out.paid || out.status) ? {
            status: out.status ?? null,
            content_type: out.content_type ?? null,
            json: parsed,
            body: parsed ? undefined : (out.body ?? null),
          } : null,
          note: out.note || null,
          budget: budgetOf(after),
          disclosure:
            'The amount above came from an Animica wallet funded for buying and nothing else. It is never the key that '
            + "settles Animica's incoming payments — that combination is refused outright. Your payment to Animica covers "
            + 'the service of buying; the downstream cost is stated so the economics are legible rather than hidden in a spread.',
          generated_at: new Date(now()).toISOString(),
        },
      };
    },
  };
}

module.exports = { createBuyProduct, utcDay };
