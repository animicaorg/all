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
 * Encode a CBOR map (major type 5) with canonical key ordering.
 *
 * Object.entries surfaces all keys as strings. The server-side canonical
 * encoder (Python/cbor2) preserves integer keys for the signing preimage
 * (keys 1..7 in `{1: domain, ..., 7: body}`), so numeric-string keys here
 * must be re-encoded as CBOR integers to match. Otherwise the wallet's
 * preimage diverges from the chain's preimage and PQ verification fails.
 */
function encodeMap(obj: Record<string | number, unknown>): number[] {
  const pairs: Array<{ key: Uint8Array; value: Uint8Array }> = [];

  for (const [key, value] of Object.entries(obj)) {
    const keyEncoded = /^(0|[1-9]\d*)$/.test(key) && Number(key) <= Number.MAX_SAFE_INTEGER
      ? encodeValue(Number(key))
      : encodeValue(key);
    const keyBytes = new Uint8Array(keyEncoded);
    const valueBytes = new Uint8Array(encodeValue(value));
    pairs.push({ key: keyBytes, value: valueBytes });
  }

  pairs.sort((a, b) => compareBytes(a.key, b.key));

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
 * Build the canonical V1 tx body shape that the Animica node consumes:
 *
 *   transfer (t=0): payload.v = { to, amount, data }
 *   deploy   (t=1): payload.v = { code, manifest }
 *   call     (t=2): payload.v = { to, data }
 *
 * Layout matches sdk/python/omni_sdk/tx/encode.py exactly so the wallet's
 * preimage bytes match what the node will reproduce. The node's
 * normalize_tx_body short-circuits when v/gas/payload are present and uses
 * this body verbatim for both signing preimage and txid.
 */
export function toCanonicalBodyShape(body: {
  chain_id: number;
  nonce: number;
  from_addr: Uint8Array;
  to_addr: Uint8Array;
  value: bigint | number;
  fee: bigint | number;
  gas_limit: bigint | number;
  data: Uint8Array;
  kind?: number;
  code?: Uint8Array;
  manifest?: Uint8Array;
}): Record<string, unknown> {
  const kind = typeof body.kind === 'number' ? body.kind : 0;

  let payloadV: Record<string, unknown>;
  if (kind === 1) {
    payloadV = {
      code: body.code ?? new Uint8Array(),
      manifest: body.manifest ?? new Uint8Array(),
    };
  } else if (kind === 2) {
    payloadV = {
      to: body.to_addr,
      data: body.data,
    };
  } else {
    payloadV = {
      to: body.to_addr,
      amount: body.value,
      data: body.data,
    };
  }

  return {
    v: 1,
    chainId: body.chain_id,
    from: body.from_addr,
    gas: {
      price: body.fee,
      limit: body.gas_limit,
    },
    payload: {
      t: kind,
      v: payloadV,
    },
    accessList: [],
    nonce: body.nonce,
  };
}

/**
 * Encode a complete transaction envelope to canonical CBOR.
 *
 * Wire shape mirrors the node's canonical envelope:
 *   { tx: <canonical body>, sigs: [{ alg, pubkey, sig }] }
 *
 * The previous {body, auth} shape was rejected by the node with
 * "-32602 Missing 'sig' object" because the chain's envelope normalizer
 * looks for `sig` / `sigs` and the body field for `from` (not `from_addr`).
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
    code?: Uint8Array;
    manifest?: Uint8Array;
  };
  auth: {
    scheme_id: number;
    pubkey_bytes: Uint8Array;
    signature_bytes: Uint8Array;
    prehash_id: number;
  };
}): Uint8Array {
  const obj = {
    tx: toCanonicalBodyShape(envelope.body),
    sigs: [
      {
        alg: envelope.auth.scheme_id,
        pubkey: envelope.auth.pubkey_bytes,
        sig: envelope.auth.signature_bytes,
      },
    ],
  };

  return encodeCanonical(obj);
}
