'use strict';
/**
 * P2 — bulk chain data, $0.05/export (X402_BULK_CHAIN_PRICE_USDC).
 *
 *   GET /x402/chain/blocks        block-range export (full tx objects)
 *   GET /x402/chain/transactions  tx-range export (block context stamped
 *                                 per row, optional address filter)
 *   GET /x402/chain/export        umbrella: ?type=blocks|transactions
 *
 * The free per-block RPC stays free — this sells volume + shaping +
 * pagination + honest caps. Every rule below is from the live chain-data
 * recon (2026-08-15), not taste:
 *
 *   - Reads go to the LOCAL node RPC only, batched chain.getBlockByHeight
 *     in chunks of <=X402_BULK_CHUNK_BLOCKS (100): one 1000-block batch
 *     holds the node's single event loop 5.8-8.6 s — a DoS against miners
 *     and wallets sharing it. Chunks + the node client's single-flight rule
 *     keep any hold under ~0.6 s.
 *   - Caps are enforced BEFORE settlement where derivable from params
 *     (window size vs X402_BULK_MAX_BLOCKS -> 400, no payment requested).
 *     Budgets only discoverable mid-flight (bytes, execution deadline,
 *     tx-record cap, a failed block fetch) TRUNCATE at a block boundary
 *     with `next_cursor` — a paid export never 500s over its own budget.
 *   - Cursor semantics copy the explorer's hard-learned rules: the cursor
 *     is the first UNCONSUMED height; a failed fetch is the resume point,
 *     never skipped; an emitted block's txs are never sliced — budgets stop
 *     BETWEEN blocks, not inside one.
 *   - The head is resolved ONCE pre-settle and the export is pinned to
 *     head - X402_BULK_HEAD_MARGIN so it can't straddle a reorg at the
 *     tip; the pinned head rides in the meta line.
 *   - Amounts (tx value/tip, nANM = 1e-9 ANM) are decimal STRINGS end to
 *     end — the node client quotes them before JSON.parse because live
 *     balances already exceed 2^53.
 *   - The node duplicates ~half of every block (txs mirrors transactions,
 *     header mirrors the top level, receipts are always empty) — stripped.
 *   - NDJSON (default) or JSON via ?format= / Accept; gzip via
 *     Accept-Encoding (measured 14.8x on block exports). Byte budgets
 *     meter UNCOMPRESSED bytes.
 */

const zlib = require('node:zlib');

const { ProductError, ProductUnavailable } = require('./errors');
const { toAccountDigestHex } = require('../bech32m');

const NDJSON = 'application/x-ndjson';

function intParam(query, name, { required = false, min = 0, max = Number.MAX_SAFE_INTEGER, fallback } = {}) {
  const raw = query.get(name);
  if (raw === null || raw === '') {
    if (required) throw new ProductError(`missing required query param ${name}`, { body: { error: 'invalid_params', detail: `missing required query param ${name}` } });
    return fallback;
  }
  const n = Number(raw);
  if (!Number.isInteger(n) || n < min || n > max) {
    throw new ProductError(`${name} must be an integer in [${min}, ${max}]`, { body: { error: 'invalid_params', detail: `${name} must be an integer in [${min}, ${max}]` } });
  }
  return n;
}

/** Shape one raw node block: strip txs/header/receipts mirrors, stringify money. */
function shapeBlock(raw) {
  const txs = Array.isArray(raw.transactions) ? raw.transactions : [];
  return {
    height: raw.number,
    hash: raw.hash,
    parent_hash: raw.parentHash,
    timestamp: raw.timestamp,
    chain_id: raw.chainId,
    theta_micro: raw.thetaMicro,
    nonce: raw.nonce,
    roots: raw.roots,
    transactions: txs.map((t) => shapeTx(t)),
  };
}

function shapeTx(t) {
  return {
    hash: t.hash,
    from: t.from,
    to: t.to,
    // value/tip arrive as decimal strings from the BigInt-safe parser.
    value: t.value === undefined ? undefined : String(t.value),
    tip: t.tip === undefined ? undefined : String(t.tip),
    gas: t.gas,
    kind: t.kind,
    data: t.data,
  };
}

function txRow(block, index, t) {
  return {
    height: block.number,
    block_hash: block.hash,
    block_timestamp: block.timestamp,
    tx_index: index,
    ...shapeTx(t),
  };
}

function digestOf(hex0x) {
  return typeof hex0x === 'string' ? hex0x.replace(/^0x/, '').toLowerCase() : '';
}

function createBulkChainProduct({ cfg, node, sleep }) {
  const pause = sleep || ((ms) => new Promise((r) => setTimeout(r, ms)));
  let headCache = { at: 0, height: null };

  async function pinnedHead() {
    if (Date.now() - headCache.at < 5000 && headCache.height !== null) return headCache.height;
    const head = await node.call('chain.getHead', {}, { timeoutMs: 5000 });
    const h = head && Number.isInteger(head.height) ? head.height : null;
    if (h === null) throw new ProductUnavailable('bulk_chain_head_unknown', 'chain.getHead returned no height');
    headCache = { at: Date.now(), height: h };
    return h;
  }

  function parseParams(ctx, { forType } = {}) {
    const q = ctx.query;
    let type = forType || q.get('type');
    if (!type) type = 'blocks';
    if (type !== 'blocks' && type !== 'transactions') {
      throw new ProductError('type must be blocks or transactions', { body: { error: 'invalid_params', detail: 'type must be blocks or transactions' } });
    }
    const from = intParam(q, 'from', { required: true, min: 0 });
    let to = intParam(q, 'to', { min: 0, fallback: null });
    const count = intParam(q, 'count', { min: 1, max: cfg.bulkMaxBlocks, fallback: null });
    if (to === null) to = count !== null ? from + count - 1 : from + 99;
    if (to < from) {
      throw new ProductError('to must be >= from', { body: { error: 'invalid_params', detail: 'to must be >= from' } });
    }
    // Hard cap enforced BEFORE any payment is requested.
    if (to - from + 1 > cfg.bulkMaxBlocks) {
      throw new ProductError('window exceeds cap', {
        body: {
          error: 'window_too_large',
          detail: `requested ${to - from + 1} blocks; max ${cfg.bulkMaxBlocks} per request — page with the cursor`,
          caps: { max_blocks: cfg.bulkMaxBlocks, max_tx_records: cfg.bulkMaxTxRecords, max_response_bytes: cfg.bulkMaxResponseBytes },
        },
      });
    }
    let addressDigest = null;
    const addr = q.get('address');
    if (addr) {
      if (type !== 'transactions') {
        throw new ProductError('address filter applies to type=transactions only', { body: { error: 'invalid_params', detail: 'address filter applies to type=transactions only' } });
      }
      try {
        addressDigest = toAccountDigestHex(addr);
      } catch (e) {
        throw new ProductError(e.message, { body: { error: 'invalid_address', detail: e.message } });
      }
    }
    const fmtRaw = q.get('format');
    let format;
    if (fmtRaw) {
      if (fmtRaw !== 'json' && fmtRaw !== 'ndjson') {
        throw new ProductError('format must be json or ndjson', { body: { error: 'invalid_params', detail: 'format must be json or ndjson' } });
      }
      format = fmtRaw;
    } else {
      const accept = String(ctx.headers['accept'] || '');
      format = accept.includes('application/json') && !accept.includes(NDJSON) ? 'json' : 'ndjson';
    }
    return { type, from, to, addressDigest, format, addressEcho: addr || null };
  }

  /**
   * Walk [from..to] in chunks, invoking onBlock(raw) for every block, in
   * order, with budgets checked BETWEEN blocks (an emitted block is never
   * sliced). Returns { nextCursor, truncatedReason }: nextCursor is the
   * first height NOT consumed (null = window complete); a failed batch or
   * a budget stop resumes exactly there.
   */
  async function walkWindow({ from, to, deadline, shouldStop, onBlock }) {
    let h = from;
    let truncatedReason = null;
    outer: while (h <= to) {
      if (Date.now() > deadline) { truncatedReason = 'time_budget'; break; }
      const stop0 = shouldStop();
      if (stop0) { truncatedReason = stop0; break; }
      const chunkEnd = Math.min(h + cfg.bulkChunkBlocks - 1, to);
      const calls = [];
      for (let i = h; i <= chunkEnd; i++) {
        calls.push({ method: 'chain.getBlockByHeight', params: { height: i, includeTxObjects: true, includeReceipts: false } });
      }
      let results;
      try {
        results = await node.batch(calls, { timeoutMs: 15000 });
      } catch (e) {
        // A failed fetch is the RESUME POINT — never a skipped hole.
        truncatedReason = `fetch_failed: ${e.message}`;
        break;
      }
      for (const raw of results) {
        if (raw === null || raw === undefined) { truncatedReason = 'beyond_head'; break outer; }
        const stop = shouldStop();
        if (stop) { truncatedReason = stop; break outer; }
        onBlock(raw);
        h += 1;
      }
      if (h <= to) await pause(25); // politeness: yield the node's loop
    }
    return { nextCursor: truncatedReason ? h : null, truncatedReason };
  }

  function buildSummary({ counts, truncatedReason, nextCursor, effTo, requestedTo }) {
    const pinnedShort = effTo < requestedTo;
    const summary = {
      type: 'summary',
      ...counts,
      truncated_reason: truncatedReason || (pinnedShort ? 'pinned_below_requested_to' : null),
      next_cursor: truncatedReason ? nextCursor : (pinnedShort ? effTo + 1 : null),
    };
    summary.complete = summary.next_cursor === null;
    return summary;
  }

  async function runExport(ctx, params) {
    const head = ctx.pinned.head;
    const effTo = Math.min(params.to, Math.max(0, head - cfg.bulkHeadMargin));
    const deadline = Date.now() + cfg.bulkExecTimeoutMs;
    const isTxExport = params.type === 'transactions';

    let bytes = 0;
    let blockCount = 0;
    let txCount = 0;
    const lines = []; // ndjson lines OR shaped objects (json format)
    const meta = {
      type: 'meta', product: 'bulk_chain', export: params.type, chain_id: null,
      head_height: head, head_margin: cfg.bulkHeadMargin,
      from_height: params.from, to_height: effTo, unit: 'nANM',
      payment: ctx.paymentMeta,
    };
    if (isTxExport) {
      meta.address = params.addressEcho;
      meta.address_digest = params.addressDigest;
    }

    const emit = (obj) => {
      if (params.format === 'ndjson') {
        const line = JSON.stringify(obj);
        bytes += line.length + 1;
        lines.push(line);
      } else {
        bytes += JSON.stringify(obj).length + 1;
        lines.push(obj);
      }
    };

    const onBlock = (raw) => {
      if (meta.chain_id === null && raw.chainId !== undefined) meta.chain_id = raw.chainId;
      blockCount += 1;
      const txs = Array.isArray(raw.transactions) ? raw.transactions : [];
      if (isTxExport) {
        for (let i = 0; i < txs.length; i++) {
          const t = txs[i];
          if (params.addressDigest && digestOf(t.from) !== params.addressDigest && digestOf(t.to) !== params.addressDigest) continue;
          txCount += 1;
          emit(txRow(raw, i, t));
        }
      } else {
        txCount += txs.length;
        emit(shapeBlock(raw));
      }
    };

    const shouldStop = () => {
      if (bytes > cfg.bulkMaxResponseBytes) return 'byte_budget';
      if (isTxExport && txCount >= cfg.bulkMaxTxRecords) return 'record_cap';
      return null;
    };

    const { nextCursor, truncatedReason } = params.from > effTo
      ? { nextCursor: null, truncatedReason: null }
      : await walkWindow({ from: params.from, to: effTo, deadline, shouldStop, onBlock });

    // A partial export (>=1 block + resume cursor) is delivered value; a
    // fetch failure before ANY block is money for nothing — throw a
    // retryable error so the paywall retries once and then issues the
    // signed failure receipt (settlement already happened).
    if (blockCount === 0 && truncatedReason && truncatedReason.startsWith('fetch_failed')) {
      const err = new Error(`chain fetch failed before any block was exported (${truncatedReason})`);
      err.retryable = true;
      throw err;
    }

    const counts = isTxExport
      ? { blocks_scanned: blockCount, txs: txCount }
      : { blocks: blockCount, txs: txCount };
    const summary = buildSummary({ counts, truncatedReason, nextCursor, effTo, requestedTo: params.to });

    let body;
    let contentType;
    if (params.format === 'ndjson') {
      body = [JSON.stringify(meta), ...lines, JSON.stringify(summary)].join('\n') + '\n';
      contentType = NDJSON;
    } else {
      body = JSON.stringify({ meta, [isTxExport ? 'transactions' : 'blocks']: lines, summary });
      contentType = 'application/json';
    }
    const headers = { 'content-type': contentType };
    let payload = body;
    if (/\bgzip\b/.test(String(ctx.headers['accept-encoding'] || '')) && body.length > 1024) {
      payload = zlib.gzipSync(Buffer.from(body), { level: 6 });
      headers['content-encoding'] = 'gzip';
    }
    return { status: 200, headers, body: payload };
  }

  return {
    id: 'bulk_chain',
    title: 'Bulk chain data export',
    description:
      `Block-range and transaction-range exports from the Animica L1 (chunked reads, cursor pagination, NDJSON/JSON, gzip). Caps per request: ${cfg.bulkMaxBlocks} blocks, ${cfg.bulkMaxTxRecords} tx records, ${cfg.bulkMaxResponseBytes} bytes. The free per-block APIs stay free.`,
    path: '/x402/chain/export',
    routes: [
      { method: 'GET', path: '/x402/chain/export' },
      { method: 'GET', path: '/x402/chain/blocks' },
      { method: 'GET', path: '/x402/chain/transactions' },
    ],
    priceUsd: cfg.bulkChainPriceUsd,
    enabled: cfg.bulkChainEnabled,
    mode: 'settle-then-execute',
    mimeType: NDJSON,
    outputSchema: {
      input: {
        type: 'http',
        method: 'GET',
        queryParams: {
          type: { type: 'string', description: 'blocks | transactions (export route only; /blocks and /transactions imply it)' },
          from: { type: 'integer', description: 'first block height (required; also the pagination cursor)' },
          to: { type: 'integer', description: `last height inclusive (window <= ${cfg.bulkMaxBlocks} blocks)` },
          count: { type: 'integer', description: 'alternative to `to`: number of blocks from `from`' },
          address: { type: 'string', description: 'transactions only: anim1… bech32m or 32-byte hex account digest; matches from/to' },
          format: { type: 'string', description: 'ndjson (default) | json' },
        },
      },
      output: {
        type: 'ndjson',
        description:
          'meta line, one block/tx per line (amounts in nANM as decimal strings), summary line with next_cursor when truncated; ?format=json returns {meta, blocks|transactions, summary}',
      },
    },

    /** Readiness = the node answers chain.getHead (cached 5 s). */
    async availability() {
      try {
        const head = await pinnedHead();
        return { available: true, head };
      } catch (e) {
        return { available: false, reason: 'bulk_chain_node_unreachable', detail: e.message };
      }
    },

    validate(ctx) {
      const forType = ctx.route.path.endsWith('/blocks') ? 'blocks'
        : ctx.route.path.endsWith('/transactions') ? 'transactions' : null;
      const params = parseParams(ctx, { forType });
      // from beyond the (cached) head is a caller mistake — reject before
      // any payment is demanded rather than selling an empty export.
      if (headCache.height !== null && params.from > headCache.height) {
        throw new ProductError('from is beyond the chain head', {
          body: { error: 'invalid_params', detail: `from=${params.from} is beyond head ${headCache.height}` },
        });
      }
      return params;
    },

    /** Pre-settle readiness: pin the head NOW; failure => nothing charged. */
    async preSettle() {
      const head = await pinnedHead();
      return { head };
    },

    async handler(ctx) {
      return runExport(ctx, ctx.params);
    },
  };
}

module.exports = { createBulkChainProduct, shapeBlock, shapeTx };
