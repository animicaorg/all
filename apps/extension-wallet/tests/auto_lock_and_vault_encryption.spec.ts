import { describe, it } from 'vitest'

// Vertical 8: Vault encryption, auto-lock timers, and content-script isolation
// Pending full integration with the extension background/store implementations.

describe('auto-lock and vault encryption', () => {
  it.todo('encrypts the vault with strong defaults and zeroizes secrets on lock')
  it.todo('auto-locks after user-configurable inactivity windows and logs the event')
  it.todo('unlocks only after correct password/passphrase and rehydrates key material securely')
  it.todo('ensures no decrypted secrets are accessible to content scripts or dapps')
  it.todo('persists lock state across reloads and resumes timers after browser restarts')
})
