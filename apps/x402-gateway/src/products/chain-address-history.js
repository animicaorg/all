'use strict';
/**
 * P4 — account transaction history, $0.05/page
 * (X402_CHAIN_HISTORY_PRICE_USDC). POST /x402/chain/address-history
 *
 * This is the one chain SKU with a real advantage over the free surfaces,
 * and the advantage is the gateway's own index (src/chain-index/): the node
 * RPC has NO history method at all, and the explorer answers address queries
 * with a live reverse block scan capped at 250 blocks / 3.5 s (measured
 * 2026-08-15: 3.54 s to return ZERO txs for the chain's most active sender —
 * full history there is ~300 paged calls). Against the index the same answer
 * is an O(log n) sqlite lookup with a stable cursor.
 *
 * Everything the buyer needs to recompute the answer from raw blocks is
 * published in the response's `derivation` block, and restated here:
 *
 *   account digest   bech32m `anim1…` payload = alg_id (2 bytes) ||
 *                    sha3_256(pubkey) (32 bytes); the join key is
 *                    payload[2:34] — the digest. The alg id is NOT
 *                    recoverable from a digest, so responses echo the input
 *                    form and match on the digest only.
 *   direction        from == to        -> exactly one row, 'self'
 *                    tx.from == digest -> 'out'
 *                    tx.to   == digest -> 'in'
 *                    (a transfer between two distinct accounts therefore
 *                    appears once in each account's history, never twice in
 *                    the same one)
 *   ordering         (height, tx_index) — DESC by default (newest first),
 *                    ASC with order:"asc". tx_index is the position in the
 *                    block's `transactions` array as the node returns it.
 *   cursor           "<as_of>:<height>:<tx_index>" — the position of the LAST
 *                    row returned. The next page is every row STRICTLY after
 *                    it in the chosen order, within the same `as_of`
 *                    snapshot, so paging can never skip or repeat a row while
 *                    the index advances.
 *   amounts          nANM (1e-9 ANM) as decimal STRINGS end to end; live
 *                    balances already exceed 2^53, so no amount is ever a JS
 *                    Number anywhere in this path.
 *
 * Availability fails CLOSED before payment whenever the index is disabled,
 * backfilling, stalled or lagging (src/chain-index/index.js::createIndexHealth):
 * a history page from an incomplete index is not stale, it is wrong.
 */

const { ProductError } = require('./errors');
const { toAccountDigestHex } = require('../bech32m');

const DIRECTIONS = new Set(['any', 'in', 'out', 'self']);
const CURSOR_RE = /^(\d{1,12}):(\d{1,12}):(\d{1,9})$/;

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

function intField(obj, name, { min = 0, max = Number.MAX_SAFE_INTEGER, fallback } = {}) {
  const raw = obj[name];
  if (raw === undefined || raw === null || raw === '') return fallback;
  const n = typeof raw === 'number' ? raw : Number(raw);
  if (!Number.isInteger(n) || n < min || n > max) {
    throw bad(`${name} must be an integer in [${min}, ${max}]`);
  }
  return n;
}

function createChainAddressHistoryProduct({ cfg, node, chainIndex, indexHealth }) {
  const health = indexHealth;

  function rowOut(r) {
    return {
      height: r.height,
      tx_index: r.tx_index,
      tx_hash: r.tx_hash,
      direction: r.direction,
      counterparty: r.counterparty ? `0x${r.counterparty}` : null,
      value: r.value,
      tip: r.tip === null || r.tip === undefined ? null : r.tip,
      gas: r.gas,
      kind: r.kind,
      block_hash: r.block_hash,
      timestamp: r.timestamp,
    };
  }

  return {
    id: 'chain_address_history',
    title: 'Account transaction history',
    description:
      `Full transaction history for one Animica account (anim1… bech32m), served from the gateway's own head-following index with a stable cursor — up to ${cfg.chainHistoryMaxLimit} rows per call, amounts in nANM as decimal strings. The free per-block and per-tx APIs stay free; single-address lookups on explorer.animica.org stay free too (they scan at most 250 blocks per call).`,
    path: '/x402/chain/address-history',
    routes: [{ method: 'POST', path: '/x402/chain/address-history' }],
    priceUsd: cfg.chainHistoryPriceUsd,
    enabled: cfg.chainHistoryEnabled,
    // The work is a local sqlite read: obtain the page FIRST (a failure there
    // charges nobody), settle, then deliver. No node work is spent.
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 16 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          address: { type: 'string', description: 'anim1… bech32m address (or a 32-byte hex account digest)' },
          limit: { type: 'integer', description: `rows per page, 1..${cfg.chainHistoryMaxLimit} (default ${cfg.chainHistoryDefaultLimit})` },
          cursor: { type: 'string', description: '"<as_of>:<height>:<tx_index>" from a previous response\'s next_cursor; pages the same index snapshot' },
          order: { type: 'string', description: 'desc (newest first, default) | asc' },
          direction: { type: 'string', description: 'any (default) | in | out | self' },
          from_height: { type: 'integer', description: 'inclusive lower height bound (default 0 = genesis)' },
          to_height: { type: 'integer', description: 'inclusive upper height bound (default: the index tip)' },
        },
      },
      output: {
        type: 'json',
        description:
          'transactions[] {height, tx_index, tx_hash, direction, counterparty, value, tip, gas, kind, block_hash, timestamp} with amounts in nANM decimal strings, plus index {as_of_height, head_height, lag_blocks}, total, has_more, next_cursor, a derivation block documenting the digest/direction/ordering/cursor rules, and payment metadata',
      },
    },

    /**
     * Readiness = the index is healthy AND caught up within
     * X402_CHAIN_INDEX_MAX_LAG_BLOCKS of the live head. Anything else is a
     * 503 with the reason and backfill progress — never a 402.
     */
    async availability() {
      const h = await health.check();
      if (h.ok) {
        return { available: true, indexed_height: h.indexed_height, head_height: h.head_height, lag_blocks: h.lag_blocks };
      }
      return {
        available: false,
        reason: h.reason,
        detail: h.detail,
        body: {
          error: h.reason,
          detail: h.detail,
          index: {
            indexed_height: h.indexed_height,
            head_height: h.head_height,
            lag_blocks: h.lag_blocks === undefined ? null : h.lag_blocks,
            max_lag_blocks: cfg.chainIndexMaxLagBlocks,
            last_tick_age_ms: h.last_tick_age_ms,
            caught_up: h.caught_up,
          },
          progress: h.progress || null,
        },
      };
    },

    /**
     * Every shape/cap problem answers 400 BEFORE a 402 is issued — including
     * a cursor that points past the index tip, which is checkable here
     * because the store is a synchronous local read.
     */
    validate(ctx) {
      const body = ctx.json;
      if (!body || typeof body !== 'object' || Array.isArray(body)) {
        throw bad('body must be a JSON object', 'invalid_request');
      }
      const addr = body.address;
      if (typeof addr !== 'string' || !addr.trim()) {
        throw bad('address is required (anim1… bech32m or a 32-byte hex account digest)', 'invalid_address');
      }
      let digest;
      try {
        digest = toAccountDigestHex(addr);
      } catch (e) {
        throw bad(e.message, 'invalid_address');
      }

      // The cap is enforced HERE, before any payment is requested, and with
      // the cap in the body so an agent can adapt without guessing.
      if (body.limit !== undefined && body.limit !== null && body.limit !== '' && Number(body.limit) > cfg.chainHistoryMaxLimit) {
        throw bad(
          `limit ${body.limit} exceeds the per-call cap of ${cfg.chainHistoryMaxLimit} rows — page with next_cursor`,
          'limit_too_large',
          { caps: { max_limit: cfg.chainHistoryMaxLimit, default_limit: cfg.chainHistoryDefaultLimit } }
        );
      }
      const limit = intField(body, 'limit', { min: 1, max: cfg.chainHistoryMaxLimit, fallback: cfg.chainHistoryDefaultLimit });

      const order = body.order === undefined || body.order === null ? 'desc' : String(body.order);
      if (order !== 'asc' && order !== 'desc') throw bad('order must be "asc" or "desc"');
      const direction = body.direction === undefined || body.direction === null ? 'any' : String(body.direction);
      if (!DIRECTIONS.has(direction)) throw bad(`direction must be one of ${[...DIRECTIONS].join(', ')}`);

      // NOTE: validate() runs BEFORE the availability hook, so every check
      // below must tolerate an empty index (tip === -1) and leave that
      // verdict to availability() — an unbuilt index is a 503 with a
      // reason, never a 400 blaming the caller's parameters.
      const state = chainIndex.getState();
      const tip = state.indexedHeight;
      const haveIndex = tip >= 0;

      let after = null;
      let asOf = null;
      if (body.cursor !== undefined && body.cursor !== null && body.cursor !== '') {
        const m = CURSOR_RE.exec(String(body.cursor));
        if (!m) throw bad('cursor must be "<as_of>:<height>:<tx_index>" as returned in next_cursor', 'invalid_cursor');
        asOf = Number(m[1]);
        after = { height: Number(m[2]), txIndex: Number(m[3]) };
        if (haveIndex && asOf > tip) {
          throw bad(
            `cursor snapshot ${asOf} is ahead of the index tip ${tip}`,
            'invalid_cursor',
            { index: { as_of_height: tip } }
          );
        }
        if (after.height > asOf) throw bad('cursor height is outside its own snapshot', 'invalid_cursor');
      }
      if (asOf === null) asOf = tip;

      const fromHeight = intField(body, 'from_height', { min: 0, fallback: 0 });
      const explicitTo = body.to_height !== undefined && body.to_height !== null && body.to_height !== '';
      const toHeightRaw = intField(body, 'to_height', { min: 0, fallback: asOf });
      if (explicitTo && toHeightRaw < fromHeight) throw bad('to_height must be >= from_height');
      if (haveIndex && !explicitTo && fromHeight > asOf) {
        throw bad(
          `from_height ${fromHeight} is beyond the index tip ${asOf}`,
          'invalid_params',
          { index: { as_of_height: asOf } }
        );
      }
      // The window can never exceed the snapshot the cursor pins.
      const toHeight = Math.min(toHeightRaw, asOf);

      return { address: addr.trim(), digest, limit, order, direction, after, asOf, fromHeight, toHeight };
    },

    /**
     * Runs after verify and BEFORE settlement (execute-then-settle): a local
     * index read costs the node nothing, so the buyer never pays for a page
     * that could not be produced.
     */
    async handler(ctx) {
      const p = ctx.params;
      const state = chainIndex.getState();
      // Re-read the gate so the response quotes the LIVE head, not the one
      // the walker happened to see last. The head is cached for 5 s inside
      // the health helper, so this is the same value availability() just
      // used — free, and honest about how far behind the tip the answer is.
      let live = null;
      try {
        live = await health.check();
      } catch {
        live = null;
      }
      const { rows, hasMore } = chainIndex.queryHistory({
        digest: p.digest,
        fromHeight: p.fromHeight,
        toHeight: p.toHeight,
        direction: p.direction,
        order: p.order,
        after: p.after,
        limit: p.limit,
      });
      const total = chainIndex.countHistory({
        digest: p.digest,
        fromHeight: p.fromHeight,
        toHeight: p.toHeight,
        direction: p.direction,
      });
      const last = rows.length ? rows[rows.length - 1] : null;

      return {
        status: 200,
        bodyObj: {
          product: 'chain_address_history',
          address: p.address,
          account_digest: `0x${p.digest}`,
          chain_id: state.chainId,
          unit: 'nANM',
          decimals: 9,
          index: {
            as_of_height: p.asOf,
            indexed_height: state.indexedHeight,
            // Live head at answer time (falls back to the walker's last
            // observation only if the node stopped answering mid-request).
            head_height: live && Number.isInteger(live.head_height) ? live.head_height : state.headHeight,
            lag_blocks: live && Number.isInteger(live.head_height)
              ? live.head_height - state.indexedHeight
              : (Number.isInteger(state.headHeight) ? state.headHeight - state.indexedHeight : null),
            head_margin: cfg.chainIndexHeadMargin,
            max_lag_blocks: cfg.chainIndexMaxLagBlocks,
          },
          window: { from_height: p.fromHeight, to_height: p.toHeight },
          filter: { direction: p.direction },
          order: p.order,
          limit: p.limit,
          count: rows.length,
          total,
          has_more: hasMore,
          next_cursor: hasMore && last ? `${p.asOf}:${last.height}:${last.tx_index}` : null,
          transactions: rows.map(rowOut),
          derivation: {
            account_digest:
              'bech32m anim1… payload = alg_id (2 bytes) || sha3_256(pubkey) (32 bytes); the match key is payload[2:34] (the 32-byte digest). tx.from/tx.to on this chain ARE those digests. The alg id is not recoverable from a digest, so responses echo your input form and match on the digest.',
            direction:
              "from == to -> a single row with direction 'self'; otherwise the sender's row is 'out' and the recipient's row is 'in'. A transfer therefore appears exactly once in each participant's history.",
            ordering:
              'rows are ordered by (height, tx_index); tx_index is the position in the block\'s `transactions` array exactly as chain.getBlockByHeight returns it.',
            cursor:
              'next_cursor = "<as_of>:<height>:<tx_index>" of the LAST row returned. The next page is every row strictly after it in the chosen order within the same as_of snapshot — no skips, no repeats while the index advances.',
            amounts:
              'value and tip are nANM (1e-9 ANM) as exact decimal strings. They are never parsed into a JS Number anywhere in this path (live amounts already exceed 2^53).',
            recompute:
              'fetch blocks from_height..to_height with chain.getBlockByHeight(includeTxObjects=true), apply the direction rule to every tx, and you get these rows byte for byte. /x402/chain/blocks sells that raw range if you want to audit it.',
            index_freshness:
              'the index only records blocks at or below head - head_margin, so a shallow reorg can never have been recorded; lag_blocks is measured against the live head and this product refuses to serve (503, no charge) above max_lag_blocks.',
          },
        },
      };
    },
  };
}

module.exports = { createChainAddressHistoryProduct };
