import { createHmac } from 'crypto'
import type { sha3_256, sha3_512 } from './sha3'

type HashFn = typeof sha3_256 | typeof sha3_512

type HmacInstance = {
  update: (data: Uint8Array | string) => HmacInstance
  digest: () => Uint8Array
}

function makeHmac(hash: HashFn, key: Uint8Array | string, firstData?: Uint8Array | string) {
  const h = createHmac((hash as any).algorithm, key as any)
  if (firstData !== undefined) h.update(firstData as any)
  const inst: HmacInstance = {
    update(data: Uint8Array | string) {
      h.update(data as any)
      return inst
    },
    digest() {
      return new Uint8Array(h.digest())
    },
  }
  return inst
}

export function hmac(hash: HashFn, key: Uint8Array | string, msg?: Uint8Array | string): Uint8Array | HmacInstance {
  if (msg !== undefined) {
    const h = createHmac((hash as any).algorithm, key as any)
    h.update(msg as any)
    return new Uint8Array(h.digest())
  }
  return makeHmac(hash, key)
}

hmac.create = function create(hash: HashFn, key: Uint8Array | string) {
  return makeHmac(hash, key)
}
