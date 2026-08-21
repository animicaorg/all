'use strict';
/**
 * Chain family additions: the gateway-owned address index (store + walker)
 * and the two products it enables —
 *
 *   chain_address_history  POST /x402/chain/address-history  $0.15
 *   chain_batch_balances   POST /x402/chain/balances         $0.10
 *
 * What these tests pin down, in the order the money flows:
 *   1. the derivation (digest join, direction rule, ordering) is exactly what
 *      the product publishes, so a buyer can recompute it from raw blocks;
 *   2. every cap and shape error answers 400 BEFORE a 402 is issued and with
 *      the facilitator never touched — we do not take money for work we are
 *      about to refuse;
 *   3. a backfilling / stalled / lagging index is a 503 with a reason, never
 *      a 402: an incomplete history is not stale, it is wrong;
 *   4. cursor paging over a pinned snapshot never skips or repeats a row;
 *   5. amounts above 2^53 survive from the node's raw JSON to the paid
 *      response byte for byte.
 *
 * The walker never runs by itself here: it is driven by explicit tick()
 * calls against a mocked node, exactly as src/server.js keeps it out of
 * createGateway().
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const bech32m = require('../src/bech32m');
const { createNodeClient } = require('../src/animica-node');
const {
  createChainIndexStore, createChainIndexer, createIndexHealth, extractRows,
} = require('../src/chain-index');
const { toBigIntAmount, balanceOf } = require('../src/products/chain-balances');
const { buildTestGateway, request, paidRequest, fakeNodeFetch } = require('./gateway-helpers');

// ---------------------------------------------------------------- fixtures

const A = 'aa'.repeat(32);
const B = 'bb'.repeat(32);
const C = 'cc'.repeat(32);
/** > 2^53: JSON.parse would silently round this; the index must not. */
const HUGE = '90071992547409910000';

function addrOf(digestHex) {
  // Animica bech32m payload = alg_id (2 bytes, 0x1003 = ML-DSA-65) || digest
  return bech32m.encode('anim', Buffer.concat([Buffer.from([0x10, 0x03]), Buffer.from(digestHex, 'hex')]));
}

const ADDR_A = addrOf(A);
const ADDR_B = addrOf(B);
const ADDR_C = addrOf(C);

/**
 * Raw block JSON text mirroring the node's real response (including its
 * duplication of `txs`/`header`). Values are emitted as bare JSON NUMBERS on
 * purpose: that is how the node sends them, and it is the only way to prove
 * the BigInt-safe parse path actually works.
 */
function rawBlock(height, txs = [], { hashPrefix = '' } = {}) {
  const hash = `0x${hashPrefix}${String(height).padStart(64 - hashPrefix.length, '0')}`;
  const parent = `0x${hashPrefix}${String(Math.max(0, height - 1)).padStart(64 - hashPrefix.length, '0')}`;
  const txJson = txs
    .map((t, i) =>
      `{"hash":"0x${String(height).padStart(4, '0')}${String(i).padStart(60, 'e')}",` +
      `"from":"0x${t.from}","to":"0x${t.to}",` +
      `"gas":23100,"tip":2157169,"value":${t.value},"kind":0,"data":"0x"}`)
    .join(',');
  const header =
    `"number":${height},"hash":"${hash}","parentHash":"${parent}",` +
    `"timestamp":${1786470000 + height},"chainId":1,"thetaMicro":26858281,` +
    `"nonce":363818152316,"roots":{"stateRoot":"0x${'00'.repeat(32)}","txsRoot":"0x${'00'.repeat(32)}"}`;
  return `{${header},"transactions":[${txJson}],"txs":[${txJson}],"receipts":[],"header":{${header}}}`;
}

/** Height -> txs. A's history is deliberately spread out and interleaved. */
const TXS = {
  10: [{ from: A, to: B, value: '1000' }],
  20: [{ from: B, to: A, value: '2000' }, { from: A, to: A, value: '3000' }],
  30: [{ from: A, to: C, value: HUGE }],
  190: [{ from: C, to: A, value: '7' }],
  // Above head - margin (200 - 6 = 194): must NOT be indexed.
  196: [{ from: A, to: B, value: '999' }],
};

function indexHandlers({ head = 200, txs = TXS, hashPrefix = '', balances } = {}) {
  return {
    'chain.getHead': () => ({ height: head, hash: `0x${String(head).padStart(64, '0')}` }),
    'chain.getBlockByHeight': (p) => {
      if (p.height > head) return null;
      return { __raw: rawBlock(p.height, txs[p.height] || [], { hashPrefix }) };
    },
    // Live-verified shape (2026-08-15): confirmed_balance/spendable_balance
    // are decimal STRINGS, plus exists/unit/display_decimals/as_of_head_*.
    'state.getAddressBalance': (p) => {
      const digest = String(p.address || '').replace(/^0x/, '').toLowerCase();
      const table = balances || { [A]: '40167904340732350', [B]: '250', [C]: '0' };
      if (!(digest in table)) throw new Error(`unknown account ${digest}`);
      return {
        __raw: `{"address":"0x${digest}","exists":true,"confirmed_balance":"${table[digest]}",` +
          `"pending_incoming":null,"pending_outgoing":null,"spendable_balance":"${table[digest]}",` +
          `"unit":"nANM","display_decimals":9,"as_of_head_height":${head},` +
          `"as_of_head_hash":"0x${String(head).padStart(64, '0')}"}`,
      };
    },
  };
}

const IDX_CFG = {
  chainIndexChunkBlocks: 50,
  chainIndexChunkPauseMs: 1,
  chainIndexHeadMargin: 6,
  chainIndexMaxLagBlocks: 12,
  chainIndexBatchTimeoutMs: 5000,
  chainIndexReorgRewind: 20,
  chainIndexPollMs: 20,
};

/** A store + walker over a mocked node; nothing starts on its own. */
function buildWalker({ handlers, cfg = {}, sleep } = {}) {
  const batchSizes = [];
  const pauses = [];
  const base = fakeNodeFetch(handlers || indexHandlers());
  const fetchImpl = async (url, opts) => {
    const body = JSON.parse(opts.body);
    if (Array.isArray(body)) batchSizes.push(body.length);
    return base(url, opts);
  };
  const store = createChainIndexStore(':memory:');
  const node = createNodeClient('http://node.test/rpc', { fetchImpl });
  const indexer = createChainIndexer({
    cfg: Object.assign({}, IDX_CFG, cfg),
    node,
    store,
    sleep: sleep || (async (ms) => { pauses.push(ms); }),
  });
  return { store, node, indexer, batchSizes, pauses };
}

/**
 * A gateway whose index is already caught up: run the walker to completion
 * against the same mocked chain the gateway will see, then hand the store in.
 */
async function gatewayWithIndex({ handlers, overrides = {}, cfg = {} } = {}) {
  const h = handlers || indexHandlers();
  const w = buildWalker({ handlers: h, cfg });
  await w.indexer.tick();
  const t = await buildTestGateway({
    handlers: h,
    chainIndex: w.store,
    overrides: Object.assign({}, IDX_CFG, overrides),
  });
  return Object.assign(t, { walker: w });
}

// -------------------------------------------------------- derivation rules

test('index: direction rule — out/in for a transfer, a SINGLE self row, exact big values', () => {
  const block = JSON.parse(rawBlock(20, [
    { from: A, to: B, value: '1000' },
    { from: A, to: A, value: HUGE },
  ]).replace(/"value":(\d+)/g, '"value":"$1"'));
  const rows = extractRows(block);
  // tx0 is a transfer: exactly one row per participant.
  const t0 = rows.filter((r) => r.tx_index === 0);
  assert.deepEqual(t0.map((r) => `${r.digest.slice(0, 2)}:${r.direction}`).sort(), ['aa:out', 'bb:in']);
  assert.equal(t0[0].value, '1000');
  // tx1 is a self-transfer: ONE row, direction 'self' — never counted twice.
  const t1 = rows.filter((r) => r.tx_index === 1);
  assert.equal(t1.length, 1);
  assert.equal(t1[0].direction, 'self');
  assert.equal(t1[0].digest, A);
  assert.equal(t1[0].value, HUGE); // exact, as a string
  assert.equal(typeof t1[0].value, 'string');
});

test('index: an anim1 address resolves to the digest the chain actually uses', () => {
  assert.equal(bech32m.toAccountDigestHex(ADDR_A), A);
  assert.equal(bech32m.toAccountDigestHex(`0x${A}`), A);
});

// ------------------------------------------------------------- the walker

test('walker: backfills to head - margin in small batches, pausing between chunks', async () => {
  const w = buildWalker();
  const progress = await w.indexer.tick();
  assert.equal(progress.head_height, 200);
  assert.equal(progress.target_height, 194); // head - margin, never the tip
  assert.equal(progress.indexed_height, 194);
  assert.equal(progress.blocks_indexed, 195);
  // Gentleness: the node's single event loop is shared with miner getwork.
  assert.ok(w.batchSizes.length >= 4, `expected chunked batches, got ${w.batchSizes.length}`);
  assert.ok(Math.max(...w.batchSizes) <= 50, `batch too large: ${Math.max(...w.batchSizes)}`);
  assert.ok(w.pauses.length >= 3, 'walker must yield the node loop between chunks');
  // The block above the margin exists on the mocked chain but is NOT indexed.
  const st = w.store.getState();
  assert.equal(st.indexedHeight, 194);
  assert.equal(w.store.countHistory({ digest: A, toHeight: 200 }), 5);
});

test('walker: a failed batch resumes at the last COMMITTED height, never a hole', async () => {
  let fail = false;
  const handlers = indexHandlers();
  const flaky = Object.assign({}, handlers, {
    'chain.getBlockByHeight': (p) => {
      if (fail && p.height >= 100) throw new Error('node busy');
      return handlers['chain.getBlockByHeight'](p);
    },
  });
  const w = buildWalker({ handlers: flaky, cfg: { chainIndexChunkBlocks: 25 } });
  fail = true;
  const first = await w.indexer.tick();
  assert.match(first.truncated_reason, /fetch_failed/);
  assert.equal(first.indexed_height, 99, 'must commit exactly the blocks that came back');
  fail = false;
  const second = await w.indexer.tick();
  assert.equal(second.indexed_height, 194);
  // No gap: every height 0..194 is present.
  const n = w.store.db.prepare('SELECT COUNT(*) AS n FROM blocks').get().n;
  assert.equal(n, 195);
});

test('walker: a parentHash break rewinds and re-indexes instead of stitching forks', async () => {
  const w = buildWalker({ handlers: indexHandlers({ head: 60 }), cfg: { chainIndexChunkBlocks: 20, chainIndexReorgRewind: 10 } });
  await w.indexer.tick();
  assert.equal(w.store.getState().indexedHeight, 54);
  // Same heights, different hashes => the stored tip no longer matches.
  w.store.db.prepare('UPDATE blocks SET hash = ? WHERE height = ?').run('0xdeadbeef', 54);
  w.store.setMeta('indexed_hash', '0xdeadbeef');
  w.store.setMeta('indexed_height', '54');
  const w2 = buildWalker({ handlers: indexHandlers({ head: 80 }), cfg: { chainIndexChunkBlocks: 20, chainIndexReorgRewind: 10 } });
  // Reuse the poisoned store on a fresh walker over a longer chain.
  const indexer = createChainIndexer({
    cfg: Object.assign({}, IDX_CFG, { chainIndexChunkBlocks: 20, chainIndexReorgRewind: 10 }),
    node: w2.node,
    store: w.store,
    sleep: async () => {},
  });
  const progress = await indexer.tick();
  assert.equal(progress.rewinds, 1, 'the continuity break must trigger exactly one rewind');
  assert.equal(progress.indexed_height, 74); // 80 - margin
  const tip = w.store.db.prepare('SELECT hash FROM blocks WHERE height = 54').get();
  assert.notEqual(tip.hash, '0xdeadbeef', 'the forked block must have been re-indexed');
});

test('walker: never touches the node until tick(); start()/stop() are explicit', async () => {
  const w = buildWalker({ handlers: indexHandlers({ head: 40 }), cfg: { chainIndexChunkBlocks: 20 } });
  assert.equal(w.batchSizes.length, 0);
  assert.equal(w.indexer.status().running, false);
  assert.equal(w.store.getState().indexedHeight, -1);
  assert.equal(w.indexer.start(), true);
  assert.equal(w.indexer.start(), false, 'start must be idempotent');
  try {
    const deadline = Date.now() + 5000;
    while (w.store.getState().indexedHeight < 34 && Date.now() < deadline) {
      await new Promise((r) => setTimeout(r, 10));
    }
    assert.equal(w.store.getState().indexedHeight, 34);
  } finally {
    w.indexer.stop();
  }
  assert.equal(w.indexer.status().running, false);
});

// ---------------------------------------------------------- store queries

test('index store: cursor paging over a pinned snapshot never skips or repeats', () => {
  const w = buildWalker();
  return w.indexer.tick().then(() => {
    const asOf = w.store.getState().indexedHeight;
    const seen = [];
    let after = null;
    for (let page = 0; page < 10; page++) {
      const { rows, hasMore } = w.store.queryHistory({ digest: A, toHeight: asOf, limit: 2, after });
      seen.push(...rows.map((r) => `${r.height}:${r.tx_index}`));
      if (!hasMore) break;
      const last = rows[rows.length - 1];
      after = { height: last.height, txIndex: last.tx_index };
    }
    assert.deepEqual(seen, ['190:0', '30:0', '20:1', '20:0', '10:0']);
    assert.equal(new Set(seen).size, seen.length, 'no repeats');

    // ascending is the exact reverse, with the same exclusivity rule
    const asc = [];
    after = null;
    for (let page = 0; page < 10; page++) {
      const { rows, hasMore } = w.store.queryHistory({ digest: A, toHeight: asOf, limit: 2, after, order: 'asc' });
      asc.push(...rows.map((r) => `${r.height}:${r.tx_index}`));
      if (!hasMore) break;
      const last = rows[rows.length - 1];
      after = { height: last.height, txIndex: last.tx_index };
    }
    assert.deepEqual(asc, [...seen].reverse());
  });
});

test('index store: direction filter and window bounds', async () => {
  const w = buildWalker();
  await w.indexer.tick();
  const asOf = w.store.getState().indexedHeight;
  const dirs = (d) => w.store.queryHistory({ digest: A, toHeight: asOf, direction: d, limit: 100 }).rows
    .map((r) => `${r.height}:${r.tx_index}`);
  assert.deepEqual(dirs('out'), ['30:0', '10:0']);
  assert.deepEqual(dirs('in'), ['190:0', '20:0']);
  assert.deepEqual(dirs('self'), ['20:1']);
  assert.equal(w.store.countHistory({ digest: A, toHeight: asOf, direction: 'out' }), 2);
  const windowed = w.store.queryHistory({ digest: A, fromHeight: 11, toHeight: 100, limit: 100 }).rows;
  assert.deepEqual(windowed.map((r) => r.height), [30, 20, 20]);
});

test('index store: rollbackAbove restores the exact tip hash and drops rows', async () => {
  const w = buildWalker();
  await w.indexer.tick();
  const hashAt25 = w.store.db.prepare('SELECT hash FROM blocks WHERE height = 25').get().hash;
  w.store.rollbackAbove(25);
  const st = w.store.getState();
  assert.equal(st.indexedHeight, 25);
  assert.equal(st.indexedHash, hashAt25);
  // Only h10 (out) and h20 (in + self) survive; h30/h190 are gone with the rows above 25.
  assert.equal(w.store.countHistory({ digest: A, toHeight: 1000 }), 3);
  assert.equal(w.store.db.prepare('SELECT COUNT(*) AS n FROM address_tx WHERE height > 25').get().n, 0);
});

// ------------------------------------------------ availability / gating

test('history: a backfilling index is 503 with progress — never a 402', async () => {
  const t = await buildTestGateway({ handlers: indexHandlers() }); // empty index
  try {
    const cat = await request(t.baseUrl, '/x402');
    const entry = cat.json.products.find((p) => p.id === 'chain_address_history');
    assert.equal(entry.available, false);
    assert.equal(entry.unavailable_reason, 'chain_index_never_ran');
    assert.equal(entry.price, '0.15');

    const res = await request(t.baseUrl, '/x402/chain/address-history', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ address: ADDR_A }),
    });
    assert.equal(res.status, 503, 'an incomplete index must never be sold');
    assert.equal(res.json.error, 'chain_index_never_ran');
    assert.equal(t.fac.calls.verify.length, 0);
    assert.equal(t.fac.calls.settle.length, 0);
  } finally {
    await t.close();
  }
});

test('history: an index that fell behind the head is 503 chain_index_stale', async () => {
  const handlers = indexHandlers();
  const w = buildWalker({ handlers });
  await w.indexer.tick();               // caught up at 194 with head 200
  w.store.rollbackAbove(100);           // now 100 blocks behind
  const t = await buildTestGateway({ handlers, chainIndex: w.store, overrides: IDX_CFG });
  try {
    const res = await request(t.baseUrl, '/x402/chain/address-history', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ address: ADDR_A }),
    });
    assert.equal(res.status, 503);
    assert.equal(res.json.error, 'chain_index_stale', 'a previously caught-up index reports stale, not backfilling');
    assert.equal(res.json.index.lag_blocks, 100);
    assert.equal(res.json.index.max_lag_blocks, 12);
    assert.equal(t.fac.calls.verify.length, 0);
  } finally {
    await t.close();
  }
});

test('history: a stalled walker fails closed even while the lag still looks fine', async () => {
  const handlers = indexHandlers();
  const w = buildWalker({ handlers });
  await w.indexer.tick();
  // Backdate the heartbeat: the index is current, but nothing is watching it.
  w.store.setMeta('last_tick_ms', String(Date.now() - 10 * 60 * 1000));
  const t = await buildTestGateway({ handlers, chainIndex: w.store, overrides: IDX_CFG });
  try {
    const res = await request(t.baseUrl, '/x402/chain/address-history', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ address: ADDR_A }),
    });
    assert.equal(res.status, 503);
    assert.equal(res.json.error, 'chain_index_walker_stalled');
    assert.equal(t.fac.calls.settle.length, 0);
  } finally {
    await t.close();
  }
});

test('history: the index gate also refuses when the head cannot be resolved', async () => {
  const handlers = indexHandlers();
  const w = buildWalker({ handlers });
  await w.indexer.tick();
  const down = Object.assign({}, handlers, { 'chain.getHead': () => { throw new Error('node down'); } });
  const t = await buildTestGateway({ handlers: down, chainIndex: w.store, overrides: IDX_CFG });
  try {
    const res = await request(t.baseUrl, '/x402/chain/address-history', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ address: ADDR_A }),
    });
    assert.equal(res.status, 503);
    assert.equal(res.json.error, 'chain_index_node_unreachable');
  } finally {
    await t.close();
  }
});

test('health gate: a disabled index is advertised as disabled, not broken', async () => {
  const store = createChainIndexStore(':memory:');
  const health = createIndexHealth({
    cfg: { chainIndexEnabled: false, chainIndexMaxLagBlocks: 12, chainIndexMaxTickAgeMs: 1000, chainIndexHeadMargin: 6 },
    store,
    node: { call: async () => ({ height: 10 }) },
  });
  const h = await health.check();
  assert.equal(h.ok, false);
  assert.equal(h.reason, 'chain_index_disabled');
});

// ------------------------------------------------- history: the paid path

test('history: 402 then a paid page with the documented derivation and exact big amounts', async () => {
  const t = await gatewayWithIndex();
  try {
    const { first, paid } = await paidRequest(t.baseUrl, '/x402/chain/address-history', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ address: ADDR_A }),
    });
    assert.equal(first.status, 402);
    const offer = JSON.parse(first.text);
    assert.equal(offer.accepts[0].maxAmountRequired, '150000'); // $0.15 in USDC atomic units
    assert.equal(paid.status, 200);

    const b = paid.json;
    assert.equal(b.product, 'chain_address_history');
    assert.equal(b.address, ADDR_A, 'the caller\'s input form is echoed back');
    assert.equal(b.account_digest, `0x${A}`);
    assert.equal(b.unit, 'nANM');
    assert.equal(b.chain_id, 1);
    assert.equal(b.index.as_of_height, 194);
    assert.equal(b.index.head_margin, 6);
    assert.equal(b.count, 5);
    assert.equal(b.total, 5);
    assert.equal(b.has_more, false);
    assert.equal(b.next_cursor, null);
    assert.deepEqual(b.transactions.map((r) => `${r.height}:${r.tx_index}:${r.direction}`), [
      '190:0:in', '30:0:out', '20:1:self', '20:0:in', '10:0:out',
    ]);
    // The >2^53 transfer survives as an exact decimal string.
    const huge = b.transactions.find((r) => r.height === 30);
    assert.equal(huge.value, HUGE);
    assert.equal(typeof huge.value, 'string');
    assert.equal(huge.counterparty, `0x${C}`);
    // The block above head - margin was never indexed, so it is not sold.
    assert.ok(!b.transactions.some((r) => r.height === 196));
    // Derivation is published with the answer.
    assert.match(b.derivation.account_digest, /payload\[2:34\]/);
    assert.match(b.derivation.direction, /'self'/);
    assert.match(b.derivation.cursor, /strictly after/);
    // Payment metadata rides along, exactly one settlement.
    assert.equal(b.payment.price_usd, '0.15');
    assert.equal(t.fac.settled(), 1);
  } finally {
    await t.close();
  }
});

test('history: cursor paging returns every row exactly once across pages', async () => {
  const t = await gatewayWithIndex();
  try {
    const seen = [];
    let cursor = null;
    for (let page = 0; page < 6; page++) {
      const { paid } = await paidRequest(t.baseUrl, '/x402/chain/address-history', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(Object.assign({ address: ADDR_A, limit: 2 }, cursor ? { cursor } : {})),
      });
      assert.equal(paid.status, 200);
      seen.push(...paid.json.transactions.map((r) => `${r.height}:${r.tx_index}`));
      if (!paid.json.has_more) { cursor = paid.json.next_cursor; break; }
      cursor = paid.json.next_cursor;
      assert.match(cursor, /^194:\d+:\d+$/, 'the cursor pins the snapshot it was issued against');
    }
    assert.deepEqual(seen, ['190:0', '30:0', '20:1', '20:0', '10:0']);
    assert.equal(cursor, null, 'the last page closes the cursor');
    assert.equal(t.fac.settled(), 3, 'one settlement per page, no more');
  } finally {
    await t.close();
  }
});

test('history: direction filter, ascending order and height window', async () => {
  const t = await gatewayWithIndex();
  try {
    const out = await paidRequest(t.baseUrl, '/x402/chain/address-history', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ address: ADDR_A, direction: 'out', order: 'asc' }),
    });
    assert.deepEqual(out.paid.json.transactions.map((r) => r.height), [10, 30]);
    assert.equal(out.paid.json.total, 2);

    const win = await paidRequest(t.baseUrl, '/x402/chain/address-history', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ address: ADDR_B, from_height: 0, to_height: 15 }),
    });
    assert.deepEqual(win.paid.json.transactions.map((r) => `${r.height}:${r.direction}`), ['10:in']);
  } finally {
    await t.close();
  }
});

test('history: caps and bad input answer 400 BEFORE any payment is requested', async () => {
  const t = await gatewayWithIndex();
  const post = (body) => request(t.baseUrl, '/x402/chain/address-history', {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(body),
  });
  try {
    const over = await post({ address: ADDR_A, limit: 5000 });
    assert.equal(over.status, 400);
    assert.equal(over.json.error, 'limit_too_large');
    assert.equal(over.json.caps.max_limit, 500);

    const badAddr = await post({ address: 'anim1notanaddress' });
    assert.equal(badAddr.status, 400);
    assert.equal(badAddr.json.error, 'invalid_address');

    // An EMPTY object is absent input, not bad input — it is exactly what a
    // discovery probe sends, so it gets the 402 OFFER rather than a 400. The
    // x402 trust monitor POSTs {} and was scoring these products as erroring.
    // Genuinely bad input (above and below) still answers 400 before payment.
    const noAddr = await post({});
    assert.equal(noAddr.status, 402);

    const badCursor = await post({ address: ADDR_A, cursor: 'nonsense' });
    assert.equal(badCursor.status, 400);
    assert.equal(badCursor.json.error, 'invalid_cursor');

    const ahead = await post({ address: ADDR_A, cursor: '9999:100:0' });
    assert.equal(ahead.status, 400);
    assert.equal(ahead.json.error, 'invalid_cursor');
    assert.equal(ahead.json.index.as_of_height, 194);

    const badDir = await post({ address: ADDR_A, direction: 'sideways' });
    assert.equal(badDir.status, 400);

    // Not one of these ever reached the facilitator.
    assert.equal(t.fac.calls.verify.length, 0);
    assert.equal(t.fac.calls.settle.length, 0);
  } finally {
    await t.close();
  }
});

test('history: an address with no activity is a valid, cheap, EMPTY answer', async () => {
  const t = await gatewayWithIndex();
  try {
    const unknown = addrOf('99'.repeat(32));
    const { paid } = await paidRequest(t.baseUrl, '/x402/chain/address-history', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ address: unknown }),
    });
    assert.equal(paid.status, 200);
    assert.equal(paid.json.count, 0);
    assert.equal(paid.json.total, 0);
    assert.equal(paid.json.has_more, false);
    assert.deepEqual(paid.json.transactions, []);
  } finally {
    await t.close();
  }
});

// ------------------------------------------------ balances: the paid path

test('balances: cap is enforced before settlement, with the cap in the body', async () => {
  const t = await buildTestGateway({ handlers: indexHandlers() });
  try {
    const many = Array.from({ length: 501 }, () => ADDR_A);
    const res = await request(t.baseUrl, '/x402/chain/balances', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ addresses: many }),
    });
    assert.equal(res.status, 400);
    assert.equal(res.json.error, 'too_many_addresses');
    assert.equal(res.json.caps.max_addresses, 500);
    assert.equal(t.fac.calls.verify.length, 0);
    assert.equal(t.fac.calls.settle.length, 0);

    const bad = await request(t.baseUrl, '/x402/chain/balances', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ addresses: [ADDR_A, 'not-an-address'] }),
    });
    assert.equal(bad.status, 400);
    assert.equal(bad.json.error, 'invalid_address');
    assert.equal(bad.json.index, 1, 'the offending entry is named');

    const empty = await request(t.baseUrl, '/x402/chain/balances', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ addresses: [] }),
    });
    assert.equal(empty.status, 400);
    assert.equal(t.fac.calls.verify.length, 0);
  } finally {
    await t.close();
  }
});

test('balances: one batched RPC, deduped, BigInt-exact decimal strings', async () => {
  const handlers = indexHandlers();
  const t = await buildTestGateway({ handlers });
  try {
    const { first, paid } = await paidRequest(t.baseUrl, '/x402/chain/balances', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      // A repeated twice: three entries, two unique accounts.
      body: JSON.stringify({ addresses: [ADDR_A, ADDR_B, ADDR_A] }),
    });
    assert.equal(first.status, 402);
    assert.equal(JSON.parse(first.text).accepts[0].maxAmountRequired, '100000'); // $0.10
    assert.equal(paid.status, 200);

    const b = paid.json;
    assert.equal(b.product, 'chain_batch_balances');
    assert.equal(b.unit, 'nANM');
    assert.equal(b.decimals, 9);
    assert.equal(b.count, 3);
    assert.equal(b.unique_addresses, 2, 'duplicates cost the node one lookup');
    assert.equal(b.failed_lookups, 0);
    assert.deepEqual(b.balances.map((e) => e.address), [ADDR_A, ADDR_B, ADDR_A]);
    // The live rank-1 balance (4.0e16 nANM) is ABOVE 2^53: exact as a string,
    // summed as BigInt, and over UNIQUE accounts only.
    assert.equal(b.balances[0].balance, '40167904340732350');
    assert.equal(typeof b.balances[0].balance, 'string');
    assert.equal(b.balances[0].spendable_balance, '40167904340732350');
    assert.equal(b.balances[0].exists, true);
    assert.equal(b.balances[2].balance, '40167904340732350');
    assert.equal(b.total_balance, '40167904340732600');
    assert.ok(BigInt(b.total_balance) > 2n ** 53n, 'the fixture must exercise the >2^53 path');
    assert.equal(b.as_of.head_height, 200);
    assert.equal(b.as_of.consistent, true);
    assert.equal(b.payment.price_usd, '0.10');
    assert.equal(t.fac.settled(), 1);
    // settle-then-execute: the head was pinned before the payer's USDC moved
    const pinIdx = t.events.indexOf('node:chain.getHead');
    const settleIdx = t.events.indexOf('fac:settle');
    const fanIdx = t.events.indexOf('node:state.getAddressBalance');
    assert.ok(pinIdx !== -1 && pinIdx < settleIdx && settleIdx < fanIdx,
      `expected head pin -> settle -> fan-out, got ${t.events.join(',')}`);
  } finally {
    await t.close();
  }
});

test('balances: a single rejected account is DATA, not a poisoned $0.10 batch', async () => {
  const handlers = indexHandlers();
  const t = await buildTestGateway({ handlers });
  try {
    const ghost = addrOf('99'.repeat(32)); // the mock node rejects unknown accounts
    const { paid } = await paidRequest(t.baseUrl, '/x402/chain/balances', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ addresses: [ADDR_A, ghost] }),
    });
    assert.equal(paid.status, 200, 'the rest of the batch must still be delivered');
    assert.equal(paid.json.failed_lookups, 1);
    assert.equal(paid.json.balances[0].balance, '40167904340732350');
    assert.equal(paid.json.balances[1].balance, null);
    assert.match(paid.json.balances[1].error, /unknown account/);
    assert.equal(paid.json.total_balance, '40167904340732350');
  } finally {
    await t.close();
  }
});

test('balances: an unreachable node refuses before payment, never after', async () => {
  const handlers = Object.assign({}, indexHandlers(), {
    'chain.getHead': () => { throw new Error('node down'); },
  });
  const t = await buildTestGateway({ handlers });
  try {
    const res = await request(t.baseUrl, '/x402/chain/balances', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ addresses: [ADDR_A] }),
    });
    assert.equal(res.status, 503);
    assert.equal(res.json.error, 'chain_balances_node_unreachable');
    assert.equal(t.fac.calls.settle.length, 0);
  } finally {
    await t.close();
  }
});

test('balances: a NUMERIC confirmed_balance above 2^53 still survives exactly', async () => {
  // The live node sends decimal strings; this proves the node client's
  // BigInt-safe quoting covers balance keys too, so a node build that ever
  // emitted a bare JSON number would degrade loudly, not silently.
  // Odd and above 2^53 => provably not representable as a double.
  const LOSSY = '40167904340732351';
  const naive = String(JSON.parse(`{"v":${LOSSY}}`).v);
  assert.notEqual(naive, LOSSY, 'sanity: the fixture must actually be lossy under JSON.parse');
  const handlers = Object.assign({}, indexHandlers(), {
    'state.getAddressBalance': (p) => ({
      __raw: `{"address":"${p.address}","exists":true,"confirmed_balance":${LOSSY},` +
        `"unit":"nANM","display_decimals":9,"as_of_head_height":200}`,
    }),
  });
  const t = await buildTestGateway({ handlers });
  try {
    const { paid } = await paidRequest(t.baseUrl, '/x402/chain/balances', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ addresses: [ADDR_A] }),
    });
    assert.equal(paid.status, 200);
    assert.equal(paid.json.balances[0].balance, LOSSY);
    assert.notEqual(paid.json.balances[0].balance, naive, 'a naive JSON.parse would have corrupted this');
  } finally {
    await t.close();
  }
});

test('balances: amount parsing is BigInt-only — decimal, hex and refusal of floats', () => {
  assert.equal(toBigIntAmount('4000000000000000'), 4000000000000000n);
  assert.equal(toBigIntAmount('0x8eb47f0599e9be'), 0x8eb47f0599e9ben);
  assert.equal(toBigIntAmount(12345), 12345n);
  assert.throws(() => toBigIntAmount(1.5), /unsafe JS number/);
  assert.throws(() => toBigIntAmount('12.5'), /unparseable/);
  assert.equal(balanceOf({ confirmed_balance: '7' }), 7n);
  assert.equal(balanceOf(null), null);
  assert.throws(() => balanceOf({ nope: 1 }), /balance field missing/);
});

// ---------------------------------------------------------------- catalog

test('catalog: both chain products publish price, caps and an input schema', async () => {
  const t = await gatewayWithIndex();
  try {
    const cat = await request(t.baseUrl, '/x402');
    const byId = Object.fromEntries(cat.json.products.map((p) => [p.id, p]));

    const hist = byId.chain_address_history;
    assert.equal(hist.price, '0.15');
    assert.equal(hist.price_atomic, '150000');
    assert.equal(hist.available, true);
    assert.deepEqual(hist.endpoints, ['POST /x402/chain/address-history']);
    assert.equal(hist.outputSchema.input.bodyType, 'json');
    assert.ok(hist.outputSchema.input.bodyFields.cursor);
    assert.match(hist.description, /free/i, 'the listing must say the free lookups stay free');

    const bal = byId.chain_batch_balances;
    assert.equal(bal.price, '0.10');
    assert.equal(bal.price_atomic, '100000');
    assert.equal(bal.available, true);
    assert.ok(bal.outputSchema.input.bodyFields.addresses);
    assert.match(bal.description, /500/);
  } finally {
    await t.close();
  }
});

test('metrics: index height/lag/tick-age are exported for the fail-closed gate', async () => {
  const t = await gatewayWithIndex();
  try {
    const res = await request(t.baseUrl, '/metrics');
    assert.equal(res.status, 200);
    assert.match(res.text, /^x402_chain_index_height 194$/m);
    assert.match(res.text, /^x402_chain_index_lag_blocks 6$/m);
    assert.match(res.text, /^x402_chain_index_last_tick_age_seconds \d+$/m);
    // every metric line still parses as Prometheus text (name value)
    for (const line of res.text.split('\n')) {
      if (!line || line.startsWith('#')) continue;
      assert.match(line, /^[a-zA-Z_][a-zA-Z0-9_]*(\{[^}]*\})? -?[0-9.e+]+$/, `bad metric line: ${line}`);
    }
  } finally {
    await t.close();
  }
});

test('cli: `animica-x402 index status` reports backfill progress from the real DB', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'x402-index-'));
  const dbPath = path.join(dir, 'x402-chain-index.db');
  try {
    const store = createChainIndexStore(dbPath);
    const node = createNodeClient('http://node.test/rpc', { fetchImpl: fakeNodeFetch(indexHandlers({ head: 60 })) });
    const indexer = createChainIndexer({ cfg: IDX_CFG, node, store, sleep: async () => {} });
    await indexer.tick();
    store.close();

    const bin = path.join(__dirname, '..', 'bin', 'animica-x402');
    const r = spawnSync(process.execPath, [bin, 'index', 'status', '--json'], {
      encoding: 'utf8',
      env: Object.assign({}, process.env, { X402_CHAIN_INDEX_DB_PATH: dbPath }),
    });
    assert.equal(r.status, 0, r.stderr);
    const [row] = JSON.parse(r.stdout);
    assert.equal(row.indexed_height, 54); // 60 - margin 6
    assert.equal(row.head_height, 60);
    assert.equal(row.lag, 6);
    assert.equal(row.blocks, 55);
    assert.equal(row.accounts, 3); // A, B, C
    assert.notEqual(row.caught_up, 'never');
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

// The root .gitignore excludes a `chain-` prefixed DIRECTORY pattern meant
// for on-disk chain DATA dirs — and it also swallowed this app's
// src/chain-index SOURCE directory. That is the failure mode where
// everything works from the working tree while every fresh clone is broken,
// which this repo has already been bitten by once.
// apps/x402-gateway/.gitignore re-includes the directory; this test makes
// sure nobody quietly removes that line, or adds another such trap.
test('packaging: no source file of this app is silently gitignored', () => {
  const appRoot = path.join(__dirname, '..');
  const files = [];
  const walk = (dir) => {
    for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
      if (e.name === 'node_modules' || e.name === 'state' || e.name.startsWith('.')) continue;
      const p = path.join(dir, e.name);
      if (e.isDirectory()) walk(p);
      else if (/\.(js|mjs|json)$/.test(e.name)) files.push(p);
    }
  };
  walk(path.join(appRoot, 'src'));
  walk(path.join(appRoot, 'test'));
  files.push(path.join(appRoot, 'bin', 'animica-x402'));

  const r = spawnSync('git', ['check-ignore', '--stdin'], {
    input: files.join('\n'), encoding: 'utf8', cwd: appRoot,
  });
  if (r.error || r.status === 128) return; // no git available: nothing to prove
  const ignored = String(r.stdout || '').split('\n').filter(Boolean);
  assert.deepEqual(ignored, [],
    `these files exist on disk but are gitignored — they would vanish from a fresh clone:\n  ${ignored.join('\n  ')}`);
});

test('config: a freshness gate that could never open is refused at startup', () => {
  const cfgMod = require('../src/config');
  assert.throws(
    () => cfgMod.loadGatewayConfig({ X402_CHAIN_INDEX_MAX_LAG_BLOCKS: '6', X402_CHAIN_INDEX_HEAD_MARGIN: '6' }),
    /must exceed/
  );
  assert.throws(
    () => cfgMod.loadGatewayConfig({ X402_CHAIN_HISTORY_DEFAULT_LIMIT: '900', X402_CHAIN_HISTORY_MAX_LIMIT: '500' }),
    /exceeds/
  );
});
