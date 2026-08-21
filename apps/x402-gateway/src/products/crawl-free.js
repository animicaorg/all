'use strict';
/**
 * ANIMICA CRAWL — a genuinely free web-reading tool.
 *
 * Replaces the "agent swarm" on animica.dev, which spawned a background build
 * per request, burned a miner, and (measured) had not returned a result after
 * 35 seconds. This does one useful thing quickly instead: fetch a page, read
 * it, and answer a question about it WITH the passages the answer came from.
 *
 * WHY IT IS FREE. It costs us a page fetch and one short model call. Making it
 * free is the point: it is the honest demonstration of the paid
 * fetch_extract / ask_url products, and a visitor who finds it useful has
 * already learned what the paid API does.
 *
 * WHAT IT REUSES, AND WHY THAT MATTERS. The fetch path is the SSRF-hardened
 * one from web.js — hostnames resolved, every redirect hop re-checked against
 * private/loopback/link-local/CGNAT ranges. This endpoint takes a URL from an
 * anonymous stranger with no payment attached, so it is the single most
 * exposed surface on this gateway; reimplementing the fetch here would mean
 * reimplementing those protections, badly.
 *
 * HONESTY: when the page does not contain the answer it says so rather than
 * letting the model improvise, and the passages ride along so the reader can
 * check. That is the same rule the paid ask_url product follows.
 */

const { resolveSafely, parseTarget, htmlToText, extractTitle, extractMeta, readCapped } = require('./web');
const { chunkText, cosine, embedBatched, EMBED_MAX_BATCH } = require('./ask-url');
const { utcDay, clientKey } = require('./trial');

function jsonOut(status, bodyObj) {
  return { status, bodyObj };
}

function createFreeCrawlRoute({ cfg, gatewayStore, fetchImpl = fetch, now = Date.now }) {
  const CAP_PRODUCT = 'free_crawl';

  // Shares the paid product's batching so the free tier cannot 400 on a long
  // page while the paid one succeeds — they read the same pages.
  const embed = (texts) => embedBatched(texts, { cfg, fetchImpl });

  async function answer(question, passages, url) {
    const headers = { 'content-type': 'application/json' };
    if (cfg.askUrlApiKey) headers.authorization = `Bearer ${cfg.askUrlApiKey}`;
    const context = passages.map((p, i) => `[${i + 1}] ${p.text}`).join('\n\n');
    const r = await fetchImpl(`${cfg.askUrlInferenceUrl}/chat/completions`, {
      method: 'POST',
      headers,
      body: JSON.stringify({
        model: cfg.askUrlModel,
        max_tokens: Number(cfg.freeCrawlMaxTokens),
        temperature: 0,
        messages: [
          {
            role: 'system',
            content:
              'Answer strictly from the numbered passages. Cite them as [1], [2]. '
              + 'If the passages do not contain the answer, say exactly that you cannot answer from this page. '
              + 'Never use outside knowledge and never guess.',
          },
          { role: 'user', content: `Passages from ${url}:\n\n${context}\n\nQuestion: ${question}` },
        ],
      }),
      signal: AbortSignal.timeout(Number(cfg.askUrlTimeoutMs)),
    });
    if (!r.ok) throw new Error(`model HTTP ${r.status}`);
    const j = await r.json();
    const c = j && j.choices && j.choices[0] && j.choices[0].message && j.choices[0].message.content;
    if (typeof c !== 'string') throw new Error('model returned no content');
    return { text: c.trim(), model: j.model || cfg.askUrlModel };
  }

  return {
    method: 'POST',
    path: '/x402/crawl',
    description:
      'FREE (capped per client per day): fetch a public web page and either extract it as clean text or answer a question about it, returning the passages the answer came from. Reaches the public internet only — hostnames are resolved and every redirect hop re-checked against private, loopback, link-local and CGNAT ranges.',
    bodyFields: {
      url: { type: 'string', required: true, description: 'the public page to fetch; http/https only, and every redirect hop is re-checked against private ranges' },
      question: { type: 'string', required: false, description: 'ask about the page instead of extracting it — the answer comes back with the passages it came from' },
    },
    match(pathname) {
      return (pathname === '/x402/crawl' || pathname === '/crawl') ? {} : null;
    },
    async handler(ctx) {
      if (!cfg.freeCrawlEnabled) {
        return jsonOut(503, { error: 'free_crawl_disabled' });
      }
      const body = ctx.json || {};
      const rawUrl = body.url;
      if (typeof rawUrl !== 'string' || !rawUrl.trim()) {
        return jsonOut(400, {
          error: 'invalid_request',
          detail: 'send {"url":"https://…","question":"optional question about the page"}',
        });
      }
      let u;
      try {
        u = parseTarget(rawUrl);
      } catch (e) {
        return jsonOut(400, { error: 'invalid_url', detail: e.message });
      }
      const question = typeof body.question === 'string' ? body.question.trim().slice(0, 500) : '';

      // Daily cap, per client. Deliberately WEAK (keyed on IP, which can be
      // rotated): its job is to stop casual over-use of a free tool, not to be
      // an authorization boundary.
      // clientKey takes the whole ctx (headers + remoteAddress), and the
      // counter is the same consumeTrial the paid products' free trials use —
      // one quota implementation, not two that drift.
      const client = clientKey(ctx);
      const quota = gatewayStore.consumeTrial(
        CAP_PRODUCT, client, utcDay(now()), Number(cfg.freeCrawlPerDay));
      if (!quota.allowed) {
        return jsonOut(429, {
          error: 'daily_limit_reached',
          detail: `the free crawler allows ${cfg.freeCrawlPerDay} pages per client per day. It resets at UTC midnight.`,
          paid_alternative: {
            extract: 'POST /x402/web/fetch',
            ask: 'POST /x402/web/ask',
            catalog: '/.well-known/x402',
            note: 'the paid endpoints have no daily cap and return the same shape',
          },
        });
      }

      // ---- fetch (SSRF-hardened, shared with the paid product) ----------
      let res;
      try {
        await resolveSafely(u.hostname);
        res = await fetchImpl(u.toString(), {
          method: 'GET',
          redirect: 'follow',
          headers: {
            'user-agent': 'AnimicaCrawl/1.0 (+https://animica.dev/crawl)',
            accept: 'text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8',
          },
          signal: AbortSignal.timeout(Number(cfg.fetchTimeoutMs)),
        });
        // A public URL can still redirect into private space.
        await resolveSafely(new URL(res.url || u.toString()).hostname);
      } catch (e) {
        return jsonOut(400, { error: 'fetch_failed', detail: e.message, url: u.toString() });
      }
      if (!res.ok) {
        return jsonOut(200, {
          product: 'free_crawl',
          url: u.toString(),
          ok: false,
          status: res.status,
          detail: `the page answered HTTP ${res.status}`,
        });
      }

      const { buffer, bytes, truncated } = await readCapped(res, Number(cfg.fetchMaxBytes));
      const raw = buffer.toString('utf8');
      const isHtml = String(res.headers.get('content-type') || '').includes('html')
        || /^\s*<(!doctype|html)/i.test(raw.slice(0, 200));
      const text = isHtml ? htmlToText(raw) : raw.trim();

      const page = {
        url: u.toString(),
        final_url: res.url || u.toString(),
        title: isHtml ? extractTitle(raw) : null,
        description: isHtml ? (extractMeta(raw, 'description') || extractMeta(raw, 'og:description')) : null,
        chars: text.length,
        bytes,
        body_truncated: truncated,
      };

      // ---- extraction only ----------------------------------------------
      if (!question) {
        return jsonOut(200, {
          product: 'free_crawl',
          ok: true,
          mode: 'extract',
          page,
          text: text.slice(0, Number(cfg.freeCrawlMaxChars)),
          text_truncated: text.length > Number(cfg.freeCrawlMaxChars),
          quota: { remaining_today: quota.remaining, per_day: Number(cfg.freeCrawlPerDay) },
          free: true,
        });
      }

      // ---- question answering, grounded in the page ----------------------
      if (text.length < 40) {
        return jsonOut(200, {
          product: 'free_crawl', ok: true, mode: 'ask', page,
          grounded: false, answer: null,
          reason: 'that page had almost no extractable text to answer from',
          quota: { remaining_today: quota.remaining, per_day: Number(cfg.freeCrawlPerDay) },
          free: true,
        });
      }

      let top = [];
      let best = 0;
      try {
        const chunks = chunkText(text, Number(cfg.askUrlChunkChars), Number(cfg.askUrlChunkOverlap))
          .slice(0, Number(cfg.freeCrawlMaxChunks));
        const vecs = await embed([question, ...chunks]);
        const qv = vecs[0];
        const scored = chunks.map((c, i) => ({ index: i, text: c, score: cosine(qv, vecs[i + 1]) }));
        scored.sort((a, b) => b.score - a.score);
        top = scored.slice(0, Number(cfg.freeCrawlTopK));
        best = top.length ? top[0].score : 0;
      } catch (e) {
        return jsonOut(200, {
          product: 'free_crawl', ok: false, mode: 'ask', page,
          detail: `could not index the page right now: ${e.message}`,
          quota: { remaining_today: quota.remaining, per_day: Number(cfg.freeCrawlPerDay) },
          free: true,
        });
      }

      // Decline rather than improvise. An unsupported answer is worse than no
      // answer, because the reader cannot tell the difference.
      if (best < Number(cfg.askUrlMinScore)) {
        return jsonOut(200, {
          product: 'free_crawl', ok: true, mode: 'ask', page, question,
          grounded: false,
          answer: null,
          reason: `nothing on that page was close enough to the question (best similarity ${best.toFixed(3)}). Answering anyway would mean inventing something.`,
          passages: top.map((t) => ({ score: Math.round(t.score * 1000) / 1000, text: t.text.slice(0, 300) })),
          quota: { remaining_today: quota.remaining, per_day: Number(cfg.freeCrawlPerDay) },
          free: true,
        });
      }

      let out;
      try {
        out = await answer(question, top, page.final_url);
      } catch (e) {
        return jsonOut(200, {
          product: 'free_crawl', ok: false, mode: 'ask', page, question,
          detail: `the free model could not answer right now: ${e.message}`,
          passages: top.map((t) => ({ score: Math.round(t.score * 1000) / 1000, text: t.text.slice(0, 300) })),
          quota: { remaining_today: quota.remaining, per_day: Number(cfg.freeCrawlPerDay) },
          free: true,
        });
      }

      return jsonOut(200, {
        product: 'free_crawl',
        ok: true,
        mode: 'ask',
        page,
        question,
        grounded: true,
        answer: out.text,
        model: out.model,
        best_score: Math.round(best * 1000) / 1000,
        passages: top.map((t, i) => ({
          citation: i + 1,
          score: Math.round(t.score * 1000) / 1000,
          text: t.text,
        })),
        quota: { remaining_today: quota.remaining, per_day: Number(cfg.freeCrawlPerDay) },
        free: true,
        note: 'The answer is generated only from the passages shown. Check it against them — that is why they are included.',
      });
    },
  };
}

module.exports = { createFreeCrawlRoute };
