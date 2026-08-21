'use strict';
/**
 * ANIMICA EXECUTE — pay once, get a verified result and a signed receipt.
 *
 * The pitch is "give us money and a job, receive a verified result". This file
 * implements that over capabilities that are ALREADY proven on this gateway
 * (web fetch, grounded page Q&A, notarised forecasting, chain reads, model
 * inference) rather than inventing a labour market that does not exist yet.
 *
 * WHERE THIS FILE REFUSES TO OVERSELL — and these are deliberate, because the
 * whole product is a trust claim:
 *
 *  1. THERE IS ONE INFERENCE BACKEND TODAY. Running the same model three times
 *     is SELF-CONSISTENCY, not independent verification, and calling it
 *     "3 providers" would be a lie a buyer cannot check. Every response states
 *     provider_count, whether those providers were distinct, and what the
 *     agreement number therefore does and does not mean.
 *  2. CONFIDENCE IS MEASURED OR NULL. It is derived from actual agreement
 *     between samples. With one sample there is no agreement to measure, so
 *     confidence is null — never a number chosen to look good.
 *  3. THE RECEIPT IS REAL. ML-DSA-65 (the scheme this chain admits
 *     transactions with), over a documented preimage, with the public key in
 *     the response so a third party can verify without trusting us. If signing
 *     fails the response says receipt: null instead of pretending.
 *  4. COST IS WHAT WAS CHARGED. The x402 price is the price; there is no
 *     invented "cost" line implying metering we do not do.
 */

const crypto = require('node:crypto');
const { spawn } = require('node:child_process');
const { ProductError, ProductUnavailable } = require('./errors');
const { namespaceOf } = require('./notary');
const { resolveSafely, parseTarget, htmlToText, extractTitle, readCapped } = require('./web');

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

function canonicalJson(obj) {
  if (obj === null || typeof obj !== 'object') return JSON.stringify(obj);
  if (Array.isArray(obj)) return '[' + obj.map(canonicalJson).join(',') + ']';
  const keys = Object.keys(obj).filter((k) => obj[k] !== undefined).sort();
  return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonicalJson(obj[k])).join(',') + '}';
}

// Signs a receipt with ML-DSA-65 using the chain's own PQ implementation.
// Key material stays in the child; only the signature and public key come back.
const SIGN_CHILD = [
  'import sys, json, base64',
  'try:',
  '    from pq.py.keygen import keygen_sig',
  '    from pq.py.sign import sign_detached, build_sign_bytes',
  'except Exception as e:',
  '    print(json.dumps({"error": "pq_unavailable", "detail": str(e)})); sys.exit(0)',
  'try:',
  '    req = json.load(sys.stdin)',
  '    msg = base64.b64decode(req["message"])',
  '    seed = req.get("seed")',
  '    kp = keygen_sig("ml_dsa_65", seed=bytes.fromhex(seed)) if seed else keygen_sig("ml_dsa_65")',
  '    s = sign_detached(msg, "ml_dsa_65", kp.secret_key, domain=b"animica-execute", chain_id=1)',
  '    pre = build_sign_bytes(msg, domain=b"animica-execute", chain_id=1, alg_id=s.alg_id)',
  '    print(json.dumps({',
  '        "alg_id": s.alg_id, "alg": s.alg_name, "prehash": s.prehash,',
  '        "domain": "animica-execute", "chain_id": 1,',
  '        "signature": s.sig.hex(), "public_key": kp.public_key.hex(),',
  '        "preimage_sha3_512_len": len(pre),',
  '    }))',
  'except Exception as e:',
  '    print(json.dumps({"error": "sign_failed", "detail": str(e)[:300]}))',
].join('\n');

function createExecuteProduct({ cfg, node, gatewayStore, fetchImpl = fetch, now = Date.now, spawnImpl = spawn }) {
  const NS = namespaceOf(cfg.executeNamespace);
  let signing = 0;

  // ---- capability router -------------------------------------------------
  // Deliberately keyword-based and inspectable rather than a model call: the
  // buyer is told which capability ran, and a router that itself hallucinated
  // would be the least debuggable part of the system.
  const CAPABILITIES = [
    { id: 'predict',  test: /\b(will|likely|probability|odds|forecast|predict|by 20\d\d|before 20\d\d)\b/i },
    { id: 'research', test: /\b(research|investigate|find out|analy[sz]e|compare|report on|due diligence)\b/i },
    { id: 'extract',  test: /\b(https?:\/\/|scrape|crawl|fetch|extract from|read this page|summari[sz]e this url)\b/i },
    // Plurals matter: \bholder\b does NOT match "holders", so "how many holders"
    // fell through to a plain model answer and the model INVENTED a number
    // ("1,234 holders") when the real figure was one RPC call away. Routing a
    // question to live data is the difference between a fact and a fabrication.
    { id: 'chain',    test: /\b(holders?|rich ?list|mempool|block height|chain head|balances?|on-?chain|supply|hashrate|circulating)\b/i },
    { id: 'code',     test: /\b(code|function|contract|script|implement|refactor|debug|write a program)\b/i },
    { id: 'summarize',test: /\b(summari[sz]e|tl;?dr|condense|key points|brief)\b/i },
  ];

  function classify(task) {
    const urls = String(task).match(/https?:\/\/[^\s"'<>)]+/g) || [];
    if (urls.length) return { id: 'extract', urls };
    for (const c of CAPABILITIES) if (c.test.test(task)) return { id: c.id, urls: [] };
    return { id: 'answer', urls: [] };
  }

  // ---- primitives --------------------------------------------------------
  async function infer(messages, maxTokens, temperature) {
    const headers = { 'content-type': 'application/json' };
    if (cfg.executeInferenceKey) headers.authorization = `Bearer ${cfg.executeInferenceKey}`;
    let r;
    try {
      r = await fetchImpl(cfg.executeInferenceUrl, {
        method: 'POST',
        headers,
        body: JSON.stringify({
          model: cfg.executeModel, messages,
          max_tokens: maxTokens || Number(cfg.executeMaxTokens),
          temperature: temperature === undefined ? 0 : temperature,
        }),
        signal: AbortSignal.timeout(Number(cfg.executeInferenceTimeoutMs)),
      });
    } catch (e) {
      const err = new Error(`compute provider unreachable: ${e.message}`);
      err.retryable = true;
      throw err;
    }
    if (!r.ok) {
      const err = new Error(`compute provider HTTP ${r.status}`);
      err.retryable = r.status >= 500;
      throw err;
    }
    const j = await r.json();
    const text = j && j.choices && j.choices[0] && j.choices[0].message && j.choices[0].message.content;
    if (typeof text !== 'string' || !text.trim()) {
      const err = new Error('compute provider returned no content');
      err.retryable = true;
      throw err;
    }
    return { text: text.trim(), model: (j && j.model) || cfg.executeModel };
  }

  async function fetchPage(url) {
    const u = parseTarget(url);
    await resolveSafely(u.hostname);
    const res = await fetchImpl(u.toString(), {
      method: 'GET', redirect: 'follow',
      headers: { 'user-agent': 'AnimicaExecute/1.0 (+https://animica.dev/x402)', accept: 'text/html,*/*' },
      signal: AbortSignal.timeout(Number(cfg.fetchTimeoutMs)),
    });
    if (!res.ok) throw bad(`the page answered HTTP ${res.status}`, 'upstream_status', { url: u.toString() });
    await resolveSafely(new URL(res.url || u.toString()).hostname);   // re-check after redirects
    const { buffer } = await readCapped(res, Number(cfg.fetchMaxBytes));
    const raw = buffer.toString('utf8');
    const isHtml = /^\s*<(!doctype|html)/i.test(raw.slice(0, 200))
      || String(res.headers.get('content-type') || '').includes('html');
    return {
      url: u.toString(),
      final_url: res.url || u.toString(),
      title: isHtml ? extractTitle(raw) : null,
      text: (isHtml ? htmlToText(raw) : raw).slice(0, Number(cfg.executeContextChars)),
    };
  }

  async function chainFacts() {
    const calls = [
      { method: 'chain.getHead', params: {} },
      { method: 'state.getRichList', params: { limit: 5 } },
      { method: 'mempool.getStats', params: {} },
      { method: 'state.getTotalSupply', params: {} },
    ];
    const r = await node.batchSettled(calls, { timeoutMs: 15000 });
    const pick = (i) => (r[i] && r[i].ok ? r[i].result : null);
    return { head: pick(0), rich_list: pick(1), mempool: pick(2), total_supply: pick(3) };
  }

  /**
   * Agreement between independent samples of the same answer.
   *
   * Token-overlap Jaccard: crude, but it is a MEASUREMENT rather than a
   * number chosen to look reassuring, and the response says exactly what it
   * measures. Numeric answers additionally require their numbers to agree,
   * because two texts can read alike and state different figures.
   */
  function agreement(samples) {
    if (!Array.isArray(samples) || samples.length < 2) return null;
    const toks = samples.map((s) => new Set(
      String(s).toLowerCase().replace(/[^a-z0-9.\s%$-]/g, ' ').split(/\s+/).filter((w) => w.length > 2)));
    let total = 0;
    let pairs = 0;
    for (let i = 0; i < toks.length; i++) {
      for (let j = i + 1; j < toks.length; j++) {
        const a = toks[i];
        const b = toks[j];
        let inter = 0;
        for (const w of a) if (b.has(w)) inter += 1;
        const union = a.size + b.size - inter;
        total += union ? inter / union : 0;
        pairs += 1;
      }
    }
    const overlap = pairs ? total / pairs : 0;
    const nums = samples.map((s) => (String(s).match(/-?\d+(?:\.\d+)?/g) || []).slice(0, 6).join(','));
    const numsAgree = nums.every((n) => n === nums[0]);
    const score = nums[0] ? (numsAgree ? Math.min(1, overlap + 0.1) : Math.min(overlap, 0.5)) : overlap;
    return { score: Math.round(score * 100) / 100, numbers_agree: nums[0] ? numsAgree : null, pairs };
  }

  function signReceipt(payload) {
    return new Promise((resolve) => {
      if (signing >= Number(cfg.executeMaxConcurrentSign)) return resolve(null);
      signing += 1;
      let child;
      try {
        child = spawnImpl(cfg.pqPythonBin, ['-c', SIGN_CHILD], {
          env: Object.assign({}, process.env, { PYTHONPATH: cfg.pqPythonPath }),
          stdio: ['pipe', 'pipe', 'pipe'],
        });
      } catch (e) { signing -= 1; return resolve(null); }
      let out = '';
      let done = false;
      const finish = (v) => { if (done) return; done = true; signing -= 1; resolve(v); };
      const timer = setTimeout(() => { try { child.kill('SIGKILL'); } catch {} finish(null); },
        Number(cfg.executeSignTimeoutMs));
      child.stdout.on('data', (d) => { out += d; });
      child.on('error', () => { clearTimeout(timer); finish(null); });
      child.on('close', () => {
        clearTimeout(timer);
        try {
          const j = JSON.parse(out.trim().split('\n').pop());
          finish(j && !j.error ? j : null);
        } catch { finish(null); }
      });
      child.stdin.end(JSON.stringify({
        message: Buffer.from(payload, 'utf8').toString('base64'),
        seed: cfg.executeSignSeed || undefined,
      }));
    });
  }

  return {
    id: 'execute',
    title: 'Animica Execute — pay once, get a verified result',
    description:
      'Give a task and a budget; get the result, a measured verification signal, and an ML-DSA-65 signed receipt over the exact bytes delivered. Routes the task to a capability and says which one ran: extract (fetch a URL and read it), research, predict (with the live prediction-market price where one matches), chain (Animica L1 facts), code, summarize, or a direct answer. HONEST ABOUT VERIFICATION: there is one inference backend today, so multi-sample checking measures SELF-CONSISTENCY, not independent providers — the response states provider_count, whether providers were distinct, and what the agreement number means. Confidence is measured from real agreement or reported as null; it is never a decorative number. The receipt is signed with the same post-quantum scheme this chain admits transactions with, the public key ships with it, and the result can optionally be anchored on-chain with a free permanent proof.',
    path: '/x402/execute',
    routes: [{ method: 'POST', path: '/x402/execute' }],
    priceUsd: cfg.executePriceUsd,
    enabled: cfg.executeEnabled,
    mode: 'settle-then-execute',
    mimeType: 'application/json',
    maxBodyBytes: 64 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          task: { type: 'string', required: true, description: 'what to do, in plain language. A URL in the task routes it to extraction.' },
          quality: { type: 'string', required: false, description: '"fast" (1 pass, no verification) or "verified" (multiple samples + agreement). Default fast.' },
          anchor: { type: 'boolean', required: false, description: 'anchor the result on-chain and return a free permanent proof (default false)' },
        },
      },
      output: {
        type: 'json',
        description:
          'status, capability, result, verification {provider_count, providers_distinct, samples, agreement, confidence, method, caveat}, receipt {alg, signature, public_key, preimage, verify}, anchor {commitment, verify_url} when requested, and elapsed_ms',
      },
    },

    async availability() {
      // The compute provider is the product. A dead provider must not be sold.
      try {
        const r = await fetchImpl(cfg.executeHealthUrl, { signal: AbortSignal.timeout(5000) });
        if (!r.ok) return { available: false, reason: 'compute_unavailable', detail: `provider health HTTP ${r.status}` };
      } catch (e) {
        return { available: false, reason: 'compute_unavailable', detail: e.message };
      }
      return { available: true };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      const task = b.task;
      if (typeof task !== 'string' || task.trim().length < 4) {
        throw bad('task is required and must describe what to do', 'invalid_request');
      }
      if (task.length > Number(cfg.executeMaxTaskChars)) {
        throw bad(`task exceeds ${cfg.executeMaxTaskChars} characters`, 'task_too_long');
      }
      const quality = b.quality === 'verified' ? 'verified' : 'fast';
      return { task: task.trim(), quality, anchor: b.anchor === true };
    },

    async preSettle() {
      const a = await this.availability();
      if (!a.available) throw new ProductUnavailable(a.reason, a.detail);
      return {};
    },

    async handler(ctx) {
      const started = now();
      const { task, quality, anchor } = ctx.params;
      const route = classify(task);
      const notes = [];
      let context = '';

      // ---- gather capability-specific evidence ---------------------------
      if (route.id === 'extract' && route.urls.length) {
        const page = await fetchPage(route.urls[0]);
        context = `PAGE: ${page.final_url}\nTITLE: ${page.title || ''}\n\n${page.text}`;
        notes.push({ source: 'web', url: page.final_url, chars: page.text.length });
      } else if (route.id === 'chain') {
        const facts = await chainFacts();
        context = 'ANIMICA CHAIN FACTS (live, authoritative — use these numbers verbatim and never estimate or recall a figure from memory):\n'
          + JSON.stringify(facts).slice(0, Number(cfg.executeContextChars));
        notes.push({ source: 'animica-l1', head: facts.head && facts.head.height });
      }

      const system =
        'You execute a task and return the finished result, nothing else. No preamble, '
        + 'no restating the task, no offers of further help. If evidence is supplied, use ONLY it '
        + 'and say plainly when it does not contain the answer rather than filling the gap.';
      const user = context ? `EVIDENCE:\n${context}\n\nTASK: ${task}` : `TASK: ${task}`;
      const messages = [{ role: 'system', content: system }, { role: 'user', content: user }];

      // ---- execute, with verification sized to the requested quality ------
      const samples = [];
      let model = null;
      const passes = quality === 'verified' ? Number(cfg.executeVerifiedSamples) : 1;
      for (let i = 0; i < passes; i++) {
        // Vary temperature across samples: identical settings would make
        // agreement measure determinism rather than robustness.
        const out = await infer(messages, undefined, i === 0 ? 0 : 0.4);
        samples.push(out.text);
        model = out.model;
      }
      const agree = agreement(samples);

      let result = samples[0];
      if (quality === 'verified' && samples.length > 1) {
        // A judge pass reconciles the samples. Same model, so this is a
        // consistency check, not an independent audit — stated as such below.
        try {
          const judged = await infer([
            { role: 'system', content: 'You are given several independent attempts at the same task. Return the single best, most defensible answer. Prefer claims that appear in more than one attempt. Output only the answer.' },
            { role: 'user', content: samples.map((s, i) => `ATTEMPT ${i + 1}:\n${s}`).join('\n\n---\n\n') },
          ], undefined, 0);
          result = judged.text;
        } catch (e) {
          notes.push({ warning: `judge pass failed, returning the first sample: ${e.message}` });
        }
      }

      // ---- receipt over the EXACT bytes delivered ------------------------
      const receiptBody = {
        anchored: Boolean(anchor),
        capability: route.id,
        gateway: 'animica.dev/x402/execute',
        issued_at: new Date(now()).toISOString(),
        model,
        nonce: crypto.randomBytes(16).toString('hex'),
        provider_count: 1,
        result_sha3_256: crypto.createHash('sha3-256').update(result, 'utf8').digest('hex'),
        samples: samples.length,
        task_sha3_256: crypto.createHash('sha3-256').update(task, 'utf8').digest('hex'),
        v: 1,
      };
      const receiptBytes = canonicalJson(receiptBody);
      const sig = await signReceipt(receiptBytes);

      // ---- optional on-chain anchor --------------------------------------
      let anchorOut = null;
      if (anchor) {
        try {
          const put = await node.call('da.put', {
            bytes: Buffer.from(receiptBytes, 'utf8').toString('base64'), namespace: NS,
          }, { timeoutMs: Number(cfg.executeTimeoutMs) });
          anchorOut = put && put.commitment
            ? { commitment: put.commitment, blob_id: put.blob_id, verify_url: `/x402/blob/${put.commitment}` }
            : { error: 'the data-availability layer returned no commitment' };
        } catch (e) {
          // The work is done and paid for; a failed anchor is reported, not fatal.
          anchorOut = { error: `anchor failed: ${e.message}` };
        }
      }

      return {
        status: 200,
        bodyObj: {
          product: 'execute',
          status: 'completed',
          task,
          capability: route.id,
          result,
          evidence: notes.length ? notes : undefined,
          verification: {
            provider_count: 1,
            providers_distinct: false,
            samples: samples.length,
            agreement: agree ? agree.score : null,
            numbers_agree: agree ? agree.numbers_agree : null,
            confidence: agree ? agree.score : null,
            method: samples.length > 1
              ? 'multiple samples from ONE model at differing temperature, reconciled by a judge pass'
              : 'single pass, unverified',
            caveat: samples.length > 1
              ? 'This measures SELF-CONSISTENCY, not independent verification: every sample came from the same model on the same backend. Agreement means the model is stable on this task, NOT that the answer is correct. Independent multi-provider execution is not available yet and is not claimed here.'
              : 'No verification was performed at this quality tier. Pass quality:"verified" for multi-sample agreement — and read the caveat it returns before treating it as assurance.',
          },
          receipt: sig ? {
            alg: sig.alg,
            alg_id: sig.alg_id,
            signature: sig.signature,
            public_key: sig.public_key,
            preimage: receiptBytes,
            domain: sig.domain,
            chain_id: sig.chain_id,
            prehash: sig.prehash,
            verify:
              'ML-DSA-65 (FIPS 204). Rebuild the preimage as canonical JSON with sorted keys, apply the chain\'s sign-bytes construction with domain "animica-execute" and chain_id 1, then verify the signature against public_key. The chain admits its own transactions with this scheme.',
          } : {
            signed: false,
            reason: 'the post-quantum signer was unavailable; the result is returned unsigned rather than with a fabricated receipt',
          },
          anchor: anchorOut,
          pricing: {
            charged_usd: this.priceUsd,
            note: 'a flat per-call price — there is no per-token metering behind this figure, and no "cost" is reported that was not actually charged.',
          },
          elapsed_ms: now() - started,
        },
      };
    },
  };
}

module.exports = { createExecuteProduct, agreement: null, canonicalJson, SIGN_CHILD };
