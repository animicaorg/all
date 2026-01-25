/**
 * Local Signer Implementation
 * Uses local private keys for signing (HMAC-SHA256)
 */

import { createHmac } from 'crypto';
import { Signer, Signature, KeyNotFoundError, SigningError } from './types.js';

export interface LocalSignerConfig {
  /**
   * Keys for signing/verification
   * Map of key ID to key material (base64 or Buffer)
   */
  keys: Map<string, Buffer | string>;

  /**
   * Current key ID (used for signing)
   */
  currentKeyId?: string;

  /**
   * Algorithm (default: HMAC-SHA256)
   */
  algorithm?: string;
}

/**
 * Local signer using HMAC
 * Suitable for API signature verification and service tokens
 */
export class LocalSigner implements Signer {
  private keys: Map<string, Buffer>;
  private currentKeyId: string;
  private algorithm: string;

  constructor(config: LocalSignerConfig) {
    this.algorithm = config.algorithm || 'HMAC-SHA256';
    this.keys = new Map();

    // Convert string keys to Buffer
    for (const [keyId, keyMaterial] of config.keys.entries()) {
      const buffer =
        typeof keyMaterial === 'string'
          ? Buffer.from(keyMaterial, 'base64')
          : keyMaterial;
      this.keys.set(keyId, buffer);
    }

    if (this.keys.size === 0) {
      throw new Error('At least one key must be provided');
    }

    // Set current key ID
    this.currentKeyId =
      config.currentKeyId || Array.from(this.keys.keys())[this.keys.size - 1];

    if (!this.keys.has(this.currentKeyId)) {
      throw new KeyNotFoundError(this.currentKeyId);
    }
  }

  async sign(message: Buffer, keyId?: string): Promise<Signature> {
    const useKeyId = keyId || this.currentKeyId;
    const key = this.keys.get(useKeyId);

    if (!key) {
      throw new KeyNotFoundError(useKeyId);
    }

    try {
      const hmac = createHmac('sha256', key);
      hmac.update(message);
      const signature = hmac.digest();

      return {
        signature,
        keyId: useKeyId,
        algorithm: this.algorithm,
      };
    } catch (error: any) {
      throw new SigningError(`Failed to sign message: ${error.message}`, error);
    }
  }

  async verify(message: Buffer, signature: Buffer, keyId?: string): Promise<boolean> {
    try {
      // If keyId is provided, verify with that key only
      if (keyId) {
        const key = this.keys.get(keyId);
        if (!key) {
          return false; // Key not found, signature invalid
        }

        const hmac = createHmac('sha256', key);
        hmac.update(message);
        const expected = hmac.digest();

        return signature.equals(expected);
      }

      // Otherwise, try all keys (for rotation support)
      for (const key of this.keys.values()) {
        const hmac = createHmac('sha256', key);
        hmac.update(message);
        const expected = hmac.digest();

        if (signature.equals(expected)) {
          return true;
        }
      }

      return false;
    } catch (error: any) {
      throw new SigningError(`Failed to verify signature: ${error.message}`, error);
    }
  }

  async getPublicKey(keyId?: string): Promise<Buffer | Map<string, Buffer>> {
    if (keyId) {
      const key = this.keys.get(keyId);
      if (!key) {
        throw new KeyNotFoundError(keyId);
      }
      // For HMAC, the "public key" is the shared secret (don't expose in production)
      return key;
    }

    return new Map(this.keys);
  }

  async getKeyIds(): Promise<string[]> {
    return Array.from(this.keys.keys());
  }

  /**
   * Get current key ID
   */
  getCurrentKeyId(): string {
    return this.currentKeyId;
  }

  /**
   * Add a new key (for rotation)
   */
  addKey(keyId: string, keyMaterial: Buffer | string): void {
    const buffer =
      typeof keyMaterial === 'string' ? Buffer.from(keyMaterial, 'base64') : keyMaterial;
    this.keys.set(keyId, buffer);
  }

  /**
   * Remove a key
   */
  removeKey(keyId: string): boolean {
    if (keyId === this.currentKeyId) {
      throw new Error('Cannot remove current key');
    }
    return this.keys.delete(keyId);
  }

  /**
   * Set current key ID
   */
  setCurrentKey(keyId: string): void {
    if (!this.keys.has(keyId)) {
      throw new KeyNotFoundError(keyId);
    }
    this.currentKeyId = keyId;
  }
}
