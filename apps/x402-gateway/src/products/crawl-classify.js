'use strict';
/**
 * PAID CRAWL — who is asking, and can they prove it?
 *
 * This module answers one question about an inbound request: what kind of
 * client is this, and is its claim about itself TRUSTWORTHY. It decides
 * nothing about money; policy (crawl-policy.js) turns a verdict into
 * free/pay/block. Keeping the two apart matters because the classifier is
 * the part that must stay honest when the policy gets greedy: a site owner
 * who prices AI crawlers cannot be allowed to accidentally bill Googlebot
 * into dropping them from search.
 *
 * THE CENTRAL PROBLEM: User-Agent is a claim, not an identity. Anyone can
 * send `Googlebot`. So a UA match alone NEVER earns the free-search lane —
 * it earns a *pending* claim that must survive forward-confirmed reverse DNS
 * (rDNS to a hostname, then that hostname resolved back to the same IP; the
 * technique Google, Bing and Apple all document). An unverifiable claim to
 * be Googlebot is worse than no claim at all: it is a spoof, and it is
 * treated as one.
 *
 * WHY THE TAXONOMY IS DATA, NOT CODE. Crawlers appear and rename constantly
 * (Google-Extended and Applebot-Extended are training opt-outs bolted onto
 * existing crawlers; meta-externalagent replaced FacebookBot for AI). A
 * table can be reviewed by a human and corrected in one place; a chain of
 * regexes in a dispatch function cannot.
 *
 * NO ASN DATABASE, DELIBERATELY. "Datacenter IP" detection needs a licensed,
 * constantly-stale ASN feed, and getting it wrong blocks real users behind
 * corporate VPNs and mobile CGNAT. Instead the signals here are ones we can
 * verify ourselves: the UA claim, forward-confirmed rDNS, and (supplied by
 * the caller from the store) the client's recent request rate. A caller that
 * HAS an ASN feed can pass `datacenter: true` and the verdict will use it.
 */

const dns = require('node:dns');

/**
 * The taxonomy. `purpose` drives policy defaults; `verify` names the rDNS
 * suffixes that can forward-confirm the claim (empty = no published method,
 * so the claim can never be verified and never reaches the trusted lane).
 *
 * `pattern` is matched case-insensitively against the raw UA string.
 */
const AGENTS = [
  // ---- Search indexing. Free by default: billing these costs the site its
  // organic traffic, which is worth orders of magnitude more than the crawl.
  { id: 'googlebot',    pattern: 'googlebot',           operator: 'Google',     purpose: 'search',     verify: ['.googlebot.com', '.google.com'] },
  { id: 'bingbot',      pattern: 'bingbot',             operator: 'Microsoft',  purpose: 'search',     verify: ['.search.msn.com'] },
  { id: 'duckduckbot',  pattern: 'duckduckbot',         operator: 'DuckDuckGo', purpose: 'search',     verify: [] },
  { id: 'applebot',     pattern: 'applebot',            operator: 'Apple',      purpose: 'search',     verify: ['.applebot.apple.com'] },
  { id: 'yandexbot',    pattern: 'yandexbot',           operator: 'Yandex',     purpose: 'search',     verify: ['.yandex.ru', '.yandex.net', '.yandex.com'] },
  { id: 'baiduspider',  pattern: 'baiduspider',         operator: 'Baidu',      purpose: 'search',     verify: ['.baidu.com', '.baidu.jp'] },

  // ---- AI training / answer engines. These are the ones Paid Crawl exists
  // for. Google-Extended and Applebot-Extended are training-only opt-out
  // tokens: they identify TRAINING use of the same crawler, so they are AI,
  // not search, and are listed BEFORE the plain bots so they win the match.
  { id: 'google-extended',   pattern: 'google-extended',    operator: 'Google',     purpose: 'ai_training', verify: ['.googlebot.com', '.google.com'] },
  { id: 'applebot-extended', pattern: 'applebot-extended',  operator: 'Apple',      purpose: 'ai_training', verify: ['.applebot.apple.com'] },
  { id: 'gptbot',            pattern: 'gptbot',             operator: 'OpenAI',     purpose: 'ai_training', verify: [] },
  { id: 'oai-searchbot',     pattern: 'oai-searchbot',      operator: 'OpenAI',     purpose: 'ai_answers',  verify: [] },
  { id: 'chatgpt-user',      pattern: 'chatgpt-user',       operator: 'OpenAI',     purpose: 'ai_answers',  verify: [] },
  { id: 'claudebot',         pattern: 'claudebot',          operator: 'Anthropic',  purpose: 'ai_training', verify: [] },
  { id: 'claude-web',        pattern: 'claude-web',         operator: 'Anthropic',  purpose: 'ai_answers',  verify: [] },
  { id: 'claude-user',       pattern: 'claude-user',        operator: 'Anthropic',  purpose: 'ai_answers',  verify: [] },
  { id: 'anthropic-ai',      pattern: 'anthropic-ai',       operator: 'Anthropic',  purpose: 'ai_training', verify: [] },
  { id: 'perplexitybot',     pattern: 'perplexitybot',      operator: 'Perplexity', purpose: 'ai_answers',  verify: ['.perplexity.ai'] },
  { id: 'perplexity-user',   pattern: 'perplexity-user',    operator: 'Perplexity', purpose: 'ai_answers',  verify: ['.perplexity.ai'] },
  { id: 'ccbot',             pattern: 'ccbot',              operator: 'Common Crawl', purpose: 'ai_training', verify: [] },
  { id: 'bytespider',        pattern: 'bytespider',         operator: 'ByteDance',  purpose: 'ai_training', verify: [] },
  { id: 'amazonbot',         pattern: 'amazonbot',          operator: 'Amazon',     purpose: 'ai_training', verify: [] },
  { id: 'meta-externalagent', pattern: 'meta-externalagent', operator: 'Meta',      purpose: 'ai_training', verify: [] },
  { id: 'facebookbot',       pattern: 'facebookbot',        operator: 'Meta',       purpose: 'ai_training', verify: [] },
  { id: 'youbot',            pattern: 'youbot',             operator: 'You.com',    purpose: 'ai_answers',  verify: [] },
  { id: 'cohere-ai',         pattern: 'cohere-ai',          operator: 'Cohere',     purpose: 'ai_training', verify: [] },
  { id: 'ai2bot',            pattern: 'ai2bot',             operator: 'Allen AI',   purpose: 'ai_training', verify: [] },
  { id: 'diffbot',           pattern: 'diffbot',            operator: 'Diffbot',    purpose: 'ai_training', verify: [] },
  { id: 'omgili',            pattern: 'omgili',             operator: 'Webz.io',    purpose: 'ai_training', verify: [] },
  { id: 'imagesiftbot',      pattern: 'imagesiftbot',       operator: 'ImageSift',  purpose: 'ai_training', verify: [] },
  { id: 'timpibot',          pattern: 'timpibot',           operator: 'Timpi',      purpose: 'ai_training', verify: [] },
  { id: 'petalbot',          pattern: 'petalbot',           operator: 'Huawei',     purpose: 'ai_training', verify: [] },
  { id: 'mistralai-user',    pattern: 'mistralai-user',     operator: 'Mistral',    purpose: 'ai_answers',  verify: [] },

  // ---- Infrastructure we must not break. Uptime checks and feed readers
  // are cheap, low-volume, and blocking them breaks the owner's own alerts.
  { id: 'uptimerobot',  pattern: 'uptimerobot',   operator: 'UptimeRobot', purpose: 'monitoring', verify: [] },
  { id: 'pingdom',      pattern: 'pingdom',       operator: 'Pingdom',     purpose: 'monitoring', verify: [] },
  { id: 'statuscake',   pattern: 'statuscake',    operator: 'StatusCake',  purpose: 'monitoring', verify: [] },
  { id: 'betteruptime', pattern: 'betteruptime',  operator: 'Better Stack', purpose: 'monitoring', verify: [] },
  { id: 'letsencrypt',  pattern: 'let\'s encrypt', operator: 'ISRG',       purpose: 'monitoring', verify: [] },

  // ---- Social preview unfurlers. A human pasted a link; blocking these
  // silently breaks how the owner's own content looks when shared.
  { id: 'twitterbot',     pattern: 'twitterbot',     operator: 'X',        purpose: 'preview', verify: [] },
  { id: 'slackbot',       pattern: 'slackbot',       operator: 'Slack',    purpose: 'preview', verify: [] },
  { id: 'discordbot',     pattern: 'discordbot',     operator: 'Discord',  purpose: 'preview', verify: [] },
  { id: 'linkedinbot',    pattern: 'linkedinbot',    operator: 'LinkedIn', purpose: 'preview', verify: [] },
  { id: 'whatsapp',       pattern: 'whatsapp',       operator: 'Meta',     purpose: 'preview', verify: [] },
  { id: 'telegrambot',    pattern: 'telegrambot',    operator: 'Telegram', purpose: 'preview', verify: [] },
  { id: 'facebookexternalhit', pattern: 'facebookexternalhit', operator: 'Meta', purpose: 'preview', verify: [] },

  // ---- Generic automation. Honest about being a script but says nothing
  // about WHY. Policy decides; most owners charge these.
  { id: 'scrapy',        pattern: 'scrapy',            operator: null, purpose: 'scraping', verify: [] },
  { id: 'python-requests', pattern: 'python-requests', operator: null, purpose: 'scraping', verify: [] },
  { id: 'python-urllib', pattern: 'python-urllib',     operator: null, purpose: 'scraping', verify: [] },
  { id: 'httpx',         pattern: 'httpx/',            operator: null, purpose: 'scraping', verify: [] },
  { id: 'aiohttp',       pattern: 'aiohttp',           operator: null, purpose: 'scraping', verify: [] },
  { id: 'go-http',       pattern: 'go-http-client',    operator: null, purpose: 'scraping', verify: [] },
  { id: 'curl',          pattern: 'curl/',             operator: null, purpose: 'scraping', verify: [] },
  { id: 'wget',          pattern: 'wget',              operator: null, purpose: 'scraping', verify: [] },
  { id: 'java-http',     pattern: 'java/',             operator: null, purpose: 'scraping', verify: [] },
  { id: 'node-fetch',    pattern: 'node-fetch',        operator: null, purpose: 'scraping', verify: [] },
  { id: 'axios',         pattern: 'axios/',            operator: null, purpose: 'scraping', verify: [] },
  { id: 'headless',      pattern: 'headlesschrome',    operator: null, purpose: 'scraping', verify: [] },
  { id: 'phantomjs',     pattern: 'phantomjs',         operator: null, purpose: 'scraping', verify: [] },
  { id: 'puppeteer',     pattern: 'puppeteer',         operator: null, purpose: 'scraping', verify: [] },
];

/** Browser fingerprints. Necessary but NOT sufficient — every scraper sends
 *  one of these too, which is exactly why a browser UA only reaches the
 *  human lane when nothing else contradicts it. */
const BROWSER_HINTS = ['mozilla/', 'applewebkit', 'gecko/', 'chrome/', 'safari/', 'firefox/', 'edg/', 'opr/'];

function lower(s) { return String(s === null || s === undefined ? '' : s).toLowerCase(); }

/** First taxonomy hit. Order in AGENTS is significant (see Google-Extended). */
function matchAgent(userAgent) {
  const ua = lower(userAgent);
  if (!ua) return null;
  for (const a of AGENTS) {
    if (ua.includes(a.pattern)) return a;
  }
  return null;
}

function looksLikeBrowser(userAgent) {
  const ua = lower(userAgent);
  if (!ua) return false;
  return BROWSER_HINTS.some((h) => ua.includes(h));
}

/**
 * Forward-confirmed reverse DNS.
 *
 * rDNS alone proves nothing — anyone controlling a PTR record can claim
 * `crawl-66-249-66-1.googlebot.com`. The confirmation is resolving that
 * hostname FORWARD and requiring it to come back to the same IP, which only
 * the operator of the A/AAAA record can arrange.
 *
 * Returns { verified, hostname, reason }. Never throws: a DNS failure is an
 * unverified claim, not an error, and must not take the decision path down.
 */
async function forwardConfirmedRdns(ip, suffixes, { resolver = dns.promises, timeoutMs = 1500 } = {}) {
  if (!ip || !Array.isArray(suffixes) || suffixes.length === 0) {
    return { verified: false, hostname: null, reason: 'no_published_method' };
  }
  const withTimeout = (p) => Promise.race([
    p,
    new Promise((_res, rej) => setTimeout(() => rej(new Error('dns_timeout')), timeoutMs).unref?.()),
  ]);
  let names;
  try {
    names = await withTimeout(resolver.reverse(ip));
  } catch (e) {
    // WHOSE failure was it? An IP with no PTR record at all (NXDOMAIN /
    // NODATA) is the answer their side gave us, and every crawler that
    // publishes a verification method also publishes PTRs — so a claimed
    // Googlebot from a PTR-less IP is evidence AGAINST the claim. A timeout
    // or SERVFAIL is OUR resolver failing, which is evidence of nothing and
    // must never be turned into an accusation.
    const code = String((e && (e.code || e.message)) || '');
    const theirs = code.includes('ENOTFOUND') || code.includes('ENODATA');
    return { verified: false, hostname: null, reason: theirs ? 'rdns_no_ptr' : 'rdns_unavailable' };
  }
  const hostname = (names || []).find((n) => suffixes.some((s) => lower(n).endsWith(lower(s))));
  if (!hostname) {
    return { verified: false, hostname: (names || [])[0] || null, reason: 'rdns_suffix_mismatch' };
  }
  let forward = [];
  try {
    const [v4, v6] = await Promise.all([
      withTimeout(resolver.resolve4(hostname)).catch(() => []),
      withTimeout(resolver.resolve6(hostname)).catch(() => []),
    ]);
    forward = [...(v4 || []), ...(v6 || [])];
  } catch (_e) {
    return { verified: false, hostname, reason: 'forward_failed' };
  }
  if (!forward.includes(ip)) {
    return { verified: false, hostname, reason: 'forward_mismatch' };
  }
  return { verified: true, hostname, reason: 'forward_confirmed' };
}

/**
 * classifyLocal(request, options) -> verdict
 *
 * The SYNCHRONOUS core: everything that can be decided from the request
 * itself, with no network. This is the hot path — a gate decision must not
 * wait on DNS for the common case, and must still work when DNS is down.
 *
 * request: { userAgent, ip, path, method, datacenter? }
 * options: { recentRate (req/min, supplied by the caller from the store),
 *            rateThreshold }
 *
 * The verdict is descriptive, not prescriptive: `kind` says what this is and
 * `trusted` says whether the claim was PROVEN. Policy maps the pair onto
 * money. When an agent publishes a verification method, the sync core leaves
 * `trusted:false` and flags `needsRdns` — the async wrapper resolves it.
 */
function classifyLocal(request = {}, options = {}) {
  const ua = String(request.userAgent || '');
  const ip = request.ip || null;
  const { recentRate = null, rateThreshold = 30 } = options;

  const reasons = [];
  const agent = matchAgent(ua);

  if (!ua.trim()) {
    // No UA at all. Every mainstream browser and every well-behaved crawler
    // sends one; omitting it is a choice, and almost always an evasive one.
    reasons.push('no_user_agent');
    return verdict({ kind: 'unknown', purpose: 'unknown', reasons, ua, ip });
  }

  if (agent) {
    reasons.push(`ua_match:${agent.id}`);
    const kind = agent.purpose === 'search' ? 'search'
      : (agent.purpose === 'ai_training' || agent.purpose === 'ai_answers') ? 'ai'
        : agent.purpose === 'monitoring' ? 'monitoring'
          : agent.purpose === 'preview' ? 'preview'
            : 'tool';
    if (agent.verify.length === 0) {
      // No published verification method: we take the claim at face value but
      // never call it trusted. That honesty is why the free lane is reserved
      // for purposes where a false positive is cheap.
      reasons.push('claim_unverifiable');
    }
    return verdict({
      kind,
      actor: agent.id,
      operator: agent.operator,
      purpose: agent.purpose,
      reasons,
      ua,
      ip,
      needsRdns: agent.verify.length > 0,
      verifySuffixes: agent.verify,
    });
  }

  // Unmatched UA. A browser string with human-looking request rate is the
  // only path to the free human lane.
  const browser = looksLikeBrowser(ua);
  reasons.push(browser ? 'browser_ua' : 'unrecognised_ua');
  if (request.datacenter === true) reasons.push('datacenter_ip');
  const fast = typeof recentRate === 'number' && recentRate > rateThreshold;
  if (fast) reasons.push(`rate_exceeded:${recentRate}/min`);

  if (browser && !fast && request.datacenter !== true) {
    return verdict({ kind: 'human', purpose: 'browsing', reasons, ua, ip });
  }
  if (browser) {
    // A browser string behaving like a machine. This is the single largest
    // category of real scraping traffic, and the reason UA-only gating fails.
    reasons.push('browser_ua_machine_behaviour');
    return verdict({ kind: 'unknown', purpose: 'scraping', reasons, ua, ip, unknownUa: true });
  }
  return verdict({ kind: 'unknown', purpose: 'unknown', reasons, ua, ip, unknownUa: true });
}

/**
 * classify(request, options) -> Promise<verdict>
 *
 * classifyLocal plus forward-confirmed rDNS for agents that publish a
 * verification method. A claim that is actively CONTRADICTED becomes
 * kind:'spoofed' and loses every privilege the claimed identity had —
 * the whole point of verifying at all.
 *
 * DNS failure is an unverified claim, never an error: the verdict degrades
 * to untrusted and the decision path stays up.
 */
async function classify(request = {}, options = {}) {
  const { verifyRdns = true, resolver = dns.promises } = options;
  const v = classifyLocal(request, options);
  if (!v.needsRdns || !verifyRdns || !v.ip) {
    if (v.needsRdns) v.reasons.push('verification_skipped');
    return v;
  }
  const rdns = await forwardConfirmedRdns(v.ip, v.verifySuffixes, { resolver });
  v.reasons.push(`rdns:${rdns.reason}`);
  v.hostname = rdns.hostname || null;
  if (rdns.verified) {
    v.trusted = true;
    return v;
  }
  // It claimed an identity WITH a published verification method and did not
  // pass. Three outcomes, and the difference between them is whose failure it
  // was — collapsing them is how a scraper gets Googlebot's free lane.
  if (rdns.reason === 'rdns_suffix_mismatch' || rdns.reason === 'forward_mismatch' || rdns.reason === 'rdns_no_ptr') {
    // Actively contradicted, or coming from an IP that has no reverse record
    // at all while claiming an identity whose operator publishes them. Not a
    // near-miss: a spoof, and a spoof inherits nothing.
    v.spoofed = true;
    v.kind = 'spoofed';
    v.purpose = 'spoofing';
    v.trusted = false;
    v.bot = true;
    v.reasons.push('spoof_detected');
    return v;
  }
  // OUR resolver could not answer (timeout, SERVFAIL, DNS disabled). That is
  // not evidence about the client, so it is not an accusation — but it is also
  // not proof, so the claim must not reach the trusted free lane either. The
  // verdict degrades to unidentified and the site's own policy decides.
  v.trusted = false;
  v.verificationUnavailable = true;
  v.reasons.push('verification_unavailable_degraded');
  return v;
}

function verdict(v) {
  return {
    kind: v.kind,
    actor: v.actor || null,
    operator: v.operator || null,
    purpose: v.purpose,
    trusted: !!v.trusted,
    spoofed: !!v.spoofed,
    bot: v.kind !== 'human',
    ip: v.ip || null,
    ua: v.ua || '',
    hostname: v.hostname || null,
    needsRdns: !!v.needsRdns,
    // True when this agent's operator publishes a verification method, so a
    // failure to verify is meaningful rather than merely unknown.
    verifiable: !!v.needsRdns,
    verificationUnavailable: !!v.verificationUnavailable,
    verifySuffixes: v.verifySuffixes || [],
    // Flags a UA the taxonomy did not recognise. The AICF triage job reads
    // these to propose new taxonomy rows; billing never depends on it.
    unknownUa: !!v.unknownUa,
    reasons: v.reasons,
  };
}

module.exports = {
  classify,
  classifyLocal,
  forwardConfirmedRdns,
  matchAgent,
  looksLikeBrowser,
  AGENTS,
};
