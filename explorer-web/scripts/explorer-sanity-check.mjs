#!/usr/bin/env node
/**
 * Explorer API sanity check
 *
 * Verifies that explorer-reported head height matches the node RPC and that
 * transaction counts per block are non-negative and self-consistent.
 *
 * Usage:
 *   EXPLORER_API_URL=http://localhost:8080 RPC_URL=http://localhost:8545/rpc \
 *     node scripts/explorer-sanity-check.mjs --blocks 5
 *
 * Flags:
 *   --api / --explorer   Explorer base URL (env: EXPLORER_API_URL)
 *   --rpc                Node JSON-RPC URL (env: RPC_URL or ANIMICA_RPC_URL)
 *   --blocks             How many latest blocks to validate (default: 5)
 */

const args = process.argv.slice(2);

function parseArgs() {
  const out = { blocks: 5 };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === '--api' || a === '--explorer') {
      out.api = args[++i];
    } else if (a === '--rpc') {
      out.rpc = args[++i];
    } else if (a === '--blocks') {
      out.blocks = Number(args[++i]);
    }
  }
  return out;
}

const opts = parseArgs();
const apiBase = (opts.api || process.env.EXPLORER_API_URL || '').replace(/\/+$/, '');
const rpcUrl = (opts.rpc || process.env.RPC_URL || process.env.ANIMICA_RPC_URL || '').replace(/\/+$/, '');
const blockLimit = Number.isFinite(opts.blocks) && opts.blocks > 0 ? Math.floor(opts.blocks) : 5;

if (!apiBase || !rpcUrl) {
  console.error('Usage: EXPLORER_API_URL=<base> RPC_URL=<rpc> node scripts/explorer-sanity-check.mjs');
  process.exit(1);
}

function toInt(x) {
  if (typeof x === 'number') return Math.trunc(x);
  if (typeof x === 'string') {
    const trimmed = x.trim();
    if (trimmed.startsWith('0x')) return parseInt(trimmed, 16);
    if (/^\d+(\.\d+)?$/.test(trimmed)) return Math.trunc(Number(trimmed));
  }
  return undefined;
}

async function fetchJson(url, label, timeoutMs = 10000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(url, { headers: { Accept: 'application/json' }, signal: ctrl.signal });
    if (!res.ok) throw new Error(`${label} HTTP ${res.status}`);
    return await res.json();
  } finally {
    clearTimeout(t);
  }
}

function join(base, path) {
  if (!path.startsWith('/')) path = '/' + path;
  return base + path;
}

async function rpcCall(method, params = []) {
  const body = JSON.stringify({ jsonrpc: '2.0', id: Date.now(), method, params });
  const res = await fetch(rpcUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body,
  });
  if (!res.ok) throw new Error(`RPC ${method} HTTP ${res.status}`);
  const json = await res.json();
  if (json.error) throw new Error(`RPC ${method} error ${json.error.code}: ${json.error.message}`);
  return json.result;
}

async function fetchRpcHeadHeight() {
  const candidates = [
    { method: 'chain.getHead', params: [] },
    { method: 'chain_getHead', params: [] },
    { method: 'chain.head', params: [] },
    { method: 'getHead', params: [] },
    { method: 'omni_getHead', params: [] },
    { method: 'eth_blockNumber', params: [] },
  ];

  let lastErr;
  for (const c of candidates) {
    try {
      const res = await rpcCall(c.method, c.params);
      if (res && typeof res === 'object' && 'height' in res) {
        const h = toInt(res.height);
        if (Number.isFinite(h)) return h;
      }
      const h = toInt(res);
      if (Number.isFinite(h)) return h;
    } catch (e) {
      lastErr = e;
      continue;
    }
  }
  throw lastErr || new Error('Unable to determine head height from RPC');
}

async function tryExplorerPaths(paths) {
  for (const p of paths) {
    try {
      const data = await fetchJson(join(apiBase, p), p);
      return { path: p, data };
    } catch {
      // try next
    }
  }
  return { path: null, data: null };
}

function extractHeadHeight(stats) {
  if (!stats || typeof stats !== 'object') return undefined;
  const keys = ['headHeight', 'height', 'head_height'];
  for (const k of keys) {
    const v = toInt(stats[k]);
    if (Number.isFinite(v)) return v;
  }
  return undefined;
}

function pickBlockItems(list) {
  if (Array.isArray(list)) return list;
  if (list && typeof list === 'object') {
    if (Array.isArray(list.items)) return list.items;
  }
  return [];
}

function extractTxCount(obj) {
  if (!obj || typeof obj !== 'object') return undefined;
  if (Array.isArray(obj.txsList)) return obj.txsList.length;
  if (Array.isArray(obj.txs)) return obj.txs.length;
  const candidates = ['txs', 'txCount', 'tx_count', 'txsCount', 'transactionsCount', 'transactions'];
  for (const k of candidates) {
    if (k in obj) {
      const v = obj[k];
      if (Array.isArray(v)) return v.length;
      const n = toInt(v);
      if (Number.isFinite(n)) return n;
    }
  }
  return undefined;
}

async function main() {
  console.log(`Explorer base: ${apiBase}`);
  console.log(`RPC URL:      ${rpcUrl}`);

  const statsResp = await tryExplorerPaths(['/api/stats', '/stats', '/api/summary', '/summary']);
  if (!statsResp.data) throw new Error('Explorer stats/summary endpoint not reachable');
  const explorerHead = extractHeadHeight(statsResp.data);
  if (!Number.isFinite(explorerHead)) throw new Error('Explorer head height not found in stats payload');
  console.log(`Explorer head height (${statsResp.path}): ${explorerHead}`);

  const rpcHead = await fetchRpcHeadHeight();
  console.log(`RPC head height: ${rpcHead}`);
  if (explorerHead !== rpcHead) {
    throw new Error(`Head mismatch: explorer=${explorerHead}, rpc=${rpcHead}`);
  }

  const blocksResp = await tryExplorerPaths([
    `/api/blocks?limit=${blockLimit}`,
    `/blocks?limit=${blockLimit}`,
    `/api/blocks?pageSize=${blockLimit}`,
    `/blocks?pageSize=${blockLimit}`,
  ]);
  if (!blocksResp.data) throw new Error('Explorer blocks endpoint not reachable');
  const blockItems = pickBlockItems(blocksResp.data).slice(0, blockLimit);
  if (!blockItems.length) throw new Error('No blocks returned from explorer');

  const blocksBase = (blocksResp.path || '/api/blocks').split('?')[0];

  for (const blk of blockItems) {
    const h = toInt(blk.height ?? blk.number ?? blk.h);
    if (!Number.isFinite(h)) throw new Error(`Block missing height: ${JSON.stringify(blk)}`);
    const listCount = extractTxCount(blk);
    if (listCount !== undefined && listCount < 0) throw new Error(`Negative tx count in list for block ${h}`);

    const detail = await fetchJson(join(apiBase, `${blocksBase}/${h}`), `block ${h}`);
    const detailCount = extractTxCount(detail);
    if (detailCount !== undefined && detailCount < 0) throw new Error(`Negative tx count in detail for block ${h}`);

    let pageTotal;
    let pageItemsCount;
    try {
      const txsPage = await fetchJson(join(apiBase, `${blocksBase}/${h}/txs?limit=200`), `block ${h} txs`);
      if (txsPage && typeof txsPage === 'object') {
        if (Array.isArray(txsPage.items)) pageItemsCount = txsPage.items.length;
        if (typeof txsPage.total !== 'undefined') pageTotal = toInt(txsPage.total);
      } else if (Array.isArray(txsPage)) {
        pageItemsCount = txsPage.length;
      }
    } catch {
      // optional
    }

    if (pageTotal !== undefined && pageTotal < 0) throw new Error(`Negative tx total in txs page for block ${h}`);
    if (pageItemsCount !== undefined && pageItemsCount < 0) throw new Error(`Negative txs array length for block ${h}`);

    // Consistency checks (only compare if both sides present)
    const comparisons = [
      ['list vs detail', listCount, detailCount],
      ['list vs txs total', listCount, pageTotal],
      ['detail vs txs total', detailCount, pageTotal],
    ];
    for (const [label, a, b] of comparisons) {
      if (a !== undefined && b !== undefined && a !== b) {
        throw new Error(`Tx count mismatch (${label}) for block ${h}: ${a} vs ${b}`);
      }
    }
    if (detailCount !== undefined && pageItemsCount !== undefined && pageItemsCount > detailCount) {
      throw new Error(`Tx page items exceed reported count for block ${h}: ${pageItemsCount} > ${detailCount}`);
    }

    console.log(`Block ${h}: txs=${detailCount ?? listCount ?? pageTotal ?? 'unknown'} ✓`);
  }

  console.log(`Success: explorer and RPC agree on head (${rpcHead}); tx counts are sane for ${blockItems.length} block(s).`);
}

main().catch((err) => {
  console.error('[FAILED]', err.message || err);
  process.exit(1);
});
