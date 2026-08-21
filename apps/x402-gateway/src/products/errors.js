'use strict';
/** Typed errors products throw; src/paywall.js maps them to HTTP. */

/** Client-shaped request problem: 400 before any payment is requested. */
class ProductError extends Error {
  constructor(message, { status = 400, body } = {}) {
    super(message);
    this.name = 'ProductError';
    this.status = status;
    this.body = body;
    this.retryable = false;
  }
}

/** Product cannot serve right now: 503, never a 402, nothing charged. */
class ProductUnavailable extends Error {
  constructor(reason, detail) {
    super(detail || reason);
    this.name = 'ProductUnavailable';
    this.reason = reason;
    this.retryable = false;
  }
}

/**
 * A validation refusal that TEACHES. Refusing is right — we do not quote a
 * price for a request we would reject — but "input is required" alone names one
 * field, quotes nothing, and points nowhere, so a caller that does not know the
 * shape has nothing to correct towards. Observed live: one agent repeated the
 * same wrong body 77 times over two days and never learned either the schema or
 * the price.
 *
 * Purely additive: the caller-facing error/detail keys are never overwritten,
 * and no `payment-required` header is emitted — this is a refusal, not an offer.
 * `trial` suppresses the free-trial pointer, which is noise to someone already
 * inside the trial.
 */
function teachingError(product, body, { trial = false } = {}) {
  const out = Object.assign({}, body);
  const input = (product.outputSchema && product.outputSchema.input) || null;
  if (input && out.input_schema === undefined) out.input_schema = input;
  if (out.price_usd === undefined && product.priceUsd) out.price_usd = product.priceUsd;
  if (!trial && out.free_trial === undefined && product.trialLimitPerDay) {
    const method = (product.routes && product.routes[0] && product.routes[0].method) || 'POST';
    out.free_trial = {
      endpoint: `${method} ${product.path}/trial`,
      limit_per_day: product.trialLimitPerDay,
    };
  }
  if (out.catalog === undefined) out.catalog = '/.well-known/x402';
  return out;
}

module.exports = { ProductError, ProductUnavailable, teachingError };
