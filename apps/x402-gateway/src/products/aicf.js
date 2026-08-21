'use strict';
/**
 * THE AICF INFERENCE ENGINE — inference bought from Animica's own network.
 *
 * WHY THIS FILE EXISTS SEPARATELY FROM structured.js. Every other model call in
 * this gateway goes to `cfg.utilityInferenceUrl`, which today is the pool API's
 * OpenAI-compatible /v1 — and that maps `anm-fast-8b` onto a local ollama
 * process on this box. That is a perfectly good small-model backend, but it is
 * NOT AICF. AICF is the on-chain fabric: a job is queued on the node, a
 * registered worker claims it, serves the tokens, and is PAID IN ANM for the
 * work. When we say an Animica product runs on Animica's own inference network,
 * that has to mean the second thing, and it has to be checkable.
 *
 * THE PROVENANCE PROBLEM, AND HOW IT IS SOLVED HERE.
 *
 * The chat bridge in front of AICF (`apps/animica-chat/bridge/server.py`) has a
 * last-resort upstream: when every AICF worker times out or returns a stub, it
 * answers from the pool's ollama rather than apologising. That is the right
 * behaviour for a chat window and the WRONG thing to be unaware of in a product
 * that advertises where its intelligence comes from. The bridge is honest about
 * it in exactly one place: the `model` field of the response.
 *
 *   - It echoes back the model you REQUESTED when an AICF worker served you
 *     (`model_label = req.model or DEFAULT_MODEL`).
 *   - It replaces it with the model that ACTUALLY answered when the fallback
 *     served you (`text, model_label = alt[0], alt[1]`).
 *
 * So a response whose `model` is not the network model we asked for was not
 * served by AICF, and this engine says so — in the product body, every time,
 * as `provenance.served_by`. Measured on this host 2026-08-19: asking for
 * `animica-chat` returned `anm-fast-8b`, i.e. no AICF worker claimed the job
 * and the pool answered. A buyer is told that rather than being sold a
 * decentralised-inference story the request did not actually get.
 *
 * INFERENCE IS NEVER AN AVAILABILITY DEPENDENCY. Same rule the GEO fixer
 * follows: the deterministic half of every analytics answer ships even when the
 * network is dark. `narrate()` returns a null narrative with a stated reason
 * instead of throwing, and callers are built to render that.
 *
 * THE MODEL WRITES PROSE, NOT NUMBERS. The bridge injects its own chat system
 * prompt into every turn (measured: ~195 tokens of house grounding on a
 * six-token question), so this is not a clean instruction-following channel and
 * must not be treated as one. Every statistic in every analytics response is
 * computed in JavaScript from the index. The model is asked only to interpret
 * facts it is handed, and `groundNumbers()` below deletes any sentence
 * containing a figure that is not in that fact set — the same discipline as
 * discarding model-invented URLs in the GEO fixer, applied to statistics.
 */

const { ProductError } = require('./errors');

/**
 * Numbers we accept in a narrative without them appearing in the fact set.
 * Ordinals and small counts ("the top 3", "both of the two") are prose, not
 * claims, and stripping sentences over them would gut every summary.
 */
const FREE_SMALL_INTEGERS = new Set([0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 100]);

/** Every numeric literal in a piece of prose, including $1.23, 45%, 1,234. */
function numbersIn(text) {
  const out = [];
  const re = /-?\d[\d,]*(?:\.\d+)?/g;
  let m;
  while ((m = re.exec(String(text))) !== null) {
    const n = Number(m[0].replace(/,/g, ''));
    if (Number.isFinite(n)) out.push(n);
  }
  return out;
}

/**
 * Flatten the computed facts into the set of numbers the model is allowed to
 * repeat. Percentages and rounded forms are added alongside the raw value,
 * because "0.183" being reported as "18.3%" is a correct restatement and
 * rejecting it would train the prose to be useless.
 */
function allowedNumbers(facts, out = new Set()) {
  const add = (n) => {
    if (!Number.isFinite(n)) return;
    out.add(n);
    out.add(Math.round(n));
    out.add(Math.round(n * 10) / 10);
    out.add(Math.round(n * 100) / 100);
    out.add(Math.round(n * 1000) / 1000);
    out.add(Math.round(n * 10000) / 10000);
    // A ratio restated as a percentage, and a percentage restated as a ratio.
    if (Math.abs(n) <= 1) {
      out.add(Math.round(n * 100));
      out.add(Math.round(n * 1000) / 10);
    }
    if (Math.abs(n) > 1 && Math.abs(n) <= 100) out.add(Math.round(n) / 100);
  };
  const walk = (v) => {
    if (v === null || v === undefined) return;
    if (typeof v === 'number') { add(v); return; }
    if (typeof v === 'string') { for (const n of numbersIn(v)) add(n); return; }
    if (Array.isArray(v)) { for (const x of v) walk(x); return; }
    if (typeof v === 'object') { for (const x of Object.values(v)) walk(x); }
  };
  walk(facts);
  return out;
}

/** Split prose into sentences, keeping the terminator so rejoining reads right. */
function sentences(text) {
  return String(text)
    .split(/(?<=[.!?])\s+/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * Drop any sentence asserting a number that is not in the fact set.
 *
 * This is the honesty guard that makes a model-written summary safe to sell
 * beside computed statistics. A small model handed twenty figures will
 * confidently produce a twenty-first, and a plausible invented median is far
 * more damaging in a pricing product than a shorter summary. Removals are
 * COUNTED and reported, never silently swallowed.
 */
function groundNumbers(text, facts) {
  const allowed = allowedNumbers(facts);
  const kept = [];
  const dropped = [];
  for (const s of sentences(text)) {
    const nums = numbersIn(s);
    const bad = nums.filter((n) => !allowed.has(n) && !FREE_SMALL_INTEGERS.has(n));
    if (bad.length) dropped.push({ sentence: s.slice(0, 240), ungrounded: [...new Set(bad)].slice(0, 6) });
    else kept.push(s);
  }
  return { text: kept.join(' '), kept: kept.length, dropped };
}

/**
 * @param cfg  needs aicfUrl, aicfHealthUrl, aicfModel, aicfTimeoutMs,
 *             aicfMaxTokens, aicfKey (optional), aicfEnabled
 */
function createAicfEngine({ cfg, fetchImpl = fetch, now = Date.now }) {
  const model = String(cfg.aicfModel);

  /**
   * One raw completion. Returns { text, servedModel, latencyMs } or throws.
   * Never used directly by a product — everything goes through narrate().
   */
  async function raw(messages, maxTokens) {
    const headers = { 'content-type': 'application/json' };
    if (cfg.aicfKey) headers.authorization = `Bearer ${cfg.aicfKey}`;
    const t0 = now();
    let r;
    try {
      r = await fetchImpl(cfg.aicfUrl, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          model,
          messages,
          max_tokens: maxTokens || Number(cfg.aicfMaxTokens),
          temperature: 0,
        }),
        // AICF is a job queue with a claim step, not a local socket. A worker
        // claiming and then loading a model routinely takes tens of seconds,
        // and cutting that off would guarantee we only ever see the fallback.
        signal: AbortSignal.timeout(Number(cfg.aicfTimeoutMs)),
      });
    } catch (e) {
      throw new Error(`aicf unreachable: ${e.message}`);
    }
    if (!r.ok) throw new Error(`aicf HTTP ${r.status}`);
    const j = await r.json();
    const text = j && j.choices && j.choices[0] && j.choices[0].message && j.choices[0].message.content;
    if (typeof text !== 'string' || !text.trim()) throw new Error('aicf returned no content');
    return { text, servedModel: (j && j.model) || null, latencyMs: now() - t0 };
  }

  /**
   * Which network model actually answered, and was it AICF at all.
   *
   * `served === requested` is the bridge's own signal that a registered worker
   * served the job; anything else is the last-resort upstream naming the model
   * that really ran. There is one ambiguous case — configuring `aicfModel` to
   * the same string the bridge falls back to — and it is reported as
   * `indeterminate` rather than resolved in our favour.
   */
  function provenanceOf(servedModel, latencyMs) {
    const served = servedModel || null;
    if (served && served === model) {
      return {
        network: 'aicf',
        requested_model: model,
        served_by: served,
        latency_ms: latencyMs,
        verified_by: 'the AICF bridge echoes the requested network model only when a registered worker served the job; a fallback names the model that actually ran',
        note: 'served by a registered AICF worker, which is paid in ANM for this inference',
      };
    }
    if (served && cfg.aicfFallbackModelHint && served === cfg.aicfFallbackModelHint && served === model) {
      return { network: 'indeterminate', requested_model: model, served_by: served, latency_ms: latencyMs };
    }
    return {
      network: 'fallback',
      requested_model: model,
      served_by: served,
      latency_ms: latencyMs,
      verified_by: 'the response named a different model than the one requested, which is how the AICF bridge reports that no registered worker served the job',
      note: `no AICF worker served this request; it was answered by ${served || 'an unnamed upstream'} instead. The computed statistics in this response are unaffected — they are never produced by a model.`,
    };
  }

  /**
   * Ask AICF to interpret facts we computed. NEVER throws.
   *
   * Returns { text, provenance, grounding } where `text` is null when the
   * network could not answer or everything it said was ungrounded. The caller
   * renders the deterministic result either way.
   */
  async function narrate({ instruction, facts, maxTokens, maxChars = 1400 }) {
    if (!cfg.aicfEnabled) {
      return { text: null, provenance: { network: 'disabled', requested_model: model }, grounding: null,
        unavailable_reason: 'AICF narration is disabled by configuration (X402_AICF_ENABLED=0)' };
    }
    const sys =
      'You are a market analyst writing two to five sentences of plain prose. '
      + 'You are given a JSON block of ALREADY-COMPUTED statistics. Interpret them: say what stands out and what a buyer should do about it. '
      + 'RULES: use ONLY numbers that appear in the JSON. Never estimate, extrapolate, or introduce a figure of your own. '
      + 'Do not repeat the JSON back. Do not use markdown, headings, bullet points, or code fences. Plain sentences only.';
    const messages = [
      { role: 'system', content: sys },
      { role: 'user', content: `${instruction}\n\nCOMPUTED STATISTICS:\n${JSON.stringify(facts)}` },
    ];

    let out;
    try {
      out = await raw(messages, maxTokens);
    } catch (e) {
      return {
        text: null,
        provenance: { network: 'unavailable', requested_model: model },
        grounding: null,
        unavailable_reason: `AICF did not answer: ${e.message}`,
      };
    }

    const provenance = provenanceOf(out.servedModel, out.latencyMs);
    // Strip fences and headings a chat-tuned model adds despite the instruction.
    const cleaned = String(out.text)
      .replace(/```[a-z]*\n?|```/g, '')
      .replace(/^\s*[#>*-]+\s*/gm, '')
      .trim();
    const g = groundNumbers(cleaned, facts);
    const text = g.text.trim().slice(0, maxChars) || null;

    return {
      text,
      provenance,
      grounding: {
        sentences_kept: g.kept,
        sentences_dropped: g.dropped.length,
        dropped: g.dropped.slice(0, 5),
        rule: 'Every statistic in this response is computed from the index in code. Any sentence the model wrote containing a number that is not in those computed facts was deleted before you saw it.',
      },
      unavailable_reason: text ? undefined
        : 'the model answered, but every sentence it produced asserted a figure that was not in the computed facts, so none of it was kept',
    };
  }

  /**
   * Is the network serving at all? Reports the tiers the bridge advertises so
   * the answer is specific enough to act on, and never throws.
   */
  async function health() {
    if (!cfg.aicfEnabled) return { available: false, reason: 'aicf_disabled', detail: 'X402_AICF_ENABLED=0' };
    try {
      const r = await fetchImpl(cfg.aicfHealthUrl, { signal: AbortSignal.timeout(8000) });
      if (!r.ok) return { available: false, reason: 'aicf_unavailable', detail: `health HTTP ${r.status}` };
      const j = await r.json();
      const models = Array.isArray(j && j.data) ? j.data : [];
      const serving = models.filter((m) => m && m.serving === true).map((m) => m.id);
      const known = models.some((m) => m && m.id === model);
      return {
        available: true,
        models: models.map((m) => m && m.id).filter(Boolean),
        serving,
        requested_model_listed: known,
        // Deliberately NOT gating availability on `serving`: the bridge answers
        // either way, and the product's deterministic half does not need a
        // model at all. What matters is telling the buyer which one served.
      };
    } catch (e) {
      return { available: false, reason: 'aicf_unavailable', detail: e.message };
    }
  }

  return { narrate, health, raw, provenanceOf };
}

module.exports = {
  createAicfEngine, groundNumbers, allowedNumbers, numbersIn, sentences, ProductError,
};
