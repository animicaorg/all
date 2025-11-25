/**
 * @package @animica/sdk/wallet
 * Barrel exports for wallet utilities:
 *  - Mnemonic helpers (create/import, validation)
 *  - Encrypted keystore (WebCrypto AES-GCM)
 *  - PQ signers (Dilithium3/SPHINCS+ via WASM/native backends)
 *
 * Usage:
 *   import {
 *     generateMnemonic, mnemonicToSeed,
 *     createKeystore, openKeystore,
 *     createDilithiumSigner, createSphincsSigner
 *   } from '@animica/sdk/wallet'
 */

export * from './mnemonic'
export * from './keystore'
export * from './signer'

// Re-export common types for convenience
export type { AlgorithmId, SignRequest, SignResult, Signer } from './signer'
