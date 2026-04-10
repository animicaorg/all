/**
 * Crypto Utility
 * Password hashing and TOTP generation
 */

import argon2 from 'argon2';
import speakeasy from 'speakeasy';
import { randomBytes } from 'crypto';

/**
 * Hash password using Argon2
 */
export async function hashPassword(password: string): Promise<string> {
  return argon2.hash(password, {
    type: argon2.argon2id,
    memoryCost: 19456, // 19 MiB
    timeCost: 2,
    parallelism: 1,
  });
}

/**
 * Verify password against hash
 */
export async function verifyPassword(hash: string, password: string): Promise<boolean> {
  try {
    return await argon2.verify(hash, password);
  } catch {
    return false;
  }
}

/**
 * Generate TOTP secret
 */
export function generateTotpSecret(): string {
  return speakeasy.generateSecret({
    length: 32,
    name: 'Animica Admin',
  }).base32;
}

/**
 * Verify TOTP token
 */
export function verifyTotpToken(secret: string, token: string, window = 2): boolean {
  return speakeasy.totp.verify({
    secret,
    encoding: 'base32',
    token,
    window,
  });
}

/**
 * Generate QR code URL for TOTP setup
 */
export function generateTotpQrCodeUrl(secret: string, email: string, issuer = 'Animica Admin'): string {
  return speakeasy.otpauthURL({
    secret,
    label: email,
    issuer,
    encoding: 'base32',
  });
}

/**
 * Generate secure random token
 */
export function generateSecureToken(length = 32): string {
  return randomBytes(length).toString('hex');
}
