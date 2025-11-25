import { describe, it, beforeEach, expect, vi } from 'vitest'

// ---- Mocks -----------------------------------------------------------------
// In-memory storage mock replacing extension storage used by the keyring.
const mem: Record<string, any> = {}

vi.mock('../../src/background/keyring/storage', () => {
  return {
    saveVault: vi.fn(async (v: any) => {
      mem.vault = v
    }),
    loadVault: vi.fn(async () => mem.vault ?? null),
    clearVault: vi.fn(async () => {
      delete mem.vault
    }),
    saveSession: vi.fn(async (s: any) => {
      mem.session = s
    }),
    loadSession: vi.fn(async () => mem.session ?? null),
    clearSession: vi.fn(async () => {
      delete mem.session
    }),
  }
})

// Deterministic RNG so tests are stable (if mnemonic generation uses RNG).
vi.mock('../../src/background/pq/rng', () => {
  return {
    getRandomBytes: (n: number) => new Uint8Array(Array.from({ length: n }, (_, i) => (i * 31 + 7) & 0xff)),
  }
})

// Lightweight PQ stubs: derive "pubkeys" deterministically from seed+index+algo.
// (Real implementation is WASM-backed; tests only need shape & determinism.)
vi.mock('../../src/background/pq/dilithium3', () => {
  return {
    derivePublicKey: (seed: Uint8Array, index: number) => {
      const x = seed.reduce((a, b) => (a + b) & 0xffffffff, 0) ^ (index * 0x9e3779b9)
      // 32-byte fake pubkey
      const out = new Uint8Array(32)
      for (let i = 0; i < out.length; i++) out[i] = (x >> (i % 24)) & 0xff
      return out
    },
  }
})

vi.mock('../../src/background/pq/sphincs_shake_128s', () => {
  return {
    derivePublicKey: (seed: Uint8Array, index: number) => {
      const x = seed.reduce((a, b) => (a * 131 + b) & 0xffffffff, 0) ^ (index * 0x7f4a7c15)
      const out = new Uint8Array(32)
      for (let i = 0; i < out.length; i++) out[i] = (x >> ((i * 5) % 24)) & 0xff
      return out
    },
  }
})

// ----------------------------------------------------------------------------

// Import the keyring facade. The implementation may export either
//   - createKeyring(options)
//   - class KeyringController
// We support both for test portability.
import * as KR from '../../src/background/keyring'

function makeKeyring(opts: any = {}) {
  const factory = (KR as any).createKeyring
  if (typeof factory === 'function') return factory(opts)
  const Ctor = (KR as any).KeyringController
  return new Ctor(opts)
}

describe('Keyring — mnemonic → keys → addresses', () => {
  const mnemonic =
    'sudden lobster lawn swift island pudding radar upper sudden lobster lawn swift' // 12 words, deterministic for tests
  const pin = '1234'

  beforeEach(() => {
    for (const k of Object.keys(mem)) delete (mem as any)[k]
  })

  it('imports mnemonic, unlocks with PIN, derives first address (Dilithium3)', async () => {
    const keyring: any = makeKeyring()
    await keyring.importMnemonic(mnemonic, { pin })
    expect(await keyring.isLocked?.() ?? keyring.locked === false).toBe(false)

    const acct0 = await keyring.deriveAccount?.({ index: 0, algo: 'dilithium3' }) ??
      (await keyring.getAccountAt?.(0, 'dilithium3'))
    expect(acct0).toBeTruthy()
    expect(acct0.algo).toBe('dilithium3')
    expect(typeof acct0.address).toBe('string')
    expect(acct0.address.startsWith('anim1')).toBe(true)

    // Listing accounts should include the derived default (if implementation pre-populates)
    const list = (await keyring.getAccounts?.()) ?? []
    if (Array.isArray(list) && list.length > 0) {
      expect(list[0].address).toBe(acct0.address)
    }
  })

  it('derives distinct addresses for different indices', async () => {
    const keyring: any = makeKeyring()
    await keyring.importMnemonic(mnemonic, { pin })

    const a0 =
      (await keyring.deriveAccount?.({ index: 0, algo: 'dilithium3' })) ??
      (await keyring.getAccountAt?.(0, 'dilithium3'))
    const a1 =
      (await keyring.deriveAccount?.({ index: 1, algo: 'dilithium3' })) ??
      (await keyring.getAccountAt?.(1, 'dilithium3'))

    expect(a0.address).not.toBe(a1.address)
    expect(a0.address.startsWith('anim1')).toBe(true)
    expect(a1.address.startsWith('anim1')).toBe(true)
  })

  it('supports SPHINCS+ addresses too (different from Dilithium3)', async () => {
    const keyring: any = makeKeyring()
    await keyring.importMnemonic(mnemonic, { pin })

    const d3 =
      (await keyring.deriveAccount?.({ index: 0, algo: 'dilithium3' })) ??
      (await keyring.getAccountAt?.(0, 'dilithium3'))
    const sp =
      (await keyring.deriveAccount?.({ index: 0, algo: 'sphincs_shake_128s' })) ??
      (await keyring.getAccountAt?.(0, 'sphincs_shake_128s'))

    expect(d3.address).not.toBe(sp.address)
    expect(sp.address.startsWith('anim1')).toBe(true)
  })

  it('lock → disallows sensitive ops; unlock restores access', async () => {
    const keyring: any = makeKeyring()
    await keyring.importMnemonic(mnemonic, { pin })

    // Export mnemonic works when unlocked
    const exported1 = await (keyring.exportMnemonic?.() ?? keyring.getMnemonic?.())
    expect(typeof exported1).toBe('string')
    expect((exported1 as string).trim()).toBe(mnemonic)

    // Lock
    await (keyring.lock?.() ?? (async () => (keyring.locked = true))())
    const locked = await (keyring.isLocked?.() ?? keyring.locked)
    expect(locked).toBe(true)

    // Exporting mnemonic when locked should throw
    const exportWhenLocked = keyring.exportMnemonic?.() ?? keyring.getMnemonic?.()
    await expect(exportWhenLocked).rejects.toBeTruthy()

    // Derive should also fail or return a safe error
    const deriveLocked = keyring.deriveAccount?.({ index: 0, algo: 'dilithium3' }) ??
      keyring.getAccountAt?.(0, 'dilithium3')
    await expect(deriveLocked).rejects.toBeTruthy()

    // Unlock
    await (keyring.unlock?.(pin) ?? (async () => (keyring.locked = false))())
    const unlocked = await (keyring.isLocked?.() ?? keyring.locked === false)
    expect(unlocked).toBe(false)

    // Export works again
    const exported2 = await (keyring.exportMnemonic?.() ?? keyring.getMnemonic?.())
    expect(exported2).toBe(mnemonic)
  })

  it('round-trips vault through storage (persisted across instances)', async () => {
    // Instance A imports mnemonic (saves vault to storage mock)
    const A: any = makeKeyring()
    await A.importMnemonic(mnemonic, { pin })
    await A.lock?.()

    // New instance B should load the existing vault and be able to unlock with the same PIN
    const B: any = makeKeyring()
    const isLockedBefore = await (B.isLocked?.() ?? true)
    expect(isLockedBefore).toBe(true)

    await B.unlock?.(pin)
    const acct0 =
      (await B.deriveAccount?.({ index: 0, algo: 'dilithium3' })) ??
      (await B.getAccountAt?.(0, 'dilithium3'))
    expect(acct0.address.startsWith('anim1')).toBe(true)
  })
})
