// Minimal stub of @animica/studio-wasm for tests and offline environments.
export function createState() {
  return {} as Record<string, unknown>;
}

export const state = { create: createState };

export async function compileSource() {
  return { ok: true, ir: new Uint8Array(), diagnostics: [], gasUpperBound: 0 };
}

export async function simulateCall() {
  return { ok: true, return: null, logs: [], gasUsed: 0 };
}

export async function estimateGas() {
  return 0;
}

export async function boot() { /* no-op */ }
export async function load() { /* no-op */ }
export async function init() { /* no-op */ }
