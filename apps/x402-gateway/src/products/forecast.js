'use strict';
/**
 * NOTARISED FORECASTS.
 *
 * WHAT IS ACTUALLY BEING SOLD. Not a prediction — an 8B model's probability is
 * not calibrated and will often be worse than a liquid market, and selling it
 * as alpha would be a claim we cannot back. What is scarce is PROOF OF WHEN.
 * Anyone can say afterwards that they called it; almost nobody can prove what
 * they believed beforehand. So every call anchors an immutable record into the
 * Animica data-availability layer and returns a commitment that anyone can
 * verify for free, forever, without trusting us.
 *
 * THREE RULES THIS FILE EXISTS TO ENFORCE:
 *
 *  1. BOTH NUMBERS, ALWAYS. The response carries the model's estimate AND the
 *     live market price side by side. Publishing only ours would imply a
 *     superiority we have not earned; showing both makes the divergence the
 *     product and lets the buyer judge.
 *  2. NO NOTARISATION, NO SALE. If the record cannot be anchored the call
 *     fails rather than returning a prediction — an unanchored forecast is the
 *     one thing this product is not.
 *  3. THE TRACK RECORD IS PUBLIC AND FREE, including when we lose. Markets
 *     resolve, so each forecast is scored (Brier) against both the model and
 *     the market. A seller who publishes their own calibration is rare; a
 *     seller who hides it is selling something else.
 */

const crypto = require('node:crypto');
const { ProductError, ProductUnavailable } = require('./errors');
const { namespaceOf } = require('./notary');

const GAMMA = 'https://gamma-api.polymarket.com';

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

/** Canonical JSON: sorted keys, no whitespace — the exact bytes anchored. */
function canonicalJson(obj) {
  if (obj === null || typeof obj !== 'object') return JSON.stringify(obj);
  if (Array.isArray(obj)) return '[' + obj.map(canonicalJson).join(',') + ']';
  const keys = Object.keys(obj).filter((k) => obj[k] !== undefined).sort();
  return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonicalJson(obj[k])).join(',') + '}';
}

/** Polymarket returns these as JSON-encoded strings inside JSON. */
function parseList(v) {
  if (Array.isArray(v)) return v;
  if (typeof v !== 'string') return [];
  try { const p = JSON.parse(v); return Array.isArray(p) ? p : []; } catch { return []; }
}

/**
 * The YES price of a binary market, or null when it is not a clean binary.
 * Never guesses: a market we cannot read is reported as unpriced rather than
 * given a made-up number.
 */
function yesPrice(market) {
  const outcomes = parseList(market.outcomes).map((s) => String(s).toLowerCase());
  const prices = parseList(market.outcomePrices);
  if (outcomes.length !== 2 || prices.length !== 2) return null;
  const i = outcomes.indexOf('yes');
  if (i < 0) return null;
  const p = Number(prices[i]);
  return Number.isFinite(p) ? p : null;
}

/** A resolved binary market names its winner as a price of exactly 1. */
function resolvedOutcome(market) {
  if (!market || market.closed !== true) return null;
  const outcomes = parseList(market.outcomes);
  const prices = parseList(market.outcomePrices).map(Number);
  if (outcomes.length !== 2 || prices.length !== 2) return null;
  const i = prices.findIndex((p) => p === 1);
  if (i < 0) return null;                 // closed but not settled to a winner
  return { outcome: String(outcomes[i]), yesWon: String(outcomes[i]).toLowerCase() === 'yes' };
}

/** Brier score for a single binary forecast. Lower is better; 0.25 = a coin. */
function brier(prob, yesWon) {
  const p = Number(prob);
  if (!Number.isFinite(p)) return null;
  const actual = yesWon ? 1 : 0;
  return Math.round((p - actual) ** 2 * 1e6) / 1e6;
}

function createForecastProduct({ cfg, node, gatewayStore, fetchImpl = fetch, now = Date.now }) {
  const NS = namespaceOf(cfg.forecastNamespace);

  async function gamma(path, timeoutMs) {
    const r = await fetchImpl(GAMMA + path, {
      headers: { accept: 'application/json', 'user-agent': 'AnimicaX402Forecast/1.0 (+https://animica.dev/x402)' },
      signal: AbortSignal.timeout(timeoutMs || Number(cfg.forecastMarketTimeoutMs)),
    });
    if (!r.ok) throw new Error(`market API HTTP ${r.status}`);
    return r.json();
  }

  // Words that carry no matching signal. Searching the full question returns
  // nothing useful (measured: 0 open markets for a full sentence, 35 for its
  // keywords), so the query is reduced to its content words.
  const STOP = new Set(['will','the','a','an','be','is','are','to','of','in','on','by','for',
    'and','or','it','this','that','before','after','above','below','than','at','over','under',
    'happen','there','any','more','less','least','most','do','does','did','have','has','get',
    'reach','hit','end','year','years','month','months','day','days','who','what','when','which']);

  function keywords(q) {
    return String(q).toLowerCase()
      .replace(/[^a-z0-9$.,%\s-]/g, ' ')
      .split(/\s+/)
      .map((w) => w.replace(/^[.,-]+|[.,-]+$/g, ''))
      .filter((w) => w && w.length > 1 && !STOP.has(w));
  }

  /**
   * How well a market matches the question, 0..1. Numbers are weighted
   * heavily: "Bitcoin above $200,000" and "Bitcoin reach $95,000" share every
   * word except the one that matters, and attaching the wrong market to
   * someone's permanent record would be worse than attaching none.
   */
  function relevance(question, marketQuestion) {
    const q = new Set(keywords(question));
    const m = new Set(keywords(marketQuestion));
    if (!q.size || !m.size) return 0;
    let hits = 0;
    for (const w of q) if (m.has(w)) hits += 1;
    const base = hits / q.size;
    // Numeric tokens (thresholds, dates) must agree or the markets are about
    // different things regardless of how much prose they share.
    const num = (set) => [...set].filter((w) => /\d/.test(w));
    const qn = num(q);
    const mn = num(m);
    if (qn.length && mn.length) {
      const shared = qn.filter((w) => mn.includes(w)).length;
      if (shared === 0) return Math.min(base, 0.34);   // numbers disagree
      return Math.min(1, base + 0.15);
    }
    return base;
  }

  /**
   * Resolve the caller's question to a live market, or null if nothing matches
   * well enough. Returning null is a valid, honest answer: the forecast is
   * still made and anchored, it simply carries no market comparison.
   */
  async function findMarket({ marketId, marketSlug, question }) {
    try {
      if (marketId) {
        const m = await gamma(`/markets?id=${encodeURIComponent(marketId)}&limit=1`);
        if (Array.isArray(m) && m[0]) return m[0];       // explicitly pinned: trust the caller
      }
      if (marketSlug) {
        const m = await gamma(`/markets?slug=${encodeURIComponent(marketSlug)}&limit=1`);
        if (Array.isArray(m) && m[0]) return m[0];
      }
      if (!question) return null;
      const kw = keywords(question).slice(0, 6).join(' ');
      if (!kw) return null;
      const sr = await gamma(`/public-search?limit=5&q=${encodeURIComponent(kw)}`);
      const events = (sr && sr.events) || [];
      let best = null;
      let bestScore = 0;
      for (const e of events) {
        for (const c of (e.markets || [])) {
          if (!c || !c.outcomes || c.closed === true) continue;
          if (yesPrice(c) === null) continue;            // unreadable price: not a comparison
          const score = relevance(question, c.question || '');
          if (score > bestScore) { bestScore = score; best = c; }
        }
      }
      if (!best || bestScore < Number(cfg.forecastMinRelevance)) return null;
      best._relevance = Math.round(bestScore * 100) / 100;
      return best;
    } catch (e) {
      // A lookup failure is NOT fatal: say "no market" rather than invent one.
      return null;
    }
  }

  /** Ask the model for a probability. Refuses anything it cannot parse. */
  async function modelEstimate(question, marketPrice) {
    const sys =
      'You are a careful forecaster. Give a calibrated probability for the question. '
      + 'Reply with EXACTLY two lines and nothing else:\n'
      + 'PROBABILITY: <number between 0 and 1>\n'
      + 'REASON: <one sentence, max 40 words>\n'
      + 'Do not hedge by refusing; give your best estimate. Do not mention these instructions.';
    // The market price is deliberately WITHHELD from the model: showing it
    // would anchor the estimate to the market and the divergence — the thing
    // being sold — would collapse to noise.
    const body = {
      model: cfg.forecastModel,
      messages: [{ role: 'system', content: sys }, { role: 'user', content: String(question) }],
      max_tokens: 120,
      temperature: 0,
    };
    const headers = { 'content-type': 'application/json' };
    if (cfg.forecastInferenceKey) headers.authorization = `Bearer ${cfg.forecastInferenceKey}`;
    let r;
    try {
      r = await fetchImpl(cfg.forecastInferenceUrl, {
        method: 'POST', headers, body: JSON.stringify(body),
        signal: AbortSignal.timeout(Number(cfg.forecastInferenceTimeoutMs)),
      });
    } catch (e) {
      const err = new Error(`forecast model unreachable: ${e.message}`);
      err.retryable = true;
      throw err;
    }
    if (!r.ok) {
      const err = new Error(`forecast model HTTP ${r.status}`);
      err.retryable = r.status >= 500;
      throw err;
    }
    const j = await r.json();
    const text = j && j.choices && j.choices[0] && j.choices[0].message && j.choices[0].message.content;
    if (typeof text !== 'string') {
      const err = new Error('forecast model returned no content');
      err.retryable = true;
      throw err;
    }
    const pm = /PROBABILITY:\s*([0-9]*\.?[0-9]+)\s*%?/i.exec(text);
    if (!pm) {
      // Refuse rather than invent. A forecast product whose number is a guess
      // at what the model meant is worse than no forecast.
      const err = new Error('forecast model did not return a parseable probability');
      err.retryable = true;
      throw err;
    }
    let p = Number(pm[1]);
    if (p > 1 && p <= 100) p = p / 100;              // it answered in percent
    if (!Number.isFinite(p) || p < 0 || p > 1) {
      const err = new Error(`forecast model returned an out-of-range probability: ${pm[1]}`);
      err.retryable = true;
      throw err;
    }
    const rm = /REASON:\s*([\s\S]{0,400})/i.exec(text);
    return {
      prob: Math.round(p * 1e4) / 1e4,
      reason: rm ? rm[1].trim().replace(/\s+/g, ' ').slice(0, 300) : null,
      model: (j && j.model) || cfg.forecastModel,
    };
  }

  async function head() {
    try {
      const h = await node.call('chain.getHead', {}, { timeoutMs: 5000 });
      return h && Number.isInteger(h.height) ? { height: h.height, hash: h.hash || null } : { height: null, hash: null };
    } catch { return { height: null, hash: null }; }
  }

  return {
    id: 'forecast_notarized',
    title: 'Notarised forecast with market comparison',
    description:
      'Ask a yes/no question about the future and get three things: an independent model probability, the live price of the matching prediction market when one exists, and — the point of the product — the whole record anchored into the Animica data-availability layer with a commitment anyone can verify FREE and permanently at GET /x402/forecast/{commitment}. Anyone can claim afterwards that they called it; this proves what was believed beforehand. Both numbers are always returned side by side: the model is NOT claimed to beat the market, and our own accuracy against every resolved market is published free at GET /x402/forecast/calibration, including when we lose. If the record cannot be anchored the call fails rather than selling an unanchored prediction.',
    path: '/x402/forecast',
    routes: [{ method: 'POST', path: '/x402/forecast' }],
    priceUsd: cfg.forecastPriceUsd,
    enabled: cfg.forecastEnabled,
    // Real work + an on-chain write: re-check readiness, settle, then produce.
    mode: 'settle-then-execute',
    mimeType: 'application/json',
    maxBodyBytes: 16384,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          question: { type: 'string', required: true, description: 'a yes/no question about the future, e.g. "Will X happen before 2027?"' },
          market_slug: { type: 'string', required: false, description: 'pin the comparison to a specific prediction market by slug' },
          market_id: { type: 'string', required: false, description: 'pin the comparison by market id' },
        },
      },
      output: {
        type: 'json',
        description:
          'commitment, verify_url, record (the exact anchored bytes), model {probability, reasoning, name}, market {price, id, slug, end_date, source} or null, divergence, anchored_at {head_height, head_hash}, proof, and an explicit statement of what the proof does and does not establish',
      },
    },

    async availability() {
      // The anchor is the product, so DA writability gates the whole thing.
      try {
        const s = await node.call('da.status', {}, { timeoutMs: 5000 });
        if (!s || s.ok !== true || s.writable !== true) {
          return { available: false, reason: 'da_unavailable', detail: 'the data-availability layer is not writable, so a forecast could not be anchored' };
        }
      } catch (e) {
        return { available: false, reason: 'da_unreachable', detail: e.message };
      }
      return { available: true };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      const q = b.question;
      if (typeof q !== 'string' || q.trim().length < 8) {
        throw bad('question is required and must be a real question (8+ characters)', 'invalid_request');
      }
      if (q.length > 500) throw bad('question must be under 500 characters', 'invalid_request');
      return {
        question: q.trim(),
        marketId: typeof b.market_id === 'string' ? b.market_id.trim() : null,
        marketSlug: typeof b.market_slug === 'string' ? b.market_slug.trim() : null,
      };
    },

    async preSettle() {
      const s = await node.call('da.status', {}, { timeoutMs: 5000 });
      if (!s || s.writable !== true) {
        throw new ProductUnavailable('da_unavailable', 'cannot anchor a forecast right now');
      }
      return {};
    },

    async handler(ctx) {
      const { question, marketId, marketSlug } = ctx.params;
      const market = await findMarket({ marketId, marketSlug, question });
      const price = market ? yesPrice(market) : null;
      const est = await modelEstimate(question, price);
      const h = await head();
      const at = new Date(now()).toISOString();

      // The exact bytes anchored. Canonical (sorted keys, no whitespace) so a
      // verifier can rebuild them from the response and re-hash.
      const record = {
        anchored_at: at,
        head_hash: h.hash,
        head_height: h.height,
        market_id: market ? String(market.id) : null,
        market_price_yes: price === null ? null : String(price),
        market_slug: market ? String(market.slug || '') : null,
        model: est.model,
        model_probability: String(est.prob),
        model_reasoning: est.reason,
        nonce: crypto.randomBytes(16).toString('hex'),
        question,
        v: 1,
      };
      const bytes = Buffer.from(canonicalJson(record), 'utf8');

      let put;
      try {
        put = await node.call('da.put', { bytes: bytes.toString('base64'), namespace: NS },
          { timeoutMs: Number(cfg.forecastTimeoutMs) });
      } catch (e) {
        const err = new Error(`could not anchor the forecast: ${e.message}`);
        err.retryable = true;
        throw err;
      }
      if (!put || !put.commitment) {
        // RULE 2: no anchor, no sale.
        const err = new Error('the data-availability layer returned no commitment; refusing to sell an unanchored forecast');
        err.retryable = true;
        throw err;
      }

      let proof = null;
      try {
        const p = await node.call('da.getProof', { commitment: put.commitment }, { timeoutMs: Number(cfg.forecastTimeoutMs) });
        const first = p && Array.isArray(p.proofs) ? p.proofs[0] : null;
        proof = {
          leaf_count: p ? p.leaf_count : null,
          tree_height: p ? p.tree_height : null,
          data_shards: p ? p.data_shards : null,
          total_shards: p ? p.total_shards : null,
          leaf_index: first ? first.leaf_index : null,
        };
      } catch (e) {
        proof = { error: `proof not retrievable right now: ${e.message}`, retry: `/x402/forecast/${put.commitment}` };
      }

      const forecastId = crypto.randomUUID();
      try {
        gatewayStore.putForecast({
          forecastId,
          commitment: put.commitment,
          blobId: put.blob_id,
          question,
          marketId: market ? String(market.id) : null,
          marketSlug: market ? String(market.slug || '') : null,
          marketPrice: price,
          modelProb: est.prob,
          modelReasoning: est.reason,
          modelName: est.model,
          headHeight: h.height,
          headHash: h.hash,
          endDate: market ? String(market.endDate || '') : null,
          createdAt: Math.floor(now() / 1000),
        });
      } catch (e) {
        // The anchor already exists on-chain and is what the buyer paid for;
        // a local bookkeeping failure must not fail the call. It only costs us
        // the later scoring of this one forecast, which is our problem.
      }

      const divergence = price === null ? null : Math.round((est.prob - price) * 1e4) / 1e4;

      return {
        status: 200,
        bodyObj: {
          product: 'forecast_notarized',
          question,
          commitment: put.commitment,
          blob_id: put.blob_id,
          verify_url: `/x402/forecast/${put.commitment}`,
          record,
          record_bytes_base64: bytes.toString('base64'),
          model: { probability: est.prob, reasoning: est.reason, name: est.model },
          market: market ? {
            price_yes: price,
            match_confidence: market._relevance === undefined ? null : market._relevance,
            id: String(market.id),
            slug: String(market.slug || ''),
            question: String(market.question || ''),
            end_date: market.endDate || null,
            liquidity: market.liquidity !== undefined ? market.liquidity : null,
            source: 'Polymarket (public gamma API)',
          } : null,
          divergence: divergence === null ? null : {
            model_minus_market: divergence,
            direction: divergence > 0 ? 'model is more bullish than the market'
              : (divergence < 0 ? 'model is more bearish than the market' : 'model agrees with the market'),
          },
          anchored_at: { head_height: h.height, head_hash: h.hash, observed_at: at },
          proof,
          calibration_url: '/x402/forecast/calibration',
          honesty: {
            what_this_proves:
              'that this exact record — the question, the model probability, the market price and the chain head observed at the time — was committed to the Animica DA layer before the outcome was known. Verify it free at the verify_url.',
            what_this_does_not_prove:
              'nothing about accuracy. The model estimate is NOT claimed to beat the market; a liquid market will often be better. Both numbers are shown so you can judge, and our scored track record against every resolved market is public and free at /x402/forecast/calibration.',
            model_caveat:
              'the estimate comes from a small, fast model and is not a calibrated forecasting system. Treat it as one opinion alongside the market price, not as a signal.',
          },
        },
      };
    },
  };
}

/**
 * FREE: read one anchored forecast back, with its resolution if the market has
 * settled. A proof only the seller can read is not a proof.
 */
function createForecastRecordRoute({ cfg, node, gatewayStore }) {
  const RE = /^\/x402\/forecast\/([0-9a-fA-F]{64})$/;
  return {
    method: 'GET',
    path: '/x402/forecast/{commitment}',
    title: 'Notarised forecast — one anchored record (free)',
    description:
      'FREE and permanent: the anchored forecast record for a commitment, its DA inclusion proof, and — once the market settles — the outcome and Brier scores for both the model and the market.',
    match(pathname) {
      const m = RE.exec(pathname);
      return m ? { commitment: m[1].toLowerCase() } : null;
    },
    async handler(ctx) {
      const commitment = ctx.params.commitment;
      let blob = null;
      try {
        blob = await node.call('da.get', { commitment }, { timeoutMs: 8000 });
      } catch (e) {
        return { status: 404, bodyObj: { error: 'commitment_not_found', detail: e.message, commitment } };
      }
      if (!blob || !blob.bytes) {
        return { status: 404, bodyObj: { error: 'commitment_not_found', commitment } };
      }
      const raw = Buffer.from(blob.bytes, 'base64');
      let record = null;
      let parseError = null;
      try { record = JSON.parse(raw.toString('utf8')); }
      catch (e) { parseError = `stored bytes are not a forecast record: ${e.message}`; }

      let canonicalMatches = null;
      if (record && typeof record === 'object') {
        canonicalMatches = Buffer.from(canonicalJson(record), 'utf8').equals(raw);
      }
      const row = gatewayStore ? gatewayStore.getForecast(commitment) : null;

      let proof = null;
      try {
        const p = await node.call('da.getProof', { commitment }, { timeoutMs: 8000 });
        proof = { leaf_count: p.leaf_count, tree_height: p.tree_height, data_shards: p.data_shards, total_shards: p.total_shards };
      } catch (e) { proof = { error: e.message }; }

      return {
        status: 200,
        bodyObj: {
          product: 'forecast_record',
          free: true,
          commitment,
          record,
          parse_error: parseError,
          canonical_bytes_match: canonicalMatches,
          proof,
          resolution: row && row.resolved_at ? {
            outcome: row.resolved_outcome,
            resolved_at: new Date(Number(row.resolved_at) * 1000).toISOString(),
            brier_model: row.brier_model === null ? null : Number(row.brier_model),
            brier_market: row.brier_market === null ? null : Number(row.brier_market),
            note: 'Brier score: lower is better; 0.25 is what an uninformative 50/50 guess scores.',
          } : { resolved: false, note: 'the market has not settled yet, or this forecast had no matching market' },
          note:
            'canonical_bytes_match true means the stored bytes are exactly the canonical JSON of the record shown, so it has not been altered since it was anchored.',
        },
      };
    },
  };
}

/**
 * FREE: our own scored track record. Published including the cases where the
 * market beat us — a seller who hides their calibration is selling something
 * other than forecasts.
 */
function createCalibrationRoute({ cfg, gatewayStore, fetchImpl = fetch, now = Date.now }) {
  return {
    method: 'GET',
    path: '/x402/forecast/calibration',
    title: 'Notarised forecast — published Brier track record (free)',
    description:
      'FREE: the scored track record of every notarised forecast whose market has settled — mean Brier score for the model AND for the market, so you can see plainly whether the model adds anything. Published win or lose.',
    match(pathname) {
      return pathname === '/x402/forecast/calibration' ? {} : null;
    },
    async handler() {
      const st = gatewayStore.forecastStats() || {};
      const recent = gatewayStore.recentForecasts(10).map((r) => ({
        commitment: r.commitment,
        question: r.question,
        model_probability: Number(r.model_prob),
        market_price_yes: r.market_price === null ? null : Number(r.market_price),
        anchored_at: new Date(Number(r.created_at) * 1000).toISOString(),
        resolved_outcome: r.resolved_outcome || null,
        brier_model: r.brier_model === null ? null : Number(r.brier_model),
        brier_market: r.brier_market === null ? null : Number(r.brier_market),
        verify: `/x402/forecast/${r.commitment}`,
      }));
      const bm = st.brier_model === null || st.brier_model === undefined ? null : Number(st.brier_model);
      const bk = st.brier_market === null || st.brier_market === undefined ? null : Number(st.brier_market);
      let verdict;
      if (bm === null || bk === null) {
        verdict = 'not enough resolved forecasts yet to say anything meaningful.';
      } else if (bm < bk) {
        verdict = 'on resolved forecasts so far the model has scored better than the market. Small samples flatter whoever is ahead — treat this as provisional.';
      } else if (bm > bk) {
        verdict = 'on resolved forecasts so far the MARKET has scored better than the model. That is the expected result and it is published because hiding it would make the whole product dishonest.';
      } else {
        verdict = 'model and market have scored identically so far.';
      }
      return {
        status: 200,
        bodyObj: {
          product: 'forecast_calibration',
          free: true,
          total_forecasts: Number(st.n || 0),
          resolved: Number(st.resolved || 0),
          mean_brier_model: bm,
          mean_brier_market: bk,
          verdict,
          scale: 'Brier score, 0 is perfect, 0.25 is an uninformative 50/50 guess, 1 is confidently wrong.',
          recent,
          generated_at: new Date(now()).toISOString(),
        },
      };
    },
  };
}

/**
 * Score any anchored forecast whose market has settled. Safe to run on a timer;
 * never throws.
 */
async function resolveOpenForecasts({ gatewayStore, fetchImpl = fetch, limit = 25, now = Date.now, cfg }) {
  let scored = 0;
  try {
    const open = gatewayStore.openForecasts(limit);
    for (const row of open) {
      try {
        const r = await fetchImpl(`${GAMMA}/markets?id=${encodeURIComponent(row.market_id)}&limit=1`, {
          headers: { accept: 'application/json' },
          signal: AbortSignal.timeout((cfg && cfg.forecastMarketTimeoutMs) || 10000),
        });
        if (!r.ok) continue;
        const arr = await r.json();
        const m = Array.isArray(arr) ? arr[0] : null;
        const res = resolvedOutcome(m);
        if (!res) continue;
        gatewayStore.resolveForecast({
          forecastId: row.forecast_id,
          outcome: res.outcome,
          brierModel: brier(row.model_prob, res.yesWon),
          brierMarket: row.market_price === null ? null : brier(row.market_price, res.yesWon),
          at: Math.floor(now() / 1000),
        });
        scored += 1;
      } catch { /* one market failing must not stop the sweep */ }
    }
  } catch { /* never throw from a background sweep */ }
  return scored;
}

module.exports = {
  createForecastProduct,
  createForecastRecordRoute,
  createCalibrationRoute,
  resolveOpenForecasts,
  yesPrice,
  resolvedOutcome,
  brier,
  canonicalJson,
  parseList,
};
