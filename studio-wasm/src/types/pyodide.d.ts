/* Minimal Pyodide ambient types for studio-wasm.
 * These are intentionally lightweight and only include members we actually use.
 * If you need more surface, extend here rather than sprinkling `any` in code.
 */

export interface PyodideRunOptions {
  globals?: any;
}

export interface PyodideFS {
  mkdir?(path: string): void;
  writeFile?(path: string, data: string | Uint8Array, opts?: { encoding?: 'utf8' }): void;
  readFile?(path: string, opts?: { encoding?: 'utf8' }): string | Uint8Array;
  unlink?(path: string): void;
  readdir?(path: string): string[];
}

export interface PyodideInterface {
  /** e.g. "0.24.1" */
  version?: string;
  FS?: PyodideFS;

  runPython<T = unknown>(code: string, opts?: PyodideRunOptions): T;
  runPythonAsync<T = unknown>(code: string, opts?: PyodideRunOptions): Promise<T>;

  /** Import a Python module/package. Often proxied via micropip. */
  loadPackage?(name: string | string[], opts?: Record<string, unknown>): Promise<void>;

  /** Convert a Python object to JS (present when using pyproxy helpers). */
  toPy?(obj: unknown): any;
  /** Release a PyProxy (no-op if not using proxies). */
  destroy?(obj: any): void;
}

export interface LoadPyodideOptions {
  /** Base URL where pyodide.{js,wasm,data} live. */
  indexURL?: string;
  stdin?: (msg: string) => void;
  stdout?: (msg: string) => void;
  stderr?: (msg: string) => void;
  /** Extra packages to preload (not commonly used in our bundle). */
  packages?: string[]; // e.g. ['micropip']
}

/** Global loader injected by the pyodide.js entry. */
export function loadPyodide(opts?: LoadPyodideOptions): Promise<PyodideInterface>;

/** Augment Window so TS is happy in browser contexts. */
declare global {
  interface Window {
    loadPyodide?: typeof loadPyodide;
    pyodide?: PyodideInterface;
  }
}

export {};
