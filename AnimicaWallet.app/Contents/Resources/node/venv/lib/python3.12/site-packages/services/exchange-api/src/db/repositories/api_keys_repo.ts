/**
 * API Keys Repository
 * Data access layer for API key management
 */

import type { PrismaClient, ApiKey } from '@prisma/client';
import { randomBytes, randomUUID } from 'crypto';
import argon2 from 'argon2';

export interface CreateApiKeyInput {
  userId: string;
  name: string;
  scopes: string[];
  ipAllowlist?: string[];
}

export interface ApiKeyWithSecret {
  apiKey: ApiKey;
  keyId: string; // The actual key to give to user
  secret: string; // The actual secret to give to user
}

export class ApiKeysRepository {
  constructor(private prisma: PrismaClient) {}

  /**
   * Create a new API key
   * Returns the key and secret which should be shown to user only once
   */
  async createApiKey(input: CreateApiKeyInput): Promise<ApiKeyWithSecret> {
    // Generate secure random key and secret
    const keyId = this.generateKey();
    const secret = this.generateSecret();

    // Hash the secret for storage
    const secretHash = await argon2.hash(secret, {
      type: argon2.argon2id,
      memoryCost: 65536,
      timeCost: 3,
      parallelism: 4,
    });

    const apiKey = await this.prisma.apiKey.create({
      data: {
        userId: input.userId,
        name: input.name,
        keyId,
        secretHash,
        scopes: input.scopes,
        ipAllowlist: input.ipAllowlist || null,
      },
    });

    return {
      apiKey,
      keyId,
      secret,
    };
  }

  /**
   * Get API key by key ID
   */
  async getByKeyId(keyId: string): Promise<ApiKey | null> {
    return this.prisma.apiKey.findUnique({
      where: { keyId },
    });
  }

  /**
   * Get API keys for a user
   */
  async getByUserId(userId: string): Promise<ApiKey[]> {
    return this.prisma.apiKey.findMany({
      where: { userId },
      orderBy: { createdAt: 'desc' },
    });
  }

  /**
   * Revoke an API key
   */
  async revokeApiKey(id: string): Promise<ApiKey> {
    return this.prisma.apiKey.update({
      where: { id },
      data: { revokedAt: new Date() },
    });
  }

  /**
   * Update last used timestamp
   */
  async updateLastUsed(id: string): Promise<void> {
    await this.prisma.apiKey.update({
      where: { id },
      data: { lastUsedAt: new Date() },
    });
  }

  /**
   * Verify secret against hash
   */
  async verifySecret(hash: string, secret: string): Promise<boolean> {
    try {
      return await argon2.verify(hash, secret);
    } catch {
      return false;
    }
  }

  /**
   * Check if API key has specific scope
   */
  hasScope(apiKey: ApiKey, scope: string): boolean {
    const scopes = apiKey.scopes as string[];
    return scopes.includes(scope) || scopes.includes('*');
  }

  /**
   * Check if IP is allowed
   */
  isIpAllowed(apiKey: ApiKey, ip: string): boolean {
    if (!apiKey.ipAllowlist) return true; // No allowlist means all IPs allowed
    
    const allowlist = apiKey.ipAllowlist as string[];
    return allowlist.includes(ip);
  }

  /**
   * Generate a secure key ID (32 chars hex = 128 bits)
   */
  private generateKey(): string {
    return randomBytes(16).toString('hex');
  }

  /**
   * Generate a secure secret (64 chars hex = 256 bits)
   */
  private generateSecret(): string {
    return randomBytes(32).toString('hex');
  }
}
