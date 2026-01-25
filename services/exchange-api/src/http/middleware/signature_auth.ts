/**
 * HMAC Signature Authentication Utilities
 * 
 * Provides secure request signature generation and verification using HMAC-SHA256.
 * Used for API key authentication to prevent request tampering and replay attacks.
 */

import crypto from 'node:crypto';

export interface SignatureComponents {
  timestamp: string;
  nonce: string;
  method: string;
  path: string;
  query: string;
  bodyHash: string;
}

/**
 * Computes SHA256 hash of request body
 */
export function hashBody(body: string | Buffer | undefined): string {
  if (!body || (typeof body === 'string' && body.length === 0)) {
    return crypto.createHash('sha256').update('').digest('hex');
  }
  return crypto.createHash('sha256').update(body).digest('hex');
}

/**
 * Builds the prehash string for signature computation
 * Format: <timestamp>\n<nonce>\n<method>\n<path>\n<query>\n<body_sha256_hex>
 */
export function buildPrehashString(components: SignatureComponents): string {
  return [
    components.timestamp,
    components.nonce,
    components.method,
    components.path,
    components.query,
    components.bodyHash,
  ].join('\n');
}

/**
 * Computes HMAC-SHA256 signature
 */
export function computeSignature(secret: string, prehash: string): string {
  return crypto
    .createHmac('sha256', secret)
    .update(prehash, 'utf8')
    .digest('base64');
}

/**
 * Verifies HMAC signature using timing-safe comparison
 * Returns true if signature matches, false otherwise
 */
export function verifySignature(
  expectedSignature: string,
  providedSignature: string
): boolean {
  try {
    // Ensure both signatures are valid base64 and same length
    const expectedBuffer = Buffer.from(expectedSignature, 'base64');
    const providedBuffer = Buffer.from(providedSignature, 'base64');

    // Must be same length for timingSafeEqual
    if (expectedBuffer.length !== providedBuffer.length) {
      return false;
    }

    // Constant-time comparison to prevent timing attacks
    return crypto.timingSafeEqual(expectedBuffer, providedBuffer);
  } catch (error) {
    // Invalid base64 or other error
    return false;
  }
}

/**
 * Extracts signature components from Express request
 */
export function extractSignatureComponents(
  timestamp: string,
  nonce: string,
  method: string,
  path: string,
  query: string,
  body: string | Buffer | undefined
): SignatureComponents {
  return {
    timestamp,
    nonce,
    method: method.toUpperCase(),
    path,
    query,
    bodyHash: hashBody(body),
  };
}
