/**
 * Event decoder helpers
 * =====================
 * Utilities to:
 *  - Build a quick lookup index over ABI events
 *  - Normalize/pretty-print simulator events
 *  - Decode args when a simulator/bridge returns raw-encoded payloads
 *  - Compute an event signature hash (topic0) compatible with keccak(name(types))
 *
 * Notes
 * -----
 * The Pyodide simulator usually already returns decoded events:
 *    { name: string, args: Record<string, any> }
 * These helpers gracefully handle both decoded and raw payloads (hex/base64/bytes).
 */

import { hexToBytes, isHexPrefixed, bytesToUtf8 } from "../utils/bytes";
import { decodeCbor } from "../utils/cbor";
import { keccak256 } from "../utils/hash";

/* ---------------------------------- Types ---------------------------------- */

export type Json = Record<string, any>;

export interface AbiParam {
  name: string;
  type: string; // e.g. "int", "bool", "bytes", "address", "string", "bytes32", "list<uint64>"
  indexed?: boolean; // reserved for future compatibility
}

export interface AbiEvent {
  name: string;
  inputs: AbiParam[];
  anonymous?: boolean; // reserved
}

export interface ContractAbi {
  events?: AbiEvent[];
}

export interface RawEvent {
  name: string | Uint8Array;
  /** Either fully-decoded args (object), or encoded bytes/hex/base64 string. */
  args: Json | Uint8Array | string | null;
  /** Optional raw topics/data for future compatibility with receipts. */
  topics?: (string | Uint8Array)[];
  data?: string | Uint8Array;
}

export interface DecodedEvent {
  name: string;
  /** Fully-decoded args keyed by param name. */
  args: Json;
  /** Convenience signature 'Name(type1,type2,...)' */
  signature?: string;
  /** keccak256(signature) as 0x hex; useful when matching by topic0 in future. */
  topic0?: string;
}

/* ------------------------------- ABI indexing ------------------------------ */

export interface EventIndex {
  byName: Map<string, AbiEvent[]>;
}

export function buildEventIndex(abi: ContractAbi | undefined | null): EventIndex {
  const byName = new Map<string, AbiEvent[]>();
  for (const ev of abi?.events ?? []) {
    const key = (ev.name || "").toString();
    if (!byName.has(key)) byName.set(key, []);
    byName.get(key)!.push(ev);
  }
  return { byName };
}

/* ----------------------------- Helper utilities ---------------------------- */

function isUint8Array(v: unknown): v is Uint8Array {
  return v instanceof Uint8Array;
}

function tryBase64ToBytes(s: string): Uint8Array | null {
  // A very loose base64 check: contains only valid base64 chars and optional '=' padding.
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(s)) return null;
  try {
    const bin = atob(s);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  } catch {
    return null;
  }
}

function normalizeName(n: string | Uint8Array): string {
  return typeof n === "string" ? n : bytesToUtf8(n);
}

function toBytes(maybe: string | Uint8Array): Uint8Array {
  if (isUint8Array(maybe)) return maybe;
  if (isHexPrefixed(maybe)) return hexToBytes(maybe);
  const b = tryBase64ToBytes(maybe);
  if (b) return b;
  // If it looks like plain UTF-8 JSON, return bytes of it; caller may decode JSON.
  return new TextEncoder().encode(maybe);
}

/* ------------------------------- Type casting ------------------------------ */

/**
 * Very small & pragmatic caster to align decoded JSON-ish values
 * to ABI param types. Simulator already returns JS-native values
 * for standard scalars; this only tweaks a few edge cases.
 */
function castToType(type: string, value: any): any {
  const t = type.toLowerCase();

  if (t === "bool") {
    if (typeof value === "boolean") return value;
    if (typeof value === "number") return value !== 0;
    if (typeof value === "string") return value === "true" || value === "1";
    return Boolean(value);
  }

  if (t === "int" || t === "int64" || t === "uint64" || t === "uint" || t === "u256" || t === "i256") {
    if (typeof value === "number") return value; // ok for small ints
    if (typeof value === "bigint") return value.toString(); // keep precision as string
    if (typeof value === "string") {
      // pass through numeric-looking strings (including big ints) untouched
      return value.trim();
    }
    // Fallback: JSON stringify (caller may post-process)
    return JSON.stringify(value);
  }

  if (t === "string") {
    if (typeof value === "string") return value;
    if (isUint8Array(value)) return bytesToUtf8(value);
    return String(value);
  }

  if (t === "address") {
    // Expect 0x-hex or bech32 upstream; here we just stringify.
    return typeof value === "string" ? value : String(value);
  }

  if (t.startsWith("bytes")) {
    if (typeof value === "string" && isHexPrefixed(value)) return value.toLowerCase();
    if (isUint8Array(value)) return "0x" + Array.from(value).map(b => b.toString(16).padStart(2, "0")).join("");
    if (typeof value === "string") {
      const b = tryBase64ToBytes(value);
      if (b) return "0x" + Array.from(b).map(x => x.toString(16).padStart(2, "0")).join("");
    }
    return typeof value === "string" ? value : JSON.stringify(value);
  }

  if (t.startsWith("list<") && t.endsWith(">")) {
    const inner = t.slice(5, -1);
    if (Array.isArray(value)) return value.map(v => castToType(inner, v));
    return [];
  }

  // Default: return value as-is.
  return value;
}

/* ---------------------------- Signature & topic0 --------------------------- */

export function eventSignature(ev: AbiEvent): string {
  const types = (ev.inputs ?? []).map(p => p.type);
  return `${ev.name}(${types.join(",")})`;
}

export function eventTopic0(ev: AbiEvent): string {
  return keccak256(new TextEncoder().encode(eventSignature(ev)));
}

/* ------------------------------- Decode logic ------------------------------ */

function guessDecodeArgs(raw: Uint8Array | string): any {
  // Try CBOR first (simulator commonly uses canonical CBOR)
  try {
    const buf = typeof raw === "string" ? toBytes(raw) : raw;
    return decodeCbor(buf);
  } catch {
    // Not CBOR; try UTF-8 JSON
    try {
      const text = typeof raw === "string" ? raw : new TextDecoder().decode(raw);
      return JSON.parse(text);
    } catch {
      // Give up; return opaque
      return raw;
    }
  }
}

/**
 * Decode a single event given an ABI index. If multiple ABI entries share
 * the same name, we pick the first (common in simple demos).
 */
export function decodeEvent(e: RawEvent, index?: EventIndex): DecodedEvent {
  const name = normalizeName(e.name);
  const abiCandidates = index?.byName.get(name) ?? [];
  const abi = abiCandidates[0];

  // If args already look like an object, adopt them and (optionally) cast.
  if (e.args && typeof e.args === "object" && !isUint8Array(e.args)) {
    const argsObj = e.args as Json;
    let casted = argsObj;

    if (abi) {
      // Cast scalars per declared types where possible.
      const next: Json = {};
      for (const p of abi.inputs ?? []) {
        if (Object.prototype.hasOwnProperty.call(argsObj, p.name)) {
          next[p.name] = castToType(p.type, (argsObj as any)[p.name]);
        }
      }
      casted = { ...argsObj, ...next };
    }

    return {
      name,
      args: casted,
      signature: abi ? eventSignature(abi) : undefined,
      topic0: abi ? eventTopic0(abi) : undefined,
    };
  }

  // Otherwise, attempt to decode the raw payload (CBOR/JSON/opaque)
  const decoded = e.args ? guessDecodeArgs(e.args as any) : {};
  const args: Json = {};

  if (abi && decoded && typeof decoded === "object" && !Array.isArray(decoded)) {
    for (const p of abi.inputs ?? []) {
      const v = (decoded as any)[p.name];
      args[p.name] = castToType(p.type, v);
    }
  } else if (decoded && Array.isArray(decoded) && abi) {
    // Positional array → map by ABI input order
    (abi.inputs ?? []).forEach((p, i) => {
      args[p.name] = castToType(p.type, decoded[i]);
    });
  } else if (decoded && typeof decoded === "object") {
    Object.assign(args, decoded as any);
  }

  return {
    name,
    args,
    signature: abi ? eventSignature(abi) : undefined,
    topic0: abi ? eventTopic0(abi) : undefined,
  };
}

/**
 * Decode all events in a list, using the provided ABI (optional).
 */
export function decodeEvents(
  events: RawEvent[],
  abi?: ContractAbi | null
): DecodedEvent[] {
  const idx = buildEventIndex(abi ?? undefined);
  return events.map(ev => decodeEvent(ev, idx));
}

/* ------------------------------ Pretty printing ---------------------------- */

export function formatEvent(e: DecodedEvent): string {
  const args = Object.entries(e.args ?? {})
    .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(", ");
  return `${e.name}(${args})`;
}

export default {
  buildEventIndex,
  eventSignature,
  eventTopic0,
  decodeEvent,
  decodeEvents,
  formatEvent,
};
