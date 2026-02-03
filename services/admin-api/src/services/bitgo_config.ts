/**
 * BitGo Configuration Service
 * Stores encrypted BitGo settings and provides masked responses.
 */

import type { PrismaClient } from '@prisma/client';
import type { Config } from '../config.js';
import type { Logger } from '../utils/logger.js';
import { decryptSecret, encryptSecret, maskSecret, normalizeEncryptionKey } from '../utils/encryption.js';

const BITGO_CONFIG_ID = 'default';

export type BitgoEnvironment = 'test' | 'prod';
type BitgoEnvironmentDb = 'TEST' | 'PROD';

export interface BitgoConfigPayload {
  environment: BitgoEnvironment;
  baseUrl?: string | null;
  accessToken?: string | null;
  webhookSecret?: string | null;
  wallets?: Record<string, string> | null;
  coins?: Record<string, any> | null;
  enabled: boolean;
}

export interface BitgoConfigResponse {
  id: string;
  environment: BitgoEnvironment;
  baseUrl: string | null;
  wallets: Record<string, string> | null;
  coins: Record<string, any> | null;
  enabled: boolean;
  accessTokenMasked: string | null;
  webhookSecretMasked: string | null;
  updatedAt: string | null;
}

export interface BitgoEffectiveConfig {
  environment: BitgoEnvironment;
  baseUrl: string;
  accessToken?: string;
  webhookSecret?: string;
  wallets: Record<string, string> | null;
  coins: Record<string, any> | null;
  enabled: boolean;
}

export class BitgoConfigService {
  private encryptionKey: Buffer;

  constructor(
    private prisma: PrismaClient,
    private config: Config,
    private logger: Logger
  ) {
    this.encryptionKey = normalizeEncryptionKey(config.CONFIG_ENCRYPTION_KEY);
  }

  private toDbEnvironment(env: BitgoEnvironment): BitgoEnvironmentDb {
    return env === 'prod' ? 'PROD' : 'TEST';
  }

  private fromDbEnvironment(env: BitgoEnvironmentDb): BitgoEnvironment {
    return env === 'PROD' ? 'prod' : 'test';
  }

  async getConfig(): Promise<BitgoConfigResponse> {
    const record = await this.prisma.bitgoConfig.findUnique({
      where: { id: BITGO_CONFIG_ID },
    });

    if (!record) {
      return {
        id: BITGO_CONFIG_ID,
        environment: this.config.BITGO_ENV,
        baseUrl: this.config.BITGO_API_URL ?? null,
        wallets: null,
        coins: null,
        enabled: false,
        accessTokenMasked: this.config.BITGO_ACCESS_TOKEN ? '••••' : null,
        webhookSecretMasked: null,
        updatedAt: null,
      };
    }

    return {
      id: record.id,
      environment: this.fromDbEnvironment(record.environment as BitgoEnvironmentDb),
      baseUrl: record.baseUrl,
      wallets: (record.wallets as Record<string, string> | null) ?? null,
      coins: (record.coins as Record<string, any> | null) ?? null,
      enabled: record.enabled,
      accessTokenMasked: record.accessTokenEncrypted
        ? maskSecret(decryptSecret(record.accessTokenEncrypted, this.encryptionKey))
        : null,
      webhookSecretMasked: record.webhookSecretEncrypted
        ? maskSecret(decryptSecret(record.webhookSecretEncrypted, this.encryptionKey))
        : null,
      updatedAt: record.updatedAt?.toISOString() ?? null,
    };
  }

  async getEffectiveConfig(): Promise<BitgoEffectiveConfig> {
    const record = await this.prisma.bitgoConfig.findUnique({
      where: { id: BITGO_CONFIG_ID },
    });

    if (!record || !record.accessTokenEncrypted) {
      const baseUrl = this.config.BITGO_API_URL
        ?? (this.config.BITGO_ENV === 'prod' ? 'https://app.bitgo.com' : 'https://app.bitgo-test.com');

      return {
        environment: this.config.BITGO_ENV,
        baseUrl,
        accessToken: this.config.BITGO_ACCESS_TOKEN,
        webhookSecret: undefined,
        wallets: null,
        coins: null,
        enabled: false,
      };
    }

    return {
      environment: this.fromDbEnvironment(record.environment as BitgoEnvironmentDb),
      baseUrl: record.baseUrl
        ?? (record.environment === 'PROD' ? 'https://app.bitgo.com' : 'https://app.bitgo-test.com'),
      accessToken: decryptSecret(record.accessTokenEncrypted, this.encryptionKey),
      webhookSecret: record.webhookSecretEncrypted
        ? decryptSecret(record.webhookSecretEncrypted, this.encryptionKey)
        : undefined,
      wallets: (record.wallets as Record<string, string> | null) ?? null,
      coins: (record.coins as Record<string, any> | null) ?? null,
      enabled: record.enabled,
    };
  }

  async updateConfig(
    payload: BitgoConfigPayload,
    adminId: string
  ): Promise<{ response: BitgoConfigResponse; before: BitgoConfigResponse | null }> {
    const before = await this.prisma.bitgoConfig.findUnique({
      where: { id: BITGO_CONFIG_ID },
    });

    const updateData: any = {
      environment: this.toDbEnvironment(payload.environment),
      baseUrl: payload.baseUrl?.trim() || null,
      wallets: payload.wallets ?? null,
      coins: payload.coins ?? null,
      enabled: payload.enabled,
      updatedBy: adminId,
    };

    if (payload.accessToken !== undefined) {
      updateData.accessTokenEncrypted =
        payload.accessToken && payload.accessToken.trim().length > 0
          ? encryptSecret(payload.accessToken.trim(), this.encryptionKey)
          : null;
    }

    if (payload.webhookSecret !== undefined) {
      updateData.webhookSecretEncrypted =
        payload.webhookSecret && payload.webhookSecret.trim().length > 0
          ? encryptSecret(payload.webhookSecret.trim(), this.encryptionKey)
          : null;
    }

    await this.prisma.bitgoConfig.upsert({
      where: { id: BITGO_CONFIG_ID },
      create: {
        id: BITGO_CONFIG_ID,
        ...updateData,
      },
      update: updateData,
    });

    const response = await this.getConfig();

    return {
      response,
      before: before
        ? {
            id: before.id,
            environment: this.fromDbEnvironment(before.environment as BitgoEnvironmentDb),
            baseUrl: before.baseUrl,
            wallets: (before.wallets as Record<string, string> | null) ?? null,
            coins: (before.coins as Record<string, any> | null) ?? null,
            enabled: before.enabled,
            accessTokenMasked: before.accessTokenEncrypted
              ? maskSecret(decryptSecret(before.accessTokenEncrypted, this.encryptionKey))
              : null,
            webhookSecretMasked: before.webhookSecretEncrypted
              ? maskSecret(decryptSecret(before.webhookSecretEncrypted, this.encryptionKey))
              : null,
            updatedAt: before.updatedAt?.toISOString() ?? null,
          }
        : null,
    };
  }

  async testConnection(): Promise<{ ok: boolean; message: string }> {
    const config = await this.getEffectiveConfig();

    if (!config.accessToken) {
      return { ok: false, message: 'BitGo access token is not configured.' };
    }

    const url = `${config.baseUrl}/api/v2/user/me`;

    try {
      const response = await fetch(url, {
        method: 'GET',
        headers: {
          Authorization: `Bearer ${config.accessToken}`,
          'Content-Type': 'application/json',
        },
      });

      if (!response.ok) {
        const body = await response.text();
        this.logger.warn({ status: response.status, body }, 'BitGo connection test failed');
        return { ok: false, message: `BitGo responded with status ${response.status}.` };
      }

      return { ok: true, message: 'BitGo connection successful.' };
    } catch (error) {
      this.logger.error({ error }, 'BitGo connection test error');
      return { ok: false, message: 'Failed to reach BitGo endpoint.' };
    }
  }
}
