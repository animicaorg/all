#!/usr/bin/env node
/**
 * check_links.mjs — Dead link checker for CI
 *
 * Modes (pick one):
 *  1) --sitemap <file|url>      # Parse sitemap.xml and check all <loc> pages + their anchors
 *  2) --start   <url>           # Crawl from a start URL (same-origin), discover internal pages
 *  3) --dir     <path> --site <url>  # Scan local .html files and resolve relative links via --site
 *
 * What it checks:
 *  - <a href="..."> (http/https, relative, root-absolute)
 *  - <img src="..."> (broken images)
 *  - Same-page anchors (#id) and cross-page hash anchors (/page#id)
 *  - Skips: mailto:, tel:, javascript:, data:, blob:
 *
 * Exit codes:
 *  - 0  success (no broken links)
 *  - 1  broken links found
 *  - 2  usage / configuration error
 *
 * Examples:
 *   node website/scripts/check_links.mjs --sitemap website/public/sitemap.xml
 *   node website/scripts/check_links.mjs --start http://localhost:4321 --max-pages 200
 *   node website/scripts/check_links.mjs --dir dist --site https://animica.example
 *
 * Options:
 *   --concurrency N   (default 16)
 *   --timeout MS      (default 10000)
 *   --retries N       (default 2)
 *   --include REGEX   (filter pages to check; may repeat)
 *   --exclude REGEX   (exclude pages; may repeat)
 *   --max-pages N     (crawl mode)
 *   --user-agent UA   (custom UA)
 *   --verbose         (log progress)
 */

import { promises as fs } from "node:fs";
import path from "node:path";
import url from "node:url";

const UA_DEFAULT = "Animica-LinkCheck/1.0 (+https://animica.example)";
const OK_RANGE = { min: 200, max: 399 };

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));

/* --------------------------------- CLI ---------------------------------- */

function parseArgs(argv) {
  const opts = {
    sitemap: null,
    start: null,
    dir: null,
    site: null,
    concurrency: 16,
    timeout: 10_000,
    retries: 2,
    include: [],
    exclude: [],
    maxPages: 500,
    userAgent: process.env.LINKCHECK_UA || UA_DEFAULT,
    verbose: false,
  };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--sitemap" && argv[i + 1]) opts.sitemap = argv[++i];
    else if (a === "--start" && argv[i + 1]) opts.start = argv[++i];
    else if (a === "--dir" && argv[i + 1]) opts.dir = argv[++i];
    else if (a === "--site" && argv[i + 1]) opts.site = argv[++i];
    else if (a === "--concurrency" && argv[i + 1]) opts.concurrency = Number(argv[++i]);
    else if (a === "--timeout" && argv[i + 1]) opts.timeout = Number(argv[++i]);
    else if (a === "--retries" && argv[i + 1]) opts.retries = Number(argv[++i]);
    else if (a === "--include" && argv[i + 1]) opts.include.push(argv[++i]);
    else if (a === "--exclude" && argv[i + 1]) opts.exclude.push(argv[++i]);
    else if (a === "--max-pages" && argv[i + 1]) opts.maxPages = Number(argv[++i]);
    else if (a === "--user-agent" && argv[i + 1]) opts.userAgent = argv[++i];
    else if (a === "--verbose") opts.verbose = true;
    else if (a === "-h" || a === "--help") usage(0);
  }
  // Validate mutually exclusive mode
  const modes = [opts.sitemap && "sitemap", opts.start && "start", opts.dir && "dir"].filter(Boolean);
  if (modes.length !== 1) {
    console.error("✖ Must specify exactly one of --sitemap, --start, or --dir (with --site).\n");
    usage(2);
  }
  if (opts.dir && !opts.site) {
    console.error("✖ --dir mode requires --site (base URL to resolve relative paths).");
    usage(2);
  }
  return opts;
}

function usage(code = 0) {
  console.log(`Usage:
  # Sitemap mode
  node ${path.basename(process.argv[1])} --sitemap website/public/sitemap.xml [--concurrency 16] [--timeout 10000]

  # Crawl mode
  node ${path.basename(process.argv[1])} --start http://localhost:4321 [--max-pages 500]

  # Static directory mode
  node ${path.basename(process.argv[1])} --dir dist --site https://animica.example

  Common flags:
  --include REGEX  --exclude REGEX  --retries N  --user-agent "UA"  --verbose
`);
  process.exit(code);
}

const OPTS = parseArgs(process.argv);

/* ------------------------------ Utilities ------------------------------- */

function isHttpUrl(x) {
  try { const u = new URL(x); return u.protocol === "http:" || u.protocol === "https:"; } catch { return false; }
}
function joinUrl(base, rel) {
  try { return new URL(rel, base).toString(); } catch { return null; }
}
function sameOrigin(a, b) {
  try { const A = new URL(a), B = new URL(b); return A.origin === B.origin; } catch { return false; }
}
function normalizePath(u) { try { const x = new URL(u); x.hash = ""; return x.toString().replace(/\/+$/, ""); } catch { return u; } }
function sleep(ms){ return new Promise(r=>setTimeout(r, ms)); }

function makeFilter(include, exclude) {
  const inc = include.map((r) => new RegExp(r));
  const exc = exclude.map((r) => new RegExp(r));
  return (href) => {
    const okInc = inc.length === 0 || inc.some((re) => re.test(href));
    const okExc = !exc.some((re) => re.test(href));
    return okInc && okExc;
  };
}

const shouldCheck = makeFilter(OPTS.include, OPTS.exclude);

const SKIP_SCHEMES = /^(mailto:|tel:|javascript:|data:|blob:)/i;

/* ------------------------------ HTML parse ------------------------------ */

function extractLinks(html, baseUrl) {
  // Find <a href=""> and <img src=""> values (simple regex is fine for CI)
  const anchors = [];
  const images = [];

  // a href
  const reHref = /<a\s+[^>]*href\s*=\s*"(.*?)"[^>]*>/gim;
  let m;
  while ((m = reHref.exec(html))) {
    const raw = m[1].trim();
    if (SKIP_SCHEMES.test(raw) || raw === "") continue;
    const abs = joinUrl(baseUrl, raw);
    if (!abs) continue;
    anchors.push({ raw, abs });
  }

  // img src
  const reImg = /<img\s+[^>]*src\s*=\s*"(.*?)"[^>]*>/gim;
  while ((m = reImg.exec(html))) {
    const raw = m[1].trim();
    if (SKIP_SCHEMES.test(raw) || raw === "") continue;
    const abs = joinUrl(baseUrl, raw);
    if (!abs) continue;
    images.push({ raw, abs });
  }

  return { anchors, images };
}

function parseIdsForAnchors(html) {
  // Collect ids and named anchors to validate #hash
  const ids = new Set();
  const reId = /\s(id|name)\s*=\s*"([^"]+)"/gim;
  let m;
  while ((m = reId.exec(html))) ids.add(m[2]);
  return ids;
}

/* ------------------------------ HTTP fetch ------------------------------ */

async function fetchWithTimeout(urlStr, { method = "HEAD", timeout = OPTS.timeout, ua = OPTS.userAgent } = {}) {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeout);
  try {
    const res = await fetch(urlStr, { method, redirect: "manual", signal: controller.signal, headers: { "user-agent": ua } });
    return res;
  } finally {
    clearTimeout(t);
  }
}

async function checkUrl(urlStr, { retries = OPTS.retries } = {}) {
  let lastErr = null;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      // Some servers reject HEAD; fallback to GET on 405/501 or if fetch throws
      let res = await fetchWithTimeout(urlStr, { method: "HEAD" });
      if (res.status === 405 || res.status === 501) res = await fetchWithTimeout(urlStr, { method: "GET" });
      return { ok: res.status >= OK_RANGE.min && res.status <= OK_RANGE.max, status: res.status, finalUrl: urlStr };
    } catch (e) {
      lastErr = e;
      await sleep(200 * (attempt + 1));
    }
  }
  return { ok: false, status: 0, error: lastErr?.name === "AbortError" ? "timeout" : (lastErr?.message || "network-error") };
}

/* ------------------------------ Work queue ------------------------------ */

function makePool(limit) {
  let running = 0;
  const queue = [];
  const runNext = () => {
    if (running >= limit || queue.length === 0) return;
    const { fn, resolve, reject } = queue.shift();
    running++;
    Promise.resolve()
      .then(fn)
      .then((v) => { running--; resolve(v); runNext(); })
      .catch((e) => { running--; reject(e); runNext(); });
  };
  return function run(fn) {
    return new Promise((resolve, reject) => {
      queue.push({ fn, resolve, reject });
      runNext();
    });
  };
}

/* ------------------------------- Sitemap -------------------------------- */

async function readSitemap(input) {
  if (!input) return [];
  let xml;
  if (isHttpUrl(input)) {
    const res = await fetchWithTimeout(input, { method: "GET" });
    if (!(res.status >= 200 && res.status < 300)) throw new Error(`Sitemap fetch failed: ${res.status}`);
    xml = await res.text();
  } else {
    xml = await fs.readFile(input, "utf8");
  }
  const locRe = /<loc>\s*([^<\s]+)\s*<\/loc>/gi;
  const urls = [];
  let m;
  while ((m = locRe.exec(xml))) urls.push(m[1].trim());
  return urls;
}

/* --------------------------------- Crawl -------------------------------- */

async function crawl(startUrl, maxPages) {
  const origin = new URL(startUrl).origin;
  const toVisit = [startUrl];
  const seen = new Set();
  const pages = [];

  while (toVisit.length && pages.length < maxPages) {
    const u = toVisit.shift();
    const canon = normalizePath(u);
    if (seen.has(canon)) continue;
    seen.add(canon);

    try {
      const res = await fetchWithTimeout(u, { method: "GET" });
      if (!(res.status >= OK_RANGE.min && res.status <= OK_RANGE.max)) continue;
      const html = await res.text();
      pages.push({ url: u, html });

      const { anchors } = extractLinks(html, u);
      for (const a of anchors) {
        if (!sameOrigin(origin, a.abs)) continue;
        const absNoHash = a.abs.split("#")[0];
        if (!seen.has(normalizePath(absNoHash)) && shouldCheck(absNoHash)) toVisit.push(absNoHash);
      }
    } catch {
      // ignore fetch errors during discovery; validator will catch on check
    }
  }
  return pages;
}

/* ------------------------------ Local scan ------------------------------- */

async function scanDirHtml(dir, siteBase) {
  const root = path.resolve(dir);
  const files = [];
  async function walk(d) {
    for (const ent of await fs.readdir(d, { withFileTypes: true })) {
      const full = path.join(d, ent.name);
      if (ent.isDirectory()) await walk(full);
      else if (ent.isFile() && ent.name.toLowerCase().endsWith(".html")) files.push(full);
    }
  }
  await walk(root);

  const pages = [];
  for (const f of files) {
    const rel = "/" + path.relative(root, f).replace(/\\/g, "/");
    const urlAbs = joinUrl(siteBase, rel);
    const html = await fs.readFile(f, "utf8");
    pages.push({ url: urlAbs, html });
  }
  return pages;
}

/* ------------------------------- Validate -------------------------------- */

async function validatePages(pages) {
  const pool = makePool(OPTS.concurrency);
  const results = {
    checked: 0,
    broken: [],
  };
  const cache = new Map(); // url -> {ok,status}

  async function ensure(urlStr) {
    if (cache.has(urlStr)) return cache.get(urlStr);
    const res = await checkUrl(urlStr);
    cache.set(urlStr, res);
    return res;
  }

  // Pre-check all page URLs themselves
  for (const p of pages) {
    const pageRes = await ensure(p.url);
    if (!pageRes.ok) {
      results.broken.push({ page: p.url, link: p.url, kind: "page", reason: `HTTP ${pageRes.status || pageRes.error}` });
    }
  }

  // For anchor validation we need page HTML
  const anchorIdCache = new Map(); // pageUrl(no hash) -> Set(ids)
  for (const p of pages) {
    const ids = parseIdsForAnchors(p.html);
    anchorIdCache.set(p.url.split("#")[0], ids);
  }

  const tasks = [];

  for (const p of pages) {
    const { anchors, images } = extractLinks(p.html, p.url);
    const pageBase = p.url.split("#")[0];

    // Filter links to check by include/exclude
    const filteredAnchors = anchors.filter(a => shouldCheck(a.abs));
    const filteredImages = images.filter(i => shouldCheck(i.abs));

    for (const a of filteredAnchors) {
      tasks.push(pool(async () => {
        const [target, hash] = a.abs.split("#");
        // Only follow HTTP(S) resources
        if (!isHttpUrl(target)) return;
        const res = await ensure(target);
        results.checked++;
        if (!res.ok) {
          results.broken.push({ page: p.url, link: a.abs, kind: "link", reason: `HTTP ${res.status || res.error}` });
          if (OPTS.verbose) console.log(`✖ ${p.url} -> ${a.abs} (${res.status || res.error})`);
          return;
        }
        // If anchor hash exists and is same-origin, check presence of id/name
        if (hash && sameOrigin(pageBase, target)) {
          const idSet = anchorIdCache.get(target) || new Set();
          if (!idSet.has(hash)) {
            results.broken.push({ page: p.url, link: a.abs, kind: "anchor", reason: `missing #${hash}` });
            if (OPTS.verbose) console.log(`✖ ${p.url} -> ${a.abs} (missing #${hash})`);
          } else if (OPTS.verbose) {
            console.log(`✓ ${p.url} -> ${a.abs}`);
          }
        } else if (OPTS.verbose) {
          console.log(`✓ ${p.url} -> ${a.abs}`);
        }
      }));
    }

    for (const img of filteredImages) {
      tasks.push(pool(async () => {
        const res = await ensure(img.abs);
        results.checked++;
        if (!res.ok) {
          results.broken.push({ page: p.url, link: img.abs, kind: "image", reason: `HTTP ${res.status || res.error}` });
          if (OPTS.verbose) console.log(`✖ ${p.url} -> [img] ${img.abs} (${res.status || res.error})`);
        } else if (OPTS.verbose) {
          console.log(`✓ ${p.url} -> [img] ${img.abs}`);
        }
      }));
    }
  }

  // Wait for all tasks
  await Promise.all(tasks);
  return results;
}

/* --------------------------------- Main ---------------------------------- */

(async function main() {
  const startTime = Date.now();
  let pages = [];

  if (OPTS.sitemap) {
    const list = await readSitemap(OPTS.sitemap);
    if (list.length === 0) {
      console.warn("⚠ sitemap contained 0 URLs.");
    }
    // Fetch each page HTML for anchor scanning
    const pool = makePool(OPTS.concurrency);
    const jobs = list
      .filter(u => isHttpUrl(u) && shouldCheck(u))
      .map(u => pool(async () => {
        try {
          const res = await fetchWithTimeout(u, { method: "GET" });
          const html = await res.text().catch(() => "");
          return { url: u, html };
        } catch {
          return { url: u, html: "" };
        }
      }));
    pages = (await Promise.all(jobs)).filter(Boolean);
  } else if (OPTS.start) {
    pages = await crawl(OPTS.start, OPTS.maxPages);
  } else if (OPTS.dir) {
    pages = await scanDirHtml(OPTS.dir, OPTS.site);
  }

  if (pages.length === 0) {
    console.error("✖ No pages discovered to check.");
    process.exit(2);
  }

  if (OPTS.verbose) console.log(`Discovered ${pages.length} page(s); checking...`);
  const results = await validatePages(pages);
  const dur = ((Date.now() - startTime) / 1000).toFixed(2);

  // Reporting
  console.log(`\nLink Check Summary:
  pages:   ${pages.length}
  checked: ${results.checked}
  broken:  ${results.broken.length}
  time:    ${dur}s
  mode:    ${OPTS.sitemap ? "sitemap" : OPTS.start ? "crawl" : "dir"}
`);

  if (results.broken.length > 0) {
    console.log("Broken links:");
    for (const b of results.broken) {
      console.log(` - [${b.kind}] ${b.page} -> ${b.link} :: ${b.reason}`);
    }
    process.exit(1);
  } else {
    console.log("✓ No broken links found.");
    process.exit(0);
  }
})().catch((err) => {
  console.error("✖ check_links failed:");
  console.error(err?.stack || err?.message || String(err));
  process.exit(2);
});
