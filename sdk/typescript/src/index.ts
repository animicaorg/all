/**
 * @packageDocumentation
 * Public entry for @animica/sdk — re-exports the primary building blocks.
 * Prefer deep imports (e.g. `@animica/sdk/rpc/http`) for optimal tree-shaking.
 */

export { version } from './version'
export * from './errors'

// RPC
export * from './rpc/http'
export * from './rpc/ws'

// Wallet & PQ signing
export * from './wallet/mnemonic'
export * from './wallet/keystore'
export * from './wallet/signer'

// Address
export * from './address'

// Transactions
export * from './tx/build'
export * from './tx/encode'
export * from './tx/send'

// Contracts
export * from './contracts/client'
export * from './contracts/deployer'
export * from './contracts/events'
export * from './contracts/codegen'

// Data Availability
export * from './da/client'

// AICF (AI / Quantum)
export * from './aicf/client'

// Randomness (Beacon)
export * from './randomness/client'

// Light Client verify
export * from './light_client/verify'

// Proof helpers
export * from './proofs/hashshare'
export * from './proofs/ai'
export * from './proofs/quantum'

// Shared utilities
export * from './utils/bytes'
export * from './utils/hash'
export * from './utils/cbor'
export * from './utils/bech32'
export * from './utils/retry'
