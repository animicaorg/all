'use strict';
/**
 * THE MESH INDEX — a union catalogue of every x402 service we can find,
 * scored for whether an agent should actually spend money on it.
 *
 * WHAT THIS IS FOR. Directories tell you a service EXISTS. Before an agent
 * spends, it needs three harder things: can I invoke this at all, what does it
 * really cost, and is the demand behind it real. This module answers those and
 * refuses to pretend when it cannot.
 *
 * SOURCES. Coinbase Bazaar (15k resources, priced, with 30-day call and unique
 * payer counts) and 402index (95k rows, latency and reliability but almost no
 * prices and no schemas). They overlap heavily — 402index mirrors a large slice
 * of Bazaar — so entries are merged by canonical resource URL and every record
 * carries the sources it came from.
 *
 * THE THREE THINGS THAT MAKE THIS MORE THAN A PROXY:
 *
 * 1. CALLABILITY. Roughly 5% of Bazaar entries carry a call spec. The rest name
 *    a URL and nothing about how to invoke it. That distinction is the single
 *    most useful fact for an agent planning to spend, so it is a first-class
 *    field rather than something a caller has to infer from missing keys.
 *
 * 2. PRICE SANITY. One live listing advertises ten billion dollars. Prices come
 *    from merchants, not from us, and a planner that trusts them arithmetically
 *    is one bad row away from a nonsense plan. Anything outside a plausible band
 *    is flagged and excluded from ranking rather than silently normalised —
 *    quietly rewriting a merchant's stated price would be worse.
 *
 * 3. PAYER DIVERSITY. `l30DaysTotalCalls` alone is trivially inflatable by the
 *    merchant calling itself. The ratio of unique payers to total calls is not:
 *    a service with 900 calls from 900 payers is meaningfully different from one
 *    with 900 calls from 2. We report both, and concentration suppresses the
 *    demand signal instead of amplifying it.
 *
 * NO INFERENCE. Matching is lexical (BM25-style over descriptions). It is
 * cheap, deterministic and explains itself; embedding 15k descriptions is a
 * clear upgrade but not one to hide behind a first version.
 */

const BAZAAR = 'https://api.cdp.coinbase.com/platform/v2/x402/discovery/resources';
const INDEX402 = 'https://402index.io/api/v1/services';

/**
 * A price band for ranking. The ceiling exists because the directory contains
 * a $10,000,000,000 listing; the floor catches zero/negative rows that would
 * otherwise look like the cheapest option available.
 */
const MIN_PRICE_USD = 0.0000001;
const MAX_PLAUSIBLE_USD = 100;

function num(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

/** Canonical form for merging the same resource across directories. */
function canon(url) {
  try {
    const u = new URL(String(url));
    u.hash = '';
    u.protocol = 'https:';
    u.hostname = u.hostname.toLowerCase().replace(/^www\./, '');
    let p = u.pathname.replace(/\/+$/, '');
    return `${u.hostname}${p}${u.search}`;
  } catch {
    return String(url);
  }
}

function get(o, ...path) {
  let cur = o;
  for (const k of path) {
    if (!cur || typeof cur !== 'object') return undefined;
    cur = cur[k];
  }
  return cur;
}

/**
 * A call spec, if the merchant published one anywhere we know to look. Bazaar
 * puts it in three different places depending on how the resource registered,
 * and an agent should not have to know that.
 */
function callSpecOf(item) {
  const sources = [item, ...(Array.isArray(item.accepts) ? item.accepts : [])];
  for (const s of sources) {
    const info = get(s, 'extensions', 'bazaar', 'info');
    if (info && (info.method || info.inputSchema || info.queryParams || info.body)) {
      return {
        method: info.method || null,
        input_schema: info.inputSchema || null,
        query_params: info.queryParams ? Object.keys(info.queryParams) : null,
        body_example: info.body || null,
        source: 'bazaar.info',
      };
    }
    const out = s && s.outputSchema;
    if (out && typeof out === 'object' && out.input) {
      return {
        method: get(out, 'input', 'method') || null,
        input_schema: get(out, 'input', 'bodyFields') || null,
        body_type: get(out, 'input', 'bodyType') || null,
        source: 'outputSchema.input',
      };
    }
  }
  return null;
}

function normalizeBazaar(item) {
  const accepts = Array.isArray(item.accepts) ? item.accepts : [];
  const a = accepts[0] || {};
  const amount = num(a.amount);
  // USDC and most x402 stablecoins are 6-decimal. `extra.decimals` wins when present.
  const decimals = num(get(a, 'extra', 'decimals')) ?? 6;
  const priceUsd = amount === null ? null : amount / 10 ** decimals;
  const q = item.quality || {};
  const calls = num(q.l30DaysTotalCalls) ?? 0;
  const payers = num(q.l30DaysUniquePayers) ?? 0;
  return {
    resource: item.resource,
    key: canon(item.resource),
    description: String(item.description || '').slice(0, 1200),
    price_usd: priceUsd,
    asset: get(a, 'extra', 'name') || a.asset || null,
    network: a.network || null,
    pay_to: a.payTo || a.recipient || null,
    scheme: a.scheme || null,
    calls_30d: calls,
    unique_payers_30d: payers,
    last_called_at: q.lastCalledAt || null,
    call_spec: callSpecOf(item),
    sources: ['bazaar'],
  };
}

function normalize402index(row) {
  const p = num(row.price_usd);
  return {
    resource: row.url,
    key: canon(row.url),
    description: String(row.description || row.name || '').slice(0, 1200),
    price_usd: p,
    asset: row.payment_asset || null,
    network: row.payment_network || null,
    pay_to: null,
    scheme: null,
    calls_30d: 0,
    unique_payers_30d: 0,
    last_called_at: row.last_checked || null,
    latency_p50_ms: num(row.latency_p50_ms),
    reliability_score: num(row.reliability_score),
    health_status: row.health_status || null,
    call_spec: null,
    sources: ['402index'],
  };
}

/** Later sources fill gaps; they never overwrite a value we already trust. */
function merge(a, b) {
  const out = { ...a };
  for (const [k, v] of Object.entries(b)) {
    if (k === 'sources') continue;
    if (out[k] === null || out[k] === undefined || out[k] === '' || out[k] === 0) {
      if (v !== null && v !== undefined && v !== '') out[k] = v;
    }
  }
  out.sources = [...new Set([...(a.sources || []), ...(b.sources || [])])];
  // Prefer the longer description: one directory routinely stores only a name.
  if ((b.description || '').length > (out.description || '').length) out.description = b.description;
  return out;
}

// ---------------------------------------------------------------------------
// Scoring
// ---------------------------------------------------------------------------

const STOP = new Set(['the', 'a', 'an', 'and', 'or', 'for', 'of', 'to', 'in', 'on', 'with',
  'is', 'are', 'be', 'by', 'from', 'as', 'at', 'this', 'that', 'it', 'api', 'x402', 'get', 'endpoint']);

function tokens(s) {
  return String(s).toLowerCase().match(/[a-z0-9]+/g)?.filter((t) => t.length > 2 && !STOP.has(t)) || [];
}

/** BM25 over descriptions. Deterministic, explainable, and needs no model. */
function buildBm25(records) {
  const df = new Map();
  const docs = records.map((r) => {
    const t = tokens(`${r.description} ${r.resource}`);
    for (const w of new Set(t)) df.set(w, (df.get(w) || 0) + 1);
    return t;
  });
  const N = docs.length || 1;
  const avgLen = docs.reduce((s, d) => s + d.length, 0) / N || 1;
  return function score(i, queryTokens) {
    const d = docs[i];
    if (!d.length) return 0;
    const tf = new Map();
    for (const w of d) tf.set(w, (tf.get(w) || 0) + 1);
    let s = 0;
    for (const q of queryTokens) {
      const f = tf.get(q);
      if (!f) continue;
      const idf = Math.log(1 + (N - (df.get(q) || 0) + 0.5) / ((df.get(q) || 0) + 0.5));
      s += idf * ((f * 2.5) / (f + 1.5 * (0.25 + 0.75 * (d.length / avgLen))));
    }
    return s;
  };
}

/**
 * Demand, damped by payer concentration.
 *
 * A merchant can call its own endpoint all day; it cannot easily manufacture
 * distinct paying counterparties. So volume is credited only in proportion to
 * how many separate payers produced it.
 */
function demandScore(r) {
  const calls = r.calls_30d || 0;
  const payers = r.unique_payers_30d || 0;
  if (!calls) return { score: 0, concentration: null, note: 'no calls recorded in the last 30 days' };
  const diversity = payers / calls;            // 1.0 = every call from a different payer
  const volume = Math.log10(1 + calls) / 4;    // 10k calls ~= 1.0
  return {
    score: Math.max(0, Math.min(1, volume * Math.min(1, diversity * 2))),
    concentration: Math.round((1 - diversity) * 100) / 100,
    note: diversity < 0.1
      ? `${calls} calls from only ${payers} distinct payers — volume here is concentrated, and is discounted accordingly`
      : null,
  };
}

function priceIssue(p) {
  if (p === null) return 'no price published';
  if (!(p > 0)) return 'price is zero or negative';
  if (p > MAX_PLAUSIBLE_USD) return `price of $${p} is outside any plausible per-call band and is treated as bad data, not a bargain or a warning`;
  if (p < MIN_PRICE_USD) return 'price rounds to zero';
  return null;
}

/**
 * The record an agent gets back. Weights are returned with the result so a
 * caller can see why something ranked where it did, and disagree.
 */
function rank(records, bm25, queryTokens, { maxPriceUsd, requireCallable, weights }) {
  const w = Object.assign({ relevance: 0.45, demand: 0.25, price: 0.15, callable: 0.15 }, weights || {});
  const scored = [];
  for (let i = 0; i < records.length; i++) {
    const r = records[i];
    const rel = bm25(i, queryTokens);
    if (rel <= 0) continue;
    const issue = priceIssue(r.price_usd);
    if (issue && issue.startsWith('price of $')) continue;             // bad data, not a candidate
    if (maxPriceUsd !== null && r.price_usd !== null && r.price_usd > maxPriceUsd) continue;
    const callable = Boolean(r.call_spec);
    if (requireCallable && !callable) continue;
    const d = demandScore(r);
    // Cheaper is better, on a log scale so $0.001 vs $0.01 matters more than
    // $1.00 vs $1.01.
    const priceScore = r.price_usd ? Math.max(0, 1 - Math.log10(1 + r.price_usd * 1000) / 3) : 0.3;
    scored.push({
      record: r,
      relevance: rel,
      parts: { demand: d, price_score: Math.round(priceScore * 100) / 100, callable },
      total: w.relevance * rel + w.demand * d.score + w.price * priceScore + w.callable * (callable ? 1 : 0),
      price_note: issue,
    });
  }
  // Normalise relevance after the fact so the weighted total is comparable.
  const maxRel = Math.max(1e-9, ...scored.map((s) => s.relevance));
  for (const s of scored) {
    s.total = w.relevance * (s.relevance / maxRel) + w.demand * s.parts.demand.score
      + w.price * s.parts.price_score + w.callable * (s.parts.callable ? 1 : 0);
  }
  scored.sort((a, b) => b.total - a.total);
  return { scored, weights: w };
}

module.exports = {
  BAZAAR, INDEX402, canon, normalizeBazaar, normalize402index, merge,
  tokens, buildBm25, demandScore, priceIssue, rank, callSpecOf,
  MAX_PLAUSIBLE_USD, MIN_PRICE_USD,
};
