// Re-export all public APIs
export * from './algorithms.js';
export * from './address.js';
export * from './signing.js';
export * from './keygen.js';
export * from './transaction.js';
export * from './utils.js';

// Version
export const VERSION = '0.1.0';

/**
 * @animica/crypto
 * 
 * Custom post-quantum cryptography library for Animica blockchain.
 * 
 * IMPORTANT: This package does NOT implement the full PQ signing/verification
 * backends (Dilithium3/SPHINCS+). Those are complex and require either:
 * 1. Porting from python/animica/_vendor/ to TypeScript
 * 2. Compiling to WASM
 * 3. Using Pyodide
 * 
 * This package provides:
 * - ✅ Address derivation (bech32m + SHA3-256)
 * - ✅ SignBytes construction (domain separation, length prefixing)
 * - ✅ Algorithm metadata and validation
 * - ✅ Utility functions (encoding, hashing, etc.)
 * 
 * For actual signing:
 * - Dapp IDE: Request signatures from wallet via window.animica provider
 * - Wallet Extension: Implements full PQ backend
 * - CLI/Node: Uses Python implementation
 * 
 * For verification/diagnostics:
 * - Use the node's RPC to verify signatures
 * - Or implement full PQ backend in wallet extension
 * 
 * NO LIBOQS POLICY:
 * This package NEVER uses liboqs or any third-party PQ library.
 * All implementations must be from Animica's repository code.
 */
