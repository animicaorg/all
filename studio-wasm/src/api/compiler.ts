/**
 * Compiler API
 * ============
 * Helpers around the Pyodide-backed Python VM compiler bridges.
 *
 * - compileSource: Python source (+ manifest) → compiled IR + hash
 * - compileIR:     pre-built IR bytes/JSON → compiled IR + hash
 * - linkManifest:  attach code hash (and optional metadata) into a manifest object
 */

import { ensurePyReady } from "./simulator";

/* ---------------------------------- Types ---------------------------------- */

export type Json = Record<string, any>;

export interface CompileSourceParams {
  /** Contract Python source (utf-8). */
  source: string;
  /** Manifest JSON (ABI + metadata); used for validation/linking in compiler. */
  manifest: Json;
  /** When true, also return the compiled artifact bytes. Default: true. */
  withBytes?: boolean;
  /** Initialize/boot options are inherited from ensurePyReady via simulator.ts */
  init?: Parameters<typeof ensurePyReady>[0];
}

export interface CompileResult {
  /** Compiled IR bytes suitable for the simulator runtime. */
  ir: Uint8Array;
  /** 0x-prefixed hex digest of the compiled artifact (best-effort). */
  codeHash?: string;
  /** Size of the compiled artifact, in bytes (if available). */
  codeSize?: number;
  /** Compiled artifact bytes (alias for `ir`). */
  artifact?: Uint8Array;
  /** Optional diagnostics emitted by the compiler. */
  diagnostics?: string[];
  /** Optional upper-bound gas estimate derived from static analysis. */
  gasUpperBound?: number;
  /** ABI emitted by the compiler, if available. */
  abi?: Json;
  /** Manifest echoed back by the compiler (or the provided manifest). */
  manifest?: Json;
  /** Entry function name if provided by the bridge. */
  entry?: string;
  /** Convenience flag mirroring bridge.ok when present. */
  ok?: boolean;
}

export interface CompileIRParams {
  /**
   * IR input, either:
   *  - Uint8Array of the encoded IR (preferred), or
   *  - a JSON-serializable IR object/string understood by the Python bridge.
   */
  ir: Uint8Array | string | Json;
  /** Optional manifest for validation/linking. */
  manifest?: Json;
  /** Also return compiled bytes; default true. */
  withBytes?: boolean;
  init?: Parameters<typeof ensurePyReady>[0];
}

/* -------------------------------- Utilities -------------------------------- */

function isUint8Array(v: unknown): v is Uint8Array {
  return v instanceof Uint8Array;
}

function decodeBase64(b64: string): Uint8Array {
  if (typeof atob === "function") {
    const s = atob(b64);
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }
  const maybeBuf = (globalThis as any).Buffer?.from?.(b64, "base64");
  if (maybeBuf) {
    const buf: any = maybeBuf;
    const offset = buf.byteOffset ?? 0;
    const length = buf.byteLength ?? buf.length ?? 0;
    return new Uint8Array(buf.buffer ?? buf, offset, length);
  }
  throw new Error("No base64 decoder available");
}

/** Normalize various byte encodings from the bridge to a Uint8Array. */
function normalizeArtifactBytes(maybe:
  | { __bytes_b64?: string }
  | { code_b64?: string }
  | { code_hex?: string }
  | { code?: string | Uint8Array }
  | string
  | Uint8Array
  | null
  | undefined
): Uint8Array | undefined {
  if (!maybe) return undefined;

  // Direct bytes already
  if (isUint8Array(maybe as any)) return maybe as Uint8Array;

  // BytesBox or explicit base64 field
  const asObj = maybe as any;
  const b64 =
    (typeof asObj === "object" && (asObj.__bytes_b64 || asObj.code_b64)) ||
    (typeof maybe === "string" && /^[A-Za-z0-9+/]+={0,2}$/.test(maybe) ? (maybe as string) : null);

  if (b64) {
    return decodeBase64(b64);
  }

  // Hex (with or without 0x)
  const hex =
    (typeof asObj === "object" && asObj.code_hex) ||
    (typeof maybe === "string" && /^(0x)?[0-9a-fA-F]*$/.test(maybe) ? (maybe as string) : null);

  if (hex) {
    const clean = hex.startsWith("0x") ? hex.slice(2) : hex;
    if (clean.length % 2 !== 0) throw new Error("Odd-length hex string from compiler");
    const out = new Uint8Array(clean.length / 2);
    for (let i = 0; i < clean.length; i += 2) {
      out[i / 2] = parseInt(clean.slice(i, i + 2), 16);
    }
    return out;
  }

  if (typeof asObj === "object" && isUint8Array(asObj.code)) {
    return asObj.code as Uint8Array;
  }

  return undefined;
}

function to0x(hexLike: string | undefined): string | undefined {
  if (!hexLike) return undefined;
  return hexLike.startsWith("0x") ? hexLike : `0x${hexLike}`;
}

function mapDiagnostics(v: any): string[] | undefined {
  const msgs = v?.diagnostics;
  if (!msgs) return undefined;
  if (Array.isArray(msgs)) return msgs.map(String);
  return [String(msgs)];
}

function asBytes(input: string): Uint8Array {
  const enc = new TextEncoder();
  return enc.encode(input);
}

function mapCompileResult(res: any, fallbackManifest?: Json): CompileResult {
  const artifact = normalizeArtifactBytes(res?.artifact ?? res?.code ?? res);
  if (!artifact) throw new Error("Compiler did not return artifact bytes");

  const hash = to0x(res?.code_hash ?? res?.hash ?? res?.codeHash);
  const gasUpperBound =
    typeof res?.gas_upper_bound === "number"
      ? res.gas_upper_bound
      : typeof res?.gasUpperBound === "number"
      ? res.gasUpperBound
      : undefined;

  return {
    ok: res?.ok !== false,
    ir: artifact,
    artifact,
    codeHash: hash,
    codeSize: typeof res?.code_size === "number" ? res.code_size : artifact?.byteLength,
    diagnostics: mapDiagnostics(res),
    gasUpperBound,
    abi: res?.abi,
    manifest: res?.manifest ?? fallbackManifest,
    entry: typeof res?.entry === "string" ? res.entry : undefined,
  };
}

/* --------------------------------- API impl -------------------------------- */

/**
 * Compile Python source to a deterministic artifact and code hash.
 * Uses `bridge.entry.compile_bytes` under the hood.
 */
export async function compileSource(params: CompileSourceParams): Promise<CompileResult> {
  const { source, manifest } = params;
  const withBytes = params.withBytes !== false;

  try {
    const client = await ensurePyReady(params.init);
    const result = await client.call(
      "bridge.entry.compile_bytes",
      [manifest],
      { source_bytes: asBytes(source), return_bytes: withBytes },
      120_000
    );

    return mapCompileResult(result, manifest);
  } catch (e) {
    throw new Error(`compileSource failed: ${e instanceof Error ? e.message : String(e)}`);
  }
}

/**
 * Compile/encode an IR payload to an artifact + hash.
 */
export async function compileIR(params: CompileIRParams): Promise<CompileResult> {
  const { ir, manifest } = params;
  const withBytes = params.withBytes !== false;

  try {
    const client = await ensurePyReady(params.init);
    const payload: Record<string, any> = { return_bytes: withBytes };
    if (isUint8Array(ir)) {
      payload.ir_bytes = Array.from(ir);
    } else if (typeof ir === "string") {
      payload.ir_obj_json = ir;
    } else {
      payload.ir_obj_json = JSON.stringify(ir);
    }

    const result = await client.call("bridge.entry.compile_bytes", [manifest ?? {}], payload, 120_000);
    return mapCompileResult(result, manifest);
  } catch (e) {
    throw new Error(`compileIR failed: ${e instanceof Error ? e.message : String(e)}`);
  }
}

/**
 * Return a new manifest with the code hash linked in, preserving existing fields.
 * Writes both `codeHash` (camelCase) and `code_hash` (snake_case) for compatibility.
 */
export function linkManifest(manifest: Json, codeHash: string, extras?: Partial<Json>): Json {
  const hash = to0x(codeHash)!;
  return {
    ...manifest,
    codeHash: hash,
    code_hash: hash,
    ...(extras ?? {}),
  };
}

/* --------------------------------- Exports --------------------------------- */

export default {
  compileSource,
  compileIR,
  linkManifest,
};
