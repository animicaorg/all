import { describe, expect, it, vi } from 'vitest';
import { BitgoConfigService } from '../bitgo_config.js';

const encryptionKey = Buffer.alloc(32, "b").toString("base64");

const makeConfig = () => ({
  CONFIG_ENCRYPTION_KEY: encryptionKey,
  BITGO_ENV: 'test',
  BITGO_API_URL: 'https://app.bitgo-test.com',
  BITGO_ACCESS_TOKEN: 'env-token',
} as any);

const makeLogger = () => ({
  info: vi.fn(),
  warn: vi.fn(),
  error: vi.fn(),
});

function createMockPrisma() {
  const state: any = {
    bitgoConfig: null,
  };

  const prisma: any = {
    bitgoConfig: {
      findUnique: vi.fn(async () => state.bitgoConfig),
      upsert: vi.fn(async ({ create, update }: any) => {
        state.bitgoConfig = state.bitgoConfig ? { ...state.bitgoConfig, ...update } : { ...create };
        return state.bitgoConfig;
      }),
    },
  };

  return { prisma, state };
}

describe('BitgoConfigService', () => {
  it('encrypts secrets and returns masked values', async () => {
    const { prisma, state } = createMockPrisma();
    const service = new BitgoConfigService(prisma, makeConfig(), makeLogger() as any);

    const { response } = await service.updateConfig(
      {
        environment: 'test',
        baseUrl: 'https://app.bitgo-test.com',
        accessToken: 'secret-token-1234',
        webhookSecret: 'webhook-secret-5678',
        wallets: { btc: 'wallet-id' },
        coins: { btc: { feePolicy: 'standard' } },
        enabled: true,
      },
      'admin-1'
    );

    expect(state.bitgoConfig.accessTokenEncrypted).not.toContain('secret-token-1234');
    expect(response.accessTokenMasked).toContain('1234');
    expect(response.webhookSecretMasked).toContain('5678');
  });

  it('uses stored config for effective settings', async () => {
    const { prisma } = createMockPrisma();
    const service = new BitgoConfigService(prisma, makeConfig(), makeLogger() as any);

    await service.updateConfig(
      {
        environment: 'prod',
        baseUrl: 'https://app.bitgo.com',
        accessToken: 'secret-token-9999',
        webhookSecret: null,
        wallets: null,
        coins: null,
        enabled: true,
      },
      'admin-1'
    );

    const effective = await service.getEffectiveConfig();
    expect(effective.environment).toBe('prod');
    expect(effective.baseUrl).toBe('https://app.bitgo.com');
    expect(effective.accessToken).toBe('secret-token-9999');
  });
});
