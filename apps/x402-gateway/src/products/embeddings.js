'use strict';
/**
 * BATCH EMBEDDINGS — all-MiniLM-L6-v2 (384-dim), served from the model the
 * deploy indexer already keeps resident on this box.
 *
 * WHY THIS IS A BATCH PRODUCT AND NOT A PER-CALL ONE. Embedding one short
 * string costs us almost nothing, but every x402 settlement costs sponsored
 * Base gas (~$0.002-0.004), so a per-string price could never be honest: it
 * would be ~99% payment overhead. Selling a BATCH amortises one settlement
 * across up to X402_EMBED_MAX_TEXTS vectors, which is the only shape in which
 * this product makes sense at all. On the ANM lane the payer pays their own
 * fee and the overhead argument mostly disappears — but the batch is still
 * the better deal, so it stays the unit we sell.
 *
 * TRUTH IN LABELLING: this is a small, fast, local sentence-transformer, not
 * a frontier embedding model. It is 384-dimensional and normalised for cosine
 * similarity. The model id is in every response so nobody has to guess what
 * produced their vectors, and so a future model change is visible rather than
 * silent.
 */

const { ProductError, ProductUnavailable } = require('./errors');

const MODEL_ID = 'all-MiniLM-L6-v2';
const DIMS = 384;

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

function createEmbeddingsProduct({ cfg, fetchImpl = fetch, now = Date.now }) {
  let healthCache = { at: 0, ok: false, detail: null };

  async function health() {
    if (now() - healthCache.at < 5000) return healthCache;
    let ok = false;
    let detail = null;
    try {
      const r = await fetchImpl(`${cfg.embedUrl}/healthz`, { signal: AbortSignal.timeout(4000) });
      ok = r.ok;
      if (!ok) detail = `embedding service answered HTTP ${r.status}`;
    } catch (e) {
      detail = `embedding service unreachable: ${e.message}`;
    }
    healthCache = { at: now(), ok, detail };
    return healthCache;
  }

  return {
    id: 'embed_batch',
    title: 'Batch text embeddings',
    description:
      `Embed up to ${cfg.embedMaxTexts} texts in ONE call with ${MODEL_ID} (${DIMS}-dimensional, normalised for cosine similarity), up to ${cfg.embedMaxCharsPerText} characters each. Sold as a batch on purpose: a single settlement costs real gas, so a per-string price would be almost entirely payment overhead — one call, many vectors, is the only honest shape for this. This is a small fast local sentence-transformer, not a frontier embedding model; the model id rides on every response so a future change is visible rather than silent.`,
    path: '/x402/embed',
    routes: [{ method: 'POST', path: '/x402/embed' }],
    priceUsd: cfg.embedPriceUsd,
    enabled: cfg.embedEnabled,
    // Compute it first: an embedding failure must charge nobody.
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 4 * 1024 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          texts: { type: 'array', required: true, description: `1..${cfg.embedMaxTexts} strings` },
          normalize: { type: 'boolean', required: false, description: 'vectors are already unit-normalised; set false only if you want them raw (default true)' },
        },
      },
      output: {
        type: 'json',
        description: 'model, dimensions, count, vectors[][] in request order, total_chars, and per-text char counts',
      },
    },

    async availability() {
      const h = await health();
      return h.ok ? { available: true } : { available: false, reason: 'embedding_service_unavailable', detail: h.detail };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      const texts = b.texts;
      if (!Array.isArray(texts) || texts.length === 0) throw bad('texts must be a non-empty array of strings', 'invalid_request');
      if (texts.length > Number(cfg.embedMaxTexts)) {
        throw bad(`${texts.length} texts exceeds the per-call cap of ${cfg.embedMaxTexts} — split the batch`, 'too_many_texts', {
          caps: { max_texts: Number(cfg.embedMaxTexts) },
        });
      }
      let totalChars = 0;
      for (let i = 0; i < texts.length; i++) {
        if (typeof texts[i] !== 'string') throw bad(`texts[${i}] must be a string`, 'invalid_request', { index: i });
        if (!texts[i].length) throw bad(`texts[${i}] is empty`, 'invalid_request', { index: i });
        if (texts[i].length > Number(cfg.embedMaxCharsPerText)) {
          throw bad(
            `texts[${i}] is ${texts[i].length} chars, over the ${cfg.embedMaxCharsPerText} cap — chunk it first`,
            'text_too_long',
            { index: i, caps: { max_chars_per_text: Number(cfg.embedMaxCharsPerText) } }
          );
        }
        totalChars += texts[i].length;
      }
      return { texts, totalChars };
    },

    async handler(ctx) {
      const { texts, totalChars } = ctx.params;
      const started = now();
      let res;
      try {
        res = await fetchImpl(`${cfg.embedUrl}/embed`, {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ texts }),
          signal: AbortSignal.timeout(Number(cfg.embedTimeoutMs)),
        });
      } catch (e) {
        const err = new Error(`embedding service failed: ${e.message}`);
        err.retryable = true;
        throw err;
      }
      if (!res.ok) {
        const err = new Error(`embedding service answered HTTP ${res.status}`);
        err.retryable = res.status >= 500;
        throw err;
      }
      let out;
      try {
        out = await res.json();
      } catch (e) {
        const err = new Error(`embedding service returned unparseable JSON: ${e.message}`);
        err.retryable = true;
        throw err;
      }
      const vectors = out && (out.vectors || out.embeddings);
      if (!Array.isArray(vectors) || vectors.length !== texts.length) {
        // A count mismatch would silently misalign every vector with the
        // wrong text — refuse rather than deliver a plausible-looking lie.
        const err = new Error(
          `embedding service returned ${Array.isArray(vectors) ? vectors.length : 'no'} vectors for ${texts.length} texts`
        );
        err.retryable = true;
        throw err;
      }

      return {
        status: 200,
        bodyObj: {
          product: 'embed_batch',
          model: MODEL_ID,
          dimensions: Array.isArray(vectors[0]) ? vectors[0].length : DIMS,
          count: vectors.length,
          vectors,
          total_chars: totalChars,
          chars: texts.map((t) => t.length),
          latency_ms: now() - started,
          notes: {
            similarity: 'vectors are unit-normalised, so cosine similarity is a plain dot product',
            order: 'vectors[i] corresponds to texts[i]; the count is checked before delivery so they cannot misalign',
            model:
              'all-MiniLM-L6-v2 running locally on the gateway host. Small and fast, not a frontier model — stated so you can judge fitness rather than assume it.',
          },
        },
      };
    },
  };
}

module.exports = { createEmbeddingsProduct, MODEL_ID, DIMS };
