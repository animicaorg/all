/**
 * TOTP (Time-based One-Time Password) Implementation
 * Used for 2FA authentication
 */

import * as OTPAuth from 'otpauth';

/**
 * TOTP configuration
 */
export interface TotpConfig {
  /**
   * Issuer name (shown in authenticator apps)
   */
  issuer: string;

  /**
   * Account label (usually user email or username)
   */
  label: string;

  /**
   * Secret key (base32 encoded)
   * If not provided, a new secret will be generated
   */
  secret?: string;

  /**
   * Algorithm (default: SHA1 for compatibility)
   */
  algorithm?: 'SHA1' | 'SHA256' | 'SHA512';

  /**
   * Number of digits (default: 6)
   */
  digits?: number;

  /**
   * Period in seconds (default: 30)
   */
  period?: number;
}

/**
 * Generate a new TOTP secret
 */
export function generateTotpSecret(): string {
  const secret = new OTPAuth.Secret({ size: 20 }); // 160 bits
  return secret.base32;
}

/**
 * Create a TOTP instance
 */
export function createTotp(config: TotpConfig): OTPAuth.TOTP {
  return new OTPAuth.TOTP({
    issuer: config.issuer,
    label: config.label,
    algorithm: config.algorithm || 'SHA1',
    digits: config.digits || 6,
    period: config.period || 30,
    secret: config.secret || generateTotpSecret(),
  });
}

/**
 * Generate a TOTP URI for QR code generation
 */
export function generateTotpUri(config: TotpConfig): string {
  const totp = createTotp(config);
  return totp.toString();
}

/**
 * Verify a TOTP token
 * @param secret - Base32 encoded secret
 * @param token - 6-digit token from authenticator
 * @param window - Number of time steps to check (default: 1 = ±30s)
 */
export function verifyTotpToken(
  secret: string,
  token: string,
  window: number = 1
): boolean {
  const totp = new OTPAuth.TOTP({
    secret,
    algorithm: 'SHA1',
    digits: 6,
    period: 30,
  });

  // Validate returns null if invalid, or the delta (time step difference) if valid
  const delta = totp.validate({
    token,
    window,
  });

  return delta !== null;
}

/**
 * Generate current TOTP token (for testing/dev)
 */
export function generateTotpToken(secret: string): string {
  const totp = new OTPAuth.TOTP({
    secret,
    algorithm: 'SHA1',
    digits: 6,
    period: 30,
  });

  return totp.generate();
}

/**
 * Setup TOTP for a user
 * Returns secret and QR code URI
 */
export function setupTotp(issuer: string, userEmail: string): {
  secret: string;
  uri: string;
  qrCodeUrl: string;
} {
  const secret = generateTotpSecret();
  const totp = createTotp({
    issuer,
    label: userEmail,
    secret,
  });

  const uri = totp.toString();
  // QR code can be generated using a library like qrcode
  // For now, return a Google Charts QR URL (basic, not for production)
  const qrCodeUrl = `https://chart.googleapis.com/chart?chs=200x200&cht=qr&chl=${encodeURIComponent(
    uri
  )}`;

  return { secret, uri, qrCodeUrl };
}
