#!/usr/bin/env node
/**
 * generate_sitemap.mjs
 *
 * Build a sitemap from Astro routes:
 *  - Scans `src/pages/**/*.astro` (excludes API & dynamic `[param]` routes)
 *  - Expands blog posts from `src/content/blog/**/*.mdx` into `/blog/<slug>`
 *  - Expands docs mdx from `src/docs/**/*.mdx` into `/docs/<slug>`
 *  - Writes `public/sitemap.xml` by default
 *
 * Usage:
 *   node website/scripts/generate_sitemap.mjs [--site https://example.com] [--out website/public/sitemap.xml]
 *
 * Env:
 *   SITE_URL=<url>            // fallback if --site not provided
 */

import { promises as fs } from "node:fs";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const SRC = path.join(ROOT, "src");
const PAGES = path.join(SRC, "pages");
const BLOG = path.join(SRC, "content", "blog");
const DOCS = path.join(SRC, "docs");

function parseArgs(argv) {
  const out = { site: null, out: null };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if ((a === "--site" || a === "-s") && argv[i + 1]) out.site = argv[++i];
    else if ((a === "--out" || a === "-o") && argv[i + 1]) out.out = argv[++i];
    else if (a === "-h" || a === "--help") {
      console.log(`Usage: ${path.basename(argv[1])} [--site https://example.com] [--out website/public/sitemap.xml]`);
      process.exit(0);
    }
  }
  return out;
}

const cli = parseArgs(process.argv);
const SITE = (cli.site || process.env.SITE_URL || "https://animica.example").replace(/\/+$/, "");
const OUTFILE = path.resolve(cli.out || path.join(ROOT, "public", "sitemap.xml"));

/* ------------------------------- FS helpers ------------------------------- */

async function statOrNull(p) {
  try { return await fs.stat(p); } catch { return null; }
}

async function existsDir(p) {
  const st = await statOrNull(p);
  return !!(st && st.isDirectory());
}

async function listFilesRecursive(dir, filterFn) {
  const results = [];
  async function walk(d) {
    let ents;
    try { ents = await fs.readdir(d, { withFileTypes: true }); }
    catch { return; }
    for (const ent of ents) {
      const full = path.join(d, ent.name);
      if (ent.isDirectory()) await walk(full);
      else if (ent.isFile()) {
        if (!filterFn || filterFn(full)) results.push(full);
      }
    }
  }
  await walk(dir);
  return results;
}

function mtimeIso(stats) {
  return (stats?.mtime ?? new Date()).toISOString();
}

/* ------------------------------ Route builders ---------------------------- */

function routeFromPageFile(fullPath) {
  // src/pages/foo/bar/index.astro => /foo/bar
  // src/pages/about.astro => /about
  const rel = path.relative(PAGES, fullPath).replace(/\\/g, "/");
  if (rel.startsWith("api/")) return null; // exclude API endpoints
  if (/\[[^\]]+\]/.test(rel)) return null; // dynamic param route (will be expanded separately if needed)
  if (rel.toLowerCase() === "404.astro") return null; // exclude 404
  if (!rel.endsWith(".astro")) return null;

  let route = "/" + rel.replace(/\.astro$/i, "");
  route = route.replace(/\/index$/i, ""); // drop trailing index
  if (route === "") route = "/";

  return route;
}

function slugFromFile(fp) {
  return path.basename(fp).replace(/\.(md|mdx)$/i, "");
}

/* --------------------------------- Build ---------------------------------- */

async function build() {
  const urls = new Map(); // route -> { loc, lastmod }

  // 1) Static astro pages (non-dynamic)
  if (await existsDir(PAGES)) {
    const pages = await listFilesRecursive(PAGES, (f) => f.endsWith(".astro"));
    for (const f of pages) {
      const route = routeFromPageFile(f);
      if (!route) continue;
      const st = await statOrNull(f);
      urls.set(route, { loc: SITE + route, lastmod: mtimeIso(st) });
    }
  }

  // 2) Blog posts -> /blog/<slug>
  if (await existsDir(BLOG)) {
    const posts = await listFilesRecursive(BLOG, (f) => /\.(md|mdx)$/i.test(f));
    for (const f of posts) {
      const slug = slugFromFile(f);
      const route = `/blog/${slug}`;
      const st = await statOrNull(f);
      urls.set(route, { loc: SITE + route, lastmod: mtimeIso(st) });
    }
  }

  // 3) Docs mdx -> /docs/<slug>
  if (await existsDir(DOCS)) {
    const docs = await listFilesRecursive(DOCS, (f) => /\.(md|mdx)$/i.test(f));
    for (const f of docs) {
      const slug = slugFromFile(f).toLowerCase();
      const route = `/docs/${slug}`;
      const st = await statOrNull(f);
      urls.set(route, { loc: SITE + route, lastmod: mtimeIso(st) });
    }
  }

  // Ensure docs index page exists
  if (!urls.has("/docs")) {
    urls.set("/docs", { loc: SITE + "/docs", lastmod: new Date().toISOString() });
  }

  // 4) Sort by path for stable output
  const sorted = Array.from(urls.values()).sort((a, b) => a.loc.localeCompare(b.loc));

  // 5) Build XML
  const isoNow = new Date().toISOString();
  const xml =
    `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    sorted.map(u =>
      `  <url>\n` +
      `    <loc>${escapeXml(u.loc)}</loc>\n` +
      `    <lastmod>${u.lastmod || isoNow}</lastmod>\n` +
      `    <changefreq>daily</changefreq>\n` +
      `    <priority>${priorityFor(u.loc)}</priority>\n` +
      `  </url>`
    ).join("\n") +
    `\n</urlset>\n`;

  // 6) Write out
  await fs.mkdir(path.dirname(OUTFILE), { recursive: true });
  await fs.writeFile(OUTFILE, xml, "utf8");

  console.log(`✔ sitemap generated (${sorted.length} URLs)\n   -> ${path.relative(process.cwd(), OUTFILE)}\n   site=${SITE}`);
}

function escapeXml(s) {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function priorityFor(loc) {
  try {
    const u = new URL(loc);
    const path = u.pathname;
    if (path === "/") return "1.0";
    if (path === "/blog" || path === "/docs" || path === "/network" || path === "/status") return "0.9";
    if (/^\/blog\/[^/]+$/.test(path)) return "0.8";
    if (/^\/docs\/[^/]+$/.test(path)) return "0.8";
    return "0.7";
  } catch {
    return "0.7";
  }
}

build().catch((err) => {
  console.error("✖ generate_sitemap failed:");
  console.error(err?.stack || err?.message || String(err));
  process.exit(1);
});
