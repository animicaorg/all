import { describe, expect, it, vi } from 'vitest';
import { AdminBootstrapService } from '../admin_bootstrap.js';

const makeConfig = () => ({
  ADMIN_BOOTSTRAP_SECRET: 'bootstrap-secret-12345',
} as any);

const makeLogger = () => ({
  info: vi.fn(),
  warn: vi.fn(),
});

function createMockPrisma() {
  const state = {
    admins: [] as any[],
    auditLogs: [] as any[],
  };

  const prisma: any = {
    admin: {
      count: vi.fn(async () => state.admins.length),
      create: vi.fn(async ({ data }: any) => {
        const admin = { id: `admin-${state.admins.length + 1}`, ...data };
        state.admins.push(admin);
        return admin;
      }),
    },
    auditLog: {
      create: vi.fn(async ({ data }: any) => {
        state.auditLogs.push(data);
        return data;
      }),
    },
    $queryRaw: vi.fn(async () => []),
    $transaction: async (fn: any) => fn(prisma),
  };

  return { prisma, state };
}

describe('AdminBootstrapService', () => {
  it('creates the first admin when none exist and secret is valid', async () => {
    const { prisma, state } = createMockPrisma();
    const service = new AdminBootstrapService(prisma, makeConfig(), makeLogger() as any);

    const result = await service.bootstrapIfNeeded(
      { email: 'admin@example.com', password: 'Password1234' },
      'bootstrap-secret-12345',
      '127.0.0.1'
    );

    expect(result.created).toBe(true);
    expect(state.admins).toHaveLength(1);
    expect(state.auditLogs).toHaveLength(1);
  });

  it('does not create another admin after bootstrap', async () => {
    const { prisma, state } = createMockPrisma();
    const service = new AdminBootstrapService(prisma, makeConfig(), makeLogger() as any);

    await service.bootstrapIfNeeded(
      { email: 'admin@example.com', password: 'Password1234' },
      'bootstrap-secret-12345'
    );

    const result = await service.bootstrapIfNeeded(
      { email: 'admin2@example.com', password: 'Password1234' },
      'bootstrap-secret-12345'
    );

    expect(result.created).toBe(false);
    expect(state.admins).toHaveLength(1);
  });

  it('rejects invalid bootstrap secrets', async () => {
    const { prisma } = createMockPrisma();
    const service = new AdminBootstrapService(prisma, makeConfig(), makeLogger() as any);

    await expect(
      service.bootstrapIfNeeded(
        { email: 'admin@example.com', password: 'Password1234' },
        'wrong-secret'
      )
    ).rejects.toThrow('Invalid credentials');
  });
});
