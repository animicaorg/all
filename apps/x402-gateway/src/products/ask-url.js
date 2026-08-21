'use strict';
/**
 * ASK-A-URL - ephemeral retrieval-augmented answering over one web page.
 *
 * The agent hands us a URL and a question; we fetch the page, chunk it, embed
 * the chunks and the question, pick the closest passages and answer from
 * THOSE ONLY, returning the passages we used. Nothing is stored: this is a
 * one-shot pipeline, not an index, which is exactly what an agent wants when
 * it needs one answer from one page and does not want to run a RAG stack.
 *
 * THE HONESTY PROPERTY THAT MAKES IT WORTH BUYING. The answer is generated
 * from retrieved passages that are RETURNED WITH IT, so the caller can check
 * the answer against its sources instead of trusting it. When retrieval finds
 * nothing relevant the product says so rather than letting the model
 * improvise - an unsupported answer is worse than no answer, because the
 * caller cannot tell the difference without the citations.
 *
 * It reuses the same SSRF-guarded fetch as fetch_extract: the URL is
 * attacker-supplied, so hostnames are resolved and private space refused.
 */

const { ProductError, ProductUnavailable } = require('./errors');
const { resolveSafely, parseTarget, htmlToText, extractTitle, readCapped } = require('./web');

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

/**
 * Split text into overlapping chunks on paragraph boundaries where possible.
 * Overlap matters: a fact that straddles a boundary is otherwise retrievable
 * by neither chunk.
 */
function chunkText(text, size, overlap) {
  const out = [];
  const paras = String(text).split(/\n\s*\n/).map((p) => p.trim()).filter(Boolean);
  let cur = '';
  for (const p of paras) {
    if (cur && (cur.length + p.length + 2) > size) {
      out.push(cur);
      // carry the tail of the previous chunk so boundary-straddling facts
      // remain findable
      cur = overlap > 0 ? cur.slice(-overlap) + '\n\n' + p : p;
    } else {
      cur = cur ? cur + '\n\n' + p : p;
    }
    // A single paragraph longer than the chunk size is hard-split.
    while (cur.length > size) {
      out.push(cur.slice(0, size));
      cur = cur.slice(size - overlap);
    }
  }
  if (cur.trim()) out.push(cur);
  return out;
}

function cosine(a, b) {
  let dot = 0;
  let na = 0;
  let nb = 0;
  for (let i = 0; i < a.length; i++) { dot += a[i] * b[i]; na += a[i] * a[i]; nb += b[i] * b[i]; }
  if (na === 0 || nb === 0) return 0;
  return dot / (Math.sqrt(na) * Math.sqrt(nb));
}

/**
 * Hard limits of the embedding service, which rejects anything past them with a
 * 400. They are duplicated here on purpose: a request that exceeds them cannot
 * succeed, so splitting it is the only correct behaviour, and discovering that
 * at runtime would mean charging a caller for a call we already know will fail.
 */
const EMBED_MAX_BATCH = 64;
const EMBED_MAX_CHARS = 8000;

/**
 * Embed any number of texts by splitting into service-sized batches.
 *
 * The single-shot version of this silently worked in testing and then 400'd on
 * every real page: a 1200-char chunker turns anything past ~75KB of text into
 * more than 64 chunks, and the whole request was rejected. Batches are issued
 * sequentially rather than in parallel because the embedder is one small
 * community-run box, and a burst of 4 concurrent batches is how it starts
 * timing out instead of merely being slow.
 */
async function embedBatched(texts, { cfg, fetchImpl }) {
  const out = [];
  for (let i = 0; i < texts.length; i += EMBED_MAX_BATCH) {
    const batch = texts.slice(i, i + EMBED_MAX_BATCH).map((t) => String(t).slice(0, EMBED_MAX_CHARS));
    const r = await fetchImpl(`${cfg.embedUrl}/embed`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ texts: batch }),
      signal: AbortSignal.timeout(Number(cfg.embedTimeoutMs)),
    });
    if (!r.ok) throw Object.assign(new Error(`embedding service HTTP ${r.status}`), { retryable: r.status >= 500 });
    const j = await r.json();
    const v = j && (j.vectors || j.embeddings);
    if (!Array.isArray(v) || v.length !== batch.length) {
      throw Object.assign(new Error('embedding service returned a mismatched vector count'), { retryable: true });
    }
    out.push(...v);
  }
  return out;
}

function createAskUrlProduct({ cfg, fetchImpl = fetch, now = Date.now }) {
  const embed = (texts) => embedBatched(texts, { cfg, fetchImpl });

  async function complete(messages, maxTokens) {
    const headers = { 'content-type': 'application/json' };
    if (cfg.askUrlApiKey) headers.authorization = `Bearer ${cfg.askUrlApiKey}`;
    const r = await fetchImpl(`${cfg.askUrlInferenceUrl}/chat/completions`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ model: cfg.askUrlModel, messages, max_tokens: maxTokens, temperature: 0 }),
      signal: AbortSignal.timeout(Number(cfg.askUrlTimeoutMs)),
    });
    if (!r.ok) throw Object.assign(new Error(`inference HTTP ${r.status}`), { retryable: r.status >= 500 });
    const j = await r.json();
    const content = j && j.choices && j.choices[0] && j.choices[0].message && j.choices[0].message.content;
    if (typeof content !== 'string') {
      throw Object.assign(new Error('inference returned no message content'), { retryable: true });
    }
    return { content: content.trim(), usage: j.usage || null, model: j.model || cfg.askUrlModel };
  }

  return {
    id: 'ask_url',
    title: 'Ask a question about a web page',
    description:
      `Give a URL and a question; get an answer grounded in that page, WITH the passages the answer was drawn from. We fetch the page, chunk it, embed the chunks and your question, retrieve the closest ${cfg.askUrlTopK} passages and answer from those only. Nothing is stored — it is a one-shot pipeline, not an index. The retrieved passages come back with the answer so you can check it rather than trust it, and when nothing on the page is relevant the product says so instead of letting the model improvise. Reaches the public internet only, with the same SSRF protections as the fetch product.`,
    path: '/x402/web/ask',
    routes: [{ method: 'POST', path: '/x402/web/ask' }],
    priceUsd: cfg.askUrlPriceUsd,
    enabled: cfg.askUrlEnabled,
    // Three downstreams (fetch, embed, model). Produce the answer first; a
    // failure anywhere in the chain must charge nobody.
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 16384,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          url: { type: 'string', required: true, description: 'absolute http(s) URL of a public page' },
          question: { type: 'string', required: true, description: 'what you want to know about that page' },
          top_k: { type: 'integer', required: false, description: `passages to retrieve (default ${cfg.askUrlTopK})` },
        },
      },
      output: {
        type: 'json',
        description: 'answer, grounded (bool), passages[] {text, score, index}, page {url, final_url, title, chars}, model, chunks_total, usage',
      },
    },

    async availability() {
      try {
        const r = await fetchImpl(`${cfg.embedUrl}/healthz`, { signal: AbortSignal.timeout(4000) });
        if (!r.ok) return { available: false, reason: 'embedding_service_unavailable', detail: `HTTP ${r.status}` };
      } catch (e) {
        return { available: false, reason: 'embedding_service_unavailable', detail: e.message };
      }
      return { available: true };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      if (typeof b.url !== 'string' || !b.url.trim()) throw bad('url is required', 'invalid_request');
      if (typeof b.question !== 'string' || !b.question.trim()) throw bad('question is required', 'invalid_request');
      if (b.question.length > 2000) throw bad('question must be under 2000 characters', 'invalid_request');
      const url = parseTarget(b.url);
      let topK = Number(cfg.askUrlTopK);
      if (b.top_k !== undefined && b.top_k !== null) {
        if (!Number.isInteger(b.top_k) || b.top_k < 1 || b.top_k > 20) {
          throw bad('top_k must be an integer between 1 and 20', 'invalid_request');
        }
        topK = b.top_k;
      }
      return { url, question: b.question.trim(), topK };
    },

    async handler(ctx) {
      const { url, question, topK } = ctx.params;

      // 1. Fetch, with the same SSRF rules as fetch_extract.
      await resolveSafely(url.hostname);
      let res;
      try {
        res = await fetchImpl(url.toString(), {
          method: 'GET',
          redirect: 'follow',
          headers: {
            'user-agent': 'AnimicaX402Ask/1.0 (+https://animica.dev/x402)',
            accept: 'text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8',
          },
          signal: AbortSignal.timeout(Number(cfg.fetchTimeoutMs)),
        });
      } catch (e) {
        throw Object.assign(new Error(`could not fetch the page: ${e.message}`), { retryable: true });
      }
      if (!res.ok) throw bad(`the page answered HTTP ${res.status}`, 'upstream_status', { status: res.status });
      // A redirect chain can still land somewhere private.
      try {
        await resolveSafely(new URL(res.url || url.toString()).hostname);
      } catch (e) {
        throw bad(`the page redirected somewhere we will not fetch: ${e.message}`, 'blocked_address');
      }

      const { buffer } = await readCapped(res, Number(cfg.fetchMaxBytes));
      const raw = buffer.toString('utf8');
      const contentType = String(res.headers.get('content-type') || '').toLowerCase();
      const isHtml = contentType.includes('html') || /^\s*<(!doctype|html)/i.test(raw.slice(0, 200));
      const text = isHtml ? htmlToText(raw) : raw.trim();
      if (text.length < 40) {
        throw bad('that page had almost no extractable text to answer from', 'no_content', { chars: text.length });
      }

      // 2. Chunk and embed. The question is embedded in the SAME batch so it
      //    shares the model and normalisation exactly.
      const chunks = chunkText(text, Number(cfg.askUrlChunkChars), Number(cfg.askUrlChunkOverlap))
        .slice(0, Number(cfg.askUrlMaxChunks));
      const vectors = await embed([question, ...chunks]);
      const qv = vectors[0];
      const scored = chunks.map((c, i) => ({ index: i, text: c, score: cosine(qv, vectors[i + 1]) }));
      scored.sort((a, b) => b.score - a.score);
      const top = scored.slice(0, topK);

      // 3. Grounding gate. If the best passage is weak, say so rather than
      //    asking the model to invent something plausible.
      const best = top.length ? top[0].score : 0;
      const grounded = best >= Number(cfg.askUrlMinScore);
      if (!grounded) {
        return {
          status: 200,
          bodyObj: {
            product: 'ask_url',
            question,
            answer: null,
            grounded: false,
            reason: 'no_relevant_passage',
            detail:
              `Nothing on that page was close enough to the question (best similarity ${best.toFixed(3)}, threshold ${cfg.askUrlMinScore}). Answering anyway would mean inventing something, so the product declines instead.`,
            best_score: best,
            passages: top.map((t) => ({ index: t.index, score: t.score, text: t.text.slice(0, 500) })),
            page: { url: url.toString(), final_url: res.url || url.toString(), title: isHtml ? extractTitle(raw) : null, chars: text.length },
            chunks_total: chunks.length,
          },
        };
      }

      // 4. Answer from the retrieved passages ONLY.
      const context = top.map((t, i) => `[${i + 1}] ${t.text}`).join('\n\n');
      const messages = [
        {
          role: 'system',
          content:
            'You answer strictly from the numbered passages provided. Cite the passages you used as [1], [2] etc. If the passages do not contain the answer, say exactly that you cannot answer from this page. Never use outside knowledge and never guess.',
        },
        { role: 'user', content: `Passages from ${url.toString()}:\n\n${context}\n\nQuestion: ${question}` },
      ];
      const completion = await complete(messages, Number(cfg.askUrlMaxTokens));

      return {
        status: 200,
        bodyObj: {
          product: 'ask_url',
          question,
          answer: completion.content,
          grounded: true,
          best_score: best,
          // Returned so the caller can CHECK the answer instead of trusting
          // it — the whole point of grounding.
          passages: top.map((t, i) => ({ citation: i + 1, index: t.index, score: t.score, text: t.text })),
          page: {
            url: url.toString(),
            final_url: res.url || url.toString(),
            title: isHtml ? extractTitle(raw) : null,
            chars: text.length,
          },
          chunks_total: chunks.length,
          retrieved: top.length,
          model: completion.model,
          usage: completion.usage,
          answered_at: new Date(now()).toISOString(),
          note:
            'The answer is generated only from the passages returned above. Check it against them — that is why they are included. Retrieval is embedding similarity over one page, so a fact stated only in an image, a script or a linked page will not be found.',
        },
      };
    },
  };
}

module.exports = { createAskUrlProduct, chunkText, cosine, embedBatched, EMBED_MAX_BATCH, EMBED_MAX_CHARS };
