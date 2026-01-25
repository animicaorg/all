/**
 * Backup Codes for 2FA Recovery
 * One-time use codes that allow account recovery if TOTP device is lost
 */

import { randomBytes } from 'crypto';
import { hashPassword, verifyPassword } from './password.js';

/**
 * Generate a set of backup codes
 * @param count - Number of codes to generate (default: 10)
 * @returns Array of backup codes (format: XXXX-XXXX)
 */
export function generateBackupCodes(count: number = 10): string[] {
  const codes: string[] = [];

  for (let i = 0; i < count; i++) {
    // Generate 8 random bytes, convert to hex, take first 8 chars
    const code = randomBytes(4)
      .toString('hex')
      .toUpperCase()
      .match(/.{1,4}/g)
      ?.join('-') || '';
    codes.push(code);
  }

  return codes;
}

/**
 * Hash backup codes for storage
 * Each code should be hashed before storing in the database
 */
export async function hashBackupCodes(codes: string[]): Promise<string[]> {
  return Promise.all(codes.map((code) => hashPassword(code)));
}

/**
 * Verify a backup code against stored hashes
 * Returns the index of the matching code, or -1 if no match
 */
export async function verifyBackupCode(
  code: string,
  hashedCodes: string[]
): Promise<number> {
  for (let i = 0; i < hashedCodes.length; i++) {
    const isValid = await verifyPassword(hashedCodes[i], code);
    if (isValid) {
      return i;
    }
  }
  return -1;
}

/**
 * Format a backup code for display
 * Removes dashes and converts to uppercase
 */
export function normalizeBackupCode(code: string): string {
  return code.replace(/-/g, '').toUpperCase();
}

/**
 * Validate backup code format
 * Should be 8 hex characters, optionally with a dash in the middle
 */
export function isValidBackupCodeFormat(code: string): boolean {
  const normalized = normalizeBackupCode(code);
  return /^[A-F0-9]{8}$/.test(normalized);
}
