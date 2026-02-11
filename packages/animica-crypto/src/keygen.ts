/**
 * Keypair structure
 */
export interface KeyPair {
  algId: number;
  algName: string;
  publicKey: Uint8Array;
  secretKey: Uint8Array;
  address: string;
}

/**
 * Generate a PQ keypair
 * 
 * NOTE: This function currently uses a PLACEHOLDER.
 * Actual key generation requires either:
 * 1. Porting Dilithium3/SPHINCS+ keygen from Python to TypeScript
 * 2. Compiling Python implementation to WASM
 * 3. Using Pyodide to run Python PQ code in browser
 * 
 * For wallet operations, use the wallet extension which has the full PQ backend.
 * The Dapp IDE should NEVER generate keys - it should only request signatures from the wallet.
 */
export function generateKeypair(algorithm: number | string): KeyPair {
  throw new Error(
    'Key generation not implemented in @animica/crypto. ' +
    'The Dapp IDE should NEVER generate keys directly. ' +
    'Keys should only be generated in:\n' +
    '1. The browser wallet extension (which has the full PQ backend)\n' +
    '2. The Animica CLI (Python implementation)\n' +
    '3. The Animica node (Python implementation)\n' +
    '\n' +
    'The Dapp IDE should only request signatures via window.animica provider.'
  );
}
