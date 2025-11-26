/**
 * Compile slice — tracks compile state for the active contract file:
 * - status & timestamps
 * - IR bytes (Uint8Array)
 * - diagnostics (path/pos/severity)
 * - static gas upper bound (from compiler estimator)
 *
 * Integrates with studio-web/src/services/wasm.ts which exposes compile helpers
 * backed by the studio-wasm package (Pyodide).
 */

import useStore, { registerSlice, type SliceCreator, type StoreState, type SetState, type GetState } from './store';
import * as Wasm from '../services/wasm';

export type Sev = 'error' | 'warning' | 'info';

export interface Diagnostic {
  path: string;
  message: string;
  severity: Sev;
  line?: number;
  column?: number;
  endLine?: number;
  endColumn?: number;
  code?: string | number;
}

export interface CompileOutputs {
  ir?: Uint8Array;
  gasUpperBound?: number;
  version?: string;
}

export interface CompileSlice {
  // state
  status: 'idle' | 'compiling' | 'success' | 'error';
  inputPath?: string;
  manifestPath?: string;
  ir?: Uint8Array;
  gasUpperBound?: number;
  diagnostics: Diagnostic[];
  error?: string;
  version?: string;
  lastCompiledAt?: number;
  lastRequestedAt?: number;
  _reqId: number; // internal for stale-request filtering

  // actions
  clear(): void;
  setManifestPath(path?: string): void;

  /**
   * Compile from explicit source (preferred in hooks)
   */
  compile(path: string, source: string, opts?: { manifestJson?: any; manifestPath?: string }): Promise<boolean>;

  /**
   * Convenience: compile the currently active file from the project slice.
   * If path is provided, compiles that file; otherwise uses project.active.
   */
  compileFromProject(path?: string): Promise<boolean>;

  /**
   * Inject diagnostics (e.g., from editor/monaco) without changing IR.
   */
  setDiagnostics(diags: Diagnostic[]): void;
}

function toDiagnostics(maybe: any, fallbackPath?: string): Diagnostic[] {
  if (!maybe) return [];
  if (Array.isArray(maybe)) {
    return maybe.map((d) => ({
      path: d.path ?? fallbackPath ?? '',
      message: String(d.message ?? d.msg ?? 'Unknown'),
      severity: (d.severity ?? d.level ?? 'error') as Sev,
      line: num(d.line),
      column: num(d.column),
      endLine: num(d.endLine),
      endColumn: num(d.endColumn),
      code: d.code,
    }));
  }
  // single object
  return [
    {
      path: maybe.path ?? fallbackPath ?? '',
      message: String(maybe.message ?? maybe.msg ?? 'Unknown'),
      severity: (maybe.severity ?? maybe.level ?? 'error') as Sev,
      line: num(maybe.line),
      column: num(maybe.column),
      endLine: num(maybe.endLine),
      endColumn: num(maybe.endColumn),
      code: maybe.code,
    },
  ];
}

function num(v: any): number | undefined {
  const n = Number(v);
  return Number.isFinite(n) ? n : undefined;
}

function now(): number {
  return Date.now();
}

const compileSlice: SliceCreator<CompileSlice> = (set: SetState<StoreState>, get: GetState<StoreState>) => ({
  status: 'idle',
  inputPath: undefined,
  manifestPath: undefined,
  ir: undefined,
  gasUpperBound: undefined,
  diagnostics: [],
  error: undefined,
  version: undefined,
  lastCompiledAt: undefined,
  lastRequestedAt: undefined,
  _reqId: 0,

  clear() {
    set({
      status: 'idle',
      ir: undefined,
      gasUpperBound: undefined,
      diagnostics: [],
      error: undefined,
      version: undefined,
    } as Partial<StoreState>);
  },

  setManifestPath(path?: string) {
    set({ manifestPath: path } as Partial<StoreState>);
  },

  async compile(path: string, source: string, opts?: { manifestJson?: any; manifestPath?: string }): Promise<boolean> {
    const myId = (get() as unknown as CompileSlice)._reqId + 1;
    set({ status: 'compiling', inputPath: path, error: undefined, diagnostics: [], lastRequestedAt: now(), _reqId: myId } as Partial<StoreState>);

    try {
      // The wasm service may expose either named helpers or a namespaced object.
      // We try a few shapes for resilience.
      const compiler: any = Wasm as any;

      const manifestJson = opts?.manifestJson;
      const manifestPath = opts?.manifestPath ?? (get() as unknown as CompileSlice).manifestPath;

      // compileSource expected to return { irBytes?, diagnostics?, gasUpperBound?, version? }
      const res: any =
        typeof compiler.compileSource === 'function'
          ? await compiler.compileSource({ path, source, manifest: manifestJson, manifestPath })
          : typeof compiler.getCompiler === 'function'
            ? await (async () => {
                const api = await compiler.getCompiler();
                return api.compileSource({ path, source, manifest: manifestJson, manifestPath });
              })()
            : (() => {
                throw new Error('WASM compiler not available');
              })();

      // Stale request guard
      if ((get() as unknown as CompileSlice)._reqId !== myId) {
        return false;
      }

      const ir: Uint8Array | undefined =
        res?.irBytes instanceof Uint8Array
          ? (res.irBytes as Uint8Array)
          : Array.isArray(res?.irBytes)
            ? new Uint8Array(res.irBytes as number[])
            : res?.ir instanceof Uint8Array
              ? (res.ir as Uint8Array)
              : undefined;

      const diags = toDiagnostics(res?.diagnostics, path);
      const gasUpperBound = res?.gasUpperBound ?? res?.gas ?? undefined;
      const version = res?.version ?? res?.vmVersion ?? undefined;

      const status: CompileSlice['status'] = diags.some((d) => d.severity === 'error') ? 'error' : 'success';

      set({
        status,
        ir,
        gasUpperBound,
        diagnostics: diags,
        version,
        lastCompiledAt: now(),
        error: status === 'error' && !ir ? 'Compilation failed' : undefined,
      } as Partial<StoreState>);

      return status === 'success' || Boolean(ir);
    } catch (e: any) {
      if ((get() as unknown as CompileSlice)._reqId !== myId) {
        return false;
      }
      set({
        status: 'error',
        error: String(e?.message ?? e),
        lastCompiledAt: now(),
      } as Partial<StoreState>);
      return false;
    }
  },

  async compileFromProject(path?: string): Promise<boolean> {
    // Best-effort read from project slice without tight typing to avoid circular deps.
    const s = get() as any;
    const project = s as { files?: Record<string, { path: string; content: string }>; active?: string };
    const p = path ?? project.active;
    if (!p) {
      set({ status: 'error', error: 'No active file to compile' } as Partial<StoreState>);
      return false;
    }
    const file = project.files?.[p];
    if (!file) {
      set({ status: 'error', error: `File not found: ${p}` } as Partial<StoreState>);
      return false;
    }

    // Try to auto-locate a sibling manifest if present in project state
    let manifestJson: any = undefined;
    let manifestPath: string | undefined = (get() as unknown as CompileSlice).manifestPath;
    try {
      if (!manifestPath && project.files) {
        // Heuristics: prefer '<dir>/manifest.json' or 'manifest.json' at root
        const dir = p.includes('/') ? p.split('/').slice(0, -1).join('/') : '';
        const candidates = [dir ? `${dir}/manifest.json` : 'manifest.json', 'manifest.json'];
        for (const c of candidates) {
          if (project.files[c]) {
            manifestPath = c;
            break;
          }
        }
      }
      if (manifestPath && project.files?.[manifestPath]) {
        manifestJson = JSON.parse(project.files[manifestPath].content);
      }
    } catch {
      // ignore JSON parse errors — compiler will report
    }

    return (get() as unknown as CompileSlice).compile(p, file.content, { manifestJson, manifestPath });
  },

  setDiagnostics(diags: Diagnostic[]) {
    set({ diagnostics: diags } as Partial<StoreState>);
  },
});

registerSlice<CompileSlice>(compileSlice);

export const useCompileStore = useStore;

export default undefined;
