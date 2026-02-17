export function toHexPrefixed(value: Uint8Array): string {
  let out = '0x';
  for (const byte of value) {
    out += byte.toString(16).padStart(2, '0');
  }
  return out;
}

export function toJsonSafe(value: unknown): unknown {
  if (typeof value === 'bigint') {
    return value.toString(10);
  }

  if (value instanceof Uint8Array) {
    return toHexPrefixed(value);
  }

  if (Array.isArray(value)) {
    return value.map((item) => toJsonSafe(item));
  }

  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    const out: Record<string, unknown> = {};
    for (const [key, nested] of Object.entries(record)) {
      out[key] = toJsonSafe(nested);
    }
    return out;
  }

  return value;
}

export function stringifySafe(value: unknown): string {
  return JSON.stringify(toJsonSafe(value));
}

