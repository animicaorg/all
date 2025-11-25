/**
 * Initialize the embedded Python VM inside a running Pyodide instance.
 *
 * Responsibilities:
 *  - Mount the bundled Python package trees (py/vm_pkg and py/bridge) into
 *    the Pyodide virtual FS at a configurable mount point (default: /lib/py)
 *  - Prepend the mount point to sys.path so `import bridge.entry` works
 *  - Optionally pre-import modules (by default: ["bridge.entry"])
 *
 * The module is idempotent: calling `initPyVm()` multiple times is safe.
 */

import type { PyodideInstance } from "./loader";

export interface InitOptions {
  /** Mount point within the Pyodide FS. Defaults to "/lib/py". */
  mountRoot?: string;
  /**
   * Optional explicit file map: relativePath (under /py) -> text content.
   * If omitted, we try to bundle via `import.meta.glob` (Vite/Rollup) and
   * fall back to fetching from `${fetchBaseUrl}/py/...` at runtime.
   */
  files?: Record<string, string>;
  /**
   * Base URL used by the fetch fallback when `files` aren't provided AND
   * `import.meta.glob` is unavailable. Defaults to current origin ("").
   * Example: "/studio-wasm" if your app serves the /py folder there.
   */
  fetchBaseUrl?: string;
  /**
   * Modules to import eagerly once sys.path is configured.
   * Defaults to ["bridge.entry"] so higher-level APIs can immediately call
   * into the Python bridge functions.
   */
  preimport?: string[];
  /** Verbose logging to the console during mount/init. */
  verbose?: boolean;
}

let _initialized = false;

/**
 * Mount the Python package files and ensure `bridge.entry` can be imported.
 * Requires that a Pyodide instance is already loaded.
 */
export async function initPyVm(
  opts: InitOptions = {}
): Promise<void> {
  if (_initialized) return;

  const pyodide = ensurePyodide();
  const mountRoot = opts.mountRoot || "/lib/py";
  const verbose = !!opts.verbose;

  // 1) Collect file contents for /py/**/* from one of:
  //    - explicit `opts.files`
  //    - build-time bundler import (import.meta.glob)
  //    - runtime fetch from `${fetchBaseUrl}/py/...`
  const files =
    opts.files ||
    (await tryLoadFilesFromBundler(verbose)) ||
    (await fetchFilesAtRuntime(opts.fetchBaseUrl ?? "", verbose));

  // 2) Mount the files under the mount root
  mountFileMap(pyodide, files, mountRoot, verbose);

  // 3) Ensure the mount root is on sys.path and pre-import modules
  await configureSysPathAndImports(pyodide, mountRoot, opts.preimport, verbose);

  _initialized = true;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function ensurePyodide(): PyodideInstance {
  const g = globalThis as any;
  // In our loader we cache the instance in module state; some apps may also
  // stash it on `globalThis.pyodide`. Be flexible:
  const maybeFromGlobal = g.pyodide as PyodideInstance | undefined;
  if (maybeFromGlobal && typeof maybeFromGlobal.runPython === "function") {
    return maybeFromGlobal;
  }

  // Prefer an ESM-loaded singleton if the app used our loader; we detect it by
  // looking for the `pyimport` method on an object held by closure. Since we
  // cannot import from the loader here without creating a circular dep during
  // some bundling setups, require the host to load via our public API first.
  // If we got here without a global, we still assume the runtime provides one.
  if (typeof (globalThis as any).loadPyodide !== "function") {
    throw new Error(
      "Pyodide is not loaded. Call loadPyodide() from studio-wasm before initPyVm()."
    );
  }

  // If a consumer loaded pyodide directly but didn't stash the instance,
  // we still can try the conventional `globalThis.pyodide` after load.
  const inst = (globalThis as any).pyodide as PyodideInstance | undefined;
  if (!inst) {
    throw new Error(
      "Pyodide instance not found. Ensure you assign the result of loadPyodide() to globalThis.pyodide or use studio-wasm's loader."
    );
  }
  return inst;
}

/**
 * Attempt to bundle Python sources using Vite/Rollup's `import.meta.glob`.
 * Returns `null` if unsupported in the current environment/bundler.
 */
async function tryLoadFilesFromBundler(verbose: boolean): Promise<Record<string, string> | null> {
  const anyImportMeta: any = import.meta as any;
  if (typeof anyImportMeta.glob !== "function") return null;

  if (verbose) console.info("[studio-wasm] Importing Python sources via import.meta.glob…");

  // Grab every file under ../../py/** and import as raw text.
  // The keys are module-relative paths like "/src/pyodide/../../py/bridge/entry.py".
  const globbed = anyImportMeta.glob("../../py/**/*", { as: "raw", eager: true }) as Record<
    string,
    string
  >;

  const out: Record<string, string> = {};
  const prefix = "/py/"; // we will normalize paths to be relative to this
  for (const [absPath, content] of Object.entries(globbed)) {
    // Normalize: find the "/py/" segment and use the suffix as the relative key
    const idx = absPath.lastIndexOf(prefix);
    if (idx === -1) continue;
    const rel = absPath.slice(idx + prefix.length); // e.g., "bridge/entry.py"
    out[rel] = content;
  }

  if (Object.keys(out).length === 0) {
    if (verbose) console.warn("[studio-wasm] import.meta.glob found no /py files; falling back to runtime fetch.");
    return null;
  }

  if (verbose) console.info(`[studio-wasm] Bundled ${Object.keys(out).length} Python files.`);
  return out;
}

/**
 * Fetch the /py tree at runtime. We enumerate known file lists that are
 * required by this package to function. If you add files, update this list
 * or pass an explicit `files` map to `initPyVm`.
 */
async function fetchFilesAtRuntime(
  base: string,
  verbose: boolean
): Promise<Record<string, string>> {
  const baseUrl = trimTrailingSlash(base);

  // Minimal manifest: keep in sync with the repo's /py layout.
  // (We include the key files; if you add more, expand this list.)
  const required: string[] = [
    // bridge
    "bridge/__init__.py",
    "bridge/entry.py",
    "bridge/abi_helpers.py",
    "bridge/fs_mem.py",
    // vm_pkg (runtime core + stdlib + compiler)
    "vm_pkg/__init__.py",
    "vm_pkg/runtime/__init__.py",
    "vm_pkg/runtime/engine.py",
    "vm_pkg/runtime/gasmeter.py",
    "vm_pkg/runtime/context.py",
    "vm_pkg/runtime/storage_api.py",
    "vm_pkg/runtime/events_api.py",
    "vm_pkg/runtime/hash_api.py",
    "vm_pkg/runtime/abi.py",
    "vm_pkg/runtime/random_api.py",
    "vm_pkg/stdlib/__init__.py",
    "vm_pkg/stdlib/storage.py",
    "vm_pkg/stdlib/events.py",
    "vm_pkg/stdlib/hash.py",
    "vm_pkg/stdlib/abi.py",
    "vm_pkg/stdlib/treasury.py",
    "vm_pkg/compiler/__init__.py",
    "vm_pkg/compiler/ir.py",
    "vm_pkg/compiler/encode.py",
    "vm_pkg/compiler/typecheck.py",
    "vm_pkg/compiler/gas_estimator.py",
    "vm_pkg/loader.py",
    "vm_pkg/errors.py",
    // optional metadata
    "requirements.txt",
  ];

  const results: Record<string, string> = {};
  const errors: string[] = [];

  if (verbose) {
    console.info(
      `[studio-wasm] Fetching Python files at runtime from "${baseUrl || "<origin>"}"…`
    );
  }

  await Promise.all(
    required.map(async (rel) => {
      const url = `${baseUrl}/py/${rel}`;
      try {
        const res = await fetch(url, { credentials: "same-origin" });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        results[rel] = await res.text();
      } catch (e: any) {
        errors.push(`${rel}: ${e?.message || e}`);
      }
    })
  );

  if (errors.length > 0) {
    throw new Error(
      `Failed to fetch required Python files for studio-wasm:\n${errors
        .map((e) => ` - ${e}`)
        .join("\n")}\nHint: serve the /py directory or bundle files via import.meta.glob, or pass 'files' to initPyVm().`
    );
  }

  if (verbose) console.info(`[studio-wasm] Fetched ${Object.keys(results).length} Python files.`);
  return results;
}

function trimTrailingSlash(s: string): string {
  return s.endsWith("/") ? s.slice(0, -1) : s;
}

function mountFileMap(
  pyodide: PyodideInstance,
  files: Record<string, string>,
  mountRoot: string,
  verbose: boolean
): void {
  const FS = pyodide.FS;

  // Create the mount root if missing
  mkdirTree(FS, mountRoot);

  let count = 0;
  for (const [rel, content] of Object.entries(files)) {
    const fullPath = joinPath(mountRoot, rel);
    const dir = fullPath.substring(0, fullPath.lastIndexOf("/"));
    mkdirTree(FS, dir);
    try {
      // Write file (as text). If you add binary assets, adapt this to Uint8Array.
      FS.writeFile(fullPath, content, { encoding: "utf8" });
      count++;
    } catch (e) {
      throw new Error(`Failed to write ${fullPath} to Pyodide FS: ${(e as Error).message}`);
    }
  }

  if (verbose) console.info(`[studio-wasm] Mounted ${count} Python files under ${mountRoot}`);
}

function mkdirTree(FS: any, path: string): void {
  const parts = path.split("/").filter(Boolean);
  let acc = "";
  for (const p of parts) {
    acc += "/" + p;
    try {
      FS.lookupPath(acc);
    } catch {
      try {
        FS.mkdir(acc);
      } catch {
        // directory may already exist due to races; ignore
      }
    }
  }
}

function joinPath(a: string, b: string): string {
  if (a.endsWith("/")) a = a.slice(0, -1);
  if (b.startsWith("/")) b = b.slice(1);
  return `${a}/${b}`;
}

async function configureSysPathAndImports(
  pyodide: PyodideInstance,
  mountRoot: string,
  preimport: string[] | undefined,
  verbose: boolean
): Promise<void> {
  const toImport = preimport && preimport.length > 0 ? preimport : ["bridge.entry"];

  // Prepend mount root to sys.path if not present
  const sysPathScript = `
import sys
root = ${JSON.stringify(mountRoot)}
if root not in sys.path:
    sys.path.insert(0, root)
len(sys.path)
`;

  try {
    await pyodide.runPythonAsync(sysPathScript);
  } catch (e) {
    throw new Error(`Failed to configure sys.path in Pyodide: ${(e as Error).message}`);
  }

  // Import requested modules
  for (const mod of toImport) {
    try {
      pyodide.pyimport(mod);
      if (verbose) console.info(`[studio-wasm] Imported Python module: ${mod}`);
    } catch (e) {
      throw new Error(`Failed to import Python module "${mod}": ${(e as Error).message}`);
    }
  }
}

