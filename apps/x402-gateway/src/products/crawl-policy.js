'use strict';
/**
 * PAID CRAWL — turning "who is this" into "free, pay, or blocked".
 *
 * Split from crawl-classify.js on purpose. The classifier must stay honest
 * about identity even when the policy wants revenue; this file is where the
 * money rules live and where the guardrails that protect a site owner from
 * their own pricing are enforced.
 *
 * THE GUARDRAILS ARE NOT CONFIGURABLE, AND THAT IS THE POINT:
 *
 *   1. A VERIFIED search crawler is always free. A site owner who bills
 *      Googlebot loses organic traffic worth orders of magnitude more than
 *      the crawl fee, discovers it weeks later in a ranking drop, and blames
 *      us — correctly. No price setting can override this.
 *   2. Monitoring and link-preview bots are always free. Charging them breaks
 *      the owner's own uptime alerts and makes their links render blank in
 *      Slack and iMessage. Both failures look like "the site is broken".
 *   3. A SPOOFED identity is blocked, never billed. Something that forged
 *      Googlebot to get the free lane is not a customer, and taking its money
 *      would mean selling access to a proven liar.
 *   4. Humans are never billed. If we are unsure whether something is human,
 *      it is treated as human. A false charge to a reader is a broken website;
 *      a missed charge to a bot is a rounding error on a tenth of a cent.
 *
 * THE FREE ALLOWANCE EXISTS FOR THE SAME REASON. Every site gets a daily
 * grace quota per client before anything is charged, so a crawler that reads
 * a handful of pages to see whether the site is worth indexing is never
 * billed for looking. It is also what makes the 402 defensible: by the time
 * one arrives, that client has already had real content for free.
 */

/** Purposes that money must never touch, and why. */
const ALWAYS_FREE_KINDS = new Set(['human', 'monitoring', 'preview']);

/** What an operator may choose for the ambiguous lanes. */
const UNKNOWN_POLICIES = new Set(['charge', 'block', 'allow']);

const DEFAULTS = {
  // A tenth of a cent. Deliberately below the price of the engineering time
  // any crawler operator would spend evading it.
  priceUsd: '0.001',
  // Grace: pages one client may read per UTC day before the first 402.
  freePerDay: 100,
  // What to do with honest-but-unexplained automation (curl, python-requests,
  // a browser UA moving at machine speed). Charging is the default because
  // blocking silently breaks integrations the owner may not know they have.
  unknownPolicy: 'charge',
  // Requests/min above which a browser UA stops being treated as a human.
  rateThreshold: 30,
  // Operator's share of every payment. The rest is the gateway fee.
  operatorShareBps: 9000,
};

function normalizeSite(site = {}) {
  const s = {
    domain: String(site.domain || '').toLowerCase(),
    priceUsd: site.priceUsd ? String(site.priceUsd) : DEFAULTS.priceUsd,
    freePerDay: Number.isFinite(Number(site.freePerDay)) ? Math.max(0, Number(site.freePerDay)) : DEFAULTS.freePerDay,
    unknownPolicy: UNKNOWN_POLICIES.has(site.unknownPolicy) ? site.unknownPolicy : DEFAULTS.unknownPolicy,
    rateThreshold: Number.isFinite(Number(site.rateThreshold)) ? Number(site.rateThreshold) : DEFAULTS.rateThreshold,
    operatorShareBps: Number.isFinite(Number(site.operatorShareBps)) ? Number(site.operatorShareBps) : DEFAULTS.operatorShareBps,
    // Extra UA substrings the owner wants free (their own monitoring, a
    // partner's integration). Additive only — it can widen the free lane,
    // never narrow the guardrails above.
    allowUa: Array.isArray(site.allowUa) ? site.allowUa.map((x) => String(x).toLowerCase()) : [],
    // Paths that are free for everyone regardless of who is asking. robots.txt
    // and the terms document MUST be free or the protocol cannot bootstrap.
    freePaths: Array.isArray(site.freePaths) ? site.freePaths.map(String) : [],
    enabled: site.enabled !== false,
  };
  return s;
}

/** Paths that are free on every site, always. A crawler that cannot read the
 *  rules for free can never learn how to pay. */
const PROTOCOL_FREE_PATHS = ['/robots.txt', '/.well-known/x402', '/.well-known/paid-crawl', '/llms.txt', '/sitemap.xml', '/favicon.ico'];

function pathIsFree(pathname, site) {
  const p = String(pathname || '/');
  if (PROTOCOL_FREE_PATHS.some((f) => p === f || p.startsWith(`${f}/`))) return true;
  if (p.startsWith('/sitemap') && p.endsWith('.xml')) return true;
  return (site.freePaths || []).some((f) => (f.endsWith('*') ? p.startsWith(f.slice(0, -1)) : p === f));
}

/**
 * decide({ verdict, site, usage, pass }) -> decision
 *
 * `usage`  : { usedToday } — the client's grace consumption, from the store.
 * `pass`   : an already-purchased crawl pass with requests remaining, or null.
 *
 * Returns { action: 'allow'|'charge'|'block', reason, priceUsd?, free_remaining?,
 *           billable, guardrail? }. `billable` is what the metering layer
 *           records; `action` is what the site's edge enforces.
 *
 * Ordering is the whole design: guardrails first, then an already-paid pass,
 * then the grace allowance, and only then a price. Every earlier rule can
 * only ever make access CHEAPER, so no reordering can accidentally bill a
 * protected client.
 */
function decide({ verdict, site, usage = {}, pass = null, pathname = '/' } = {}) {
  const s = normalizeSite(site);

  if (!s.enabled) {
    return out('allow', 'site_disabled', { billable: false });
  }

  // 0. The protocol's own surface is always readable.
  if (pathIsFree(pathname, s)) {
    return out('allow', 'protocol_free_path', { billable: false });
  }

  const v = verdict || {};

  // 1. GUARDRAILS — not overridable by site configuration.
  if (v.spoofed || v.kind === 'spoofed') {
    return out('block', 'forged_identity', { billable: false, guardrail: true });
  }
  if (v.kind === 'search' && v.trusted) {
    return out('allow', 'verified_search_crawler', { billable: false, guardrail: true });
  }
  if (ALWAYS_FREE_KINDS.has(v.kind)) {
    return out('allow', `always_free:${v.kind}`, { billable: false, guardrail: true });
  }

  // 2. Owner's additive allowlist.
  const ua = String(v.ua || '').toLowerCase();
  if (ua && s.allowUa.some((frag) => frag && ua.includes(frag))) {
    return out('allow', 'site_allowlist', { billable: false });
  }

  // 3. A search crawler we could not prove. WHICH KIND of unproven matters,
  // and conflating the two is a free pass for every scraper on the internet:
  //
  //   a) The operator publishes NO verification method (DuckDuckBot). Nobody
  //      can ever prove it, so refusing the free lane would just mean never
  //      granting it. Billing an unverifiable search bot risks exactly the
  //      ranking damage guardrail 1 exists to prevent, so it stays free and
  //      the decision says plainly that the claim was not proven.
  //
  //   b) The operator DOES publish one and the check did not pass. Anything
  //      claiming Googlebot is in this branch unless it proved it. Granting
  //      the free lane here would mean the string "Googlebot" is worth free
  //      access to anyone who types it — which is the entire product, gone.
  //      (An actively contradicted claim never even reaches here: the
  //      classifier already marked it spoofed and rule 1 blocked it.)
  //
  // (b) therefore falls through to the ordinary unidentified path below,
  // where the site's own policy applies. A verification outage on OUR side
  // degrades a real search crawler to "charged", never to "blocked" —
  // recoverable, and the daily free allowance absorbs most of it.
  if (v.kind === 'search' && !v.verifiable) {
    return out('allow', 'search_crawler_no_published_method', { billable: false, guardrail: true });
  }

  // 4. An already-purchased pass. Checked before the grace allowance so a
  // paying client is never silently spending grace it already bought past.
  if (pass && Number(pass.remaining) > 0) {
    return out('allow', 'crawl_pass', {
      billable: true,
      pass_id: pass.pass_id || pass.passId || null,
      pass_remaining: Number(pass.remaining) - 1,
    });
  }

  // 5. Grace allowance.
  const used = Math.max(0, Number(usage.usedToday || 0));
  if (used < s.freePerDay) {
    return out('allow', 'free_allowance', {
      billable: false,
      free_remaining: s.freePerDay - used - 1,
    });
  }

  // 6. Money, or the door.
  if (v.kind === 'ai') {
    return out('charge', `ai_crawler:${v.actor || 'unidentified'}`, { billable: false, priceUsd: s.priceUsd });
  }
  if (v.kind === 'tool' || v.kind === 'unknown' || v.kind === 'search') {
    if (s.unknownPolicy === 'allow') return out('allow', 'unknown_policy_allow', { billable: false });
    if (s.unknownPolicy === 'block') return out('block', 'unknown_policy_block', { billable: false });
    return out('charge', `unidentified_automation:${v.actor || 'none'}`, { billable: false, priceUsd: s.priceUsd });
  }

  // Unreachable for known kinds; defaulting to allow keeps an unrecognised
  // future verdict from turning into an outage on somebody's website.
  return out('allow', 'unclassified_default_allow', { billable: false });
}

function out(action, reason, extra = {}) {
  return Object.assign({ action, reason, billable: false }, extra);
}

module.exports = { decide, normalizeSite, pathIsFree, DEFAULTS, PROTOCOL_FREE_PATHS, UNKNOWN_POLICIES, ALWAYS_FREE_KINDS };
