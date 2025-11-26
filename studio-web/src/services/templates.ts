/**
 * Load contract templates (source + manifest) from local fixtures.
 * Works in dev/build via Vite asset URLs (new URL(..., import.meta.url)).
 *
 * Directory layout expected:
 *   studio-web/src/fixtures/templates/index.json
 *   studio-web/src/fixtures/templates/<id>/contract.py
 *   studio-web/src/fixtures/templates/<id>/manifest.json
 */

export type TemplateMeta = {
  id: string;
  name: string;
  description?: string;
  tags?: string[];
};

export type Template = TemplateMeta & {
  source: string;                             // contents of contract.py
  manifest: Record<string, unknown>;          // parsed manifest.json
};

const DEFAULT_TEMPLATE_IDS = ['counter', 'escrow', 'ai_agent', 'quantum_rng'];

const INDEX_URL = new URL('../fixtures/templates/index.json', import.meta.url);

let _metaCache: TemplateMeta[] | null = null;
const _tplCache = new Map<string, Template>();

/** Resolve asset URLs for a given template id. */
function urlsFor(id: string): { contract: URL; manifest: URL } {
  return {
    contract: new URL(`../fixtures/templates/${id}/contract.py`, import.meta.url),
    manifest: new URL(`../fixtures/templates/${id}/manifest.json`, import.meta.url),
  };
}

/** Fetch helper that throws a nice error with context. */
async function fetchText(u: URL, kind: string): Promise<string> {
  const res = await fetch(u);
  if (!res.ok) throw new Error(`${kind} fetch failed (${res.status}) for ${u}`);
  return await res.text();
}
async function fetchJson<T = any>(u: URL, kind: string): Promise<T> {
  const res = await fetch(u);
  if (!res.ok) throw new Error(`${kind} fetch failed (${res.status}) for ${u}`);
  return (await res.json()) as T;
}

/**
 * List available templates.
 * Reads fixtures/templates/index.json when present; otherwise falls back to defaults.
 * index.json may be either:
 *   { "templates": [ { id, name, description?, tags? }, ... ] }
 * or
 *   [ { id, name, ... }, ... ]
 */
export async function listTemplates(): Promise<TemplateMeta[]> {
  if (_metaCache) return _metaCache;

  try {
    const data = await fetchJson<any>(INDEX_URL, 'index');
    const arr: any[] = Array.isArray(data) ? data : Array.isArray(data?.templates) ? data.templates : [];
    if (arr.length > 0) {
      _metaCache = arr.map((t) => ({
        id: String(t.id),
        name: String(t.name ?? t.id),
        description: t.description ? String(t.description) : undefined,
        tags: Array.isArray(t.tags) ? t.tags.map(String) : undefined,
      }));
      return _metaCache;
    }
  } catch {
    // fall through to defaults
  }

  _metaCache = DEFAULT_TEMPLATE_IDS.map((id) => ({
    id,
    name: id.replace(/(^|_)(\w)/g, (_, p1, c) => (p1 ? ' ' : '') + c.toUpperCase()), // "ai_agent" -> "Ai Agent"
  }));
  return _metaCache;
}

/** Load a single template by id (cached). */
export async function loadTemplate(id: string): Promise<Template> {
  if (_tplCache.has(id)) return _tplCache.get(id)!;

  // Ensure id is in the catalog; if index.json has a different canonical list we preserve its metadata.
  const catalog = await listTemplates();
  const meta = catalog.find((m) => m.id === id) ?? {
    id,
    name: id,
  };

  const { contract, manifest } = urlsFor(id);
  const [source, manifestObj] = await Promise.all([
    fetchText(contract, `template:${id}/contract.py`),
    fetchJson<Record<string, unknown>>(manifest, `template:${id}/manifest.json`),
  ]);

  const tpl: Template = { ...meta, source, manifest: manifestObj };
  _tplCache.set(id, tpl);
  return tpl;
}

/** Load all templates (respect index order). */
export async function loadAllTemplates(): Promise<Template[]> {
  const metas = await listTemplates();
  return Promise.all(metas.map((m) => loadTemplate(m.id)));
}

/** Clear in-memory caches (useful for hot-reload/dev). */
export function clearTemplateCache(): void {
  _metaCache = null;
  _tplCache.clear();
}

/** Convenience: get only IDs (index order). */
export async function listTemplateIds(): Promise<string[]> {
  const metas = await listTemplates();
  return metas.map((m) => m.id);
}

/** Backwards-compatible alias. */
export const loadTemplateById = loadTemplate;

/** Ensure at least the first template is loaded (used as a safe default). */
export async function ensureDefaultTemplate(): Promise<Template> {
  const ids = await listTemplateIds();
  const firstId = ids[0] ?? DEFAULT_TEMPLATE_IDS[0];
  return loadTemplate(firstId);
}

export default {
  listTemplates,
  listTemplateIds,
  loadTemplate,
  loadTemplateById,
  loadAllTemplates,
  clearTemplateCache,
  ensureDefaultTemplate,
};
