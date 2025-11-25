import { createHash } from 'crypto'

type HashFactory = ((msg: Uint8Array | string) => Uint8Array) & {
  create: () => { update: (data: Uint8Array | string) => any; digest: () => Uint8Array }
  algorithm: string
}

function makeHash(algo: 'sha3-256' | 'sha3-512'): HashFactory {
  const fn = ((msg: Uint8Array | string) => {
    const h = createHash(algo)
    h.update(msg as any)
    return new Uint8Array(h.digest())
  }) as HashFactory

  fn.algorithm = algo
  fn.create = () => {
    const h = createHash(algo)
    return {
      update(data: Uint8Array | string) {
        h.update(data as any)
        return this
      },
      digest() {
        return new Uint8Array(h.digest())
      },
    }
  }
  return fn
}

export const sha3_256 = makeHash('sha3-256')
export const sha3_512 = makeHash('sha3-512')
