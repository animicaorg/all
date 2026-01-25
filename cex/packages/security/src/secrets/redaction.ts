/**
 * Secret Redaction Utilities
 * Prevents sensitive data from appearing in logs, errors, and responses
 */

/**
 * Patterns to detect sensitive data
 */
const SENSITIVE_PATTERNS = {
  // API keys and tokens
  apiKey: /\b[A-Za-z0-9]{32,}\b/g,
  bearerToken: /Bearer\s+[A-Za-z0-9\-._~+/]+=*/gi,
  
  // Passwords
  password: /"password"\s*:\s*"[^"]+"/gi,
  passwordField: /password[^&\s]*=[^&\s]+/gi,
  
  // Credit cards (basic pattern)
  creditCard: /\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b/g,
  
  // Private keys
  privateKey: /-----BEGIN[A-Z\s]+PRIVATE KEY-----[\s\S]+?-----END[A-Z\s]+PRIVATE KEY-----/g,
  
  // JWT tokens
  jwt: /eyJ[A-Za-z0-9_-]*\.eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]*/g,
  
  // Database URLs
  dbUrl: /(postgres|mysql|mongodb):\/\/[^:]+:[^@]+@[^\s]+/gi,
  
  // AWS keys
  awsAccessKey: /AKIA[0-9A-Z]{16}/g,
  awsSecretKey: /[A-Za-z0-9/+=]{40}/g,
};

/**
 * Sensitive field names (case-insensitive)
 */
const SENSITIVE_FIELDS = new Set([
  'password',
  'secret',
  'token',
  'apikey',
  'api_key',
  'private_key',
  'privatekey',
  'access_token',
  'refresh_token',
  'authorization',
  'cookie',
  'set-cookie',
  'totp_secret',
  'totpsecret',
  'backup_code',
  'backupcode',
]);

/**
 * Redact sensitive data from a string
 */
export function redactString(str: string): string {
  let redacted = str;
  
  // Apply pattern-based redactions
  for (const [_name, pattern] of Object.entries(SENSITIVE_PATTERNS)) {
    redacted = redacted.replace(pattern, '[REDACTED]');
  }
  
  return redacted;
}

/**
 * Redact sensitive fields from an object
 */
export function redactObject<T = any>(obj: any, deep: boolean = true): T {
  if (obj === null || typeof obj !== 'object') {
    return obj;
  }

  if (Array.isArray(obj)) {
    return obj.map((item) => (deep ? redactObject(item, deep) : item)) as T;
  }

  const redacted: any = {};
  
  for (const [key, value] of Object.entries(obj)) {
    const lowerKey = key.toLowerCase();
    
    // Check if key is sensitive
    const isSensitive = SENSITIVE_FIELDS.has(lowerKey) || lowerKey.includes('secret');
    
    if (isSensitive) {
      // Redact the entire value
      redacted[key] = '[REDACTED]';
    } else if (typeof value === 'string') {
      // Check if string value contains sensitive patterns
      redacted[key] = redactString(value);
    } else if (deep && typeof value === 'object' && value !== null) {
      // Recursively redact nested objects
      redacted[key] = redactObject(value, deep);
    } else {
      redacted[key] = value;
    }
  }
  
  return redacted as T;
}

/**
 * Partially redact an email address
 * Example: john.doe@example.com -> j***e@example.com
 */
export function redactEmail(email: string): string {
  const [local, domain] = email.split('@');
  if (!domain) return '[INVALID_EMAIL]';
  
  if (local.length <= 2) {
    return `${local[0]}***@${domain}`;
  }
  
  return `${local[0]}***${local[local.length - 1]}@${domain}`;
}

/**
 * Partially redact a phone number
 * Example: +1234567890 -> +123***890
 */
export function redactPhone(phone: string): string {
  if (phone.length <= 6) return '***';
  
  const prefix = phone.slice(0, 3);
  const suffix = phone.slice(-3);
  return `${prefix}***${suffix}`;
}

/**
 * Partially redact an address
 * Example: 0x1234567890abcdef -> 0x1234...cdef
 */
export function redactAddress(address: string): string {
  if (address.length <= 10) return '***';
  
  const prefix = address.slice(0, 6);
  const suffix = address.slice(-4);
  return `${prefix}...${suffix}`;
}

/**
 * Create a safe error for logging (strips stack trace secrets)
 */
export function redactError(error: Error): { name: string; message: string; stack?: string } {
  return {
    name: error.name,
    message: redactString(error.message),
    stack: error.stack ? redactString(error.stack) : undefined,
  };
}

/**
 * Redact HTTP headers (particularly Authorization and Cookie)
 */
export function redactHeaders(headers: Record<string, any>): Record<string, any> {
  const redacted: Record<string, any> = {};
  
  for (const [key, value] of Object.entries(headers)) {
    const lowerKey = key.toLowerCase();
    
    if (lowerKey === 'authorization' || lowerKey === 'cookie' || lowerKey === 'set-cookie') {
      redacted[key] = '[REDACTED]';
    } else if (typeof value === 'string') {
      redacted[key] = redactString(value);
    } else {
      redacted[key] = value;
    }
  }
  
  return redacted;
}

/**
 * Check if a string contains sensitive data
 */
export function containsSensitiveData(str: string): boolean {
  for (const pattern of Object.values(SENSITIVE_PATTERNS)) {
    if (pattern.test(str)) {
      return true;
    }
  }
  return false;
}
