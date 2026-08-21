'use strict';
/**
 * GEO fix tests.
 *
 * The claims this product makes are what get tested hardest: that nothing is
 * invented (no model-hallucinated URL survives, no unverified link is
 * published, no ungrounded JSON-LD field is emitted), and that the robots.txt
 * rewrite is CONSERVATIVE — it must never silently open paths a wildcard rule
 * was protecting, because doing that would be a far worse outcome than the
 * problem it is fixing.
 */

const { test } = require('node:test');
const assert = require('node:assert/strict');

const { createGeoFixProduct, buildRobots, unifiedDiff, sitemapLocs, pageLinks, socialLinks } = require('../src/products/geo-fix');
const { loadGatewayConfig } = require('../src/config');

const cfg = loadGatewayConfig(process.env);
const publicLookup = async () => [{ address: '93.184.216.34', family: 4 }];

/**
 * A fetch Response stand-in. It needs BOTH `body` (an async iterable, which
 * readCapped streams) and `json()` (which the inference engine calls) — a
 * fixture with only one of them makes the model appear permanently down and
 * silently exercises the fallback path instead of the one under test.
 */
function res(status, body, contentType = 'text/html', url = 'https://example.com/') {
  const buf = Buffer.from(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    url,
    headers: { get: (h) => (h.toLowerCase() === 'content-type' ? contentType : null) },
    body: (async function* () { yield buf; })(),
    json: async () => JSON.parse(body),
    text: async () => body,
  };
}

// ---------------------------------------------------------------------------
// robots.txt rewriting
// ---------------------------------------------------------------------------

test('buildRobots removes a group that names only AI agents', () => {
  const r = buildRobots(['User-agent: GPTBot', 'User-agent: ClaudeBot', 'Disallow: /', '', 'User-agent: *', 'Disallow: /admin'].join('\n'), {});
  assert.ok(r.excluded_agents_freed.includes('GPTBot') && r.excluded_agents_freed.includes('ClaudeBot'));
  assert.doesNotMatch(r.content, /User-agent: GPTBot\nUser-agent: ClaudeBot\nDisallow: \//);
  assert.match(r.content, /Disallow: \/admin/, 'unrelated rules must survive');
  assert.match(r.content, /User-agent: GPTBot/, 'the agent reappears in the new allow group');
  assert.match(r.content, /Allow: \//);
});

test('buildRobots leaves a mixed group alone rather than freeing an unrelated crawler', () => {
  // Dropping this group would also un-block SomeSpamBot, which nobody asked for.
  const r = buildRobots(['User-agent: GPTBot', 'User-agent: SomeSpamBot', 'Disallow: /'].join('\n'), {});
  assert.match(r.content, /User-agent: SomeSpamBot/);
  assert.match(r.content, /Disallow: \//, 'the original group must remain intact');
});

test('buildRobots never edits a wildcard rule, and says so', () => {
  const r = buildRobots(['User-agent: *', 'Disallow: /'].join('\n'), {});
  assert.deepEqual(r.excluded_agents_freed, [], 'nothing was freed by editing a wildcard');
  assert.ok(r.still_blocked_by_wildcard.length > 0, 'the wildcard block must be reported, not silently fixed');
  assert.match(r.content, /User-agent: \*\nDisallow: \//, 'the site\'s blanket policy is left exactly as written');
});

test('buildRobots adds a Sitemap line only when one is absent', () => {
  const without = buildRobots('User-agent: *\nAllow: /\n', { sitemapUrl: 'https://example.com/sitemap.xml' });
  assert.match(without.content, /^Sitemap: https:\/\/example\.com\/sitemap\.xml$/m);
  const withOne = buildRobots('User-agent: *\nAllow: /\nSitemap: https://example.com/custom.xml\n', { sitemapUrl: 'https://example.com/sitemap.xml' });
  assert.equal((withOne.content.match(/^Sitemap:/gm) || []).length, 1, 'must not add a second Sitemap line');
  assert.match(withOne.content, /custom\.xml/);
});

test('buildRobots creates a file from nothing when there is no robots.txt', () => {
  const r = buildRobots(null, { sitemapUrl: 'https://example.com/sitemap.xml' });
  assert.equal(r.action, 'create');
  assert.match(r.content, /User-agent: GPTBot/);
});

test('unifiedDiff produces an applicable diff and nothing when unchanged', () => {
  const d = unifiedDiff('a\nb\nc', 'a\nX\nc');
  assert.match(d, /^--- a\/robots\.txt/m);
  assert.match(d, /^-b$/m);
  assert.match(d, /^\+X$/m);
  assert.equal(unifiedDiff('same\ntext', 'same\ntext'), null, 'an unchanged file has no diff');
});

// ---------------------------------------------------------------------------
// Extraction helpers
// ---------------------------------------------------------------------------

test('link and sitemap extraction stays same-origin and drops junk hrefs', () => {
  const html = '<a href="/a">A</a><a href="https://evil.example/x">X</a><a href="mailto:a@b.c">M</a><a href="#frag">F</a><a href="/b?q=1">B</a>';
  assert.deepEqual(pageLinks(html, 'https://example.com'), ['https://example.com/a', 'https://example.com/b?q=1']);
  assert.deepEqual(sitemapLocs('<urlset><url><loc>https://example.com/p</loc></url></urlset>'), ['https://example.com/p']);
});

test('socialLinks picks profile hosts only', () => {
  const html = '<a href="https://github.com/acme">g</a><a href="https://cdn.example.net/x.js">c</a><a href="https://x.com/acme">x</a>';
  const s = socialLinks(html, 'https://example.com');
  assert.deepEqual(s.sort(), ['https://github.com/acme', 'https://x.com/acme']);
});

// ---------------------------------------------------------------------------
// End to end
// ---------------------------------------------------------------------------

const HOME = `<html><head><title>Acme</title>
<meta name="description" content="Acme builds widgets.">
<meta property="og:site_name" content="Acme Inc">
<meta property="og:image" content="/logo.png">
</head><body><h1>Acme</h1>
<a href="/docs">Docs</a><a href="/pricing">Pricing</a><a href="/gone">Gone</a>
<a href="https://github.com/acme">GitHub</a>
<p>${'Acme builds widgets for industrial use. '.repeat(20)}</p></body></html>`;

function fixture({ modelReply = null, modelStatus = 200, robots = 'User-agent: *\nAllow: /\n' } = {}) {
  const seen = [];
  const impl = async (url, init) => {
    if (url.includes('/v1/chat/completions')) {
      seen.push('model');
      if (modelStatus !== 200) return res(modelStatus, '{}', 'application/json');
      return res(200, JSON.stringify({
        model: 'test-model',
        choices: [{ message: { content: JSON.stringify(modelReply) } }],
      }), 'application/json');
    }
    const p = new URL(url).pathname;
    seen.push(p);
    if (p === '/robots.txt') return res(200, robots, 'text/plain');
    if (p === '/sitemap.xml') return res(404, 'no', 'text/plain');
    if (p === '/gone') return res(404, 'not found', 'text/html');
    if (p === '/docs') return res(200, '<html><head><title>Docs</title><meta name="description" content="How to use Acme."></head><body>d</body></html>');
    if (p === '/pricing') return res(200, '<html><head><title>Pricing</title></head><body>p</body></html>');
    if (p === '/') return res(200, HOME);
    return res(404, 'x', 'text/plain');
  };
  return { impl, seen };
}

async function run(f, url = 'https://example.com/') {
  const p = createGeoFixProduct({ cfg, fetchImpl: f.impl, lookup: publicLookup });
  const out = await p.handler({ params: p.validate({ json: { url, max_links: 5 } }) });
  assert.equal(out.status, 200);
  assert.ok(out.bodyObj, 'must return { status, bodyObj }');
  return out.bodyObj;
}

test('a URL the model invented is discarded, and a 404 link is never published', async () => {
  const f = fixture({
    modelReply: {
      summary: 'Acme builds widgets for industrial customers and documents how to use them.',
      pages: [
        { url: 'https://example.com/docs', answers: 'How do I use Acme?' },
        { url: 'https://example.com/totally-made-up', answers: 'Invented page' },
        { url: 'https://example.com/gone', answers: 'A page that 404s' },
      ],
    },
  });
  const d = await run(f);
  const llms = d.artifacts.llms_txt.content;

  assert.doesNotMatch(llms, /totally-made-up/, 'a URL the model invented must never reach the file');
  assert.doesNotMatch(llms, /\/gone/, 'a link that 404d must be dropped');
  assert.match(llms, /How do I use Acme\?/, 'a grounded note survives');
  assert.equal(d.artifacts.llms_txt.links_dropped, 1);
  assert.equal(d.artifacts.llms_txt.links_verified, 2);
});

test('every published link was actually fetched', async () => {
  const f = fixture({ modelReply: { summary: 'Acme builds widgets and documents them.', pages: [] } });
  const d = await run(f);
  const urls = [...d.artifacts.llms_txt.content.matchAll(/\]\((https?:\/\/[^)]+)\)/g)].map((m) => m[1]);
  assert.ok(urls.length > 0);
  for (const u of urls) {
    assert.ok(f.seen.includes(new URL(u).pathname), `${u} was published without ever being fetched`);
  }
});

test('a model outage still yields all three artifacts, sourced honestly', async () => {
  const f = fixture({ modelStatus: 503 });
  const d = await run(f);
  assert.match(d.artifacts.llms_txt.summary_source, /model was unavailable/);
  assert.match(d.artifacts.llms_txt.content, /Acme builds widgets\./, "falls back to the site's own description");
  assert.ok(d.artifacts.robots_txt.content.length > 0);
  assert.ok(d.artifacts.json_ld.content.includes('"@type": "Organization"'));
});

test('JSON-LD omits what it cannot ground, and grounds what it can', async () => {
  const f = fixture({ modelReply: { summary: 'Acme builds widgets for industry.', pages: [] } });
  const d = await run(f);
  const ld = JSON.parse(d.artifacts.json_ld.content.replace(/<\/?script[^>]*>/g, ''));
  assert.equal(ld.name, 'Acme Inc', 'og:site_name wins over <title>');
  assert.equal(ld.description, 'Acme builds widgets.');
  assert.equal(ld.logo, 'https://example.com/logo.png', 'og:image is resolved against the origin');
  assert.deepEqual(ld.sameAs, ['https://github.com/acme']);

  // Now a page declaring none of it: those fields must be absent, not invented.
  const bare = fixture({ modelReply: { summary: 'A site.', pages: [] } });
  bare.impl = (async (orig) => orig)(bare.impl);
  const p = createGeoFixProduct({
    cfg, lookup: publicLookup,
    fetchImpl: async (url, init) => {
      if (url.includes('/v1/chat/completions')) return bare.impl(url, init);
      const path = new URL(url).pathname;
      if (path === '/') return res(200, '<html><head><title>Bare</title></head><body>hi</body></html>');
      return res(404, 'x', 'text/plain');
    },
  });
  const out = await p.handler({ params: p.validate({ json: { url: 'https://example.com/' } }) });
  const ld2 = JSON.parse(out.bodyObj.artifacts.json_ld.content.replace(/<\/?script[^>]*>/g, ''));
  assert.equal(ld2.description, undefined);
  assert.equal(ld2.logo, undefined);
  assert.equal(ld2.sameAs, undefined);
  assert.equal(out.bodyObj.artifacts.json_ld.omitted.length, 3, 'each omission is stated');
});

test('the sitemap declared in robots.txt is preferred over the conventional path', async () => {
  const f = fixture({
    modelReply: { summary: 'Acme builds widgets.', pages: [] },
    robots: 'User-agent: *\nAllow: /\nSitemap: https://example.com/sitemap-index.xml\n',
  });
  const base = f.impl;
  f.impl = async (url, init) => {
    if (new URL(url).pathname === '/sitemap-index.xml') {
      f.seen.push('/sitemap-index.xml');
      return res(200, '<urlset><url><loc>https://example.com/docs</loc></url></urlset>', 'application/xml');
    }
    return base(url, init);
  };
  const d = await run(f);
  assert.equal(d.discovered.sitemap, 'https://example.com/sitemap-index.xml');
  assert.equal(d.discovered.sitemap_source, 'declared in robots.txt');
  assert.ok(!f.seen.includes('/sitemap.xml'), 'the conventional path is not probed when one is declared');
});

test('an unreachable origin is refused, not charged for', async () => {
  const p = createGeoFixProduct({ cfg, fetchImpl: async () => { throw new Error('ECONNREFUSED'); }, lookup: publicLookup });
  await assert.rejects(
    () => p.handler({ params: p.validate({ json: { url: 'https://example.com/' } }) }),
    (e) => { assert.match(e.body.detail, /Nothing was charged/); return true; },
  );
});

test('max_links is bounded', () => {
  const p = createGeoFixProduct({ cfg, fetchImpl: async () => res(200, ''), lookup: publicLookup });
  assert.throws(() => p.validate({ json: { url: 'https://example.com', max_links: 0 } }));
  assert.throws(() => p.validate({ json: { url: 'https://example.com', max_links: 9999 } }));
  assert.equal(p.validate({ json: { url: 'https://example.com', max_links: 5 } }).maxLinks, 5);
});
