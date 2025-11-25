import { describe, test, expect } from 'vitest'

/**
 * This test is intentionally defensive about the signer API shape.
 * Different environments (WASM present vs. stub) or minor refactors
 * should still satisfy: keygen → sign → verify passes; tamper fails.
 */

type AnySigner = {
  alg?: string
  algorithm?: string
  sign?: (msg: Uint8Array) => Promise<Uint8Array> | Uint8Array
  verify?: (msg: Uint8Array, sig: Uint8Array) => Promise<boolean> | boolean
  publicKey?: Uint8Array
  getPublicKey?: () => Uint8Array
  exportKeypair?: () => { publicKey: Uint8Array; secretKey?: Uint8Array }
}

type SignerFactory = (alg: string, seed: Uint8Array) => Promise<AnySigner>

/** Normalize to Uint8Array */
function u8(x: ArrayLike<number> | Buffer): Uint8Array {
  return x instanceof Uint8Array ? x : new Uint8Array(x as ArrayLike<number>)
}

/** Read public key off a signer instance in a tolerant way. */
function readPub(signer: AnySigner): Uint8Array {
  if (signer.publicKey) return signer.publicKey
  if (typeof signer.getPublicKey === 'function') return signer.getPublicKey()
  if (typeof signer.exportKeypair === 'function') return signer.exportKeypair().publicKey
  throw new Error('Unable to read publicKey from signer')
}

/** Call sign(msg) on a signer in a tolerant way. */
async function doSign(signer: AnySigner, msg: Uint8Array): Promise<Uint8Array> {
  if (!signer.sign) throw new Error('sign method missing')
  const out = await signer.sign(msg)
  return u8(out)
}

/** Verify, either via instance.verify or module-level verify(). */
async function doVerify(
  signer: AnySigner,
  msg: Uint8Array,
  sig: Uint8Array,
  mod: any,
  alg: string
): Promise<boolean> {
  if (typeof signer.verify === 'function') {
    return Boolean(await signer.verify(msg, sig))
  }
  // Try module-level verifier shapes
  const pub = readPub(signer)
  if (typeof mod.verify === 'function') {
    try {
      const res = await mod.verify({ alg, publicKey: pub, message: msg, signature: sig })
      return Boolean(res === true || res?.ok === true || res === 1)
    } catch {
      return false
    }
  }
  if (mod && mod.default && typeof mod.default.verify === 'function') {
    try {
      const r = await mod.default.verify({ alg, publicKey: pub, message: msg, signature: sig })
      return Boolean(r === true || r?.ok === true)
    } catch {
      return false
    }
  }
  throw new Error('No verify path available')
}

/** Build a tolerant signer factory from the module exports. */
async function buildFactory(mod: any): Promise<SignerFactory> {
  if (typeof mod.createSigner === 'function') {
    return async (alg, seed) => {
      return await mod.createSigner({ alg, seed })
    }
  }
  if (mod?.default && typeof mod.default.createSigner === 'function') {
    return async (alg, seed) => {
      return await mod.default.createSigner({ alg, seed })
    }
  }
  // Class-based exports?
  for (const key of ['Signer', 'PQSigner', 'DefaultSigner', 'JsonRpcSigner']) {
    const Cls = mod[key] || mod?.default?.[key]
    if (typeof Cls === 'function') {
      // Prefer fromSeed({alg, seed})
      if (typeof Cls.fromSeed === 'function') {
        return async (alg, seed) => await Cls.fromSeed({ alg, seed })
      }
      // Or new Cls({alg, seed})
      return async (alg, seed) => new Cls({ alg, seed })
    }
  }
  // Algorithm-specific named classes (Dilithium3Signer / SphincsSigner)
  const named = [
    ['dilithium3', mod.Dilithium3Signer || mod?.default?.Dilithium3Signer],
    ['sphincs-shake-128s', mod.SPHINCSSigner || mod?.default?.SPHINCSSigner || mod.SphincsSigner]
  ] as const
  for (const [name, Cls] of named) {
    if (typeof Cls === 'function') {
      return async (alg, seed) => {
        if (alg !== name) throw new Error(`factory bound to ${name}`)
        if (typeof Cls.fromSeed === 'function') return await Cls.fromSeed({ seed })
        return new Cls({ seed })
      }
    }
  }

  throw new Error('Could not construct a signer factory from module exports')
}

/** Choose the set of algorithms to try based on module hints. */
function chooseAlgs(mod: any): string[] {
  const hinted: string[] =
    mod?.SUPPORTED_ALGS ||
    mod?.ALGORITHMS ||
    mod?.supportedAlgorithms ||
    mod?.default?.SUPPORTED_ALGS ||
    []
  const candidates = ['dilithium3', 'sphincs-shake-128s']
  if (Array.isArray(hinted) && hinted.length) {
    return candidates.filter(a => hinted.includes(a))
  }
  return candidates
}

describe('PQ wallet signer — sign/verify roundtrip', async () => {
  const mod = await import('../src/wallet/signer')
  const factory = await buildFactory(mod)
  const algs = chooseAlgs(mod)

  test.each(algs.map(a => [a]))('sign/verify works for %s', async (alg: string) => {
    // Deterministic seed for stable keys in tests
    const seed = new Uint8Array(32)
    seed.fill(7) // arbitrary non-zero pattern
    const signer = await factory(alg, seed)

    const msg = new TextEncoder().encode('animica pq test: ' + alg)
    const sig = await doSign(signer, msg)
    expect(sig.byteLength).toBeGreaterThan(16) // at least non-trivial length

    const ok = await doVerify(signer, msg, sig, mod, alg)
    expect(ok).toBe(true)

    // Tamper message — should fail verification
    const tampered = msg.slice()
    tampered[0] ^= 0xff
    let bad = false
    try {
      bad = await doVerify(signer, tampered, sig, mod, alg)
    } catch {
      bad = false
    }
    expect(bad).toBe(false)
  })
})
