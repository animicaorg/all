/**
 * Canonical CBOR encoding for Animica transactions
 * 
 * This module provides deterministic CBOR encoding that matches
 * the node's canonical encoding (core/encoding/cbor.py).
 * 
 * Key requirements:
 * - Map keys MUST be sorted by their encoded byte representation
 * - Integers MUST use minimal encoding
 * - No indefinite-length items
 * - Deterministic output for same input
 */

/**
 * Encode an additional information byte sequence for CBOR
 */
function encodeAdditionalInfo(major: number, value: bigint): number[] {
  if (value < 24n) {
    return [(major << 5) | Number(value)];
  }
  if (value <= 0xffn) {
    return [(major << 5) | 24, Number(value)];
  }
  if (value <= 0xffffn) {
    return [
      (major << 5) | 25,
      Number((value >> 8n) & 0xffn),
      Number(value & 0xffn),
    ];
  }
  if (value <= 0xffffffffn) {
    return [
      (major << 5) | 26,
      Number((value >> 24n) & 0xffn),
      Number((value >> 16n) & 0xffn),
      Number((value >> 8n) & 0xffn),
      Number(value & 0xffn),
    ];
  }
  if (value <= 0xffffffffffffffffn) {
    return [
      (major << 5) | 27,
      Number((value >> 56n) & 0xffn),
      Number((value >> 48n) & 0xffn),
      Number((value >> 40n) & 0xffn),
      Number((value >> 32n) & 0xffn),
      Number((value >> 24n) & 0xffn),
      Number((value >> 16n) & 0xffn),
      Number((value >> 8n) & 0xffn),
      Number(value & 0xffn),
    ];
  }
  throw new Error(`Integer too large for CBOR encoding: ${value}`);
}

/**
 * Encode a CBOR integer (major types 0 or 1)
 */
function encodeInteger(value: bigint): number[] {
  if (value >= 0n) {
    return encodeAdditionalInfo(0, value);
  }
  return encodeAdditionalInfo(1, -1n - value);
}

/**
 * Encode a CBOR byte string (major type 2)
 */
function encodeBytes(data: Uint8Array): number[] {
  return [
    ...encodeAdditionalInfo(2, BigInt(data.length)),
    ...Array.from(data),
  ];
}

/**
 * Encode a CBOR text string (major type 3)
 */
function encodeText(text: string): number[] {
  const utf8 = new TextEncoder().encode(text);
  return [
    ...encodeAdditionalInfo(3, BigInt(utf8.length)),
    ...Array.from(utf8),
  ];
}

/**
 * Encode a CBOR array (major type 4)
 */
function encodeArray(items: unknown[]): number[] {
  const result = [...encodeAdditionalInfo(4, BigInt(items.length))];
  for (const item of items) {
    result.push(...encodeValue(item));
  }
  return result;
}

/**
 * Compare two byte arrays lexicographically
 */
function compareBytes(a: Uint8Array, b: Uint8Array): number {
  const minLen = Math.min(a.length, b.length);
  for (let i = 0; i < minLen; i++) {
    const diff = a[i] - b[i];
    if (diff !== 0) return diff;
  }
  return a.length - b.length;
}

/**
 * Encode a CBOR map (major type 5) with canonical key ordering
 */
function encodeMap(obj: Record<string | number, unknown>): number[] {
  // Encode each key-value pair and collect as bytes
  const pairs: Array<{ key: Uint8Array; value: Uint8Array }> = [];
  
  for (const [key, value] of Object.entries(obj)) {
    const keyBytes = new Uint8Array(encodeValue(key));
    const valueBytes = new Uint8Array(encodeValue(value));
    pairs.push({ key: keyBytes, value: valueBytes });
  }
  
  // Sort pairs by encoded key bytes (canonical ordering)
  pairs.sort((a, b) => compareBytes(a.key, b.key));
  
  // Build result
  const result = [...encodeAdditionalInfo(5, BigInt(pairs.length))];
  for (const pair of pairs) {
    result.push(...Array.from(pair.key));
    result.push(...Array.from(pair.value));
  }
  
  return result;
}

/**
 * Encode any value to CBOR
 */
function encodeValue(value: unknown): number[] {
  // null, undefined
  if (value === null || value === undefined) {
    return [0xf6]; // CBOR null
  }
  
  // boolean
  if (value === false) {
    return [0xf4];
  }
  if (value === true) {
    return [0xf5];
  }
  
  // number
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) {
      throw new Error(`Cannot encode non-finite number: ${value}`);
    }
    if (!Number.isInteger(value)) {
      throw new Error(`Cannot encode non-integer number: ${value}`);
    }
    return encodeInteger(BigInt(value));
  }
  
  // bigint
  if (typeof value === 'bigint') {
    return encodeInteger(value);
  }
  
  // string
  if (typeof value === 'string') {
    return encodeText(value);
  }
  
  // Uint8Array, ArrayBuffer, Buffer
  if (value instanceof Uint8Array) {
    return encodeBytes(value);
  }
  if (value instanceof ArrayBuffer) {
    return encodeBytes(new Uint8Array(value));
  }
  if (typeof Buffer !== 'undefined' && value instanceof Buffer) {
    return encodeBytes(new Uint8Array(value));
  }
  
  // Array
  if (Array.isArray(value)) {
    return encodeArray(value);
  }
  
  // Object (map)
  if (typeof value === 'object' && value !== null) {
    return encodeMap(value as Record<string | number, unknown>);
  }
  
  throw new Error(`Unsupported CBOR type: ${typeof value}`);
}

/**
 * Encode a value to canonical CBOR bytes
 * 
 * @param value - The value to encode
 * @returns Canonical CBOR encoding as Uint8Array
 */
export function encodeCanonical(value: unknown): Uint8Array {
  return new Uint8Array(encodeValue(value));
}

/**
 * Encode a transaction body to canonical CBOR
 * 
 * This ensures all required fields are present and properly typed.
 */
export function encodeTxBody(body: {
  version: number;
  chain_id: number;
  nonce: number;
  from_addr: Uint8Array;
  to_addr: Uint8Array;
  value: bigint | number;
  fee: bigint | number;
  gas_limit: bigint | number;
  data: Uint8Array;
  memo: string;
  timestamp: number;
  kind: number;
}): Uint8Array {
  const obj = {
    version: body.version,
    chain_id: body.chain_id,
    nonce: body.nonce,
    from_addr: body.from_addr,
    to_addr: body.to_addr,
    value: body.value,
    fee: body.fee,
    gas_limit: body.gas_limit,
    data: body.data,
    memo: body.memo,
    timestamp: body.timestamp,
    kind: body.kind,
  };
  
  return encodeCanonical(obj);
}

/**
 * Encode a transaction auth to canonical CBOR
 */
export function encodeTxAuth(auth: {
  scheme_id: number;
  pubkey_bytes: Uint8Array;
  signature_bytes: Uint8Array;
  prehash_id: number;
}): Uint8Array {
  const obj = {
    scheme_id: auth.scheme_id,
    pubkey_bytes: auth.pubkey_bytes,
    signature_bytes: auth.signature_bytes,
    prehash_id: auth.prehash_id,
  };
  
  return encodeCanonical(obj);
}

/**
 * Encode a complete transaction envelope to canonical CBOR
 * 
 * The envelope contains body and auth, but NOT txid (which is derived).
 */
export function encodeTxEnvelope(envelope: {
  body: {
    version: number;
    chain_id: number;
    nonce: number;
    from_addr: Uint8Array;
    to_addr: Uint8Array;
    value: bigint | number;
    fee: bigint | number;
    gas_limit: bigint | number;
    data: Uint8Array;
    memo: string;
    timestamp: number;
    kind: number;
  };
  auth: {
    scheme_id: number;
    pubkey_bytes: Uint8Array;
    signature_bytes: Uint8Array;
    prehash_id: number;
  };
}): Uint8Array {
  const obj = {
    body: {
      version: envelope.body.version,
      chain_id: envelope.body.chain_id,
      nonce: envelope.body.nonce,
      from_addr: envelope.body.from_addr,
      to_addr: envelope.body.to_addr,
      value: envelope.body.value,
      fee: envelope.body.fee,
      gas_limit: envelope.body.gas_limit,
      data: envelope.body.data,
      memo: envelope.body.memo,
      timestamp: envelope.body.timestamp,
      kind: envelope.body.kind,
    },
    auth: {
      scheme_id: envelope.auth.scheme_id,
      pubkey_bytes: envelope.auth.pubkey_bytes,
      signature_bytes: envelope.auth.signature_bytes,
      prehash_id: envelope.auth.prehash_id,
    },
  };
  
  return encodeCanonical(obj);
}
