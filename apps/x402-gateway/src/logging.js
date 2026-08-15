'use strict';
/**
 * Structured JSON logging for the gateway + facilitator. One JSON object per
 * line on stdout/stderr with a stable field set:
 *
 *   ts, level, service, event, request_id?, product?, payer?, amount?,
 *   asset?, network?, payment_id?, settlement_tx?, status?, latency_ms?, ...
 *
 * Redaction is structural and name-based (src/facilitator-evm/key.js
 * REDACT_FIELDS): any field whose NAME looks like key/signature/authorization
 * material is replaced before serialization. Never pass a raw private key as
 * a VALUE of an innocent-looking field — nothing can save a log line from
 * that, which is why key material never leaves key.js in the first place.
 *
 * X402_LOG_FORMAT=pretty switches to human console output for local dev;
 * production default is JSON lines.
 */

const { redact } = require('./facilitator-evm/key');

const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };

function createLogger({ service, level = process.env.X402_LOG_LEVEL || 'info', format = process.env.X402_LOG_FORMAT || 'json', stream = process.stdout, errStream = process.stderr } = {}) {
  const minLevel = LEVELS[level] || LEVELS.info;

  function emit(lvl, event, fields) {
    if (LEVELS[lvl] < minLevel) return;
    const out = lvl === 'error' || lvl === 'warn' ? errStream : stream;
    const record = Object.assign(
      { ts: new Date().toISOString(), level: lvl, service, event },
      redact(fields || {})
    );
    if (format === 'pretty') {
      const { ts, level: l, service: s, event: e, ...rest } = record;
      out.write(`${ts} [${l}] ${s} ${e} ${Object.keys(rest).length ? JSON.stringify(rest) : ''}\n`);
    } else {
      out.write(JSON.stringify(record) + '\n');
    }
  }

  const logger = {
    debug: (event, fields) => emit('debug', event, fields),
    info: (event, fields) => emit('info', event, fields),
    warn: (event, fields) => emit('warn', event, fields),
    error: (event, fields) => emit('error', event, fields),
    child: (bound) => {
      const c = createLogger({ service, level, format, stream, errStream });
      for (const lvl of Object.keys(LEVELS)) {
        const orig = c[lvl];
        c[lvl] = (event, fields) => orig(event, Object.assign({}, bound, fields));
      }
      return c;
    },
  };
  return logger;
}

let reqCounter = 0;
/** Cheap unique request id: time + counter (not a security token). */
function newRequestId() {
  return `req_${Date.now().toString(36)}_${(reqCounter++ & 0xffff).toString(36)}`;
}

let payCounter = 0;
function newPaymentId() {
  return `pay_${Date.now().toString(36)}${require('node:crypto').randomBytes(6).toString('hex')}${(payCounter++ & 0xfff).toString(36)}`;
}

module.exports = { createLogger, newRequestId, newPaymentId };
