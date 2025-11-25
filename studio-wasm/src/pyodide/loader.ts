/**
 * Pyodide loader (idempotent, cached, environment-aware).
 *
 * This module:
 *  - Locates and loads `pyodide.js` (via dynamic import or <script> injection)
 *  - Boots a single shared Pyodide instance with a configurable base URL
 *  - Caches and reuses the instance across the app
 *
 * It supports three discovery strategies, tried in order until one works:
 *   1) Global `loadPyodide` already present (e.g., preloaded by host)
 *   2) Dynamic import of the "pyodide" ESM package (if available)
 *   3) Script-tag injection from a list of base URL candidates
 *
 * You can override the search by passing `baseUrl` explicitly.
 */

export interface PyodideInstance {
  // Core API surface used by studio-wasm
  runPython: (code: string) => any;
  runPythonAsync: (code: string) => Promise<any>;
  pyimport: (name: string) => any;
  globals: any;
  FS: any;
  loadPackage?: (names: string | string[]) => Promise<void>;
  isPyProxy?: (obj: unknown) => boolean;
  toPy?: (obj: unknown) => any;
  toJs?: (obj: unknown, opts?: any) => any;
}

export interface LoadOptions {
  /**
   * Base URL where `pyodide.js` (and its related files) are hosted.
   * Example: "/vendor/pyodide" or "https://cdn.jsdelivr.net/pyodide/v0.24.1/full"
   * If omitted, the loader tries several sensible defaults and CDNs.
   */
  baseUrl?: string;

  /**
   * Value passed as Pyodide's `indexURL` option (defaults to `baseUrl`).
   * Rarely needed; override only if your file layout differs.
   */
  indexURL?: string;

  /**
   * Fallback version used when loading from a CDN candidate.
   * Default: "0.24.1". Can also be injected via build env:
   *   - import.meta.env.VITE_PYODIDE_VERSION or .PYODIDE_VERSION
   */
  version?: string;

  /**
   * Maximum time to wait when injecting the script tag or waiting for the
   * global loader to appear (per candidate).
   */
  timeoutMs?: number;
}

/** Internal cached promise & instance to guarantee single boot. */
let _pyodidePromise: Promise<PyodideInstance> | null = null;
let _pyodide: PyodideInstance | null = null;

/** Public: retrieve the already-initialized instance (or `null` if not yet loaded). */
export function getPyodide(): PyodideInstance | null {
  return _pyodide;
}

/** Public: quick boolean for consumers that need to branch on availability. */
export function isLoaded(): boolean {
  return _pyodide !== null;
}

/** Test-only: reset the cached state. */
export function __resetForTests(): void {
  _pyodide = null;
  _pyodidePromise = null;
}

/**
 * Load (and cache) a Pyodide instance. Safe to call multiple times.
 */
export async function loadPyodide(opts: LoadOptions = {}): Promise<PyodideInstance> {
  if (_pyodidePromise) return _pyodidePromise;

  _pyodidePromise = (async () => {
    const instance = await bootPyodide(opts);
    _pyodide = instance;
    return instance;
  })();

  return _pyodidePromise;
}

// ------------------------------- Internals -------------------------------

type LoadFn = (options: { indexURL: string }) => Promise<PyodideInstance>;

async function bootPyodide(opts: LoadOptions): Promise<PyodideInstance> {
  const env = (import.meta as any)?.env ?? {};
  const version =
    opts.version ||
    env.VITE_PYODIDE_VERSION ||
    env.PYODIDE_VERSION ||
    "0.24.1";

  // Build candidate base URLs in priority order
  const candidates: string[] = [];

  // 1) Explicit override
  if (opts.baseUrl) candidates.push(opts.baseUrl);

  // 2) Build-time env (Vite, etc.)
  const envBase = env.VITE_PYODIDE_BASE_URL || env.PYODIDE_BASE_URL;
  if (envBase) candidates.push(envBase);

  // 3) Try to infer from an existing <script src=".../pyodide.js"> if present
  const inferred = inferBaseUrlFromScriptTag();
  if (inferred) candidates.push(inferred);

  // 4) Common local paths (served by your app)
  candidates.push(
    "/vendor/pyodide",
    "/pyodide",
    "/assets/pyodide",
    "/static/pyodide"
  );

  // 5) CDN fallback (last resort)
  candidates.push(`https://cdn.jsdelivr.net/pyodide/v${version}/full`);

  const timeoutMs = opts.timeoutMs ?? 20_000;

  // If global loader is already present, we can try without script injection
  const preGlobal = resolveGlobalLoader();
  if (preGlobal) {
    const indexURL = normalizeBaseUrl(opts.indexURL || opts.baseUrl || candidates[0] || "");
    return await preGlobal({ indexURL });
  }

  // Try ESM dynamic import first (works in Node or bundlers with the package)
  const esmLoader = await tryResolveEsmLoader();
  if (esmLoader) {
    const indexURL = normalizeBaseUrl(opts.indexURL || opts.baseUrl || candidates[0] || "");
    return await esmLoader({ indexURL });
  }

  // Otherwise attempt script-tag injection for each candidate
  let lastError: unknown = null;
  for (const base of candidates) {
    const baseUrl = normalizeBaseUrl(base);
    try {
      const loadFn = await ensureScriptAndResolveLoader(baseUrl, timeoutMs);
      const indexURL = normalizeBaseUrl(opts.indexURL || baseUrl);
      return await loadFn({ indexURL });
    } catch (err) {
      lastError = err;
      // Try next candidate
    }
  }

  const msg = [
    "Failed to load Pyodide.",
    "Attempted strategies: global, ESM import('pyodide'), and script injection from candidates:",
    ...candidates.map((c) => ` - ${c}`),
    `Last error: ${(lastError as Error)?.message ?? String(lastError)}`,
  ].join("\n");

  throw new Error(msg);
}

function normalizeBaseUrl(url: string): string {
  if (!url) return "";
  return url.endsWith("/") ? url.slice(0, -1) : url;
}

function resolveGlobalLoader(): LoadFn | null {
  const g: any = globalThis as any;
  if (typeof g.loadPyodide === "function") {
    return g.loadPyodide.bind(g) as LoadFn;
  }
  return null;
}

async function tryResolveEsmLoader(): Promise<LoadFn | null> {
  try {
    // This path works if the "pyodide" ESM package is available at runtime.
    // eslint-disable-next-line @typescript-eslint/ban-ts-comment
    // @ts-ignore - dynamic import path is resolved by bundler if present
    const mod = await import("pyodide");
    if (mod && typeof mod.loadPyodide === "function") {
      return mod.loadPyodide.bind(mod) as LoadFn;
    }
    return null;
  } catch {
    return null;
  }
}

function inferBaseUrlFromScriptTag(): string | null {
  if (typeof document === "undefined") return null;
  const scripts = Array.from(document.getElementsByTagName("script"));
  for (const s of scripts) {
    const src = s.getAttribute("src") || "";
    const idx = src.indexOf("pyodide.js");
    if (idx >= 0) {
      // Trim trailing "/pyodide.js" and anything after it
      const base = src.slice(0, idx);
      return base.replace(/\/$/, "");
    }
  }
  return null;
}

async function ensureScriptAndResolveLoader(baseUrl: string, timeoutMs: number): Promise<LoadFn> {
  const existing = resolveGlobalLoader();
  if (existing) return existing;

  if (typeof document === "undefined") {
    throw new Error("Cannot inject <script> for Pyodide: document is undefined (non-browser environment).");
  }

  const jsUrl = `${baseUrl}/pyodide.js`;
  // Avoid duplicate scripts: if one with same src exists, reuse it
  const already = document.querySelector(`script[src="${cssEscape(jsUrl)}"]`) as HTMLScriptElement | null;
  if (already) {
    await waitForGlobalLoader(timeoutMs);
    const loader = resolveGlobalLoader();
    if (!loader) throw new Error("pyodide.js present but global loadPyodide not found.");
    return loader;
  }

  // Inject the loader script
  await injectScript(jsUrl, timeoutMs);

  const loader = resolveGlobalLoader();
  if (!loader) {
    throw new Error("Injected pyodide.js but global loadPyodide not found.");
  }
  return loader;
}

function injectScript(src: string, timeoutMs: number): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = src;
    script.async = true;
    script.crossOrigin = "anonymous";

    let done = false;
    const cleanup = () => {
      script.removeEventListener("load", onLoad);
      script.removeEventListener("error", onError);
      if (timer) {
        clearTimeout(timer);
        timer = undefined as any;
      }
    };

    const onLoad = () => {
      if (done) return;
      done = true;
      cleanup();
      resolve();
    };

    const onError = () => {
      if (done) return;
      done = true;
      cleanup();
      reject(new Error(`Failed to load script: ${src}`));
    };

    script.addEventListener("load", onLoad);
    script.addEventListener("error", onError);
    document.head.appendChild(script);

    let timer: any = setTimeout(() => {
      if (done) return;
      done = true;
      cleanup();
      reject(new Error(`Timed out loading script: ${src}`));
    }, timeoutMs);
  });
}

function waitForGlobalLoader(timeoutMs: number): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    const start = Date.now();
    const check = () => {
      if (resolveGlobalLoader()) return resolve();
      if (Date.now() - start > timeoutMs) {
        return reject(new Error("Timed out waiting for global loadPyodide"));
      }
      setTimeout(check, 25);
    };
    check();
  });
}

/**
 * Escape a URL for use in a CSS attribute selector.
 * Minimal implementation: escapes quotes and backslashes.
 */
function cssEscape(s: string): string {
  return s.replace(/["\\]/g, "\\$&");
}
