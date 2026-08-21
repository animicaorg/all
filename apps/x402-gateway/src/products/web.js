'use strict';
/**
 * FETCH & EXTRACT - a URL in, clean text out.
 *
 * The most-wanted commodity in agent tooling: models need web content as
 * text, not HTML with nav bars and cookie banners. It is also the most
 * DANGEROUS product here, because the caller chooses the URL. Everything
 * below exists because of that.
 *
 * SSRF RULES (the URL is attacker-supplied BY DESIGN):
 *   - http/https only. No file:, gopher:, data:, ftp:.
 *   - The hostname is RESOLVED and every resulting address checked against
 *     private/loopback/link-local/CGNAT ranges. Checking the NAME is not
 *     enough: "localtest.me" and many others resolve to 127.0.0.1.
 *   - Redirects are followed MANUALLY and every hop is re-resolved and
 *     re-checked. A public URL that 302s to 169.254.169.254 is the classic
 *     cloud-metadata escape, and only per-hop checking catches it.
 *   - We block narrow /24s, never enclosing /16s. Blocking 192.0.0.0/16
 *     wholesale once blocked Automattic (192.0.78.0/24) and silently broke
 *     every WordPress.com-hosted customer. Over-blocking is a real cost.
 *   - Size is capped WHILE STREAMING, so a huge response cannot exhaust
 *     memory before the cap is noticed.
 */

const dns = require('node:dns').promises;
const net = require('node:net');
const { ProductError } = require('./errors');

function bad(detail, error = 'invalid_params', extra) {
  return new ProductError(detail, { body: Object.assign({ error, detail }, extra) });
}

/** Is this IP one we must never fetch from? Explicit, narrow ranges only. */
function isBlockedAddress(ip) {
  if (net.isIPv4(ip)) {
    const p = ip.split('.').map(Number);
    if (p[0] === 0) return 'unspecified';
    if (p[0] === 10) return 'private-10/8';
    if (p[0] === 127) return 'loopback';
    if (p[0] === 169 && p[1] === 254) return 'link-local (cloud metadata lives here)';
    if (p[0] === 172 && p[1] >= 16 && p[1] <= 31) return 'private-172.16/12';
    if (p[0] === 192 && p[1] === 168) return 'private-192.168/16';
    if (p[0] === 100 && p[1] >= 64 && p[1] <= 127) return 'cgnat-100.64/10';
    // Narrow /24s ONLY. Not 192.0.0.0/16 - 192.0.78.0/24 is Automattic.
    if (p[0] === 192 && p[1] === 0 && p[2] === 0) return 'ietf-protocol-192.0.0/24';
    if (p[0] === 192 && p[1] === 0 && p[2] === 2) return 'testnet-192.0.2/24';
    if (p[0] === 198 && p[1] === 51 && p[2] === 100) return 'testnet-198.51.100/24';
    if (p[0] === 203 && p[1] === 0 && p[2] === 113) return 'testnet-203.0.113/24';
    if (p[0] === 198 && (p[1] === 18 || p[1] === 19)) return 'benchmark-198.18/15';
    if (p[0] >= 224) return 'multicast/reserved';
    return null;
  }
  if (net.isIPv6(ip)) {
    const s = ip.toLowerCase().replace(/^\[|\]$/g, '');
    if (s === '::' || s === '::1') return 'loopback/unspecified';
    if (s.startsWith('fe80')) return 'link-local';
    if (/^f[cd]/.test(s)) return 'unique-local';
    const m = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/.exec(s);
    if (m) return isBlockedAddress(m[1]);
    return null;
  }
  return 'unparseable';
}

/** Resolve a hostname and refuse if ANY answer is a blocked address. */
async function resolveSafely(hostname, lookup = dns.lookup) {
  if (net.isIP(hostname)) {
    const why = isBlockedAddress(hostname);
    if (why) throw bad('refusing to fetch ' + hostname + ': ' + why, 'blocked_address');
    return [hostname];
  }
  let answers;
  try {
    answers = await lookup(hostname, { all: true });
  } catch (e) {
    throw bad('cannot resolve ' + hostname + ': ' + (e.code || e.message), 'dns_failure');
  }
  if (!answers.length) throw bad(hostname + ' resolved to nothing', 'dns_failure');
  for (const a of answers) {
    const why = isBlockedAddress(a.address);
    if (why) {
      throw bad(
        'refusing to fetch ' + hostname + ': it resolves to ' + a.address + ' (' + why +
        '). This endpoint reaches the public internet only.',
        'blocked_address'
      );
    }
  }
  return answers.map((a) => a.address);
}

function parseTarget(raw) {
  let u;
  try {
    u = new URL(String(raw));
  } catch {
    throw bad('url must be an absolute http(s) URL', 'invalid_url');
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') {
    throw bad('only http and https are fetchable, not ' + u.protocol, 'unsupported_scheme');
  }
  if (u.username || u.password) throw bad('credentials in the URL are not accepted', 'invalid_url');
  return u;
}

function safeCodePoint(n) {
  if (!Number.isFinite(n) || n < 0 || n > 0x10ffff) return '';
  try { return String.fromCodePoint(n); } catch { return ''; }
}

function decodeEntities(s) {
  const named = {
    amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ', mdash: '—',
    ndash: '–', hellip: '…', rsquo: '’', lsquo: '‘',
    ldquo: '“', rdquo: '”', copy: '©', reg: '®',
  };
  return String(s)
    .replace(/&#x([0-9a-fA-F]+);/g, (_, h) => safeCodePoint(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_, d) => safeCodePoint(parseInt(d, 10)))
    .replace(/&([a-zA-Z]+);/g, (m, n) => (named[n] !== undefined ? named[n] : m));
}

/** Strip HTML to readable text. Deliberately dependency-free. */
function htmlToText(html) {
  let s = String(html);
  s = s.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, ' ');
  s = s.replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, ' ');
  s = s.replace(/<noscript\b[^>]*>[\s\S]*?<\/noscript>/gi, ' ');
  s = s.replace(/<svg\b[^>]*>[\s\S]*?<\/svg>/gi, ' ');
  s = s.replace(/<!--[\s\S]*?-->/g, ' ');
  s = s.replace(/<(nav|header|footer|aside|form)\b[^>]*>[\s\S]*?<\/\1>/gi, ' ');
  s = s.replace(/<\/(p|div|section|article|h[1-6]|li|tr|blockquote|pre)>/gi, '\n');
  s = s.replace(/<br\s*\/?>/gi, '\n');
  s = s.replace(/<li\b[^>]*>/gi, '\n- ');
  s = s.replace(/<[^>]+>/g, ' ');
  s = decodeEntities(s);
  s = s.replace(/[ \t\f\v]+/g, ' ');
  s = s.replace(/\n\s*\n\s*\n+/g, '\n\n');
  return s.trim();
}

function extractTitle(html) {
  const m = /<title[^>]*>([\s\S]*?)<\/title>/i.exec(String(html));
  return m ? decodeEntities(m[1]).replace(/\s+/g, ' ').trim().slice(0, 300) : null;
}

function extractMeta(html, name) {
  const re = new RegExp('<meta[^>]+(?:name|property)=["\']' + name + '["\'][^>]*>', 'i');
  const tag = re.exec(String(html));
  if (!tag) return null;
  const c = /content=["']([^"']*)["']/i.exec(tag[0]);
  return c ? decodeEntities(c[1]).trim().slice(0, 500) : null;
}

/** Read a response body with a hard byte cap enforced DURING streaming. */
async function readCapped(res, maxBytes) {
  const chunks = [];
  let total = 0;
  let truncated = false;
  for await (const chunk of res.body) {
    const buf = Buffer.from(chunk);
    if (total + buf.length > maxBytes) {
      chunks.push(buf.subarray(0, Math.max(0, maxBytes - total)));
      total = maxBytes;
      truncated = true;
      break;
    }
    chunks.push(buf);
    total += buf.length;
  }
  return { buffer: Buffer.concat(chunks), bytes: total, truncated };
}

function createFetchProduct({ cfg, fetchImpl = fetch, now = Date.now, lookup = dns.lookup }) {
  return {
    id: 'fetch_extract',
    title: 'Fetch & extract web page text',
    description:
      'Fetch a public web page and return clean readable text with its title and metadata - the form a model can actually use, with navigation, scripts and styling removed. Follows up to ' +
      cfg.fetchMaxRedirects + ' redirects, caps the download at ' + cfg.fetchMaxBytes +
      ' bytes and gives up after ' + cfg.fetchTimeoutMs +
      'ms. Reaches the PUBLIC internet only: the hostname is resolved and every redirect hop re-checked against private, loopback, link-local and CGNAT ranges, so this cannot read internal services or cloud metadata. Returns raw HTML too on request.',
    path: '/x402/web/fetch',
    routes: [{ method: 'POST', path: '/x402/web/fetch' }, { method: 'GET', path: '/x402/web/fetch' }],
    priceUsd: cfg.fetchPriceUsd,
    enabled: cfg.fetchEnabled,
    // The fetch IS the product: obtain it first, charge only if it worked.
    // A page that 404s or times out must cost the caller nothing.
    mode: 'execute-then-settle',
    mimeType: 'application/json',
    maxBodyBytes: 8192,
    outputSchema: {
      input: {
        type: 'http',
        method: 'POST',
        bodyType: 'json',
        bodyFields: {
          url: { type: 'string', required: true, description: 'absolute http(s) URL of a public page' },
          include_html: { type: 'boolean', required: false, description: 'also return raw HTML (default false)' },
          max_chars: { type: 'integer', required: false, description: 'truncate extracted text to this many characters' },
        },
        queryParams: { url: { type: 'string', description: 'same as body.url, for GET callers' } },
      },
      output: {
        type: 'json',
        description:
          'url, final_url, status, title, description, text, chars, bytes, body_truncated, text_truncated, content_type, fetched_at, redirects[], and html when requested',
      },
    },

    async availability() {
      return { available: true };
    },

    validate(ctx) {
      const body = ctx.json || {};
      const raw = (body && body.url) || ctx.query.get('url');
      if (!raw) throw bad('url is required', 'invalid_request');
      const u = parseTarget(raw);
      let maxChars = null;
      if (body.max_chars !== undefined && body.max_chars !== null) {
        if (!Number.isInteger(body.max_chars) || body.max_chars < 1) {
          throw bad('max_chars must be a positive integer', 'invalid_request');
        }
        maxChars = Math.min(body.max_chars, 5000000);
      }
      return { url: u, includeHtml: body.include_html === true, maxChars };
    },

    async handler(ctx) {
      const { url, includeHtml, maxChars } = ctx.params;
      const redirects = [];
      let current = url;
      let res = null;
      const deadline = now() + Number(cfg.fetchTimeoutMs);

      for (let hop = 0; hop <= Number(cfg.fetchMaxRedirects); hop++) {
        // EVERY hop re-resolved and re-checked - a public URL that redirects
        // into 169.254.169.254 is the standard metadata escape.
        await resolveSafely(current.hostname, lookup);
        const remaining = deadline - now();
        if (remaining <= 0) throw bad('timed out after ' + cfg.fetchTimeoutMs + 'ms', 'fetch_timeout');

        let r;
        try {
          r = await fetchImpl(current.toString(), {
            method: 'GET',
            redirect: 'manual',
            headers: {
              // Identify honestly. A crawler that lies about who it is gets
              // the whole gateway blocked, and rightly.
              'user-agent': 'AnimicaX402Fetch/1.0 (+https://animica.dev/x402)',
              accept: 'text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8',
              'accept-language': 'en',
            },
            signal: AbortSignal.timeout(remaining),
          });
        } catch (e) {
          const err = new Error('fetch failed: ' + e.message);
          err.retryable = /timeout|abort|ECONN|EAI_AGAIN|network/i.test(e.message);
          throw err;
        }

        if ([301, 302, 303, 307, 308].includes(r.status)) {
          const loc = r.headers.get('location');
          if (!loc) throw bad(r.status + ' redirect without a Location header', 'bad_redirect');
          let next;
          try {
            next = new URL(loc, current);
          } catch {
            throw bad('unfollowable redirect target: ' + loc, 'bad_redirect');
          }
          if (next.protocol !== 'http:' && next.protocol !== 'https:') {
            throw bad('redirect to a non-http scheme (' + next.protocol + ') refused', 'unsupported_scheme');
          }
          redirects.push({ from: current.toString(), to: next.toString(), status: r.status });
          current = next;
          continue;
        }
        res = r;
        break;
      }

      if (!res) throw bad('more than ' + cfg.fetchMaxRedirects + ' redirects', 'too_many_redirects', { redirects });
      if (!res.ok) {
        // An upstream error is data about the caller's URL, not our failure -
        // but they must not pay for it, so it is a ProductError raised before
        // settlement in execute-then-settle mode.
        throw bad('the page answered HTTP ' + res.status, 'upstream_status', {
          status: res.status,
          final_url: res.url || current.toString(),
        });
      }

      const contentType = String(res.headers.get('content-type') || '').toLowerCase();
      const { buffer, bytes, truncated } = await readCapped(res, Number(cfg.fetchMaxBytes));
      const head = buffer.subarray(0, 200).toString('utf8');
      const isHtml = contentType.includes('html') || /^\s*<(!doctype|html)/i.test(head);
      const rawText = buffer.toString('utf8');
      let text = isHtml ? htmlToText(rawText) : rawText.trim();
      let textTruncated = false;
      if (maxChars && text.length > maxChars) {
        text = text.slice(0, maxChars);
        textTruncated = true;
      }

      return {
        status: 200,
        bodyObj: {
          product: 'fetch_extract',
          url: url.toString(),
          final_url: res.url || current.toString(),
          status: res.status,
          content_type: contentType || null,
          title: isHtml ? extractTitle(rawText) : null,
          description: isHtml
            ? (extractMeta(rawText, 'description') || extractMeta(rawText, 'og:description'))
            : null,
          text,
          chars: text.length,
          bytes,
          // TWO different truncations, reported separately: we stopped
          // downloading at the byte cap, versus we trimmed text to the
          // max_chars you asked for. Collapsing them would hide a partial
          // page behind a parameter the caller chose.
          body_truncated: truncated,
          text_truncated: textTruncated,
          redirects,
          fetched_at: new Date(now()).toISOString(),
          html: includeHtml ? rawText : undefined,
          extraction: isHtml
            ? 'HTML stripped of script/style/nav/header/footer/aside/form; block elements became newlines; entities decoded'
            : 'served as-is (not HTML)',
        },
      };
    },
  };
}

module.exports = {
  createFetchProduct, isBlockedAddress, resolveSafely, htmlToText,
  extractTitle, extractMeta, parseTarget, readCapped, decodeEntities,
};
