'use strict';
/**
 * PAID CRAWL — the gate.
 *
 * THE SHAPE OF THIS PRODUCT, AND WHY IT IS NOT A PROXY. A website owner
 * points their edge at us with one nginx snippet; for each request the edge
 * asks "should I serve this?" and we answer allow / pay / block. We never see
 * their HTML, never terminate their TLS, never become a dependency their
 * content flows through. That is a deliberate limit: a proxy that goes down
 * takes the customer's website with it, and no crawl revenue on earth pays
 * for that phone call. A decision service that goes down fails open at the
 * edge and the site simply serves everything, exactly as it did before.
 *
 * SITE OPERATORS PAY NOTHING, EVER. Every operator-facing route here is free
 * and unauthenticated: register, read your terms, verify your domain, ask for
 * a decision, read your earnings. There is no account, no API key and no
 * subscription, because the money in this product comes from the CRAWLER
 * side. The single paid surface is POST /x402/crawl/pass, and the thing
 * buying it is a bot. Charging the operator would also be self-defeating —
 * they are the side we need thousands of, and they are the side with no
 * demonstrated revenue yet.
 *
 * WHY A PASS AND NOT PER-PAGE SETTLEMENT. A tenth of a cent cannot carry a
 * Base USDC settlement: the gas to move the money is the same order as the
 * money. So one settlement buys a PASS — N requests against one domain inside
 * a bounded window — and the gate decrements a counter. The chain sees one
 * payment for a thousand pages, which is the only arrangement where the
 * arithmetic works at all.
 *
 * REGISTRATION IS UNAUTHENTICATED, WHICH MEANS REGISTRATION PROVES NOTHING.
 * Anyone can POST somebody else's domain. That is survivable because a row
 * only becomes payable once its owner has served a token at a well-known path
 * on the domain itself: unverified rows can be READ (a crawler needs the terms
 * before it can pay) but earn nothing. Proof of control gates the money, not
 * the paperwork.
 */

const crypto = require('node:crypto');

const { classify } = require('./crawl-classify');
const { decide, normalizeSite, DEFAULTS, UNKNOWN_POLICIES } = require('./crawl-policy');
const { utcDay, clientKey } = require('./trial');
const { resolveSafely, parseTarget, readCapped } = require('./web');
const { ProductError } = require('./errors');

const PASS_PREFIX = 'anmcp_';
const VERIFY_PATH = '/.well-known/paid-crawl-verify.txt';

/** Hostname hygiene. Everything downstream keys on this, so it is strict. */
function normalizeDomain(raw) {
  let d = String(raw || '').trim().toLowerCase();
  d = d.replace(/^https?:\/\//, '').replace(/\/.*$/, '').replace(/:\d+$/, '');
  if (!d) throw new ProductError('domain is required', { status: 400, body: { error: 'missing_domain' } });
  if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/.test(d)) {
    throw new ProductError(`not a valid domain: ${d}`, { status: 400, body: { error: 'invalid_domain', domain: d } });
  }
  if (d.length > 253) throw new ProductError('domain too long', { status: 400, body: { error: 'invalid_domain' } });
  return d;
}

function tokenHashOf(token) {
  return crypto.createHash('sha256').update(String(token)).digest('hex');
}

function mintPassToken() {
  return PASS_PREFIX + crypto.randomBytes(24).toString('base64url');
}

function readPassToken(headers = {}, query = null) {
  const h = headers || {};
  const direct = h['x-crawl-pass'] || h['X-Crawl-Pass'];
  if (typeof direct === 'string' && direct.trim()) return direct.trim();
  const auth = h.authorization || h.Authorization;
  if (typeof auth === 'string' && auth.toLowerCase().startsWith('bearer ')) {
    const t = auth.slice(7).trim();
    if (t.startsWith(PASS_PREFIX)) return t;
  }
  if (query && typeof query.get === 'function') {
    const q = query.get('pass');
    if (q && String(q).startsWith(PASS_PREFIX)) return String(q);
  }
  return null;
}

/** The site row as the world sees it — never leaks verify_token. */
function publicSite(row) {
  if (!row) return null;
  return {
    domain: row.domain,
    price_usd: row.price_usd,
    free_per_day: Number(row.free_per_day),
    unknown_policy: row.unknown_policy,
    operator_share_bps: Number(row.operator_share_bps),
    payout_address: row.payout_address || null,
    payout_network: row.payout_network || null,
    allow_ua: safeJson(row.allow_ua_json, []),
    free_paths: safeJson(row.free_paths_json, []),
    enabled: Number(row.enabled) === 1,
    verified: Number(row.verified) === 1,
    verified_at: row.verified_at ? new Date(Number(row.verified_at) * 1000).toISOString() : null,
    created_at: new Date(Number(row.created_at) * 1000).toISOString(),
  };
}

function safeJson(s, fallback) {
  try { const v = JSON.parse(s); return v === null || v === undefined ? fallback : v; } catch (_e) { return fallback; }
}

function siteFromRow(row) {
  return normalizeSite({
    domain: row.domain,
    priceUsd: row.price_usd,
    freePerDay: Number(row.free_per_day),
    unknownPolicy: row.unknown_policy,
    rateThreshold: Number(row.rate_threshold),
    operatorShareBps: Number(row.operator_share_bps),
    allowUa: safeJson(row.allow_ua_json, []),
    freePaths: safeJson(row.free_paths_json, []),
    enabled: Number(row.enabled) === 1,
  });
}

/**
 * The robots.txt block a site owner pastes. Generated server-side from the
 * STORED row so the published terms and the enforced terms cannot drift —
 * the number a crawler reads is the number the gate charges.
 */
function robotsSnippet(row) {
  const s = publicSite(row);
  return [
    '# Animica Paid Crawl — machine-readable crawl terms',
    '# Humans and verified search engines: free. AI crawlers: pay per page.',
    '',
    'User-agent: *',
    'Allow: /',
    '',
    `X402-Crawl: https://animica.dev/x402/crawl/${s.domain}`,
    `X402-Price: ${s.price_usd} USDC per page`,
    'X402-Network: base',
    `X402-Free-Per-Day: ${s.free_per_day}`,
  ].join('\n');
}

function createCrawlGate({ cfg, gatewayStore, fetchImpl = fetch, resolver, now = Date.now, logger = null }) {
  const gatewayFeeBps = () => 10000 - Number((cfg && cfg.crawlOperatorShareBps) || DEFAULTS.operatorShareBps);

  // ------------------------------------------------------------------ decide
  /**
   * The hot path. Deterministic, no model, no chain call — a website's edge
   * is blocked on this, so the only I/O it may do is one optional reverse-DNS
   * lookup (to catch forged Googlebot) and two local sqlite statements.
   */
  async function decideRequest(input) {
    const domain = normalizeDomain(input.domain);
    const row = gatewayStore.getCrawlSite(domain);

    // An unregistered domain is not an error and must not be a block: some
    // edge somewhere is asking us about a site nobody configured, and the
    // safe answer is always "serve it".
    if (!row) {
      return {
        action: 'allow',
        reason: 'domain_not_registered',
        billable: false,
        registered: false,
        register: 'POST https://animica.dev/x402/crawl/sites',
      };
    }

    const site = siteFromRow(row);
    const ua = String(input.userAgent || '');
    const ip = input.ip || null;
    const pathname = String(input.path || '/');
    const ck = input.clientKey || ip || 'unknown';

    // Rate signal for the human-vs-machine call. Reads the same grace counter
    // the allowance uses, which is already per-client-per-day — cheap, and no
    // second table to keep consistent.
    let recentRate = null;
    try {
      recentRate = gatewayStore.crawlUsage(domain, ck, utcDay(now()));
    } catch (_e) { recentRate = null; }

    const verdict = await classify(
      { userAgent: ua, ip, path: pathname, method: input.method, datacenter: input.datacenter },
      { verifyRdns: input.verifyRdns !== false, resolver, recentRate, rateThreshold: site.rateThreshold },
    );

    // Feed the triage queue. Never blocks the decision, never affects it.
    if (verdict.unknownUa && ua) {
      try { gatewayStore.seeUnknownUa(ua); } catch (_e) { /* advisory only */ }
    }

    const passToken = input.passToken || null;
    let pass = null;
    if (passToken) {
      const p = gatewayStore.getCrawlPass(tokenHashOf(passToken));
      if (p && p.domain === domain && Number(p.expires_at) > Math.floor(now() / 1000)) {
        pass = { pass_id: p.pass_id, remaining: Number(p.requests_total) - Number(p.requests_used) };
      }
    }

    const usedToday = recentRate === null ? 0 : recentRate;
    const d = decide({ verdict, site, usage: { usedToday }, pass, pathname });

    // Effects. Only now, once the decision is made, do we write anything.
    if (d.action === 'allow' && d.reason === 'crawl_pass' && passToken) {
      const spent = gatewayStore.spendCrawlPass({
        tokenHash: tokenHashOf(passToken),
        domain,
        actor: verdict.actor,
        operator: verdict.operator,
        kind: verdict.kind,
        path: pathname,
        priceUsd: site.priceUsd,
        operatorShareBps: site.operatorShareBps,
      });
      if (!spent.ok) {
        // The pass died between the read and the spend (exhausted by a
        // concurrent request, or expired). Re-decide WITHOUT it rather than
        // serving a page nobody paid for.
        const d2 = decide({ verdict, site, usage: { usedToday }, pass: null, pathname });
        return finish(d2, { domain, verdict, site, ck, note: 'pass_expired_between_check_and_spend' });
      }
      return {
        action: 'allow',
        reason: 'crawl_pass',
        billable: true,
        pass_remaining: spent.remaining,
        classified_as: publicVerdict(verdict),
        registered: true,
      };
    }

    return finish(d, { domain, verdict, site, ck });
  }

  function finish(d, { domain, verdict, site, ck, note }) {
    if (d.action === 'allow' && d.reason === 'free_allowance') {
      try { gatewayStore.bumpCrawlUsage(domain, ck, utcDay(now())); } catch (_e) { /* counter is best-effort */ }
    }
    const out = {
      action: d.action,
      reason: d.reason,
      billable: !!d.billable,
      registered: true,
      classified_as: publicVerdict(verdict),
    };
    if (note) out.note = note;
    if (d.free_remaining !== undefined) out.free_remaining = Math.max(0, d.free_remaining);
    if (d.guardrail) out.guardrail = true;
    if (d.action === 'charge') {
      out.price_usd = d.priceUsd;
      out.buy_pass = {
        endpoint: 'POST https://animica.dev/x402/crawl/pass',
        body: { domain, requests: 1000 },
        protocol: 'x402',
        networks: ['base', 'animica:1'],
        note: 'one settlement buys a pass; send it back as "X-Crawl-Pass: anmcp_..." on subsequent requests',
      };
    }
    return out;
  }

  function publicVerdict(v) {
    return {
      kind: v.kind,
      actor: v.actor,
      operator: v.operator,
      purpose: v.purpose,
      identity_verified: v.trusted,
      spoofed: v.spoofed,
      signals: v.reasons,
    };
  }

  // ------------------------------------------------------------ verification
  /**
   * Proof of control: the domain must serve its own verify token at
   * /.well-known/paid-crawl-verify.txt. Uses the SSRF-hardened resolver the
   * paid fetch product uses — this endpoint takes a hostname from an
   * anonymous stranger, so it is a request-forgery surface and must not grow
   * its own softer fetch.
   */
  async function verifyDomain(domain) {
    const row = gatewayStore.getCrawlSite(domain);
    if (!row) throw new ProductError(`not registered: ${domain}`, { status: 404, body: { error: 'unknown_domain', domain } });
    const url = `https://${domain}${VERIFY_PATH}`;
    const target = parseTarget(url);
    await resolveSafely(target.hostname);
    let res;
    try {
      res = await fetchImpl(url, { redirect: 'error', signal: AbortSignal.timeout(8000), headers: { 'user-agent': 'AnimicaPaidCrawl/1.0 (+https://animica.dev/x402/crawl)' } });
    } catch (e) {
      return { verified: false, reason: 'fetch_failed', detail: e.message, expected_at: url };
    }
    if (!res.ok) return { verified: false, reason: `http_${res.status}`, expected_at: url };
    const body = (await readCapped(res, 4096)).buffer.toString('utf8').trim();
    if (body !== String(row.verify_token)) {
      return { verified: false, reason: 'token_mismatch', expected_at: url, got_bytes: body.length };
    }
    gatewayStore.markCrawlSiteVerified(domain);
    return { verified: true, domain, verified_at: new Date().toISOString() };
  }

  // ------------------------------------------------------------- free routes
  function registerRoute() {
    return {
      method: 'POST',
      path: '/x402/crawl/sites',
      title: 'Paid Crawl — register a site (free)',
      description:
        'FREE, no account. Register a domain for Paid Crawl and get back the robots.txt snippet and a verification token. Site operators never pay for this product — the crawler pays.',
      // Every field the handler reads. Only `domain` is required — each of the
      // rest keeps its existing value on re-registration, or the default on a
      // first one, so an operator can update one setting without restating all.
      bodyFields: {
        domain: { type: 'string', required: true, description: 'the domain to register, e.g. "example.com"' },
        price_usd: { type: 'string', required: false, description: 'USD charged per AI crawl request' },
        unknown_policy: { type: 'string', required: false, description: 'what to do with an unrecognised crawler: allow | charge | block' },
        free_per_day: { type: 'integer', required: false, description: 'free requests per crawler per UTC day before charging starts' },
        rate_threshold: { type: 'number', required: false, description: 'requests/minute above which an unknown client is treated as a crawler' },
        payout_address: { type: 'string', required: false, description: 'where your share is paid out (verification gates payouts, not registration)' },
        payout_network: { type: 'string', required: false, description: 'the network that payout address is on' },
        allow_ua: { type: 'array', required: false, description: 'user-agent substrings that are always allowed free, up to 32' },
        free_paths: { type: 'array', required: false, description: 'path prefixes that are always free, up to 64' },
        enabled: { type: 'boolean', required: false, description: 'turn charging on or off without deleting the registration' },
        contact: { type: 'string', required: false, description: 'contact for payout and abuse questions' },
      },
      match(p) { return p === '/x402/crawl/sites' ? {} : null; },
      async handler(ctx) {
        const b = ctx.json || {};
        let domain;
        try { domain = normalizeDomain(b.domain); } catch (e) { return { status: e.status || 400, bodyObj: e.body || { error: 'invalid_domain' } }; }

        const existing = gatewayStore.getCrawlSite(domain);
        const price = b.price_usd === undefined ? (existing ? existing.price_usd : DEFAULTS.priceUsd) : String(b.price_usd);
        if (!/^\d+(\.\d{1,6})?$/.test(price) || Number(price) <= 0 || Number(price) > 1) {
          return { status: 400, bodyObj: { error: 'invalid_price', detail: 'price_usd must be a positive USD amount up to 1.00 with at most 6 decimals' } };
        }
        const unknownPolicy = b.unknown_policy === undefined ? (existing ? existing.unknown_policy : DEFAULTS.unknownPolicy) : String(b.unknown_policy);
        if (!UNKNOWN_POLICIES.has(unknownPolicy)) {
          return { status: 400, bodyObj: { error: 'invalid_unknown_policy', allowed: [...UNKNOWN_POLICIES] } };
        }

        const row = gatewayStore.putCrawlSite({
          domain,
          priceUsd: price,
          freePerDay: b.free_per_day === undefined ? (existing ? Number(existing.free_per_day) : DEFAULTS.freePerDay) : Math.max(0, Number(b.free_per_day) || 0),
          unknownPolicy,
          rateThreshold: b.rate_threshold === undefined ? (existing ? Number(existing.rate_threshold) : DEFAULTS.rateThreshold) : Number(b.rate_threshold),
          operatorShareBps: existing ? Number(existing.operator_share_bps) : Number((cfg && cfg.crawlOperatorShareBps) || DEFAULTS.operatorShareBps),
          payoutAddress: b.payout_address === undefined ? (existing ? existing.payout_address : null) : String(b.payout_address).slice(0, 128),
          payoutNetwork: b.payout_network === undefined ? (existing ? existing.payout_network : null) : String(b.payout_network).slice(0, 32),
          allowUa: Array.isArray(b.allow_ua) ? b.allow_ua.slice(0, 32).map(String) : (existing ? safeJson(existing.allow_ua_json, []) : []),
          freePaths: Array.isArray(b.free_paths) ? b.free_paths.slice(0, 64).map(String) : (existing ? safeJson(existing.free_paths_json, []) : []),
          enabled: b.enabled === undefined ? (existing ? Number(existing.enabled) === 1 : true) : !!b.enabled,
          verifyToken: existing ? existing.verify_token : `animica-paid-crawl-${crypto.randomBytes(16).toString('hex')}`,
          contact: b.contact === undefined ? (existing ? existing.contact : null) : String(b.contact).slice(0, 200),
        });

        return {
          status: existing ? 200 : 201,
          bodyObj: {
            product: 'paid_crawl_site',
            cost_to_operator: 'free',
            site: publicSite(row),
            robots_txt: robotsSnippet(row),
            verification: {
              required_for: 'payouts — an unverified domain can be read but never earns',
              serve_this_at: `https://${domain}${VERIFY_PATH}`,
              token: row.verify_token,
              then: `POST https://animica.dev/x402/crawl/verify {"domain":"${domain}"}`,
            },
            install: 'https://animica.dev/x402/crawl/install',
            decision_endpoint: 'POST https://animica.dev/x402/crawl/decide',
          },
        };
      },
    };
  }

  function verifyRoute() {
    return {
      method: 'POST',
      path: '/x402/crawl/verify',
      title: 'Paid Crawl — verify domain ownership (free)',
      description: 'FREE. Prove you control a registered domain by serving its token at /.well-known/paid-crawl-verify.txt. Verification gates payouts, not registration.',
      bodyFields: {
        domain: { type: 'string', required: true, description: 'the registered domain to re-check, e.g. "example.com"' },
      },
      match(p) { return p === '/x402/crawl/verify' ? {} : null; },
      async handler(ctx) {
        const b = ctx.json || {};
        let domain;
        try { domain = normalizeDomain(b.domain); } catch (e) { return { status: e.status || 400, bodyObj: e.body || { error: 'invalid_domain' } }; }
        try {
          const out = await verifyDomain(domain);
          return { status: out.verified ? 200 : 409, bodyObj: { product: 'paid_crawl_verify', ...out } };
        } catch (e) {
          return { status: e.status || 502, bodyObj: e.body || { error: 'verification_failed', detail: e.message } };
        }
      },
    };
  }

  function decideRoute() {
    return {
      method: 'POST',
      path: '/x402/crawl/decide',
      title: 'Paid Crawl — crawl decision endpoint (free)',
      description:
        'FREE and unmetered. The decision endpoint a site edge calls (nginx auth_request, a Cloudflare Worker, or Express middleware): given the inbound request it answers allow / charge / block. Site operators are never billed for this.',
      // Every field the handler reads. All optional in the body because the
      // same values may arrive as X-Crawl-* headers instead: nginx
      // auth_request cannot send a body, so requiring one here would document
      // an endpoint the primary integration cannot call.
      bodyFields: {
        domain: { type: 'string', required: false, description: 'the site being crawled; REQUIRED unless sent as X-Crawl-Domain / X-Original-Host / Host' },
        path: { type: 'string', required: false, description: 'request path (default "/"); or X-Crawl-Path / X-Original-URI' },
        method: { type: 'string', required: false, description: 'request method (default GET); or X-Crawl-Method' },
        user_agent: { type: 'string', required: false, description: 'the crawler\'s User-Agent; or X-Crawl-User-Agent / User-Agent' },
        ip: { type: 'string', required: false, description: 'the crawler\'s IP, used for rDNS verification; or X-Crawl-IP' },
        pass: { type: 'string', required: false, description: 'an anmcp_ crawl pass token; or X-Crawl-Pass' },
        datacenter: { type: 'boolean', required: false, description: 'the edge already knows this IP is datacenter-range' },
        verify_rdns: { type: 'boolean', required: false, description: 'force forward-confirmed rDNS verification of the declared bot' },
      },
      match(p) { return p === '/x402/crawl/decide' ? {} : null; },
      async handler(ctx) {
        const b = ctx.json || {};
        const h = ctx.headers || {};
        // Accept the request either as a JSON body or entirely as forwarded
        // headers, because nginx auth_request cannot send a body.
        const input = {
          domain: b.domain || h['x-crawl-domain'] || h['x-original-host'] || h.host,
          path: b.path || h['x-crawl-path'] || h['x-original-uri'] || '/',
          method: b.method || h['x-crawl-method'] || 'GET',
          userAgent: b.user_agent !== undefined ? b.user_agent : (h['x-crawl-user-agent'] || h['user-agent'] || ''),
          ip: b.ip || h['x-crawl-ip'] || clientKey(ctx),
          passToken: b.pass || readPassToken(h, ctx.query),
          datacenter: b.datacenter,
          verifyRdns: b.verify_rdns,
        };
        if (!input.domain) {
          return { status: 400, bodyObj: { error: 'missing_domain', detail: 'send {"domain":"example.com"} or an X-Crawl-Domain header' } };
        }
        let out;
        try {
          out = await decideRequest(input);
        } catch (e) {
          if (e instanceof ProductError) return { status: e.status, bodyObj: e.body || { error: 'bad_request', detail: e.message } };
          // FAIL OPEN. A bug here must never take a customer's website down.
          if (logger && logger.error) logger.error('paid-crawl decide failed', { detail: e.message });
          return { status: 200, headers: { 'x-crawl-action': 'allow' }, bodyObj: { action: 'allow', reason: 'gate_error_fail_open', detail: e.message, billable: false } };
        }
        // Status codes are the contract for nginx auth_request, which reads
        // nothing else: 204 allow, 403 deny. The headers carry the detail so
        // the edge can turn a deny into a 402 with a real challenge.
        const headers = {
          'x-crawl-action': out.action,
          'x-crawl-reason': out.reason,
        };
        if (out.price_usd) headers['x-crawl-price-usd'] = String(out.price_usd);
        if (out.free_remaining !== undefined) headers['x-crawl-free-remaining'] = String(out.free_remaining);
        if (out.action === 'allow') return { status: 200, headers, bodyObj: out };
        return { status: 403, headers, bodyObj: out };
      },
    };
  }

  function termsRoute() {
    return {
      method: 'GET',
      path: '/x402/crawl/{domain}',
      title: 'Paid Crawl — published crawl terms for one domain (free)',
      description: 'FREE. The machine-readable crawl terms for one domain — the document a robots.txt X402-Crawl line points at. Readable by anyone, including unpaid crawlers, because a crawler that cannot read the rules can never follow them.',
      match(p) {
        const m = /^\/x402\/crawl\/([^/]+)$/.exec(p);
        if (!m) return null;
        // Reserved sub-paths are real endpoints, not domains.
        // Every sibling endpoint under /x402/crawl/ must be listed here, or
        // this route claims it as a domain name and answers 400 for a real
        // endpoint. (Observed live: /x402/crawl/pubkey 400'd because 'pubkey'
        // was missing.) Keep in sync when adding a route.
        if (['sites', 'verify', 'decide', 'pass', 'install', 'earnings', 'proposals', 'pubkey', 'licence', 'license'].includes(m[1])) return null;
        return { domain: m[1] };
      },
      async handler(ctx) {
        let domain;
        try { domain = normalizeDomain(ctx.params.domain); } catch (e) { return { status: e.status || 400, bodyObj: e.body || { error: 'invalid_domain' } }; }
        const row = gatewayStore.getCrawlSite(domain);
        if (!row) {
          return {
            status: 404,
            bodyObj: {
              error: 'domain_not_registered',
              domain,
              detail: 'this domain has not set crawl terms',
              register: 'POST https://animica.dev/x402/crawl/sites',
              cost_to_operator: 'free',
            },
          };
        }
        const s = publicSite(row);
        return {
          status: 200,
          bodyObj: {
            product: 'paid_crawl_terms',
            x402Version: 2,
            domain: s.domain,
            terms: {
              price_usd_per_page: s.price_usd,
              free_per_day_per_client: s.free_per_day,
              free_always: ['verified search engines', 'uptime monitors', 'link previews', 'human browsers'],
              charged: ['AI training crawlers', 'AI answer engines', s.unknown_policy === 'charge' ? 'unidentified automation' : null].filter(Boolean),
              unknown_policy: s.unknown_policy,
            },
            verified_owner: s.verified,
            buy_pass: {
              endpoint: 'POST https://animica.dev/x402/crawl/pass',
              body: { domain: s.domain, requests: 1000 },
              protocol: 'x402',
              networks: ['base', 'animica:1'],
              usage: 'send the returned token as "X-Crawl-Pass: anmcp_..." on every subsequent request',
            },
            robots_txt: robotsSnippet(row),
          },
        };
      },
    };
  }

  function earningsRoute() {
    return {
      method: 'GET',
      path: '/x402/crawl/earnings/{domain}',
      title: 'Paid Crawl — crawl earnings for one domain (free)',
      description: 'FREE. What a domain has earned from paid crawls, and which crawlers paid it.',
      match(p) {
        const m = /^\/x402\/crawl\/earnings\/([^/]+)$/.exec(p);
        return m ? { domain: m[1] } : null;
      },
      async handler(ctx) {
        let domain;
        try { domain = normalizeDomain(ctx.params.domain); } catch (e) { return { status: e.status || 400, bodyObj: e.body || { error: 'invalid_domain' } }; }
        const row = gatewayStore.getCrawlSite(domain);
        if (!row) return { status: 404, bodyObj: { error: 'domain_not_registered', domain } };
        const days = Math.min(365, Math.max(1, Number(ctx.query.get('days') || 30)));
        const since = Math.floor(now() / 1000) - days * 86400;
        const e = gatewayStore.crawlEarnings(domain, since);
        const actors = gatewayStore.crawlTopActors(domain, since, 10);
        return {
          status: 200,
          bodyObj: {
            product: 'paid_crawl_earnings',
            domain,
            window_days: days,
            verified_owner: Number(row.verified) === 1,
            payable: Number(row.verified) === 1,
            payable_note: Number(row.verified) === 1 ? null : 'unverified domains accrue nothing — serve the verify token to enable payouts',
            billed_requests: Number(e.billed_requests || 0),
            operator_usd: Number(e.operator_usd || 0).toFixed(6),
            gateway_usd: Number(e.gateway_usd || 0).toFixed(6),
            operator_share_bps: Number(row.operator_share_bps),
            by_crawler: actors.map((a) => ({
              actor: a.actor, operator: a.operator, requests: Number(a.n), operator_usd: Number(a.operator_usd).toFixed(6),
            })),
          },
        };
      },
    };
  }

  /**
   * The canonical, agent-readable description of Paid Crawl — BOTH halves.
   *
   * Every other discovery surface we publish lists things that cost money, so
   * an agent acting for a website owner had no way to find the half that is
   * free: how to set the thing up. It could see three passes for sale and
   * nothing about how a site starts selling them. This document is the entry
   * point for both audiences and is itself free, unauthenticated, and CORS-
   * open, because a protocol whose setup instructions are behind a paywall
   * cannot bootstrap.
   */
  function wellKnownRoute() {
    return {
      method: 'GET',
      path: '/.well-known/paid-crawl',
      title: 'Paid Crawl — protocol discovery document (free)',
      description: 'FREE, no auth. The whole Paid Crawl protocol in one document: how a website starts charging AI crawlers (free, no account), and how a crawler discovers terms and pays.',
      match(p) { return p === '/.well-known/paid-crawl' ? {} : null; },
      async handler() {
        return {
          status: 200,
          bodyObj: {
            protocol: 'animica.paid-crawl/v1',
            summary: 'Websites charge AI crawlers per page. Readers, verified search engines, uptime monitors and link-preview bots are always free.',
            operator_cost: 'free — a website never pays to use Paid Crawl, in any amount, ever. The crawler pays.',
            gateway: 'https://animica.dev/x402/crawl',

            for_website_owners: {
              cost: 'free',
              account_required: false,
              api_key_required: false,
              steps: [
                {
                  step: 1,
                  what: 'Publish your terms. Free, no account.',
                  call: 'POST https://animica.dev/x402/crawl/sites',
                  body: { domain: 'example.com', price_usd: '0.001', free_per_day: 100 },
                  returns: 'your robots.txt snippet, a verification token, and your settings',
                },
                {
                  step: 2,
                  what: 'Prove you control the domain. Required only to receive payouts — an unverified domain can publish terms and be read, but earns nothing.',
                  serve: `a plain-text file at https://<your-domain>${VERIFY_PATH} containing the token from step 1`,
                  call: 'POST https://animica.dev/x402/crawl/verify',
                  body: { domain: 'example.com' },
                },
                {
                  step: 3,
                  what: 'Paste the returned snippet into your robots.txt so crawlers can read your price.',
                  note: 'this publishes your terms; it does not enforce them',
                },
                {
                  step: 4,
                  what: 'Enforce it at your edge. Ten lines of nginx, or any code that can make one HTTP call per request.',
                  call: 'GET https://animica.dev/x402/crawl/install?domain=example.com',
                  decision_endpoint: 'POST https://animica.dev/x402/crawl/decide',
                  fail_open: 'treat any error, timeout or unexpected status as ALLOW — the gate must never take a website down',
                },
                {
                  step: 5,
                  what: 'Read what you earned, any time, free.',
                  call: 'GET https://animica.dev/x402/crawl/earnings/example.com',
                },
              ],
              revenue_share: {
                operator_bps: Number((cfg && cfg.crawlOperatorShareBps) || DEFAULTS.operatorShareBps),
                note: 'the site keeps this share of every billed crawl; the remainder is the gateway fee',
              },
            },

            for_crawlers: {
              steps: [
                { step: 1, what: 'Read a site\'s terms, free.', call: 'GET https://animica.dev/x402/crawl/{domain}' },
                { step: 2, what: 'Buy a pass. One settlement covers many pages — per-page settlement is impossible at these prices because Base gas would exceed the fee.',
                  options: [
                    { call: 'POST https://animica.dev/x402/crawl/pass', price_usd: '0.010' },
                    { call: 'POST https://animica.dev/x402/crawl/pass/10', price_usd: '0.100' },
                    { call: 'POST https://animica.dev/x402/crawl/pass/100', price_usd: '1.000' },
                  ],
                  pages_granted: 'the pass value divided by the site\'s own per-page rate',
                  protocol: 'x402', networks: ['base', 'animica:1'] },
                { step: 3, what: 'Send the pass on every request.', header: 'X-Crawl-Pass: anmcp_...' },
                { step: 4, what: 'Claim a post-quantum signed licence proving what you licensed. Free to claim, free for anyone to verify.',
                  call: 'POST https://animica.dev/x402/crawl/licence',
                  verify: 'POST https://animica.dev/x402/crawl/licence/verify',
                  public_key: 'GET https://animica.dev/x402/crawl/pubkey',
                  scheme: 'ML-DSA-65 (FIPS 204, scheme id 4099)' },
              ],
            },

            always_free_to_crawl: [
              'verified search engines (forward-confirmed reverse DNS)',
              'uptime and monitoring bots',
              'link-preview unfurlers',
              'human browsers',
              'robots.txt, sitemaps, llms.txt and this document',
              'each site\'s daily free allowance, per client',
            ],
            blocked: 'a crawler that forges a verified identity (e.g. claims Googlebot without proving it) is blocked, never billed',

            robots_txt_directives: {
              'X402-Crawl': 'URL of the machine-readable terms for this domain',
              'X402-Price': 'price per page, e.g. "0.001 USDC per page"',
              'X402-Network': 'settlement network, e.g. "base"',
              'X402-Free-Per-Day': 'free pages per client per UTC day',
              note: 'an Animica convention, not part of the robots.txt standard; a crawler that ignores it is not violating robots.txt, but will be gated by the decision endpoint',
            },

            free_endpoints: [
              'POST /x402/crawl/sites', 'POST /x402/crawl/verify', 'POST /x402/crawl/decide',
              'GET /x402/crawl/install', 'GET /x402/crawl/{domain}', 'GET /x402/crawl/earnings/{domain}',
              'POST /x402/crawl/licence', 'POST /x402/crawl/licence/verify', 'GET /x402/crawl/pubkey',
              'GET /.well-known/paid-crawl',
            ],
            paid_endpoints: ['POST /x402/crawl/pass', 'POST /x402/crawl/pass/10', 'POST /x402/crawl/pass/100'],

            honest_note: 'Most AI crawlers do not implement x402 today. The realistic near-term outcome of enabling this is that unpaying crawlers are BLOCKED rather than that they pay. That is still useful, but it is not revenue yet.',
            documentation: 'https://animica.org/#paid-crawl',
            contact: 'ai@3vdc.com',
          },
        };
      },
    };
  }

  function installRoute() {
    return {
      method: 'GET',
      path: '/x402/crawl/install',
      title: 'Paid Crawl — install guide (free)',
      description: 'FREE. Copy-paste install for the Paid Crawl gate: the nginx auth_request snippet, the Cloudflare Worker, and the plain-HTTP contract for anything else.',
      match(p) { return p === '/x402/crawl/install' ? {} : null; },
      async handler(ctx) {
        const domain = String((ctx.query && ctx.query.get('domain')) || 'example.com');
        const row = gatewayStore.getCrawlSite(domain.toLowerCase());
        return {
          status: 200,
          bodyObj: {
            product: 'paid_crawl_install',
            cost_to_operator: 'free — registration, decisions, verification and earnings are all unpaid routes',
            how_it_works: [
              'Your edge asks us about each inbound request; we answer allow, charge or block.',
              'We never see, proxy or store your content — only the request metadata you send.',
              'If we are unreachable your edge fails OPEN and serves the page, exactly as it did before.',
            ],
            step_1_register: {
              call: 'POST https://animica.dev/x402/crawl/sites',
              body: { domain, price_usd: '0.001', free_per_day: 100 },
            },
            step_2_verify: {
              serve_at: `https://${domain}${VERIFY_PATH}`,
              content: row ? row.verify_token : 'the token returned by step 1',
              then: 'POST https://animica.dev/x402/crawl/verify {"domain":"' + domain + '"}',
              why: 'verification gates PAYOUTS — an unverified domain can publish terms but never earns',
            },
            step_3_robots: row ? robotsSnippet(row) : 'register first to get your snippet',
            step_4_enforce: {
              nginx: nginxSnippet(domain),
              contract: {
                request: 'POST https://animica.dev/x402/crawl/decide with headers X-Crawl-Domain, X-Crawl-Path, X-Crawl-User-Agent, X-Crawl-IP (or the same fields as a JSON body)',
                response: '200 = serve it. 403 = do not serve. X-Crawl-Action / X-Crawl-Reason / X-Crawl-Price-USD headers carry the detail.',
                fail_open: 'treat any error, timeout or non-200/403 as ALLOW',
              },
            },
            earnings: `GET https://animica.dev/x402/crawl/earnings/${domain}`,
          },
        };
      },
    };
  }

  /** The nginx a site owner pastes. Generated so the header names here and the
   *  ones decideRoute() reads can never drift apart. */
  function nginxSnippet(domain) {
    return [
      '# Animica Paid Crawl — ask the gate before serving. Fails OPEN.',
      '# Put this inside your server { } block.',
      '',
      'location = /_paid_crawl_auth {',
      '    internal;',
      '    proxy_pass              https://animica.dev/x402/crawl/decide;',
      '    proxy_method            POST;',
      '    proxy_pass_request_body off;',
      '    proxy_set_header        Content-Length "";',
      `    proxy_set_header        X-Crawl-Domain     ${domain};`,
      '    proxy_set_header        X-Crawl-Path       $request_uri;',
      '    proxy_set_header        X-Crawl-Method     $request_method;',
      '    proxy_set_header        X-Crawl-User-Agent $http_user_agent;',
      '    proxy_set_header        X-Crawl-IP         $remote_addr;',
      '    proxy_set_header        X-Crawl-Pass       $http_x_crawl_pass;',
      '    # Never let the gate become your latency or your outage.',
      '    proxy_connect_timeout   2s;',
      '    proxy_read_timeout      2s;',
      '}',
      '',
      'location / {',
      '    auth_request      /_paid_crawl_auth;',
      '    auth_request_set  $crawl_action $upstream_http_x_crawl_action;',
      '    auth_request_set  $crawl_price  $upstream_http_x_crawl_price_usd;',
      '    error_page 401 403 = @paid_crawl_402;',
      '    # ... your normal config (try_files / proxy_pass) stays here ...',
      '}',
      '',
      'location @paid_crawl_402 {',
      '    add_header Content-Type application/json always;',
      `    add_header X-402-Terms "https://animica.dev/x402/crawl/${domain}" always;`,
      '    return 402 \'{"error":"payment_required","buy":"https://animica.dev/x402/crawl/pass","domain":"' + domain + '","terms":"https://animica.dev/x402/crawl/' + domain + '"}\';',
      '}',
    ].join('\n');
  }

  // ------------------------------------------------------- the paid product
  /**
   * The ONLY paid surface in Paid Crawl, and a crawler is what buys it.
   *
   * WHY THREE FIXED SIZES INSTEAD OF "pay for N requests". The paywall reads
   * one static `priceUsd` per product when it builds the 402 challenge —
   * there is no per-request quoting hook, and inventing one would mean
   * touching the settlement path every product on this gateway depends on.
   * Fixed price with a FLEXIBLE QUANTITY gets the same outcome safely: the
   * buyer picks a size, and how many pages that buys is the size divided by
   * the site's own published per-page rate. A $1 pass at a tenth-of-a-cent
   * site is a thousand pages for ONE settlement, which is the whole economic
   * point of a pass.
   *
   * The site owner still sets the rate, so the same $1 buys 200 pages of a
   * $0.005 site and 50 of a $0.02 one. Nobody is marked up for being cheap.
   */
  const PASS_TIERS = [
    { id: 'crawl_pass', suffix: '', priceUsd: '0.010', label: 'Crawl pass (small)' },
    { id: 'crawl_pass_10', suffix: '/10', priceUsd: '0.100', label: 'Crawl pass (medium)' },
    { id: 'crawl_pass_100', suffix: '/100', priceUsd: '1.000', label: 'Crawl pass (bulk)' },
  ];

  function passProduct(tier) {
    const path = `/x402/crawl/pass${tier.suffix}`;
    return {
      id: tier.id,
      title: tier.label,
      description:
        `Buy bulk crawl access to one domain that publishes Paid Crawl terms. $${tier.priceUsd} buys $${tier.priceUsd} worth of that site's pages — the site sets the per-page rate, so the request budget is the pass value divided by that rate. ONE settlement covers the whole budget: per-page settlement is impossible at these prices because Base gas would exceed the fee. Send the returned token back as "X-Crawl-Pass: anmcp_...". Site operators pay nothing for Paid Crawl; crawlers do.`,
      path,
      routes: [{ method: 'POST', path }],
      priceUsd: tier.priceUsd,
      enabled: true,
      mode: 'execute-then-settle',
      mimeType: 'application/json',
      maxBodyBytes: 4 * 1024,
      outputSchema: {
        // bodyFields, NOT a raw JSON Schema: discovery/openapi.js builds the
        // requestBody from `bodyFields` and ignores anything else, so a raw
        // `schema` here produces an OpenAPI operation with NO request body —
        // which is exactly the "Missing input schema" x402scan rejects a
        // resource for. Silent: the 402 challenge still looked correct.
        input: {
          type: 'json',
          bodyFields: {
            domain: { type: 'string', required: true, description: 'the domain to buy crawl access to; it must publish terms at /x402/crawl/{domain}' },
            ttl_seconds: { type: 'integer', description: 'how long the pass stays valid, 60-86400 (default 3600)' },
          },
        },
        output: { type: 'json', description: 'a crawl pass token (anmcp_...), how many requests it covers, the per-page rate it was priced at, and its expiry' },
      },
      async availability() {
        return { available: true };
      },
      async handler(ctx) {
        const b = ctx.json || {};
        const domain = normalizeDomain(b.domain);
        const row = gatewayStore.getCrawlSite(domain);
        if (!row) {
          throw new ProductError(`no published crawl terms for ${domain}`, {
            status: 404,
            body: { error: 'domain_not_registered', domain, detail: 'this domain does not sell crawl access', terms: `https://animica.dev/x402/crawl/${domain}` },
          });
        }
        const perPage = Number(row.price_usd);
        if (!(perPage > 0)) throw new ProductError('site has an invalid per-page price', { status: 409, body: { error: 'invalid_site_price', domain } });

        // Quantity, not price, is what flexes. Floor: a buyer never receives
        // a fraction of a page, and never fewer than one.
        const requests = Math.max(1, Math.floor(Number(tier.priceUsd) / perPage));
        const ttlSec = Math.min(86400, Math.max(60, Number(b.ttl_seconds) || 3600));
        const token = mintPassToken();
        const passId = crypto.randomBytes(12).toString('hex');
        const issuedAt = Math.floor(now() / 1000);

        gatewayStore.putCrawlPass({
          passId,
          tokenHash: tokenHashOf(token),
          domain,
          requestsTotal: requests,
          priceUsd: String(perPage),
          paidUsd: tier.priceUsd,
          payer: ctx.payer || null,
          paymentFingerprint: ctx.paymentFingerprint || null,
          issuedAt,
          expiresAt: issuedAt + ttlSec,
        });

        return {
          status: 200,
          bodyObj: {
            product: tier.id,
            pass: token,
            pass_id: passId,
            domain,
            requests_purchased: requests,
            price_usd_per_page: String(perPage),
            paid_usd: tier.priceUsd,
            expires_at: new Date((issuedAt + ttlSec) * 1000).toISOString(),
            usage: `send "X-Crawl-Pass: ${token}" as a request header on every crawl of ${domain}`,
            terms: `https://animica.dev/x402/crawl/${domain}`,
            operator_share_bps: Number(row.operator_share_bps),
            note: 'the site operator is credited per request you actually spend, not per pass sold',
          },
        };
      },
    };
  }

  return {
    decideRequest,
    verifyDomain,
    robotsSnippet,
    nginxSnippet,
    normalizeDomain,
    tokenHashOf,
    mintPassToken,
    passProducts: PASS_TIERS.map(passProduct),
    freeRoutes: [registerRoute(), verifyRoute(), decideRoute(), installRoute(), wellKnownRoute(), earningsRoute(), termsRoute()],
  };
}

module.exports = {
  createCrawlGate, normalizeDomain, robotsSnippet, tokenHashOf, mintPassToken,
  readPassToken, publicSite, PASS_PREFIX, VERIFY_PATH,
};
