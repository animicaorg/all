/**
 * Compiler API
 * ============
 * Helpers around the Pyodide-backed Python VM compiler bridges.
 *
 * - compileSource: Python source (+ manifest) → compiled artifact + hash
 * - compileIR:     pre-built IR bytes/JSON → compiled artifact + hash (best-effort)
 * - linkManifest:  attach code hash (and optional metadata) into a manifest object
 */

import { PyVmWorkerClient } from "../worker/protocol";
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
  /** 0x-prefixed hex digest of the compiled artifact. */
  codeHash: string;
  /** Size of the compiled artifact, in bytes (if available). */
  codeSize?: number;
  /** Compiled artifact bytes (when requested and available). */
  artifact?: Uint8Array;
  /** Optional diagnostics emitted by the compiler. */
  diagnostics?: string[];
  /** Optional upper-bound gas estimate derived from static analysis. */
  gasUpperBound?: number;
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

/** Normalize various byte encodings from the bridge to a Uint8Array. */
function normalizeArtifactBytes(maybe:
  | { __bytes_b64?: string }
  | { code_b64?: string }
  | { code_hex?: string }
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
    const s = atob(b64);
    const out = new Uint8Array(s.length);
    for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
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

  return undefined;
}

function to0x(hexLike: string | undefined): string | undefined {
  if (!hexLike) return undefined;
  return hexLike.startsWith("0x") ? hexLike : `0x${hexLike}`;
}

function okHashOrThrow(v: any): string {
  const h = typeof v === "string" ? v : v?.code_hash ?? v?.hash ?? v?.digest;
  const hx = to0x(h);
  if (!hx || !/^0x[0-9a-fA-F]+$/.test(hx)) throw new Error("Compiler did not return a valid code hash");
  return hx.toLowerCase();
}

function mapDiagnostics(v: any): string[] | undefined {
  const msgs = v?.diagnostics;
  if (!msgs) return undefined;
  if (Array.isArray(msgs)) return msgs.map(String);
  return [String(msgs)];
}

/* --------------------------------- API impl -------------------------------- */

/**
 * Compile Python source to a deterministic artifact and code hash.
 * Uses `bridge.entry.compile_bytes` under the hood.
 */
export async function compileSource(params: CompileSourceParams): Promise<CompileResult> {
  const { source, manifest } = params;
  const withBytes = params.withBytes !== false;

  let client: PyVmWorkerClient | null = null;
  try {
    client = await ensurePyReady(params.init);

    const result = await client.call(
      "bridge.entry.compile_bytes",
      [],
      { source, manifest, return_bytes: withBytes },
      120_000
    );

    const codeHash = okHashOrThrow(result);
    const artifact = withBytes ? normalizeArtifactBytes(result?.artifact ?? result) : undefined;
    const codeSize =
      typeof result?.code_size === "number"
        ? result.code_size
        : artifact?.byteLength;

    return {
      codeHash,
      codeSize,
      artifact,
      diagnostics: mapDiagnostics(result),
      gasUpperBound: typeof result?.gas_upper_bound === "number" ? result.gas_upper_bound : undefined,
    };
  } catch (e) {
    throw new Error(`compileSource failed: ${e instanceof Error ? e.message : String(e)}`);
  }
}

/**
 * Compile/encode an IR payload to an artifact + hash.
 * Tries, in order:
 *  1) bridge.entry.compile_ir (preferred)
 *  2) bridge.entry.compile_ir_bytes
 *  3) bridge.entry.compile_bytes when given a textual IR (fallback)
 */
export async function compileIR(params: CompileIRParams): Promise<CompileResult> {
  const { ir, manifest } = params;
  const withBytes = params.withBytes !== false;

  const client = await ensurePyReady(params.init);

  // Helper to dispatch a call and map response.
  const run = async (fqfn: string, payload: any) => {
    const res = await client.call(fqfn, [], { ...payload, return_bytes: withBytes }, 120_000);
    const codeHash = okHashOrThrow(res);
    const artifact = withBytes ? normalizeArtifactBytes(res?.artifact ?? res) : undefined;
    const codeSize =
      typeof res?.code_size === "number" ? res.code_size : artifact?.byteLength;

    return {
      codeHash,
      codeSize,
      artifact,
      diagnostics: mapDiagnostics(res),
      gasUpperBound: typeof res?.gas_upper_bound === "number" ? res.gas_upper_bound : undefined,
    } as CompileResult;
  };

  try {
    // Prefer a dedicated IR endpoint if present.
    try {
      return await run("bridge.entry.compile_ir", { ir, manifest });
    } catch {
      // continue
    }
    try {
      return await run("bridge.entry.compile_ir_bytes", {
        ir_bytes: isUint8Array(ir) ? Array.from(ir) : ir,
        manifest,
      });
    } catch {
      // continue
    }

    // Fallback: if IR is textual JSON the bridge might accept it via compile_bytes too.
    if (typeof ir === "string") {
      return await run("bridge.entry.compile_bytes", { source: ir, manifest });
    }

    throw new Error("No compatible IR compile endpoint found in bridge");
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
