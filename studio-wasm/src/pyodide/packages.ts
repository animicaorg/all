/**
 * Optional package manager helpers for Pyodide.
 *
 * This module wraps `micropip` for installing extra pure-Python wheels
 * at runtime. It aims to be safe-by-default and resilient across Pyodide
 * versions by:
 *  - Lazily loading `micropip` (via `pyodide.loadPackage('micropip')` if needed)
 *  - Supporting explicit wheel URLs and simple requirement strings
 *  - Allowing additional index URLs (private indexes / mirrors)
 *  - Keeping installs idempotent and surfacing clear errors
 *
 * Notes:
 *  - Prefer bundling critical Python code with your app (see initPy.ts).
 *  - These helpers are intended for *optional* extras (demos, labs).
 *  - Binary wheels requiring native extensions are not supported in Pyodide.
 */

import type { PyodideInstance } from "./loader";

export interface PipInstallOptions {
  /**
   * Additional index URLs to search for packages (e.g., private/simple indexes).
   * Each should point to a "simple" style index (PEP 503).
   */
  extraIndexUrls?: string[];
  /**
   * If true, attempt to continue installing the rest of the packages when one fails.
   * Defaults to true (safer for demos).
   */
  keepGoing?: boolean;
  /**
   * Allow pre-release versions when resolving dependencies (default false).
   */
  preRelease?: boolean;
  /**
   * If true, do not install dependencies (only the requested packages/wheels).
   * Defaults to false. For explicit wheel URLs, deps are typically already bundled → true.
   */
  noDeps?: boolean;
  /**
   * Log progress to console.
   */
  verbose?: boolean;
}

export interface RequirementParse {
  /** Canonical list of requirement entries (names, version pins, or wheel URLs). */
  entries: string[];
  /** Lines that were ignored (comments/empty). */
  ignored: string[];
}

const DEFAULT_OPTS: Required<PipInstallOptions> = {
  extraIndexUrls: [],
  keepGoing: true,
  preRelease: false,
  noDeps: false,
  verbose: false,
};

/** Ensure `micropip` is importable; load it if necessary. */
export async function ensureMicropip(pyodide: PyodideInstance, verbose = false): Promise<void> {
  try {
    pyodide.pyimport("micropip");
    return;
  } catch {
    // not loaded yet
  }
  if (typeof pyodide.loadPackage === "function") {
    if (verbose) console.info("[studio-wasm] Loading Pyodide package: micropip…");
    // Some Pyodide versions require the package loader to fetch micropip.
    await pyodide.loadPackage("micropip");
  }
  // Try again
  try {
    pyodide.pyimport("micropip");
  } catch (e) {
    throw new Error(
      "Failed to load `micropip` in Pyodide. " +
        "Ensure your Pyodide distribution includes micropip, or update Pyodide."
    );
  }
}

/**
 * Install packages (names/specifiers or wheel URLs) via micropip.
 *
 * Examples:
 *   await installPackages(py, ["msgspec==0.18.6"])
 *   await installPackages(py, ["https://example.com/wheels/some_pkg-1.0.0-py3-none-any.whl"], { noDeps: true })
 *   await installPackages(py, ["packageA==1.2.3"], { extraIndexUrls: ["https://my.index/simple/"] })
 */
export async function installPackages(
  pyodide: PyodideInstance,
  packages: string[],
  options: PipInstallOptions = {}
): Promise<void> {
  const opts = { ...DEFAULT_OPTS, ...options };
  if (!packages || packages.length === 0) return;

  await ensureMicropip(pyodide, opts.verbose);

  // Build and run a small Python snippet to call micropip.install with options.
  // Using runPythonAsync avoids PyProxy coroutine juggling.
  const pyCode = `
import json, micropip

_pkgs = json.loads(${jsonArg(packages)})
_extra = json.loads(${jsonArg(opts.extraIndexUrls)})
_keep = ${opts.keepGoing ? "True" : "False"}
_pre  = ${opts.preRelease ? "True" : "False"}
_nodeps = ${opts.noDeps ? "True" : "False"}

# Configure additional indexes first (older micropip supports add_index_url).
for _u in _extra:
    try:
        micropip.add_index_url(_u)
    except Exception as _e:
        # Fall back: some versions only accept index_urls via install(); ignore if missing.
        pass

await micropip.install(
    _pkgs,
    keep_going=_keep,
    index_urls=_extra if len(_extra) > 0 else None,
    pre=_pre,
    deps=(not _nodeps),
)
`.trim();

  if (opts.verbose) {
    console.info(`[studio-wasm] micropip.install(${packages.join(", ")})`);
  }

  try {
    await pyodide.runPythonAsync(pyCode);
  } catch (e: any) {
    const msg = normalizeMicropipError(e);
    throw new Error(`micropip.install failed: ${msg}`);
  }
}

/** Install explicit wheel URLs (convenience wrapper; defaults to noDeps=true). */
export async function installWheels(
  pyodide: PyodideInstance,
  wheelUrls: string[],
  options: PipInstallOptions = {}
): Promise<void> {
  const opts: PipInstallOptions = { ...options, noDeps: options.noDeps ?? true };
  await installPackages(pyodide, wheelUrls, opts);
}

/**
 * Parse a requirements.txt-style string into entries suitable for install().
 * - Strips comments ("#", ";") and blank lines
 * - Preserves specifiers like "pkg==1.2.3" or direct URLs
 */
export function parseRequirementsText(text: string): RequirementParse {
  const entries: string[] = [];
  const ignored: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || line.startsWith(";")) {
      if (line) ignored.push(raw);
      continue;
    }
    // Remove inline comments after at least one whitespace
    const cleaned = line.replace(/\s+#.*$/, "").trim();
    if (cleaned) entries.push(cleaned);
  }
  return { entries, ignored };
}

/**
 * Install from a requirements.txt style content.
 */
export async function installFromRequirementsText(
  pyodide: PyodideInstance,
  text: string,
  options: PipInstallOptions = {}
): Promise<void> {
  const parsed = parseRequirementsText(text);
  if (parsed.entries.length === 0) return;
  await installPackages(pyodide, parsed.entries, options);
}

/**
 * Best-effort check whether a Python package is importable inside Pyodide.
 * This does not consult micropip metadata; it attempts an `import` in Python.
 */
export async function isPackageImportable(
  pyodide: PyodideInstance,
  moduleName: string
): Promise<boolean> {
  const code = `
_mod = None
try:
    _mod = __import__(${jsonArg(moduleName)})
    _ok = True
except Exception:
    _ok = False
_ok
`;
  try {
    const ok = await pyodide.runPythonAsync(code);
    return !!ok;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

/** Safely embed a JS value into Python via JSON literal. */
function jsonArg(v: unknown): string {
  // Ensure a minimal, safe JSON without functions or undefined
  return "`" + JSON.stringify(v ?? null).replace(/`/g, "\\`") + "`";
}

/** Improve common micropip / network error messages. */
function normalizeMicropipError(e: any): string {
  const raw = (e?.message ?? String(e)) as string;
  // Tidy some frequent patterns
  return raw
    .replace(/\s+at .+?(\n|$)/g, " ")
    .replace(/PythonError:\s*/g, "")
    .replace(/Traceback \(most recent call last\):[\s\S]*?Error:\s*/g, "Error: ")
    .trim();
}
