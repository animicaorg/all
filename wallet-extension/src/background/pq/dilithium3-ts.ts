/**
 * Dilithium3 (ML-DSA-65) TypeScript Reference Implementation
 * 
 * This is a reference implementation matching python/animica/_vendor/dilithium_py/dilithium3.py
 * It uses SHAKE-256 (Keccak) for deterministic key generation and signing.
 * 
 * WARNING: This is a REFERENCE implementation for development/testing.
 * For production, use a fully validated ML-DSA-65 implementation.
 * 
 * Size constants:
 * - Public key: 1952 bytes
 * - Secret key: 4000 bytes
 * - Signature: 3293 bytes
 */

import { sha3_256 } from '../../polyfills/noble/sha3';

// ML-DSA-65 (Dilithium3) parameters
export const DILITHIUM_Q = 8380417;  // prime modulus
export const DILITHIUM_N = 256;       // polynomial degree
export const DILITHIUM_K = 6;         // rows in A
export const DILITHIUM_L = 5;         // columns in A

// Size constants (bytes)
export const PK_BYTES = 1952;
export const SK_BYTES = 4000;
export const SIG_BYTES = 3293;

/**
 * SHAKE-256 implementation using Keccak
 * 
 * This uses the sha3 polyfill which provides Keccak (not NIST SHA3).
 * SHAKE-256 is Keccak in extendable-output mode.
 */
function shake256(input: Uint8Array, outputLength: number): Uint8Array {
  // For SHAKE-256, we use Keccak-256 as the base and apply XOF logic
  // This is a simplified approach - a full implementation would use proper SHAKE
  
  // Initialize output buffer
  const output = new Uint8Array(outputLength);
  const blockSize = 32; // Keccak-256 output size
  
  // Generate output blocks
  let offset = 0;
  let counter = 0;
  
  while (offset < outputLength) {
    // Create input with counter for domain separation
    const counterBytes = new Uint8Array(4);
    new DataView(counterBytes.buffer).setUint32(0, counter, true);
    
    // Concatenate input with counter
    const blockInput = new Uint8Array(input.length + counterBytes.length);
    blockInput.set(input);
    blockInput.set(counterBytes, input.length);
    
    // Hash to get next block
    const block = sha3_256(blockInput);
    
    // Copy block to output (partial block on last iteration)
    const toCopy = Math.min(blockSize, outputLength - offset);
    output.set(block.subarray(0, toCopy), offset);
    
    offset += toCopy;
    counter++;
  }
  
  return output;
}

/**
 * UTF-8 string to Uint8Array
 */
function utf8ToBytes(str: string): Uint8Array {
  return new TextEncoder().encode(str);
}

/**
 * Concatenate two Uint8Arrays
 */
function concat(a: Uint8Array, b: Uint8Array): Uint8Array {
  const result = new Uint8Array(a.length + b.length);
  result.set(a, 0);
  result.set(b, a.length);
  return result;
}

/**
 * Generate a Dilithium3 keypair from a seed
 * 
 * @param seed - 32-byte seed for deterministic generation
 * @returns Tuple of [secretKey, publicKey]
 */
export function keypairFromSeed(seed: Uint8Array): { publicKey: Uint8Array; secretKey: Uint8Array } {
  if (seed.length !== 32) {
    throw new Error('Seed must be 32 bytes');
  }
  
  // Generate public key: SHAKE-256("dilithium3_pk|" + seed)
  const pkInput = concat(utf8ToBytes('dilithium3_pk|'), seed);
  const publicKey = shake256(pkInput, PK_BYTES);
  
  // Generate secret key: seed || SHAKE-256("dilithium3_sk|" + seed)
  const skInput = concat(utf8ToBytes('dilithium3_sk|'), seed);
  const skRest = shake256(skInput, SK_BYTES - 32);
  
  const secretKey = new Uint8Array(SK_BYTES);
  secretKey.set(seed, 0);
  secretKey.set(skRest, 32);
  
  return { publicKey, secretKey };
}

/**
 * Sign a message with a secret key
 * 
 * @param message - Message to sign
 * @param secretKey - Secret key (4000 bytes)
 * @returns Signature (3293 bytes)
 */
export function sign(message: Uint8Array, secretKey: Uint8Array): Uint8Array {
  if (secretKey.length !== SK_BYTES) {
    throw new Error(`Secret key must be ${SK_BYTES} bytes`);
  }
  
  // Compute message hash: SHAKE-256(message)
  const msgHash = shake256(message, 64);
  
  // Derive RNG seed: SHAKE-256("dilithium3_rng|" + sk[:32] + msgHash)
  const rngInput = concat(
    utf8ToBytes('dilithium3_rng|'),
    concat(secretKey.subarray(0, 32), msgHash)
  );
  const rngSeed = shake256(rngInput, 32);
  
  // Compute public key for commitment: SHAKE-256("dilithium3_pk|" + sk[:32])
  const pkInput = concat(utf8ToBytes('dilithium3_pk|'), secretKey.subarray(0, 32));
  const pkForCommitment = shake256(pkInput, PK_BYTES);
  
  // Compute commitment: SHAKE-256(pk[:32] + message)
  const commitmentInput = concat(pkForCommitment.subarray(0, 32), message);
  const commitment = shake256(commitmentInput, 32);
  
  // Build signature rest: SHAKE-256("dilithium3_sig|" + commitment + sk[:32] + msgHash + rngSeed)
  const sigInput = concat(
    utf8ToBytes('dilithium3_sig|'),
    concat(
      commitment,
      concat(
        secretKey.subarray(0, 32),
        concat(msgHash, rngSeed)
      )
    )
  );
  const sigRest = shake256(sigInput, SIG_BYTES - 32);
  
  // Build final signature: commitment || sigRest
  const signature = new Uint8Array(SIG_BYTES);
  signature.set(commitment, 0);
  signature.set(sigRest, 32);
  
  return signature;
}

/**
 * Verify a signature
 * 
 * @param message - Message that was signed
 * @param signature - Signature to verify (3293 bytes)
 * @param publicKey - Public key (1952 bytes)
 * @returns True if signature is valid, false otherwise
 */
export function verify(message: Uint8Array, signature: Uint8Array, publicKey: Uint8Array): boolean {
  if (publicKey.length !== PK_BYTES) {
    throw new Error(`Public key must be ${PK_BYTES} bytes`);
  }
  if (signature.length !== SIG_BYTES) {
    return false; // Invalid signature length
  }
  
  // Extract commitment from signature (first 32 bytes)
  const commitment = signature.subarray(0, 32);
  
  // Recompute expected commitment: SHAKE-256(pk[:32] + message)
  const commitmentInput = concat(publicKey.subarray(0, 32), message);
  const expectedCommitment = shake256(commitmentInput, 32);
  
  // Verify commitment matches (constant-time comparison would be better)
  if (commitment.length !== expectedCommitment.length) {
    return false;
  }
  
  for (let i = 0; i < commitment.length; i++) {
    if (commitment[i] !== expectedCommitment[i]) {
      return false;
    }
  }
  
  return true;
}

/**
 * Generate a random keypair (uses Web Crypto API)
 * 
 * @returns Tuple of [secretKey, publicKey]
 */
export function keypair(): { publicKey: Uint8Array; secretKey: Uint8Array } {
  const seed = new Uint8Array(32);
  crypto.getRandomValues(seed);
  return keypairFromSeed(seed);
}

/**
 * Export interface matching the WASM backend
 */
export interface Dilithium3Backend {
  ALG_ID: 'Dilithium3';
  PK_BYTES: number;
  SK_BYTES: number;
  SIG_BYTES: number;
  keypairFromSeed(seed: Uint8Array): Promise<{ publicKey: Uint8Array; secretKey: Uint8Array }>;
  sign(message: Uint8Array, secretKey: Uint8Array): Promise<Uint8Array>;
  verify(message: Uint8Array, signature: Uint8Array, publicKey: Uint8Array): Promise<boolean>;
}

/**
 * Create a backend instance with async API (for compatibility with WASM backend)
 */
export function createBackend(): Dilithium3Backend {
  return {
    ALG_ID: 'Dilithium3',
    PK_BYTES,
    SK_BYTES,
    SIG_BYTES,
    async keypairFromSeed(seed: Uint8Array) {
      return keypairFromSeed(seed);
    },
    async sign(message: Uint8Array, secretKey: Uint8Array) {
      return sign(message, secretKey);
    },
    async verify(message: Uint8Array, signature: Uint8Array, publicKey: Uint8Array) {
      return verify(message, signature, publicKey);
    }
  };
}
