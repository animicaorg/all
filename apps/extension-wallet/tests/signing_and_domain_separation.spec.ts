import { describe, it } from 'vitest'

// Vertical 8: Signing flow, domain separation, and anti-replay semantics
// Placeholder coverage to be filled once signing APIs are runnable in the extension.

describe('signing and domain separation', () => {
  it.todo('prefixes sign bytes with chainId and domain per Animica SignDoc spec')
  it.todo('rejects signature replay attempts across different chainIds')
  it.todo('rejects signature replay across different dapp domains/origins')
  it.todo('supports offline signing flows while preserving domain separation metadata')
  it.todo('surfaces signature payload previews that match the serialized bytes')
})
