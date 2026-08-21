'use strict';
/**
 * GEO FIX — emit the actual files, not more advice.
 *
 * The audit tells a site owner what is wrong. This writes the three artifacts
 * that fix most of it: a real /llms.txt, a /robots.txt that stops excluding the
 * agents they want, and a JSON-LD block for their <head>. Ready to deploy.
 *
 * THE RULE THAT MATTERS: NOTHING IS INVENTED.
 *
 * An llms.txt full of plausible-looking URLs that 404 is worse than no llms.txt
 * at all — it teaches every agent that reads it that this site's own map of
 * itself is unreliable. So every link in the emitted file is a URL discovered
 * on the site (sitemap or on-page) and then FETCHED to confirm it answers 200.
 * Links that fail are dropped and counted, not quietly kept.
 *
 * The same applies to the JSON-LD: every field is copied from something the
 * page actually declares (og:site_name, meta description, og:image, outbound
 * social links). A field we cannot ground is omitted rather than guessed,
 * because structured data is a machine-readable CLAIM about an organisation
 * and inventing one is a different kind of wrong from padding some prose.
 *
 * ONE MODEL CALL, TIGHTLY FENCED. Prose describing a site is the one part a
 * template does badly, so a small model writes the summary and the one-line
 * "what this page answers" notes — from the titles and descriptions we
 * extracted, and nothing else. Its output is schema-validated in code, and any
 * URL it returns that is not in the verified set is DISCARDED. If the model is
 * unreachable the product still emits everything, using the site's own meta
 * description as the summary, and says so in `summary_source`.
 */

const dns = require('node:dns').promises;
const { createProbe, pool, parseRobots, robotsVerdict, FETCHING_AGENTS, ROBOTS_ONLY_AGENTS } = require('./geo');
const { parseTarget, extractTitle, extractMeta, htmlToText } = require('./web');
const { createEngine } = require('./structured');
const { ProductError } = require('./errors');

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

/**
 * Boilerplate required by law or policy rather than written to answer a
 * question. Sorted last rather than excluded: with enough slots these still
 * appear, they just never displace documentation.
 */
const BOILERPLATE = /\/(accessibility|privacy|terms|legal|cookies?|imprint|dmca|disclaimer)(\/|$)/i;

/** Every agent we would ask a site to admit, in one list. */
const ALL_AGENTS = [...FETCHING_AGENTS.map((a) => a.id), ...ROBOTS_ONLY_AGENTS.map((a) => a.id)];

// ---------------------------------------------------------------------------
// Link discovery
// ---------------------------------------------------------------------------

/** <loc> entries from a sitemap or sitemap index. */
function sitemapLocs(xml) {
  const out = [];
  const re = /<loc>\s*([^<\s]+)\s*<\/loc>/gi;
  let m;
  while ((m = re.exec(xml)) !== null && out.length < 500) out.push(m[1].trim());
  return out;
}

function isSitemapIndex(xml) {
  return /<sitemapindex/i.test(xml);
}

/** Same-origin hrefs from a page, in document order. */
function pageLinks(html, origin) {
  const out = [];
  const re = /<a\b[^>]*href\s*=\s*["']([^"'#]+)["']/gi;
  let m;
  while ((m = re.exec(html)) !== null && out.length < 300) {
    let href = m[1].trim();
    if (!href || href.startsWith('mailto:') || href.startsWith('tel:') || href.startsWith('javascript:')) continue;
    try {
      const u = new URL(href, origin);
      if (u.origin !== origin) continue;
      u.hash = '';
      out.push(u.toString());
    } catch { /* an unparseable href is not a link */ }
  }
  return out;
}

/** Outbound links to the profile hosts schema.org sameAs is actually for. */
const SOCIAL_HOSTS = /(^|\.)(x\.com|twitter\.com|github\.com|linkedin\.com|youtube\.com|mastodon\.social|discord\.gg|facebook\.com|instagram\.com|reddit\.com)$/i;
function socialLinks(html, origin) {
  const out = new Set();
  const re = /<a\b[^>]*href\s*=\s*["'](https?:\/\/[^"'\s]+)["']/gi;
  let m;
  while ((m = re.exec(html)) !== null && out.size < 12) {
    try {
      const u = new URL(m[1]);
      if (u.origin !== origin && SOCIAL_HOSTS.test(u.hostname)) out.add(u.origin + u.pathname.replace(/\/+$/, ''));
    } catch { /* skip */ }
  }
  return [...out];
}

// ---------------------------------------------------------------------------
// robots.txt rewriting
// ---------------------------------------------------------------------------

/**
 * A minimal unified diff. Line-oriented LCS — robots.txt is short, and a
 * changelog without a diff cannot be reviewed or applied mechanically.
 */
function unifiedDiff(aText, bText, aName = 'a/robots.txt', bName = 'b/robots.txt') {
  const a = aText.split('\n');
  const b = bText.split('\n');
  const n = a.length;
  const m = b.length;
  const lcs = Array.from({ length: n + 1 }, () => new Uint32Array(m + 1));
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] = a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const hunk = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) { hunk.push(' ' + a[i]); i++; j++; }
    else if (lcs[i + 1][j] >= lcs[i][j + 1]) { hunk.push('-' + a[i]); i++; }
    else { hunk.push('+' + b[j]); j++; }
  }
  while (i < n) hunk.push('-' + a[i++]);
  while (j < m) hunk.push('+' + b[j++]);
  if (!hunk.some((l) => l[0] === '+' || l[0] === '-')) return null;
  return `--- ${aName}\n+++ ${bName}\n@@ -1,${n} +1,${m} @@\n${hunk.join('\n')}\n`;
}

/**
 * Produce a robots.txt that stops excluding the AI agents, preserving every
 * rule that is not about them.
 *
 * Deliberately conservative: it only ever REMOVES an exclusion that names one
 * of these agents, and adds an explicit allow group plus a Sitemap line. It
 * never touches a wildcard group, because `User-agent: *` rules exist for
 * reasons that have nothing to do with AI, and silently opening a site's admin
 * paths to every crawler would be a far worse outcome than the problem here.
 */
function buildRobots(existing, { sitemapUrl, agents = ALL_AGENTS }) {
  const changes = [];
  const lines = existing === null ? [] : existing.split('\n');
  const groups = existing === null ? [] : parseRobots(existing);

  // Which agents are currently excluded by a group that names them.
  const named = new Set();
  for (const g of groups) for (const a of g.agents) named.add(a);
  const excluded = agents.filter((id) => {
    const v = robotsVerdict(groups, id, '/');
    return !v.allowed && v.specific;
  });
  const excludedByWildcard = agents.filter((id) => {
    const v = robotsVerdict(groups, id, '/');
    return !v.allowed && !v.specific;
  });

  // Drop only the groups that exclusively name agents we are un-excluding.
  const out = [];
  let dropping = false;
  let lastWasAgent = false;
  let groupAgents = [];
  for (const raw of lines) {
    const line = raw.replace(/#.*$/, '').trim();
    const field = line.slice(0, Math.max(0, line.indexOf(':'))).trim().toLowerCase();
    const value = line.slice(line.indexOf(':') + 1).trim().toLowerCase();
    if (field === 'user-agent') {
      if (!lastWasAgent) { groupAgents = []; dropping = false; }
      groupAgents.push(value);
      lastWasAgent = true;
      // Drop the group only when EVERY agent it names is one we are freeing.
      dropping = groupAgents.every((a) => excluded.some((e) => e.toLowerCase() === a));
      if (dropping) { changes.push(`removed exclusion group for ${groupAgents.join(', ')}`); continue; }
      out.push(raw);
      continue;
    }
    lastWasAgent = false;
    if (dropping) continue;
    out.push(raw);
  }

  const allowBlock = [
    '',
    '# Explicitly welcome AI crawlers and answer engines.',
    '# These identify themselves honestly and are low volume.',
    ...agents.map((a) => `User-agent: ${a}`),
    'Allow: /',
    '',
  ];
  const already = agents.every((id) => named.has(id.toLowerCase()) && !excluded.includes(id));
  if (!already || excluded.length) {
    out.push(...allowBlock);
    changes.push(`added an explicit allow group for ${agents.length} AI agents`);
  }

  if (sitemapUrl && !/^\s*sitemap\s*:/im.test(out.join('\n'))) {
    out.push(`Sitemap: ${sitemapUrl}`);
    changes.push('added the Sitemap: line');
  }

  const content = out.join('\n').replace(/\n{3,}/g, '\n\n').trim() + '\n';
  return {
    content,
    changes,
    excluded_agents_freed: excluded,
    // Surfaced, never silently "fixed": a wildcard Disallow is the site's own
    // blanket policy and un-picking it automatically is not our call.
    still_blocked_by_wildcard: excludedByWildcard,
    diff: unifiedDiff(existing === null ? '' : existing, content),
    action: existing === null ? 'create' : 'replace',
  };
}

// ---------------------------------------------------------------------------
// The product
// ---------------------------------------------------------------------------

const SUMMARY_SCHEMA = {
  type: 'object',
  required: ['summary', 'pages'],
  additionalProperties: false,
  properties: {
    summary: {
      type: 'string', minLength: 40, maxLength: 900,
      description: 'two or three plain sentences: what this site is, who it is for, what a reader can do here',
    },
    pages: {
      type: 'array', maxItems: 40,
      items: {
        type: 'object', required: ['url', 'answers'], additionalProperties: false,
        properties: {
          url: { type: 'string', description: 'must be copied EXACTLY from the provided list' },
          answers: { type: 'string', maxLength: 200, description: 'one line: what question this page answers' },
        },
      },
    },
  },
};

function createGeoFixProduct({ cfg, fetchImpl = fetch, now = Date.now, lookup = dns.lookup }) {
  const probe = createProbe({ cfg, fetchImpl, now, lookup });
  const engine = createEngine({ cfg, fetchImpl, now });

  return {
    id: 'geo_fix',
    title: 'GEO fix — deployable llms.txt, robots.txt and JSON-LD',
    description:
      'Emit the actual files that fix a site\'s AI legibility, ready to deploy: a real /llms.txt, a /robots.txt that stops excluding AI crawlers (with a unified diff against the current one), and a JSON-LD block for the page <head>. Nothing is invented: every link in the llms.txt is discovered on the site and then FETCHED to confirm it answers 200, with failures dropped and counted, and every JSON-LD field is copied from something the page actually declares — anything that cannot be grounded is omitted rather than guessed. A small model writes only the prose, from the titles and descriptions we extracted; any URL it returns that is not in the verified set is discarded. Pairs with /x402/geo/audit: audit tells you what is wrong, this hands you the files. Re-run the audit afterwards to confirm the score moved.',
    path: '/x402/geo/fix',
    routes: [{ method: 'POST', path: '/x402/geo/fix' }],
    priceUsd: cfg.geoFixPriceUsd,
    enabled: cfg.geoFixEnabled,
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 16 * 1024,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          url: { type: 'string', required: true, description: 'absolute http(s) URL of the site to fix; the origin is derived from it' },
          max_links: { type: 'integer', required: false, description: `how many pages to verify and list in llms.txt (1..${cfg.geoFixMaxLinks}, default ${cfg.geoFixDefaultLinks})` },
        },
      },
      output: {
        type: 'json',
        description:
          'artifacts {llms_txt {path, content, links_verified, links_dropped, summary_source}, robots_txt {path, content, diff, changes, still_blocked_by_wildcard}, json_ld {content, types, grounded_in, omitted}}, discovered {sitemap, candidate_links, verified}, next_step',
      },
    },

    async availability() {
      // The model is NOT a hard dependency: the summary falls back to the
      // site's own meta description, so a model outage degrades one paragraph
      // rather than withholding two fully deterministic artifacts.
      return { available: true };
    },

    validate(ctx) {
      const b = ctx.json;
      if (!b || typeof b !== 'object' || Array.isArray(b)) throw bad('body must be a JSON object', 'invalid_request');
      if (typeof b.url !== 'string' || !b.url.trim()) throw bad('url is required and must be an absolute http(s) URL', 'invalid_request');
      const u = parseTarget(b.url.trim());
      let maxLinks = Number(cfg.geoFixDefaultLinks);
      if (b.max_links !== undefined) {
        if (!Number.isInteger(b.max_links) || b.max_links < 1 || b.max_links > Number(cfg.geoFixMaxLinks)) {
          throw bad(`max_links must be an integer between 1 and ${cfg.geoFixMaxLinks}`, 'invalid_request');
        }
        maxLinks = b.max_links;
      }
      return { url: u.toString(), origin: u.origin, maxLinks };
    },

    async handler(ctx) {
      const { url, origin, maxLinks } = ctx.params;
      const deadline = now() + Number(cfg.geoFixBudgetMs);
      const conc = Number(cfg.geoAuditConcurrency);

      // ---- 1. The site's own account of itself --------------------------
      const home = await probe(url, { maxBytes: Number(cfg.geoAuditMaxBytes), deadline });
      if (!home.ok) {
        throw bad(
          `could not fetch ${url}: ${home.detail || home.error || `HTTP ${home.status}`}. Nothing was charged.`,
          'origin_unreachable', { origin_error: home.error || `http_${home.status}` },
        );
      }
      const html = home.body || '';
      const siteName = extractMeta(html, 'og:site_name') || extractTitle(html) || new URL(origin).hostname;
      const siteDesc = extractMeta(html, 'description') || extractMeta(html, 'og:description') || '';
      const logo = extractMeta(html, 'og:image') || null;
      const sameAs = socialLinks(html, origin);

      // ---- 2. Existing files --------------------------------------------
      const robotsRes = await probe(`${origin}/robots.txt`, { maxBytes: 200_000, deadline });
      const existingRobots = robotsRes.ok ? robotsRes.body : null;

      // robots.txt is where a site DECLARES its sitemap, and it is frequently
      // not /sitemap.xml. Honour the declaration first and fall back to the
      // conventional path, or we ignore the authoritative page list a site
      // publishes for exactly this purpose.
      const declared = existingRobots && /^\s*sitemap\s*:\s*(\S+)/im.exec(existingRobots);
      let declaredSitemap = null;
      if (declared) {
        try {
          const d = new URL(declared[1], origin);
          if (d.origin === origin) declaredSitemap = d.toString();
        } catch { /* a malformed Sitemap: line is not a sitemap */ }
      }
      const sitemapCandidate = declaredSitemap || `${origin}/sitemap.xml`;
      const sitemapRes = await probe(sitemapCandidate, { maxBytes: 500_000, deadline });

      // ---- 3. Candidate pages -------------------------------------------
      let locs = [];
      let sitemapUrl = null;
      if (sitemapRes.ok && /<(urlset|sitemapindex)/i.test(sitemapRes.body)) {
        sitemapUrl = sitemapCandidate;
        if (isSitemapIndex(sitemapRes.body)) {
          // One nested sitemap only — enough to produce a useful list without
          // turning a single paid call into a crawl of the whole site.
          const first = sitemapLocs(sitemapRes.body)[0];
          if (first) {
            const nested = await probe(first, { maxBytes: 500_000, deadline });
            if (nested.ok) locs = sitemapLocs(nested.body);
          }
        } else {
          locs = sitemapLocs(sitemapRes.body);
        }
      }
      // ORDER MATTERS MORE THAN COVERAGE. llms.txt is a short map, not an
      // index, so the first N links decide whether it is useful. A sitemap is
      // usually alphabetical — ordering by it put "Accessibility" and three old
      // blog posts ahead of the docs, wallet and mining pages. On-page links
      // come first because nav order is the site's OWN statement of what
      // matters; the sitemap then fills any remaining slots.
      const candidates = [];
      const seen = new Set([url.replace(/\/$/, ''), origin]);
      for (const list of [pageLinks(html, origin), locs]) {
        for (const raw of list) {
          let u;
          try { u = new URL(raw, origin); } catch { continue; }
          if (u.origin !== origin) continue;
          const key = u.toString().replace(/\/$/, '');
          if (seen.has(key)) continue;
          seen.add(key);
          candidates.push(u.toString());
          if (candidates.length >= maxLinks * 2) break;
        }
        if (candidates.length >= maxLinks * 2) break;
      }

      // ---- 4. Verify every candidate. This is the product's whole claim. --
      const ordered = candidates.slice(0, maxLinks * 2)
        .map((c, i) => ({ c, i, boiler: BOILERPLATE.test(new URL(c).pathname) }))
        .sort((a, b) => (Number(a.boiler) - Number(b.boiler)) || (a.i - b.i))
        .map((x) => x.c);
      const checked = await pool(
        ordered.map((c) => () => probe(c, { maxBytes: 120_000, deadline }).then((r) => ({ url: c, r }))),
        conc,
      );
      const verified = [];
      let dropped = 0;
      for (const { url: c, r } of checked) {
        if (!r.ok || !(r.contentType.includes('html') || /<html/i.test(r.body || ''))) { dropped++; continue; }
        verified.push({
          url: c,
          title: extractTitle(r.body) || '',
          description: extractMeta(r.body, 'description') || '',
        });
        if (verified.length >= maxLinks) break;
      }

      // ---- 5. Prose, fenced to what we actually read ---------------------
      let summary = '';
      let summarySource;
      let pageNotes = new Map();
      const facts = [
        `SITE: ${siteName}`,
        siteDesc ? `SITE DESCRIPTION: ${siteDesc}` : '',
        `HOMEPAGE TEXT (truncated): ${htmlToText(html).slice(0, 2500)}`,
        '',
        'PAGES (use these URLs EXACTLY as written, and only these):',
        ...verified.map((v) => `- ${v.url} | title: ${v.title} | description: ${v.description}`.slice(0, 400)),
      ].filter(Boolean).join('\n');

      try {
        const out = await engine.structured({
          instruction:
            'Write an llms.txt summary for this website and one line per page saying what question that page answers. '
            + 'Use ONLY the facts given. Do not invent pages, features, claims or URLs. Copy each URL exactly from the list. '
            + 'Write plainly and specifically; no marketing adjectives.',
          input: facts,
          schema: SUMMARY_SCHEMA,
          maxTokens: 1200,
        });
        summary = String(out.data.summary || '').trim();
        summarySource = `written by ${out.model} from this site's own homepage text and page titles`;
        // THE GUARD: a URL the model returned that we did not verify is
        // discarded outright. This is the failure mode that would make the
        // artifact worse than nothing.
        const allowed = new Set(verified.map((v) => v.url));
        for (const p of out.data.pages || []) {
          if (allowed.has(p.url)) pageNotes.set(p.url, String(p.answers || '').trim());
        }
      } catch (e) {
        summary = siteDesc || `${siteName}.`;
        summarySource = siteDesc
          ? "the site's own meta description (the writer model was unavailable, so nothing was paraphrased)"
          : "the site's own name (no meta description was published, and the writer model was unavailable)";
      }

      // ---- 6. Assemble llms.txt ------------------------------------------
      const llmsLines = [
        `# ${siteName}`,
        '',
        summary,
        '',
        '## Pages',
        '',
        ...verified.map((v) => {
          const note = pageNotes.get(v.url) || v.description || v.title || '';
          return `- [${v.title || v.url}](${v.url})${note ? `: ${note}` : ''}`;
        }),
        '',
      ];
      const llmsContent = llmsLines.join('\n');

      // ---- 7. robots.txt ---------------------------------------------------
      const robots = buildRobots(existingRobots, { sitemapUrl: sitemapUrl || declaredSitemap || `${origin}/sitemap.xml` });

      // ---- 8. JSON-LD, grounded field by field ----------------------------
      const org = { '@context': 'https://schema.org', '@type': 'Organization', name: siteName, url: origin };
      const omitted = [];
      if (siteDesc) org.description = siteDesc; else omitted.push('description (the homepage publishes no meta description)');
      if (logo) org.logo = new URL(logo, origin).toString(); else omitted.push('logo (no og:image on the homepage)');
      if (sameAs.length) org.sameAs = sameAs; else omitted.push('sameAs (no links to profile sites found on the homepage)');
      const jsonLd = `<script type="application/ld+json">\n${JSON.stringify(org, null, 2)}\n</script>`;

      return { status: 200, bodyObj: {
        product: 'geo_fix',
        url,
        origin,
        artifacts: {
          llms_txt: {
            path: '/llms.txt',
            content_type: 'text/plain; charset=utf-8',
            action: 'create or replace',
            content: llmsContent,
            links_verified: verified.length,
            links_dropped: dropped,
            summary_source: summarySource,
            note: 'Every URL listed here was fetched and returned HTML with a 200. Links that did not are dropped, not kept — an llms.txt that 404s teaches every agent reading it that this site\'s own map is unreliable.',
          },
          robots_txt: {
            path: '/robots.txt',
            content_type: 'text/plain; charset=utf-8',
            action: robots.action,
            content: robots.content,
            diff: robots.diff,
            changes: robots.changes,
            excluded_agents_freed: robots.excluded_agents_freed,
            still_blocked_by_wildcard: robots.still_blocked_by_wildcard,
            note: robots.still_blocked_by_wildcard.length
              ? `A "User-agent: *" rule still blocks ${robots.still_blocked_by_wildcard.join(', ')}. That wildcard is your blanket policy and may exist for reasons unrelated to AI, so it was left alone deliberately — change it yourself if you meant to admit these agents.`
              : 'Only groups naming AI agents were touched. Wildcard rules were left exactly as they were.',
          },
          json_ld: {
            insert_into: 'the <head> of your homepage (and one per page type for Article/Product pages)',
            content: jsonLd,
            types: ['Organization'],
            grounded_in: {
              name: siteName === new URL(origin).hostname ? 'hostname (no og:site_name or <title>)' : 'og:site_name or <title>',
              url: 'the audited origin',
              description: siteDesc ? 'meta description' : null,
              logo: logo ? 'og:image' : null,
              sameAs: sameAs.length ? 'outbound links to profile sites on the homepage' : null,
            },
            omitted,
            note: 'Fields that could not be grounded in something the page declares are omitted rather than guessed. Structured data is a machine-readable claim about an organisation; an invented one is a different kind of wrong from padded prose.',
          },
        },
        discovered: {
          sitemap: sitemapUrl,
          sitemap_source: sitemapUrl ? (declaredSitemap ? 'declared in robots.txt' : 'the conventional /sitemap.xml path') : null,
          candidates_found: candidates.length,
          links_checked: checked.length,
          links_verified: verified.length,
        },
        next_step: `Deploy these three, then POST ${cfg.resourceBaseUrl || 'https://animica.dev'}/x402/geo/audit with the same URL to confirm the score moved. The audit is deterministic, so any change in the number is a change you made.`,
        generated_at: new Date(now()).toISOString(),
      } };
    },
  };
}

module.exports = { createGeoFixProduct, BOILERPLATE, buildRobots, unifiedDiff, sitemapLocs, pageLinks, socialLinks, SUMMARY_SCHEMA };
