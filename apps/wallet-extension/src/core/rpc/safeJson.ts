/**
 * BigInt-safe and Uint8Array-safe JSON helpers used across every boundary
 * where wallet data crosses JSON (RPC bodies, chrome.runtime.sendMessage
 * arguments, encrypted vault payloads, debug logs).
 *
 * Two encodings are provided:
 *  - "rpc"     : Uint8Array → "0x..." hex (matches Animica RPC expectations).
 *  - "storage" : Uint8Array → numeric-keyed object (round-trips through the
 *                vault loader via coerceBytes — preserves legacy data shape).
 *
 * In both encodings, bigint is rendered as a decimal string. Consumers that
 * need to recover a bigint should parse via BigInt() at the read site.
 */

export type SafeEncoding = 'rpc' | 'storage';

export function toHexPrefixed(value: Uint8Array): string {
  let out = '0x';
  for (const byte of value) {
    out += byte.toString(16).padStart(2, '0');
  }
  return out;
}

function bytesToNumericObject(value: Uint8Array): Record<string, number> {
  const out: Record<string, number> = {};
  for (let i = 0; i < value.length; i += 1) {
    out[String(i)] = value[i];
  }
  return out;
}

function encodeUint8Array(value: Uint8Array, encoding: SafeEncoding): unknown {
  return encoding === 'rpc' ? toHexPrefixed(value) : bytesToNumericObject(value);
}

function isPlainArrayBufferView(value: unknown): value is ArrayBufferView {
  return (
    typeof ArrayBuffer !== 'undefined' &&
    !!value &&
    typeof (value as ArrayBufferView).byteLength === 'number' &&
    ArrayBuffer.isView(value as ArrayBufferView) &&
    !(value instanceof Uint8Array)
  );
}

function convertArrayBufferLike(value: unknown): Uint8Array | null {
  if (value instanceof ArrayBuffer) return new Uint8Array(value);
  if (isPlainArrayBufferView(value)) {
    const view = value as ArrayBufferView;
    return new Uint8Array(view.buffer, view.byteOffset, view.byteLength);
  }
  return null;
}

export function toJsonSafe(value: unknown, encoding: SafeEncoding = 'rpc'): unknown {
  if (typeof value === 'bigint') {
    return value.toString(10);
  }

  if (value instanceof Uint8Array) {
    return encodeUint8Array(value, encoding);
  }

  const arrayBufferAsBytes = convertArrayBufferLike(value);
  if (arrayBufferAsBytes) {
    return encodeUint8Array(arrayBufferAsBytes, encoding);
  }

  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null;
    return value;
  }

  if (Array.isArray(value)) {
    return value.map((item) => toJsonSafe(item, encoding));
  }

  if (value && typeof value === 'object') {
    if (value instanceof Date) return value.toISOString();
    if (value instanceof Map) {
      const out: Record<string, unknown> = {};
      for (const [k, v] of value.entries()) {
        out[String(k)] = toJsonSafe(v, encoding);
      }
      return out;
    }
    if (value instanceof Set) {
      return Array.from(value, (v) => toJsonSafe(v, encoding));
    }
    const record = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const [key, nested] of Object.entries(record)) {
      out[key] = toJsonSafe(nested, encoding);
    }
    return out;
  }

  return value;
}

export function stringifySafe(value: unknown): string {
  return JSON.stringify(toJsonSafe(value, 'rpc'));
}

/**
 * Storage-safe stringify used when writing to chrome.storage.local (e.g. the
 * encrypted vault payload). Preserves Uint8Array as a numeric-keyed object so
 * the existing vault loader (coerceBytes) can recover it on read.
 */
export function stringifyForStorage(value: unknown): string {
  return JSON.stringify(toJsonSafe(value, 'storage'));
}
