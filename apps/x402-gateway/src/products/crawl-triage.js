'use strict';
/**
 * PAID CRAWL — unknown-User-Agent triage, run on Animica's own miners.
 *
 * WHAT THIS OFFLOADS, AND WHY IT IS THE RIGHT PIECE. The gate's hot path is
 * deterministic and must stay that way: a website's edge is waiting on it,
 * and a model in that loop would add seconds of latency to every page and put
 * a hallucination between a crawler and somebody's content. So the work sent
 * to the network is the part that is genuinely fuzzy and genuinely NOT
 * urgent — reading the User-Agent strings the hand-written taxonomy did not
 * recognise, and proposing what they are. That is real judgment work, it
 * batches naturally, and being wrong about it is harmless.
 *
 * A REGISTERED AICF WORKER IS PAID IN ANM FOR EVERY ONE OF THESE JOBS. That
 * is the point of running it here rather than on the gateway box: the
 * classifier improves, and the network's miners earn for improving it.
 *
 * THE PROPOSAL IS ADVISORY AND CAN NEVER BILL ANYONE. Proposals land in
 * crawl_unknown_ua with status 'triaged' and are read by exactly one thing:
 * an operator running `animica-x402 crawl proposals`. The gate consults the
 * reviewed taxonomy in crawl-classify.js and nothing else. This is deliberate
 * and load-bearing — a model that decides "this UA is an AI crawler" would
 * otherwise be a model that decides who gets charged money, and a confidently
 * wrong answer would bill a customer's browser.
 *
 * PROVENANCE IS NOT OPTIONAL. The chat bridge in front of AICF falls back to
 * a local model when no worker claims the job, and announces that ONLY by
 * changing the `model` field of the response. Every proposal therefore stores
 * `served_by`, so an operator reviewing the queue can see whether the network
 * actually did this work or whether the box answered its own question. A
 * proposal with no worker behind it is still recorded — it is just labelled.
 *
 * THE MODEL NEVER PRODUCES A NUMBER. It picks from a closed set of labels and
 * names an operator. Prices, counts and shares are computed in code from the
 * store, because a figure invented by a model that reaches a billing surface
 * is the failure mode this codebase has already paid for once.
 */

const { AGENTS } = require('./crawl-classify');

/** The closed label set. Anything outside it is rejected on parse. */
const LABELS = ['search', 'ai_training', 'ai_answers', 'monitoring', 'preview', 'scraping', 'browser', 'unknown'];

const SYSTEM = 'You identify HTTP User-Agent strings. You are given one User-Agent string. '
  + 'Answer with a single JSON object and nothing else: '
  + '{"label": one of ' + JSON.stringify(LABELS) + ', "operator": the company or project that runs it or null, '
  + '"confidence": one of "high"|"medium"|"low", "why": one short sentence}. '
  + 'RULES: never invent a company you are not confident about — use null. '
  + 'Never output a price, a number, a rate, or any figure. '
  + 'No markdown, no code fences, no prose outside the JSON object.';

/** Pull the first JSON object out of a model reply that may still be wrapped. */
function parseProposal(text) {
  const raw = String(text || '').replace(/```[a-z]*\n?|```/g, '').trim();
  const start = raw.indexOf('{');
  const end = raw.lastIndexOf('}');
  if (start === -1 || end === -1 || end <= start) return null;
  let obj;
  try {
    obj = JSON.parse(raw.slice(start, end + 1));
  } catch (_e) {
    return null;
  }
  if (!obj || typeof obj !== 'object') return null;
  const label = String(obj.label || '').trim();
  if (!LABELS.includes(label)) return null;
  const confidence = ['high', 'medium', 'low'].includes(String(obj.confidence)) ? String(obj.confidence) : 'low';
  return {
    label,
    operator: obj.operator === null || obj.operator === undefined || obj.operator === '' ? null : String(obj.operator).slice(0, 80),
    confidence,
    why: obj.why ? String(obj.why).slice(0, 240) : null,
  };
}

/**
 * A proposal only ever ADDS a candidate. If the string in fact matches a
 * taxonomy entry already, the deterministic classifier would have caught it,
 * so a proposal for it means the model is re-deriving something we know —
 * flag it rather than storing a duplicate opinion.
 */
function alreadyKnown(userAgent) {
  const ua = String(userAgent || '').toLowerCase();
  return AGENTS.some((a) => ua.includes(a.pattern));
}

/**
 * createCrawlTriage({ gatewayStore, aicf, cfg, now })
 *
 * `aicf` is the engine from products/aicf.js — we use its raw() and
 * provenanceOf() rather than growing a second inference client, so the
 * network selection, timeout and fallback-detection rules stay in one place.
 */
function createCrawlTriage({ gatewayStore, aicf, cfg = {}, logger = null, now = Date.now }) {
  const log = (msg, extra) => { if (logger && logger.info) logger.info(msg, extra); };

  /**
   * Triage up to `limit` unrecognised User-Agents. Returns a summary; never
   * throws. A dark network is a no-op with a stated reason, not an error —
   * this runs on a timer and must never take the gateway down with it.
   */
  async function runOnce({ limit = 10 } = {}) {
    if (!gatewayStore) return { ok: false, reason: 'no_store', triaged: 0 };
    if (!aicf || typeof aicf.raw !== 'function') return { ok: false, reason: 'no_aicf_engine', triaged: 0 };

    let queue;
    try {
      queue = gatewayStore.untriagedUserAgents(limit) || [];
    } catch (e) {
      return { ok: false, reason: `store_read_failed:${e.message}`, triaged: 0 };
    }
    if (queue.length === 0) return { ok: true, reason: 'queue_empty', triaged: 0 };

    let triaged = 0;
    let servedByNetwork = 0;
    const results = [];

    for (const row of queue) {
      const ua = String(row.user_agent || '');
      if (!ua) continue;
      let out;
      try {
        out = await aicf.raw(
          [{ role: 'system', content: SYSTEM }, { role: 'user', content: ua }],
          220,
        );
      } catch (e) {
        // One dead job must not abandon the rest of the batch.
        results.push({ ua, ok: false, reason: `aicf_failed:${e.message}` });
        continue;
      }
      const provenance = aicf.provenanceOf ? aicf.provenanceOf(out.servedModel, out.latencyMs) : null;
      const onNetwork = !!(provenance && provenance.network === 'aicf');
      if (onNetwork) servedByNetwork += 1;

      const proposal = parseProposal(out.text);
      if (!proposal) {
        results.push({ ua, ok: false, reason: 'unparseable_proposal', served_by: out.servedModel || null });
        continue;
      }
      proposal.already_in_taxonomy = alreadyKnown(ua);
      proposal.network = onNetwork ? 'aicf' : 'fallback';
      proposal.advisory = true;

      try {
        gatewayStore.setUaProposal({ uaHash: row.ua_hash, proposal, servedBy: out.servedModel || null });
        triaged += 1;
        results.push({ ua, ok: true, label: proposal.label, operator: proposal.operator, served_by: out.servedModel || null, network: proposal.network });
      } catch (e) {
        results.push({ ua, ok: false, reason: `store_write_failed:${e.message}` });
      }
    }

    log('paid-crawl: ua triage batch', { queued: queue.length, triaged, servedByNetwork });
    return {
      ok: true,
      triaged,
      considered: queue.length,
      served_by_aicf: servedByNetwork,
      // Stated plainly so an operator reading the summary is never left to
      // assume the network did work the local fallback actually did.
      served_by_fallback: triaged - servedByNetwork,
      results,
    };
  }

  /**
   * Background timer. Slow on purpose: this is opportunistic improvement of a
   * classifier that already works, not a queue anyone is waiting on, and each
   * job costs a miner real GPU time.
   */
  function schedule({ intervalMs = 15 * 60 * 1000, limit = 10 } = {}) {
    const t = setInterval(() => {
      runOnce({ limit }).catch(() => {});
    }, intervalMs);
    if (t.unref) t.unref();
    return t;
  }

  return { runOnce, schedule, parseProposal, LABELS };
}

module.exports = { createCrawlTriage, parseProposal, alreadyKnown, LABELS, SYSTEM };
