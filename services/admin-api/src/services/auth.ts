/**
 * Authentication Service
 * Handles admin login, session management, and TOTP verification
 */

import jwt from 'jsonwebtoken';
import type { SignOptions } from 'jsonwebtoken';
import type { PrismaClient, Admin } from '@prisma/client';
import type { Config } from '../config.js';
import type { Logger } from '../utils/logger.js';
import { hashPassword, verifyPassword, verifyTotpToken, generateSecureToken } from '../utils/crypto.js';

export interface LoginCredentials {
  email: string;
  password: string;
  totpToken?: string;
}

export interface LoginResult {
  admin: Omit<Admin, 'passwordHash' | 'totpSecretEncrypted'>;
  accessToken: string;
  refreshToken: string;
  sessionId: string;
}

export class AuthService {
  constructor(
    private prisma: PrismaClient,
    private config: Config,
    private logger: Logger
  ) {}

  /**
   * Authenticate admin and create session
   */
  async login(credentials: LoginCredentials, ip?: string, userAgent?: string): Promise<LoginResult> {
    const { email, password, totpToken } = credentials;

    // Find admin by email
    const admin = await this.prisma.admin.findUnique({
      where: { email: email.toLowerCase() },
    });

    if (!admin) {
      this.logger.warn({ email }, 'Login attempt for non-existent admin');
      throw new Error('Invalid credentials');
    }

    if (admin.status !== 'ACTIVE') {
      this.logger.warn({ email, status: admin.status }, 'Login attempt for inactive admin');
      throw new Error('Account is disabled');
    }

    // Verify password
    const validPassword = await verifyPassword(admin.passwordHash, password);
    if (!validPassword) {
      this.logger.warn({ email }, 'Invalid password attempt');
      throw new Error('Invalid credentials');
    }

    // Verify TOTP if enabled
    if (admin.totpSecretEncrypted) {
      if (!totpToken) {
        throw new Error('TOTP token required');
      }

      // In production, decrypt the TOTP secret first
      // For now, assume it's stored as base32
      const totpValid = verifyTotpToken(admin.totpSecretEncrypted, totpToken, this.config.TOTP_WINDOW);
      if (!totpValid) {
        this.logger.warn({ email }, 'Invalid TOTP token');
        throw new Error('Invalid TOTP token');
      }
    }

    // Create session
    const refreshToken = generateSecureToken(32);
    const refreshTokenHash = await hashPassword(refreshToken);
    const expiresAt = new Date();
    const [, expiresValue, expiresUnit] = this.config.REFRESH_TOKEN_EXPIRES_IN.match(/^(\d+)([dhms])$/) || [];
    
    if (expiresUnit === 'd') {
      expiresAt.setDate(expiresAt.getDate() + parseInt(expiresValue));
    } else if (expiresUnit === 'h') {
      expiresAt.setHours(expiresAt.getHours() + parseInt(expiresValue));
    }

    const session = await this.prisma.adminSession.create({
      data: {
        adminId: admin.id,
        refreshTokenHash,
        expiresAt,
        ip,
        userAgent,
      },
    });

    // Update last login
    await this.prisma.admin.update({
      where: { id: admin.id },
      data: { lastLoginAt: new Date() },
    });

    // Generate JWT access token
    const jwtOptions: SignOptions = {
      expiresIn: this.config.JWT_EXPIRES_IN as SignOptions['expiresIn'],
    };

    const accessToken = jwt.sign(
      {
        adminId: admin.id,
        email: admin.email,
        role: admin.role,
        sessionId: session.id,
      },
      this.config.JWT_SECRET,
      jwtOptions
    );

    this.logger.info({ adminId: admin.id, email: admin.email, role: admin.role }, 'Admin logged in');

    const { passwordHash, totpSecretEncrypted, ...safeAdmin } = admin;

    return {
      admin: safeAdmin,
      accessToken,
      refreshToken,
      sessionId: session.id,
    };
  }

  /**
   * Refresh access token using refresh token
   */
  async refresh(sessionId: string, refreshToken: string): Promise<{ accessToken: string }> {
    const session = await this.prisma.adminSession.findUnique({
      where: { id: sessionId },
      include: { admin: true },
    });

    if (!session || session.revokedAt || session.expiresAt < new Date()) {
      throw new Error('Invalid or expired session');
    }

    const validToken = await verifyPassword(session.refreshTokenHash, refreshToken);
    if (!validToken) {
      throw new Error('Invalid refresh token');
    }

    if (session.admin.status !== 'ACTIVE') {
      throw new Error('Account is disabled');
    }

    // Generate new access token
    const jwtOptions: SignOptions = {
      expiresIn: this.config.JWT_EXPIRES_IN as SignOptions['expiresIn'],
    };

    const accessToken = jwt.sign(
      {
        adminId: session.admin.id,
        email: session.admin.email,
        role: session.admin.role,
        sessionId: session.id,
      },
      this.config.JWT_SECRET,
      jwtOptions
    );

    return { accessToken };
  }

  /**
   * Logout and revoke session
   */
  async logout(sessionId: string): Promise<void> {
    await this.prisma.adminSession.update({
      where: { id: sessionId },
      data: { revokedAt: new Date() },
    });

    this.logger.info({ sessionId }, 'Admin logged out');
  }

  /**
   * Revoke all sessions for an admin
   */
  async revokeAllSessions(adminId: string): Promise<void> {
    await this.prisma.adminSession.updateMany({
      where: { adminId, revokedAt: null },
      data: { revokedAt: new Date() },
    });

    this.logger.info({ adminId }, 'All admin sessions revoked');
  }
}
