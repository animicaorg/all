/**
 * LEB128 uvarint encoding (little-endian base-128)
 * Used for length prefixes in SignBytes construction
 */
export function encodeUvarint(n: number): Uint8Array {
  if (n < 0) {
    throw new Error('uvarint expects non-negative integer');
  }
  
  const bytes: number[] = [];
  while (true) {
    let b = n & 0x7F;
    n >>>= 7; // Unsigned right shift
    if (n === 0) {
      bytes.push(b);
      break;
    }
    bytes.push(b | 0x80);
  }
  
  return new Uint8Array(bytes);
}

/**
 * Decode LEB128 uvarint
 * Returns [value, bytesRead]
 */
export function decodeUvarint(bytes: Uint8Array, offset: number = 0): [number, number] {
  let value = 0;
  let shift = 0;
  let i = offset;
  
  while (i < bytes.length) {
    const b = bytes[i];
    value |= (b & 0x7F) << shift;
    i++;
    
    if ((b & 0x80) === 0) {
      return [value, i - offset];
    }
    
    shift += 7;
    if (shift > 63) {
      throw new Error('uvarint overflow');
    }
  }
  
  throw new Error('uvarint: unexpected end of input');
}

/**
 * Length-prefix a byte array (uvarint length || bytes)
 */
export function lengthPrefix(data: Uint8Array): Uint8Array {
  const len = encodeUvarint(data.length);
  const result = new Uint8Array(len.length + data.length);
  result.set(len, 0);
  result.set(data, len.length);
  return result;
}

/**
 * Concatenate multiple Uint8Arrays
 */
export function concatBytes(...arrays: Uint8Array[]): Uint8Array {
  const totalLength = arrays.reduce((sum, arr) => sum + arr.length, 0);
  const result = new Uint8Array(totalLength);
  let offset = 0;
  for (const arr of arrays) {
    result.set(arr, offset);
    offset += arr.length;
  }
  return result;
}

/**
 * Convert hex string to Uint8Array
 */
export function hexToBytes(hex: string): Uint8Array {
  // Remove 0x prefix if present
  const clean = hex.startsWith('0x') ? hex.slice(2) : hex;
  
  if (clean.length % 2 !== 0) {
    throw new Error('Hex string must have even length');
  }
  
  const bytes = new Uint8Array(clean.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(clean.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

/**
 * Convert Uint8Array to hex string
 */
export function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes)
    .map(b => b.toString(16).padStart(2, '0'))
    .join('');
}

/**
 * Normalize domain to bytes
 */
export function normalizeDomain(domain: string | Uint8Array): Uint8Array {
  if (domain instanceof Uint8Array) {
    if (domain.length === 0) {
      throw new Error('Domain must be non-empty');
    }
    return domain;
  }
  
  const trimmed = domain.trim();
  if (!trimmed) {
    throw new Error('Domain must be non-empty');
  }
  
  return new TextEncoder().encode(trimmed);
}

/**
 * Safe comparison of byte arrays (constant-time for equal lengths)
 */
export function bytesEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) {
    return false;
  }
  
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= a[i] ^ b[i];
  }
  return diff === 0;
}
