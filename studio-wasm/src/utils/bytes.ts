/**
 * bytes.ts
 * --------
 * Small, dependency-free utilities for working with bytes/hex/base64/utf8.
 * All hex is normalized to lowercase.
 */

/* ------------------------------- Type Guards ------------------------------- */

export function isBytes(v: unknown): v is Uint8Array {
  return v instanceof Uint8Array;
}

export function isHexPrefixed(s: unknown): s is string {
  return typeof s === "string" && /^0x[0-9a-fA-F]*$/.test(s.trim());
}

/* --------------------------------- Hex I/O -------------------------------- */

export function add0x(hex: string): `0x${string}` {
  const h = hex.startsWith("0x") || hex.startsWith("0X") ? hex.slice(2) : hex;
  return ("0x" + h.toLowerCase()) as `0x${string}`;
}

export function trim0x(hex: string): string {
  return hex.startsWith("0x") || hex.startsWith("0X") ? hex.slice(2) : hex;
}

export function assertHex(maybeHex: string, { even = true } = {}): void {
  const h = trim0x(maybeHex);
  if (!/^[0-9a-fA-F]*$/.test(h)) {
    throw new Error(`Invalid hex string: ${maybeHex}`);
  }
  if (even && (h.length % 2) !== 0) {
    throw new Error(`Hex length must be even, got ${h.length}: ${maybeHex}`);
  }
}

export function hexToBytes(hex: string): Uint8Array {
  const clean = trim0x(hex).replace(/\s+/g, "");
  if (!/^[0-9a-fA-F]*$/.test(clean)) {
    throw new Error(`Invalid hex string: ${hex}`);
  }
  const h = clean.length % 2 === 1 ? "0" + clean : clean;
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i++) {
    out[i] = parseInt(h.substr(i * 2, 2), 16);
  }
  return out;
}

export function bytesToHex(bytes: Uint8Array, { withPrefix = true }: { withPrefix?: boolean } = {}): string {
  const hex = Array.from(bytes)
    .map(b => b.toString(16).padStart(2, "0"))
    .join("");
  return withPrefix ? "0x" + hex : hex;
}

/* -------------------------------- UTF-8 I/O ------------------------------- */

export function utf8ToBytes(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}

export function bytesToUtf8(bytes: Uint8Array): string {
  return new TextDecoder().decode(bytes);
}

/* ------------------------------- Base64 I/O -------------------------------- */

export function toBase64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

export function fromBase64(b64: string): Uint8Array {
  const bin = atob(b64);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

/** RFC 4648 base64url helpers */
export function toBase64Url(bytes: Uint8Array): string {
  return toBase64(bytes).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

export function fromBase64Url(b64url: string): Uint8Array {
  const pad = b64url.length % 4 === 2 ? "==" : b64url.length % 4 === 3 ? "=" : "";
  const b64 = b64url.replace(/-/g, "+").replace(/_/g, "/") + pad;
  return fromBase64(b64);
}

/* --------------------------------- Buffers -------------------------------- */

export function concatBytes(...arrays: Uint8Array[]): Uint8Array {
  const len = arrays.reduce((n, a) => n + a.length, 0);
  const out = new Uint8Array(len);
  let off = 0;
  for (const a of arrays) {
    out.set(a, off);
    off += a.length;
  }
  return out;
}

export function equalBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

export function bytesFrom(
  input: string | ArrayBuffer | ArrayBufferView | number[] | Uint8Array
): Uint8Array {
  if (typeof input === "string") {
    if (isHexPrefixed(input) || /^[0-9a-fA-F]+$/.test(trim0x(input))) return hexToBytes(input);
    return utf8ToBytes(input);
  }
  if (input instanceof Uint8Array) return new Uint8Array(input);
  if (ArrayBuffer.isView(input)) return new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  if (input instanceof ArrayBuffer) return new Uint8Array(input);
  if (Array.isArray(input)) return new Uint8Array(input);
  throw new Error("Unsupported input for bytesFrom");
}

/* ---------------------------- Integer Conversions -------------------------- */

export function numberToBytesBE(n: number | bigint, byteLength: number): Uint8Array {
  let x = typeof n === "bigint" ? n : BigInt(n >>> 0);
  const out = new Uint8Array(byteLength);
  for (let i = byteLength - 1; i >= 0; i--) {
    out[i] = Number(x & 0xffn);
    x >>= 8n;
  }
  return out;
}

export function bytesToNumberBE(bytes: Uint8Array): bigint {
  let x = 0n;
  for (const b of bytes) x = (x << 8n) | BigInt(b);
  return x;
}

/* ------------------------------- Misc helpers ------------------------------ */

export function leftPadBytes(bytes: Uint8Array, length: number): Uint8Array {
  if (bytes.length >= length) return bytes;
  const out = new Uint8Array(length);
  out.set(bytes, length - bytes.length);
  return out;
}

export function rightPadBytes(bytes: Uint8Array, length: number): Uint8Array {
  if (bytes.length >= length) return bytes;
  const out = new Uint8Array(length);
  out.set(bytes, 0);
  return out;
}

export default {
  isBytes,
  isHexPrefixed,
  add0x,
  trim0x,
  assertHex,
  hexToBytes,
  bytesToHex,
  utf8ToBytes,
  bytesToUtf8,
  toBase64,
  fromBase64,
  toBase64Url,
  fromBase64Url,
  concatBytes,
  equalBytes,
  bytesFrom,
  numberToBytesBE,
  bytesToNumberBE,
  leftPadBytes,
  rightPadBytes,
};
