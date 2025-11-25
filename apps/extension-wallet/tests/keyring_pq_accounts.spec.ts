import { describe, it } from 'vitest'

// Vertical 8: PQ keyring behavior and address handling
// TODO: Implement mocks for the keyring module once Dilithium/SPHINCS+ flows are wired up.

describe('post-quantum keyring accounts', () => {
  it.todo('derives Dilithium keys from mnemonics and exports deterministic public keys')
  it.todo('derives SPHINCS+ keys from mnemonics and exports deterministic public keys')
  it.todo('encodes Bech32m addresses correctly for both PQ schemes')
  it.todo('imports PQ accounts via JSON/QR and restores chain-aware metadata')
  it.todo('exports accounts with encrypted seed data and checksum integrity markers')
})
