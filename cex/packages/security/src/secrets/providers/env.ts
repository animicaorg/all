/**
 * Environment Variable Secret Provider
 * Reads secrets from process.env
 */

import { SecretProvider, SecretNotFoundError } from '../types.js';

export class EnvSecretProvider implements SecretProvider {
  constructor(private readonly prefix: string = '') {}

  async getSecret(key: string): Promise<string | undefined> {
    const fullKey = this.prefix ? `${this.prefix}${key}` : key;
    return process.env[fullKey];
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
    if (!this.prefix) {
      return Object.keys(process.env);
    }
    return Object.keys(process.env)
      .filter((key) => key.startsWith(this.prefix))
      .map((key) => key.slice(this.prefix.length));
  }
}
