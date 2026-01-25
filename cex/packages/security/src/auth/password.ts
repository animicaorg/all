/**
 * Password Hashing with Argon2id
 * Industry standard for password hashing with strong parameters
 */

import argon2 from 'argon2';

/**
 * Argon2id hashing configuration
 * These parameters provide strong security while maintaining reasonable performance
 */
const ARGON2_CONFIG = {
  type: argon2.argon2id,
  memoryCost: 65536, // 64 MiB
  timeCost: 3,       // 3 iterations
  parallelism: 4,    // 4 threads
};

/**
 * Hash a password using Argon2id
 */
export async function hashPassword(password: string): Promise<string> {
  return argon2.hash(password, ARGON2_CONFIG);
}

/**
 * Verify a password against a hash
 */
export async function verifyPassword(hash: string, password: string): Promise<boolean> {
  try {
    return await argon2.verify(hash, password);
  } catch (error) {
    // Invalid hash format
    return false;
  }
}

/**
 * Check if a password hash needs rehashing (e.g., after config changes)
 */
export async function needsRehash(hash: string): Promise<boolean> {
  return argon2.needsRehash(hash, ARGON2_CONFIG);
}
