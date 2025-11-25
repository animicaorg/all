import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { listTemplates, getTemplate } from "../services/templates";

/** Local mirrors of the expected shapes from services/templates.ts (structural typing). */
export interface TemplateMeta {
  id: string;
  name: string;
  description?: string;
  tags?: string[];
  filesCount?: number;
}

export interface TemplateFile {
  path: string;
  contents: string;
}

export interface Template {
  id: string;
  name: string;
  description?: string;
  tags?: string[];
  files: TemplateFile[];
}

/** Public hook API */
export interface UseTemplates {
  /** Raw, unsorted index as returned by the service (but we keep it stable). */
  list: TemplateMeta[];
  /** Sorted & filtered by query convenience list. */
  filtered: TemplateMeta[];
  /** Current search query (matches id/name/description/tags). */
  query: string;
  /** Update search query. */
  setQuery: (q: string) => void;
  /** Whether we're currently fetching the template index. */
  loading: boolean;
  /** Last error message, if any. */
  error: string | null;
  /** Force-refresh the template index. */
  refresh: () => Promise<void>;
  /** Load a full template (files & metadata). Caches per id for the session. */
  load: (id: string) => Promise<Template>;
  /** Returns true if a given template is cached already. */
  isCached: (id: string) => boolean;
}

/**
 * useTemplates — list & load project templates (counter/escrow/ai_agent, …).
 * Backed by studio-web/src/services/templates.ts.
 *
 * - Caches loaded templates in-memory for the session
 * - Provides simple search against id/name/description/tags
 * - Exposes a refresh() to re-fetch the index
 */
export function useTemplates(): UseTemplates {
  const [list, setList] = useState<TemplateMeta[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState<string>("");

  // Cache loaded templates by id for fast reopen.
  const cacheRef = useRef<Map<string, Template>>(new Map());

  const abortRef = useRef<AbortController | null>(null);

  const fetchIndex = useCallback(async () => {
    setLoading(true);
    setError(null);

    // Cancel any in-flight request
    if (abortRef.current) abortRef.current.abort();
    const ac = new AbortController();
    abortRef.current = ac;

    try {
      const idx = await listTemplates({ signal: ac.signal });
      // Defensive: ensure minimal shape and stable sort by name then id.
      const normalized = (idx || [])
        .filter((t: any) => t && typeof t.id === "string" && typeof t.name === "string")
        .map((t: any) => ({
          id: t.id,
          name: t.name,
          description: t.description,
          tags: Array.isArray(t.tags) ? t.tags.slice(0, 8) : undefined,
          filesCount: typeof t.filesCount === "number" ? t.filesCount : undefined,
        }))
        .sort((a: TemplateMeta, b: TemplateMeta) =>
          a.name.localeCompare(b.name) || a.id.localeCompare(b.id)
        );

      setList(normalized);
    } catch (e: any) {
      if (e?.name === "AbortError") return;
      setError(e?.message ?? String(e));
    } finally {
      if (abortRef.current === ac) abortRef.current = null;
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    // Initial fetch on mount
    // eslint-disable-next-line @typescript-eslint/no-floating-promises
    fetchIndex();
    return () => {
      if (abortRef.current) abortRef.current.abort();
    };
  }, [fetchIndex]);

  const refresh = useCallback(async () => {
    await fetchIndex();
  }, [fetchIndex]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return list;

    return list.filter((t) => {
      const hay = [
        t.id,
        t.name,
        t.description || "",
        ...(t.tags || []),
      ]
        .join(" ")
        .toLowerCase();
      return hay.includes(q);
    });
  }, [list, query]);

  const isCached = useCallback(
    (id: string) => cacheRef.current.has(id),
    []
  );

  const load = useCallback(async (id: string): Promise<Template> => {
    if (!id || typeof id !== "string") {
      throw new Error("Template id is required");
    }
    const cached = cacheRef.current.get(id);
    if (cached) return cached;

    const tpl = await getTemplate(id);
    // Validate minimal shape
    if (!tpl || typeof tpl.id !== "string" || !Array.isArray(tpl.files)) {
      throw new Error("Template payload malformed");
    }
    // Freeze shallow to discourage accidental mutation by consumers
    const frozen: Template = Object.freeze({
      id: tpl.id,
      name: tpl.name ?? id,
      description: tpl.description,
      tags: tpl.tags,
      files: tpl.files.map((f: any) =>
        Object.freeze({
          path: String(f.path),
          contents: String(f.contents ?? ""),
        })
      ),
    });

    cacheRef.current.set(id, frozen);
    return frozen;
  }, []);

  return {
    list,
    filtered,
    query,
    setQuery,
    loading,
    error,
    refresh,
    load,
    isCached,
  };
}

export default useTemplates;
