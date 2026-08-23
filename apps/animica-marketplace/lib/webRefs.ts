// Web access for media jobs (gateway side). Default ON for every job (opt out per job with
// params.web=false, or globally with MEDIA_WEB_REFS=0).
//
// The gateway runs no model, but it does have a metasearch engine (the same SearXNG the
// x402 web-search products use). For an image/video request it looks up REFERENCE PHOTOS
// of the subject and hands their URLs to the miner, whose CLIP judge then also rewards
// candidates that look like the real thing — a measurable accuracy lever for specific or
// technical subjects the diffusion model only half-knows ("crib style log trailer").
// For chat-style grounding the same engine returns web snippets (see webSnippets()).
//
// Everything here is best-effort and time-boxed: a search failure never blocks a job.

const SEARX = process.env.SEARXNG_URL || 'http://127.0.0.1:8890';
const TIMEOUT_MS = Number(process.env.MEDIA_WEB_TIMEOUT_MS ?? 9000);

const IMG_EXT = /\.(jpe?g|png|webp|gif|bmp|avif)(\?|$)/i;
const SKIP_HOST = /(^|\.)(pinterest|facebook|instagram|tiktok|twitter|x)\.com$|\.svg(\?|$)/i;

export function webRefsEnabled(params?: Record<string, unknown> | null): boolean {
  if (process.env.MEDIA_WEB_REFS === '0') return false;
  if (params && (params.web === false || params.web === 'off' || params.web === 0)) return false;
  return true;
}

async function fetchJson(url: string): Promise<any | null> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(url, { signal: ctl.signal, headers: { accept: 'application/json' } });
    if (!r.ok) return null;
    return await r.json();
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}

// Subject query for reference lookup: the compiled prompt minus style/quality words, capped.
export function referenceQuery(prompt: string): string {
  const drop = /\b(photo(realistic)?|realistic|painting|illustration|vector|flat|cartoon|anime|3d|render|cinematic|isometric|minimal(ist)?|highly detailed|sharp focus|studio lighting|4k|8k|hdr|masterpiece|best quality)\b/gi;
  const q = prompt.replace(/"[^"]*"/g, ' ').replace(drop, ' ').replace(/[,.;:]+/g, ' ').replace(/\s+/g, ' ').trim();
  return q.split(' ').slice(0, 12).join(' ');
}

export interface ReferenceLookup {
  query: string;
  urls: string[];
  sources: { url: string; title?: string }[];
}

export async function findReferenceImages(prompt: string, n = 4): Promise<ReferenceLookup | null> {
  const query = referenceQuery(prompt);
  if (!query || query.split(' ').length < 1) return null;
  const d = await fetchJson(`${SEARX}/search?q=${encodeURIComponent(query)}&format=json&categories=images&safesearch=1`);
  if (!d || !Array.isArray(d.results)) return null;
  const urls: string[] = [];
  const sources: { url: string; title?: string }[] = [];
  for (const r of d.results) {
    const u: string = r.img_src || r.thumbnail_src || '';
    if (!u || !/^https?:\/\//i.test(u)) continue;
    let host = '';
    try { host = new URL(u).hostname; } catch { continue; }
    if (SKIP_HOST.test(host) || SKIP_HOST.test(u)) continue;
    if (!IMG_EXT.test(u) && !/image|img|photo|upload|media|cdn/i.test(u)) continue;
    if (urls.includes(u)) continue;
    urls.push(u);
    sources.push({ url: r.url || u, title: typeof r.title === 'string' ? r.title.slice(0, 120) : undefined });
    if (urls.length >= n) break;
  }
  return urls.length ? { query, urls, sources } : { query, urls: [], sources: [] };
}

export interface WebSnippet { url: string; title: string; content: string }

export async function webSnippets(query: string, n = 5): Promise<WebSnippet[]> {
  const d = await fetchJson(`${SEARX}/search?q=${encodeURIComponent(query)}&format=json&categories=general`);
  if (!d || !Array.isArray(d.results)) return [];
  return d.results
    .filter((r: any) => r && typeof r.url === 'string')
    .slice(0, n)
    .map((r: any) => ({ url: r.url, title: String(r.title || '').slice(0, 160), content: String(r.content || '').slice(0, 400) }));
}
