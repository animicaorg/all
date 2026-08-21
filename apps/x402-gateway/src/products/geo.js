'use strict';
/**
 * GEO AUDIT — is this site legible to AI agents at all?
 *
 * WHY THIS EXISTS. We learned it the expensive way on our own infrastructure:
 * every paid endpoint here sat at zero external payers, and the cause was not
 * price. It was that the crawlers and agents which would have found us were
 * being served 429s and 404s. The service was live, priced correctly, and
 * completely invisible. That is the single most valuable thing this product
 * checks, and it is why crawler access carries the heaviest weight below.
 *
 * WHAT IT IS NOT. It does not ask ChatGPT or Perplexity whether they have
 * heard of you. That needs frontier API keys and recurring spend, and a
 * number produced that way drifts daily and cannot be reproduced by the
 * buyer. Everything here is a deterministic, re-runnable measurement of the
 * INPUTS that decide whether an agent can read, quote and cite a site. The
 * response says so in `not_measured` rather than letting a buyer assume a
 * brand-mention score is hiding somewhere in the total.
 *
 * NO INFERENCE. Not one model call. That makes the audit cheap, fast,
 * identical on every run, and immune to whatever the inference tier is doing.
 * A score that moves when the site did not is a broken score.
 *
 * SAFETY. The URL is attacker-supplied by definition, so every request goes
 * through the same SSRF guard as the fetch product: hostnames resolved and
 * checked before connecting, the post-redirect host re-checked, byte caps
 * enforced while streaming, per-request timeouts, a whole-audit wall clock,
 * and bounded concurrency — auditing a site is not a licence to hammer it.
 */

const dns = require('node:dns').promises;
const { resolveSafely, parseTarget, htmlToText, extractTitle, extractMeta, readCapped } = require('./web');
const { ProductError } = require('./errors');

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

/**
 * The agents that actually fetch pages, with the user-agent each one really
 * sends. Probing with a made-up string would measure nothing.
 */
const FETCHING_AGENTS = [
  { id: 'GPTBot', operator: 'OpenAI', purpose: 'training + retrieval', ua: 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; GPTBot/1.2; +https://openai.com/gptbot' },
  { id: 'OAI-SearchBot', operator: 'OpenAI', purpose: 'ChatGPT search index', ua: 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; OAI-SearchBot/1.0; +https://openai.com/searchbot' },
  { id: 'ChatGPT-User', operator: 'OpenAI', purpose: 'live fetch during a user chat', ua: 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ChatGPT-User/1.0; +https://openai.com/bot' },
  { id: 'ClaudeBot', operator: 'Anthropic', purpose: 'training + retrieval', ua: 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; ClaudeBot/1.0; +claudebot@anthropic.com' },
  { id: 'Claude-User', operator: 'Anthropic', purpose: 'live fetch during a user chat', ua: 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; Claude-User/1.0; +Claude-User@anthropic.com' },
  { id: 'PerplexityBot', operator: 'Perplexity', purpose: 'search index', ua: 'Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko); compatible; PerplexityBot/1.0; +https://perplexity.ai/perplexitybot' },
  { id: 'CCBot', operator: 'Common Crawl', purpose: 'the corpus most open models train on', ua: 'CCBot/2.0 (https://commoncrawl.org/faq/)' },
  { id: 'meta-externalagent', operator: 'Meta', purpose: 'training + retrieval', ua: 'meta-externalagent/1.1 (+https://developers.facebook.com/docs/sharing/webmasters/crawler)' },
  { id: 'Amazonbot', operator: 'Amazon', purpose: 'Alexa / retrieval', ua: 'Mozilla/5.0 (compatible; Amazonbot/0.1; +https://developer.amazon.com/support/amazonbot)' },
];

/**
 * Tokens that exist ONLY in robots.txt. There is no crawler behind them:
 * Google fetches as Googlebot and Apple as Applebot, and these tokens gate
 * whether that already-fetched content may be used for AI. Live-probing them
 * would produce a confident, meaningless result, so they are checked in
 * robots.txt and nowhere else.
 */
const ROBOTS_ONLY_AGENTS = [
  { id: 'Google-Extended', operator: 'Google', purpose: 'Gemini / Vertex AI use of already-crawled pages' },
  { id: 'Applebot-Extended', operator: 'Apple', purpose: 'Apple Intelligence use of already-crawled pages' },
];

const AUDIT_UA = 'AnimicaGeoAudit/1.0 (+https://animica.dev/x402/geo/audit)';

// ---------------------------------------------------------------------------
// robots.txt
// ---------------------------------------------------------------------------

/**
 * Parse robots.txt into groups. Consecutive User-agent lines share one rule
 * block — a detail that matters, because `User-agent: GPTBot` followed by
 * `User-agent: CCBot` then `Disallow: /` blocks both, and a parser that keeps
 * only the last agent would report one of them as allowed.
 */
function parseRobots(text) {
  const groups = [];
  let current = null;
  let lastWasAgent = false;
  for (const rawLine of String(text).split(/\r?\n/)) {
    const line = rawLine.replace(/#.*$/, '').trim();
    if (!line) continue;
    const idx = line.indexOf(':');
    if (idx < 0) continue;
    const field = line.slice(0, idx).trim().toLowerCase();
    const value = line.slice(idx + 1).trim();
    if (field === 'user-agent') {
      if (!lastWasAgent || !current) {
        current = { agents: [], rules: [] };
        groups.push(current);
      }
      current.agents.push(value.toLowerCase());
      lastWasAgent = true;
      continue;
    }
    lastWasAgent = false;
    if (!current) continue;
    if (field === 'disallow' || field === 'allow') {
      current.rules.push({ type: field, path: value });
    }
  }
  return groups;
}

/**
 * Robots verdict for one agent against one path, following the specificity
 * rule crawlers actually use: the most specific matching group wins, and
 * within it the longest matching pattern wins, Allow breaking ties.
 */
function robotsVerdict(groups, agentId, path = '/') {
  const token = String(agentId).toLowerCase();
  const exact = groups.filter((g) => g.agents.includes(token));
  const wildcard = groups.filter((g) => g.agents.includes('*'));
  const chosen = exact.length ? exact : wildcard;
  if (!chosen.length) return { allowed: true, matched: 'no matching group', specific: false };

  let best = null;
  for (const g of chosen) {
    for (const r of g.rules) {
      // An empty Disallow means "allow everything" and matches nothing.
      if (r.type === 'disallow' && r.path === '') continue;
      if (!pathMatches(r.path, path)) continue;
      const len = r.path.length;
      if (!best || len > best.len || (len === best.len && r.type === 'allow')) {
        best = { type: r.type, path: r.path, len };
      }
    }
  }
  return {
    allowed: !best || best.type === 'allow',
    matched: best ? `${best.type}: ${best.path}` : 'no matching rule',
    specific: exact.length > 0,
  };
}

/** robots.txt path matching: prefix, with `*` wildcards and a `$` anchor. */
function pathMatches(pattern, path) {
  if (pattern === '') return false;
  const anchored = pattern.endsWith('$');
  const body = anchored ? pattern.slice(0, -1) : pattern;
  const parts = body.split('*').map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  const re = new RegExp('^' + parts.join('.*') + (anchored ? '$' : ''));
  return re.test(path);
}

// ---------------------------------------------------------------------------
// HTML signals
// ---------------------------------------------------------------------------

function extractJsonLd(html) {
  const out = [];
  const re = /<script[^>]+type\s*=\s*["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html)) !== null && out.length < 25) {
    try {
      const parsed = JSON.parse(m[1].trim());
      for (const node of Array.isArray(parsed) ? parsed : [parsed]) {
        if (node && typeof node === 'object') out.push(node);
        if (node && Array.isArray(node['@graph'])) out.push(...node['@graph'].filter((n) => n && typeof n === 'object'));
      }
    } catch {
      out.push({ '@type': '__invalid__' });
    }
  }
  return out;
}

function scriptBytes(html) {
  let total = 0;
  const re = /<script\b[^>]*>([\s\S]*?)<\/script>/gi;
  let m;
  while ((m = re.exec(html)) !== null) total += m[1].length;
  return total;
}

function countTag(html, tag) {
  const re = new RegExp(`<${tag}\\b[^>]*>`, 'gi');
  return (String(html).match(re) || []).length;
}

// ---------------------------------------------------------------------------
// Findings and scoring
// ---------------------------------------------------------------------------

const CATEGORIES = {
  crawler_access:      { weight: 30, label: 'AI crawler access' },
  content_citability:  { weight: 18, label: 'Citable content' },
  llms_txt:            { weight: 15, label: 'llms.txt' },
  crawl_basics:        { weight: 15, label: 'Crawl basics' },
  structured_data:     { weight: 12, label: 'Structured data' },
  machine_endpoints:   { weight: 10, label: 'Machine-readable endpoints' },
};

/**
 * One guarded request, shared by the audit and the fix products.
 *
 * Returns a result object rather than throwing, because for the audit a failed
 * probe IS a finding — a 429 to GPTBot is the answer the buyer paid for, not
 * an error.
 */
function createProbe({ cfg, fetchImpl = fetch, now = Date.now, lookup = dns.lookup }) {
  return async function probe(url, { ua = AUDIT_UA, maxBytes, deadline } = {}) {
    const started = now();
    const remaining = deadline ? deadline - started : Number(cfg.geoAuditTimeoutMs);
    if (remaining <= 0) return { ok: false, error: 'budget_exhausted', status: null, ms: 0 };
    const timeout = Math.min(Number(cfg.geoAuditTimeoutMs), remaining);

    let u;
    try {
      u = parseTarget(url);
    } catch {
      return { ok: false, error: 'invalid_url', status: null, ms: 0 };
    }
    try {
      await resolveSafely(u.hostname, lookup);
    } catch (e) {
      return { ok: false, error: 'blocked_host', detail: e.message, status: null, ms: 0 };
    }

    let res;
    try {
      res = await fetchImpl(u.toString(), {
        method: 'GET',
        redirect: 'follow',
        headers: { 'user-agent': ua, accept: 'text/html,application/json,text/plain,*/*' },
        signal: AbortSignal.timeout(timeout),
      });
    } catch (e) {
      return { ok: false, error: 'unreachable', detail: String(e && e.message).slice(0, 200), status: null, ms: now() - started };
    }

    // The redirect target is a fresh, unvalidated host: re-check it, or an
    // open redirect turns this into an SSRF primitive.
    try {
      await resolveSafely(new URL(res.url || u.toString()).hostname, lookup);
    } catch (e) {
      return { ok: false, error: 'blocked_redirect', detail: e.message, status: res.status, ms: now() - started };
    }

    let body = '';
    let bytes = 0;
    try {
      const capped = await readCapped(res, Math.min(maxBytes || 65536, Number(cfg.geoAuditMaxBytes)));
      body = capped.buffer.toString('utf8');
      bytes = capped.bytes;
    } catch {
      /* headers already tell us most of what we need */
    }
    return {
      ok: res.ok,
      status: res.status,
      finalUrl: res.url || u.toString(),
      contentType: res.headers.get('content-type') || '',
      body,
      bytes,
      ms: now() - started,
    };
  };
}

/** Run tasks with bounded concurrency, so neither product floods one origin. */
async function pool(tasks, limit) {
  const results = new Array(tasks.length);
  let next = 0;
  const workers = Array.from({ length: Math.min(limit, tasks.length) }, async () => {
    while (true) {
      const i = next++;
      if (i >= tasks.length) return;
      results[i] = await tasks[i]();
    }
  });
  await Promise.all(workers);
  return results;
}

function createGeoAuditProduct({ cfg, fetchImpl = fetch, now = Date.now, lookup = dns.lookup }) {
  const probe = createProbe({ cfg, fetchImpl, now, lookup });

  return {
    id: 'geo_audit',
    title: 'GEO / agent-legibility audit',
    description:
      'Audit whether a website can actually be read, quoted and cited by AI agents, and get a prioritised fix list. Probes the homepage as each real AI crawler (GPTBot, OAI-SearchBot, ChatGPT-User, ClaudeBot, Claude-User, PerplexityBot, CCBot, meta-externalagent, Amazonbot) to catch the 429s, 403s and soft-404s that make a live site invisible; parses robots.txt per agent including the robots-only training tokens Google-Extended and Applebot-Extended; and checks llms.txt, structured data, machine-readable endpoints (OpenAPI, MCP, x402, well-known), sitemap, canonical and title/meta, plus how much of the page survives with JavaScript off. Fully deterministic — no model is called, so the same site scores the same twice. It does NOT ask ChatGPT or Perplexity whether they have heard of your brand; it measures the inputs that decide whether they can.',
    path: '/x402/geo/audit',
    routes: [{ method: 'POST', path: '/x402/geo/audit' }],
    priceUsd: cfg.geoAuditPriceUsd,
    enabled: cfg.geoAuditEnabled,
    // The origin is a third party we do not control. If it is down or blocks
    // us outright there is no report to sell, so the work happens first.
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 16 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          url: { type: 'string', required: true, description: 'absolute http(s) URL of the site to audit; the origin is derived from it' },
        },
      },
      output: {
        type: 'json',
        description:
          'score (0-100), grade, categories[] {id, label, weight, score, findings[]}, findings[] {id, category, status, detail, evidence, fix}, crawler_access[] {agent, operator, purpose, http_status, robots, verdict}, fixes[] (prioritised, highest impact first), pages_fetched, not_measured[], audited_at',
      },
    },

    async availability() {
      return { available: true };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      if (typeof b.url !== 'string' || !b.url.trim()) throw bad('url is required and must be an absolute http(s) URL', 'invalid_request');
      const u = parseTarget(b.url.trim());
      return { url: u.toString(), origin: u.origin, host: u.hostname };
    },

    async handler(ctx) {
      const { url, origin } = ctx.params;
      const deadline = now() + Number(cfg.geoAuditBudgetMs);
      const conc = Number(cfg.geoAuditConcurrency);
      const findings = [];
      const add = (f) => findings.push(f);

      // ---- 1. The page itself, as an ordinary reader ----------------------
      const home = await probe(url, { maxBytes: Number(cfg.geoAuditMaxBytes), deadline });
      if (!home.ok && (home.error === 'unreachable' || home.error === 'blocked_host' || home.error === 'invalid_url')) {
        // Nothing to audit and nothing to charge for.
        throw bad(
          `could not fetch ${url}: ${home.detail || home.error}. Nothing was charged.`,
          'origin_unreachable',
          { origin_error: home.error },
        );
      }
      const html = home.contentType.includes('html') || /<html/i.test(home.body) ? home.body : '';
      const text = html ? htmlToText(html) : home.body;

      // ---- 2. Well-known paths, in parallel -------------------------------
      const WELL_KNOWN = [
        { key: 'robots', path: '/robots.txt' },
        { key: 'llms', path: '/llms.txt' },
        { key: 'llmsFull', path: '/llms-full.txt' },
        { key: 'sitemap', path: '/sitemap.xml' },
        { key: 'openapi', path: '/openapi.json' },
        { key: 'mcp', path: '/.well-known/mcp.json' },
        { key: 'x402', path: '/.well-known/x402' },
        { key: 'aiPlugin', path: '/.well-known/ai-plugin.json' },
      ];
      const wkResults = await pool(
        WELL_KNOWN.map((w) => () => probe(origin + w.path, { maxBytes: 200_000, deadline })),
        conc,
      );
      const wk = {};
      WELL_KNOWN.forEach((w, i) => { wk[w.key] = wkResults[i]; });

      const robotsGroups = wk.robots && wk.robots.ok ? parseRobots(wk.robots.body) : [];
      const robotsPresent = Boolean(wk.robots && wk.robots.ok);

      // ---- 3. Probe as each real AI crawler -------------------------------
      // The headline check. A site can be perfect in every other respect and
      // still be invisible because it answers 429 to the agents that matter.
      //
      // ROBOTS IS CONSULTED FIRST, AND HONOURED. Where robots.txt already
      // forbids an agent the answer is settled, so no request is sent: sending
      // one would mean identifying as a crawler the site has explicitly asked
      // to stay away, purely to confirm something we can already read. The
      // probe only runs where the site says the agent is welcome — which is
      // also where the interesting failure lives, because a 403 or 429 there
      // is an edge rule blocking traffic the owner chose to allow. That is the
      // finding worth paying for, and it is invisible from robots.txt alone.
      const reqPath = new URL(url).pathname || '/';
      const robotsFor = FETCHING_AGENTS.map((a) => (robotsPresent
        ? robotsVerdict(robotsGroups, a.id, reqPath)
        : { allowed: true, matched: 'no robots.txt', specific: false }));

      const agentResults = await pool(
        FETCHING_AGENTS.map((a, i) => () => (robotsFor[i].allowed
          ? probe(url, { ua: a.ua, maxBytes: 4096, deadline })
          : Promise.resolve({ ok: false, error: 'not_probed_robots_disallow', status: null, ms: 0 }))),
        conc,
      );
      const probesSent = robotsFor.filter((r) => r.allowed).length;

      const crawlerAccess = FETCHING_AGENTS.map((a, i) => {
        const r = agentResults[i];
        const robots = robotsFor[i];
        let verdict;
        let detail;
        if (!robots.allowed) {
          verdict = 'blocked_by_robots';
          detail = `robots.txt disallows this agent (${robots.matched}), so no request was sent. `
            + 'If that exclusion is deliberate, this is working as intended and the lost points are the price you chose to pay.';
        } else if (r.error === 'budget_exhausted') {
          verdict = 'not_probed';
          detail = 'the audit ran out of time before this agent was probed';
        } else if (r.status === null) {
          verdict = 'unreachable';
          detail = r.detail || r.error || 'no response';
        } else if (r.status === 429) {
          verdict = 'rate_limited';
          detail = 'robots.txt permits this agent but the origin answered 429 to it — an edge or WAF rule contradicting your own stated policy. This is the single most common cause of an otherwise healthy site being absent from AI answers, and it is usually unintentional.';
        } else if (r.status === 403 || r.status === 401) {
          verdict = 'forbidden';
          detail = `robots.txt permits this agent but the origin answered ${r.status} to its user-agent while serving ordinary readers — an edge or WAF rule contradicting your own stated policy.`;
        } else if (r.status >= 500) {
          verdict = 'server_error';
          detail = `the origin answered ${r.status} to this crawler`;
        } else if (r.status === 404) {
          verdict = 'not_found';
          detail = 'the origin answered 404 to this crawler for a URL that exists for ordinary readers';
        } else if (r.status >= 200 && r.status < 300) {
          verdict = 'ok';
          detail = `served ${r.status}`;
        } else {
          verdict = 'unexpected';
          detail = `the origin answered ${r.status}`;
        }
        return {
          agent: a.id,
          operator: a.operator,
          purpose: a.purpose,
          http_status: r.status,
          response_ms: r.ms,
          robots: { allowed: robots.allowed, rule: robots.matched, agent_specific_group: robots.specific },
          verdict,
          detail,
        };
      });

      const robotsOnly = ROBOTS_ONLY_AGENTS.map((a) => {
        const v = robotsPresent
          ? robotsVerdict(robotsGroups, a.id, '/')
          : { allowed: true, matched: 'no robots.txt', specific: false };
        return {
          agent: a.id,
          operator: a.operator,
          purpose: a.purpose,
          http_status: null,
          response_ms: null,
          robots: { allowed: v.allowed, rule: v.matched, agent_specific_group: v.specific },
          verdict: v.allowed ? 'allowed_by_robots' : 'blocked_by_robots',
          detail: v.allowed
            ? 'permitted in robots.txt; there is no separate crawler to probe for this token'
            : `robots.txt disallows this token (${v.matched}); no separate crawler exists to probe`,
          note: 'robots.txt-only token — this operator crawls under a different user-agent, so a live probe would measure the wrong thing',
        };
      });

      const probed = crawlerAccess.filter((c) => c.verdict !== 'not_probed');
      const reachable = probed.filter((c) => c.verdict === 'ok');
      const accessScore = probed.length ? reachable.length / probed.length : 0;

      // robots.txt says welcome, the edge says no. Nobody configures that on
      // purpose, and it is usually a one-line WAF fix with outsized effect.
      const contradictions = crawlerAccess.filter(
        (c) => c.robots.allowed && ['rate_limited', 'forbidden', 'not_found', 'server_error'].includes(c.verdict),
      );
      add({
        id: 'ai_crawler_access',
        category: 'crawler_access',
        contradiction: contradictions.length > 0,
        status: accessScore === 1 ? 'pass' : accessScore >= 0.7 ? 'warn' : 'fail',
        detail: `${reachable.length} of ${probed.length} AI crawlers were served normally.`
          + (reachable.length < probed.length
            ? ` Blocked or failing: ${probed.filter((c) => c.verdict !== 'ok').map((c) => `${c.agent} (${c.verdict})`).join(', ')}.`
            : ''),
        evidence: {
          probed: probed.length,
          served: reachable.length,
          self_contradicting: contradictions.map((c) => `${c.agent}: robots allows, origin answered ${c.http_status}`),
        },
        // The remedy depends on WHERE the block lives. Telling someone to check
        // their WAF when the exclusion is three lines they wrote in robots.txt
        // makes the whole report look like it did not read its own findings.
        fix: accessScore === 1 ? null : (() => {
          const httpBlocked = contradictions.length > 0;
          const robotsBlocked = crawlerAccess.some((c) => c.verdict === 'blocked_by_robots');
          const waf = 'Allow these user-agents through your CDN/WAF rate limiting and bot rules — they are low-volume and identify themselves honestly. '
            + 'A 429 or 403 here means your content cannot be quoted in AI answers at all, and no amount of on-page work compensates.';
          const robots = 'These agents are excluded by your own robots.txt. If that is deliberate, this category is working as intended and you can ignore the lost points. '
            + 'If it is not — an inherited default, a template, or a line added before AI answers mattered — removing those Disallow rules is the single highest-impact change available here.';
          if (!httpBlocked) return robots;
          const names = contradictions.map((c) => c.agent).join(', ');
          const edge = `${names} ${contradictions.length === 1 ? 'is' : 'are'} permitted by your robots.txt but refused by your server, `
            + `which nobody configures on purpose. ${waf}`;
          return robotsBlocked ? `Two separate causes. First, the edge: ${edge} Second, robots.txt: ${robots}` : edge;
        })(),
      });

      // ---- 4. llms.txt ----------------------------------------------------
      const llmsOk = wk.llms && wk.llms.ok && wk.llms.body.trim().length > 0;
      const llmsIsHtml = llmsOk && /<html|<!doctype/i.test(wk.llms.body.slice(0, 400));
      const llmsChars = llmsOk ? wk.llms.body.trim().length : 0;
      add({
        id: 'llms_txt',
        category: 'llms_txt',
        status: !llmsOk || llmsIsHtml ? 'fail' : llmsChars < 300 ? 'warn' : 'pass',
        detail: !llmsOk
          ? 'No /llms.txt. This is the one file written specifically for agents: a short, plain-language map of what this site is and where the useful parts are.'
          : llmsIsHtml
            ? '/llms.txt returned HTML, not plain text — almost always a catch-all route or SPA fallback answering 200 for a file that does not exist. An agent reads that as a broken file.'
            : llmsChars < 300
              ? `/llms.txt exists but is only ${llmsChars} characters — too thin to orient an agent.`
              : `/llms.txt present (${llmsChars} characters).`,
        evidence: { status: wk.llms ? wk.llms.status : null, chars: llmsChars, looks_like_html: llmsIsHtml },
        fix: !llmsOk || llmsIsHtml || llmsChars < 300
          ? 'Publish /llms.txt as plain text: one paragraph on what the site is, then a linked list of the pages that actually answer questions, each with a sentence saying what it answers. Serve it with content-type text/plain and make sure a missing file 404s rather than falling through to your SPA.'
          : null,
      });
      const llmsFullOk = wk.llmsFull && wk.llmsFull.ok && !/<html|<!doctype/i.test(wk.llmsFull.body.slice(0, 400));
      add({
        id: 'llms_full_txt',
        category: 'llms_txt',
        status: llmsFullOk ? 'pass' : 'warn',
        detail: llmsFullOk
          ? `/llms-full.txt present (${wk.llmsFull.body.length} characters) — an agent can read the whole story in one fetch.`
          : 'No /llms-full.txt. Optional, but it lets an agent take your full documentation in a single request instead of crawling and guessing.',
        evidence: { status: wk.llmsFull ? wk.llmsFull.status : null },
        fix: llmsFullOk ? null : 'Publish /llms-full.txt: the expanded version of llms.txt with your key documentation inlined as plain text.',
      });

      // ---- 5. Citable content --------------------------------------------
      const textChars = text ? text.length : 0;
      const jsChars = html ? scriptBytes(html) : 0;
      const jsHeavy = textChars > 0 && jsChars > textChars * 4;
      const thin = textChars < 500;
      add({
        id: 'server_rendered_text',
        category: 'content_citability',
        status: thin ? 'fail' : jsHeavy ? 'warn' : 'pass',
        detail: thin
          ? `Only ${textChars} characters of text survive with JavaScript off. Most AI crawlers do not execute JavaScript, so this page is close to empty to them however it looks in a browser.`
          : jsHeavy
            ? `${textChars} characters of text against ${jsChars} characters of inline script. Readable, but the balance suggests much of the page is assembled client-side and invisible to non-executing crawlers.`
            : `${textChars} characters of server-rendered text — readable without executing anything.`,
        evidence: { text_chars: textChars, inline_script_chars: jsChars, html_bytes: home.bytes },
        fix: thin || jsHeavy
          ? 'Server-render or pre-render the content that matters. If the stack cannot, ship a static HTML version of each key page and reference it from the sitemap — an agent that sees an empty shell has nothing to cite.'
          : null,
      });
      const h1s = html ? countTag(html, 'h1') : 0;
      add({
        id: 'heading_structure',
        category: 'content_citability',
        status: h1s === 1 ? 'pass' : h1s === 0 ? 'fail' : 'warn',
        detail: h1s === 0
          ? 'No <h1>. Retrieval systems lean on headings to decide what a page is about and which fragment to quote.'
          : h1s === 1
            ? 'Exactly one <h1> — a clear subject for the page.'
            : `${h1s} <h1> elements. Multiple top-level headings make it ambiguous which claim the page is actually making.`,
        evidence: { h1_count: h1s },
        fix: h1s === 1 ? null : 'Use exactly one <h1> stating what the page is about, with <h2>/<h3> beneath it for each answerable sub-topic.',
      });

      // ---- 6. Crawl basics ------------------------------------------------
      const title = html ? extractTitle(html) : null;
      const desc = html ? extractMeta(html, 'description') : null;
      const canonical = html ? /<link[^>]+rel=["']canonical["'][^>]*>/i.test(html) : false;
      add({
        id: 'title_and_description',
        category: 'crawl_basics',
        status: title && desc ? 'pass' : title || desc ? 'warn' : 'fail',
        detail: `title: ${title ? `"${title.slice(0, 120)}"` : 'MISSING'}; meta description: ${desc ? `"${desc.slice(0, 120)}"` : 'MISSING'}.`,
        evidence: { has_title: Boolean(title), has_description: Boolean(desc) },
        fix: title && desc ? null : 'Give every page a specific <title> and a meta description that states what the page answers. These are what a retrieval system shows when it cites you.',
      });
      add({
        id: 'canonical_url',
        category: 'crawl_basics',
        status: canonical ? 'pass' : 'warn',
        detail: canonical ? 'Canonical link present.' : 'No canonical link — duplicate URLs can split whatever authority the page earns.',
        evidence: { canonical },
        fix: canonical ? null : 'Add <link rel="canonical" href="…"> pointing at the preferred URL for each page.',
      });
      const sitemapOk = wk.sitemap && wk.sitemap.ok && /<urlset|<sitemapindex/i.test(wk.sitemap.body);
      add({
        id: 'sitemap',
        category: 'crawl_basics',
        status: sitemapOk ? 'pass' : 'warn',
        detail: sitemapOk ? 'A valid sitemap.xml is served.' : 'No usable /sitemap.xml — crawlers have to discover pages by following links, and unlinked pages stay unseen.',
        evidence: { status: wk.sitemap ? wk.sitemap.status : null },
        fix: sitemapOk ? null : 'Publish /sitemap.xml listing every page worth citing, and reference it from robots.txt with a Sitemap: line.',
      });
      add({
        id: 'robots_txt',
        category: 'crawl_basics',
        status: robotsPresent ? 'pass' : 'warn',
        detail: robotsPresent
          ? `robots.txt served with ${robotsGroups.length} rule group(s).`
          : 'No robots.txt. Not fatal — absent means allowed — but it is where you grant or withhold AI training and retrieval explicitly.',
        evidence: { status: wk.robots ? wk.robots.status : null, groups: robotsGroups.length },
        fix: robotsPresent ? null : 'Publish /robots.txt with an explicit stance on the AI user-agents, and a Sitemap: line.',
      });

      // ---- 7. Structured data ---------------------------------------------
      const ld = html ? extractJsonLd(html) : [];
      const ldTypes = [...new Set(ld.map((n) => n && n['@type']).filter(Boolean).flatMap((t) => (Array.isArray(t) ? t : [t])))];
      const ldInvalid = ldTypes.includes('__invalid__');
      const validTypes = ldTypes.filter((t) => t !== '__invalid__');
      add({
        id: 'json_ld',
        category: 'structured_data',
        status: ldInvalid ? 'fail' : validTypes.length ? 'pass' : 'warn',
        detail: ldInvalid
          ? 'A JSON-LD block is present but does not parse. Invalid structured data is worse than none: it is silently discarded, so you get the maintenance cost and none of the benefit.'
          : validTypes.length
            ? `JSON-LD present: ${validTypes.slice(0, 8).join(', ')}.`
            : 'No JSON-LD structured data. This is the machine-readable statement of what your organisation, product or article IS, rather than leaving it to be inferred from prose.',
        evidence: { types: validTypes.slice(0, 12), blocks: ld.length, invalid: ldInvalid },
        fix: !ldInvalid && validTypes.length
          ? null
          : 'Add a JSON-LD <script type="application/ld+json"> block with at minimum an Organization (name, url, sameAs, description) and, per page, the matching type — Article, Product, SoftwareApplication or FAQPage. Validate that it parses.',
      });

      // ---- 8. Machine-readable endpoints ----------------------------------
      const machine = [
        { key: 'openapi', label: 'OpenAPI (/openapi.json)', r: wk.openapi, valid: (r) => { try { const j = JSON.parse(r.body); return Boolean(j.openapi || j.swagger); } catch { return false; } } },
        { key: 'mcp', label: 'MCP (/.well-known/mcp.json)', r: wk.mcp, valid: (r) => { try { JSON.parse(r.body); return true; } catch { return false; } } },
        { key: 'x402', label: 'x402 payments (/.well-known/x402)', r: wk.x402, valid: (r) => { try { const j = JSON.parse(r.body); return Boolean(j.x402Version || j.items || j.resources); } catch { return false; } } },
        { key: 'aiPlugin', label: 'AI plugin manifest (/.well-known/ai-plugin.json)', r: wk.aiPlugin, valid: (r) => { try { JSON.parse(r.body); return true; } catch { return false; } } },
      ];
      const present = machine.filter((m) => m.r && m.r.ok && m.valid(m.r));
      // Scored on a scale rather than a pass/fail bucket, and never to zero: a
      // newspaper legitimately has no API, and marking that a total failure
      // would make the audit look like it does not know what it is reading.
      const machineScore = present.length >= 2 ? 1 : present.length === 1 ? 0.75 : 0.5;
      add({
        id: 'machine_endpoints',
        category: 'machine_endpoints',
        status: present.length >= 2 ? 'pass' : 'warn',
        detail: present.length
          ? `Machine-readable surfaces found: ${present.map((m) => m.label).join(', ')}.`
          : 'No machine-readable surface. Optional if this site is only meant to be read — decisive if you want an agent to DO something here, because there is currently nothing for one to call.',
        evidence: Object.fromEntries(machine.map((m) => [m.key, m.r ? (m.r.ok && m.valid(m.r) ? 'valid' : m.r.status) : 'not_probed'])),
        fix: present.length >= 2
          ? null
          : 'Publish at least one machine-readable surface: an OpenAPI document at /openapi.json for a REST API, an MCP manifest at /.well-known/mcp.json for agent tools, or an x402 catalog at /.well-known/x402 if you want agents to pay you directly.',
      });

      // ---- 9. Score --------------------------------------------------------
      const STATUS_SCORE = { pass: 1, warn: 0.5, fail: 0, skip: null };
      const categories = Object.entries(CATEGORIES).map(([id, meta]) => {
        const own = findings.filter((f) => f.category === id);
        const scored = own.map((f) => STATUS_SCORE[f.status]).filter((v) => v !== null);
        // Crawler access is measured as a proportion, not a pass/fail bucket:
        // being served to 8 of 9 agents is genuinely better than 4 of 9, and a
        // three-bucket score would erase that.
        const score = id === 'crawler_access' ? accessScore
          : id === 'machine_endpoints' ? machineScore
          : scored.length ? scored.reduce((a, b) => a + b, 0) / scored.length : 0;
        return { id, label: meta.label, weight: meta.weight, score: Math.round(score * 100) / 100, findings: own.map((f) => f.id) };
      });
      const total = Math.round(categories.reduce((sum, c) => sum + c.score * c.weight, 0));
      const grade = total >= 90 ? 'A' : total >= 75 ? 'B' : total >= 60 ? 'C' : total >= 40 ? 'D' : 'F';

      // Fixes ordered by how much score each one is actually costing, so the
      // first item is always the highest-impact change and not merely the
      // first check that happened to run.
      const fixes = findings
        .filter((f) => f.fix)
        .map((f) => {
          const cat = CATEGORIES[f.category];
          const own = findings.filter((x) => x.category === f.category);
          const share = f.category === 'crawler_access' ? 1 : 1 / Math.max(1, own.length);
          const catScore = f.category === 'crawler_access' ? accessScore
            : f.category === 'machine_endpoints' ? machineScore
            : STATUS_SCORE[f.status] ?? 0;
          const lost = cat.weight * share * (1 - catScore);
          return {
            id: f.id,
            category: f.category,
            status: f.status,
            contradiction: Boolean(f.contradiction),
            points_recoverable: Math.round(lost * 10) / 10,
            detail: f.detail,
            fix: f.fix,
          };
        })
        // A configuration that disagrees with itself comes first even when it
        // costs fewer points: it is a defect rather than a design choice, and
        // it is usually the cheapest thing on the list to correct.
        .sort((a, b) => (Number(b.contradiction) - Number(a.contradiction)) || (b.points_recoverable - a.points_recoverable));

      return { status: 200, bodyObj: {
        product: 'geo_audit',
        url,
        origin,
        final_url: home.finalUrl || url,
        score: total,
        grade,
        categories,
        crawler_access: [...crawlerAccess, ...robotsOnly],
        findings,
        fixes,
        pages_fetched: 1 + WELL_KNOWN.length + probesSent,
        crawler_probes_sent: probesSent,
        crawler_probes_skipped_by_robots: FETCHING_AGENTS.length - probesSent,
        method: 'deterministic — no model was called; re-running against an unchanged site returns the same score',
        not_measured: [
          'Whether ChatGPT, Claude, Perplexity or Gemini currently mention this brand in their answers. That requires frontier API access, changes daily, and cannot be reproduced by you from this response — so it is not scored here and is not hidden inside the total.',
          'Content quality, factual accuracy, and how persuasive the writing is.',
          'Backlinks, domain authority, and traffic.',
          'Anything behind authentication, and any page other than the one URL given.',
        ],
        audited_at: new Date(now()).toISOString(),
      } };
    },
  };
}

module.exports = {
  createGeoAuditProduct, createProbe, pool, parseRobots, robotsVerdict, pathMatches,
  extractJsonLd, scriptBytes, FETCHING_AGENTS, ROBOTS_ONLY_AGENTS, CATEGORIES,
};
