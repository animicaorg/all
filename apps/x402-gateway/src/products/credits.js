'use strict';
/**
 * PREPAID CREDITS — one settlement buys N calls.
 *
 * WHY THIS EXISTS (the economics, measured on this box):
 * every x402 settlement costs the gateway sponsored Base gas. The one
 * production settlement spent 515,712,375,115 wei and reserved
 * 1,192,268,000,000 wei — about $0.0018 spent / $0.0042 reserved at ETH
 * $3,500 — and `checkEconomicFloor` refuses to settle when reserved gas x2
 * exceeds the payment. That puts a HARD FLOOR near $0.0084 under every
 * per-call price. It is why this gateway cannot sell a $0.001 call the way
 * other x402 sellers do, and it is why cheap-per-unit products (embeddings,
 * a single fetch, one chain read) are impossible to price per call at all.
 *
 * A voucher moves the settlement OFF the per-call path: the buyer settles
 * once, and every subsequent call is a local SQLite debit costing no gas.
 * The bonus (default 10%) is not marketing — it is the gas we genuinely do
 * not spend on calls 2..N, handed back to the buyer.
 *
 * SECURITY MODEL. The voucher token is a BEARER SECRET:
 *   - it is returned exactly once, in the response to the purchase;
 *   - only sha256(token) is stored, so reading this database does not let
 *     anyone spend a balance (same reasoning as the sealed commit-reveal
 *     secrets);
 *   - anyone holding the token can spend it. That is the intended property
 *     for an agent-to-agent credential, and it is stated in the product
 *     description rather than left for a buyer to discover.
 */

const crypto = require('node:crypto');
const { ProductError } = require('./errors');

const TOKEN_PREFIX = 'anmc_';

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

/** Mint a fresh bearer token. 32 bytes of CSPRNG, base64url, prefixed. */
function mintToken() {
  return TOKEN_PREFIX + crypto.randomBytes(32).toString('base64url');
}

/** voucher_id = sha256(token). The token itself is never persisted. */
function voucherIdOf(token) {
  return crypto.createHash('sha256').update(String(token), 'utf8').digest('hex');
}

/**
 * Pull a credit token off a request. Two spellings, because agent stacks
 * differ: a dedicated header, or a standard bearer Authorization. Anything
 * that does not carry the prefix is not a credit attempt at all (so an
 * unrelated Authorization header cannot be misread as a spend).
 */
function parseCreditToken(headers) {
  const direct = headers['x-animica-credits'] || headers['x-animica-credit'];
  if (typeof direct === 'string' && direct.startsWith(TOKEN_PREFIX)) return direct.trim();
  const auth = headers.authorization;
  if (typeof auth === 'string') {
    const m = /^Bearer\s+(\S+)$/i.exec(auth.trim());
    if (m && m[1].startsWith(TOKEN_PREFIX)) return m[1];
  }
  return null;
}

/** Shape a voucher row for a response. Never includes the token. */
function publicVoucher(row, { includeId = true } = {}) {
  if (!row) return null;
  const out = {
    balance_atomic: row.balance_atomic,
    balance_usdc: atomicToUsd(row.balance_atomic),
    minted_atomic: row.minted_atomic,
    bonus_atomic: row.bonus_atomic,
    currency: 'USDC',
    created_at: new Date(Number(row.created_at) * 1000).toISOString(),
    expires_at: new Date(Number(row.expires_at) * 1000).toISOString(),
    spend_count: Number(row.spend_count || 0),
    revoked: Boolean(row.revoked),
  };
  if (includeId) out.voucher_id = row.voucher_id;
  if (row.label) out.label = row.label;
  return out;
}

/** USDC atomic (6dp) -> decimal string. Integer math only, never a float. */
function atomicToUsd(atomic) {
  const v = BigInt(atomic);
  const neg = v < 0n;
  const abs = neg ? -v : v;
  const whole = abs / 1000000n;
  const frac = (abs % 1000000n).toString().padStart(6, '0');
  return `${neg ? '-' : ''}${whole}.${frac}`;
}

function createCreditsProduct({ cfg, gatewayStore, now = Date.now }) {
  const bonusPct = BigInt(cfg.creditsBonusPct);
  const ttlSec = Number(cfg.creditsTtlDays) * 86400;

  return {
    id: 'credits_buy',
    title: 'Prepaid call credits',
    description:
      `Buy a prepaid credit voucher in ONE on-chain settlement, then spend it across every paid product on this gateway with no further settlement and no gas. Send the returned token as "X-Animica-Credits: anmc_..." (or "Authorization: Bearer anmc_...") on any paid route and the call is served from the balance instead of a payment. Credit is granted at face value plus ${cfg.creditsBonusPct}% — that bonus is the Base settlement gas we do not spend on your calls 2..N, returned to you. The voucher is a BEARER secret shown exactly once and stored only as its SHA-256 hash: anyone holding it can spend it, and we cannot recover it for you. Balance and full spend history are readable for free at GET /x402/credits/balance with the token. Unused credit expires ${cfg.creditsTtlDays} days after purchase. Send the token on THIS route to top up an existing voucher instead of minting a new one.`,
    path: '/x402/credits/buy',
    routes: [{ method: 'POST', path: '/x402/credits/buy' }],
    priceUsd: cfg.creditsPriceUsd,
    enabled: cfg.creditsEnabled,
    // Minting is local, deterministic and cannot fail once money is in —
    // settle first, then write the row.
    mode: 'settle-then-execute',
    mimeType: 'application/json',
    maxBodyBytes: 4096,
    // A voucher IS the payment instrument; letting one be bought with another
    // would let a caller launder balance between vouchers and complicates
    // nothing usefully. Top-up is handled explicitly below.
    creditable: false,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          label: {
            type: 'string',
            required: false,
            description: 'optional free-text label (<=64 chars) returned with your balance, so an agent can tell its own vouchers apart',
          },
        },
        headers: {
          'X-Animica-Credits': {
            required: false,
            description: 'an existing anmc_ token — when present the purchase TOPS UP that voucher and no new token is minted',
          },
        },
      },
      output: {
        type: 'json',
        description:
          'voucher {token (shown ONCE, only on a new mint), voucher_id, balance_atomic, balance_usdc, bonus_atomic, expires_at}, how_to_use, and payment metadata',
      },
    },

    /** Local SQLite write — available whenever the store is. */
    async availability() {
      try {
        gatewayStore.ping();
        return { available: true };
      } catch (e) {
        return { available: false, reason: 'credit_store_unavailable', detail: e.message };
      }
    },

    validate(ctx) {
      const body = ctx.json;
      if (body !== null && body !== undefined && (typeof body !== 'object' || Array.isArray(body))) {
        throw bad('body must be a JSON object or empty', 'invalid_request');
      }
      let label = null;
      if (body && body.label !== undefined && body.label !== null) {
        if (typeof body.label !== 'string') throw bad('label must be a string', 'invalid_request');
        label = body.label.trim().slice(0, 64);
      }
      // A top-up target is identified by the token the caller already holds.
      // Validate its SHAPE here (pre-payment); whether it still exists is
      // re-checked at mint time, because it could expire in between.
      const token = parseCreditToken(ctx.headers);
      let topUpId = null;
      if (token) {
        topUpId = voucherIdOf(token);
        const row = gatewayStore.getVoucher(topUpId);
        if (!row) {
          throw bad(
            'the X-Animica-Credits token does not match a known voucher; omit the header to mint a new one',
            'unknown_voucher'
          );
        }
        if (row.revoked) throw bad('that voucher is revoked and cannot be topped up', 'voucher_revoked');
      }
      return { label, topUpId };
    },

    async handler(ctx) {
      const { label, topUpId } = ctx.params;
      const at = Math.floor(now() / 1000);
      const face = BigInt(ctx.product ? ctx.product.priceAtomic : this.priceAtomic);
      // Bonus is integer math on atomic units. Floor, so we never grant a
      // fraction of an atomic unit we did not intend.
      const bonus = (face * bonusPct) / 100n;
      const granted = face + bonus;

      if (topUpId) {
        // Top-up = a refund-shaped credit onto the existing voucher. Reuses
        // the same CAS-guarded write as every other balance change, so a
        // concurrent spend cannot lose the top-up.
        const r = gatewayStore.refundVoucher({
          voucherId: topUpId,
          amountAtomic: granted.toString(),
          product: 'credits_buy',
          resource: '/x402/credits/buy',
          requestId: ctx.requestId,
          now: now(),
        });
        if (!r.ok) {
          // Money is already in at this point, so this must be retryable
          // rather than a silent loss.
          const err = new Error(`top-up failed: ${r.reason}`);
          err.retryable = true;
          throw err;
        }
        const row = gatewayStore.getVoucher(topUpId);
        return {
          status: 200,
          bodyObj: {
            product: 'credits_buy',
            action: 'top_up',
            voucher: publicVoucher(row),
            granted_atomic: granted.toString(),
            granted_usdc: atomicToUsd(granted),
            bonus_atomic: bonus.toString(),
            note: 'Topped up the voucher identified by the token you sent. No new token was minted — keep using the one you have.',
          },
        };
      }

      const token = mintToken();
      const voucherId = voucherIdOf(token);
      gatewayStore.putVoucher({
        voucherId,
        label,
        mintedAtomic: face.toString(),
        bonusAtomic: bonus.toString(),
        payer: (ctx.settlement && ctx.settlement.payer) || null,
        settlementTx: (ctx.settlement && ctx.settlement.transaction) || null,
        createdAt: at,
        expiresAt: at + ttlSec,
      });
      const row = gatewayStore.getVoucher(voucherId);

      return {
        status: 200,
        bodyObj: {
          product: 'credits_buy',
          action: 'mint',
          // THE ONLY TIME THIS IS EVER RETURNED.
          token,
          voucher: publicVoucher(row),
          granted_atomic: granted.toString(),
          granted_usdc: atomicToUsd(granted),
          bonus_atomic: bonus.toString(),
          bonus_percent: Number(cfg.creditsBonusPct),
          how_to_use: {
            header: `X-Animica-Credits: ${token}`,
            alternative: `Authorization: Bearer ${token}`,
            balance: 'GET /x402/credits/balance (free, send the same header)',
            note:
              'Send this on any paid route on this gateway. The call is served from your balance at the route\'s listed price, with no on-chain settlement and no gas. When the balance is short of the price, the route answers its normal 402 so you can pay for that call directly.',
          },
          warning:
            'This token is shown ONCE and stored only as a SHA-256 hash. Save it now. Anyone who holds it can spend the balance, and we cannot recover or reissue it.',
        },
      };
    },
  };
}

/**
 * FREE balance/history route. Checking your own balance must not cost
 * anything: an agent needs to know whether it can afford the next call
 * BEFORE it makes it, and charging for that would make the credit system
 * unusable for the exact planning step it exists to enable.
 */
function createCreditsBalanceRoute({ cfg, gatewayStore }) {
  return {
    method: 'GET',
    path: '/x402/credits/balance',
    description:
      'FREE. Send X-Animica-Credits (or Authorization: Bearer) with your anmc_ token to read that voucher\'s balance, expiry and recent spend history. Free by design — an agent must be able to check what it can afford before it commits to a call.',
    match(pathname) {
      return pathname === '/x402/credits/balance' ? {} : null;
    },
    // NOTE: the contract is `handler`, not `handle` — server.js calls
    // free.route.handler(ctx). Naming it anything else 500s at runtime while
    // every unit test that calls the function directly still passes.
    async handler(ctx) {
      const token = parseCreditToken(ctx.headers);
      if (!token) {
        return {
          status: 401,
          bodyObj: {
            error: 'missing_credit_token',
            detail: 'send your voucher as "X-Animica-Credits: anmc_..." or "Authorization: Bearer anmc_..."',
            buy: '/x402/credits/buy',
          },
        };
      }
      const voucherId = voucherIdOf(token);
      const row = gatewayStore.getVoucher(voucherId);
      if (!row) {
        // Deliberately the same answer shape as a real miss — there is no
        // enumeration value here anyway (the id is a sha256 of 32 CSPRNG
        // bytes), but there is also no reason to confirm non-existence in a
        // different way than any other failure.
        return {
          status: 404,
          bodyObj: { error: 'unknown_voucher', detail: 'no voucher matches that token', buy: '/x402/credits/buy' },
        };
      }
      const entries = gatewayStore.listCreditEntries(voucherId, 50);
      return {
        status: 200,
        bodyObj: {
          product: 'credits_balance',
          voucher: publicVoucher(row),
          expired: Number(row.expires_at) <= Math.floor(Date.now() / 1000),
          recent_activity: entries.map((e) => ({
            at: new Date(Number(e.created_at) * 1000).toISOString(),
            product: e.product,
            resource: e.resource,
            amount_atomic: e.amount_atomic,
            amount_usdc: atomicToUsd(e.amount_atomic),
            balance_after: e.balance_after,
            kind: String(e.amount_atomic).startsWith('-') ? 'credit' : 'debit',
          })),
          note:
            'A negative amount is credit added (a purchase, top-up, or an automatic refund for a call that failed after it was debited).',
        },
      };
    },
  };
}

module.exports = {
  createCreditsProduct,
  createCreditsBalanceRoute,
  parseCreditToken,
  voucherIdOf,
  mintToken,
  publicVoucher,
  atomicToUsd,
  TOKEN_PREFIX,
};
