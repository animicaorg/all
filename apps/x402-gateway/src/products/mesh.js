'use strict';
/**
 * POST /x402/mesh/find — who should an agent buy from?
 *
 * Builds a merged, scored index of the x402 economy from Coinbase Bazaar and
 * 402index, and answers "what should I call to do X, within this budget".
 *
 * WHY THIS IS NOT A PROXY FOR A FREE DIRECTORY. Both sources are public. What
 * they do not tell you is whether a listing can be invoked at all, whether its
 * price is real, or whether its usage numbers came from more than one wallet.
 * Roughly 5% of Bazaar entries publish a call spec; one live listing advertises
 * ten billion dollars a call; and total-call counts are trivially inflatable by
 * the merchant itself. Those three facts decide whether an agent should spend,
 * and this endpoint answers them explicitly rather than passing raw rows along.
 *
 * HARVEST, NOT PER-CALL FAN-OUT. The index is built once and cached, and a
 * refresh coalesces so a hundred simultaneous callers cause one harvest and not
 * a hundred. A search must never turn into 150 upstream requests.
 */

const { ProductError, ProductUnavailable } = require('./errors');
const M = require('./mesh-index');

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

function createMeshIndexCache({ cfg, fetchImpl = fetch, now = Date.now, logger = null, gatewayStore = null }) {
  let cache = null;      // { at, records, bm25, counts }
  let inflight = null;

  const nap = (ms) => new Promise((r) => setTimeout(r, ms));

  /**
   * Fetch one directory page, backing off when the directory says to.
   *
   * Harvesting 200 pages back-to-back earned a 429 from 402index and cost us
   * that whole source for a build. The index has a multi-hour TTL, so being
   * slow is free and being rate-limited is not. `Retry-After` is honoured when
   * offered rather than guessed at.
   */
  async function fetchJson(url, timeoutMs) {
    const retries = Number(cfg.meshDirectoryRetries);
    let lastErr = null;
    let throttled = false;
    for (let attempt = 0; attempt <= retries; attempt++) {
      let r;
      try {
        r = await fetchImpl(url, {
          headers: { accept: 'application/json', 'user-agent': 'AnimicaMesh/1.0 (+https://animica.dev/x402/mesh/find)' },
          signal: AbortSignal.timeout(timeoutMs),
        });
      } catch (e) {
        lastErr = e;
        if (attempt === retries) throw e;
        await nap(1000 * 2 ** attempt);
        continue;
      }
      if (r.status === 429 || r.status === 503) {
        lastErr = new Error(`HTTP ${r.status}`);
        if (attempt === retries) throw lastErr;
        throttled = true;
        const ra = Number(r.headers && r.headers.get && r.headers.get('retry-after'));
        await nap(Number.isFinite(ra) && ra > 0 ? Math.min(ra * 1000, 30_000) : 4000 * 2 ** attempt);
        continue;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const j = await r.json();
      if (throttled && j && typeof j === 'object') j.__throttled = true;
      return j;
    }
    throw lastErr || new Error('unreachable');
  }

  async function harvestBazaar() {
    const out = [];
    const limit = 100;
    let offset = 0;
    let total = null;
    const maxPages = Number(cfg.meshMaxPages);
    for (let page = 0; page < maxPages; page++) {
      const d = await fetchJson(`${M.BAZAAR}?limit=${limit}&offset=${offset}`, Number(cfg.meshFetchTimeoutMs));
      const items = Array.isArray(d.items) ? d.items : [];
      if (!items.length) break;
      for (const i of items) if (i && i.resource) out.push(M.normalizeBazaar(i));
      total = d.pagination && d.pagination.total;
      offset += limit;
      if (total !== null && offset >= total) break;
      await nap(Number(cfg.meshDirectoryPageDelayMs));
    }
    return { records: out, total };
  }

  async function harvest402index() {
    const out = [];
    const limit = 100;
    let total = null;
    const maxPages = Number(cfg.meshIndex402MaxPages);
    // Adaptive: any 429 doubles the pause for the rest of this harvest. Being
    // told to slow down and then not slowing down is how a source disappears.
    let delay = Number(cfg.meshIndex402PageDelayMs);
    if (!maxPages) return { records: out, total: null, skipped: 'disabled' };
    for (let page = 0; page < maxPages; page++) {
      const d = await fetchJson(`${M.INDEX402}?limit=${limit}&offset=${page * limit}`, Number(cfg.meshFetchTimeoutMs));
      if (d && d.__throttled) delay = Math.min(delay * 2, 15000);
      const rows = Array.isArray(d.services) ? d.services : [];
      if (!rows.length) break;
      for (const r of rows) if (r && r.url) out.push(M.normalize402index(r));
      total = Number(d.total) || total;
      if ((page + 1) * limit >= (total || 0)) break;
      await nap(delay);
    }
    return { records: out, total };
  }

  async function build() {
    const started = now();
    // A source being down degrades coverage; it must not fail the whole index.
    const [bz, ix] = await Promise.allSettled([harvestBazaar(), harvest402index()]);
    const sources = {};
    const merged = new Map();
    for (const [name, res] of [['bazaar', bz], ['402index', ix]]) {
      if (res.status !== 'fulfilled') {
        sources[name] = { ok: false, error: String(res.reason && res.reason.message).slice(0, 160), records: 0 };
        continue;
      }
      sources[name] = { ok: true, records: res.value.records.length, reported_total: res.value.total ?? null };
      for (const r of res.value.records) {
        const prev = merged.get(r.key);
        merged.set(r.key, prev ? M.merge(prev, r) : r);
      }
    }
    if (!Object.values(sources).some((s) => s.ok)) {
      throw new ProductUnavailable('mesh_sources_unreachable', 'no x402 directory could be reached, so there is no index to search');
    }
    let records = [...merged.values()];

    // ---- Overlay what we learned by CALLING these resources ----------------
    // A probe result outranks any directory row, because it came from the
    // merchant's own 402 rather than a listing they wrote once and forgot.
    // This is the whole point of the harvester: it is the difference between
    // "a directory says this costs $0.01" and "this endpoint told us so today".
    const probeCounts = { applied: 0, paywalled: 0, open: 0, dead: 0, error: 0, blocked: 0, gained_call_spec: 0, price_corrected: 0 };
    if (gatewayStore && typeof gatewayStore.allProbes === 'function') {
      const byKey = new Map();
      try {
        for (const row of gatewayStore.allProbes()) byKey.set(row.key, row);
      } catch (e) {
        logger && logger.warn && logger.warn('mesh_probe_overlay_failed', { error: e.message });
      }
      for (const r of records) {
        const p = byKey.get(r.key);
        if (!p) continue;
        probeCounts.applied++;
        probeCounts[p.outcome] = (probeCounts[p.outcome] || 0) + 1;
        r.probe = {
          outcome: p.outcome,
          http_status: p.http_status,
          method: p.method,
          latency_ms: p.latency_ms,
          probed_at: p.probed_at ? new Date(p.probed_at * 1000).toISOString() : null,
          error: p.error || null,
        };
        if (p.outcome !== 'paywalled') continue;
        const observed = p.price_usd === null ? null : Number(p.price_usd);
        if (observed !== null && Number.isFinite(observed)) {
          if (r.price_usd !== null && Math.abs(observed - r.price_usd) / Math.max(observed, r.price_usd) > 0.05) {
            probeCounts.price_corrected++;
            r.directory_price_usd = r.price_usd;
          }
          r.price_usd = observed;
        }
        if (p.pay_to) r.pay_to = p.pay_to;
        if (p.network) r.network = p.network;
        if (p.scheme) r.scheme = p.scheme;
        if (p.asset) r.asset = p.asset;
        if (p.call_spec_json && !r.call_spec) {
          try { r.call_spec = { ...JSON.parse(p.call_spec_json), source: 'probed 402' }; probeCounts.gained_call_spec++; } catch { /* keep null */ }
        }
      }
      // A resource we called and found gone is not a candidate to spend on.
      const before = records.length;
      records = records.filter((r) => !(r.probe && r.probe.outcome === 'dead'));
      probeCounts.dropped_dead = before - records.length;
    }

    const counts = {
      total: records.length,
      callable: records.filter((r) => r.call_spec).length,
      priced: records.filter((r) => r.price_usd !== null && !M.priceIssue(r.price_usd)).length,
      price_rejected: records.filter((r) => {
        const i = M.priceIssue(r.price_usd);
        return i && i.startsWith('price of $');
      }).length,
      with_demand: records.filter((r) => (r.calls_30d || 0) > 0).length,
      probed: probeCounts.applied,
      probe: probeCounts,
      in_both_directories: records.filter((r) => (r.sources || []).length > 1).length,
      sources,
      built_in_ms: now() - started,
    };
    logger && logger.info && logger.info('mesh_index_built', counts);
    return { at: now(), records, bm25: M.buildBm25(records), counts };
  }

  /**
   * Cached index. Concurrent callers share one harvest, and a restart reuses
   * the persisted snapshot rather than re-harvesting two third-party
   * directories from scratch — which is what earned us a 429 during a run of
   * deploys, and would have kept earning them.
   */
  async function getIndex() {
    const ttl = Number(cfg.meshCacheTtlMs);
    if (cache && now() - cache.at < ttl) return cache;

    if (!cache && gatewayStore && typeof gatewayStore.getIndexSnapshot === 'function') {
      try {
        const snap = gatewayStore.getIndexSnapshot();
        if (snap && now() - snap.harvestedAt < ttl && Array.isArray(snap.records) && snap.records.length) {
          cache = { at: snap.harvestedAt, records: snap.records, bm25: M.buildBm25(snap.records), counts: snap.counts };
          logger && logger.info && logger.info('mesh_index_from_snapshot', {
            records: snap.records.length, age_seconds: Math.round((now() - snap.harvestedAt) / 1000),
          });
          return cache;
        }
      } catch (e) {
        logger && logger.warn && logger.warn('mesh_snapshot_read_failed', { error: e.message });
      }
    }
    if (inflight) return inflight;
    inflight = build()
      .then((c) => {
        cache = c;
        if (gatewayStore && typeof gatewayStore.putIndexSnapshot === 'function') {
          try { gatewayStore.putIndexSnapshot({ harvestedAt: c.at, counts: c.counts, records: c.records }); }
          catch (e) { logger && logger.warn && logger.warn('mesh_snapshot_write_failed', { error: e.message }); }
        }
        return c;
      })
      .finally(() => { inflight = null; });
    try {
      return await inflight;
    } catch (e) {
      // A stale index beats no index: a refresh failure must not take the
      // product down while we still hold usable data.
      if (cache) {
        logger && logger.warn && logger.warn('mesh_index_refresh_failed_serving_stale', { error: e.message });
        return cache;
      }
      throw e;
    }
  }

  /**
   * Kick off the first harvest in the background.
   *
   * A cold build takes over a minute against two directories. Making the first
   * paying caller absorb that — and possibly time out — would be charging
   * someone for our startup cost.
   */
  function warm() {
    if (cache || inflight) return;
    getIndex().catch((e) => {
      logger && logger.warn && logger.warn('mesh_index_warm_failed', { error: e.message });
    });
  }

  return { getIndex, warm, _build: build };
}

function createMeshFindProduct({ cfg, fetchImpl = fetch, now = Date.now, logger = null, indexCache = null, gatewayStore = null }) {
  const idx = indexCache || createMeshIndexCache({ cfg, fetchImpl, now, logger, gatewayStore });

  return {
    id: 'mesh_find',
    warmIndex: () => idx.warm(),
    title: 'x402 Mesh — find who to buy from',
    description:
      'Search the whole x402 economy and get a ranked shortlist of services that can do a job, within a budget. Merges Coinbase Bazaar and 402index into one index keyed by canonical resource URL, then answers the three questions a directory does not: CAN this be invoked (only about 5% publish a call spec, and that is a first-class field here, not something you infer from missing keys), is the price REAL (one live listing advertises ten billion dollars a call — implausible prices are excluded as bad data and reported, never silently rewritten), and is the demand real (total calls are trivially inflated by a merchant calling itself, so volume is credited only in proportion to distinct paying counterparties). Every result explains its own score, and the weights are returned so you can disagree with them. Lexical matching, no model: the same query against the same index ranks the same way.',
    path: '/x402/mesh/find',
    routes: [{ method: 'POST', path: '/x402/mesh/find' }],
    priceUsd: cfg.meshFindPriceUsd,
    enabled: cfg.meshEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 16 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          goal: { type: 'string', required: true, description: 'what you are trying to do, in plain words — matched against service descriptions' },
          max_price_usd: { type: 'number', required: false, description: 'exclude anything priced above this per call' },
          require_callable: { type: 'boolean', required: false, description: 'only return services that publish a call spec you can invoke without guessing (default false)' },
          limit: { type: 'integer', required: false, description: `how many results, 1..${cfg.meshMaxResults} (default 10)` },
          weights: { type: 'object', required: false, description: 'override the ranking weights {relevance, demand, price, callable}; they are normalised as given' },
        },
      },
      output: {
        type: 'json',
        description:
          'results[] {resource, description, price_usd, network, callable, call_spec, demand {calls_30d, unique_payers_30d, concentration, note}, score, why}, weights, index {total, callable, priced, price_rejected, with_demand, in_both_directories, sources, age_seconds}, caveats[]',
      },
    },

    async availability() {
      return { available: true };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      if (typeof b.goal !== 'string' || b.goal.trim().length < 3) {
        throw bad('goal is required: describe in plain words what you are trying to do', 'invalid_request');
      }
      let limit = 10;
      if (b.limit !== undefined) {
        if (!Number.isInteger(b.limit) || b.limit < 1 || b.limit > Number(cfg.meshMaxResults)) {
          throw bad(`limit must be an integer between 1 and ${cfg.meshMaxResults}`, 'invalid_request');
        }
        limit = b.limit;
      }
      let maxPrice = null;
      if (b.max_price_usd !== undefined) {
        const p = Number(b.max_price_usd);
        if (!Number.isFinite(p) || p <= 0) throw bad('max_price_usd must be a positive number', 'invalid_request');
        maxPrice = p;
      }
      let weights = null;
      if (b.weights !== undefined) {
        if (!b.weights || typeof b.weights !== 'object' || Array.isArray(b.weights)) {
          throw bad('weights must be an object like {relevance, demand, price, callable}', 'invalid_request');
        }
        weights = {};
        for (const k of ['relevance', 'demand', 'price', 'callable']) {
          if (b.weights[k] === undefined) continue;
          const v = Number(b.weights[k]);
          if (!Number.isFinite(v) || v < 0 || v > 1) throw bad(`weights.${k} must be between 0 and 1`, 'invalid_request');
          weights[k] = v;
        }
      }
      const q = M.tokens(b.goal);
      if (!q.length) {
        throw bad('goal contained no searchable words — describe the capability you need, not just punctuation or stop words', 'invalid_request');
      }
      return { goal: b.goal.trim().slice(0, 500), queryTokens: q, limit, maxPrice, weights, requireCallable: b.require_callable === true };
    },

    async handler(ctx) {
      const { goal, queryTokens, limit, maxPrice, weights, requireCallable } = ctx.params;
      const index = await idx.getIndex();
      const { scored, weights: used } = M.rank(index.records, index.bm25, queryTokens, {
        maxPriceUsd: maxPrice, requireCallable, weights,
      });

      // The same service is frequently listed under several hosts — preview
      // deployments, staging mirrors, a vanity domain. They are indistinguish-
      // able by description and price, and returning four of them wastes a
      // shortlist an agent is paying for. Keep the best-scoring one and name
      // the others rather than hiding them.
      const byIdentity = new Map();
      const deduped = [];
      for (const s of scored) {
        const id = `${s.record.description.trim().toLowerCase()}|${s.record.price_usd}`;
        if (s.record.description.trim() && byIdentity.has(id)) {
          const keep = byIdentity.get(id);
          (keep.mirrors = keep.mirrors || []).push(s.record.resource);
          continue;
        }
        byIdentity.set(id, s);
        deduped.push(s);
      }

      const results = deduped.slice(0, limit).map((s) => {
        const r = s.record;
        const why = [];
        why.push(`matched "${goal}" on its description`);
        if (s.parts.callable) why.push('publishes a call spec, so it can be invoked without guessing');
        else why.push('publishes NO call spec — you would have to discover its request shape yourself');
        if (s.parts.demand.note) why.push(s.parts.demand.note);
        else if (r.calls_30d) why.push(`${r.calls_30d} calls from ${r.unique_payers_30d} distinct payers in 30 days`);
        if (r.probe && r.probe.outcome === 'paywalled') {
          why.push(r.directory_price_usd !== undefined
            ? `we called it: it answers 402 and quotes $${r.price_usd}, not the $${r.directory_price_usd} its directory listing claims`
            : `we called it: it answers 402 with the terms shown, so this price is the merchant's own, not a directory copy`);
        } else if (r.probe && r.probe.outcome === 'open') {
          why.push('we called it and it answered WITHOUT requiring payment — it is listed as paid but is not actually paywalled');
        } else if (r.probe && r.probe.outcome === 'error') {
          why.push(`we called it and it failed (${r.probe.error}) — listed, but not reliably answering`);
        } else if (!r.probe) {
          why.push('not yet verified by us — price and terms are the directory\'s claim, not something we confirmed');
        }
        if (s.price_note) why.push(s.price_note);
        if (s.mirrors && s.mirrors.length) {
          why.push(`${s.mirrors.length} other host(s) list an identical description and price — probably mirrors of this same service, collapsed into this entry`);
        }
        return {
          resource: r.resource,
          description: r.description.slice(0, 400),
          price_usd: r.price_usd,
          asset: r.asset,
          network: r.network,
          pay_to: r.pay_to,
          callable: s.parts.callable,
          call_spec: r.call_spec,
          demand: {
            calls_30d: r.calls_30d,
            unique_payers_30d: r.unique_payers_30d,
            payer_concentration: s.parts.demand.concentration,
            last_called_at: r.last_called_at,
          },
          verified: r.probe ? { ...r.probe, directory_price_usd: r.directory_price_usd ?? undefined } : null,
          latency_p50_ms: r.latency_p50_ms ?? null,
          health_status: r.health_status ?? null,
          listed_in: r.sources,
          score: Math.round(s.total * 1000) / 1000,
          also_listed_at: s.mirrors || undefined,
          why,
        };
      });

      return { status: 200, bodyObj: {
        product: 'mesh_find',
        goal,
        candidates_considered: scored.length,
        distinct_services: deduped.length,
        returned: results.length,
        candidates_note: 'candidates_considered counts every indexed service sharing ANY meaningful word with your goal, which is a wide net by design. Ranking is what narrows it; the count is not a claim that this many are suitable.',
        results,
        weights: used,
        weights_note: 'These are the weights the ranking used. Override them with `weights` — the score is a stated formula, not a judgement you have to accept.',
        index: {
          total: index.counts.total,
          callable: index.counts.callable,
          priced: index.counts.priced,
          price_rejected_as_implausible: index.counts.price_rejected,
          with_demand_data: index.counts.with_demand,
          verified_by_probe: index.counts.probe,
          in_both_directories: index.counts.in_both_directories,
          sources: index.counts.sources,
          age_seconds: Math.round((now() - index.at) / 1000),
        },
        caveats: [
          `Only ${index.counts.callable} of ${index.counts.total} indexed services publish a call spec. For the rest you know the URL and the price but not the request shape — set require_callable:true to see only the ones you can invoke today.`,
          `${index.counts.probe.applied} of ${index.counts.total} services have been called by us without paying, which is how a 402 reveals the merchant's own price and request shape. Results carrying "verified" were confirmed that way; the rest are the directory's claim. ${index.counts.probe.price_corrected} listing(s) quoted a different price than their directory row.`,
          'Descriptions and usage counts are published by the merchants themselves. We check prices for plausibility and report payer concentration, but we do not audit either.',
          `${index.counts.price_rejected} listing(s) advertise a price outside any plausible per-call band and were excluded as bad data rather than surfaced as bargains.`,
          'Matching is lexical, not semantic: a service that describes itself in different words than your goal can be missed. Try synonyms before concluding nothing exists.',
        ],
        generated_at: new Date(now()).toISOString(),
      } };
    },
  };
}

module.exports = { createMeshFindProduct, createMeshIndexCache };

/**
 * POST /x402/mesh/probe — "tell me exactly how to call this thing."
 *
 * Calls one x402 resource WITHOUT paying and returns what its own 402 says:
 * the real price, the payment terms, and the request shape when it publishes
 * one. This is the single-resource, on-demand form of the background harvester,
 * for the case where an agent has already chosen a candidate and needs the
 * terms it is about to accept — from the merchant, not from a directory row
 * that may be months stale.
 */
function createMeshProbeProduct({ cfg, harvester, gatewayStore, now = Date.now }) {
  return {
    id: 'mesh_probe',
    title: 'x402 Mesh — verify one endpoint before you pay it',
    description:
      "Call any x402 endpoint WITHOUT paying and get back what its own 402 challenge says: the real price, asset, network, payTo address, scheme, timeout, and the request schema when the merchant publishes one. Directory listings are written once and go stale; this is the merchant's own current statement of the terms you are about to accept. It also distinguishes the cases a directory cannot: a resource that answers 402 properly, one that is listed as paid but answers WITHOUT payment, one that is listed but gone, and one that simply fails. No payment header is ever sent, so this cannot settle anything, and unknown endpoints are probed with GET — never a write verb with a guessed body.",
    path: '/x402/mesh/probe',
    routes: [{ method: 'POST', path: '/x402/mesh/probe' }],
    priceUsd: cfg.meshProbePriceUsd,
    enabled: cfg.meshHarvestEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 8 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          resource: { type: 'string', required: true, description: 'absolute http(s) URL of the x402 endpoint to verify' },
          method: { type: 'string', required: false, description: 'HTTP verb to probe with. Omit and GET is tried first, then POST with an empty body — a write verb is never guessed at an unknown endpoint' },
          fresh: { type: 'boolean', required: false, description: 'ignore any cached probe and call it again now (default false)' },
        },
      },
      output: {
        type: 'json',
        description: 'outcome (paywalled|open|dead|error|blocked), http_status, method, price {atomic, usd, asset, network, pay_to, scheme, max_timeout_seconds}, call_spec, accepts[], latency_ms, probed_at, cached',
      },
    },

    async availability() {
      return { available: true };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      if (typeof b.resource !== 'string' || !b.resource.trim()) {
        throw bad('resource is required and must be an absolute http(s) URL', 'invalid_request');
      }
      let method = null;
      if (b.method !== undefined) {
        const m = String(b.method).toUpperCase();
        // A probe must stay safe. Offering DELETE here would make this endpoint
        // a way to have Animica issue destructive requests to third parties.
        if (!['GET', 'POST'].includes(m)) {
          throw bad('method must be GET or POST — a discovery probe never issues a destructive verb', 'invalid_request');
        }
        method = m;
      }
      return { resource: b.resource.trim(), method, fresh: b.fresh === true };
    },

    async handler(ctx) {
      const { resource, method, fresh } = ctx.params;
      const key = M.canon(resource);
      let row = null;
      let cached = false;

      if (!fresh && gatewayStore && typeof gatewayStore.getProbe === 'function') {
        const prev = gatewayStore.getProbe(key);
        const ageMs = prev ? now() - prev.probed_at * 1000 : Infinity;
        if (prev && ageMs < Number(cfg.meshProbeTtlMs)) { row = prev; cached = true; }
      }
      if (!row) row = await harvester.probeAndStore(resource, { declaredMethod: method });

      let accepts = null;
      try { accepts = row.accepts_json ? JSON.parse(row.accepts_json) : null; } catch { /* keep null */ }
      let spec = null;
      try { spec = row.call_spec_json ? JSON.parse(row.call_spec_json) : null; } catch { /* keep null */ }

      const meaning = {
        paywalled: 'It answers 402 with payment terms. The price and terms below are the merchant\'s own, read from that challenge just now.',
        open: 'It answered WITHOUT requiring payment. It is listed as a paid resource but is not actually paywalled — do not budget for it, and be aware the listing misrepresents it.',
        dead: 'It is listed in a directory but does not serve this URL. Nothing to buy.',
        error: 'It failed to answer usefully. That is reliability information no directory publishes, but it is a single observation, not a verdict.',
        blocked: 'Our own SSRF guard refused this target, so it was never contacted. It resolves somewhere we will not reach.',
      };

      return { status: 200, bodyObj: {
        product: 'mesh_probe',
        resource,
        outcome: row.outcome,
        means: meaning[row.outcome] || null,
        http_status: row.http_status,
        method_that_answered: row.method,
        price: row.outcome === 'paywalled' ? {
          atomic: row.price_atomic,
          usd: row.price_usd === null ? null : Number(row.price_usd),
          asset: row.asset,
          network: row.network,
          pay_to: row.pay_to,
          scheme: row.scheme,
          max_timeout_seconds: row.max_timeout_s,
        } : null,
        call_spec: spec,
        accepts,
        latency_ms: row.latency_ms,
        error: row.error,
        probed_at: row.probed_at ? new Date(row.probed_at * 1000).toISOString() : null,
        cached,
        cache_note: cached
          ? `Returned from a probe made within the last ${Math.round(Number(cfg.meshProbeTtlMs) / 86400000)} days. Send fresh:true to call it again now.`
          : 'Called just now.',
        no_payment_note: 'No payment header was sent, so nothing settled and nothing was spent on your behalf at the probed endpoint. You paid Animica for the lookup, not the merchant.',
        generated_at: new Date(now()).toISOString(),
      } };
    },
  };
}

module.exports.createMeshProbeProduct = createMeshProbeProduct;
