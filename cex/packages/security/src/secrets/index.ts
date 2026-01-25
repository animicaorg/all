/**
 * Secrets Management Package
 * Unified interface for accessing secrets from various backends
 */

export * from './types.js';
export * from './providers/env.js';
export * from './providers/aws_sm.js';
export * from './providers/gcp_sm.js';
export * from './redaction.js';

import { SecretProvider } from './types.js';
import { EnvSecretProvider } from './providers/env.js';

/**
 * Global secret provider instance
 * Can be configured at application startup
 */
let globalProvider: SecretProvider = new EnvSecretProvider();

/**
 * Configure the global secret provider
 */
export function configureSecrets(provider: SecretProvider): void {
  globalProvider = provider;
}

/**
 * Get the current global provider
 */
export function getProvider(): SecretProvider {
  return globalProvider;
}

/**
 * Get a secret using the global provider
 */
export async function getSecret(key: string): Promise<string | undefined> {
  return globalProvider.getSecret(key);
}

/**
 * Require a secret using the global provider (throws if not found)
 */
export async function requireSecret(key: string): Promise<string> {
  return globalProvider.requireSecret(key);
}

/**
 * Check if a secret exists using the global provider
 */
export async function hasSecret(key: string): Promise<boolean> {
  return globalProvider.hasSecret(key);
}

/**
 * List all secret keys using the global provider
 */
export async function listSecretKeys(): Promise<string[]> {
  return globalProvider.listKeys();
}

/**
 * Health check for secrets
 * Returns status of required secrets
 */
export interface SecretsHealthCheck {
  status: 'healthy' | 'degraded' | 'unhealthy';
  required: { key: string; available: boolean }[];
  optional: { key: string; available: boolean }[];
}

/**
 * Check health of secrets configuration
 */
export async function checkSecretsHealth(
  required: string[],
  optional: string[] = []
): Promise<SecretsHealthCheck> {
  const requiredResults = await Promise.all(
    required.map(async (key) => ({
      key,
      available: await hasSecret(key),
    }))
  );

  const optionalResults = await Promise.all(
    optional.map(async (key) => ({
      key,
      available: await hasSecret(key),
    }))
  );

  const allRequiredAvailable = requiredResults.every((r) => r.available);
  const anyOptionalMissing = optionalResults.some((r) => !r.available);

  let status: 'healthy' | 'degraded' | 'unhealthy';
  if (!allRequiredAvailable) {
    status = 'unhealthy';
  } else if (anyOptionalMissing) {
    status = 'degraded';
  } else {
    status = 'healthy';
  }

  return {
    status,
    required: requiredResults,
    optional: optionalResults,
  };
}
