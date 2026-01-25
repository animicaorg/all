/**
 * Signer Interface
 * Abstraction for signing operations (local, HSM, etc.)
 */

/**
 * Signature result
 */
export interface Signature {
  /**
   * Signature data (format depends on algorithm)
   */
  signature: Buffer;

  /**
   * Key ID used for signing (for key rotation support)
   */
  keyId?: string;

  /**
   * Algorithm used
   */
  algorithm: string;
}

/**
 * Signer interface for abstracting signing operations
 */
export interface Signer {
  /**
   * Sign a message
   * @param message - Message to sign
   * @param keyId - Optional key ID (for multi-key support)
   */
  sign(message: Buffer, keyId?: string): Promise<Signature>;

  /**
   * Verify a signature
   * @param message - Original message
   * @param signature - Signature to verify
   * @param keyId - Optional key ID (for verification with specific key)
   */
  verify(message: Buffer, signature: Buffer, keyId?: string): Promise<boolean>;

  /**
   * Get public key(s)
   * @param keyId - Optional key ID, if not provided returns all public keys
   */
  getPublicKey(keyId?: string): Promise<Buffer | Map<string, Buffer>>;

  /**
   * Get available key IDs
   */
  getKeyIds(): Promise<string[]>;
}

/**
 * Signer errors
 */
export class SignerError extends Error {
  constructor(message: string, public readonly code: string) {
    super(message);
    this.name = 'SignerError';
  }
}

export class KeyNotFoundError extends SignerError {
  constructor(keyId: string) {
    super(`Key not found: ${keyId}`, 'KEY_NOT_FOUND');
    this.name = 'KeyNotFoundError';
  }
}

export class SigningError extends SignerError {
  constructor(message: string, public readonly cause?: Error) {
    super(message, 'SIGNING_ERROR');
    this.name = 'SigningError';
  }
}
