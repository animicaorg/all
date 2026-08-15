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

module.exports = { ProductError, ProductUnavailable };
