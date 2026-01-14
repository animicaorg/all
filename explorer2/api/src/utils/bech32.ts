/**
 * Bech32m address encoding utilities for Animica addresses.
 * Converts raw address bytes to human-readable bech32m format (anim1...).
 */

import { bech32m } from 'bech32'

const DEFAULT_ALG_ID = 1 // Dilithium3
const ANIM_PREFIX = 'anim'

/**
 * Convert address bytes to bech32m format (anim1...).
 * 
 * @param addressBytes - Raw address bytes (32 or 34 bytes)
 * @returns Bech32m encoded address (anim1...) or hex fallback
 * 
 * @remarks
 * - 32-byte addresses (digest only): prepends default algorithm ID
 * - 34-byte addresses: already contain algorithm ID
 * - Other lengths: returns hex format as fallback
 */
export function addressToBech32(addressBytes: Buffer | Uint8Array): string {
  try {
    const buffer = Buffer.from(addressBytes)
    let payload: Buffer
    
    if (buffer.length === 32) {
      // StateDB stores only 32-byte digest, prepend algorithm ID
      const algId = Buffer.from([0x00, DEFAULT_ALG_ID]) // 2 bytes: big-endian alg_id
      payload = Buffer.concat([algId, buffer])
    } else if (buffer.length === 34) {
      // Already has algorithm ID
      payload = buffer
    } else {
      // Unexpected length, return hex
      return `0x${buffer.toString('hex')}`
    }
    
    const words = bech32m.toWords(payload)
    return bech32m.encode(ANIM_PREFIX, words)
  } catch {
    // Fallback to hex on any error
    return `0x${Buffer.from(addressBytes).toString('hex')}`
  }
}
