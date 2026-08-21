'use strict';
/**
 * THE SCHEMA HARVESTER — turn a directory of names into an index of callable,
 * priced capabilities.
 *
 * A directory tells you a URL exists. Only about 1.8% of them say how to call
 * it or what it really costs. But the x402 protocol itself does: an unpaid
 * request to a paywalled resource MUST answer 402 with `accepts[]`, which is
 * the merchant's own authoritative statement of price, asset, network, payTo
 * and scheme — and frequently carries the request schema the directory omitted.
 *
 * So the harvester asks every indexed resource directly. **A 402 is the success
 * case.** Everything else is still worth recording: a 404 or a refused
 * connection is reliability data no directory publishes, and a 200 means the
 * resource is not actually paywalled at all, which an agent planning to pay
 * for it very much wants to know.
 *
 * THINGS THIS MUST NOT DO, AND WHY EACH GUARD EXISTS:
 *
 *  - **Never pay.** No payment header is ever attached. A probe cannot settle.
 *
 *  - **Never trigger a side effect.** Probing 31,000 unknown endpoints with a
 *    write verb is how you place someone's order or send someone's email. GET
 *    is used unless the directory explicitly declares POST, the body is empty,
 *    and a resource that answers 200 to an unpaid request is recorded as `open`
 *    and never probed again — it is not paywalled, so a second call would be a
 *    free request against somebody's real service for no new information.
 *
 *  - **Never hammer one host.** Many listings share a host (mirror deploys,
 *    one merchant with forty endpoints). Probes are scheduled with a per-host
 *    serial lock and a delay between them, so a harvest of 31,000 resources is
 *    never 40 simultaneous requests to one small server.
 *
 *  - **Never reach a private network.** These URLs come from a public directory
 *    and are attacker-controllable by anyone who can list a service, so every
 *    probe goes through the same SSRF guard as the fetch product.
 *
 *  - **Never block the gateway.** The harvest runs as a slow background sweep
 *    with a hard per-tick budget. Serving traffic always wins.
 */

const dns = require('node:dns').promises;
const { resolveSafely, parseTarget } = require('./web');
const M = require('./mesh-index');

/** Outcomes, in the order of how useful they are to a buyer. */
const OUTCOME = {
  PAYWALLED: 'paywalled',   // 402 with usable accepts[] — the good case
  OPEN: 'open',             // answered without payment; not actually paywalled
  DEAD: 'dead',             // 404/410 — listed but gone
  ERROR: 'error',           // 5xx, timeout, TLS failure, refused
  BLOCKED: 'blocked',       // our own SSRF guard refused the target
};

/**
 * Pull the payment terms out of a 402 body. x402 has moved through a few
 * shapes, so accept all of them rather than only the newest: a harvester that
 * silently records "no terms" for an older-format merchant is worse than one
 * that admits it did not understand.
 */
function parseAccepts(body) {
  if (!body || typeof body !== 'object') return null;
  const list = body.accepts || body.paymentRequirements || (body.x402 && body.x402.accepts);
  if (!Array.isArray(list) || !list.length) return null;
  return list;
}

function pickAccept(accepts) {
  // Prefer a Base/USDC lane when several are offered, because that is what the
  // overwhelming majority of the economy settles in and what a caller compares.
  return accepts.find((a) => String(a.network || '').includes('8453')) || accepts[0];
}

function atomicToUsd(amount, accept) {
  const n = Number(amount);
  if (!Number.isFinite(n)) return null;
  const dec = Number(accept && accept.extra && accept.extra.decimals);
  return n / 10 ** (Number.isFinite(dec) ? dec : 6);
}

/** The request shape, if the 402 published one anywhere we know to look. */
function specFrom(accept, body) {
  const candidates = [
    accept && accept.outputSchema && accept.outputSchema.input,
    accept && accept.extensions && accept.extensions.bazaar && accept.extensions.bazaar.info,
    body && body.outputSchema && body.outputSchema.input,
  ].filter(Boolean);
  for (const c of candidates) {
    if (c.method || c.bodyFields || c.inputSchema || c.queryParams) {
      return {
        method: c.method || null,
        body_type: c.bodyType || null,
        body_fields: c.bodyFields || c.inputSchema || null,
        query_params: c.queryParams ? Object.keys(c.queryParams) : null,
        description: typeof c.description === 'string' ? c.description.slice(0, 400) : null,
      };
    }
  }
  return null;
}

/**
 * A per-host serial lock with a delay. One merchant's forty listings become
 * forty polite sequential probes rather than a burst that looks like an attack.
 */
function createHostGate(delayMs) {
  const chains = new Map();
  return function schedule(host, fn) {
    const prev = chains.get(host) || Promise.resolve();
    const next = prev
      .then(() => new Promise((r) => setTimeout(r, delayMs)))
      .then(fn, fn);
    // Keep the chain from growing without bound for a host we stop probing.
    chains.set(host, next.then(() => {}, () => {}));
    return next;
  };
}

function createHarvester({ cfg, gatewayStore, fetchImpl = fetch, now = Date.now, lookup = dns.lookup, logger = null }) {
  const gate = createHostGate(Number(cfg.meshProbeHostDelayMs));
  let sweeping = false;
  let stopped = false;

  /**
   * Probe ONE resource. Returns the row written to the store.
   *
   * `declaredMethod` comes from the directory when it published one. Without it
   * we use GET: it is the only verb HTTP defines as safe, and guessing POST
   * against an unknown endpoint is how a discovery tool causes a side effect.
   */
  async function probeOne(resource, { declaredMethod = null, timeoutMs = null } = {}) {
    const key = M.canon(resource);
    const started = now();
    const base = { key, resource, probed_at: Math.floor(now() / 1000), latency_ms: null,
      method: null, http_status: null, price_atomic: null, price_usd: null, asset: null,
      network: null, pay_to: null, scheme: null, max_timeout_s: null,
      call_spec_json: null, accepts_json: null, error: null };

    let u;
    try {
      u = parseTarget(resource);
    } catch (e) {
      return { ...base, outcome: OUTCOME.BLOCKED, error: 'unparseable url' };
    }
    try {
      await resolveSafely(u.hostname, lookup);
    } catch (e) {
      return { ...base, outcome: OUTCOME.BLOCKED, error: String(e.message).slice(0, 200) };
    }

    const verbs = declaredMethod
      ? [declaredMethod.toUpperCase()]
      : ['GET', 'POST'];   // POST only as a fallback, with an empty body

    let last = null;
    for (const method of verbs) {
      let res;
      const t0 = now();
      try {
        res = await fetchImpl(u.toString(), {
          method,
          headers: Object.assign(
            { accept: 'application/json', 'user-agent': cfg.meshProbeUserAgent },
            method === 'POST' ? { 'content-type': 'application/json' } : {},
          ),
          body: method === 'POST' ? '{}' : undefined,
          redirect: 'follow',
          signal: AbortSignal.timeout(Number(timeoutMs || cfg.meshProbeTimeoutMs)),
        });
      } catch (e) {
        last = { ...base, outcome: OUTCOME.ERROR, method, latency_ms: now() - t0,
          error: String(e && e.name === 'TimeoutError' ? 'timeout' : e && e.message).slice(0, 200) };
        continue;
      }
      const latency = now() - t0;
      let body = null;
      try {
        const text = (await res.text()).slice(0, 200_000);
        body = JSON.parse(text);
      } catch { /* a non-JSON 402 tells us little, but the status still counts */ }

      if (res.status === 402) {
        const accepts = parseAccepts(body);
        if (!accepts) {
          return { ...base, outcome: OUTCOME.PAYWALLED, method, http_status: 402, latency_ms: latency,
            error: 'answered 402 but published no readable accepts[]' };
        }
        const a = pickAccept(accepts);
        const amount = a.maxAmountRequired ?? a.amount ?? null;
        const spec = specFrom(a, body);
        return {
          ...base,
          outcome: OUTCOME.PAYWALLED,
          method,
          http_status: 402,
          latency_ms: latency,
          price_atomic: amount === null ? null : String(amount),
          price_usd: amount === null ? null : String(atomicToUsd(amount, a)),
          asset: (a.extra && a.extra.name) || a.asset || null,
          network: a.network || null,
          pay_to: a.payTo || a.recipient || null,
          scheme: a.scheme || null,
          max_timeout_s: Number.isFinite(Number(a.maxTimeoutSeconds)) ? Number(a.maxTimeoutSeconds) : null,
          call_spec_json: spec ? JSON.stringify(spec) : null,
          accepts_json: JSON.stringify(accepts).slice(0, 20_000),
        };
      }
      if (res.status >= 200 && res.status < 300) {
        // Not paywalled. Recorded and never retried: a second unpaid call would
        // be a real request against somebody's service for no new information.
        return { ...base, outcome: OUTCOME.OPEN, method, http_status: res.status, latency_ms: latency,
          error: 'answered without requiring payment — this resource is not actually paywalled' };
      }
      if (res.status === 404 || res.status === 410) {
        last = { ...base, outcome: OUTCOME.DEAD, method, http_status: res.status, latency_ms: latency,
          error: 'listed in a directory but not served' };
        continue;   // a POST-only endpoint 404s on GET; try the other verb
      }
      if (res.status === 405) {
        last = { ...base, outcome: OUTCOME.ERROR, method, http_status: 405, latency_ms: latency,
          error: 'method not allowed' };
        continue;
      }
      last = { ...base, outcome: OUTCOME.ERROR, method, http_status: res.status, latency_ms: latency,
        error: `unexpected status ${res.status}` };
    }
    return last || { ...base, outcome: OUTCOME.ERROR, error: 'no verb produced a usable answer' };
  }

  /** Probe and persist, serialised per host. */
  async function probeAndStore(resource, opts) {
    let host;
    try { host = new URL(resource).hostname; } catch { host = resource; }
    return gate(host, async () => {
      const row = await probeOne(resource, opts);
      try { gatewayStore.putProbe(row); } catch (e) {
        logger && logger.warn && logger.warn('mesh_probe_store_failed', { error: e.message });
      }
      return row;
    });
  }

  /**
   * One background sweep: probe the resources we know least about, newest
   * knowledge last. Bounded by wall clock so it can never crowd out serving.
   */
  async function sweep(records) {
    if (sweeping || stopped) return { skipped: true };
    sweeping = true;
    const deadline = now() + Number(cfg.meshSweepBudgetMs);
    const counts = { probed: 0, paywalled: 0, open: 0, dead: 0, error: 0, blocked: 0 };
    try {
      const staleAfter = Number(cfg.meshProbeTtlMs);
      const nowSec = Math.floor(now() / 1000);
      // Never-probed first, then the stalest. An `open` resource is deliberately
      // never re-probed: it is not paywalled, and re-asking is a free call
      // against someone's real service that teaches us nothing.
      const queue = [];
      for (const r of records) {
        const prev = gatewayStore.getProbe(r.key);
        if (prev && prev.outcome === OUTCOME.OPEN) continue;
        // Never-probed sorts first, but as a FINITE sentinel: `Infinity -
        // Infinity` is NaN, and a comparator returning NaN leaves the order
        // undefined, which quietly wrecked the "oldest first" guarantee.
        const age = prev ? (nowSec - prev.probed_at) * 1000 : Number.MAX_SAFE_INTEGER;
        if (age < staleAfter) continue;
        let host;
        try { host = new URL(r.resource).hostname; } catch { host = r.resource; }
        queue.push({ r, age, host });
      }
      queue.sort((a, b) => b.age - a.age);

      // Interleave by host. Probes to one host are serialised with a delay
      // between them, so a batch that happens to be forty listings from one
      // merchant would spend the entire sweep budget walking that single chain
      // while every other host sat idle. Round-robin keeps the per-host rate
      // exactly as polite while letting the sweep actually make progress.
      const byHost = new Map();
      for (const q of queue) {
        if (!byHost.has(q.host)) byHost.set(q.host, []);
        byHost.get(q.host).push(q);
      }
      const interleaved = [];
      const chains = [...byHost.values()];
      for (let depth = 0; interleaved.length < queue.length; depth++) {
        let added = false;
        for (const chain of chains) {
          if (depth < chain.length) { interleaved.push(chain[depth]); added = true; }
        }
        if (!added) break;
      }

      const batch = interleaved.slice(0, Number(cfg.meshSweepMaxProbes));
      const inFlight = new Set();
      for (const { r } of batch) {
        if (now() > deadline || stopped) break;
        const method = r.call_spec && r.call_spec.method ? r.call_spec.method : null;
        const p = probeAndStore(r.resource, { declaredMethod: method })
          .then((row) => {
            counts.probed++;
            counts[row.outcome] = (counts[row.outcome] || 0) + 1;
          })
          .catch(() => { counts.error++; })
          .finally(() => inFlight.delete(p));
        inFlight.add(p);
        if (inFlight.size >= Number(cfg.meshSweepConcurrency)) await Promise.race(inFlight);
      }
      await Promise.all(inFlight);
      logger && logger.info && logger.info('mesh_sweep_done', {
        ...counts, queued: queue.length, distinct_hosts: byHost.size, budget_ms: Number(cfg.meshSweepBudgetMs),
      });
      return counts;
    } finally {
      sweeping = false;
    }
  }

  return {
    probeOne,
    probeAndStore,
    sweep,
    stop() { stopped = true; },
    isSweeping: () => sweeping,
    OUTCOME,
  };
}

module.exports = { createHarvester, parseAccepts, pickAccept, specFrom, atomicToUsd, createHostGate, OUTCOME };
