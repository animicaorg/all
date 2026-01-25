/**
 * GCP Secret Manager Provider (Stub)
 * Production implementation would fetch from GCP Secret Manager
 */

import { SecretProvider, SecretNotFoundError, SecretProviderError } from '../types.js';

export interface GcpSecretManagerConfig {
  projectId: string;
  /**
   * Optional prefix for secret names
   */
  prefix?: string;
}

/**
 * GCP Secret Manager Provider
 * 
 * To use in production:
 * 1. Install @google-cloud/secret-manager
 * 2. Configure GCP credentials (service account, ADC, etc.)
 * 3. Ensure service account has Secret Manager Secret Accessor role
 * 
 * Example:
 * ```typescript
 * const provider = new GcpSecretManagerProvider({
 *   projectId: 'my-project',
 *   prefix: 'prod-exchange-'
 * });
 * const dbUrl = await provider.requireSecret('DATABASE_URL');
 * ```
 */
export class GcpSecretManagerProvider implements SecretProvider {
  private cache: Map<string, string> = new Map();
  private readonly config: GcpSecretManagerConfig;

  constructor(config: GcpSecretManagerConfig) {
    this.config = config;
  }

  async getSecret(key: string): Promise<string | undefined> {
    // Check cache first
    if (this.cache.has(key)) {
      return this.cache.get(key);
    }

    const secretName = this.config.prefix ? `${this.config.prefix}${key}` : key;
    const fullName = `projects/${this.config.projectId}/secrets/${secretName}/versions/latest`;

    try {
      // TODO: In production, uncomment and use actual GCP SDK
      /*
      const { SecretManagerServiceClient } = await import('@google-cloud/secret-manager');
      const client = new SecretManagerServiceClient();
      
      const [version] = await client.accessSecretVersion({ name: fullName });
      const payload = version.payload?.data?.toString();
      
      if (payload) {
        this.cache.set(key, payload);
        return payload;
      }
      */

      // Stub: Fall back to environment variable
      const envValue = process.env[key];
      if (envValue) {
        this.cache.set(key, envValue);
        return envValue;
      }

      return undefined;
    } catch (error: any) {
      if (error.code === 5) { // NOT_FOUND
        return undefined;
      }
      throw new SecretProviderError(
        `Failed to fetch secret ${secretName}: ${error.message}`,
        error
      );
    }
  }

  async requireSecret(key: string): Promise<string> {
    const value = await this.getSecret(key);
    if (value === undefined) {
      throw new SecretNotFoundError(key);
    }
    return value;
  }

  async hasSecret(key: string): Promise<boolean> {
    const value = await this.getSecret(key);
    return value !== undefined;
  }

  async listKeys(): Promise<string[]> {
    // In production, this would list secrets from GCP
    // For now, return cache keys
    return Array.from(this.cache.keys());
  }

  /**
   * Clear the cache (useful for rotation)
   */
  clearCache(): void {
    this.cache.clear();
  }
}
