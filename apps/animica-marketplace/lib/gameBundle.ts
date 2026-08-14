import { MAX_CONTENT_BYTES } from './storage';

// Web-game play bundle helpers for the store's DIGITAL_GOOD web games (Game Lab).
// A play bundle is a SINGLE self-contained HTML document (inline <style>+<script>, assets as
// data: URIs, no network) — the same artifact Forge's buildSandboxedSrcDoc() produces. It is
// stored as a CID-addressed ContentObject (lib/storage.ts) and served, sandboxed, by the content
// route. This module only validates the doc and guarantees it carries a locked-down inner CSP;
// it never composes a game from raw html/css/js (that is Forge's job) and never touches AppBuild.

// Served + stored mime for a play bundle (matches ContentObject's default mime).
export const GAME_BUNDLE_MIME = 'text/html; charset=utf-8';

// Hard cap for a play bundle — the ContentObject rail is 2 MB (in-DB, the .anm hosting rail).
export const GAME_BUNDLE_MAX_BYTES = MAX_CONTENT_BYTES;

// The strict per-document CSP a sandboxed game must carry, mirroring Forge lib/sandbox.ts
// IFRAME_CSP: no network (connect-src 'none'), no external anything, inline script/style only,
// data:/blob: images + audio. The content route additionally serves every object under a
// `sandbox` response CSP (opaque origin, allow-scripts) — this meta is the in-document half,
// which also locks down the network that the `sandbox` directive alone does not.
export const GAME_BUNDLE_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; " +
  "img-src data: blob:; font-src data:; media-src data: blob:; connect-src 'none'; " +
  "form-action 'none'; base-uri 'none'; frame-src 'none'";

// Does this look like ONE self-contained HTML document (not JSON, a fragment, or several
// concatenated docs)? Must start with a doctype/<html> and carry at most one <html> root.
export function looksLikeHtmlDoc(html: string): boolean {
  const head = html.trimStart().slice(0, 512).toLowerCase();
  const startsDoc = head.startsWith('<!doctype html') || head.startsWith('<html');
  const roots = (html.match(/<html[\s>]/gi) || []).length;
  if (roots > 1) return false; // concatenated documents are not a single bundle
  return startsDoc || roots === 1;
}

// Does the document already carry a restrictive CSP meta (default-src 'none')?
export function hasSandboxCsp(html: string): boolean {
  const metas = html.match(/<meta[^>]+http-equiv=["']?content-security-policy["']?[^>]*>/gi) || [];
  return metas.some((m) => /default-src\s+'none'/i.test(m));
}

// Guarantee the bundle carries the locked-down inner CSP. Idempotent: if a `default-src 'none'`
// CSP meta is already present (Forge's buildSandboxedSrcDoc emits one), the doc is returned
// unchanged; otherwise the meta is injected as early as possible so it governs the whole doc.
export function ensureSandboxedBundle(html: string): string {
  if (hasSandboxCsp(html)) return html;
  const meta = `<meta http-equiv="Content-Security-Policy" content="${GAME_BUNDLE_CSP}">`;
  const headOpen = /<head\b[^>]*>/i.exec(html);
  if (headOpen) {
    const at = headOpen.index + headOpen[0].length;
    return html.slice(0, at) + meta + html.slice(at);
  }
  const htmlOpen = /<html\b[^>]*>/i.exec(html);
  if (htmlOpen) {
    const at = htmlOpen.index + htmlOpen[0].length;
    return html.slice(0, at) + `<head>${meta}</head>` + html.slice(at);
  }
  return `<!doctype html><html><head><meta charset="utf-8">${meta}</head><body>${html}</body></html>`;
}
