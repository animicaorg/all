/**
 * Library version.
 *
 * Build systems can inject the version at compile time using a define like
 * `__STUDIO_WASM_VERSION__`. When not provided, we try common env fallbacks
 * and finally default to "0.0.0-dev".
 */

// Optional build-time define (e.g., via Vite/Rollup/Webpack DefinePlugin)
declare const __STUDIO_WASM_VERSION__: string | undefined;

// Resolve version with graceful fallbacks (browser & node-friendly)
export const VERSION: string =
  (typeof __STUDIO_WASM_VERSION__ !== "undefined" && __STUDIO_WASM_VERSION__) ||
  // Vite-style env injection (configure via define: { 'import.meta.env.PKG_VERSION': JSON.stringify(pkg.version) })
  ((import.meta as any)?.env?.PKG_VERSION as string | undefined) ||
  // Node/SSR fallback if bundler exposes package version
  (typeof process !== "undefined" &&
    (process as any)?.env?.PKG_VERSION) ||
  "0.0.0-dev";

/** Small helper for parity with some consumers that prefer a function call. */
export function getVersion(): string {
  return VERSION;
}
