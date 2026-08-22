import { sha3_512 } from '@noble/hashes/sha3';
import { AlgorithmId, getAlgorithmInfo } from './algorithms.js';
import { encodeUvarint, lengthPrefix, normalizeDomain, concatBytes } from './utils.js';

/**
 * Prehash type
 */
export type PrehashKind = 'sha3-512' | 'sha3-256';

/**
 * Signature envelope
 */
export interface Signature {
  algId: number;
  algName: string;
  domain: string;
  prehash: PrehashKind;
  sig: Uint8Array;
}

/**
 * Sign options
 */
export interface SignOptions {
  /** Domain for signature separation (e.g., "tx", "animica.tx.v1") */
  domain: string | Uint8Array;
  /** Chain ID (optional but recommended for transactions) */
  chainId?: number;
  /** Fork ID (optional) */
  forkId?: number;
  /** Additional context bytes (optional) */
  context?: Uint8Array;
  /** Prehash algorithm (default: "sha3-512") */
  prehash?: PrehashKind;
}

/**
 * Verify options
 */
export interface VerifyOptions extends SignOptions {
  /** Enforce domain match (default: true) */
  strictDomain?: boolean;
  /** Enforce prehash match (default: true) */
  strictPrehash?: boolean;
  /** Enforce algorithm match (default: true) */
  strictAlg?: boolean;
}

/**
 * Build canonical SignBytes for Animica PQ signatures
 * 
 * Layout before prehash:
 *   TAG        = "animica:sign/v1"
 *   DOMAIN     = domain (bytes)
 *   CHAIN_ID?  = if provided (uvarint)
 *   FORK_ID?   = if provided (uvarint)
 *   ALG_ID     = uvarint(alg_id)
 *   CONTEXT    = freeform domain-specific bytes
 *   MESSAGE    = the original message bytes
 * 
 * All fields are length-prefixed with uvarints.
 * Final SignBytes = SHA3-512(raw_bytes)
 */
export function buildSignBytes(
  message: Uint8Array,
  algId: number,
  options: SignOptions
): Uint8Array {
  const {
    domain,
    chainId,
    forkId,
    context = new Uint8Array(0),
    prehash = 'sha3-512'
  } = options;
  
  // Build components
  const tag = new TextEncoder().encode('animica:sign/v1');
  const domainBytes = normalizeDomain(domain);
  
  const parts: Uint8Array[] = [];
  
  // TAG
  parts.push(lengthPrefix(tag));
  
  // DOMAIN
  parts.push(lengthPrefix(domainBytes));
  
  // CHAIN_ID (optional)
  if (chainId !== undefined && chainId !== null) {
    const chainIdBytes = encodeUvarint(chainId);
    parts.push(lengthPrefix(chainIdBytes));
  } else {
    parts.push(encodeUvarint(0)); // empty length
  }
  
  // FORK_ID (optional)
  if (forkId !== undefined && forkId !== null) {
    const forkIdBytes = encodeUvarint(forkId);
    parts.push(lengthPrefix(forkIdBytes));
  } else {
    parts.push(encodeUvarint(0)); // empty length
  }
  
  // ALG_ID
  const algIdBytes = encodeUvarint(algId);
  parts.push(lengthPrefix(algIdBytes));
  
  // CONTEXT
  parts.push(lengthPrefix(context));
  
  // MESSAGE
  parts.push(lengthPrefix(message));
  
  // Concatenate all parts
  const raw = concatBytes(...parts);
  
  // Prehash with SHA3-512 (or SHA3-256)
  if (prehash === 'sha3-512') {
    return sha3_512(raw);
  } else {
    const { sha3_256 } = require('@noble/hashes/sha3');
    return sha3_256(raw);
  }
}

/**
 * Sign a message with PQ algorithm
 * 
 * NOTE: This function currently uses a PLACEHOLDER for the actual PQ signing.
 * The full implementation requires either:
 * 1. Porting Dilithium3/SPHINCS+ from Python to TypeScript
 * 2. Compiling Python implementation to WASM
 * 3. Using Pyodide to run Python code in the browser
 * 
 * For now, this throws an error directing developers to implement the backend.
 */
export function sign(
  algorithm: AlgorithmId | number,
  secretKey: Uint8Array,
  message: Uint8Array,
  options: SignOptions
): Signature {
  const algInfo = getAlgorithmInfo(algorithm);
  
  // Validate secret key length
  if (secretKey.length !== algInfo.secretKeyLength) {
    throw new Error(
      `Invalid secret key length: expected ${algInfo.secretKeyLength}, got ${secretKey.length}`
    );
  }
  
  // Build canonical SignBytes
  const signBytes = buildSignBytes(message, algInfo.id, options);
  
  // TODO: Call actual PQ signing backend
  throw new Error(
    'PQ signing backend not yet implemented. ' +
    'This requires either:\n' +
    '1. Porting Dilithium3/SPHINCS+ from python/animica/_vendor/ to TypeScript\n' +
    '2. Compiling Python implementation to WASM\n' +
    '3. Using Pyodide to run Python PQ code in browser\n' +
    '\n' +
    'See apps/dapp-ide/docs/PQ_DISCOVERY.md for implementation guide.\n' +
    'For wallet signing, use the wallet extension which has the full PQ backend.'
  );
  
  // Placeholder return (unreachable)
  /*
  const sigBytes = await pqBackend.sign(algInfo.id, secretKey, signBytes);
  
  return {
    algId: algInfo.id,
    algName: algInfo.name,
    domain: typeof options.domain === 'string' ? options.domain : new TextDecoder().decode(options.domain),
    prehash: options.prehash || 'sha3-512',
    sig: sigBytes
  };
  */
}

/**
 * Verify a PQ signature
 * 
 * NOTE: This function currently uses a PLACEHOLDER for the actual PQ verification.
 * Like signing, this requires a PQ backend implementation.
 */
export function verify(
  publicKey: Uint8Array,
  message: Uint8Array,
  signature: Signature,
  options: VerifyOptions
): boolean {
  const algInfo = getAlgorithmInfo(signature.algId);
  
  // Validate public key length
  if (publicKey.length !== algInfo.publicKeyLength) {
    throw new Error(
      `Invalid public key length: expected ${algInfo.publicKeyLength}, got ${publicKey.length}`
    );
  }
  
  // Validate signature length
  if (signature.sig.length !== algInfo.signatureLength) {
    throw new Error(
      `Invalid signature length: expected ${algInfo.signatureLength}, got ${signature.sig.length}`
    );
  }
  
  // Check domain if strict
  if (options.strictDomain !== false) {
    const expectedDomain = typeof options.domain === 'string' 
      ? options.domain 
      : new TextDecoder().decode(options.domain);
    if (signature.domain !== expectedDomain) {
      throw new Error(`Domain mismatch: expected ${expectedDomain}, got ${signature.domain}`);
    }
  }
  
  // Check prehash if strict
  const expectedPrehash = options.prehash || 'sha3-512';
  if (options.strictPrehash !== false && signature.prehash !== expectedPrehash) {
    throw new Error(`Prehash mismatch: expected ${expectedPrehash}, got ${signature.prehash}`);
  }
  
  // Rebuild SignBytes with same parameters
  const signBytes = buildSignBytes(message, signature.algId, {
    domain: options.domain,
    chainId: options.chainId,
    forkId: options.forkId,
    context: options.context,
    prehash: signature.prehash
  });
  
  // TODO: Call actual PQ verification backend
  throw new Error(
    'PQ verification backend not yet implemented. ' +
    'This requires the same PQ backend as signing. ' +
    'See apps/dapp-ide/docs/PQ_DISCOVERY.md for implementation guide.'
  );
  
  // Placeholder return (unreachable)
  /*
  return await pqBackend.verify(signature.algId, publicKey, signBytes, signature.sig);
  */
}

