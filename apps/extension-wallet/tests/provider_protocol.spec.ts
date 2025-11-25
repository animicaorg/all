import { describe, it } from 'vitest'

// Vertical 8: Provider protocol surface validation
// These tests outline expectations for the in-page provider injected at window.animica.
// They are intentionally written as TODOs to be filled with concrete assertions once the
// provider implementation is wired up inside the browser extension.

describe('window.animica provider protocol', () => {
  it.todo('exposes request, on, removeListener, and emits events per the Animica spec')
  it.todo('delegates animica_requestAccounts, animica_chainId, and other required RPC methods')
  it.todo('normalizes params/result shapes for JSON-RPC 2.0 responses and errors')
  it.todo('guards against unknown methods and surfaces ProviderRpcError codes consistently')
  it.todo('publishes provider metadata (isAnimica, version, chains) for dapp introspection')
})
