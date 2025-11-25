#!/usr/bin/env node
/**
 * redirects_from_env.mjs
 *
 * Produce a redirect map for Studio / Explorer / Docs based on environment.
 * Generates:
 *   - website/src/generated/redirects.json   (primary; consumed by app/edge)
 *   - website/public/_redirects              (Netlify-compatible)
 *   - website/src/generated/vercel.redirects.json (Vercel importable snippet)
 *
 * Env (or CLI overrides):
 *   PUBLIC_STUDIO_URL     (--studio)
 *   PUBLIC_EXPLORER_URL   (--explorer)
 *   PUBLIC_DOCS_URL       (--docs)
 *
 * Usage:
 *   node website/scripts/redirects_from_env.mjs \
 *     [--studio https://studio.example] \
 *     [--explorer https://explorer.example] \
 *     [--docs https://docs.example] \
 *     [--status 302] \
 *     [--outdir website/src/generated] \
 *     [--netlify website/public/_redirects] \
 *     [--vercel website/src/generated/vercel.redirects.json]
 */

import { promises as fs } from "node:fs";
import path from "node:path";
import url from "node:url";

const __dirname = path.dirname(url.fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const DEFAULT_JSON_OUT = path.join(ROOT, "src", "generated", "redirects.json");
const DEFAULT_NETLIFY_OUT = path.join(ROOT, "public", "_redirects");
const DEFAULT_VERCEL_OUT = path.join(ROOT, "src", "generated", "vercel.redirects.json");

function parseArgs(argv) {
  const o = {
    studio: process.env.PUBLIC_STUDIO_URL || "",
    explorer: process.env.PUBLIC_EXPLORER_URL || "",
    docs: process.env.PUBLIC_DOCS_URL || "",
    status: Number(process.env.REDIRECT_STATUS || 302),
    jsonOut: process.env.REDIRECTS_JSON_OUT || DEFAULT_JSON_OUT,
    netlifyOut: process.env.REDIRECTS_NETLIFY_OUT || DEFAULT_NETLIFY_OUT,
    vercelOut: process.env.REDIRECTS_VERCEL_OUT || DEFAULT_VERCEL_OUT,
  };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if ((a === "--studio" || a === "-s") && argv[i + 1]) o.studio = argv[++i];
    else if ((a === "--explorer" || a === "-e") && argv[i + 1]) o.explorer = argv[++i];
    else if ((a === "--docs" || a === "-d") && argv[i + 1]) o.docs = argv[++i];
    else if (a === "--status" && argv[i + 1]) o.status = Number(argv[++i]);
    else if (a === "--outdir" && argv[i + 1]) {
      const dir = path.resolve(argv[++i]);
      o.jsonOut = path.join(dir, "redirects.json");
      o.vercelOut = path.join(dir, "vercel.redirects.json");
    } else if (a === "--netlify" && argv[i + 1]) o.netlifyOut = path.resolve(argv[++i]);
    else if (a === "--vercel" && argv[i + 1]) o.vercelOut = path.resolve(argv[++i]);
    else if (a === "-h" || a === "--help") {
      console.log(`Usage: ${path.basename(argv[1])} [--studio URL] [--explorer URL] [--docs URL] [--status 302] [--outdir DIR] [--netlify PATH] [--vercel PATH]`);
      process.exit(0);
    }
  }
  return o;
}

const opts = parseArgs(process.argv);

function isHttpUrl(u) {
  try {
    const p = new URL(u);
    return p.protocol === "https:" || p.protocol === "http:";
  } catch {
    return false;
  }
}

function clean(u) {
  if (!u) return "";
  try {
    const p = new URL(u);
    // remove trailing slash for consistent concat
    p.pathname = p.pathname.replace(/\/+$/, "");
    return p.toString();
  } catch {
    return u;
  }
}

function ensureList() {
  const list = [
    { from: "/studio",  to: clean(opts.studio) },
    { from: "/explorer", to: clean(opts.explorer) },
    { from: "/docs",    to: clean(opts.docs) }
  ].filter(r => r.to && isHttpUrl(r.to));
  return list;
}

async function writeFileEnsuringDir(fp, contents) {
  await fs.mkdir(path.dirname(fp), { recursive: true });
  await fs.writeFile(fp, contents, "utf8");
}

function netlifyLines(redirects, status) {
  // add both exact and /* splat
  const lines = [];
  for (const r of redirects) {
    lines.push(`${r.from} ${r.to} ${status}`);
    lines.push(`${r.from}/* ${r.to} ${status}`);
  }
  return lines.join("\n") + "\n";
}

function vercelJson(redirects, status) {
  return JSON.stringify({
    redirects: redirects.flatMap((r) => ([
      { source: r.from, destination: r.to, permanent: status === 301 },
      { source: `${r.from}/:path*`, destination: r.to, permanent: status === 301 }
    ]))
  }, null, 2) + "\n";
}

function redirectsJson(redirects, status) {
  return JSON.stringify({
    ok: true,
    generatedAt: new Date().toISOString(),
    status,
    redirects
  }, null, 2) + "\n";
}

async function main() {
  const redirects = ensureList();
  if (redirects.length === 0) {
    console.warn("No valid redirect targets found in env/CLI; nothing to do.");
    // still emit empty files for deterministic builds
  }

  await writeFileEnsuringDir(opts.jsonOut, redirectsJson(redirects, opts.status));
  await writeFileEnsuringDir(opts.netlifyOut, netlifyLines(redirects, opts.status));
  await writeFileEnsuringDir(opts.vercelOut, vercelJson(redirects, opts.status));

  console.log("✔ redirect maps generated");
  console.log(`   json:    ${path.relative(process.cwd(), opts.jsonOut)}`);
  console.log(`   netlify: ${path.relative(process.cwd(), opts.netlifyOut)}`);
  console.log(`   vercel:  ${path.relative(process.cwd(), opts.vercelOut)}`);
  for (const r of redirects) console.log(`   ${r.from} -> ${r.to} (${opts.status})`);
}

main().catch((err) => {
  console.error("✖ redirects_from_env failed:");
  console.error(err?.stack || err?.message || String(err));
  process.exit(1);
});
