'use strict';
/**
 * THE x402 ANALYTICS ENGINE — what is the x402 economy actually doing?
 *
 * Three endpoints over the same merged index the Mesh already maintains
 * (Coinbase Bazaar + 402index, overlaid with our own unpaid 402 probes):
 *
 *   POST /x402/analytics/market  — the shape of a segment: how many services,
 *                                  what they charge, whether anyone is paying,
 *                                  whether they can be called at all.
 *   POST /x402/analytics/price   — where a price sits against real comparables,
 *                                  with the comparable set named.
 *   POST /x402/analytics/peers   — one listing measured against its own peers.
 *
 * WHY THIS IS WORTH BUYING. Bazaar and 402index publish rows. Neither publishes
 * a distribution. A merchant deciding what to charge, or an agent deciding
 * whether a quote is reasonable, needs the percentile — and needs to know which
 * services the percentile was computed over, because "the 70th percentile of
 * the x402 economy" over a badly chosen comparable set is a number that sounds
 * authoritative and means nothing.
 *
 * THE FOUR RULES THAT MAKE THESE NUMBERS HONEST:
 *
 * 1. EVERY STATISTIC IS COMPUTED IN CODE. No model produces a figure here.
 *    Inference is used for interpretation only, and `groundNumbers()` in
 *    aicf.js deletes any sentence containing a number that is not in the
 *    computed facts.
 *
 * 2. THE COMPARABLE SET IS NAMED AND COUNTED. Percentiles are refused below
 *    `analyticsMinComparables` rather than computed over four loosely-related
 *    rows. A thin segment is reported as thin; that is a real answer.
 *
 * 3. IMPLAUSIBLE PRICES ARE EXCLUDED AS BAD DATA AND COUNTED. One live listing
 *    advertises ten billion dollars a call. Including it would move every mean
 *    in the economy; silently rewriting it would be worse. It is dropped from
 *    the statistics and the drop is reported.
 *
 * 4. VOLUME IS DISCOUNTED BY PAYER CONCENTRATION. `l30DaysTotalCalls` is
 *    trivially inflatable by a merchant calling itself. Demand figures here
 *    always carry the unique-payer count beside them, and the concentration
 *    statistic is a first-class output rather than a footnote.
 *
 * TRENDS. The index is a snapshot; a single snapshot cannot show a trend. Each
 * market call records the aggregate it computed, so trend becomes available
 * once history accrues, and until then the response says `insufficient_history`
 * instead of inventing a direction of travel.
 */

const { ProductError, ProductUnavailable } = require('./errors');
const { createAicfEngine } = require('./aicf');
const M = require('./mesh-index');

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

// ---------------------------------------------------------------------------
// Statistics. Deliberately plain: an analytics product whose arithmetic a buyer
// cannot re-derive from the same public rows is not worth trusting.
// ---------------------------------------------------------------------------

function round(n, dp = 6) {
  if (!Number.isFinite(n)) return null;
  const f = 10 ** dp;
  return Math.round(n * f) / f;
}

/**
 * Nearest-rank percentile over a SORTED ascending array.
 *
 * Nearest-rank rather than interpolated on purpose: every value returned is a
 * price some service actually charges, which is the useful thing in a market
 * with a long tail and a handful of dominant round numbers.
 */
function percentile(sorted, p) {
  if (!sorted.length) return null;
  const rank = Math.ceil((p / 100) * sorted.length);
  return sorted[Math.min(sorted.length - 1, Math.max(0, rank - 1))];
}

/** The price distribution of a set of records, with bad data excluded and counted. */
function priceStats(records) {
  const usable = [];
  let unpriced = 0;
  let implausible = 0;
  for (const r of records) {
    const issue = M.priceIssue(r.price_usd);
    if (!issue) { usable.push(r.price_usd); continue; }
    if (issue.startsWith('price of $') || issue === 'price is zero or negative' || issue === 'price rounds to zero') implausible++;
    else unpriced++;
  }
  usable.sort((a, b) => a - b);
  const n = usable.length;
  const sum = usable.reduce((s, v) => s + v, 0);
  return {
    priced: n,
    unpriced,
    excluded_as_implausible: implausible,
    min: n ? round(usable[0]) : null,
    p10: round(percentile(usable, 10)),
    p25: round(percentile(usable, 25)),
    median: round(percentile(usable, 50)),
    p75: round(percentile(usable, 75)),
    p90: round(percentile(usable, 90)),
    max: n ? round(usable[n - 1]) : null,
    mean: n ? round(sum / n) : null,
    // The mean sits far above the median in this market; saying so beside the
    // two numbers stops a buyer reading the mean as "the typical price".
    skew_note: n && percentile(usable, 50) > 0
      ? `mean is ${round((sum / n) / percentile(usable, 50), 2)}x the median — the price distribution has a long upper tail, so the median is the number to price against`
      : null,
    values_sorted: usable,
  };
}

/**
 * Demand across a set, always reported with payer diversity beside volume.
 * Records with no demand data at all are counted separately from records with
 * a genuine zero, because "not published" and "nobody called it" are different
 * facts and only one of them is a market signal.
 */
function demandStats(records) {
  let calls = 0;
  let payers = 0;
  let withData = 0;
  let withCalls = 0;
  const concentrations = [];
  let mostConcentrated = null;
  for (const r of records) {
    const c = r.calls_30d || 0;
    const p = r.unique_payers_30d || 0;
    if (r.sources && r.sources.includes('bazaar')) withData++;
    if (!c) continue;
    withCalls++;
    calls += c;
    payers += p;
    const conc = 1 - (p / c);
    concentrations.push(conc);
    if (!mostConcentrated || conc > mostConcentrated.concentration) {
      mostConcentrated = { resource: r.resource, calls_30d: c, unique_payers_30d: p, concentration: round(conc, 3) };
    }
  }
  concentrations.sort((a, b) => a - b);
  return {
    services_with_demand_data: withData,
    services_called_in_30d: withCalls,
    total_calls_30d: calls,
    total_unique_payers_30d: payers,
    median_payer_concentration: round(percentile(concentrations, 50), 3),
    most_concentrated: mostConcentrated,
    concentration_note: 'Concentration is 1 - (unique payers / total calls). 0 means every call came from a different payer; values near 1 mean the volume came from very few wallets and should not be read as market demand.',
    demand_coverage_note: 'Only Bazaar publishes call and payer counts. Services listed solely in 402index contribute no demand data and are not counted as zero-demand.',
  };
}

/** Callability: the single most useful fact for an agent planning to spend. */
function callabilityStats(records) {
  const callable = records.filter((r) => r.call_spec).length;
  return {
    with_call_spec: callable,
    without_call_spec: records.length - callable,
    share_callable: records.length ? round(callable / records.length, 4) : null,
    note: 'A service without a call spec publishes a URL and a price but not the request shape. You can pay it; you cannot reliably invoke it without discovering the schema yourself.',
  };
}

/** What our own unpaid probes found within this set. */
function verificationStats(records) {
  const out = { probed: 0, paywalled: 0, open: 0, error: 0, blocked: 0, price_corrected: 0 };
  for (const r of records) {
    if (!r.probe) continue;
    out.probed++;
    if (out[r.probe.outcome] !== undefined) out[r.probe.outcome]++;
    if (r.directory_price_usd !== undefined) out.price_corrected++;
  }
  return Object.assign(out, {
    unprobed: records.length - out.probed,
    note: 'A probe is an unpaid request: by protocol a paywalled x402 resource must answer 402 with its own terms, so a 402 is the success case. "open" means a resource listed as paid answered without requiring payment at all.',
  });
}

/** Group by a field, ordered by count, with the median price inside each group. */
function groupBy(records, field, limit = 12) {
  const groups = new Map();
  for (const r of records) {
    const k = r[field] || '(unstated)';
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(r);
  }
  return [...groups.entries()]
    .map(([k, rs]) => {
      const priced = rs.map((r) => r.price_usd).filter((p) => !M.priceIssue(p)).sort((a, b) => a - b);
      return {
        value: k,
        services: rs.length,
        share: round(rs.length / records.length, 4),
        median_price_usd: round(percentile(priced, 50)),
        callable: rs.filter((r) => r.call_spec).length,
      };
    })
    .sort((a, b) => b.services - a.services)
    .slice(0, limit);
}

/** Host concentration — who actually owns this segment. */
function hostStats(records, limit = 10) {
  const byHost = new Map();
  for (const r of records) {
    let h;
    try { h = new URL(r.resource).hostname.replace(/^www\./, ''); } catch { h = '(unparseable)'; }
    if (!byHost.has(h)) byHost.set(h, []);
    byHost.get(h).push(r);
  }
  const rows = [...byHost.entries()]
    .map(([host, rs]) => {
      const priced = rs.map((r) => r.price_usd).filter((p) => !M.priceIssue(p)).sort((a, b) => a - b);
      return {
        host,
        services: rs.length,
        share_of_segment: round(rs.length / records.length, 4),
        median_price_usd: round(percentile(priced, 50)),
        calls_30d: rs.reduce((s, r) => s + (r.calls_30d || 0), 0),
      };
    })
    .sort((a, b) => b.services - a.services);
  const top = rows.slice(0, limit);
  const topShare = rows.length ? round(rows.slice(0, 3).reduce((s, r) => s + r.services, 0) / records.length, 4) : null;
  return {
    distinct_hosts: rows.length,
    top_hosts: top,
    top3_share_of_segment: topShare,
    note: 'One operator commonly deploys the same service to several hosts. A high top-3 share usually means a segment with fewer independent participants than the listing count suggests.',
  };
}

/** How much of this segment shows any sign of life. */
function freshnessStats(records, nowMs) {
  const DAY = 86_400_000;
  let recent30 = 0;
  let recent7 = 0;
  let never = 0;
  let unknown = 0;
  for (const r of records) {
    const t = r.last_called_at ? Date.parse(r.last_called_at) : NaN;
    if (!Number.isFinite(t)) { if (r.calls_30d) unknown++; else never++; continue; }
    const age = nowMs - t;
    if (age <= 7 * DAY) recent7++;
    if (age <= 30 * DAY) recent30++;
  }
  return {
    called_within_7d: recent7,
    called_within_30d: recent30,
    no_recorded_activity: never,
    activity_unknown: unknown,
    share_active_30d: records.length ? round(recent30 / records.length, 4) : null,
  };
}

/**
 * Select the segment. BM25 finds candidates; a word-coverage floor decides
 * which of them are actually about the thing that was asked.
 *
 * Without the floor this is the same failure the planner had: BM25 always
 * returns a best match, so a segment query for "weather forecast" quietly
 * includes an email validator that happens to share a token, and every
 * percentile computed over that set is wrong in a way nobody can see.
 */
function selectSegment(index, segment, cfg, { maxPriceUsd, network, requireCallable }) {
  const all = index.records;
  let matched;
  let queryTokens = [];
  let floor = null;

  if (!segment) {
    matched = all.map((r, i) => ({ record: r, relevance: null, coverage: null, i }));
  } else {
    queryTokens = M.tokens(segment);
    if (!queryTokens.length) throw bad('segment contains no searchable words — it is all stop words, punctuation or very short tokens', 'invalid_params');
    floor = Number(cfg.analyticsMinCoverage);
    const hits = [];
    for (let i = 0; i < all.length; i++) {
      const rel = index.bm25(i, queryTokens);
      if (rel <= 0) continue;
      const want = new Set(queryTokens);
      const have = new Set(M.tokens(`${all[i].description} ${all[i].resource}`));
      let hit = 0;
      for (const w of want) if (have.has(w)) hit++;
      const cov = hit / want.size;
      if (cov < floor) continue;
      hits.push({ record: all[i], relevance: rel, coverage: round(cov, 3), i });
    }
    matched = hits;
  }

  const filters = { max_price_usd: maxPriceUsd, network, require_callable: requireCallable };
  let excludedByFilter = 0;
  const kept = matched.filter(({ record: r }) => {
    if (requireCallable && !r.call_spec) { excludedByFilter++; return false; }
    if (network && String(r.network || '').toLowerCase() !== String(network).toLowerCase()) { excludedByFilter++; return false; }
    if (maxPriceUsd !== null && r.price_usd !== null && r.price_usd > maxPriceUsd) { excludedByFilter++; return false; }
    return true;
  });

  return {
    records: kept.map((x) => x.record),
    scored: kept,
    query_tokens: queryTokens,
    coverage_floor: floor,
    excluded_by_filters: excludedByFilter,
    filters,
  };
}

/** Trend, or an honest refusal. */
function trendFrom(history, current) {
  if (!history || history.length < 2) {
    return {
      available: false,
      reason: 'insufficient_history',
      snapshots_held: history ? history.length : 0,
      detail: 'A trend needs at least two snapshots of the same segment. Every call to this endpoint records one, so this fills in over time. A single index harvest cannot show a direction of travel, and one will not be inferred for you.',
    };
  }
  const oldest = history[history.length - 1];
  const spanMs = current.at - oldest.at;
  const delta = (a, b) => (a === null || b === null || a === undefined || b === undefined ? null : round(b - a, 6));
  const pct = (a, b) => (a ? round((b - a) / a, 4) : null);
  return {
    available: true,
    snapshots_held: history.length,
    span_days: round(spanMs / 86_400_000, 2),
    since: new Date(oldest.at).toISOString(),
    services: { then: oldest.services, now: current.services, change: delta(oldest.services, current.services), change_pct: pct(oldest.services, current.services) },
    median_price_usd: { then: oldest.median_price_usd, now: current.median_price_usd, change: delta(oldest.median_price_usd, current.median_price_usd), change_pct: pct(oldest.median_price_usd, current.median_price_usd) },
    calls_30d: { then: oldest.calls_30d, now: current.calls_30d, change: delta(oldest.calls_30d, current.calls_30d), change_pct: pct(oldest.calls_30d, current.calls_30d) },
    unique_payers_30d: { then: oldest.unique_payers_30d, now: current.unique_payers_30d, change: delta(oldest.unique_payers_30d, current.unique_payers_30d), change_pct: pct(oldest.unique_payers_30d, current.unique_payers_30d) },
    callable: { then: oldest.callable, now: current.callable, change: delta(oldest.callable, current.callable) },
    note: 'Snapshots are recorded by calls to this endpoint, so the series is as dense as this segment is popular. Gaps are not interpolated.',
  };
}

/** The canonical key a segment's history is stored under. */
function segmentKey(segment, filters) {
  const base = segment ? M.tokens(segment).sort().join(' ') : '__whole_market__';
  const f = [
    filters.network ? `net:${String(filters.network).toLowerCase()}` : '',
    filters.require_callable ? 'callable' : '',
    filters.max_price_usd !== null && filters.max_price_usd !== undefined ? `max:${filters.max_price_usd}` : '',
  ].filter(Boolean).join('|');
  return f ? `${base}##${f}` : base;
}

// ---------------------------------------------------------------------------
// Product 1 — POST /x402/analytics/market
// ---------------------------------------------------------------------------

function createAnalyticsMarketProduct({ cfg, indexCache, gatewayStore, fetchImpl = fetch, now = Date.now, logger = null }) {
  const aicf = createAicfEngine({ cfg, fetchImpl, now });

  return {
    id: 'analytics_market',
    title: 'x402 Analytics — the shape of a market segment',
    description:
      "Statistical analysis of any segment of the x402 economy, computed over a merged index of Coinbase Bazaar and 402index overlaid with our own unpaid 402 probes. Returns the full price distribution (min, p10, p25, median, p75, p90, max, mean), demand discounted by payer concentration, what share of the segment can actually be invoked, the network and asset split, host concentration, liveness, and — once history exists — the trend. Every figure is computed in code from named rows; the interpretation is written by Animica's own AICF inference network and every sentence containing a number not in the computed facts is deleted before delivery. Directories publish rows; this publishes the distribution.",
    path: '/x402/analytics/market',
    routes: [{ method: 'POST', path: '/x402/analytics/market' }],
    priceUsd: cfg.analyticsMarketPriceUsd,
    enabled: cfg.analyticsEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 8 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          segment: { type: 'string', required: false, description: 'words describing the segment to analyse, e.g. "weather forecast data". Omit to analyse the whole indexed economy' },
          network: { type: 'string', required: false, description: 'restrict to one settlement network, e.g. "base"' },
          max_price_usd: { type: 'number', required: false, description: 'restrict to services at or below this price per call' },
          require_callable: { type: 'boolean', required: false, description: 'restrict to services that publish a call spec (default false)' },
          top: { type: 'integer', required: false, description: 'how many example services to return, 0-25 (default 10)' },
          narrative: { type: 'boolean', required: false, description: 'include the AICF-written interpretation (default true)' },
        },
      },
      output: {
        type: 'json',
        description: 'segment {matched, coverage_floor, filters}, price {min,p10,p25,median,p75,p90,max,mean}, demand, callability, verification, networks[], assets[], hosts, freshness, trend, examples[], narrative, inference {provenance, grounding}',
      },
    },

    async availability() {
      // The deterministic half needs only the index. AICF being dark degrades
      // the narrative, and is reported in the body — it is not an outage.
      return { available: true };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      const segment = b.segment === undefined || b.segment === null ? null : String(b.segment).trim();
      if (segment !== null && segment.length > 300) throw bad('segment must be 300 characters or fewer');
      let maxPrice = null;
      if (b.max_price_usd !== undefined && b.max_price_usd !== null) {
        maxPrice = Number(b.max_price_usd);
        if (!Number.isFinite(maxPrice) || maxPrice <= 0) throw bad('max_price_usd must be a positive number');
      }
      let top = 10;
      if (b.top !== undefined && b.top !== null) {
        top = Number(b.top);
        if (!Number.isInteger(top) || top < 0 || top > 25) throw bad('top must be an integer between 0 and 25');
      }
      return {
        segment: segment || null,
        network: b.network === undefined || b.network === null ? null : String(b.network).trim().slice(0, 60),
        maxPriceUsd: maxPrice,
        requireCallable: b.require_callable === true,
        top,
        narrative: b.narrative !== false,
      };
    },

    async handler(ctx) {
      const { segment, network, maxPriceUsd, requireCallable, top, narrative } = ctx.params;
      const index = await indexCache.getIndex();
      if (!index || !index.records || !index.records.length) {
        throw new ProductUnavailable('analytics_index_empty', 'the x402 index is empty, so there is nothing to compute statistics over');
      }

      const sel = selectSegment(index, segment, cfg, { maxPriceUsd, network, requireCallable });
      const records = sel.records;

      // A segment too thin to describe is reported as thin. Percentiles over a
      // handful of rows look identical to percentiles over ten thousand, and
      // that is exactly the confusion this product exists to remove.
      const minN = Number(cfg.analyticsMinComparables);
      if (segment && records.length < minN) {
        return { status: 200, bodyObj: {
          product: 'analytics_market',
          segment,
          sufficient: false,
          matched: records.length,
          minimum_required: minN,
          reason: `only ${records.length} indexed service(s) clear the ${Math.round(Number(cfg.analyticsMinCoverage) * 100)}% word-coverage floor for this segment, and ${minN} is the minimum this endpoint will compute a distribution over.`,
          what_this_means: 'This is a finding, not a failure: either the segment barely exists on x402 yet, or it is described in words other than the ones you used. A percentile computed over a handful of loosely-related rows would look exactly like a percentile computed over ten thousand, which is the confusion this refusal prevents.',
          matched_services: records.slice(0, 10).map((r) => ({ resource: r.resource, description: r.description.slice(0, 200), price_usd: r.price_usd })),
          suggestion: 'Broaden the segment, try the vocabulary merchants use in their own descriptions, or omit `segment` entirely for whole-market statistics.',
          index: { total: index.counts.total, age_seconds: Math.round((now() - index.at) / 1000) },
          generated_at: new Date(now()).toISOString(),
        } };
      }

      const price = priceStats(records);
      const demand = demandStats(records);
      const callability = callabilityStats(records);
      const verification = verificationStats(records);
      const networks = groupBy(records, 'network');
      const assets = groupBy(records, 'asset');
      const hosts = hostStats(records);
      const freshness = freshnessStats(records, now());

      // --- trend: read history, then record this observation -----------------
      const key = segmentKey(segment, sel.filters);
      const current = {
        at: now(),
        services: records.length,
        median_price_usd: price.median,
        calls_30d: demand.total_calls_30d,
        unique_payers_30d: demand.total_unique_payers_30d,
        callable: callability.with_call_spec,
      };
      let history = [];
      if (gatewayStore && typeof gatewayStore.marketHistory === 'function') {
        try { history = gatewayStore.marketHistory(key, Number(cfg.analyticsHistoryLimit)); }
        catch (e) { logger && logger.warn && logger.warn('analytics_history_read_failed', { error: e.message }); }
      }
      const trend = trendFrom(history, current);
      if (gatewayStore && typeof gatewayStore.recordMarketSnapshot === 'function') {
        try {
          gatewayStore.recordMarketSnapshot({
            segmentKey: key,
            segment: segment || null,
            at: current.at,
            minIntervalMs: Number(cfg.analyticsSnapshotMinIntervalMs),
            stats: current,
          });
        } catch (e) {
          // History is a bonus, never a reason to fail a paid call.
          logger && logger.warn && logger.warn('analytics_history_write_failed', { error: e.message });
        }
      }

      // Examples: the biggest real payer bases in the segment, since "who is
      // actually being paid here" is the question a distribution raises.
      const examples = [...sel.scored]
        .sort((a, b) => {
          const da = M.demandScore(a.record).score;
          const db = M.demandScore(b.record).score;
          if (db !== da) return db - da;
          return (b.record.calls_30d || 0) - (a.record.calls_30d || 0);
        })
        .slice(0, top)
        .map(({ record: r, coverage: cov }) => ({
          resource: r.resource,
          description: r.description.slice(0, 220),
          price_usd: r.price_usd,
          network: r.network,
          callable: Boolean(r.call_spec),
          calls_30d: r.calls_30d || 0,
          unique_payers_30d: r.unique_payers_30d || 0,
          payer_concentration: r.calls_30d ? round(1 - (r.unique_payers_30d || 0) / r.calls_30d, 3) : null,
          verified_by_probe: r.probe ? r.probe.outcome : null,
          word_coverage: cov,
          listed_in: r.sources,
        }));

      const facts = {
        segment: segment || 'whole indexed x402 economy',
        services_matched: records.length,
        index_total: index.counts.total,
        price, demand, callability, verification,
        networks: networks.slice(0, 5),
        hosts: { distinct_hosts: hosts.distinct_hosts, top3_share_of_segment: hosts.top3_share_of_segment },
        freshness,
      };
      // The values array is for our own percentile maths, not for the model —
      // handing it ten thousand prices invites it to quote an arbitrary one.
      delete facts.price.values_sorted;

      let inference = { narrative: null, provenance: { network: 'not_requested' }, grounding: null };
      if (narrative) {
        const n = await aicf.narrate({
          instruction: segment
            ? `Interpret these statistics for the "${segment}" segment of the x402 machine-payments economy. Say what a merchant pricing into this segment, and an agent buying from it, should take away.`
            : 'Interpret these statistics for the x402 machine-payments economy as a whole. Say what stands out and what it implies for someone deciding whether to sell or buy here.',
          facts,
        });
        inference = { narrative: n.text, provenance: n.provenance, grounding: n.grounding, unavailable_reason: n.unavailable_reason };
      }

      return { status: 200, bodyObj: {
        product: 'analytics_market',
        segment: segment || null,
        sufficient: true,
        selection: {
          matched: records.length,
          share_of_index: round(records.length / index.counts.total, 4),
          coverage_floor: sel.coverage_floor,
          query_tokens: sel.query_tokens,
          excluded_by_filters: sel.excluded_by_filters,
          filters: sel.filters,
          note: segment
            ? 'A service is in this segment only if it shares at least the coverage-floor share of your query words. Lexical matching means a service describing itself in different words is missed — this is a measured population, not a census.'
            : 'No segment was given, so this is every service in the merged index.',
        },
        price,
        demand,
        callability,
        verification,
        networks,
        assets,
        hosts,
        freshness,
        trend,
        examples,
        inference,
        index: {
          total: index.counts.total,
          callable: index.counts.callable,
          priced: index.counts.priced,
          price_rejected_as_implausible: index.counts.price_rejected,
          verified_by_probe: index.counts.probe,
          sources: index.counts.sources,
          age_seconds: Math.round((now() - index.at) / 1000),
        },
        caveats: [
          'Prices, descriptions and usage counts are published by merchants about themselves. We check prices for plausibility and report payer concentration; we do not audit either.',
          `${price.excluded_as_implausible} listing(s) in this segment carry a price outside any plausible per-call band and were excluded from every statistic above rather than surfaced as bargains.`,
          'Demand data comes only from Bazaar. Services listed solely in 402index contribute no call or payer counts and are not treated as zero-demand.',
          'Segment membership is lexical. Synonyms and differently-worded descriptions are missed, so treat every count as a floor rather than a total.',
          'Every number here is computed from the index in code. No model produced any figure in this response.',
        ],
        generated_at: new Date(now()).toISOString(),
      } };
    },
  };
}

// ---------------------------------------------------------------------------
// Product 2 — POST /x402/analytics/price
// ---------------------------------------------------------------------------

function createAnalyticsPriceProduct({ cfg, indexCache, fetchImpl = fetch, now = Date.now }) {
  const aicf = createAicfEngine({ cfg, fetchImpl, now });

  return {
    id: 'analytics_price',
    title: 'x402 Analytics — where does this price sit?',
    description:
      "Price positioning for an x402 service. Describe what you sell (and optionally what you charge) and get the comparable set — named, counted, and listed — plus your exact percentile within it, the full decile ladder of what those comparables charge, how much demand sits in each price band, and a suggested band derived from the comparables rather than asserted. Refuses to compute a percentile when too few real comparables exist, because a confident percentile over four loosely-related rows is worse than no answer. Interpretation is written by Animica's own AICF inference network; every figure is computed in code.",
    path: '/x402/analytics/price',
    routes: [{ method: 'POST', path: '/x402/analytics/price' }],
    priceUsd: cfg.analyticsPricePriceUsd,
    enabled: cfg.analyticsEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 8 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          description: { type: 'string', required: true, description: 'what the service does, in the words a merchant would use to describe it' },
          price_usd: { type: 'number', required: false, description: 'the price per call you charge or are considering. Omit to get the market band without a position' },
          network: { type: 'string', required: false, description: 'restrict comparables to one settlement network' },
          top_comparables: { type: 'integer', required: false, description: 'how many comparables to list back, 0-25 (default 10)' },
          narrative: { type: 'boolean', required: false, description: 'include the AICF-written interpretation (default true)' },
        },
      },
      output: {
        type: 'json',
        description: 'comparables {count, floor, listed[]}, distribution {deciles}, your_position {percentile, vs_median}, demand_by_band[], suggested_band, inference {provenance, grounding}',
      },
    },

    async availability() {
      return { available: true };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      if (typeof b.description !== 'string' || !b.description.trim()) {
        throw bad('description is required — the comparable set is chosen from it', 'invalid_request');
      }
      const description = b.description.trim().slice(0, 600);
      let priceUsd = null;
      if (b.price_usd !== undefined && b.price_usd !== null) {
        priceUsd = Number(b.price_usd);
        if (!Number.isFinite(priceUsd) || priceUsd <= 0) throw bad('price_usd must be a positive number');
        if (priceUsd > M.MAX_PLAUSIBLE_USD) {
          throw bad(`price_usd of $${priceUsd} is outside the plausible per-call band this index ranks over (max $${M.MAX_PLAUSIBLE_USD}). Listings above it are treated as bad data, so a percentile against them would be meaningless.`);
        }
      }
      let top = 10;
      if (b.top_comparables !== undefined && b.top_comparables !== null) {
        top = Number(b.top_comparables);
        if (!Number.isInteger(top) || top < 0 || top > 25) throw bad('top_comparables must be an integer between 0 and 25');
      }
      return {
        description,
        priceUsd,
        network: b.network === undefined || b.network === null ? null : String(b.network).trim().slice(0, 60),
        top,
        narrative: b.narrative !== false,
      };
    },

    async handler(ctx) {
      const { description, priceUsd, network, top, narrative } = ctx.params;
      const index = await indexCache.getIndex();
      if (!index || !index.records || !index.records.length) {
        throw new ProductUnavailable('analytics_index_empty', 'the x402 index is empty, so there is nothing to compare against');
      }

      const sel = selectSegment(index, description, cfg, { maxPriceUsd: null, network, requireCallable: false });
      // Only PRICED comparables can position a price. A service with no
      // published price is a competitor but not a comparable.
      const priced = sel.scored.filter((x) => !M.priceIssue(x.record.price_usd));
      const minN = Number(cfg.analyticsMinComparables);

      if (priced.length < minN) {
        const closest = sel.scored.slice(0, 5).map((x) => ({
          resource: x.record.resource,
          description: x.record.description.slice(0, 160),
          price_usd: x.record.price_usd,
          word_coverage: x.coverage,
          why_not_comparable: M.priceIssue(x.record.price_usd) || null,
        }));
        return { status: 200, bodyObj: {
          product: 'analytics_price',
          description,
          your_price_usd: priceUsd,
          sufficient: false,
          comparables_found: priced.length,
          minimum_required: minN,
          reason: `only ${priced.length} indexed service(s) both clear the ${Math.round(Number(cfg.analyticsMinCoverage) * 100)}% word-coverage floor for this description AND publish a usable price. ${minN} is the minimum this endpoint will compute a percentile over.`,
          what_this_means: 'No percentile is returned, deliberately. A position computed against a handful of loosely-related listings reads exactly like one computed against a thousand, and pricing decisions get made on it. A thin comparable set is itself the finding: you may be pricing into a segment that barely exists on x402 yet, which is an argument for pricing on your own costs rather than on a market that has not formed.',
          closest_matches: closest,
          whole_market_reference: (() => {
            const p = priceStats(index.records);
            delete p.values_sorted;
            return Object.assign(p, { note: 'The whole indexed economy, offered only as a sanity reference. It is not a comparable set for your service and should not be used as one.' });
          })(),
          suggestion: 'Describe the service the way merchants in the segment describe theirs, or drop the network filter if you set one.',
          index: { total: index.counts.total, age_seconds: Math.round((now() - index.at) / 1000) },
          generated_at: new Date(now()).toISOString(),
        } };
      }

      const stats = priceStats(priced.map((x) => x.record));
      const values = stats.values_sorted;
      delete stats.values_sorted;

      const deciles = {};
      for (let d = 10; d <= 90; d += 10) deciles[`p${d}`] = round(percentile(values, d));

      // Percentile of the caller's price: the share of comparables at or below
      // it. Reported as "cheaper than N% of comparables" because that is the
      // sentence a merchant actually wants, and it is unambiguous.
      let position = null;
      if (priceUsd !== null) {
        const atOrBelow = values.filter((v) => v <= priceUsd).length;
        const strictlyBelow = values.filter((v) => v < priceUsd).length;
        const pct = round((atOrBelow / values.length) * 100, 1);
        position = {
          your_price_usd: priceUsd,
          percentile: pct,
          comparables_cheaper_than_you: strictlyBelow,
          comparables_more_expensive_than_you: values.length - atOrBelow,
          vs_median: {
            median_usd: stats.median,
            difference_usd: round(priceUsd - stats.median),
            ratio: stats.median ? round(priceUsd / stats.median, 3) : null,
          },
          reading: pct >= 90 ? 'priced above almost every comparable — defensible only if the service is materially different from them'
            : pct >= 70 ? 'priced in the expensive quartile of its comparables'
              : pct >= 30 ? 'priced within the normal band for its comparables'
                : pct >= 10 ? 'priced in the cheap quartile of its comparables'
                  : 'priced below almost every comparable — check this clears your own settlement cost before treating it as an advantage',
        };
      }

      // Where the money actually is: demand grouped by price band, so a
      // merchant can see whether the cheap end is where calls happen or just
      // where listings pile up.
      const bands = [
        { label: '<= $0.001', lo: 0, hi: 0.001 },
        { label: '$0.001 - $0.01', lo: 0.001, hi: 0.01 },
        { label: '$0.01 - $0.10', lo: 0.01, hi: 0.10 },
        { label: '$0.10 - $1.00', lo: 0.10, hi: 1 },
        { label: '> $1.00', lo: 1, hi: Infinity },
      ];
      const demandByBand = bands.map((b) => {
        const rs = priced.map((x) => x.record).filter((r) => r.price_usd > b.lo - 1e-12 && r.price_usd <= b.hi);
        const calls = rs.reduce((s, r) => s + (r.calls_30d || 0), 0);
        const payers = rs.reduce((s, r) => s + (r.unique_payers_30d || 0), 0);
        return {
          band: b.label,
          services: rs.length,
          calls_30d: calls,
          unique_payers_30d: payers,
          payer_concentration: calls ? round(1 - payers / calls, 3) : null,
          contains_your_price: priceUsd !== null && priceUsd > b.lo - 1e-12 && priceUsd <= b.hi,
        };
      });

      // The suggestion is the comparables' own interquartile range. It is not
      // a recommendation dressed up as arithmetic: it is stated as what the
      // comparables charge, with the caller's own costs named as the thing it
      // cannot know.
      const suggested = {
        low_usd: stats.p25,
        midpoint_usd: stats.median,
        high_usd: stats.p75,
        basis: `the interquartile range of the ${priced.length} priced comparables listed below`,
        what_this_does_not_know: 'your settlement cost, your gross margin, and whether your service is differentiated from these comparables. A band derived from what others charge is an input to that decision, not the decision.',
      };

      const listed = priced.slice(0, top).map((x) => ({
        resource: x.record.resource,
        description: x.record.description.slice(0, 200),
        price_usd: x.record.price_usd,
        network: x.record.network,
        callable: Boolean(x.record.call_spec),
        calls_30d: x.record.calls_30d || 0,
        unique_payers_30d: x.record.unique_payers_30d || 0,
        word_coverage: x.coverage,
        verified_by_probe: x.record.probe ? x.record.probe.outcome : null,
        price_source: x.record.directory_price_usd !== undefined
          ? `read from the merchant's own 402 challenge; its directory listing claims $${x.record.directory_price_usd}`
          : (x.record.probe && x.record.probe.outcome === 'paywalled' ? "read from the merchant's own 402 challenge" : 'directory listing, not verified by us'),
      }));

      const facts = {
        description,
        your_price_usd: priceUsd,
        comparables: priced.length,
        distribution: Object.assign({}, stats, { deciles }),
        your_position: position,
        demand_by_band: demandByBand,
        suggested_band: { low: suggested.low_usd, midpoint: suggested.midpoint_usd, high: suggested.high_usd },
      };

      let inference = { narrative: null, provenance: { network: 'not_requested' }, grounding: null };
      if (narrative) {
        const n = await aicf.narrate({
          instruction: priceUsd !== null
            ? `A merchant charges $${priceUsd} per call for: "${description}". Interpret their position against these comparables and say what it implies for their pricing.`
            : `A merchant is deciding what to charge for: "${description}". Interpret this comparable set and the band it implies.`,
          facts,
        });
        inference = { narrative: n.text, provenance: n.provenance, grounding: n.grounding, unavailable_reason: n.unavailable_reason };
      }

      return { status: 200, bodyObj: {
        product: 'analytics_price',
        description,
        sufficient: true,
        comparables: {
          count: priced.length,
          coverage_floor: sel.coverage_floor,
          query_tokens: sel.query_tokens,
          also_matched_but_unpriced: sel.scored.length - priced.length,
          listed,
          note: 'These are the services every number below was computed over. If they are not your competitors, the percentile is not about you — reword the description and run it again.',
        },
        distribution: Object.assign({}, stats, { deciles }),
        your_position: position,
        demand_by_band: demandByBand,
        suggested_band: suggested,
        inference,
        index: { total: index.counts.total, age_seconds: Math.round((now() - index.at) / 1000) },
        caveats: [
          'Comparables are chosen lexically from merchant-written descriptions. A competitor describing the same service in different words is not in this set.',
          `${stats.excluded_as_implausible} matching listing(s) carried an implausible price and were excluded from the distribution rather than allowed to move it.`,
          'A price advertised in a directory is not proof a service is being paid at that price. Where we have called the endpoint ourselves, price_source says so.',
          'Every number here is computed from the index in code. No model produced any figure in this response.',
        ],
        generated_at: new Date(now()).toISOString(),
      } };
    },
  };
}

// ---------------------------------------------------------------------------
// Product 3 — POST /x402/analytics/peers
// ---------------------------------------------------------------------------

function createAnalyticsPeersProduct({ cfg, indexCache, fetchImpl = fetch, now = Date.now }) {
  const aicf = createAicfEngine({ cfg, fetchImpl, now });

  return {
    id: 'analytics_peers',
    title: 'x402 Analytics — one endpoint against its peers',
    description:
      "Competitive position for a single x402 resource. Give a URL that is listed anywhere in the merged index and get back what the index holds on it (including any correction our own unpaid 402 probe made to its advertised price), the peer set derived from its own description, and where it ranks among those peers on price, call volume, payer diversity and callability — plus the mirrors of itself that are listed as separate services. Useful whether the endpoint is yours or one you are about to buy from. Interpretation is written by Animica's own AICF inference network; every figure is computed in code.",
    path: '/x402/analytics/peers',
    routes: [{ method: 'POST', path: '/x402/analytics/peers' }],
    priceUsd: cfg.analyticsPeersPriceUsd,
    enabled: cfg.analyticsEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 8 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          resource: { type: 'string', required: true, description: 'the absolute http(s) URL of a service listed in the x402 index' },
          top_peers: { type: 'integer', required: false, description: 'how many peers to list, 0-25 (default 10)' },
          narrative: { type: 'boolean', required: false, description: 'include the AICF-written interpretation (default true)' },
        },
      },
      output: {
        type: 'json',
        description: 'subject {listing, verified}, peers {count, listed[]}, ranks {price, calls, payer_diversity}, mirrors[], inference {provenance, grounding}',
      },
    },

    async availability() {
      return { available: true };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      if (typeof b.resource !== 'string' || !b.resource.trim()) throw bad('resource is required and must be an absolute http(s) URL', 'invalid_request');
      let u;
      try { u = new URL(b.resource.trim()); } catch { throw bad('resource must be an absolute http(s) URL'); }
      if (!['http:', 'https:'].includes(u.protocol)) throw bad('resource must be an absolute http(s) URL');
      let top = 10;
      if (b.top_peers !== undefined && b.top_peers !== null) {
        top = Number(b.top_peers);
        if (!Number.isInteger(top) || top < 0 || top > 25) throw bad('top_peers must be an integer between 0 and 25');
      }
      return { resource: b.resource.trim(), top, narrative: b.narrative !== false };
    },

    async handler(ctx) {
      const { resource, top, narrative } = ctx.params;
      const index = await indexCache.getIndex();
      if (!index || !index.records || !index.records.length) {
        throw new ProductUnavailable('analytics_index_empty', 'the x402 index is empty, so there is nothing to compare against');
      }

      const key = M.canon(resource);
      const subjectIdx = index.records.findIndex((r) => r.key === key);
      if (subjectIdx < 0) {
        // Not being indexed is a real, actionable finding for a merchant — it
        // is the same failure mode that kept our own endpoints at zero payers.
        return { status: 200, bodyObj: {
          product: 'analytics_peers',
          resource,
          found: false,
          reason: 'this resource is not in the merged index of Coinbase Bazaar and 402index',
          what_this_means: 'Agents that shop by directory cannot find it. If it is yours, that is the finding: an unlisted paid endpoint is invisible to every buyer who discovers services rather than being told about them. If it is not yours, you are being offered something no directory has ever seen.',
          index: {
            total: index.counts.total,
            sources: index.counts.sources,
            age_seconds: Math.round((now() - index.at) / 1000),
            note: 'The 402index harvest is deliberately truncated, so absence here is strong evidence but not proof of absence from that directory.',
          },
          suggestion: 'List it in a directory first, then re-run this. POST /x402/mesh/probe will confirm the endpoint answers a proper 402 challenge before you do.',
          generated_at: new Date(now()).toISOString(),
        } };
      }

      const subject = index.records[subjectIdx];
      const qTokens = M.tokens(`${subject.description} ${subject.resource}`).slice(0, 40);

      // Peers: same-description matches, minus the subject and minus its own
      // mirrors. A merchant deployed to four Vercel hosts is not four
      // competitors, and counting them as such flatters or damns unfairly.
      const mirrors = [];
      const peers = [];
      for (let i = 0; i < index.records.length; i++) {
        if (i === subjectIdx) continue;
        const r = index.records[i];
        const rel = index.bm25(i, qTokens);
        if (rel <= 0) continue;
        const sameDesc = r.description && subject.description && r.description.trim() === subject.description.trim();
        const samePrice = r.price_usd === subject.price_usd;
        if (sameDesc && samePrice) { mirrors.push(r); continue; }
        const want = new Set(qTokens);
        const have = new Set(M.tokens(`${r.description} ${r.resource}`));
        let hit = 0;
        for (const w of want) if (have.has(w)) hit++;
        const cov = hit / (want.size || 1);
        if (cov < Number(cfg.analyticsMinCoverage)) continue;
        peers.push({ record: r, relevance: rel, coverage: round(cov, 3) });
      }
      peers.sort((a, b) => b.relevance - a.relevance);

      const rankIn = (values, mine, higherIsBetter) => {
        if (mine === null || mine === undefined || !values.length) return null;
        const better = values.filter((v) => (higherIsBetter ? v > mine : v < mine)).length;
        return {
          rank: better + 1,
          of: values.length + 1,
          percentile: round(((values.length - better) / (values.length + 1)) * 100, 1),
        };
      };

      const peerRecords = peers.map((p) => p.record);
      const peerPrices = peerRecords.map((r) => r.price_usd).filter((p) => !M.priceIssue(p)).sort((a, b) => a - b);
      const peerCalls = peerRecords.map((r) => r.calls_30d || 0);
      const peerDiversity = peerRecords.filter((r) => r.calls_30d).map((r) => (r.unique_payers_30d || 0) / r.calls_30d);
      const myDiversity = subject.calls_30d ? (subject.unique_payers_30d || 0) / subject.calls_30d : null;

      const peerStats = priceStats(peerRecords);
      const peerValues = peerStats.values_sorted;
      delete peerStats.values_sorted;

      const enough = peers.length >= Number(cfg.analyticsMinComparables);
      const ranks = enough ? {
        price: Object.assign({ mine_usd: subject.price_usd, peer_median_usd: peerStats.median },
          M.priceIssue(subject.price_usd) ? { unavailable: M.priceIssue(subject.price_usd) } : { cheaper_than_peers: rankIn(peerPrices, subject.price_usd, false) }),
        calls_30d: { mine: subject.calls_30d || 0, peer_median: round(percentile([...peerCalls].sort((a, b) => a - b), 50)), position: rankIn(peerCalls, subject.calls_30d || 0, true) },
        payer_diversity: myDiversity === null
          ? { unavailable: 'no calls recorded for this resource in the last 30 days, so payer diversity cannot be computed' }
          : { mine: round(myDiversity, 3), peer_median: round(percentile([...peerDiversity].sort((a, b) => a - b), 50), 3), position: rankIn(peerDiversity, myDiversity, true) },
        callability: {
          mine: Boolean(subject.call_spec),
          peers_with_call_spec: peerRecords.filter((r) => r.call_spec).length,
          of_peers: peerRecords.length,
          note: Boolean(subject.call_spec)
            ? 'This service publishes its request shape, which most of the economy does not. That is a real and uncommon advantage in agent discovery.'
            : 'This service does not publish a request shape. An agent can pay it but must discover how to call it, which is where most automated purchase attempts stop.',
        },
      } : null;

      const facts = {
        resource: subject.resource,
        price_usd: subject.price_usd,
        calls_30d: subject.calls_30d || 0,
        unique_payers_30d: subject.unique_payers_30d || 0,
        callable: Boolean(subject.call_spec),
        peers: peers.length,
        peer_price_distribution: peerStats,
        ranks,
        mirrors: mirrors.length,
      };

      let inference = { narrative: null, provenance: { network: 'not_requested' }, grounding: null };
      if (narrative) {
        const n = await aicf.narrate({
          instruction: `Interpret how the x402 service at ${subject.resource} stands against its peers, and say what its operator should do about it.`,
          facts,
        });
        inference = { narrative: n.text, provenance: n.provenance, grounding: n.grounding, unavailable_reason: n.unavailable_reason };
      }

      return { status: 200, bodyObj: {
        product: 'analytics_peers',
        resource,
        found: true,
        subject: {
          resource: subject.resource,
          description: subject.description.slice(0, 400),
          price_usd: subject.price_usd,
          directory_price_usd: subject.directory_price_usd ?? undefined,
          price_note: subject.directory_price_usd !== undefined
            ? "we called this endpoint without paying: the price above is what its own 402 challenge quotes, and it disagrees with the directory listing shown as directory_price_usd"
            : (subject.probe && subject.probe.outcome === 'paywalled' ? "we called this endpoint without paying: the price above is from its own 402 challenge" : 'directory listing, not verified by us'),
          asset: subject.asset,
          network: subject.network,
          pay_to: subject.pay_to,
          callable: Boolean(subject.call_spec),
          call_spec: subject.call_spec,
          calls_30d: subject.calls_30d || 0,
          unique_payers_30d: subject.unique_payers_30d || 0,
          payer_concentration: subject.calls_30d ? round(1 - (subject.unique_payers_30d || 0) / subject.calls_30d, 3) : null,
          last_called_at: subject.last_called_at,
          latency_p50_ms: subject.latency_p50_ms ?? null,
          health_status: subject.health_status ?? null,
          listed_in: subject.sources,
          verified: subject.probe || null,
        },
        peers: {
          count: peers.length,
          sufficient: enough,
          minimum_for_ranking: Number(cfg.analyticsMinComparables),
          insufficient_reason: enough ? undefined
            : `only ${peers.length} peer(s) clear the coverage floor, below the ${Number(cfg.analyticsMinComparables)} needed to rank against. The listing below is what was found; no percentile is computed over it.`,
          price_distribution: peerStats,
          listed: peers.slice(0, top).map((p) => ({
            resource: p.record.resource,
            description: p.record.description.slice(0, 200),
            price_usd: p.record.price_usd,
            network: p.record.network,
            callable: Boolean(p.record.call_spec),
            calls_30d: p.record.calls_30d || 0,
            unique_payers_30d: p.record.unique_payers_30d || 0,
            word_coverage: p.coverage,
          })),
        },
        ranks,
        mirrors: {
          count: mirrors.length,
          note: mirrors.length
            ? 'These carry an identical description AND price, so they are almost certainly the same service deployed to several hosts rather than independent competitors. They are excluded from the peer set and every rank above.'
            : 'No identical deployments of this service were found under other hostnames.',
          listed: mirrors.slice(0, 10).map((r) => ({ resource: r.resource, listed_in: r.sources })),
        },
        inference,
        index: { total: index.counts.total, age_seconds: Math.round((now() - index.at) / 1000) },
        caveats: [
          'Peers are found lexically from merchant-written descriptions. A direct competitor describing itself differently will not appear here.',
          'Call and payer counts come from Bazaar and describe the merchant by their own report. Ranks are computed only over peers that publish them.',
          'Every number here is computed from the index in code. No model produced any figure in this response.',
        ],
        generated_at: new Date(now()).toISOString(),
      } };
    },
  };
}

function createAnalyticsProducts(deps) {
  return [
    createAnalyticsMarketProduct(deps),
    createAnalyticsPriceProduct(deps),
    createAnalyticsPeersProduct(deps),
  ];
}

module.exports = {
  createAnalyticsProducts,
  createAnalyticsMarketProduct,
  createAnalyticsPriceProduct,
  createAnalyticsPeersProduct,
  // exported for tests: the arithmetic is the product, so it is tested directly
  percentile, priceStats, demandStats, callabilityStats, verificationStats,
  groupBy, hostStats, freshnessStats, selectSegment, trendFrom, segmentKey, round,
};
