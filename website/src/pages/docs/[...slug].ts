/**
 * Docs catch-all endpoint:
 * - If a local MD/MDX doc exists (kept outside this dynamic route to avoid conflicts, e.g. under
 *   `src/pages/docs-local/**`), 307-redirect to the local route.
 * - Otherwise, 302-redirect to the canonical external docs site, preserving the path and query.
 *
 * Why a .ts endpoint instead of .astro?
 * This must run on the server/edge to decide dynamically between local content and canonical docs.
 *
 * Production tip:
 * - Keep any MD/MDX pages that you want to serve locally in `src/pages/docs-local/**`.
 *   Example: `src/pages/docs-local/get-started.mdx` will be served at `/docs-local/get-started`.
 * - This endpoint will detect such pages at build-time via Vite's import.meta.glob and redirect to them.
 */

import type { APIRoute } from "astro";

export const prerender = false;
export function getStaticPaths() {
  return [];
}

const CANONICAL =
  (import.meta.env.PUBLIC_DOCS_URL as string) || "https://docs.animica.dev";

/**
 * Discover local docs at build time.
 * We purposely scan `docs-local` (not `docs`) to avoid the catch-all route collision.
 * The map keys are Vite-resolved paths; we normalize them to request slugs.
 */
const localDocsMap = (() => {
  // Match both .md and .mdx under docs-local
  const modules = import.meta.glob("../../pages/docs-local/**/*.{md,mdx}", { eager: true });
  // Normalize keys like "../../pages/docs-local/foo/bar.mdx" → "foo/bar"
  const norm = new Set<string>();
  for (const key of Object.keys(modules)) {
    // Strip leading path to docs-local/
    const idx = key.indexOf("/docs-local/");
    if (idx === -1) continue;
    let sub = key.slice(idx + "/docs-local/".length);
    // Remove extension
    sub = sub.replace(/\.(md|mdx)$/, "");
    // Support index files: "foo/index" → "foo"
    sub = sub.replace(/\/index$/, "");
    norm.add(sub);
  }
  return norm;
})();

export const GET: APIRoute = async (ctx) => {
  const url = new URL(ctx.request.url);
  const parts = Array.isArray(ctx.params.slug)
    ? ctx.params.slug
    : typeof ctx.params.slug === "string"
    ? ctx.params.slug.split("/")
    : [];

  // Handle root redirect (/docs → /docs-index page or external)
  if (parts.length === 0 || (parts.length === 1 && parts[0] === "")) {
    // Prefer local docs landing if present; else external
    if (localDocsMap.has("") || localDocsMap.has("index")) {
      return ctx.redirect("/docs-local", 307);
    }
    const dest = joinExternal(CANONICAL, "/", url.search);
    return ctx.redirect(dest, 302);
  }

  const slug = cleanSlug(parts);
  // If we have a local match, prefer it
  if (localDocsMap.has(slug)) {
    const localPath = "/docs-local/" + slug;
    // Preserve querystring for anchors/utm
    const withQuery = url.search ? `${localPath}${url.search}` : localPath;
    return ctx.redirect(withQuery, 307);
  }

  // No local page: bounce to canonical external docs
  const dest = joinExternal(CANONICAL, `/${slug}`, url.search);
  return ctx.redirect(dest, 302);
};

/** Join base + path + search safely (avoids double slashes). */
function joinExternal(base: string, path: string, search = ""): string {
  try {
    const u = new URL(base);
    // Ensure single slash between base pathname and path
    const left = u.pathname.endsWith("/") ? u.pathname.slice(0, -1) : u.pathname;
    const right = path.startsWith("/") ? path : `/${path}`;
    u.pathname = `${left}${right}`;
    u.search = search.replace(/^\?/, "") ? search : "";
    return u.toString();
  } catch {
    // Fallback simple join if base isn't a URL at build time
    const b = base.replace(/\/+$/, "");
    const p = path.startsWith("/") ? path : `/${path}`;
    return `${b}${p}${search || ""}`;
  }
}

/** Normalize slug parts, removing empty segments and '.' */
function cleanSlug(parts: string[]): string {
  return parts
    .map((p) => p.trim())
    .filter((p) => p && p !== ".")
    .join("/")
    .replace(/\/+/g, "/");
}
