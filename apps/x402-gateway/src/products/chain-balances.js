'use strict';
/**
 * P5 — bulk balance lookup, $0.02/call (X402_CHAIN_BALANCES_PRICE_USDC).
 * POST /x402/chain/balances
 *
 * Sells the ONE thing the free single-lookup API cannot give an agent: up to
 * X402_CHAIN_BALANCES_MAX_ADDRESSES (500) accounts resolved in a single
 * batched JSON-RPC round trip, head-pinned, with exact decimal-string
 * amounts. Measured on this box (chain-data recon 2026-08-15): 50 rich
 * balance objects in one batch = 0.25 s, ~5 ms/address => 500 ≈ 2.5 s, which
 * is why the cap is 500 and why it is enforced BEFORE settlement. Single
 * balance lookups stay free — say so in the listing, because it is true.
 *
 * Rules this file exists to enforce:
 *   - the cap and every address' validity are checked in validate(), i.e.
 *     before a 402 is even offered: we never take money for a call we are
 *     about to refuse;
 *   - duplicate addresses are collapsed to one RPC each (a 500-entry list of
 *     the same account costs the node one lookup), while the response still
 *     answers every entry the caller sent, in order, echoing their input
 *     form;
 *   - amounts are BigInt/decimal strings end to end. `confirmed_balance`
 *     arrives as a decimal string, `state.getBalance` as a 0x quantity;
 *     both are normalised through BigInt and re-emitted as decimal nANM.
 *     Nothing here ever becomes a JS Number;
 *   - per-address RPC failures are DATA (`error` on that entry), not a
 *     poisoned batch — a settled $0.02 call must not fall into the
 *     incident/refund path because one account key upset the node;
 *   - the batch is dispatched sequentially on the node's single event loop,
 *     so a new block can land mid-batch. The response reports the observed
 *     head range and `consistent:false` when entries straddle a block rather
 *     than pretending to a snapshot it did not take.
 */

const { ProductError, ProductUnavailable } = require('./errors');
const { toAccountDigestHex } = require('../bech32m');

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

/** decimal string | 0x-hex quantity | safe integer -> BigInt. Never a float. */
function toBigIntAmount(v) {
  if (typeof v === 'bigint') return v;
  if (typeof v === 'string') {
    const s = v.trim();
    if (/^-?\d+$/.test(s)) return BigInt(s);
    if (/^0x[0-9a-fA-F]+$/.test(s)) return BigInt(s);
    throw new Error(`unparseable balance ${JSON.stringify(v)}`);
  }
  if (typeof v === 'number') {
    if (!Number.isSafeInteger(v)) throw new Error('balance arrived as an unsafe JS number');
    return BigInt(v);
  }
  throw new Error(`unparseable balance of type ${typeof v}`);
}

/** Pull the balance out of whatever shape the node answered with. */
function balanceOf(result) {
  if (result === null || result === undefined) return null;
  if (typeof result === 'string' || typeof result === 'number') return toBigIntAmount(result);
  if (typeof result === 'object') {
    for (const k of ['confirmed_balance', 'balance', 'amount']) {
      if (result[k] !== undefined && result[k] !== null) return toBigIntAmount(result[k]);
    }
  }
  throw new Error('balance field missing from the node response');
}

function createChainBalancesProduct({ cfg, node }) {
  let headCache = { at: 0, height: null, hash: null };

  async function pinnedHead() {
    if (Date.now() - headCache.at < 5000 && headCache.height !== null) return headCache;
    const head = await node.call('chain.getHead', {}, { timeoutMs: 5000 });
    const h = head && Number.isInteger(head.height) ? head.height : null;
    if (h === null) throw new ProductUnavailable('chain_balances_head_unknown', 'chain.getHead returned no height');
    headCache = { at: Date.now(), height: h, hash: head.hash || null };
    return headCache;
  }

  return {
    id: 'chain_batch_balances',
    title: 'Bulk account balances',
    description:
      `Balances for up to ${cfg.chainBalancesMaxAddresses} Animica accounts in ONE batched call, head-pinned, amounts in nANM as exact decimal strings. Single-address balance lookups stay free on the public API — this sells the batch.`,
    path: '/x402/chain/balances',
    routes: [{ method: 'POST', path: '/x402/chain/balances' }],
    priceUsd: cfg.chainBalancesPriceUsd,
    enabled: cfg.chainBalancesEnabled,
    // Real node work: pin the head pre-settle, then settle, then fan out.
    mode: 'settle-then-execute',
    mimeType: 'application/json',
    // 500 anim1 addresses ≈ 33 KB; 128 KB leaves room for whitespace/JSON.
    maxBodyBytes: 131072,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          addresses: {
            type: 'array',
            description: `1..${cfg.chainBalancesMaxAddresses} anim1… bech32m addresses (32-byte hex account digests also accepted); duplicates are collapsed to one lookup but still answered in place`,
          },
        },
      },
      output: {
        type: 'json',
        description:
          'balances[] {address, account_digest, balance (nANM decimal string), spendable_balance?, exists?, error?} in request order, plus unique_addresses, total_balance, as_of {head_height, head_hash, entry_head_min/max, consistent} and payment metadata',
      },
    },

    /** Readiness = the node answers chain.getHead (cached 5 s). */
    async availability() {
      try {
        const h = await pinnedHead();
        return { available: true, head: h.height };
      } catch (e) {
        return { available: false, reason: 'chain_balances_node_unreachable', detail: e.message };
      }
    },

    /** Caps + address validity BEFORE any payment is requested. */
    validate(ctx) {
      const body = ctx.json;
      if (!body || typeof body !== 'object' || Array.isArray(body)) {
        throw bad('body must be a JSON object with an "addresses" array', 'invalid_request');
      }
      const list = body.addresses;
      if (!Array.isArray(list) || list.length === 0) {
        throw bad('addresses must be a non-empty array', 'invalid_request');
      }
      if (list.length > cfg.chainBalancesMaxAddresses) {
        throw bad(
          `${list.length} addresses exceeds the per-call cap of ${cfg.chainBalancesMaxAddresses} — split the request`,
          'too_many_addresses',
          { caps: { max_addresses: cfg.chainBalancesMaxAddresses } }
        );
      }
      const entries = [];
      const unique = new Map(); // digest -> the first input form seen
      for (let i = 0; i < list.length; i++) {
        const raw = list[i];
        if (typeof raw !== 'string' || !raw.trim()) {
          throw bad(`addresses[${i}] must be a non-empty string`, 'invalid_address');
        }
        let digest;
        try {
          digest = toAccountDigestHex(raw);
        } catch (e) {
          throw bad(`addresses[${i}]: ${e.message}`, 'invalid_address', { index: i });
        }
        entries.push({ address: raw.trim(), digest });
        if (!unique.has(digest)) unique.set(digest, raw.trim());
      }
      return { entries, unique };
    },

    /** Pre-settle readiness: the node answers now. Failure charges nobody. */
    async preSettle() {
      const h = await pinnedHead();
      return { head: h.height, headHash: h.hash };
    },

    async handler(ctx) {
      const { entries, unique } = ctx.params;
      const digests = [...unique.keys()];
      const calls = digests.map((d) => ({
        method: 'state.getAddressBalance',
        // The node accepts anim1… bech32m, system:… or a 0x-hex digest; the
        // digest is the canonical join key, so send that.
        params: { address: `0x${d}` },
      }));

      let results;
      try {
        results = await node.batchSettled(calls, { timeoutMs: cfg.chainBalancesTimeoutMs });
      } catch (e) {
        // Transport-level failure = nothing was answered; retryable so the
        // paywall gets one bounded second attempt before the signed receipt.
        const err = new Error(`balance batch failed: ${e.message}`);
        err.retryable = true;
        throw err;
      }

      const byDigest = new Map();
      let headMin = null;
      let headMax = null;
      let unit = 'nANM';
      let decimals = 9;
      for (let i = 0; i < digests.length; i++) {
        const d = digests[i];
        const r = results[i];
        if (!r || r.ok !== true) {
          byDigest.set(d, { balance: null, error: (r && r.error) || 'no result' });
          continue;
        }
        const res = r.result;
        let balance;
        try {
          balance = balanceOf(res);
        } catch (e) {
          byDigest.set(d, { balance: null, error: e.message });
          continue;
        }
        if (balance === null) {
          byDigest.set(d, { balance: null, error: 'account not found' });
          continue;
        }
        // Real extra fields the live node returns (verified 2026-08-15):
        // spendable_balance (confirmed minus pending outgoing) and exists.
        // Passed through only when actually present — never invented.
        let spendable = null;
        if (res && typeof res === 'object' && res.spendable_balance !== undefined && res.spendable_balance !== null) {
          try {
            spendable = toBigIntAmount(res.spendable_balance).toString();
          } catch {
            spendable = null;
          }
        }
        const exists = res && typeof res === 'object' && typeof res.exists === 'boolean' ? res.exists : null;
        if (res && typeof res === 'object') {
          if (typeof res.unit === 'string') unit = res.unit;
          if (Number.isInteger(res.display_decimals)) decimals = res.display_decimals;
          const h = res.as_of_head_height;
          if (Number.isInteger(h)) {
            headMin = headMin === null ? h : Math.min(headMin, h);
            headMax = headMax === null ? h : Math.max(headMax, h);
          }
        }
        byDigest.set(d, { balance, spendable, exists, error: null });
      }

      let total = 0n;
      for (const [, v] of byDigest) if (v.balance !== null) total += v.balance;

      const balances = entries.map((e) => {
        const v = byDigest.get(e.digest);
        const out = {
          address: e.address,
          account_digest: `0x${e.digest}`,
          balance: v && v.balance !== null ? v.balance.toString() : null,
        };
        if (v && v.spendable !== null && v.spendable !== undefined) out.spendable_balance = v.spendable;
        if (v && v.exists !== null && v.exists !== undefined) out.exists = v.exists;
        if (v && v.error) out.error = v.error;
        return out;
      });

      const failed = [...byDigest.values()].filter((v) => v.error).length;

      return {
        status: 200,
        bodyObj: {
          product: 'chain_batch_balances',
          unit,
          decimals,
          count: balances.length,
          unique_addresses: digests.length,
          failed_lookups: failed,
          // Sum over UNIQUE accounts, so a duplicated address cannot inflate
          // it. Decimal string: this can exceed 2^53 on its own.
          total_balance: total.toString(),
          as_of: {
            head_height: ctx.pinned.head,
            head_hash: ctx.pinned.headHash || null,
            entry_head_min: headMin,
            entry_head_max: headMax,
            // The node dispatches a batch sequentially on one event loop, so
            // a block can land mid-batch. Say so instead of implying an
            // atomic snapshot.
            consistent: headMin === null || headMin === headMax,
          },
          balances,
          derivation: {
            account_digest:
              'bech32m anim1… payload = alg_id (2 bytes) || sha3_256(pubkey) (32 bytes); the lookup key is payload[2:34] (the 32-byte digest), which is what is sent to the node.',
            amounts:
              'balances are nANM (1e-9 ANM) as exact decimal strings, normalised through BigInt from the node\'s confirmed_balance. Divide by 1e9 for ANM — with a decimal library, never a float.',
            duplicates:
              'duplicate addresses are resolved once and answered in place; total_balance sums UNIQUE accounts only.',
            fields:
              'balance = the node\'s confirmed_balance. spendable_balance and exists are passed through only when the node returns them; nothing here is synthesised.',
            source:
              'state.getAddressBalance on the local Animica node, one batched JSON-RPC request. Note the node has an upstream read-through fallback when it believes it is behind, so a balance may come from a trusted upstream rather than local state.',
          },
        },
      };
    },
  };
}

module.exports = { createChainBalancesProduct, toBigIntAmount, balanceOf };
