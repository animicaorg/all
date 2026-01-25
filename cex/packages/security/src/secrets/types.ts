/**
 * Secrets Management Interface
 * Provides abstraction over various secret storage backends
 */

export interface SecretProvider {
  /**
   * Get a secret by key
   * @returns Secret value or undefined if not found
   */
  getSecret(key: string): Promise<string | undefined>;

  /**
   * Get a secret by key or throw if not found
   * @throws Error if secret is not found
   */
  requireSecret(key: string): Promise<string>;

  /**
   * Check if a secret exists
   */
  hasSecret(key: string): Promise<boolean>;

  /**
   * List all secret keys (for debugging/health checks)
   * Should not return values
   */
  listKeys(): Promise<string[]>;
}

/**
 * Configuration for secret rotation
 */
export interface KeyRingConfig {
  /**
   * Current key ID (used for signing/encryption)
   */
  currentKeyId: string;

  /**
   * All active keys (used for verification/decryption)
   * Map of key ID to key material (base64)
   */
  keys: Map<string, string>;
}

/**
 * Parse keyring from environment variable
 * Format: "kid1:base64key1,kid2:base64key2"
 */
export function parseKeyRing(keyringStr: string): KeyRingConfig {
  const entries = keyringStr.split(',').map((entry) => {
    const [keyId, keyMaterial] = entry.split(':');
    if (!keyId || !keyMaterial) {
      throw new Error(`Invalid keyring entry: ${entry}`);
    }
    return [keyId.trim(), keyMaterial.trim()] as const;
  });

  if (entries.length === 0) {
    throw new Error('Keyring must contain at least one key');
  }

  const keys = new Map(entries);
  const currentKeyId = entries[entries.length - 1][0]; // Last key is current

  return { currentKeyId, keys };
}

/**
 * Get current signing key from keyring
 */
export function getCurrentKey(keyring: KeyRingConfig): { keyId: string; key: Buffer } {
  const keyMaterial = keyring.keys.get(keyring.currentKeyId);
  if (!keyMaterial) {
    throw new Error(`Current key ${keyring.currentKeyId} not found in keyring`);
  }
  return {
    keyId: keyring.currentKeyId,
    key: Buffer.from(keyMaterial, 'base64'),
  };
}

/**
 * Get a key by ID from keyring (for verification)
 */
export function getKeyById(keyring: KeyRingConfig, keyId: string): Buffer | undefined {
  const keyMaterial = keyring.keys.get(keyId);
  return keyMaterial ? Buffer.from(keyMaterial, 'base64') : undefined;
}

/**
 * Base error for secrets management
 */
export class SecretsError extends Error {
  constructor(message: string, public readonly code: string) {
    super(message);
    this.name = 'SecretsError';
  }
}

export class SecretNotFoundError extends SecretsError {
  constructor(key: string) {
    super(`Secret not found: ${key}`, 'SECRET_NOT_FOUND');
    this.name = 'SecretNotFoundError';
  }
}

export class SecretProviderError extends SecretsError {
  constructor(message: string, public readonly cause?: Error) {
    super(message, 'PROVIDER_ERROR');
    this.name = 'SecretProviderError';
  }
}
