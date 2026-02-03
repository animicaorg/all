/**
 * Admin Bootstrap Service
 * Creates the first admin account on initial login attempt.
 */

import type { PrismaClient, Admin } from '@prisma/client';
import type { Config } from '../config.js';
import type { Logger } from '../utils/logger.js';
import { hashPassword } from '../utils/crypto.js';

const BOOTSTRAP_LOCK_ID = 941337;

export interface BootstrapCredentials {
  email: string;
  password: string;
}

export interface BootstrapResult {
  created: boolean;
  admin?: Admin;
}

export class AdminBootstrapService {
  constructor(
    private prisma: PrismaClient,
    private config: Config,
    private logger: Logger
  ) {}

  private validatePassword(password: string): void {
    const hasLetter = /[A-Za-z]/.test(password);
    const hasNumber = /\d/.test(password);
    if (password.length < 10 || !hasLetter || !hasNumber) {
      throw new Error('Invalid credentials');
    }
  }

  async bootstrapIfNeeded(
    credentials: BootstrapCredentials,
    bootstrapSecret: string | undefined,
    ip?: string
  ): Promise<BootstrapResult> {
    const { email, password } = credentials;

    return this.prisma.$transaction(async (tx) => {
      await tx.$queryRaw`SELECT pg_advisory_xact_lock(${BOOTSTRAP_LOCK_ID})`;

      const adminCount = await tx.admin.count();
      if (adminCount > 0) {
        return { created: false };
      }

      if (!bootstrapSecret || bootstrapSecret !== this.config.ADMIN_BOOTSTRAP_SECRET) {
        this.logger.warn({ email }, 'Invalid bootstrap secret provided');
        throw new Error('Invalid credentials');
      }

      this.validatePassword(password);

      const admin = await tx.admin.create({
        data: {
          email: email.toLowerCase(),
          passwordHash: await hashPassword(password),
          role: 'SUPERADMIN',
          status: 'ACTIVE',
        },
      });

      await tx.auditLog.create({
        data: {
          actorType: 'SYSTEM',
          action: 'admin_bootstrap_created',
          entityType: 'ADMIN',
          entityId: admin.id,
          ip: ip ?? null,
          metadata: {
            email: admin.email,
          },
        },
      });

      this.logger.info({ adminId: admin.id, email: admin.email }, 'Admin bootstrap created');

      return { created: true, admin };
    });
  }
}
