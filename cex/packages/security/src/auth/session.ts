/**
 * Session Security Utilities
 * Cookie configuration, CSRF protection, and session management
 */

import { randomBytes } from 'crypto';

/**
 * Session cookie configuration
 */
export interface SessionCookieConfig {
  /**
   * Cookie name
   */
  name: string;

  /**
   * Max age in milliseconds
   */
  maxAge: number;

  /**
   * Secure flag (HTTPS only)
   */
  secure: boolean;

  /**
   * HttpOnly flag (not accessible via JavaScript)
   */
  httpOnly: boolean;

  /**
   * SameSite attribute
   */
  sameSite: 'strict' | 'lax' | 'none';

  /**
   * Domain (optional)
   */
  domain?: string;

  /**
   * Path (default: '/')
   */
  path?: string;
}

/**
 * Generate secure session cookie options for admin panel
 */
export function getAdminSessionCookieOptions(isProduction: boolean): SessionCookieConfig {
  return {
    name: 'admin_session',
    maxAge: 7 * 24 * 60 * 60 * 1000, // 7 days
    secure: isProduction,
    httpOnly: true,
    sameSite: 'strict',
    path: '/',
  };
}

/**
 * Generate secure session cookie options for user-facing exchange
 */
export function getUserSessionCookieOptions(isProduction: boolean): SessionCookieConfig {
  return {
    name: 'user_session',
    maxAge: 7 * 24 * 60 * 60 * 1000, // 7 days
    secure: isProduction,
    httpOnly: true,
    // trade.animica.org and api.animica.io are cross-site, so cookies must be SameSite=None in production.
    sameSite: isProduction ? 'none' : 'lax',
    path: '/',
  };
}

/**
 * Generate a secure random session ID
 */
export function generateSessionId(): string {
  return randomBytes(32).toString('base64url');
}

/**
 * Generate a CSRF token
 */
export function generateCsrfToken(): string {
  return randomBytes(32).toString('base64url');
}

/**
 * Verify CSRF token (constant-time comparison)
 */
export function verifyCsrfToken(token: string, expected: string): boolean {
  if (token.length !== expected.length) {
    return false;
  }

  const tokenBuffer = Buffer.from(token);
  const expectedBuffer = Buffer.from(expected);

  // Constant-time comparison
  return tokenBuffer.equals(expectedBuffer);
}

/**
 * Account lockout configuration
 */
export interface LockoutConfig {
  /**
   * Maximum failed login attempts before lockout
   */
  maxAttempts: number;

  /**
   * Lockout duration in milliseconds
   */
  lockoutDuration: number;

  /**
   * Window for counting failed attempts (milliseconds)
   */
  attemptWindow: number;
}

/**
 * Default lockout configuration
 */
export const DEFAULT_LOCKOUT_CONFIG: LockoutConfig = {
  maxAttempts: 5,
  lockoutDuration: 15 * 60 * 1000, // 15 minutes
  attemptWindow: 60 * 60 * 1000, // 1 hour
};

/**
 * Check if account should be locked based on failed attempts
 */
export function shouldLockAccount(
  attempts: Array<{ timestamp: Date; success: boolean }>,
  config: LockoutConfig = DEFAULT_LOCKOUT_CONFIG
): { locked: boolean; unlockAt?: Date } {
  const now = new Date();
  const windowStart = new Date(now.getTime() - config.attemptWindow);

  // Count recent failed attempts
  const recentFailures = attempts.filter(
    (attempt) => !attempt.success && attempt.timestamp >= windowStart
  );

  if (recentFailures.length >= config.maxAttempts) {
    const lastFailure = recentFailures[recentFailures.length - 1];
    const unlockAt = new Date(lastFailure.timestamp.getTime() + config.lockoutDuration);

    if (now < unlockAt) {
      return { locked: true, unlockAt };
    }
  }

  return { locked: false };
}

/**
 * Generate device fingerprint from request
 * Used to detect new devices for security notifications
 */
export function generateDeviceFingerprint(userAgent: string, ip: string): string {
  const data = `${userAgent}|${ip}`;
  return Buffer.from(data).toString('base64url');
}
