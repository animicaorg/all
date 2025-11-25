/**
 * Contracts module public exports.
 *
 * This barrel file re-exports the Contract client utilities, deploy helpers,
 * event decoding tools, and ABI codegen helpers for downstream apps.
 */

export * from './client'
export * from './deployer'
export * from './events'
export * from './codegen'

// Aggregate default export for convenience: `import Contracts from '@animica/sdk/contracts'`
import * as Client from './client'
import * as Deployer from './deployer'
import * as Events from './events'
import * as Codegen from './codegen'

export default { Client, Deployer, Events, Codegen }
