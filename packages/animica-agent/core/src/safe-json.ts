/**
 * BigInt-safe JSON helpers for the Animica Coding Agent.
 *
 * The Animica ecosystem (wallet extension, RPC, miner metrics) historically
 * mixes `bigint`, hex strings, and decimal strings for the same on-chain values.
 * JSON.stringify cannot serialize bigint and JSON.parse cannot recover it, so
 * every CLI/UI/API surface in the agent routes through this module.
 *
 * Design:
 *   - Encoded form: `{ "__bn": "<decimal>" }` for bigint values.
 *   - Plain decimal and hex strings are NOT auto-coerced; only the explicit
 *     wrapper round-trips. This avoids accidentally turning user-supplied
 *     decimal strings (e.g. block numbers represented as "100") into bigint.
 *   - `safeStringify(v, true)` produces a hex-friendly form: bigint -> 0x-prefix
 *     decimal-zero-stripped lowercase hex, which matches Animica RPC convention.
 */

export const BIGINT_WRAPPER_KEY = "__bn";

export type SafeStringifyOptions = {
  /** If true, emit bigint as 0x-prefixed hex strings rather than `{__bn}` wrappers. */
  hex?: boolean;
  /** Indent passed straight to JSON.stringify. */
  indent?: number;
};

function defaultReplacer(_key: string, value: unknown): unknown {
  if (typeof value === "bigint") {
    return { [BIGINT_WRAPPER_KEY]: value.toString(10) };
  }
  return value;
}

function hexReplacer(_key: string, value: unknown): unknown {
  if (typeof value === "bigint") {
    if (value < 0n) {
      // Match ethers/animica RPC: negative bigints are unsupported in RPC.
      return value.toString(10);
    }
    return "0x" + value.toString(16);
  }
  return value;
}

export function safeStringify(value: unknown, options: SafeStringifyOptions | boolean = {}): string {
  const opts: SafeStringifyOptions = typeof options === "boolean" ? { hex: options } : options;
  const replacer = opts.hex ? hexReplacer : defaultReplacer;
  return JSON.stringify(value, replacer, opts.indent);
}

function isBigIntWrapper(v: unknown): v is { [BIGINT_WRAPPER_KEY]: string } {
  return (
    typeof v === "object" &&
    v !== null &&
    Object.keys(v).length === 1 &&
    BIGINT_WRAPPER_KEY in (v as Record<string, unknown>) &&
    typeof (v as Record<string, unknown>)[BIGINT_WRAPPER_KEY] === "string"
  );
}

export function safeParse<T = unknown>(text: string): T {
  return JSON.parse(text, (_key, value) => {
    if (isBigIntWrapper(value)) {
      return BigInt(value[BIGINT_WRAPPER_KEY]);
    }
    return value;
  }) as T;
}

/** Strict bigint parser that accepts decimal or 0x-hex input, never coerces other types. */
export function toBigInt(input: string | number | bigint): bigint {
  if (typeof input === "bigint") return input;
  if (typeof input === "number") {
    if (!Number.isSafeInteger(input)) {
      throw new RangeError(`Unsafe integer for bigint conversion: ${input}`);
    }
    return BigInt(input);
  }
  const trimmed = input.trim();
  if (trimmed.length === 0) throw new SyntaxError("Empty string cannot be bigint");
  if (/^-?0x[0-9a-fA-F]+$/.test(trimmed)) {
    const negative = trimmed.startsWith("-");
    const hex = negative ? trimmed.slice(1) : trimmed;
    const bn = BigInt(hex);
    return negative ? -bn : bn;
  }
  if (/^-?\d+$/.test(trimmed)) return BigInt(trimmed);
  throw new SyntaxError(`Invalid bigint literal: ${input}`);
}

/** Redact common secret-like keys before logging. Idempotent and bigint-safe. */
const SECRET_KEYS = new Set([
  "password",
  "passphrase",
  "privatekey",
  "private_key",
  "secret",
  "seed",
  "mnemonic",
  "authorization",
  "api_key",
  "apikey",
  "bearer",
  "session_token",
  "sessiontoken",
  "cookie",
]);

export function redact(value: unknown): unknown {
  if (value === null || value === undefined) return value;
  if (typeof value === "bigint") return value; // bigint cannot be a secret string
  if (Array.isArray(value)) return value.map(redact);
  if (typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
      if (SECRET_KEYS.has(k.toLowerCase())) {
        out[k] = "[REDACTED]";
      } else {
        out[k] = redact(v);
      }
    }
    return out;
  }
  return value;
}
